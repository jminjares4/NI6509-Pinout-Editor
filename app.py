"""The editor window.

Three panes, left to right: the list of boards (HWIL, then Ground Software),
the board you picked drawn as its connector, and a side panel that edits the
pin, port or channel you clicked.

A config file holds a list of cards: one file for the HWIL cards, another for
the Ground Software modules. ``self.files`` remembers, per card, which file it
is in and its text when last read or written; ``self.members`` remembers which
cards each file held. Together that is how the window knows what is unsaved.

Pattern: every change goes through ``self.edit(...)``, which records an undo
snapshot, applies the change to ``self.project``, and calls ``refresh()``.
``refresh()`` redraws everything from the project, so what is on screen can
never disagree with it. Widget signals are ignored while ``refresh()`` runs
(``self._updating``) so it doesn't feed back.
"""

from __future__ import annotations

import html
import json
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QIODevice, QSaveFile, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QPainter, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from model import Assignment, Board, Direction, History, LoadError, Project, Severity, worst_on
from pinout import BOARD_TYPES, DOMAINS, MICROGATE, SERIAL_VIEW, Line, SignalKind
import theme as t
from connector import ConnectorWidget

APP_NAME = "Board Setup"
FILE_FILTER = "Board config (*.json)"


@dataclass
class FileState:
    """Where a card lives on disk, and the text last read from or written
    there. ``saved`` is "" when what is on disk isn't what is shown (older
    format, problems in the file, or several boards sharing one file)."""

    path: Path | None
    saved: str


def _label(text: str = "", role: str | None = None) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    return lab


def _button(text: str, role: str | None = None, tip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    if tip:
        b.setToolTip(tip)
    return b


def _mark(sev: Severity | None) -> str:
    """A problem marker for a tab's text, so it doesn't rely on color alone."""
    return "✖  " if sev is Severity.ERROR else "⚠  " if sev is Severity.WARNING else ""


class Drop(QComboBox):
    """A drop-down menu with a visible arrow."""

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setPen(QColor(t.DIM if self.isEnabled() else t.MUTED))
        p.drawText(self.rect().adjusted(0, 0, -10, 0),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "▾")


class _PortDrawing:
    """What the connector drawing needs to show one serial port: the pins its
    setup uses, marked in or out. Stands in for a ``Board``."""

    def __init__(self, view, setup: str) -> None:
        self.bt = view
        self.lines = {line: Assignment("out" if view.fixed_direction(line) is Direction.OUTPUT else "in")
                      for line in view.used_by(setup)}

    def direction(self, line: Line) -> Direction:
        return self.bt.fixed_direction(line)


class PortCell(QFrame):
    """One serial port on the MicroGate panel: its name, its setup, and its
    connector with the pins that setup uses. Click anywhere on it to edit."""

    clicked = pyqtSignal(int)

    def __init__(self, port: int) -> None:
        super().__init__(objectName="cell")
        self.port = port
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 0)
        v.setSpacing(0)
        head = QHBoxLayout()
        self.title = QLabel(f"Port {port}")
        self.sub = _label("", "dim")
        self.sub.setTextFormat(Qt.TextFormat.PlainText)  # port names come from files
        head.addWidget(self.title)
        head.addWidget(self.sub)
        head.addStretch(1)
        v.addLayout(head)
        self.conn = ConnectorWidget()
        self.conn.readonly = True
        self.conn.pressed.connect(lambda: self.clicked.emit(self.port))
        v.addWidget(self.conn, 0, Qt.AlignmentFlag.AlignHCenter)

    def mousePressEvent(self, e) -> None:
        self.clicked.emit(self.port)

    def show_port(self, a: Assignment | None, selected: bool) -> None:
        self.title.setStyleSheet(f"font-size: 12pt; font-weight: 600; color: {t.TEXT if a else t.DIM};")
        self.sub.setText(f"·  {a.name.strip() or '(no name)'}  ·  {a.option}" if a else "·  not used")
        self.conn.show_board(_PortDrawing(SERIAL_VIEW, a.option if a else ""), 0, [], None)
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)


