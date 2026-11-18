"""The editor's working copy of the open boards, the rules that decide whether
they are safe to save, file reading, and undo history.

No Qt in here: everything is plain Python. The GUI only edits a ``Project``
(the boards that are open) and asks it for ``issues()``.

Every board is one config file: ``{"name": ..., "type": ..., "signals": [...]}``.
A board has a type (NI 6509, NI 9205, ... see ``pinout.py``) and holds
*signals*: a name, the channel it maps to and an optional note. Direction
comes from the board type. On most boards it is fixed by the channel (6509:
P0-P5 outputs, P6-P11 inputs; 9205 inputs; 9264 and 9476 outputs). On the
NI 9401 the user sets it, and DIO0-3 and DIO4-7 each share one direction.

Which file a board came from and whether it has unsaved changes is the
window's business (``app.py``), not the model's.
"""

from __future__ import annotations

import bisect
import copy
import difflib
import itertools
import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from pinout import (BOARD_TYPES, DEFAULT_TYPE, BoardType, Direction, Line, SignalKind,
                    board_type, find_type)


class Severity(IntEnum):
    WARNING = 1
    ERROR = 2


@dataclass
class Assignment:
    """One signal: its name and note. The channel it maps to is the key it
    is stored under in ``Board.lines``. ``direction`` is only kept for
    channels whose direction the user sets (NI 9401); everywhere else it
    follows from the channel. Ask ``Board.direction(line)`` either way."""

    name: str
    note: str = ""
    direction: Direction | None = None


_uids = itertools.count(1)


@dataclass
class Board:
    name: str
    lines: dict[Line, Assignment] = field(default_factory=dict)
    type: str = DEFAULT_TYPE  # a key of ``pinout.BOARD_TYPES``, e.g. "NI 9205"
    #: Stays the same through renames, undo and reload, so the window can
    #: remember which file this board belongs to.
    uid: int = field(default_factory=lambda: next(_uids), compare=False)

    @classmethod
    def numbered(cls, index: int, type: str = DEFAULT_TYPE) -> Board:
        """A blank board for position ``index`` (0-based): "Board 1"."""
        return cls(name=f"Board {index + 1}", type=type)

    @property
    def bt(self) -> BoardType:
        """The pin map and rules for this board's type."""
        return board_type(self.type)

    @property
    def label(self) -> str:
        return self.name.strip() or "(unnamed board)"

    def direction(self, line: Line) -> Direction:
        """Direction of a channel: fixed by the board type, or, where the
        user sets it, what its group is set to (input until set)."""
        fixed = self.bt.fixed_direction(line)
        if fixed is not None:
            return fixed
        a = self.lines.get(line)
        if a is not None and a.direction is not None:
            return a.direction
        return self.group_direction(line) or Direction.INPUT

    def group_direction(self, line: Line, skip: Line | None = None) -> Direction | None:
        """The direction already set by another signal in ``line``'s group."""
        group = self.bt.group(line)
        if group is None:
            return None
        return next((a.direction for other, a in self.lines.items()
                     if other != skip and a.direction is not None
                     and self.bt.group(other) == group), None)

    def set_direction(self, line: Line, direction: Direction) -> None:
        """Set the direction of ``line`` and of every signal sharing it."""
        group = self.bt.group(line)
        if group is None:
            return
        for other, a in self.lines.items():
            if self.bt.group(other) == group:
                a.direction = direction

    def sorted_lines(self) -> list[tuple[Line, Assignment]]:
        return sorted(self.lines.items())

    def to_document(self) -> dict[str, Any]:
        """What this board's file holds: ``line`` (never ``pin``), sorted by
        line, so the same board always produces the same file."""
        signals = []
        for line, a in self.sorted_lines():
            sig: dict[str, Any] = {
                "name": a.name.strip(),
                "line": str(line),
                "direction": self.direction(line).value,
            }
            if a.note.strip():
                sig["note"] = a.note.strip()
            signals.append(sig)
        return {"name": self.name.strip(), "type": self.type, "signals": signals}

    def to_json(self) -> str:
        """The exact text of this board's file."""
        return json.dumps(self.to_document(), indent=2) + "\n"


