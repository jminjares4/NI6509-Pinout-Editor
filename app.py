"""The PyQt6 editor window.

The window is a workspace of open boards. Every board is its own config file:
``self.files`` remembers, per board, which file that is and the text last
read from or written to it, which is how the window knows what is unsaved.

Pattern: every change goes through ``self.edit(...)``, which records an undo
snapshot, applies the change to ``self.project``, and calls ``refresh()``.
``refresh()`` redraws every view from the project, so the connector, table,
JSON and checks can never disagree with each other. Widget signals are
ignored while ``refresh()`` runs (``self._updating``) so it doesn't feed back.
"""

from __future__ import annotations

import html
import os
import re
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QColor, QFontDatabase, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from model import MANY, Board, Direction, History, LoadError, Project, Severity, worst_on
from pinout import BOARD_TYPES, DEFAULT_TYPE, Line, SignalKind
from schema import SCHEMA
import theme as t
from connector import ConnectorWidget, widest

APP_NAME = "NI Board Pinout Editor"
FILE_FILTER = "Board config (*.json)"


@dataclass
class FileState:
    """Where a board lives on disk, and the text last read from or written
    there. ``saved`` is "" when what is on disk isn't what is shown (older
    format, problems in the file, or several boards sharing one file)."""

    path: Path | None
    saved: str
MAX_RECENT = 8


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


