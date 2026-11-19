"""The editor's working copy of the open boards, the rules that decide whether
they are safe to save, file reading, and undo history.

No Qt in here: everything is plain Python. The GUI only edits a ``Project``
(the boards that are open) and asks it for ``issues()``.

A config file is a list of boards: ``{"boards": [{"name": ..., "type": ..., "signals": [...]}]}``.
A board has a type (NI 6509, NI 9205, ... see ``pinout.py``) and holds
*signals*: a name, the channel it maps to and an optional note. Direction
comes from the board type. On most boards it is fixed by the channel (6509:
P0-P5 outputs, P6-P11 inputs; 9205 inputs; 9264 and 9476 outputs). On the
NI 9401 the user sets it, and DIO0-3 and DIO4-7 each share one direction.

Which file a board came from and whether it has unsaved changes is the
window's business (``app.py``), not the model's.
"""

from __future__ import annotations

import copy
import itertools
import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from pinout import BOARD_TYPES, DEFAULT_TYPE, BoardType, Direction, Line, board_type, find_type


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
    #: The signal's extra setting where the board has one: an analog
    #: channel's range, a serial port's setup (``BoardType.options``).
    option: str = ""


_uids = itertools.count(1)


@dataclass
class Board:
    name: str
    lines: dict[Line, Assignment] = field(default_factory=dict)
    type: str = DEFAULT_TYPE  # a key of ``pinout.BOARD_TYPES``, e.g. "NI 9205"
    #: Stays the same through renames, undo and reload, so the window can
    #: remember which file this board belongs to.
    uid: int = field(default_factory=lambda: next(_uids), compare=False)

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
        """What this board's file holds, sorted by line, so the same board
        always produces the same file."""
        signals = []
        for line, a in self.sorted_lines():
            sig: dict[str, Any] = {"name": a.name.strip(), "line": str(line)}
            if self.bt.writes_direction:
                sig["direction"] = self.direction(line).value
            if self.bt.option_key:
                sig[self.bt.option_key] = a.option or self.bt.options[0]
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
    """Something in a file that didn't match what the editor expects. The
    loader reads the whole file, keeps everything it can, and records each
    problem with what was done about it ("left out", "ignored"). They block
    Save until the file is fixed and reopened, or the user accepts them."""

    message: str
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


@dataclass
class Project:
    boards: list[Board] = field(default_factory=list)
    #: Mismatches found in the files when they were loaded (see ``FileProblem``).
    file_problems: list[FileProblem] = field(default_factory=list, compare=False, repr=False)

    def clone(self) -> Project:
        return copy.deepcopy(self)

    # ------------------------------------------------------------ loading

    @classmethod
    def from_json(cls, text: str) -> Project:
        """Read a config file: ``{"boards": [{"name", "type", "signals"}, ...]}``
        (a single board on its own, without ``"boards"``, also loads). Never raises for
        problems in the content: they are collected in ``file_problems``."""
        def fatal(why: str) -> Project:
            return cls(file_problems=[FileProblem(f"{why} — nothing could be loaded", fatal=True)])

        problems: list[FileProblem] = []
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("boards"), list) and data["boards"]:
                return cls([_board_from(bf, i, problems) for i, bf in enumerate(data["boards"])], problems)
            if isinstance(data, dict) and "signals" in data:
                return cls([_board_from(data, 0, problems)], problems)
        except RecursionError:  # brackets nested thousands deep
            return fatal("nested far too deeply to be a board file")
        except json.JSONDecodeError as e:
            return fatal(f"line {e.lineno}: not valid JSON ({e.msg}, column {e.colno})")
        return fatal('the file must be { "boards": [ { "name": ..., "type": ..., "signals": [...] } ] } with at least one board')

    @classmethod
    def load(cls, path: str | Path) -> Project:
        path = Path(path)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise LoadError(f"{path.name} is too large to be a board file "
                                f"(over {MAX_FILE_BYTES // 1_000_000} MB)")
            # utf-8-sig: also reads files saved with a byte-order mark (Windows Notepad).
            return cls.from_json(path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError:
            raise LoadError(f"{path.name} is not a UTF-8 text file") from None
        except OSError as e:
            raise LoadError(f"reading {path}: {e.strerror or e}") from None

    # ------------------------------------------------------------ editing

    def add_board(self, type: str = DEFAULT_TYPE) -> int:
        """Add a blank board named after its kind ("NI 6509 2"). Returns its index."""
        taken = {b.name.strip().lower() for b in self.boards}
        base, n = board_type(type).kind, 1
        while f"{base} {n}".lower() in taken:
            n += 1
        self.boards.append(Board(name=f"{base} {n}", type=type))
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

    def remove_board(self, index: int) -> None:
        """Take a board out, with the problems found in its file."""
        del self.boards[index]
        self.file_problems = [fp for fp in self.file_problems if fp.board != index]
        for fp in self.file_problems:
            if fp.board is not None and fp.board > index:
                fp.board -= 1

    def assign(self, board: int, line: Line, name: str) -> Assignment:
        """Put a signal on a free channel. Where the user sets the direction
        it starts as whatever its group already is; where the board has an
        extra setting it starts as the first choice."""
        b = self.boards[board]
        direction = None
        if b.bt.fixed_direction(line) is None:
            direction = b.group_direction(line) or Direction.INPUT
        b.lines[line] = Assignment(name, direction=direction, option=b.bt.options[0] if b.bt.options else "")
        return b.lines[line]

    # ------------------------------------------------------------ rules

    def issues(self) -> list[Issue]:
        out = [Issue(Severity.ERROR, fp.board, fp.line, f"{fp.file or 'file'}: {fp.message}", from_file=True)
               for fp in self.file_problems]
        board_names: dict[str, list[int]] = {}
        names: dict[str, list[tuple[int, Line]]] = {}  # upper-case signal name -> where it is used

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
                out += [Issue(Severity.ERROR, bi, None,
                              f'board name "{self.boards[bi].name.strip()}" is used {len(group)} times; '
                              "each board needs its own name") for bi in group]
        for uses in names.values():
            if len(uses) > 1:
                for bi, line in uses:
                    others = [str(ol) if ob == bi else f"{self.boards[ob].label} {ol}"
                              for ob, ol in uses if (ob, ol) != (bi, line)]
                    out.append(Issue(Severity.ERROR, bi, line,
                                     f'{self.boards[bi].label} · {line}: name '
                                     f'"{self.boards[bi].lines[line].name.strip()}" is also used on '
                                     f"{', '.join(others)}"))

        # File problems first, then errors, then by board and position.
        out.sort(key=lambda i: (not i.from_file, -i.severity, i.board if i.board is not None else -1,
                                i.line.index if i.line else 0))
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

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)