@dataclass
class Issue:
    severity: Severity
    board: int | None  # board index, for jumping to it
    line: Line | None  # the line on that board, if the issue is about one signal
    message: str
    from_file: bool = False  # a problem found while reading the file


@dataclass
class FileProblem:
    """Something in the file that didn't match what the editor expects.

    The loader never stops at the first one: it reads the whole file, keeps
    everything it can, and records each problem with where it is and what
    was done about it ("left out", "ignored", "kept as an input"). They block
    Save until the file is fixed and reloaded, or the user accepts them.
    """

    message: str
    at: int | None = None  # line number in the file
    board: int | None = None  # board it belongs to, if any
    line: Line | None = None  # where the signal was placed, if it was kept
    fatal: bool = False  # nothing could be loaded (e.g. the JSON itself is broken)
    file: str = ""  # name of the file it was found in


#: Board files are a few kilobytes. Anything this big is the wrong file, and
#: reading it would only freeze the window.
MAX_FILE_BYTES = 5_000_000


class LoadError(ValueError):
    """The file can't be read at all (missing, no permission, not UTF-8).
    Everything wrong *inside* a readable file is a ``FileProblem`` instead."""


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Older files still open. "buttons" is read as "signals"; the other legacy
# fields are dropped. Saving always writes the current format.
LEGACY_BOARD_KEYS = {"device", "card"}
LEGACY_SIGNAL_KEYS = {"selected"}
#: The retired top-level project title. Recorded under this label (not "name",
#: which boards and signals still use) so the notice is unambiguous.
PROJECT_NAME = "project name"
#: Recorded when a board in the file has no "type" (files from before the
#: other board types existed): saving writes it.
NO_TYPE = "board type"
#: Recorded for the older shape with several boards in one file.
MANY = "boards list"
_TOP_KEYS = {"name", "boards"}
_BOARD_KEYS = {"name", "type", "signals", "buttons"} | LEGACY_BOARD_KEYS
_SIGNAL_KEYS = {"name", "line", "pin", "direction", "note"} | LEGACY_SIGNAL_KEYS


