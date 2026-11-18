"""The connector drawing, painted with QPainter.

Draws whichever board it is shown: the 100-pin connector of the 6509 as two
blocks of 50 (the layout of the pinout figure in the NI user guide), a DSUB
as one block with pins 1..n down the left and the rest down the right. The
layout comes from the board type's ``blocks`` (see ``pinout.py``). The widget
only draws and reports clicks; it never changes the project itself.

Hovering is kept cheap so it tracks the mouse smoothly: finding the pin under
the cursor is arithmetic, moving between pins repaints only the two pin boxes
involved, and the pin details show in a card drawn inside the widget instead
of a native tooltip window.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QWidget

from model import Board, Direction, Issue, Severity, worst_on
from pinout import BOARD_TYPES, DEFAULT_TYPE, BoardType, SignalKind, board_type
import theme as t

ROW_H = 19
PIN_W, PIN_H = 36, 16
LABEL_W = 150  # channel name plus signal name, beside each pin
SIG_W = 54     # of which the channel name ("P11.7", "AISENSE")
SPINE = 6
BLOCK_GAP = 40
HEADER_H = 20
BLOCK_W = LABEL_W + 6 + PIN_W + SPINE + PIN_W + 6 + LABEL_W
MARGIN = 8
SIDE = 36  # extra room left and right so the hover card fits beside the outer columns


class _Layout:
    """Where everything goes for one board type, worked out once."""

    def __init__(self, bt: BoardType) -> None:
        self.rects: dict[int, QRectF] = {}
        self.left: dict[int, bool] = {}  # pin -> its labels are on its left
        #: (x, y of the first row, pins top to bottom), for finding a pin by position
        self.columns: list[tuple[float, float, tuple[int, ...]]] = []
        self.blocks: list[tuple[float, str, QRectF, tuple[int, ...]]] = []
        rows = 0
        top = MARGIN + HEADER_H
        for n, block in enumerate(bt.blocks):
            x0 = MARGIN + SIDE + n * (BLOCK_W + BLOCK_GAP)
            left_x = x0 + LABEL_W + 6
            longest = max(len(block.left), len(block.right))
            rows = max(rows, longest)
            for pins, x, is_left in ((block.left, left_x, True),
                                     (block.right, left_x + PIN_W + SPINE, False)):
                # The shorter row of a DSUB sits half a step lower, like the real thing.
                y0 = top + (ROW_H / 2 if block.stagger and len(pins) < longest else 0)
                self.columns.append((x, y0, pins))
                for row, pin in enumerate(pins):
                    self.rects[pin] = QRectF(x, y0 + row * ROW_H + (ROW_H - PIN_H) / 2, PIN_W, PIN_H)
                    self.left[pin] = is_left
            housing = QRectF(left_x - 5, top + (ROW_H - PIN_H) / 2 - 5,
                             2 * PIN_W + SPINE + 10, (longest - 1) * ROW_H + PIN_H + 10)
            self.blocks.append((x0, block.caption, housing, block.left + block.right))
        self.size = QSize(len(bt.blocks) * BLOCK_W + (len(bt.blocks) - 1) * BLOCK_GAP
                          + 2 * (MARGIN + SIDE), HEADER_H + rows * ROW_H + 2 * MARGIN)


_LAYOUTS: dict[str, _Layout] = {}


def layout_of(bt: BoardType) -> _Layout:
    if bt.key not in _LAYOUTS:
        _LAYOUTS[bt.key] = _Layout(bt)
    return _LAYOUTS[bt.key]


def widest() -> int:
    """Width of the widest connector, so the window can leave room for it."""
    return max(layout_of(bt).size.width() for bt in BOARD_TYPES.values())


class ConnectorWidget(QWidget):
    pinClicked = pyqtSignal(int)
    pinDoubleClicked = pyqtSignal(int)
    deletePressed = pyqtSignal()
    escapePressed = pyqtSignal()
    #: Click on the selected pin again, or on empty space: clear the selection.
    cleared = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.board: Board | None = None
        self.board_index = 0
        self.issues: list[Issue] = []
        self.focus_pin: int | None = None
        self.hover_pin: int | None = None
        self.bt = board_type(DEFAULT_TYPE)
        self.lay = layout_of(self.bt)
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.mono.setPointSizeF(9)
        self.mono_bold = QFont(self.mono)
        self.mono_bold.setBold(True)
        self.setFixedSize(self.lay.size)
        # Hover details: a plain child label, so showing and moving it is
        # instant (no window to create, no fade-in like QToolTip on macOS).
        self.card = QLabel(self)
        self.card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.card.setTextFormat(Qt.TextFormat.PlainText)  # signal names and notes come from files
        self.card.setStyleSheet(
            f"QLabel {{ background: {t.RAISED}; color: {t.TEXT}; border: 1px solid {t.PIN_EDGE};"
            " border-radius: 6px; padding: 6px 9px; }")
        self.card.hide()

    def sizeHint(self) -> QSize:
        return self.lay.size

    def show_board(self, board: Board, index: int, issues: list[Issue], focus_pin: int | None) -> None:
        self.board, self.board_index, self.issues, self.focus_pin = board, index, issues, focus_pin
        if board.bt is not self.bt:
            # A different connector: new layout, new size, nothing hovered.
            self.bt = board.bt
            self.lay = layout_of(self.bt)
            self.hover_pin = None
            self.card.hide()
            self.setFixedSize(self.lay.size)
        if self.card.isVisible() and self.hover_pin:
            self._show_card(self.hover_pin)
        self.update()

    # ------------------------------------------------------------ geometry

    def pin_rect(self, pin: int) -> QRectF:
        """Screen rectangle of a pin box."""
        return self.lay.rects[pin]

    def pin_at(self, pos: QPoint | QPointF) -> int | None:
        """The pin under ``pos``, by arithmetic (this runs on every mouse move)."""
        x, y = pos.x(), pos.y()
        for left, y0, pins in self.lay.columns:
            if left <= x < left + PIN_W:
                row = int((y - y0) // ROW_H)
                if 0 <= row < len(pins) and self.pin_rect(pins[row]).contains(QPointF(x, y)):
                    return pins[row]
                return None
        return None

    def _row_rect(self, pin: int) -> QRectF:
        """Everything drawn for one pin: its box plus the labels beside it."""
        r = self.pin_rect(pin)
        if self.lay.left[pin]:
            return QRectF(r.left() - 6 - LABEL_W, r.top() - 3, LABEL_W + 6 + r.width() + 2, r.height() + 6)
        return QRectF(r.left() - 2, r.top() - 3, r.width() + 8 + LABEL_W, r.height() + 6)

    # ------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:
        if self.board is None:
            return
        dirty = QRectF(event.rect())  # hover repaints only a small area: skip the rest
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for x0, caption, housing, pins in self.lay.blocks:
            p.setPen(t.qc(t.DIM))
            p.setFont(self.font())
            p.drawText(QRectF(x0, MARGIN, BLOCK_W, 16), Qt.AlignmentFlag.AlignCenter, caption)
            p.setPen(QPen(t.qc(t.HOUSING_EDGE), 1))
            p.setBrush(t.qc(t.HOUSING))
            p.drawRoundedRect(housing, 8, 8)
            for pin in pins:
                if self._row_rect(pin).intersects(dirty):
                    self._paint_pin(p, pin)
        p.end()

    def _paint_pin(self, p: QPainter, pin: int) -> None:
        board = self.board
        s = self.bt.pins[pin]
        line = s.line
        a = board.lines.get(line) if line else None
        sev = worst_on(self.issues, self.board_index, line) if line else None
        rect = self.pin_rect(pin)
        output = bool(a) and board.direction(line) is Direction.OUTPUT

        if a:
            fill, text = (t.OUTPUT if output else t.INPUT), t.TEXT
        elif s.kind is SignalKind.POWER:
            fill, text = t.VCC, t.VCC_TEXT
        elif s.kind is SignalKind.GND:
            fill, text = t.GND, t.DIM
        elif s.kind is SignalKind.NC:
            fill, text = t.NC, t.NC_TEXT
        elif s.kind is SignalKind.AUX:
            fill, text = t.AUX, t.DIM
        else:
            fill, text = t.FREE, t.DIM

        if pin == self.focus_pin:
            pen = QPen(t.qc("#ffffff"), 2)
        elif sev is Severity.ERROR:
            pen = QPen(t.qc(t.ERROR), 2)
        elif sev is Severity.WARNING:
            pen = QPen(t.qc(t.WARN), 1.5)
        elif pin == self.hover_pin:
            pen = QPen(t.qc(t.ACCENT), 1.5)
        else:
            pen = QPen(t.qc(t.PIN_EDGE), 1)
        p.setPen(pen)
        p.setBrush(t.qc(fill))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.setPen(t.qc(text))
        p.setFont(self.mono)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(pin))

        # Channel name (P2.3, AI5) beside the box, signal name beyond it.
        left = self.lay.left[pin]
        cy = rect.center().y()
        sig_rect = (QRectF(rect.left() - 6 - SIG_W, cy - 9, SIG_W, 18) if left
                    else QRectF(rect.right() + 6, cy - 9, SIG_W, 18))
        align = (Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
        p.setPen(t.qc(t.TEXT if a else t.NC_TEXT if s.kind is SignalKind.NC else t.DIM))
        p.setFont(self.mono_bold if a else self.mono)
        p.drawText(sig_rect, align, str(s))
        if a:
            name_w = LABEL_W - SIG_W - 4
            name_rect = (QRectF(rect.left() - 6 - LABEL_W, cy - 9, name_w, 18) if left
                         else QRectF(rect.right() + 6 + SIG_W + 4, cy - 9, name_w, 18))
            color = (t.ERROR if sev is Severity.ERROR else t.WARN if sev is Severity.WARNING
                     else t.OUTPUT_TEXT if output else t.INPUT_TEXT)
            p.setPen(t.qc(color))
            p.setFont(self.mono)
            name = a.name.strip() or "(no name)"
            elided = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(name_rect.width()))
            p.drawText(name_rect, align, elided)

    # ------------------------------------------------------------ input

    def tooltip_for(self, pin: int) -> str:
        """Hover details, one short fact per line so the card stays narrow."""
        s = self.bt.pins[pin]
        if s.kind is not SignalKind.LINE:
            return f"pin {pin} · {s}\n{s.about}"
        a = self.board.lines.get(s.line)
        if a is None:
            return f"pin {pin} · {s}\nfree\ndouble-click to add"
        text = f"pin {pin} · {s}\n{a.name.strip() or '(no name)'}\n{self.board.direction(s.line).value}"
        if a.note.strip():
            text += f"\n{a.note.strip()}"
        return text

    def _box_area(self, pin: int) -> QRect:
        return self.pin_rect(pin).adjusted(-2, -2, 2, 2).toAlignedRect()

    def _show_card(self, pin: int) -> None:
        """Details beside the hovered pin, over its labels on the outer side,
        so the card never covers a pin box you might move to next."""
        card = self.card
        card.setText(self.tooltip_for(pin))
        card.setWordWrap(False)
        card.setMinimumSize(0, 0)
        card.setMaximumSize(16777215, 16777215)
        natural = card.sizeHint()
        r = self.pin_rect(pin)
        left = self.lay.left[pin]
        room = int(r.left() - 8 - 2) if left else int(self.width() - (r.right() + 8) - 2)
        w = min(natural.width(), max(room, 90))
        if w < natural.width():
            card.setWordWrap(True)
        h = card.heightForWidth(w) if card.wordWrap() else natural.height()
        card.setFixedSize(w, h)
        x = r.left() - 8 - w if left else r.right() + 8
        y = r.center().y() - h / 2
        card.move(int(max(2, min(x, self.width() - w - 2))), int(max(2, min(y, self.height() - h - 2))))
        card.show()
        card.raise_()

    def _set_hover(self, pin: int | None) -> None:
        if pin == self.hover_pin:
            return
        old, self.hover_pin = self.hover_pin, pin
        for changed in (old, pin):
            if changed:
                self.update(self._box_area(changed))
        self.setCursor(Qt.CursorShape.PointingHandCursor if pin else Qt.CursorShape.ArrowCursor)
        if pin and self.board:
            self._show_card(pin)
        else:
            self.card.hide()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self._set_hover(self.pin_at(e.position()))

    def leaveEvent(self, _e) -> None:
        self._set_hover(None)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        pin = self.pin_at(e.position())
        if pin is None or pin == self.focus_pin:
            self.cleared.emit()  # empty space, or the selected pin again: deselect
        else:
            self.pinClicked.emit(pin)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        pin = self.pin_at(e.position())
        if pin and e.button() == Qt.MouseButton.LeftButton:
            self.pinDoubleClicked.emit(pin)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deletePressed.emit()
        elif e.key() == Qt.Key.Key_Escape:
            self.escapePressed.emit()
        else:
            super().keyPressEvent(e)