class MainWindow(QMainWindow):
    def __init__(self, paths: list[Path] | None = None) -> None:
        super().__init__()
        self.project = Project()
        self.history = History()
        self.files: dict[int, FileState] = {}  # card uid -> its file
        self.members: dict[Path, list[int]] = {}  # file -> the cards in it when last read or written
        self.last_dir: Path | None = None
        self.domain = DOMAINS[0]             # the area being shown
        self.kind = next(iter(BOARD_TYPES))  # the kind of board being shown
        self.board: int | None = None        # the card being shown (index in the project)
        self.sel: Line | None = None         # the pin, port or channel being edited
        self._box_for: object = None         # what the side panel was last filled in for
        self._note: tuple[str, bool] | None = None  # a passing message for the bottom line
        self._updating = False
        self.editing = True                  # off: look, don't touch (how opened files start)

        self.resize(1440, 940)
        self.setMinimumSize(1000, 620)  # the widest board (NI 6509) fits without scrolling from 1340 wide
        self.setAcceptDrops(True)
        self._updating = True  # widgets announce changes while they are being built
        self._build_toolbar()
        self._build_central()
        self._updating = False
        if paths:
            self.open_paths(paths)
        self.refresh()

    # ================================================================ dialogs

    def ask_unsaved(self, text: str) -> str:
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, text, parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)  # card names come from files
        box.setInformativeText("Save first?")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {save: "save", discard: "discard"}.get(box.clickedButton(), "cancel")

    def ask_open_paths(self) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open board files", self._dialog_dir(),
                                                f"{FILE_FILTER};;All files (*)")
        return paths

    def ask_save_path(self, area: str) -> str | None:
        """Where the cards of an area go when they have no file yet."""
        start = Path(self._dialog_dir()) / (area.lower().replace(" ", "_") + ".json")
        return QFileDialog.getSaveFileName(self, f"Save the {area} cards as", str(start), FILE_FILTER)[0] or None

    def _dialog_dir(self) -> str:
        b = self._b()
        here = self._state(b).path if b else None
        return str(here.parent if here else self.last_dir or Path.cwd())

    # ================================================================ layout

    def _build_toolbar(self) -> None:
        K = QKeySequence.StandardKey

        def action(text: str, slot: Callable, key, tip: str) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(tip)
            return a

        self.act_open = action("Open", self.open_dialog, K.Open, "Open board files, or add more to the ones open")
        self.act_edit = action("Edit", self.set_editing, "Ctrl+E", "Opened files are view-only. Turn this on to change them.")
        self.act_edit.setCheckable(True)
        self.act_save = action("Save", self.save_all, K.Save, "Save every file whose cards changed")
        self.act_undo = action("Undo", self.undo, K.Undo, "Undo the last change")
        self.act_redo = action("Redo", self.redo, K.Redo, "Redo")
        self.addAction(self.act_redo)  # keyboard only: Ctrl+Shift+Z

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(tb)
        tb.addActions([self.act_open, self.act_edit, self.act_save])
        tb.widgetForAction(self.act_save).setObjectName("primary")  # the one filled button
        tb.addSeparator()
        tb.addAction(self.act_undo)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.chip_dirty = _label("", "chip-warn")
        self.chip_dirty_action = tb.addWidget(self.chip_dirty)  # shown and hidden through its action

    def _build_central(self) -> None:
        central = QWidget(objectName="central")
        across = QHBoxLayout(central)
        across.setContentsMargins(0, 0, 0, 0)
        across.setSpacing(0)

        # left: every board, by area
        side = QFrame(objectName="side")
        side.setFixedWidth(210)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 12, 10, 12)
        self.nav = QTreeWidget(objectName="nav")
        self.nav.setHeaderHidden(True)
        self.nav.setRootIsDecorated(False)
        self.nav.setItemsExpandable(False)
        self.nav.setIndentation(0)  # rows are indented by their text, so the highlight spans the row
        self.nav.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.nav.currentItemChanged.connect(self._nav_changed)
        self.add_btn = _button("", "ghost")
        self.add_btn.clicked.connect(self.add_board)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search signals")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Find a signal on any card by its name, channel or pin number")
        self.search.textChanged.connect(lambda _text: self.refresh())
        sv.addWidget(self.search)
        sv.addWidget(self.nav, 1)
        sv.addWidget(self.add_btn)
        across.addWidget(side)

        right = QVBoxLayout()
        right.setContentsMargins(12, 14, 12, 6)
        right.setSpacing(10)
        panes = QHBoxLayout()
        panes.setSpacing(12)
        right.addLayout(panes, 1)
        across.addLayout(right, 1)

        # middle: the board
        self.card = QFrame(objectName="card")
        cv = QVBoxLayout(self.card)
        cv.setContentsMargins(10, 16, 10, 12)
        cv.setSpacing(4)
        self.title = _label("", "h2")
        self.hint = _label("", "dim")
        cv.addWidget(self.title)
        cv.addWidget(self.hint)
        cv.addSpacing(8)
        self.body = QStackedWidget()
        cv.addWidget(self.body, 1)
        panes.addWidget(self.card, 1)

        # a board drawn as pins: 6509, cDAQ modules, the analog card's channels
        self.connector = ConnectorWidget()
        self.connector.pinClicked.connect(self._pin_clicked)
        self.connector.cleared.connect(lambda: self.select(None))
        self.connector.escapePressed.connect(lambda: self.select(None))
        self.connector.deletePressed.connect(self.clear_selected)
        self.connector.stepPressed.connect(self._step)
        self.body.addWidget(self._scroll(self.connector))

        # a serial card: its four ports
        ports = QWidget()
        grid = QGridLayout(ports)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.cells = [PortCell(n) for n in range(MICROGATE.PORTS)]
        for n, cell in enumerate(self.cells):
            cell.clicked.connect(self._port_clicked)
            grid.addWidget(cell, n // 2, n % 2)
        self.body.addWidget(self._scroll(ports, resizable=True))

        # nothing of this kind yet
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.setSpacing(12)
        ev.addStretch(1)
        self.empty_note = _label("", "dim")
        self.empty_add = _button("", "primary")
        self.empty_add.setMinimumHeight(40)
        self.empty_add.clicked.connect(self.add_board)
        for w in (self.empty_note, self.empty_add):
            ev.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        ev.addStretch(2)
        self.body.addWidget(empty)

        # right: the pin, port or channel you clicked, then the card itself
        self.panel = QFrame(objectName="card")
        self.panel.setFixedWidth(250)
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(16, 16, 16, 16)
        pv.setSpacing(8)
        self.box_title = _label("", "h2")
        self.box_hint = _label("", "dim")
        self.box_hint.setWordWrap(True)
        self.box_name = QLineEdit()
        self.box_name.setPlaceholderText("name")
        self.box_name.textEdited.connect(self._name_edited)
        self.box_name.returnPressed.connect(lambda: self._step(1, off_the_end=True))  # Enter: on to the next one
        for key, d in ((Qt.Key.Key_Down, 1), (Qt.Key.Key_Up, -1)):  # and the arrows step both ways
            arrow = QShortcut(QKeySequence(key), self.box_name)
            arrow.setContext(Qt.ShortcutContext.WidgetShortcut)
            arrow.activated.connect(lambda d=d: self._step(d))
        self.box_option = Drop()
        self.box_option.activated.connect(self._option_chosen)
        self.box_dir = Drop()
        self.box_dir.addItems(["input", "output"])  # relabelled with the group they apply to
        self.box_dir.activated.connect(self._direction_chosen)
        self.box_clear = _button("")
        self.box_clear.clicked.connect(self.clear_selected)
        self.box_unlock = _button("Edit", "primary", "This file is open view-only. Turn editing on (same as Edit in the toolbar).")
        self.box_unlock.clicked.connect(lambda: self.set_editing(True))
        self.box_issue = QLabel()  # what is wrong with this one, right under its name
        self.box_issue.setWordWrap(True)
        self.box_issue.setTextFormat(Qt.TextFormat.PlainText)
        # every field has a visible label: a placeholder alone disappears once you type
        self.lab_name, self.lab_option, self.lab_dir = _label("Name", "dim"), _label("", "dim"), _label("Direction", "dim")
        for w in (self.box_title, self.box_hint, self.lab_name, self.box_name, self.box_issue, self.lab_option, self.box_option,
                  self.lab_dir, self.box_dir, self.box_clear, self.box_unlock):
            pv.addWidget(w)
        # what is on this card: click a row to go to it
        self.list_title = _label("", "dim")
        self.signals = QListWidget(objectName="signals")
        self.signals.setFont(self.connector.mono)
        self.signals.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.signals.itemClicked.connect(lambda item: self.select(item.data(Qt.ItemDataRole.UserRole)))
        pv.addSpacing(10)
        pv.addWidget(self.list_title)
        pv.addWidget(self.signals, 1)
        self.name_label = _label("", "dim")
        self.card_name = QLineEdit()
        self.card_name.setToolTip("This card's name. It is written in its file.")
        self.card_name.textEdited.connect(self._card_name_edited)
        self.file_label = _label("", "dim")
        self.remove_btn = _button("", tip="Remove this card. The next Save takes it out of its file.")
        self.remove_btn.clicked.connect(self.remove_board)
        for w in (self.name_label, self.card_name, self.file_label, self.remove_btn):
            pv.addWidget(w)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.panel)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(lambda: self.select(None))
        panes.addWidget(self.panel)

        # the one line that says whether everything is all right
        bottom = QHBoxLayout()
        self.status_line = QPushButton(objectName="statusLine")
        self.status_line.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_line.clicked.connect(self._go_to_problem)
        self.ignore_btn = _button("Leave out && continue", "fix",
                                  "Accept what was read from the file: entries marked \"left out\" or "
                                  "\"ignored\" are not saved. Can be undone.")
        self.ignore_btn.clicked.connect(self.accept_file_problems)
        bottom.addWidget(self.status_line, 1)
        bottom.addWidget(self.ignore_btn)
        right.addLayout(bottom)
        self.setCentralWidget(central)

        # These show card and file names, which come from files: plain text,
        # so a name that looks like HTML is shown, not rendered.
        for lab in (self.title, self.hint, self.box_title, self.box_hint, self.empty_note, self.file_label):
            lab.setTextFormat(Qt.TextFormat.PlainText)

    @staticmethod
    def _scroll(widget: QWidget, resizable: bool = False) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(resizable)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        for w in (scroll.viewport(), widget):  # let the card show through
            w.setAutoFillBackground(False)
        return scroll

    # ================================================================ state helpers

    def _of_kind(self) -> list[int]:
        """Project indexes of the cards of the kind being shown."""
        return [i for i, b in enumerate(self.project.boards) if b.type == self.kind]

    def _b(self) -> Board | None:
        """The card being shown, if there is one."""
        if self.board is None or not 0 <= self.board < len(self.project.boards):
            return None
        return self.project.boards[self.board]

    def _state(self, b: Board) -> FileState:
        """The file a card belongs to. A card not seen before has none yet
        and counts as saved the way it is now."""
        if b.uid not in self.files:
            self.files[b.uid] = FileState(None, b.to_json())
        return self.files[b.uid]

    def board_dirty(self, b: Board) -> bool:
        return b.to_json() != self._state(b).saved

    def unsaved(self) -> list[Board]:
        return [b for b in self.project.boards if self.board_dirty(b)]

    def _in_file(self, path: Path) -> list[Board]:
        return [b for b in self.project.boards if self._state(b).path == path]

    def dirty_paths(self) -> list[Path]:
        """Files that no longer match their cards: a card in one changed, was added or was removed."""
        paths = dict.fromkeys(list(self.members) + [self._state(b).path for b in self.project.boards])
        return [p for p in paths if p is not None and (
            [b.uid for b in self._in_file(p)] != self.members.get(p) or any(map(self.board_dirty, self._in_file(p))))]

    def _board_for(self, path: Path) -> int | None:
        """Index of the first open card that lives in ``path``."""
        path = path.resolve()
        return next((i for i, b in enumerate(self.project.boards)
                     if self._state(b).path == path), None)

    def set_editing(self, on: bool) -> None:
        """View-only or editable. Opened files start view-only, so looking at
        a config can't change it by accident."""
        self.editing, self._note = on, None  # the Edit button shows which it is
        self.refresh()

    def edit(self, change: Callable[[], object], key: object = None, opening: bool = False) -> None:
        """Record an undo snapshot, apply ``change``, refresh every view.
        Nothing changes while the window is view-only, except opening files."""
        if not (self.editing or opening):
            self.note("View only. Click Edit to make changes.")
            return
        self.history.record(self.project, key)
        self._note = None  # whatever the bottom line was saying is out of date now
        change()
        self.refresh()

    def note(self, text: str, error: bool = False) -> None:
        """Show a passing message on the bottom line. It gives way to the
        usual line (the first problem, or all clear) at the next change, or
        by itself after a while; errors stay up longer."""
        self._note = (text, error)
        self._refresh_status()
        QTimer.singleShot(15000 if error else 7000, lambda: self._forget_note(text))

    def _forget_note(self, text: str) -> None:
        if self._note and self._note[0] == text:
            self._note = None
            self._refresh_status()

    def jump(self, board: int, line: Line | None = None) -> None:
        """Show a card (switching area and kind as needed) and, optionally,
        open the editing box on one of its pins, ports or channels."""
        b = self.project.boards[board]
        self.domain, self.kind, self.board = b.bt.domain, b.type, board
        self.select(line if line in b.bt.lines else None)

    def select(self, line: Line | None) -> None:
        self.sel = line
        self.history.break_group()
        self.refresh()
        if line is not None:
            self.box_name.setFocus()
            self.box_name.selectAll()
        elif self.body.currentIndex() == 0:
            self.connector.setFocus()

    # ================================================================ file operations

    def _keep_going_despite_changes(self) -> bool:
        changed = self.unsaved()
        if not changed and not self.dirty_paths():
            return True
        names = ", ".join(b.label for b in changed[:4]) + ("…" if len(changed) > 4 else "")
        answer = self.ask_unsaved(f"There are unsaved changes{': ' + names if names else ''}.")
        if answer == "save":
            return self.save_all()
        return answer == "discard"

    def open_dialog(self) -> None:
        paths = self.ask_open_paths()
        if paths:
            self.open_paths([Path(p) for p in paths])

    def open_paths(self, paths: list[Path]) -> bool:
        """Open board files next to the cards already open. A folder means
        every .json in it. One Undo takes them all out again."""
        files: list[Path] = []
        for path in paths:
            files += sorted(path.glob("*.json")) if path.is_dir() else [path]
        loads, failed, already = [], [], []
        seen: set[Path] = set()
        for path in files:
            if path.resolve() in seen:  # named twice, e.g. a file and the folder it is in
                continue
            seen.add(path.resolve())
            if self._board_for(path) is not None:
                already.append(path)
                continue
            try:
                loaded = Project.load(path)
            except LoadError as e:  # can't read the file at all
                failed.append(f"{path.name} ({e})")
                continue
            fatal = next((f for f in loaded.file_problems if f.fatal), None)
            if fatal:
                failed.append(f"{path.name}: {fatal.message}")
                continue
            loads.append((path, loaded))
        problems = sum(len(loaded.file_problems) for _, loaded in loads)
        first = None
        if loads:
            def apply() -> None:
                nonlocal first
                for path, loaded in loads:
                    base = self.project.add_boards(loaded.boards, loaded.file_problems, path.name)
                    first = base if first is None else first

            self.edit(apply, opening=True)
            self.editing = False  # opened to look at; Edit turns changes on
            for path, loaded in loads:
                for b in loaded.boards:  # they are saved back to the file they came from
                    self.files[b.uid] = FileState(path.resolve(), "" if loaded.file_problems else b.to_json())
                self.members[path.resolve()] = [b.uid for b in loaded.boards]
                self.last_dir = path.resolve().parent
            # Undo can't close a file: its cards would count as removed and Save would empty it.
            self.history = History()
            self.jump(first)
        elif already and not failed:
            self.jump(self._board_for(already[0]))
        boards = sum(len(loaded.boards) for _, loaded in loads)
        msg = [f"Opened {boards} card(s) from {len(loads)} file(s)"] if loads else []
        if already:
            msg.append(f"{len(already)} already open")
        if problems:
            msg.append(f"{problems} problem(s) in the file(s)")
        msg += [f"Could not open {f}" for f in failed]
        if msg and (failed or not problems):  # with file problems, the bottom line shows those instead
            self.note(". ".join(msg) + ".", error=bool(failed))
        return bool(loads)

    def accept_file_problems(self) -> None:
        if not self.editing:
            return
        self.edit(lambda: self.project.file_problems.clear())
        self.note("Accepted what was read. Saving writes the files as shown here (Undo to go back).")

    def save_all(self) -> bool:
        """Write every file whose cards changed. New cards join the open file
        of their area; if there is none (or several), you pick the file."""
        errors = self.project.errors()
        if errors:
            self._go_to_problem()
            self.note(f"Not saved: fix the {len(errors)} problem(s) first. Showing the first one: "
                      f"{errors[0].message}", error=True)
            return False
        for area in DOMAINS:
            new = [b for b in self.project.boards if b.bt.domain == area and self._state(b).path is None]
            if not new:
                continue
            here = {self._state(b).path for b in self.project.boards if b.bt.domain == area} - {None}
            path = next(iter(here)) if len(here) == 1 else self.ask_save_path(area)
            if not path:
                return False
            for b in new:
                self._state(b).path = Path(path).resolve()
        todo = self.dirty_paths()
        done = [p for p in todo if self._write(p)]
        self.refresh()
        if len(done) != len(todo):
            return False  # _write said which one failed
        self.note(f"Saved {', '.join(p.name for p in done)}." if done else "Nothing to save.")
        return True

    def _write(self, path: Path) -> bool:
        cards = self._in_file(path)
        text = json.dumps({"boards": [b.to_document() for b in cards]}, indent=2) + "\n"
        # QSaveFile writes beside the file, then swaps it in: a crash or a
        # full disk halfway through can't leave half a config file behind.
        f = QSaveFile(str(path))
        if not (f.open(QIODevice.OpenModeFlag.WriteOnly) and f.write(text.encode()) >= 0 and f.commit()):
            self.note(f"Not saved: could not write {path}: {f.errorString()}", error=True)
            return False
        for b in cards:
            self.files[b.uid] = FileState(path, b.to_json())
        self.members[path] = [b.uid for b in cards]
        self.last_dir = path.parent
        return True

    # ================================================================ undo

    def undo(self) -> None:
        p = self.history.undo(self.project)
        if p is not None:
            self.project, self._note = p, None
            self.refresh()

    def redo(self) -> None:
        p = self.history.redo(self.project)
        if p is not None:
            self.project, self._note = p, None
            self.refresh()

    # ================================================================ the list of boards

    def _nav_changed(self, item: QTreeWidgetItem | None, _old=None) -> None:
        """A row of the list was picked: a kind of board, or one card."""
        what = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if self._updating or what is None:
            return
        if isinstance(what, tuple):  # a search result: a card and a pin on it
            self.jump(*what)
        elif isinstance(what, int):
            self.jump(what)
        else:
            self.kind, self.domain = what, BOARD_TYPES[what].domain
            mine = self._of_kind()
            self.board = mine[0] if mine else None
            self.select(None)

    def add_board(self) -> None:
        """Add a card of the kind being shown. It gets a file when saved."""
        if not self.editing:
            return
        kind, box = self.kind, []
        self.edit(lambda: box.append(self.project.add_board(kind)))
        self._state(self.project.boards[box[0]])  # nothing to save until it is edited
        self.jump(box[0], BOARD_TYPES[kind].lines[0])  # ready to type the first name

    def remove_board(self) -> None:
        """Remove the current card. The next Save takes it out of its file."""
        b, index = self._b(), self.board
        if b is None or not self.editing:
            return
        self.edit(lambda: self.project.remove_board(index))
        mine = self._of_kind()  # stay with this kind: the next card, if any
        self.board = next((i for i in mine if i >= index), mine[-1]) if mine else None
        self.select(None)
        self.note("Card removed. Save takes it out of its file; Undo brings it back.")

    def _card_name_edited(self, text: str) -> None:
        b = self._b()
        if b is not None and not self._updating:
            self.edit(lambda: setattr(b, "name", text), ("card-name", b.uid))

    # ================================================================ pins, ports, channels

    def _pin_clicked(self, pin: int) -> None:
        b = self._b()
        s = b.bt.signal_at(pin) if b else None
        if s is None:
            return
        if s.kind is not SignalKind.LINE:
            self.select(None)
            self.note(f"Pin {pin} is {s} ({s.about}). It can't carry a signal.")
        else:
            self.select(s.line)

    def _port_clicked(self, port: int) -> None:
        b = self._b()
        if b is not None:
            self.select(b.bt.parse(f"PORT{port}"))

    def _name_edited(self, text: str) -> None:
        """Typing in the box: a name puts a signal on what was clicked, an
        empty name takes it off again."""
        b, line, index = self._b(), self.sel, self.board
        if b is None or line is None:
            return
        key = ("name", b.uid, line)  # one undo step for the whole word, not one per letter
        if not text.strip():
            if line in b.lines:
                self.edit(lambda: b.lines.pop(line), key)
        elif line in b.lines:
            self.edit(lambda: setattr(b.lines[line], "name", text), key)
        else:
            self.edit(lambda: self.project.assign(index, line, text), key)

    def _step(self, d: int, off_the_end: bool = False) -> None:
        """Go to the next (+1) or previous (-1) pin, port or channel, so a
        card can be read or filled in from the keyboard. Enter past the last
        one finishes; the arrows stop there."""
        b = self._b()
        if b is None:
            return
        lines = b.bt.lines
        i = lines.index(self.sel) + d if self.sel in lines else (0 if d > 0 else len(lines) - 1)
        if 0 <= i < len(lines):
            self.select(lines[i])
        elif off_the_end:
            self.select(None)

    def _option_chosen(self, index: int) -> None:
        """A range or a setup was picked from the menu."""
        b, line, i = self._b(), self.sel, self.board
        if b is None or line is None or not 0 <= index < len(b.bt.options):
            return
        value = b.bt.options[index]
        if line not in b.lines:  # picked before naming it: it still needs a name
            self.edit(lambda: setattr(self.project.assign(i, line, ""), "option", value))
        elif b.lines[line].option != value:
            self.edit(lambda: setattr(b.lines[line], "option", value))

    def _direction_chosen(self, index: int) -> None:
        """NI 9401: the direction applies to the pin's whole group of four."""
        b, line = self._b(), self.sel
        if b is None or line is None or line not in b.lines or not self.editing:
            return
        d = Direction.OUTPUT if index else Direction.INPUT
        if d is not b.direction(line):
            self.edit(lambda: b.set_direction(line, d))
            self.note(f"{b.bt.group(line)} are now {d.value}s.")

    def clear_selected(self) -> None:
        b, line = self._b(), self.sel
        if b is not None and line is not None and line in b.lines:
            self._box_for = None  # refill the box: its name is gone
            self.edit(lambda: b.lines.pop(line))

    def _go_to_problem(self) -> None:
        """Show whatever the bottom line is complaining about."""
        issues = self.project.issues()
        target = next((i for i in issues if i.board is not None), None)
        if target is not None:
            self._note = None
            self.jump(target.board, target.line)

    # ================================================================ refresh

    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh(self) -> None:
        p = self.project
        mine = self._of_kind()
        if self.board not in mine:
            self.board, self.sel = (mine[0] if mine else None), None
        b = self._b()
        if b is None or self.sel not in b.bt.lines:
            self.sel = None
        bt = BOARD_TYPES[self.kind]
        issues = p.issues()
        unsaved = {x.uid for x in self.unsaved()}

        # left: areas, the kinds of board in each, and the cards of each kind
        self.nav.clear()
        query = self.search.text().strip().lower()
        for i, x in enumerate(p.boards) if query else ():  # searching: the matches replace the list
            for line, sig in x.sorted_lines():
                if query in f"{sig.name} {line} pin {x.bt.pin_text(x.bt.pin_of(line))} {x.label}".lower():
                    hit = QTreeWidgetItem(self.nav, [f"{sig.name.strip() or '(no name)'}  ·  {line}  ·  {x.label}"])
                    hit.setData(0, Qt.ItemDataRole.UserRole, (i, line))
                    if (i, line) == (self.board, self.sel):
                        self.nav.setCurrentItem(hit)
        if query and not self.nav.topLevelItemCount():
            QTreeWidgetItem(self.nav, ["No signal matches"]).setFlags(Qt.ItemFlag.NoItemFlags)
        for area in () if query else DOMAINS:
            head = QTreeWidgetItem(self.nav, [area.upper()])
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a heading: can't be picked
            head.setForeground(0, QColor(t.DIM))
            for k in (x for x in BOARD_TYPES.values() if x.domain == area):
                cards = [i for i, x in enumerate(p.boards) if x.type == k.key]
                row = QTreeWidgetItem(head, [f"   {k.kind}"])
                row.setData(0, Qt.ItemDataRole.UserRole, k.key)
                row.setForeground(0, QColor(t.TEXT if cards else t.DIM))
                if k.key == self.kind and b is None:
                    self.nav.setCurrentItem(row)
                for i in cards:
                    sev = worst_on(issues, i)
                    leaf = QTreeWidgetItem(row, [f"       {_mark(sev)}{p.boards[i].label}" + ("  •" if p.boards[i].uid in unsaved else "")])
                    leaf.setData(0, Qt.ItemDataRole.UserRole, i)
                    leaf.setForeground(0, QColor(t.ERROR if sev is Severity.ERROR else t.WARN if sev else t.DIM))
                    leaf.setToolTip(0, str(self._state(p.boards[i]).path or "Not saved to a file yet"))
                    if i == self.board:
                        leaf.setForeground(0, QColor(t.ERROR if sev is Severity.ERROR else t.WARN if sev else t.TEXT))
                        self.nav.setCurrentItem(leaf)
        self.nav.expandAll()

        what = "module" if self.domain != DOMAINS[0] else "card"
        add = f"+  Add {bt.kind} {what}"
        self.add_btn.setText(add)
        self.empty_add.setText(add)
        self.empty_note.setText(f"{bt.title}  ·  {bt.connector}")
        self.remove_btn.setText(f"Remove {what}")
        self.name_label.setText(f"{what.capitalize()} name")
        here = self._state(b).path if b else None
        self.title.setText(b.label if b else f"No {bt.kind} {what}s yet")
        self.panel.setVisible(b is not None)
        self.add_btn.setVisible(b is not None and self.editing)
        self.empty_add.setVisible(self.editing)
        self.act_edit.setChecked(self.editing)

        if b is None:
            self.body.setCurrentIndex(2)
            self.hint.setText("")
        elif bt is MICROGATE:
            self.body.setCurrentIndex(1)
            self.hint.setText(f"{bt.title}  ·  " + ("click a port, name it, pick its setup." if self.editing else "view only.")
                              + "  Purple pins are outputs, blue are inputs.")
            for cell in self.cells:
                line = bt.parse(f"PORT{cell.port}")
                cell.show_port(b.lines.get(line), line == self.sel)
        else:
            self.body.setCurrentIndex(0)
            self.hint.setText(f"{bt.title}  ·  " + ("view only: click Edit in the toolbar to change it." if not self.editing
                                                    else "click a channel, name it, pick its range." if bt.options
                                                    else "click a pin and type its name."))
            self.connector.show_board(b, self.board, issues, bt.pin_of(self.sel) if self.sel else None)
        if b is not None:
            if not self.card_name.hasFocus() and self.card_name.text() != b.name:
                self.card_name.setText(b.name)
            self.file_label.setText(f"in {here.name}" if here else "not saved to a file yet")
            self._refresh_box(b, issues)
            self.list_title.setText(f"In use on this {what}  ·  {len(b.lines)} of {len(bt.lines)}")
            self.signals.clear()
            for line, sig in b.sorted_lines():
                sev = worst_on(issues, self.board, line)
                way = f"{'out' if b.direction(line) is Direction.OUTPUT else 'in':<5}" if bt.writes_direction else ""  # not by color alone
                item = QListWidgetItem(f"{str(line):<8}{way}{_mark(sev)}{sig.name.strip() or '(no name)'}", self.signals)
                item.setData(Qt.ItemDataRole.UserRole, line)
                item.setForeground(QColor(t.ERROR if sev is Severity.ERROR else t.WARN if sev else
                                          t.OUTPUT_TEXT if b.direction(line) is Direction.OUTPUT else t.INPUT_TEXT))
                if line == self.sel:
                    self.signals.setCurrentItem(item)

        changed = bool(unsaved or self.dirty_paths())
        self.chip_dirty.setText("unsaved changes")
        self.chip_dirty_action.setVisible(changed)
        self.act_undo.setEnabled(self.history.can_undo and self.editing)
        self.act_redo.setEnabled(self.history.can_redo and self.editing)
        for w in (self.card_name, self.box_name):
            w.setReadOnly(not self.editing)
        self.remove_btn.setVisible(self.editing)
        title = f"{b.label} · {here.name if here else 'not saved yet'}" if b else self.domain
        self.setWindowTitle(f"{title}{' •' if changed else ''} — {APP_NAME}")
        self._refresh_status(issues)

    def _refresh_box(self, b: Board, issues: list) -> None:
        """Fill in the side panel for what is selected."""
        line, bt = self.sel, b.bt
        a = b.lines.get(line) if line else None
        for w in (self.lab_name, self.box_name, self.box_clear):
            w.setVisible(line is not None)
        for w in (self.lab_option, self.box_option):
            w.setVisible(line is not None and bool(bt.options))
        self.lab_option.setText(bt.option_label)
        self.lab_dir.setVisible(line is not None and bt.group(line) is not None)
        group = bt.group(line) if line else None  # set: the direction is yours to pick (NI 9401)
        self.box_dir.setVisible(group is not None)
        self.box_hint.setVisible(line is None)
        self.box_unlock.setVisible(not self.editing)
        mine = [x for x in issues if x.board == self.board and x.line == line] if line else []
        self.box_issue.setVisible(bool(mine))
        if mine:
            self.box_issue.setText(mine[0].message.split(": ", 1)[-1])
            self.box_issue.setStyleSheet(f"color: {t.ERROR if mine[0].severity is Severity.ERROR else t.WARN};")
        if line is None:
            self.box_title.setText(f"No {bt.pin_word} selected")
            self.box_hint.setText(f"Click a {bt.pin_word} on the drawing to name it.")
            self._box_for = None
            return
        self.box_title.setText(bt.describe(line))
        showing = (b.uid, line)
        if showing != self._box_for or not self.box_name.hasFocus():
            # A different pin, or the name changed elsewhere (undo): show it.
            # While you type in the same field, leave your text alone.
            name = a.name if a else ""
            if self.box_name.text() != name:
                self.box_name.setText(name)
        self._box_for = showing
        if bt.options:
            if [self.box_option.itemText(i) for i in range(self.box_option.count())] != list(bt.options):
                self.box_option.clear()
                self.box_option.addItems(bt.options)
            self.box_option.setCurrentIndex(bt.options.index(a.option) if a and a.option in bt.options else 0)
            self.box_option.setToolTip(bt.option_label)
        if group is not None:
            for n, word in enumerate(("input", "output")):  # say what the choice applies to
                self.box_dir.setItemText(n, f"{word}  ·  {group}")
            self.box_dir.setCurrentIndex(1 if b.direction(line) is Direction.OUTPUT else 0)
            self.box_dir.setEnabled(a is not None and self.editing)
            self.box_dir.setToolTip(f"Direction of {group}: they share one")
        self.box_clear.setText({"port": "Turn port off", "channel": "Clear this channel"}.get(bt.pin_word, "Clear this pin"))
        self.box_clear.setEnabled(a is not None)
        self.box_clear.setVisible(self.editing)
        self.box_option.setEnabled(self.editing)

    def _refresh_status(self, issues: list | None = None) -> None:
        """The bottom line: a passing note, the first problem, or all clear."""
        issues = self.project.issues() if issues is None else issues
        self.ignore_btn.setVisible(bool(self.project.file_problems) and self.editing)
        if self._note:
            text, state = self._note[0], "error" if self._note[1] else "note"
        elif issues:
            first = issues[0]
            more = f"      (+{len(issues) - 1} more)" if len(issues) > 1 else ""
            go = "   ·   click to go there" if any(i.board is not None for i in issues) else ""
            text = f"{_mark(first.severity)}{first.message}{more}{go}"
            state = "error" if first.severity is Severity.ERROR else "warn"
        elif not self.project.boards:
            text, state = "Add a card to start, or open board files (drop them anywhere on this window).", "note"
        else:
            text, state = "✓  No problems. Ready to save.", "ok"
        self.status_line.setText(text.replace("&", "&&"))
        # Messages quote names from files: escaped, so they can't act as markup.
        self.status_line.setToolTip("<br>".join(html.escape(i.message) for i in issues[:20]))
        if self.status_line.property("state") != state:
            self.status_line.setProperty("state", state)
            self.status_line.style().unpolish(self.status_line)
            self.status_line.style().polish(self.status_line)

    # ================================================================ window events

    def closeEvent(self, e) -> None:
        if self._keep_going_despite_changes():
            e.accept()
        else:
            e.ignore()

    @staticmethod
    def _dropped(mime) -> list[Path]:
        """The local .json files and folders in a drag."""
        out = []
        for url in mime.urls() if mime.hasUrls() else []:
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.is_dir() or path.suffix.lower() == ".json":
                    out.append(path)
        return out

    def dragEnterEvent(self, e) -> None:
        if self._dropped(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self.open_paths(self._dropped(e.mimeData()))


def _dark_palette() -> QPalette:
    pal = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, t.BG), (QPalette.ColorRole.WindowText, t.TEXT),
        (QPalette.ColorRole.Base, t.FIELD), (QPalette.ColorRole.AlternateBase, t.PANEL),
        (QPalette.ColorRole.Text, t.TEXT), (QPalette.ColorRole.Button, t.RAISED),
        (QPalette.ColorRole.ButtonText, t.TEXT), (QPalette.ColorRole.Highlight, t.INPUT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, t.PANEL),
        (QPalette.ColorRole.ToolTipText, t.TEXT), (QPalette.ColorRole.PlaceholderText, t.DIM),
    ]:
        pal.setColor(role, QColor(color))
    return pal


def make_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(t.STYLESHEET)
    return app


def _report_error(kind, value, tb) -> None:
    """An error nobody handled. Without this, PyQt ends the program on the
    spot and unsaved cards are lost; with it the window stays open."""
    traceback.print_exception(kind, value, tb)
    box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME, "Something went wrong.")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setInformativeText("Your cards are still open. Save your work, then restart the editor.")
    box.setDetailedText("".join(traceback.format_exception(kind, value, tb)))
    box.exec()


def run(paths: list[Path] | None = None) -> int:
    sys.excepthook = _report_error
    app = make_app()
    window = MainWindow(paths)
    window.show()
    return app.exec()


def main() -> int:
    """``python3 app.py [board.json ...]``: open the editor, optionally with
    board files (or a folder of them)."""
    return run([Path(a) for a in sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