@dataclass
class Project:
    boards: list[Board] = field(default_factory=lambda: [Board.numbered(0)])
    #: Legacy fields found when loading (e.g. "buttons", "selected").
    dropped_fields: set[str] = field(default_factory=set, compare=False, repr=False)
    #: Mismatches found in the file when it was loaded (see ``FileProblem``).
    file_problems: list[FileProblem] = field(default_factory=list, compare=False, repr=False)

    def format_changes(self) -> list[str]:
        """What saving will change about an older file, in plain words."""
        out = []
        if "buttons" in self.dropped_fields:
            out.append('"buttons" becomes "signals"')
        if PROJECT_NAME in self.dropped_fields:
            out.append('the project "name" at the top of the file is removed')
        if NO_TYPE in self.dropped_fields:
            out.append('each board gets its "type" (NI 6509)')
        if MANY in self.dropped_fields:
            out.append("each board is saved to its own file")
        out += [f'"{k}" is removed'
                for k in sorted(self.dropped_fields - {"buttons", PROJECT_NAME, NO_TYPE, MANY})]
        return out

    # ------------------------------------------------------------ copies

    def clone(self) -> Project:
        return copy.deepcopy(self)

    def __eq__(self, other: object) -> bool:
        # Two projects are the same if they would save to the same file.
        return isinstance(other, Project) and self.to_json() == other.to_json()

    # ------------------------------------------------------------ loading

    @classmethod
    def from_json(cls, text: str) -> Project:
        """Read a board file. Never raises for problems in the content:
        they are collected in ``file_problems`` and the rest is loaded."""
        try:
            json.loads(text)
        except RecursionError:  # brackets nested thousands deep: not a board file
            return cls(file_problems=[FileProblem(
                "nested far too deeply to be a board file — nothing could be loaded", fatal=True)])
        except json.JSONDecodeError as e:
            lines = text.splitlines()
            shown = lines[e.lineno - 1].strip() if 0 < e.lineno <= len(lines) else ""
            snippet = f": {shown[:60]}" if shown else ""
            return cls(file_problems=[FileProblem(
                f"not valid JSON ({e.msg}, column {e.colno}){snippet} — nothing could be loaded",
                at=e.lineno, fatal=True)])
        problems: list[FileProblem] = []
        try:
            data = _parse_located(text, problems)
        except RecursionError:
            return cls(file_problems=[FileProblem(
                "nested far too deeply to be a board file — nothing could be loaded", fatal=True)])
        return cls.from_document(data, problems)

    @classmethod
    def load(cls, path: str | Path) -> Project:
        path = Path(path)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise LoadError(f"{path.name} is too large to be a board file "
                                f"(over {MAX_FILE_BYTES // 1_000_000} MB)")
            # utf-8-sig: also reads files saved with a byte-order mark (Windows Notepad).
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise LoadError(f"{path.name} is not a UTF-8 text file") from None
        except OSError as e:
            raise LoadError(f"reading {path}: {e.strerror or e}") from None
        return cls.from_json(text)

    @classmethod
    def from_document(cls, data: Any, problems: list[FileProblem] | None = None) -> Project:
        """One board per file: ``{"name", "type", "signals"}``. The older
        shape with several boards under ``"boards"`` is still read."""
        problems = [] if problems is None else problems
        if not isinstance(data, dict):
            problems.append(FileProblem(
                'the file must be a JSON object { "name": ..., "signals": [...] } — '
                "nothing could be loaded",
                at=_at(data), fatal=True))
            return cls(file_problems=problems)
        dropped: set[str] = set()
        if "boards" in data:
            dropped.add(MANY)
            if "name" in data:
                dropped.add(PROJECT_NAME)
            _unknown(data, _TOP_KEYS, {"boards"}, "top of the file", problems, None)
            boards = data["boards"]
            if isinstance(boards, list) and boards:
                parsed = [_board_from(bf, i, dropped, problems) for i, bf in enumerate(boards)]
            else:
                problems.append(FileProblem(
                    '"boards" must be a list with at least one board — an empty Board 1 is used',
                    at=_at(data, "boards")))
                parsed = [Board.numbered(0)]
        elif "signals" in data or "buttons" in data:
            # One board: its name, type and signals sit at the top level.
            parsed = [_board_from(data, 0, dropped, problems)]
        else:
            problems.append(FileProblem(
                'no "signals" list in the file — nothing could be loaded', at=_at(data), fatal=True))
            parsed = [Board.numbered(0)]
        return cls(boards=parsed, dropped_fields=dropped, file_problems=problems)

    # ------------------------------------------------------------ saving

    def to_json(self) -> str:
        """Every open board, in order. Not a file format: it is what undo
        history and ``==`` compare."""
        return json.dumps([b.to_document() for b in self.boards], indent=2) + "\n"

    # ------------------------------------------------------------ editing

    def signal_count(self) -> int:
        return sum(len(b.lines) for b in self.boards)

    def _next_free_board(self, type: str) -> Board:
        taken = {b.name.strip().lower() for b in self.boards}
        n = len(self.boards)
        while f"board {n + 1}" in taken:
            n += 1
        return Board.numbered(n, type)

    def add_board(self, type: str = DEFAULT_TYPE) -> int:
        """Add the next free "Board N" of the given type. Returns its index."""
        self.boards.append(self._next_free_board(type))
        return len(self.boards) - 1

    def add_boards(self, boards: list[Board], problems: list[FileProblem] = (), file: str = "") -> int:
        """Add boards read from ``file``, with the problems found in it.
        Returns the index of the first one."""
        base = len(self.boards)
        self.boards += boards
        for fp in problems:
            fp.board, fp.file = base + (fp.board or 0), file
            self.file_problems.append(fp)
        return base

    def replace_board(self, index: int, board: Board, problems: list[FileProblem] = (),
                      file: str = "") -> None:
        """Swap in a board read again from its file. It stays the same board
        (same ``uid``) as far as the window is concerned."""
        board.uid = self.boards[index].uid
        self.boards[index] = board
        self.file_problems = [fp for fp in self.file_problems if fp.board != index]
        for fp in problems:
            fp.board, fp.file = index, file
            self.file_problems.append(fp)

    def remove_board(self, index: int) -> bool:
        """Take a board out, with the problems found in its file. The last
        board can't be removed."""
        if len(self.boards) <= 1 or not 0 <= index < len(self.boards):
            return False
        del self.boards[index]
        self.file_problems = [fp for fp in self.file_problems if fp.board != index]
        for fp in self.file_problems:
            if fp.board is not None and fp.board > index:
                fp.board -= 1
        return True

    def name_taken(self, name: str) -> bool:
        low = name.strip().lower()
        return any(n.strip().lower() == low for n in self._all_names())

    def _all_names(self):
        for b in self.boards:
            for a in b.lines.values():
                yield a.name

    def _unique_name(self, base: str) -> str:
        name, n = base, 2
        while self.name_taken(name):
            name = f"{base}_{n}"
            n += 1
        return name

    def assign(self, board: int, line: Line) -> Assignment:
        """Add a signal with a project-wide unique default name like
        ``B2_P0_7``. Its direction follows from the line; where the user
        sets it, it starts as whatever its group already is."""
        b = self.boards[board]
        if line not in b.lines:
            direction = None
            if b.bt.fixed_direction(line) is None:
                direction = b.group_direction(line) or Direction.INPUT
            b.lines[line] = Assignment(self._unique_name(b.bt.default_name(board, line)),
                                       direction=direction)
        return b.lines[line]

    def move(self, board: int, src: Line, dst: Line) -> bool:
        """Move an assignment to a free line on the same board."""
        b = self.boards[board]
        lines = b.lines
        if src == dst or dst in lines or src not in lines:
            return False
        a = lines.pop(src)
        if b.bt.fixed_direction(dst) is None:
            # Joining another group: take that group's direction.
            a.direction = b.group_direction(dst) or a.direction or Direction.INPUT
        lines[dst] = a
        return True

    # ------------------------------------------------------------ rules

    def issues(self) -> list[Issue]:
        out: list[Issue] = []
        for fp in sorted(self.file_problems, key=lambda f: (f.board or 0, f.at if f.at is not None else 1 << 30)):
            board = fp.board if fp.board is not None and fp.board < len(self.boards) else None
            where = (fp.file or "file") + (f" line {fp.at}" if fp.at else "")
            out.append(Issue(Severity.ERROR, board, fp.line if board is not None else None,
                             f"{where}: {fp.message}", from_file=True))
        board_names: dict[str, list[int]] = {}
        # Every named signal in the project: upper-case name -> [(board, line)]
        names: dict[str, list[tuple[int, Line]]] = {}

        for bi, b in enumerate(self.boards):
            label = b.label
            if not b.name.strip():
                out.append(Issue(Severity.ERROR, bi, None, f"board {bi + 1} has no name"))
            else:
                board_names.setdefault(b.name.strip().upper(), []).append(bi)
            for line, a in b.sorted_lines():
                name = a.name.strip()
                if not name:
                    out.append(Issue(Severity.ERROR, bi, line, f"{label} · {line}: signal has no name"))
                    continue
                if a.name != name:
                    out.append(Issue(Severity.WARNING, bi, line,
                                     f'{label} · {line}: "{a.name}" has spaces at the start or end; '
                                     f'it is saved as "{name}"'))
                if not _IDENT.match(name):
                    out.append(Issue(Severity.WARNING, bi, line,
                                     f'{label} · {line}: "{name}" should use only letters, '
                                     "digits and _, and not start with a digit"))
                names.setdefault(name.upper(), []).append((bi, line))

        for group in board_names.values():
            if len(group) > 1:
                for bi in group:
                    out.append(Issue(Severity.ERROR, bi, None,
                                     f'board name "{self.boards[bi].name.strip()}" is used '
                                     f"{len(group)} times; each board needs its own name"))

        for uses in names.values():
            if len(uses) > 1:
                for bi, line in uses:
                    others = [str(ol) if ob == bi else f"{self.boards[ob].label} {ol}"
                              for ob, ol in uses if (ob, ol) != (bi, line)]
                    name = self.boards[bi].lines[line].name.strip()
                    out.append(Issue(Severity.ERROR, bi, line,
                                     f'{self.boards[bi].label} · {line}: name "{name}" is also '
                                     f"used on {', '.join(others)}"))

        # File problems first (in file order), then errors, then by board and position.
        out.sort(key=lambda i: (not i.from_file, -i.severity,
                                0 if i.from_file else (i.board if i.board is not None else -1),
                                0 if i.from_file or not i.line else i.line.index))
        return out

    def errors(self) -> list[Issue]:
        return [i for i in self.issues() if i.severity is Severity.ERROR]