def _short(text: str, limit: int = 28) -> str:
    """A name cut to fit a tab or a heading, so a very long one can't push
    the layout around. The full name is always in the Name field."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _mark(sev: Severity | None) -> str:
    """A problem marker for a tab's text, so it doesn't rely on color alone."""
    return "✖  " if sev is Severity.ERROR else "⚠  " if sev is Severity.WARNING else ""


def _tint_tabs(bar: QTabBar, problems: dict[int, Severity | None] | None = None) -> None:
    """Tab text: bright when selected (red on the blue highlight is hard to
    read; its marker still shows the problem), otherwise red or amber for a
    problem and dim for none. Set here, not in the stylesheet: a stylesheet
    color would override these."""
    for i in range(bar.count()):
        sev = (problems or {}).get(i)
        bar.setTabTextColor(i, QColor(
            t.TEXT if i == bar.currentIndex() else t.ERROR if sev is Severity.ERROR
            else t.WARN if sev is Severity.WARNING else t.DIM))


def _set_text(w: QLineEdit, text: str) -> None:
    """Update a field without fighting the user while they type in it."""
    if w.text() != text and not w.hasFocus():
        w.setText(text)


class MainWindow(QMainWindow):
    def __init__(self, paths: list[Path] | Path | None = None, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("ni6509", "pinout-editor")
        self.project = Project()
        self.history = History()
        self.files: dict[int, FileState] = {}  # board uid -> its file
        self.last_dir: Path | None = None
        self._adopt(self.project)
        self.board = 0
        #: The panel being shown: a board type. Only its boards get tabs.
        self.kind = DEFAULT_TYPE
        self.view_json = False
        self._shown: list[int] = []  # project index of each board tab in this panel
        self.focus_pin: int | None = None
        self._updating = False
        self._table_rows: list[tuple] = []   # what the signals table shows now
        self._table_focus: tuple | None = None
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.resize(1480, 920)
        self.setAcceptDrops(True)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self.statusBar().showMessage(
            "New board. Double-click a pin to add a signal, or drop board .json files to open them.")
        if paths:
            self.open_paths([paths] if isinstance(paths, Path) else list(paths))
        self.refresh()
        self.connector.setFocus()  # so the first keystroke doesn't land in a text field

    # ================================================================ dialogs

    def ask_unsaved(self, text: str) -> str:
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, text, parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)  # board names come from files
        box.setInformativeText("Save first?")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {save: "save", discard: "discard"}.get(box.clickedButton(), "cancel")

    def confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setTextFormat(Qt.TextFormat.PlainText)  # board and file names come from files
        return box.exec() == QMessageBox.StandardButton.Yes

    def ask_open_paths(self) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open board files", self._dialog_dir(),
                                                f"{FILE_FILTER};;All files (*)")
        return paths

    def ask_save_path(self, title: str, suggested: str, filter_: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path(self._dialog_dir()) / suggested), filter_)
        return path or None

    def ask_folder(self, title: str) -> str | None:
        return QFileDialog.getExistingDirectory(self, title, self._dialog_dir()) or None

    def _dialog_dir(self) -> str:
        here = self._state(self._b()).path
        return str(here.parent if here else self.last_dir or Path.cwd())

    # ================================================================ actions

    def _action(self, text: str, slot: Callable, shortcut=None, tip: str | None = None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def _build_actions(self) -> None:
        K = QKeySequence.StandardKey
        self.act_new = self._action("New", self.new_project, K.New)
        self.act_open = self._action("Open…", self.open_dialog, K.Open,
                                     "Open board files (pick as many as you like)")
        self.act_reload = self._action("Reload Board from Disk", self.reload, QKeySequence("Ctrl+R"),
                                       "Read this board's file again, e.g. after fixing it in a text editor")
        self.act_save = self._action("Save", self.save, K.Save,
                                     "Save this board to its file (refused while it has errors)")
        self.act_save_as = self._action("Save As…", self.save_as, K.SaveAs,
                                        "Save this board to a different file")
        self.act_save_all = self._action("Save All", self.save_all, QKeySequence("Ctrl+Alt+S"),
                                         "Save every board that has changes, each to its own file")
        self.act_undo = self._action("Undo", self.undo, K.Undo)
        self.act_redo = self._action("Redo", self.redo, K.Redo)
        self.act_schema = self._action("Export JSON Schema…", self.export_schema,
                                       tip="So other programs can check the file's structure")
        self.act_add_board = self._action("Add board", self.add_board)
        self.act_remove_board = self._action("Close board", self.remove_board,
                                             tip="Take this board out of the window. Its file stays on disk.")
        self.act_remove_assignment = self._action("Remove assignment", self.remove_assignment)
        self.act_quit = self._action("Quit", self.close, K.Quit)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addActions([self.act_new, self.act_open])
        self.recent_menu = f.addMenu("Open Recent")
        f.addAction(self.act_reload)
        f.addSeparator()
        f.addActions([self.act_save, self.act_save_as, self.act_save_all])
        f.addSeparator()
        f.addAction(self.act_schema)
        f.addSeparator()
        f.addAction(self.act_quit)
        e = mb.addMenu("&Edit")
        e.addActions([self.act_undo, self.act_redo])
        e.addSeparator()
        e.addAction(self.act_remove_assignment)
        b = mb.addMenu("&Board")
        b.addActions([self.act_add_board, self.act_remove_board])
        self._rebuild_recent_menu()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)
        tb.addActions([self.act_new, self.act_open, self.act_save, self.act_save_all])
        tb.widgetForAction(self.act_save).setObjectName("primary")  # the one filled button
        tb.addSeparator()
        tb.addActions([self.act_undo, self.act_redo])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.chip_dirty = _label("unsaved", "chip-warn")
        self.chip_state = _label("", "chip-ok")
        # A widget inside a toolbar is shown/hidden through its QAction.
        self.chip_dirty_action = tb.addWidget(self.chip_dirty)
        tb.addWidget(_label(" "))
        self.chip_state_action = tb.addWidget(self.chip_state)

    # ================================================================ layout

    def _build_central(self) -> None:
        central = QWidget(objectName="central")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 4, 16, 8)
        outer.setSpacing(8)

        # Panels: one per board type, holding just that type's boards, plus
        # the JSON the project saves to. A tab bar picks the panel; the stack
        # underneath shows its boards, an empty note, or the JSON.
        panels_box = QWidget()
        pv = QVBoxLayout(panels_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        bar = QHBoxLayout()
        self.panels = QTabBar(objectName="panels")
        self.panels.setExpanding(False)
        self.panels.setDrawBase(False)
        for key in BOARD_TYPES:
            self.panels.addTab(key)
        self.panels.addTab("JSON")
        self.panels.currentChanged.connect(self._panel_changed)
        bar.addWidget(self.panels)
        bar.addStretch(1)
        bar.addWidget(_label("One file per board  ·  drop board .json files anywhere to open them  ", "dim"))
        pv.addLayout(bar)
        rule = QWidget()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {t.LINE};")
        pv.addWidget(rule)
        self.stack = QStackedWidget()
        pv.addWidget(self.stack, 1)

        boards_page = QWidget()
        root = QVBoxLayout(boards_page)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(10)

        # This panel's boards, and the current board's settings
        strip = QHBoxLayout()
        self.board_tabs = QTabBar(objectName="boardTabs")
        self.board_tabs.setExpanding(False)
        self.board_tabs.setDrawBase(False)
        self.board_tabs.currentChanged.connect(self._tab_changed)
        strip.addWidget(self.board_tabs)
        self.add_btn = _button("+ Add board", "ghost", "Adds the next free Board N to this panel")
        self.add_btn.clicked.connect(self.add_board)
        strip.addWidget(self.add_btn)
        strip.addStretch(1)
        root.addLayout(strip)

        self.board_name = QLineEdit()
        self.board_name.setFixedWidth(180)
        self.board_name.textChanged.connect(
            lambda s: self._text_edit(("board-name", self.board), lambda: setattr(self._b(), "name", s)))
        self.remove_board_btn = _button("Close board", tip="Take this board out of the window. "
                                                           "Its file stays on disk.")
        self.remove_board_btn.clicked.connect(self.remove_board)

        # Connector card | inspector card
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.setHandleWidth(12)

        left = QFrame(objectName="card")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(18, 12, 18, 10)
        lv.setSpacing(6)
        # Card header: which board this is, and its two settings on the right
        head = QHBoxLayout()
        head.setSpacing(8)
        self.heading = _label("", "h1")
        self.subheading = _label("", "dim")
        head.addWidget(self.heading)
        head.addWidget(self.subheading)
        head.addStretch(1)
        for w in (_label("Name", "dim"), self.board_name, self.remove_board_btn):
            head.addWidget(w)
        lv.addLayout(head)
        self.connector = ConnectorWidget()
        self.connector.pinClicked.connect(lambda pin: self.jump(self.board, pin))
        self.connector.pinDoubleClicked.connect(self._pin_double_clicked)
        self.connector.deletePressed.connect(self.remove_assignment)
        self.connector.escapePressed.connect(self.deselect)
        self.connector.cleared.connect(self.deselect)
        scroll = QScrollArea()
        scroll.setWidget(self.connector)
        scroll.setWidgetResizable(False)
        for w in (scroll.viewport(), self.connector):  # let the card show through
            w.setAutoFillBackground(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # Wide enough for the widest connector, so it never scrolls sideways.
        bar = scroll.style().pixelMetric(scroll.style().PixelMetric.PM_ScrollBarExtent)
        scroll.setMinimumWidth(widest() + bar + 2 * scroll.frameWidth())
        lv.addWidget(scroll, 1)
        legend = _label(
            f'<span style="color:{t.INPUT}">●</span> input &nbsp; '
            f'<span style="color:{t.OUTPUT}">●</span> output &nbsp; '
            f'<span style="color:{t.PIN_EDGE}">●</span> free &nbsp; '
            f'<span style="color:{t.VCC}">●</span> supply &nbsp; '
            f'<span style="color:{t.MUTED}">○</span> GND / COM &nbsp; '
            '&nbsp; '
            f'<span style="color:{t.DIM}">click: select / deselect · double-click: assign · '
            'Delete: remove · Esc: clear</span>', "dim")
        legend.setWordWrap(True)  # wraps on a narrow window instead of forcing it wider
        lv.addWidget(legend)
        hsplit.addWidget(left)

        self.tabs = QTabWidget()
        pin_tab = self._build_pin_tab()
        # Esc while typing in the Pin tab also clears the selection.
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), pin_tab)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.deselect)
        self.tabs.addTab(pin_tab, "Pin")
        self.tabs.addTab(self._build_signals_tab(), "Signals")
        self.tabs.setMinimumWidth(340)
        self.tabs.currentChanged.connect(self._inspector_tab_shown)
        side = QFrame(objectName="card")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 10, 10, 10)
        sv.addWidget(self.tabs)
        hsplit.addWidget(side)
        hsplit.setStretchFactor(0, 3)
        hsplit.setStretchFactor(1, 2)
        hsplit.setSizes([900, 560])
        hsplit.setCollapsible(0, False)
        hsplit.setCollapsible(1, False)
        root.addWidget(hsplit, 1)

        self.stack.addWidget(boards_page)                # 0: the panel's boards
        self.stack.addWidget(self._build_empty_page())   # 1: the panel has no boards yet
        self.stack.addWidget(self._build_json_tab())     # 2: JSON

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setHandleWidth(12)
        vsplit.addWidget(panels_box)

        checks = QFrame(objectName="card")
        cv = QVBoxLayout(checks)
        cv.setContentsMargins(14, 10, 10, 8)
        cv.setSpacing(6)
        ch = QHBoxLayout()
        ch.addWidget(_label("<b>Checks</b>"))
        ch.addWidget(_label("all open boards", "dim"))
        self.checks_summary = _label("")
        ch.addWidget(self.checks_summary)
        ch.addStretch(1)
        # Shown only when the opened file had problems (see Project.file_problems).
        self.file_note = _label("", "dim")
        self.reload_btn = _button("Reload this board", tip="Read this board's file again after fixing it (Ctrl+R)")
        self.reload_btn.clicked.connect(self.reload)
        self.accept_btn = _button("Leave out && continue", "fix",
                                  "Accept what was loaded: entries marked \"left out\" or \"ignored\" "
                                  "are not saved. Can be undone.")
        self.accept_btn.clicked.connect(self.accept_file_problems)
        for w in (self.file_note, self.reload_btn, self.accept_btn):
            ch.addWidget(w)
            w.hide()
        cv.addLayout(ch)
        self.checks = QListWidget()
        self.checks.itemClicked.connect(self._check_clicked)
        cv.addWidget(self.checks)
        vsplit.addWidget(checks)
        vsplit.setSizes([780, 96])
        outer.addWidget(vsplit, 1)
        self.setCentralWidget(central)
        # These show board, signal and file names, which come from files:
        # plain text, so a name that looks like HTML is shown, not rendered.
        for lab in (self.heading, self.subheading, self.pin_title, self.pin_sub, self.pin_hint,
                    self.json_note, self.file_note):
            lab.setTextFormat(Qt.TextFormat.PlainText)

        # Shown while a .json is dragged over the window.
        self.drop_overlay = QWidget(self, objectName="dropOverlay")
        self.drop_overlay.setStyleSheet(
            f"#dropOverlay {{ background: rgba(47,110,186,50); border: 3px dashed {t.ACCENT}; border-radius: 12px; }}"
            f"#dropPill {{ background: {t.PANEL}; border: 1px solid {t.ACCENT}; border-radius: 10px;"
            "  padding: 14px 28px; font-size: 16pt; font-weight: 600; }")
        pill = QLabel("Drop to open these board files", objectName="dropPill")
        ov = QVBoxLayout(self.drop_overlay)
        ov.addWidget(pill, 0, Qt.AlignmentFlag.AlignCenter)
        self.drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_overlay.hide()

    def _build_empty_page(self) -> QWidget:
        """Shown when the project has no board of the panel's type."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)
        v.addStretch(1)
        self.empty_title = _label("", "h2")
        self.empty_note = _label("", "dim")
        self.empty_add = _button("", "primary")
        self.empty_add.clicked.connect(self.add_board)
        for x in (self.empty_title, self.empty_note, self.empty_add):
            v.addWidget(x, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addStretch(2)
        return w

    def _build_pin_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 14, 10, 10)
        v.setSpacing(10)
        self.pin_title = _label("", "h2")
        self.pin_sub = _label("", "mono")
        self.pin_sub.setFont(self.mono)
        self.pin_sub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pin_hint = _label("", "dim")
        self.pin_hint.setWordWrap(True)
        self.assign_btn = _button("", "primary")
        self.assign_btn.clicked.connect(self._assign_focus)
        for x in (self.pin_title, self.pin_sub, self.pin_hint, self.assign_btn):
            v.addWidget(x)

        self.form = QWidget()
        f = QFormLayout(self.form)
        f.setContentsMargins(0, 6, 0, 0)
        f.setVerticalSpacing(10)
        self.f_name = QLineEdit()
        self.f_name.textChanged.connect(self._name_changed)
        self.f_name.returnPressed.connect(self._name_done)
        # Direction: fixed by the board on most types (shown as text). On an
        # NI 9401 it is picked here, for the pin's whole group at once.
        self.f_dir = QLabel("")
        self.f_dir_pick = QComboBox()
        self.f_dir_pick.addItems(["input", "output"])
        self.f_dir_pick.activated.connect(self._direction_chosen)
        dir_row = QWidget()
        dr = QHBoxLayout(dir_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.addWidget(self.f_dir_pick)
        dr.addWidget(self.f_dir, 1)
        self.f_note = QPlainTextEdit()
        self.f_note.setPlaceholderText("wire color, harness, test step…")
        self.f_note.setFixedHeight(60)
        self.f_note.textChanged.connect(self._note_changed)
        f.addRow("Name", self.f_name)
        f.addRow("Direction", dir_row)
        f.addRow("Note", self.f_note)
        v.addWidget(self.form)

        self.move_row = QWidget()
        mr = QHBoxLayout(self.move_row)
        mr.setContentsMargins(0, 6, 0, 0)
        self.move_edit = QLineEdit()
        self.move_edit.setPlaceholderText("P3.4")
        self.move_edit.setFixedWidth(90)
        self.move_edit.returnPressed.connect(self._move)
        move_btn = _button("Move")
        move_btn.clicked.connect(self._move)
        mr.addWidget(QLabel("Move to"))
        mr.addWidget(self.move_edit)
        mr.addWidget(move_btn)
        mr.addStretch(1)
        v.addWidget(self.move_row)
        self.remove_btn = _button("Remove assignment", "danger", "Delete key on the connector")
        self.remove_btn.clicked.connect(self.remove_assignment)
        v.addWidget(self.remove_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.pin_issues = QLabel("")
        self.pin_issues.setWordWrap(True)
        v.addWidget(self.pin_issues)
        v.addStretch(1)
        return w

    def _build_signals_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 10, 8, 8)
        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name, line, board or type")
        self.filter_edit.textChanged.connect(lambda _: self.refresh())
        row.addWidget(self.filter_edit, 1)
        v.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Board", "Line", "Pin", "Dir", "Note"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.cellClicked.connect(self._table_clicked)
        v.addWidget(self.table, 1)
        note = _label("Double-click Name or Note to edit. Direction comes from the board: "
                      "fixed on every type except the NI 9401 (set it on the Pin tab).", "dim")
        note.setWordWrap(True)
        v.addWidget(note)
        return w

    def _build_json_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 10, 8, 8)
        row = QHBoxLayout()
        copy = _button("Copy to clipboard")
        row.addWidget(copy)
        self.json_note = _label("", "dim")
        row.addWidget(self.json_note)
        row.addStretch(1)
        v.addLayout(row)
        self.json_view = QPlainTextEdit()
        self.json_view.setReadOnly(True)
        self.json_view.setFont(self.mono)
        self.json_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        v.addWidget(self.json_view, 1)
        copy.clicked.connect(lambda: self._copy(self.json_view.toPlainText(), "JSON"))
        return w

    # ================================================================ state helpers

    def _b(self):
        return self.project.boards[self.board]

    def _focus_line(self) -> Line | None:
        if self.focus_pin is None:
            return None
        s = self._b().bt.signal_at(self.focus_pin)
        return s.line if s and s.kind is SignalKind.LINE else None

    def _pin_of(self, board: int, line: Line | None) -> int | None:
        """Connector pin of ``line`` on a board, if that board has such a channel."""
        bt = self.project.boards[board].bt
        return bt.pin_of(line) if line in bt.lines else None

    def _state(self, b: Board) -> FileState:
        """The file a board belongs to. A board not seen before has none yet
        and counts as saved the way it is now."""
        if b.uid not in self.files:
            self.files[b.uid] = FileState(None, b.to_json())
        return self.files[b.uid]

    def _adopt(self, project: Project) -> None:
        """Start tracking the boards of a fresh project."""
        self.files = {}
        for b in project.boards:
            self._state(b)

    def board_dirty(self, b: Board) -> bool:
        return b.to_json() != self._state(b).saved

    def unsaved(self) -> list[Board]:
        return [b for b in self.project.boards if self.board_dirty(b)]

    def dirty(self) -> bool:
        return bool(self.unsaved())

    def _board_for(self, path: Path) -> int | None:
        """Index of the open board that lives in ``path``."""
        path = path.resolve()
        return next((i for i, b in enumerate(self.project.boards)
                     if self._state(b).path == path), None)

    @staticmethod
    def _file_name(b: Board) -> str:
        """A file name made from the board's name: "AI module" -> AI_module.json."""
        return (re.sub(r"[^A-Za-z0-9._-]+", "_", b.label).strip("_") or "board") + ".json"

    def edit(self, change: Callable[[], object], key: object = None) -> None:
        """Record an undo snapshot, apply ``change``, refresh every view."""
        self.history.record(self.project, key)
        change()
        self.refresh()

    def _text_edit(self, key: object, change: Callable[[], object]) -> None:
        if not self._updating:
            self.edit(change, key)

    def jump(self, board: int, pin: int | None, show_pin_tab: bool = True) -> None:
        self.view_json = False
        self.kind = self.project.boards[board].type  # show the panel this board lives in
        if board != self.board:
            self.board = board
            self.move_edit.clear()
        self.focus_pin = pin
        self.history.break_group()
        if pin is not None and show_pin_tab:
            self.tabs.setCurrentIndex(0)
        self.refresh()

    def status(self, text: str, error: bool = False) -> None:
        self.statusBar().setStyleSheet(f"color: {t.ERROR if error else t.DIM};")
        self.statusBar().showMessage(text, 0 if error else 8000)

    # ================================================================ file operations

    def _keep_going_despite_changes(self) -> bool:
        changed = self.unsaved()
        if not changed:
            return True
        names = ", ".join(b.label for b in changed[:4]) + ("…" if len(changed) > 4 else "")
        answer = self.ask_unsaved(f"{len(changed)} board(s) have unsaved changes: {names}.")
        if answer == "save":
            return self.save_all()
        return answer == "discard"

    def new_project(self) -> None:
        if not self._keep_going_despite_changes():
            return
        self.project = Project()
        self._adopt(self.project)
        self.board, self.kind, self.view_json, self.focus_pin = 0, self.project.boards[0].type, False, None
        self.history.clear()
        self.refresh()
        self.status("New: one empty board. Open adds boards from their files.")

    def open_dialog(self) -> None:
        paths = self.ask_open_paths()
        if paths:
            self.open_paths([Path(p) for p in paths])

    def _is_blank_start(self) -> bool:
        """Only the untouched empty board the window starts with is open."""
        boards = self.project.boards
        return (len(boards) == 1 and not boards[0].lines and self._state(boards[0]).path is None
                and not self.board_dirty(boards[0]) and not self.history.can_undo)

    def open_paths(self, paths: list[Path]) -> bool:
        """Open board files next to the boards already open. A folder means
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
                where = f" line {fatal.at}" if fatal.at else ""
                failed.append(f"{path.name}{where}: {fatal.message}")
                continue
            loads.append((path, loaded))
        notes: list[str] = []
        problems = sum(len(loaded.file_problems) for _, loaded in loads)
        first = None
        if loads:
            blank = self._is_blank_start()

            def apply() -> None:
                nonlocal first
                for path, loaded in loads:
                    base = self.project.add_boards(loaded.boards, loaded.file_problems, path.name)
                    first = base if first is None else first
                if blank:
                    self.project.remove_board(0)
                    first -= 1

            self.edit(apply)
            for path, loaded in loads:
                changes = loaded.format_changes()
                shared = MANY in loaded.dropped_fields  # several boards in one file
                for b in loaded.boards:
                    as_on_disk = not changes and not loaded.file_problems
                    self.files[b.uid] = FileState(None if shared else path.resolve(),
                                                  b.to_json() if as_on_disk else "")
                if changes:
                    notes.append(f"{path.name} is an older format: saving updates it ({'; '.join(changes)})")
                self._add_recent(path)
                self.last_dir = path.resolve().parent
            self.jump(first, None)
        elif already and not failed:
            self.jump(self._board_for(already[0]), None)
        boards = sum(len(loaded.boards) for _, loaded in loads)
        signals = sum(len(b.lines) for _, loaded in loads for b in loaded.boards)
        msg = [f"Opened {len(loads)} file(s): {boards} board(s), {signals} signals"] if loads else []
        if already:
            msg.append(f"{len(already)} already open")
        if problems:
            msg.append(f"{problems} problem(s) in the file(s): see Checks")
        msg += notes
        msg += [f"Could not open {f}" for f in failed]
        if msg:
            self.status(". ".join(msg) + ".", error=bool(problems or failed))
        return bool(loads)

    def reload(self) -> None:
        """Read the current board's file again."""
        index, b = self.board, self._b()
        path = self._state(b).path
        if path is None:
            self.status(f"Nothing to reload: {b.label} hasn't been saved to a file.", error=True)
            return
        if self.board_dirty(b) and self._state(b).saved and not self.confirm(
                "Reload board", f"Discard the changes to {b.label} and read {path.name} again?"):
            return
        try:
            loaded = Project.load(path)
        except LoadError as e:
            self.status(f"Could not reload: {e}", error=True)
            return
        fatal = next((f for f in loaded.file_problems if f.fatal), None)
        if fatal or len(loaded.boards) != 1:
            why = fatal.message if fatal else "it now holds several boards"
            self.status(f"Could not reload {path.name}: {why}.", error=True)
            return
        new = loaded.boards[0]
        self.edit(lambda: self.project.replace_board(index, new, loaded.file_problems, path.name))
        changes = loaded.format_changes()
        self.files[new.uid] = FileState(path, "" if changes or loaded.file_problems else new.to_json())
        self.jump(index, None)
        n = len(loaded.file_problems)
        self.status(f"Reloaded {path.name}" + (f": {n} problem(s), see Checks." if n else "."), error=bool(n))

    def accept_file_problems(self) -> None:
        self.edit(lambda: self.project.file_problems.clear())
        self.status("Accepted what was loaded. Saving writes the files as shown here (Undo to go back).")

    def _board_errors(self, index: int) -> list:
        return [i for i in self.project.errors() if i.board == index]

    def _refuse(self, n: int, where: str) -> bool:
        where = f" {where}" if where else ""
        self.status(f"Not saved: fix the {n} error(s){where} listed in Checks first.", error=True)
        self.checks.setCurrentRow(0)
        self.checks.scrollToTop()
        return False

    def save(self) -> bool:
        """Save the current board to its file."""
        return self.save_board(self.board)

    def save_as(self) -> bool:
        return self.save_board(self.board, ask=True)

    def save_board(self, index: int, ask: bool = False) -> bool:
        b = self.project.boards[index]
        errors = self._board_errors(index)
        if errors:
            return self._refuse(len(errors), f"on {b.label}")
        path = self._state(b).path
        if path is None or ask:
            chosen = self.ask_save_path(f"Save {b.label}", path.name if path else self._file_name(b),
                                        FILE_FILTER)
            if not chosen:
                return False
            path = Path(chosen)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")
            other = self._board_for(path)
            if other is not None and other != index:
                self.status(f"Not saved: {path.name} is the file of {self.project.boards[other].label}, "
                            "which is open. Each board needs its own file.", error=True)
                return False
        if not self._write(b, path):
            return False
        self.status(f"Saved {b.label} to {path.name}")
        self.refresh()
        return True

    def save_all(self) -> bool:
        """Save every board with changes. Boards that have no file yet all go
        into one folder you pick, named after the board."""
        errors = self.project.errors()
        if errors:
            return self._refuse(len(errors), "")
        todo = [b for b in self.project.boards if self.board_dirty(b) or self._state(b).path is None]
        homeless = [b for b in todo if self._state(b).path is None]
        target: dict[int, Path] = {b.uid: self._state(b).path for b in todo if b not in homeless}
        if homeless:
            folder = self.ask_folder(f"Folder for {len(homeless)} new board file(s)")
            if not folder:
                return False
            taken = {p.name.lower() for p in target.values()}
            for b in homeless:
                name, n = self._file_name(b), 2
                while name.lower() in taken:  # two board names that make the same file name
                    name, n = f"{Path(self._file_name(b)).stem}_{n}.json", n + 1
                taken.add(name.lower())
                target[b.uid] = Path(folder) / name
            there = [target[b.uid].name for b in homeless if target[b.uid].exists()
                     and self._board_for(target[b.uid]) is None]
            clash = [target[b.uid].name for b in homeless if self._board_for(target[b.uid]) is not None]
            if clash:
                self.status(f"Not saved: {', '.join(clash)} in that folder belong to boards that are "
                            "open. Pick another folder or rename the board.", error=True)
                return False
            if there and not self.confirm("Replace files", f"Already in {folder}:\n\n" + "\n".join(there)
                                          + "\n\nReplace them?"):
                return False
        done = [b for b in todo if self._write(b, target[b.uid])]
        self.refresh()
        if len(done) != len(todo):
            return False  # _write said which one failed
        self.status(f"Saved {len(done)} board file(s)." if done else "Nothing to save.")
        return True

    def _write(self, b: Board, path: Path) -> bool:
        text = b.to_json()
        # Write beside the file, then swap it in: a crash or a full disk
        # halfway through can't leave half a config file behind.
        part = path.with_name(path.name + ".part")
        try:
            part.write_text(text, encoding="utf-8")
            os.replace(part, path)
        except OSError as e:
            part.unlink(missing_ok=True)
            self.status(f"Not saved: could not write {path}: {e.strerror or e}", error=True)
            return False
        self.files[b.uid] = FileState(path.resolve(), text)
        self.last_dir = path.resolve().parent
        self._add_recent(path)
        return True

    def export_schema(self) -> None:
        import json
        path = self.ask_save_path("Export JSON Schema", "ni-board.schema.json", "JSON Schema (*.json)")
        if path:
            Path(path).write_text(json.dumps(SCHEMA, indent=2) + "\n", encoding="utf-8")
            self.status(f"Exported schema to {Path(path).name}")

    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        return [value] if isinstance(value, str) else list(value or [])

    def _add_recent(self, path: Path) -> None:
        p = str(Path(path).resolve())
        recent = [p] + [r for r in self._recent() if r != p]
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self._recent()
        for r in recent:
            a = self.recent_menu.addAction(r)
            a.triggered.connect(lambda _=False, r=r: self.open_paths([Path(r)]))
        self.recent_menu.setEnabled(bool(recent))

    # ================================================================ undo

    def undo(self) -> None:
        p = self.history.undo(self.project)
        if p is not None:
            self._restore(p, "Undone.")

    def redo(self) -> None:
        p = self.history.redo(self.project)
        if p is not None:
            self._restore(p, "Redone.")

    def _restore(self, p: Project, msg: str) -> None:
        self.project = p
        self.board = min(self.board, len(p.boards) - 1)
        self.status(msg)
        self.refresh()

    # ================================================================ board operations

    def _of_kind(self) -> list[int]:
        """Project indexes of the boards in the panel being shown."""
        return [i for i, b in enumerate(self.project.boards) if b.type == self.kind]

    def _panel_changed(self, index: int) -> None:
        if self._updating or index < 0:
            return
        self.history.break_group()
        self.view_json = index >= len(BOARD_TYPES)
        if not self.view_json:
            kind = list(BOARD_TYPES)[index]
            if kind != self.kind:
                self.kind, self.focus_pin = kind, None
                self.move_edit.clear()
                mine = self._of_kind()
                if mine:
                    self.board = mine[0]
        self.refresh()

    def _tab_changed(self, index: int) -> None:
        if not self._updating and 0 <= index < len(self._shown):
            self.jump(self._shown[index], None)

    def add_board(self) -> None:
        """Add a board to the panel being shown. It gets a file when saved."""
        kind, box = self.kind, []
        self.edit(lambda: box.append(self.project.add_board(kind)))
        self._state(self.project.boards[box[0]])  # nothing to save until it is edited
        self.jump(box[0], None)

    def remove_board(self) -> None:
        """Close the current board. Its file, if it has one, is left alone."""
        if len(self.project.boards) <= 1 or not self._of_kind():
            return
        b, index = self._b(), self.board
        if self.board_dirty(b):
            answer = self.ask_unsaved(f"{b.label} has unsaved changes.")
            if answer == "cancel" or (answer == "save" and not self.save_board(index)):
                return
        self.edit(lambda: self.project.remove_board(index))
        mine = self._of_kind()  # stay in this panel: the next board of the same type, if any
        if mine:
            self.jump(next((i for i in mine if i >= index), mine[-1]), None)
        self.status("Board closed. Its file is untouched; Undo brings the board back.")

    # ================================================================ pin operations

    def _pin_double_clicked(self, pin: int) -> None:
        s = self._b().bt.signal_at(pin)
        new = s.kind is SignalKind.LINE and s.line not in self._b().lines
        if new:
            self.edit(lambda: self.project.assign(self.board, s.line))
        self.jump(self.board, pin)
        if new:
            self._start_naming()

    def _start_naming(self) -> None:
        """Put the cursor in the Name box with the default name selected, so
        typing replaces it straight away."""
        self.tabs.setCurrentIndex(0)
        self.f_name.setFocus()
        self.f_name.selectAll()

    def deselect(self) -> None:
        self.jump(self.board, None)
        self.connector.setFocus()

    def _name_done(self) -> None:
        """Enter in the Name box: back to the connector for the next pin."""
        self.history.break_group()
        self.connector.setFocus()

    def _assign_focus(self) -> None:
        line = self._focus_line()
        if line:
            self.edit(lambda: self.project.assign(self.board, line))
            self._start_naming()

    def remove_assignment(self) -> None:
        line = self._focus_line()
        if line and line in self._b().lines:
            self.edit(lambda: self._b().lines.pop(line))

    def _name_changed(self, text: str) -> None:
        line = self._focus_line()
        if line and line in self._b().lines:
            self._text_edit(("name", self.board, line), lambda: setattr(self._b().lines[line], "name", text))

    def _note_changed(self) -> None:
        line = self._focus_line()
        if line and line in self._b().lines:
            text = self.f_note.toPlainText()
            self._text_edit(("note", self.board, line), lambda: setattr(self._b().lines[line], "note", text))

    def _move(self) -> None:
        src, b = self._focus_line(), self._b()
        if not src or src not in b.lines:
            return
        try:
            dst = b.bt.parse(self.move_edit.text())
        except ValueError as e:
            self.status(str(e), error=True)
            return
        if dst == src:
            return
        if dst in self._b().lines:
            self.status(f"{dst} is already assigned on this board.", error=True)
            return
        before = b.direction(src)
        self.edit(lambda: self.project.move(self.board, src, dst))
        self.move_edit.clear()
        pin = b.bt.pin_of(dst)
        self.jump(self.board, pin)
        msg = f"Moved to {dst} (pin {pin})."
        after = b.direction(dst)
        if after is not before:
            why = b.bt.why_direction(dst) or f"like the rest of {b.bt.group(dst)}"
            msg += f" It is now an {after.value}: {why}."
        self.status(msg)

    def _direction_chosen(self, index: int) -> None:
        """NI 9401: the direction picked on the Pin tab applies to the whole group."""
        line, b = self._focus_line(), self._b()
        if not line or line not in b.lines:
            return
        d = Direction.OUTPUT if index else Direction.INPUT
        if d is not b.direction(line):
            self.edit(lambda: b.set_direction(line, d))
            self.status(f"{b.label}: {b.bt.group(line)} are now {d.value}s.")

    # ================================================================ table / checks

    def _table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        bi, line = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        a = self.project.boards[bi].lines.get(line)
        if a is None:
            return
        col, text = item.column(), item.text()
        if col == 0 and text != a.name:
            self.edit(lambda: setattr(self.project.boards[bi].lines[line], "name", text))
        elif col == 5 and text != a.note:
            self.edit(lambda: setattr(self.project.boards[bi].lines[line], "note", text))

    def _table_clicked(self, row: int, col: int) -> None:
        bi, line = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.jump(bi, self._pin_of(bi, line), show_pin_tab=False)

    def _check_clicked(self, item: QListWidgetItem) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if not target:
            return
        self.jump(*target)

    def _copy(self, text: str, what: str) -> None:
        QApplication.clipboard().setText(text)
        self.status(f"{what} copied to clipboard.")

    # ================================================================ refresh

    def refresh(self) -> None:
        self._updating = True
        try:
            self._refresh()
        finally:
            self._updating = False

    def _refresh(self) -> None:
        p = self.project
        # The board being shown always belongs to the panel being shown. A
        # panel with no boards shows its empty page instead.
        self.board = min(self.board, len(p.boards) - 1)
        self._shown = self._of_kind()
        none_here = not self._shown
        if none_here or self.board not in self._shown:
            self.focus_pin = None
            if self._shown:
                self.board = self._shown[0]
        b = self._b()
        if self.focus_pin is not None and b.bt.signal_at(self.focus_pin) is None:
            self.focus_pin = None  # a pin this board's connector doesn't have
        issues = p.issues()
        errors = sum(i.severity is Severity.ERROR for i in issues)
        warnings = len(issues) - errors
        # Which boards differ from their files: worked out once here (it
        # means serializing each board) and reused below.
        unsaved = {x.uid for x in self.unsaved()}
        dirty = bool(unsaved)

        here = self._state(b)
        file_name = here.path.name if here.path else "not saved yet"
        self.setWindowTitle(f"{b.label} · {file_name}{' •' if b.uid in unsaved else ''} — {APP_NAME}")
        changed = len(unsaved)
        self.chip_dirty.setText(f"{changed} unsaved")
        self.act_save.setEnabled(not none_here)
        self.act_save_as.setEnabled(not none_here)
        self.act_save_all.setEnabled(bool(changed))
        self.act_undo.setEnabled(self.history.can_undo)
        self.act_redo.setEnabled(self.history.can_redo)
        self.act_remove_board.setEnabled(len(p.boards) > 1 and not none_here)
        self.remove_board_btn.setEnabled(len(p.boards) > 1)
        self.act_remove_assignment.setEnabled(bool(self._focus_line() and self._focus_line() in b.lines))
        self.chip_dirty_action.setVisible(dirty)
        # Nothing to judge yet (no signals, no problems): show no verdict at all.
        empty = p.signal_count() == 0 and not issues
        self.chip_state_action.setVisible(not empty)
        if errors:
            self._chip(f"{errors} error(s)", "chip-err")
        elif warnings:
            self._chip(f"{warnings} warning(s)", "chip-warn")
        else:
            self._chip("valid", "chip-ok")

        # Panels: how many boards each holds; marked if one of them has a problem
        marks: dict[int, Severity | None] = {}
        for n, (key, bt) in enumerate(BOARD_TYPES.items()):
            mine = [i for i, bd in enumerate(p.boards) if bd.type == key]
            marks[n] = max((s for s in (worst_on(issues, i) for i in mine) if s), default=None)
            self.panels.setTabText(n, f"{_mark(marks[n])}{key}  ({len(mine)})")
            self.panels.setTabToolTip(n, f"{bt.title}  ·  {bt.connector}  ·  {bt.expected}")
        self.panels.setCurrentIndex(len(BOARD_TYPES) if self.view_json
                                    else list(BOARD_TYPES).index(self.kind))
        _tint_tabs(self.panels, marks)
        self.stack.setCurrentIndex(2 if self.view_json else 1 if none_here else 0)
        kind = BOARD_TYPES[self.kind]
        self.act_add_board.setText(f"Add {self.kind} board")
        self.add_btn.setText(f"+ Add {self.kind}")
        self.empty_title.setText(f"No {self.kind} boards in this project")
        self.empty_note.setText(f"{kind.title}  ·  {kind.connector}  ·  channels {kind.expected}")
        self.empty_add.setText(f"+  Add an {self.kind} board")

        # Board tabs: this panel's boards only
        while self.board_tabs.count() < len(self._shown):
            self.board_tabs.addTab("")
        while self.board_tabs.count() > len(self._shown):
            self.board_tabs.removeTab(self.board_tabs.count() - 1)
        marks = {n: worst_on(issues, i) for n, i in enumerate(self._shown)}
        for n, i in enumerate(self._shown):
            bd = p.boards[i]
            dot = "  •" if bd.uid in unsaved else ""
            name = _short(bd.label).replace("&", "&&")  # a lone & would become a keyboard shortcut
            self.board_tabs.setTabText(n, f"{_mark(marks[n])}{name}  ({len(bd.lines)}){dot}")
            where = self._state(bd).path
            self.board_tabs.setTabToolTip(n, str(where) if where else "Not saved to a file yet")
        if not none_here:
            self.board_tabs.setCurrentIndex(self._shown.index(self.board))
            _set_text(self.board_name, b.name)
            self.heading.setText(_short(b.label))
            self.subheading.setText(f"·  {b.bt.title}  ·  {b.bt.connector}  ·  {file_name}")
            self.subheading.setToolTip(str(here.path) if here.path else "Save gives this board its file")
            self.connector.show_board(b, self.board, issues, self.focus_pin)
        _tint_tabs(self.board_tabs, marks)
        _tint_tabs(self.tabs.tabBar())

        self._refresh_pin_tab(issues)
        self.tabs.setTabText(1, f"Signals ({p.signal_count()})")
        self._refresh_table()
        json_text = b.to_json()
        if self.json_view.toPlainText() != json_text:
            self.json_view.setPlainText(json_text)
        self.json_note.setText(f"{b.label}  →  {here.path or 'not saved yet'}.  Save writes exactly this.")

        self.checks.clear()
        for i in issues:
            icon, color = ("✖", t.ERROR) if i.severity is Severity.ERROR else ("⚠", t.WARN)
            item = QListWidgetItem(f"{icon}  {i.message}")
            item.setForeground(QColor(color))
            target = None if i.board is None else (i.board, self._pin_of(i.board, i.line))
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.checks.addItem(item)
        if empty:
            item = QListWidgetItem("Nothing to check yet. Open or drop board .json files, "
                                   "or double-click a pin to add a signal.")
            item.setForeground(QColor(t.DIM))
            self.checks.addItem(item)
        elif not issues:
            item = QListWidgetItem("No problems on any open board. It is safe to save.")
            item.setForeground(QColor(t.OK))
            self.checks.addItem(item)
        fps = p.file_problems
        self.act_reload.setEnabled(here.path is not None and not none_here)
        names = ", ".join(sorted({f.file for f in fps if f.file})) or "the file"
        self.file_note.setText(f"{len(fps)} problem(s) in {names}: fix the file and reload its board, or")
        for w in (self.file_note, self.reload_btn, self.accept_btn):
            w.setVisible(bool(fps))
        self.checks_summary.setText("" if empty else
            f'<span style="color:{t.ERROR}">{errors} error(s)</span> · '
            f'<span style="color:{t.WARN}">{warnings} warning(s)</span>')

    def _chip(self, text: str, role: str) -> None:
        self.chip_state.setText(text)
        if self.chip_state.property("role") != role:
            self.chip_state.setProperty("role", role)
            self.chip_state.style().unpolish(self.chip_state)
            self.chip_state.style().polish(self.chip_state)

    def _refresh_pin_tab(self, issues) -> None:
        b = self._b()
        pin = self.focus_pin
        shown: set[QWidget] = set()  # widgets visible in this state; the rest are hidden
        try:
            if pin is None:
                self.pin_title.setText("No pin selected")
                self.pin_sub.setText("")
                self.pin_hint.setText("Click a pin on the connector to edit it.\n"
                                      "Double-click a free pin to assign it straight away.")
                shown.add(self.pin_hint)
                return
            s = b.bt.signal_at(pin)
            self.pin_title.setText(f"Pin {pin}")
            if s.kind is not SignalKind.LINE:
                self.pin_sub.setText(f"{_short(b.label, 20)}  ·  {b.bt.key}  ·  {s}")
                self.pin_hint.setText(f"{s}: {s.about}.\nThis pin can't carry a signal.")
                shown.add(self.pin_hint)
                return
            line = s.line
            self.pin_sub.setText(f"{_short(b.label, 20)}  ·  {b.bt.key}  ·  {line}")
            a = b.lines.get(line)
            if a is None:
                self.pin_hint.setText("This line is free.")
                self.assign_btn.setText(f"+  Add a signal on {line}")
                shown |= {self.pin_hint, self.assign_btn}
                return
            shown |= {self.form, self.move_row, self.remove_btn}
            _set_text(self.f_name, a.name)
            d = b.direction(line)
            group = b.bt.group(line)  # None: the board fixes the direction
            if self.f_dir_pick.isHidden() == (group is not None):
                self.f_dir_pick.setVisible(group is not None)
            if group is None:
                color = t.OUTPUT_TEXT if d is Direction.OUTPUT else t.INPUT_TEXT
                self.f_dir.setText(f'<span style="color:{color}"><b>{d.value}</b></span>'
                                   f'<span style="color:{t.DIM}"> · fixed: {b.bt.why_direction(line)}</span>')
            else:
                self.f_dir_pick.setCurrentIndex(1 if d is Direction.OUTPUT else 0)
                self.f_dir.setText(f'<span style="color:{t.DIM}">for all of {group}</span>')
            self.move_edit.setPlaceholderText(b.bt.example)
            if self.f_note.toPlainText() != a.note and not self.f_note.hasFocus():
                self.f_note.setPlainText(a.note)
            mine = [i for i in issues if i.board == self.board and i.line == line]
            if mine:
                self.pin_issues.setText("<br>".join(
                    f'<span style="color:{t.ERROR if i.severity is Severity.ERROR else t.WARN}">'
                    f"{html.escape(i.message)}</span>"
                    for i in mine))
                shown.add(self.pin_issues)
        finally:
            # Only touch widgets whose visibility actually changes: hiding the
            # form, even for an instant, takes keyboard focus away from the
            # name field and the next keystroke lands somewhere else.
            for w in (self.form, self.move_row, self.remove_btn, self.pin_issues,
                      self.assign_btn, self.pin_hint):
                if w.isHidden() == (w in shown):
                    w.setVisible(w in shown)

    def _inspector_tab_shown(self, index: int) -> None:
        _tint_tabs(self.tabs.tabBar())
        if index == 1 and not self._updating:
            self.refresh()  # the table was left alone while it was hidden: catch up

    def _refresh_table(self) -> None:
        # Kept cheap, because every keystroke ends up here: it waits until the
        # Signals tab is on screen, rewrites only the rows that changed, and
        # sizes the columns once at the end instead of after every cell.
        if self.tabs.currentIndex() != 1 or self.table.state() == QTableWidget.State.EditingState:
            return
        needle = self.filter_edit.text().strip().upper()
        rows = []
        for bi, bd in enumerate(self.project.boards):
            board = bd.label
            for line, a in bd.sorted_lines():
                if needle and needle not in f"{a.name.upper()} {line} {board.upper()} {bd.type}":
                    continue
                rows.append((bi, line, a.name, board, bd.direction(line), a.note, bd.bt.pin_of(line)))
        old, focus = self._table_rows, (self.board, self.focus_pin)
        if rows == old and focus == self._table_focus:
            return
        # Same signals in the same order (the usual case: a name was typed)?
        same = len(rows) == len(old) and all(new[:2] == was[:2] for new, was in zip(rows, old))
        table, header = self.table, self.table.horizontalHeader()
        sized = (1, 2, 3, 4)  # the columns that fit their contents
        table.setUpdatesEnabled(False)
        for c in sized:
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        try:
            if not same:
                table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                if not same or row != old[r]:
                    self._fill_table_row(r, row)
                if (row[0], row[6]) == focus and table.currentRow() != r:
                    table.selectRow(r)
        finally:
            for c in sized:
                header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            table.setUpdatesEnabled(True)
        self._table_rows, self._table_focus = rows, focus

    def _fill_table_row(self, r: int, row: tuple) -> None:
        bi, line, name, board, direction, note, pin = row
        ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        cells = [QTableWidgetItem(name), QTableWidgetItem(board), QTableWidgetItem(str(line)),
                 QTableWidgetItem(str(pin)),
                 QTableWidgetItem("in" if direction is Direction.INPUT else "out"),
                 QTableWidgetItem(note)]
        cells[0].setData(Qt.ItemDataRole.UserRole, (bi, line))
        cells[4].setForeground(QColor(t.INPUT_TEXT if direction is Direction.INPUT else t.OUTPUT_TEXT))
        cells[1].setForeground(QColor(t.DIM))
        for c, item in enumerate(cells):
            if c in (1, 2, 3, 4):
                item.setFlags(ro)
            if c in (0, 2, 3, 4):
                item.setFont(self.mono)
            self.table.setItem(r, c, item)

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
            self.drop_overlay.setGeometry(self.centralWidget().geometry())
            self.drop_overlay.raise_()
            self.drop_overlay.show()
        elif e.mimeData().hasUrls():
            self.status("Only board .json files (or a folder of them) can be dropped here.", error=True)

    def dragLeaveEvent(self, _e) -> None:
        self.drop_overlay.hide()

    def dropEvent(self, e) -> None:
        self.drop_overlay.hide()
        paths = self._dropped(e.mimeData())
        if paths and self.open_paths(paths):
            e.acceptProposedAction()


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
    spot and unsaved boards are lost; with it the window stays open."""
    traceback.print_exception(kind, value, tb)
    box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME, "Something went wrong.")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setInformativeText("Your boards are still open. Save your work, then restart the editor.")
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