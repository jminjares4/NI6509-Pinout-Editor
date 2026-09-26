"""The PyQt6 editor window.

Pattern: every change goes through ``self.edit(...)``, which records an undo
snapshot, applies the change to ``self.project``, and calls ``refresh()``.
``refresh()`` redraws every view from the project, so the connector, table,
JSON and checks can never disagree with each other. Widget signals are
ignored while ``refresh()`` runs (``self._updating``) so it doesn't feed back.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QColor, QFontDatabase, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
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
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from model import Direction, History, LoadError, Project, Severity, direction_of, worst_on
from pinout import Line, SignalKind, signal_at
from schema import SCHEMA
import theme as t
from connector import ConnectorWidget

APP_NAME = "NI 6509 Pinout Editor"
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


def _set_text(w: QLineEdit, text: str) -> None:
    """Update a field without fighting the user while they type in it."""
    if w.text() != text and not w.hasFocus():
        w.setText(text)


class MainWindow(QMainWindow):
    def __init__(self, path: Path | None = None, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("ni6509", "pinout-editor")
        self.project = Project()
        self.history = History()
        self.saved_json = self.project.to_json()
        self.path: Path | None = None
        self.board = 0
        self.focus_pin: int | None = None
        self._updating = False
        self._table_signature: object = None
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self.resize(1480, 920)
        self.setAcceptDrops(True)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self.statusBar().showMessage(
            "New project. Double-click a pin to add a signal, or drop a .json file to load one.")
        if path:
            self.open_path(path)
        self.refresh()
        self.connector.setFocus()  # so the first keystroke doesn't land in a text field

    # ================================================================ dialogs

    def ask_unsaved(self) -> str:
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME,
                          "This project has unsaved changes.", parent=self)
        box.setInformativeText("Save them first?")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {save: "save", discard: "discard"}.get(box.clickedButton(), "cancel")

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def ask_open_path(self) -> str | None:
        path, _ = QFileDialog.getOpenFileName(self, "Open mapping", self._dialog_dir(),
                                              "Mapping (*.json);;All files (*)")
        return path or None

    def ask_save_path(self, title: str, suggested: str, filter_: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path(self._dialog_dir()) / suggested), filter_)
        return path or None

    def _dialog_dir(self) -> str:
        return str(self.path.parent) if self.path else str(Path.cwd())

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
        self.act_open = self._action("Open…", self.open_dialog, K.Open)
        self.act_reload = self._action("Reload from Disk", self.reload, QKeySequence("Ctrl+R"),
                                       "Read the file again, e.g. after fixing it in a text editor")
        self.act_save = self._action("Save", self.save, K.Save,
                                     "Save (refused while the project has errors)")
        self.act_save_as = self._action("Save As…", self.save_as, K.SaveAs)
        self.act_undo = self._action("Undo", self.undo, K.Undo)
        self.act_redo = self._action("Redo", self.redo, K.Redo)
        self.act_schema = self._action("Export JSON Schema…", self.export_schema,
                                       tip="So other programs can check the file's structure")
        self.act_add_board = self._action("Add board", self.add_board)
        self.act_remove_board = self._action("Remove board", self.remove_board)
        self.act_remove_assignment = self._action("Remove assignment", self.remove_assignment)
        self.act_quit = self._action("Quit", self.close, K.Quit)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addActions([self.act_new, self.act_open])
        self.recent_menu = f.addMenu("Open Recent")
        f.addAction(self.act_reload)
        f.addSeparator()
        f.addActions([self.act_save, self.act_save_as])
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
        tb.addActions([self.act_new, self.act_open, self.act_save])
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
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(6)

        # Top-level tabs: one per card type, plus the JSON the project saves to.
        self.sections = QTabWidget(objectName="sections")
        page6509 = QWidget()
        root = QVBoxLayout(page6509)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        # Board tabs and board settings
        strip = QHBoxLayout()
        self.board_tabs = QTabBar(objectName="boardTabs")
        self.board_tabs.setExpanding(False)
        self.board_tabs.setDrawBase(False)
        self.board_tabs.currentChanged.connect(self._tab_changed)
        strip.addWidget(self.board_tabs)
        add = _button("+ Add board", tip="Adds the next free Board N / DevN")
        add.clicked.connect(self.add_board)
        strip.addWidget(add)
        strip.addStretch(1)
        root.addLayout(strip)

        settings = QHBoxLayout()
        settings.setSpacing(8)
        self.board_name = QLineEdit()
        self.board_name.setFixedWidth(170)
        self.board_name.textChanged.connect(
            lambda s: self._text_edit(("board-name", self.board), lambda: setattr(self._b(), "name", s)))
        self.remove_board_btn = _button("Remove board", "danger")
        self.remove_board_btn.clicked.connect(self.remove_board)
        for w in (_label("Board name", "dim"), self.board_name, self.remove_board_btn):
            settings.addWidget(w)
        settings.addStretch(1)
        root.addLayout(settings)

        # Connector | inspector
        hsplit = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.heading = _label("", "h1")
        self.subheading = _label("", "dim")
        head.addWidget(self.heading)
        head.addWidget(self.subheading)
        head.addStretch(1)
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
        # Wide enough for the whole connector, so it never scrolls sideways.
        bar = scroll.style().pixelMetric(scroll.style().PixelMetric.PM_ScrollBarExtent)
        scroll.setMinimumWidth(self.connector.minimumWidth() + bar + 2 * scroll.frameWidth())
        lv.addWidget(scroll, 1)
        legend = _label(
            f'<span style="color:{t.INPUT}">■</span> input &nbsp; '
            f'<span style="color:{t.OUTPUT}">■</span> output &nbsp; '
            f'<span style="color:{t.FREE}">■</span> free &nbsp; '
            f'<span style="color:{t.VCC}">■</span> +5 V &nbsp; '
            f'<span style="color:#555">□</span> GND &nbsp; '
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
        hsplit.addWidget(self.tabs)
        hsplit.setStretchFactor(0, 3)
        hsplit.setStretchFactor(1, 2)
        hsplit.setSizes([900, 560])
        hsplit.setCollapsible(0, False)
        hsplit.setCollapsible(1, False)
        root.addWidget(hsplit, 1)

        self.sections.addTab(page6509, "NI 6509")
        self.sections.addTab(self._build_json_tab(), "JSON")
        corner = _label("Drop a .json file anywhere on this window to load it  ", "dim")
        self.sections.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(self.sections)

        checks = QWidget()
        cv = QVBoxLayout(checks)
        cv.setContentsMargins(0, 6, 0, 0)
        ch = QHBoxLayout()
        ch.addWidget(_label("<b>Checks</b>"))
        ch.addWidget(_label("whole project", "dim"))
        self.checks_summary = _label("")
        ch.addWidget(self.checks_summary)
        ch.addStretch(1)
        # Shown only when the opened file had problems (see Project.file_problems).
        self.file_note = _label("", "dim")
        self.reload_btn = _button("Reload file", tip="Read the file again after fixing it (Ctrl+R)")
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
        vsplit.setSizes([740, 120])
        outer.addWidget(vsplit, 1)
        self.setCentralWidget(central)

        # Shown while a .json is dragged over the window.
        self.drop_overlay = QWidget(self, objectName="dropOverlay")
        self.drop_overlay.setStyleSheet(
            f"#dropOverlay {{ background: rgba(47,110,186,50); border: 3px dashed {t.ACCENT}; border-radius: 12px; }}"
            f"#dropPill {{ background: {t.PANEL}; border: 1px solid {t.ACCENT}; border-radius: 10px;"
            "  padding: 14px 28px; font-size: 16pt; font-weight: 600; }")
        pill = QLabel("Drop to load this pin mapping", objectName="dropPill")
        ov = QVBoxLayout(self.drop_overlay)
        ov.addWidget(pill, 0, Qt.AlignmentFlag.AlignCenter)
        self.drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_overlay.hide()

    def _build_pin_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
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
        self.f_dir = QLabel("")  # fixed by the line: P0-P5 outputs, P6-P11 inputs
        self.f_note = QPlainTextEdit()
        self.f_note.setPlaceholderText("wire color, harness, test step…")
        self.f_note.setFixedHeight(60)
        self.f_note.textChanged.connect(self._note_changed)
        f.addRow("Name", self.f_name)
        f.addRow("Direction", self.f_dir)
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
        self.filter_edit.setPlaceholderText("Filter by name, line or board")
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
        v.addWidget(_label("Double-click Name or Note to edit. "
                           "Direction is fixed: P0–P5 outputs, P6–P11 inputs.", "dim"))
        return w

    def _build_json_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 10, 8, 8)
        row = QHBoxLayout()
        copy = _button("Copy to clipboard")
        row.addWidget(copy)
        row.addWidget(_label("The whole project, all boards. Save writes exactly this.", "dim"))
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
        s = signal_at(self.focus_pin)
        return s.line if s and s.kind is SignalKind.DIO else None

    def dirty(self) -> bool:
        return self.project.to_json() != self.saved_json

    def edit(self, change: Callable[[], object], key: object = None) -> None:
        """Record an undo snapshot, apply ``change``, refresh every view."""
        self.history.record(self.project, key)
        change()
        self.refresh()

    def _text_edit(self, key: object, change: Callable[[], object]) -> None:
        if not self._updating:
            self.edit(change, key)

    def jump(self, board: int, pin: int | None, show_pin_tab: bool = True) -> None:
        self.sections.setCurrentIndex(0)
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
        if not self.dirty():
            return True
        answer = self.ask_unsaved()
        if answer == "save":
            return self.save()
        return answer == "discard"

    def new_project(self) -> None:
        if not self._keep_going_despite_changes():
            return
        self._replace_project(Project(), None)
        self.status("New project.")

    def open_dialog(self) -> None:
        if not self._keep_going_despite_changes():
            return
        path = self.ask_open_path()
        if path:
            self.open_path(Path(path), confirmed=True)

    def open_path(self, path: Path, confirmed: bool = False) -> bool:
        if not confirmed and not self._keep_going_despite_changes():
            return False
        try:
            project = Project.load(path)
        except LoadError as e:  # can't read the file at all
            self.status(f"Could not open: {e}", error=True)
            return False
        changes = project.format_changes()
        problems = len(project.file_problems)
        self._replace_project(project, Path(path))
        self._add_recent(Path(path))
        msg = (f"Opened {Path(path).name}: {len(project.boards)} board(s), "
               f"{project.signal_count()} signals")
        if changes or problems:
            # What's on disk differs from what's shown: mark it unsaved.
            self.saved_json = ""
            self.refresh()
        if problems:
            msg += f". {problems} problem(s) in the file: see Checks"
        if changes:
            msg += f". Older format: saving updates it ({'; '.join(changes)})"
        msg += "."
        self.status(msg, error=bool(problems))
        return True

    def reload(self) -> None:
        if self.path is None:
            self.status("Nothing to reload: this project hasn't been saved to a file.", error=True)
            return
        if self.history.can_undo and not self._keep_going_despite_changes():
            return
        self.open_path(self.path, confirmed=True)

    def accept_file_problems(self) -> None:
        self.edit(lambda: self.project.file_problems.clear())
        self.status("Accepted what was loaded. Saving writes the file as shown here (Undo to go back).")

    def _replace_project(self, project: Project, path: Path | None) -> None:
        self.project = project
        self.saved_json = project.to_json()
        self.path = path
        self.board = 0
        self.focus_pin = None
        self.history.clear()
        self.refresh()

    def save(self) -> bool:
        errors = self.project.errors()
        if errors:
            self.status(f"Not saved: fix the {len(errors)} error(s) listed in Checks first.", error=True)
            self.checks.setCurrentRow(0)
            self.checks.scrollToTop()
            return False
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        if self.project.errors():
            return self.save()  # shows the same refusal
        suggested = self.path.name if self.path else "mapping.json"
        path = self.ask_save_path("Save mapping", suggested, "Mapping (*.json)")
        if not path:
            return False
        path = Path(path)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        return self._write(path)

    def _write(self, path: Path) -> bool:
        try:
            self.project.save(path)
        except OSError as e:
            self.status(f"Not saved: could not write {path}: {e.strerror or e}", error=True)
            return False
        self.path = path
        self.saved_json = self.project.to_json()
        self._add_recent(path)
        self.status(f"Saved {path.name}")
        self.refresh()
        return True

    def export_schema(self) -> None:
        import json
        path = self.ask_save_path("Export JSON Schema", "ni6509-mapping.schema.json", "JSON Schema (*.json)")
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
            a.triggered.connect(lambda _=False, r=r: self.open_path(Path(r)))
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

    def _tab_changed(self, index: int) -> None:
        if not self._updating and index >= 0:
            self.jump(index, None)

    def add_board(self) -> None:
        box: list[int] = []
        self.edit(lambda: box.append(self.project.add_board()))
        self.jump(box[0], None)

    def remove_board(self) -> None:
        if len(self.project.boards) <= 1:
            return
        b = self._b()
        if b.lines and not self.confirm("Remove board",
                                        f"Remove {b.label} and its {len(b.lines)} signal(s)?\n"
                                        "You can undo this."):
            return
        index = self.board
        self.edit(lambda: self.project.remove_board(index))
        self.jump(min(index, len(self.project.boards) - 1), None)
        self.status("Board removed. Undo brings it back.")

    # ================================================================ pin operations

    def _pin_double_clicked(self, pin: int) -> None:
        s = signal_at(pin)
        new = s.kind is SignalKind.DIO and s.line not in self._b().lines
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
        src = self._focus_line()
        if not src or src not in self._b().lines:
            return
        try:
            dst = Line.parse(self.move_edit.text())
        except ValueError as e:
            self.status(str(e), error=True)
            return
        if dst == src:
            return
        if dst in self._b().lines:
            self.status(f"{dst} is already assigned on this board.", error=True)
            return
        self.edit(lambda: self.project.move(self.board, src, dst))
        self.move_edit.clear()
        self.jump(self.board, dst.pin)
        msg = f"Moved to {dst} (pin {dst.pin})."
        if direction_of(dst) is not direction_of(src):
            msg += f" It is now an {direction_of(dst).value}: pins 1–50 are outputs, 51–100 inputs."
        self.status(msg)

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
        self.jump(bi, line.pin, show_pin_tab=False)

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
        p, b = self.project, self._b()
        issues = p.issues()
        errors = sum(i.severity is Severity.ERROR for i in issues)
        warnings = len(issues) - errors
        dirty = self.dirty()

        name = self.path.name if self.path else "untitled"
        self.setWindowTitle(f"{name}{' •' if dirty else ''} — {APP_NAME}")
        self.act_undo.setEnabled(self.history.can_undo)
        self.act_redo.setEnabled(self.history.can_redo)
        self.act_remove_board.setEnabled(len(p.boards) > 1)
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

        # Board tabs
        while self.board_tabs.count() < len(p.boards):
            self.board_tabs.addTab("")
        while self.board_tabs.count() > len(p.boards):
            self.board_tabs.removeTab(self.board_tabs.count() - 1)
        for i, bd in enumerate(p.boards):
            self.board_tabs.setTabText(i, f"{bd.label}  ({len(bd.lines)})")
            sev = worst_on(issues, i)
            self.board_tabs.setTabTextColor(
                i, QColor(t.ERROR if sev is Severity.ERROR else t.WARN if sev is Severity.WARNING else t.TEXT))
        self.board_tabs.setCurrentIndex(self.board)
        _set_text(self.board_name, b.name)

        self.heading.setText(b.label)
        self.subheading.setText("·  PCIe-6509  ·  100-pin SCSI")
        self.connector.show_board(b, self.board, issues, self.focus_pin)

        self._refresh_pin_tab(issues)
        self.tabs.setTabText(1, f"Signals ({p.signal_count()})")
        sev = max((x.severity for x in issues), default=None)
        self.sections.setTabText(0, f"NI 6509  ({p.signal_count()})")
        self.sections.tabBar().setTabTextColor(
            0, QColor(t.ERROR if sev is Severity.ERROR else t.WARN if sev is Severity.WARNING else t.TEXT))
        self._refresh_table()
        json_text = p.to_json()
        if self.json_view.toPlainText() != json_text:
            self.json_view.setPlainText(json_text)

        self.checks.clear()
        for i in issues:
            icon, color = ("✖", t.ERROR) if i.severity is Severity.ERROR else ("⚠", t.WARN)
            item = QListWidgetItem(f"{icon}  {i.message}")
            item.setForeground(QColor(color))
            target = None if i.board is None else (i.board, i.line.pin if i.line else None)
            item.setData(Qt.ItemDataRole.UserRole, target)
            self.checks.addItem(item)
        if empty:
            item = QListWidgetItem("Nothing to check yet. Open or drop a .json file, "
                                   "or double-click a pin to add a signal.")
            item.setForeground(QColor(t.DIM))
            self.checks.addItem(item)
        elif not issues:
            item = QListWidgetItem("No problems anywhere in the project. It is safe to save.")
            item.setForeground(QColor(t.OK))
            self.checks.addItem(item)
        fps = p.file_problems
        self.act_reload.setEnabled(self.path is not None)
        name = self.path.name if self.path else "the file"
        self.file_note.setText(f"{len(fps)} problem(s) in {name}: fix it and reload, or" if fps else "")
        for w in (self.file_note, self.reload_btn):
            w.setVisible(bool(fps))
        # When nothing could be loaded there is nothing to accept.
        self.accept_btn.setVisible(bool(fps) and not any(f.fatal for f in fps))
        if fps and any(f.fatal for f in fps):
            self.file_note.setText(f"{name} could not be read: fix it and")
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
            s = signal_at(pin)
            self.pin_title.setText(f"Pin {pin}")
            if s.kind is not SignalKind.DIO:
                self.pin_sub.setText(f"{s} supply pin")
                self.pin_hint.setText("Power and ground pins can't carry a signal.")
                shown.add(self.pin_hint)
                return
            line = s.line
            self.pin_sub.setText(f"{b.label}  ·  {line}  ·  port {line.port}, line {line.line}")
            a = b.lines.get(line)
            if a is None:
                self.pin_hint.setText("This line is free.")
                self.assign_btn.setText(f"+  Add a signal on {line}")
                shown |= {self.pin_hint, self.assign_btn}
                return
            shown |= {self.form, self.move_row, self.remove_btn}
            _set_text(self.f_name, a.name)
            d = direction_of(line)
            color, half = ((t.OUTPUT_TEXT, "pins 1–50 (P0–P5) are outputs") if d is Direction.OUTPUT
                           else (t.INPUT_TEXT, "pins 51–100 (P6–P11) are inputs"))
            self.f_dir.setText(f'<span style="color:{color}"><b>{d.value}</b></span>'
                               f'<span style="color:{t.DIM}"> · fixed: {half}</span>')
            if self.f_note.toPlainText() != a.note and not self.f_note.hasFocus():
                self.f_note.setPlainText(a.note)
            mine = [i for i in issues if i.board == self.board and i.line == line]
            if mine:
                self.pin_issues.setText("<br>".join(
                    f'<span style="color:{t.ERROR if i.severity is Severity.ERROR else t.WARN}">{i.message}</span>'
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

    def _refresh_table(self) -> None:
        if self.table.state() == QTableWidget.State.EditingState:
            return
        needle = self.filter_edit.text().strip().upper()
        rows = []
        for bi, bd in enumerate(self.project.boards):
            board = bd.label
            for line, a in bd.sorted_lines():
                if needle and needle not in f"{a.name.upper()} {line} {board.upper()}":
                    continue
                rows.append((bi, line, a.name, board, direction_of(line), a.note))
        signature = (tuple(rows), self.board, self.focus_pin)
        if signature == self._table_signature:
            return
        self._table_signature = signature
        self.table.setRowCount(len(rows))
        ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for r, (bi, line, name, board, direction, note) in enumerate(rows):
            cells = [QTableWidgetItem(name), QTableWidgetItem(board), QTableWidgetItem(str(line)),
                     QTableWidgetItem(str(line.pin)),
                     QTableWidgetItem("in" if direction is Direction.INPUT else "out"),
                     QTableWidgetItem(note)]
            cells[0].setData(Qt.ItemDataRole.UserRole, (bi, line))
            for c, item in enumerate(cells):
                if c in (1, 2, 3, 4):
                    item.setFlags(ro)
                if c in (0, 2, 3, 4):
                    item.setFont(self.mono)
                self.table.setItem(r, c, item)
            cells[4].setForeground(QColor(t.INPUT_TEXT if direction is Direction.INPUT else t.OUTPUT_TEXT))
            cells[1].setForeground(QColor(t.DIM))
            if bi == self.board and self.focus_pin == line.pin:
                self.table.selectRow(r)

    # ================================================================ window events

    def closeEvent(self, e) -> None:
        if self._keep_going_despite_changes():
            e.accept()
        else:
            e.ignore()

    @staticmethod
    def _dropped_json(mime) -> Path | None:
        """The first local .json file in a drag, if there is one."""
        for url in mime.urls() if mime.hasUrls() else []:
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".json"):
                return Path(url.toLocalFile())
        return None

    def dragEnterEvent(self, e) -> None:
        if self._dropped_json(e.mimeData()):
            e.acceptProposedAction()
            self.drop_overlay.setGeometry(self.centralWidget().geometry())
            self.drop_overlay.raise_()
            self.drop_overlay.show()
        elif e.mimeData().hasUrls():
            self.status("Only .json mapping files can be dropped here.", error=True)

    def dragLeaveEvent(self, _e) -> None:
        self.drop_overlay.hide()

    def dropEvent(self, e) -> None:
        self.drop_overlay.hide()
        path = self._dropped_json(e.mimeData())
        if path and self.open_path(path):
            e.acceptProposedAction()
            self.jump(0, None)


def _dark_palette() -> QPalette:
    pal = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, t.BG), (QPalette.ColorRole.WindowText, t.TEXT),
        (QPalette.ColorRole.Base, "#111216"), (QPalette.ColorRole.AlternateBase, t.PANEL),
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


def run(path: Path | None = None) -> int:
    app = make_app()
    window = MainWindow(path)
    window.show()
    return app.exec()


def main() -> int:
    """``python3 app.py [file.json]``: open the editor, optionally with a file."""
    return run(Path(sys.argv[1]) if len(sys.argv) > 1 else None)


if __name__ == "__main__":
    sys.exit(main())