def worst_on(issues: list[Issue], board: int, line: Line | None = None) -> Severity | None:
    """Worst issue on one channel (``line`` given) or anywhere on a board."""
    found = [i.severity for i in issues
             if i.board == board and (line is None or i.line == line)]
    return max(found) if found else None


# ---------------------------------------------------------------- undo


class History:
    """Snapshot-based undo/redo. Call ``record(project)`` *before* each change.

    Typing in a field calls ``record`` on every keystroke with the same
    ``key`` (e.g. ``("name", board, line)``); only the first one saves a
    snapshot, so one undo reverts the whole edit instead of one letter.
    """

    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self._undo: list[Project] = []
        self._redo: list[Project] = []
        self._key: object = None

    def record(self, project: Project, key: object = None) -> None:
        if key is not None and key == self._key:
            return
        self._undo.append(project.clone())
        del self._undo[: -self.limit]
        self._redo.clear()
        self._key = key

    def break_group(self) -> None:
        self._key = None

    def undo(self, current: Project) -> Project | None:
        if not self._undo:
            return None
        self._redo.append(current.clone())
        self._key = None
        return self._undo.pop()

    def redo(self, current: Project) -> Project | None:
        if not self._redo:
            return None
        self._undo.append(current.clone())
        self._key = None
        return self._redo.pop()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._key = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)


