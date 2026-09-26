"""The editor's working copy of a project (a set of NI PCIe-6509 boards), the
rules that decide whether it is safe to save, file I/O, and undo history.

No Qt in here: everything is plain Python. The GUI only edits a ``Project``
and asks it for ``issues()`` and ``to_json()``.

Each board holds *signals*: a name, the DIO line it maps to and an optional
note. Direction is fixed by the line, the way these 6509s are used: ports
P0-P5 (pins 1-48) are outputs and ports P6-P11 (pins 51-98) are inputs.
"""

from __future__ import annotations

import bisect
import copy
import difflib
import json
import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

from pinout import Line, SignalKind, signal_at


class Direction(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


#: Ports wired as outputs on every 6509 board (pins 1-48); ports 6-11 are inputs.
OUTPUT_PORTS = range(0, 6)


def direction_of(line: Line) -> Direction:
    """P0-P5 (pins 1-50 block) are outputs, P6-P11 (pins 51-100 block) inputs."""
    return Direction.OUTPUT if line.port in OUTPUT_PORTS else Direction.INPUT


class Severity(IntEnum):
    WARNING = 1
    ERROR = 2


@dataclass
class Assignment:
    """One signal: its name and note. The line it maps to is the key it is
    stored under in ``Board.lines``; its direction follows from that line
    (``direction_of``)."""

    name: str
    note: str = ""


@dataclass
class Board:
    name: str
    lines: dict[Line, Assignment] = field(default_factory=dict)

    @classmethod
    def numbered(cls, index: int) -> Board:
        """A blank board for position ``index`` (0-based): "Board 1"."""
        return cls(name=f"Board {index + 1}")

    @property
    def label(self) -> str:
        return self.name.strip() or "(unnamed board)"

    def sorted_lines(self) -> list[tuple[Line, Assignment]]:
        return sorted(self.lines.items())


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
_TOP_KEYS = {"name", "boards"}
_SINGLE_KEYS = {"name", "card", "buttons", "signals"}
_BOARD_KEYS = {"name", "signals", "buttons"} | LEGACY_BOARD_KEYS
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
        out += [f'"{k}" is removed' for k in sorted(self.dropped_fields - {"buttons", PROJECT_NAME})]
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
        """Read a mapping file. Never raises for problems in the content:
        they are collected in ``file_problems`` and the rest is loaded."""
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            lines = text.splitlines()
            shown = lines[e.lineno - 1].strip() if 0 < e.lineno <= len(lines) else ""
            snippet = f": {shown[:60]}" if shown else ""
            return cls(file_problems=[FileProblem(
                f"not valid JSON ({e.msg}, column {e.colno}){snippet} — nothing could be loaded",
                at=e.lineno, fatal=True)])
        problems: list[FileProblem] = []
        data = _parse_located(text, problems)
        return cls.from_document(data, problems)

    @classmethod
    def load(cls, path: str | Path) -> Project:
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise LoadError(f"{path.name} is not a UTF-8 text file") from None
        except OSError as e:
            raise LoadError(f"reading {path}: {e.strerror or e}") from None
        return cls.from_json(text)

    @classmethod
    def from_document(cls, data: Any, problems: list[FileProblem] | None = None) -> Project:
        """Accepts the ``boards`` shape and the older single-board shape."""
        problems = [] if problems is None else problems
        if not isinstance(data, dict):
            problems.append(FileProblem(
                'the file must be a JSON object { "boards": [...] } — nothing could be loaded',
                at=_at(data), fatal=True))
            return cls(file_problems=problems)
        dropped: set[str] = set()
        if "name" in data:
            dropped.add(PROJECT_NAME)
        if "boards" in data:
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
            # Older single-board shape: the signals sit at the top level.
            _unknown(data, _SINGLE_KEYS, {"boards", "signals"}, "top of the file", problems, None)
            if "card" in data:
                dropped.add("card")
            single = _Obj(data.line if isinstance(data, _Obj) else None)
            for k in ("signals", "buttons"):
                if k in data:
                    single[k] = data[k]
                    single.key_lines[k] = _at(data, k)
            parsed = [_board_from(single, 0, dropped, problems)]
        else:
            problems.append(FileProblem(
                'no "boards" list in the file — nothing could be loaded', at=_at(data), fatal=True))
            parsed = [Board.numbered(0)]
        return cls(boards=parsed, dropped_fields=dropped, file_problems=problems)

    # ------------------------------------------------------------ saving

    def to_document(self) -> dict[str, Any]:
        """Always the ``boards`` shape, ``line`` (never ``pin``), sorted by
        line: the same input always produces the same file."""
        doc: dict[str, Any] = {"boards": []}
        for b in self.boards:
            signals = []
            for line, a in b.sorted_lines():
                sig: dict[str, Any] = {
                    "name": a.name.strip(),
                    "line": str(line),
                    "direction": direction_of(line).value,
                }
                if a.note.strip():
                    sig["note"] = a.note.strip()
                signals.append(sig)
            doc["boards"].append({"name": b.name.strip(), "signals": signals})
        return doc

    def to_json(self) -> str:
        return json.dumps(self.to_document(), indent=2) + "\n"

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")
        self.dropped_fields = set()

    # ------------------------------------------------------------ editing

    def signal_count(self) -> int:
        return sum(len(b.lines) for b in self.boards)

    def _next_free_board(self) -> Board:
        taken = {b.name.strip().lower() for b in self.boards}
        n = len(self.boards)
        while f"board {n + 1}" in taken:
            n += 1
        return Board.numbered(n)

    def add_board(self) -> int:
        """Add the next free "Board N". Returns its index."""
        self.boards.append(self._next_free_board())
        return len(self.boards) - 1

    def remove_board(self, index: int) -> bool:
        """The last board can't be removed."""
        if len(self.boards) <= 1 or not 0 <= index < len(self.boards):
            return False
        del self.boards[index]
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
        ``B2_P0_7``. Its direction follows from the line."""
        b = self.boards[board]
        if line not in b.lines:
            b.lines[line] = Assignment(self._unique_name(f"B{board + 1}_P{line.port}_{line.line}"))
        return b.lines[line]

    def move(self, board: int, src: Line, dst: Line) -> bool:
        """Move an assignment to a free line on the same board."""
        lines = self.boards[board].lines
        if src == dst or dst in lines or src not in lines:
            return False
        lines[dst] = lines.pop(src)
        return True

    # ------------------------------------------------------------ rules

    def issues(self) -> list[Issue]:
        out: list[Issue] = []
        for fp in sorted(self.file_problems, key=lambda f: f.at if f.at is not None else 1 << 30):
            board = fp.board if fp.board is not None and fp.board < len(self.boards) else None
            where = f"line {fp.at}: " if fp.at else ""
            out.append(Issue(Severity.ERROR, board, fp.line if board is not None else None,
                             f"file {where}{fp.message}", from_file=True))
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
                                (0, 0) if i.from_file or not i.line else (i.line.port, i.line.line)))
        return out

    def errors(self) -> list[Issue]:
        return [i for i in self.issues() if i.severity is Severity.ERROR]


def worst_on(issues: list[Issue], board: int, line: Line | None = None) -> Severity | None:
    """Worst issue on one 6509 line (``line`` given) or anywhere on a board."""
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
    _unknown(bf, _BOARD_KEYS, {"name", "signals"}, label, problems, index)
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
        _signal_from(sf, k, label, index, at, lines, dropped, problems)
    return Board(name=name, lines=lines)


def _signal_from(sf: Any, k: int, label: str, board: int, at: int | None,
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
    line, err, err_key = _resolve(sf)
    if line is None:
        problem(f"{who}: {err} — left out", err_key)
        return
    if line in lines:
        problem(f'{who}: {line} (pin {line.pin}) already has "{lines[line].name}" — left out',
                "line" if "line" in sf else "pin")
        return
    note = sf.get("note") or ""
    if not isinstance(note, str):
        problem(f'{who}: "note" must be text, not {_show(note)} — note dropped', "note", line)
        note = ""
    # "direction" is written for readers of the file. It must match the line:
    # P0-P5 (pins 1-50) are outputs, P6-P11 (pins 51-100) are inputs.
    expected = direction_of(line)
    if "direction" in sf:
        d = sf["direction"]
        if d not in ("input", "output"):
            problem(f'{who}: "direction" is {_show(d)}; it must be "input" or "output" — kept on '
                    f"{line} as an {expected.value} (fixed by the pin)", "direction", line)
        elif d != expected.value:
            half = ("pins 1-50 (P0-P5) are outputs" if expected is Direction.OUTPUT
                    else "pins 51-100 (P6-P11) are inputs")
            problem(f'{who}: says "{d}", but {line} (pin {line.pin}) is an {expected.value}: {half} — '
                    f"kept on {line} as an {expected.value}; move it if it belongs on the other half",
                    "direction", line)
    lines[line] = Assignment(name, note)


_LOOSE_LINE = re.compile(r"^\s*[Pp]?\s*(\d+)\s*([.,\-_/: ])\s*(\d+)\s*$")


def _resolve(sf: dict) -> tuple[Line | None, str, str | None]:
    """The line a signal maps to, or (None, why not, which field)."""
    has_line, has_pin = sf.get("line") is not None, sf.get("pin") is not None
    if has_line and has_pin:
        return None, (f'gives both "line" ({_show(sf["line"])}) and "pin" ({_show(sf["pin"])}); '
                      "give only one"), "pin"
    if has_line:
        v = sf["line"]
        if not isinstance(v, str):
            return None, f'"line" must be text like "P3.4", not {_show(v)}', "line"
        try:
            return Line.parse(v), "", None
        except ValueError as e:
            m = _LOOSE_LINE.match(v)
            if m:
                port, ln = int(m.group(1)), int(m.group(3))
                if port > 11:
                    return None, f'"{v}": there is no port {port} (ports are P0-P11)', "line"
                if ln > 7:
                    return None, f'"{v}": there is no line {ln} (lines are .0-.7)', "line"
                return None, f'"{v}" is not a 6509 line; did you mean "P{port}.{ln}"?', "line"
            return None, str(e), "line"
    if has_pin:
        pin = sf["pin"]
        if isinstance(pin, str) and pin.strip().isdigit():
            return None, f'"pin" is the text "{pin}"; write it as a number: {pin.strip()}', "pin"
        if not isinstance(pin, int) or isinstance(pin, bool):
            return None, f'"pin" must be a number 1-100, not {_show(pin)}', "pin"
        s = signal_at(pin)
        if s is None:
            return None, f"pin {pin} is out of range (1-100)", "pin"
        if s.kind is not SignalKind.DIO:
            return None, f"pin {pin} is {s}, not a DIO line", "pin"
        return s.line, "", None
    return None, 'needs a "line" (e.g. "P3.4") or a "pin" (1-100)', None