# ---------------------------------------------------------------- reading a file


def _show(v: Any) -> str:
    text = json.dumps(v)
    return text if len(text) <= 40 else text[:37] + "..."


def _unknown(obj: dict, allowed: set[str], who: str, problems: list[FileProblem], board: int) -> None:
    problems += [FileProblem(f'{who}: unknown field "{k}" — ignored', board=board)
                 for k in obj if k not in allowed]


def _board_from(bf: Any, index: int, problems: list[FileProblem]) -> Board:
    def problem(msg: str) -> None:
        problems.append(FileProblem(msg, board=index))

    default = f"Board {index + 1}"
    if not isinstance(bf, dict):
        problem(f"board {index + 1} must be an object {{ ... }}, not {_show(bf)} — an empty {default} is used")
        return Board(default)
    name = bf.get("name", default)
    if not isinstance(name, str):
        problem(f'board {index + 1}: "name" must be text, not {_show(name)} — named "{default}"')
        name = default
    label = name.strip() or f"board {index + 1}"
    _unknown(bf, {"name", "type", "signals"}, label, problems, index)
    bt = board_type(DEFAULT_TYPE)  # files from before "type" existed are all 6509s
    if "type" in bf:
        found = find_type(bf["type"]) if isinstance(bf["type"], str) else None
        if found is None:
            problem(f'{label}: unknown board "type" {_show(bf["type"])} (known: '
                    f'{", ".join(BOARD_TYPES)}) — read as an {bt.key}')
        bt = found or bt
    signals = bf.get("signals")
    if not isinstance(signals, list):
        problem(f'{label}: "signals" must be a list — loaded with no signals')
        signals = []
    lines: dict[Line, Assignment] = {}
    for k, sf in enumerate(signals):
        _signal_from(sf, k, label, index, bt, lines, problems)
    return Board(name=name, lines=lines, type=bt.key)


def _signal_from(sf: Any, k: int, label: str, board: int, bt: BoardType,
                 lines: dict[Line, Assignment], problems: list[FileProblem]) -> None:
    def problem(msg: str, line: Line | None = None) -> None:
        problems.append(FileProblem(msg, board=board, line=line))

    if not isinstance(sf, dict):
        problem(f"{label} · signal #{k + 1} must be an object {{ ... }}, not {_show(sf)} — left out")
        return
    name = sf.get("name")
    who = f"{label} · signal " + (f'"{name}"' if isinstance(name, str) and name.strip() else f"#{k + 1}")
    known = {"name", "line", "note", bt.option_key} | ({"direction"} if bt.writes_direction else set())
    _unknown(sf, known, who, problems, board)
    if not isinstance(name, str):
        problem(f'{who}: "name" is {"missing" if name is None else f"not text ({_show(name)})"} — left out')
        return
    try:
        if not isinstance(sf.get("line"), str):
            raise ValueError(f'needs a "line" (e.g. "{bt.example}")')
        line = bt.parse(sf["line"])
    except ValueError as e:
        problem(f"{who}: {e} — left out")
        return
    if line in lines:
        problem(f'{who}: {line} already has "{lines[line].name}" — left out')
        return
    note = sf.get("note") or ""
    if not isinstance(note, str):
        problem(f'{who}: "note" must be text, not {_show(note)} — note dropped', line)
        note = ""
    option = ""  # the extra setting some boards have (a range, a serial setup)
    if bt.option_key:
        option = sf.get(bt.option_key)
        if option not in bt.options:
            option = bt.options[0]
            problem(f'{who}: "{bt.option_key}" must be one of {", ".join(bt.options)} — set to "{option}"', line)
    d = sf.get("direction")
    fixed = bt.fixed_direction(line)
    if fixed is not None:  # "direction" is written for readers of the file and must match
        if bt.writes_direction and "direction" in sf and d != fixed.value:
            problem(f'{who}: "direction" is {_show(d)}, but {line} is an {fixed.value} on an {bt.key} '
                    f"— kept as an {fixed.value}", line)
        lines[line] = Assignment(name, note, option=option)
        return
    # Set by the user (NI 9401), and shared with the rest of its group.
    group = bt.group(line)
    shared = next((a.direction for other, a in lines.items() if bt.group(other) == group), None)
    direction = shared or (Direction(d) if d in ("input", "output") else Direction.INPUT)
    if d != direction.value:
        problem(f'{who}: "direction" is {_show(d)}; it must be "input" or "output", the same for all of '
                f"{group} — kept as an {direction.value}", line)
    lines[line] = Assignment(name, note, direction, option)