# ---------------------------------------------------------------- helpers


# ---- reading JSON with line numbers
#
# json.loads() can't say where a value was, and it silently keeps the last of
# two identical keys. The file is first checked with json.loads (so it is
# valid JSON), then read again here, remembering the line of every object,
# key and list item, and reporting duplicate keys.


class _Obj(dict):
    """A JSON object that remembers its line and the line of each key."""

    def __init__(self, line: int | None) -> None:
        super().__init__()
        self.line = line
        self.key_lines: dict[str, int | None] = {}


class _Arr(list):
    """A JSON list that remembers its line and the line of each item."""

    def __init__(self, line: int | None) -> None:
        super().__init__()
        self.line = line
        self.item_lines: list[int] = []


_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]:,]|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?'
                    r"|true|false|null|NaN|-?Infinity")


def _parse_located(text: str, problems: list[FileProblem]) -> Any:
    newlines = [m.start() for m in re.finditer("\n", text)]
    toks = [(m.group(), bisect.bisect_left(newlines, m.start()) + 1) for m in _TOKEN.finditer(text)]

    def value(i: int) -> tuple[Any, int]:
        tok, line = toks[i]
        if tok == "{":
            obj, i = _Obj(line), i + 1
            if toks[i][0] == "}":
                return obj, i + 1
            while True:
                key, key_line = json.loads(toks[i][0]), toks[i][1]
                v, i = value(i + 2)  # skip the ":"
                if key in obj:
                    first = obj.key_lines[key]
                    earlier = "earlier on this line" if first == key_line else f"first on line {first}"
                    problems.append(FileProblem(
                        f'"{key}" appears twice in the same object ({earlier}); JSON keeps only the '
                        f"last one, {_show(v)}", at=key_line))
                obj[key], obj.key_lines[key] = v, key_line
                if toks[i][0] == ",":
                    i += 1
                    continue
                return obj, i + 1
        if tok == "[":
            arr, i = _Arr(line), i + 1
            if toks[i][0] == "]":
                return arr, i + 1
            while True:
                arr.item_lines.append(toks[i][1])
                v, i = value(i)
                arr.append(v)
                if toks[i][0] == ",":
                    i += 1
                    continue
                return arr, i + 1
        return json.loads(tok), i + 1

    return value(0)[0]


def _at(obj: Any, key: str | None = None) -> int | None:
    """File line of ``obj`` (or of its ``key``), when known."""
    if key is not None and isinstance(obj, _Obj) and key in obj.key_lines:
        return obj.key_lines[key]
    return getattr(obj, "line", None)


def _show(v: Any) -> str:
    text = json.dumps(v)
    return text if len(text) <= 40 else text[:37] + "..."


# ---- checking what was read


def _unknown(obj: dict, allowed: set[str], suggest: set[str], who: str,
             problems: list[FileProblem], board: int | None) -> None:
    """Report fields the editor doesn't know, with a spelling suggestion."""
    for k in obj:
        if k not in allowed:
            close = difflib.get_close_matches(k, sorted(suggest), n=1, cutoff=0.6)
            hint = f' (did you mean "{close[0]}"?)' if close else ""
            problems.append(FileProblem(f'{who}: unknown field "{k}"{hint} — ignored',
                                        at=_at(obj, k), board=board))


def _board_from(bf: Any, index: int, dropped: set[str], problems: list[FileProblem]) -> Board:
    default = Board.numbered(index)
    if not isinstance(bf, dict):
        problems.append(FileProblem(
            f"board {index + 1} must be an object {{ ... }}, not {_show(bf)} — an empty "
            f"{default.name} is used", at=_at(bf), board=index))
        return default
    name = bf.get("name", default.name)
    if not isinstance(name, str):
        problems.append(FileProblem(
            f'board {index + 1}: "name" must be text, not {_show(name)} — named "{default.name}"',
            at=_at(bf, "name"), board=index))
        name = default.name
    label = name.strip() or f"board {index + 1}"
    _unknown(bf, _BOARD_KEYS, {"name", "type", "signals"}, label, problems, index)
    bt = board_type(DEFAULT_TYPE)  # files from before "type" existed are all 6509s
    if "type" in bf:
        found = find_type(bf["type"]) if isinstance(bf["type"], str) else None
        if found is None:
            problems.append(FileProblem(
                f'{label}: unknown board "type" {_show(bf["type"])} (known: '
                f'{", ".join(BOARD_TYPES)}) — read as an {bt.key}',
                at=_at(bf, "type"), board=index))
        else:
            bt = found
    else:
        dropped.add(NO_TYPE)
    dropped |= LEGACY_BOARD_KEYS & bf.keys()
    key = "signals"
    if "signals" in bf and "buttons" in bf:
        problems.append(FileProblem(
            f'{label}: has both "signals" and the older "buttons" — only "signals" is used',
            at=_at(bf, "buttons"), board=index))
    elif "buttons" in bf:
        dropped.add("buttons")
        key = "buttons"
    signals = bf.get(key)
    if not isinstance(signals, list):
        what = f'"{key}" must be a list, not {_show(signals)}' if key in bf else 'has no "signals" list'
        problems.append(FileProblem(f"{label}: {what} — loaded with no signals",
                                    at=_at(bf, key if key in bf else None), board=index))
        signals = []

    lines: dict[Line, Assignment] = {}
    for k, sf in enumerate(signals):
        at = signals.item_lines[k] if isinstance(signals, _Arr) else None
        _signal_from(sf, k, label, index, at, bt, lines, dropped, problems)
    return Board(name=name, lines=lines, type=bt.key)


def _signal_from(sf: Any, k: int, label: str, board: int, at: int | None, bt: BoardType,
                 lines: dict[Line, Assignment], dropped: set[str], problems: list[FileProblem]) -> None:
    def problem(msg: str, key: str | None = None, line: Line | None = None) -> None:
        where = _at(sf, key) if key is not None and isinstance(sf, _Obj) and key in sf else at
        problems.append(FileProblem(msg, at=where, board=board, line=line))

    if not isinstance(sf, dict):
        problem(f"{label} · signal #{k + 1} must be an object {{ ... }}, not {_show(sf)} — left out")
        return
    name = sf.get("name")
    who = (f'{label} · signal "{name}"' if isinstance(name, str) and name.strip()
           else f"{label} · signal #{k + 1}")
    _unknown(sf, _SIGNAL_KEYS, {"name", "line", "pin", "direction", "note"}, who, problems, board)
    dropped |= LEGACY_SIGNAL_KEYS & sf.keys()
    if not isinstance(name, str):
        problem(f'{who}: "name" is {"missing" if name is None else f"not text ({_show(name)})"} — left out',
                "name")
        return
    line, err, err_key = _resolve(sf, bt)
    if line is None:
        problem(f"{who}: {err} — left out", err_key)
        return
    pin = bt.pin_of(line)
    if line in lines:
        problem(f'{who}: {line} (pin {pin}) already has "{lines[line].name}" — left out',
                "line" if "line" in sf else "pin")
        return
    note = sf.get("note") or ""
    if not isinstance(note, str):
        problem(f'{who}: "note" must be text, not {_show(note)} — note dropped', "note", line)
        note = ""
    d = sf.get("direction")
    valid = d in ("input", "output")
    expected = bt.fixed_direction(line)
    if expected is not None:
        # Fixed by the board. "direction" is written for readers of the file
        # and must match (6509: P0-P5 outputs, P6-P11 inputs).
        if "direction" in sf and not valid:
            problem(f'{who}: "direction" is {_show(d)}; it must be "input" or "output" — kept on '
                    f"{line} as an {expected.value} (fixed by the pin)", "direction", line)
        elif "direction" in sf and d != expected.value:
            move = "; move it if it belongs on the other half" if bt.mixed else ""
            problem(f'{who}: says "{d}", but {line} (pin {pin}) is an {expected.value}: '
                    f"{bt.why_direction(line)} — kept on {line} as an {expected.value}{move}",
                    "direction", line)
        lines[line] = Assignment(name, note)
        return
    # Set by the user (NI 9401), and shared with the rest of its group.
    group = bt.group(line)
    shared = next((a.direction for other, a in lines.items() if bt.group(other) == group), None)
    direction = shared or (Direction(d) if valid else Direction.INPUT)
    if "direction" in sf and not valid:
        problem(f'{who}: "direction" is {_show(d)}; it must be "input" or "output" — kept on '
                f"{line} as an {direction.value}", "direction", line)
    elif valid and d != direction.value:
        problem(f'{who}: says "{d}", but {group} share one direction and an earlier signal '
                f"made them {direction.value}s — kept on {line} as an {direction.value}",
                "direction", line)
    elif not valid and shared is None:
        problem(f'{who}: {line} needs a "direction" ("input" or "output"); the {bt.key} has '
                "no fixed one — kept as an input", None, line)
    lines[line] = Assignment(name, note, direction)


def _resolve(sf: dict, bt: BoardType) -> tuple[Line | None, str, str | None]:
    """The line a signal maps to, or (None, why not, which field)."""
    has_line, has_pin = sf.get("line") is not None, sf.get("pin") is not None
    if has_line and has_pin:
        return None, (f'gives both "line" ({_show(sf["line"])}) and "pin" ({_show(sf["pin"])}); '
                      "give only one"), "pin"
    if has_line:
        v = sf["line"]
        if not isinstance(v, str):
            return None, f'"line" must be text like "{bt.example}", not {_show(v)}', "line"
        try:
            return bt.parse(v), "", None
        except ValueError as e:  # the board type says what's wrong and what was probably meant
            return None, str(e), "line"
    if has_pin:
        pin = sf["pin"]
        if isinstance(pin, str) and pin.strip().isdigit():
            return None, f'"pin" is the text "{pin}"; write it as a number: {pin.strip()}', "pin"
        if not isinstance(pin, int) or isinstance(pin, bool):
            return None, f'"pin" must be a number 1-{bt.pin_count}, not {_show(pin)}', "pin"
        s = bt.signal_at(pin)
        if s is None:
            return None, f"pin {pin} is out of range (1-{bt.pin_count})", "pin"
        if s.kind is not SignalKind.LINE:
            return None, f"pin {pin} is {s}, which can't carry a signal", "pin"
        return s.line, "", None
    return None, f'needs a "line" (e.g. "{bt.example}") or a "pin" (1-{bt.pin_count})', None