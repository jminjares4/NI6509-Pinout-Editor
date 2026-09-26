"""The 100-pin connector drawing, painted with QPainter.

Same two-block layout as the pinout figure in the NI user guide. The widget
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

from model import Board, Direction, Issue, Severity, direction_of, worst_on
from pinout import PIN_COUNT, SignalKind, signal_at
import theme as t

ROW_H = 19
PIN_W, PIN_H = 36, 16
LABEL_W = 140
SPINE = 6
BLOCK_GAP = 40
HEADER_H = 20
ROWS = PIN_COUNT // 4
BLOCK_W = LABEL_W + 6 + PIN_W + SPINE + PIN_W + 6 + LABEL_W
MARGIN = 8
SIDE = 36  # extra room left and right so the hover card fits beside the outer columns


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
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.mono.setPointSizeF(9)
        self.mono_bold = QFont(self.mono)
        self.mono_bold.setBold(True)
        self.setMinimumSize(self.sizeHint())
        # Hover details: a plain child label, so showing and moving it is
        # instant (no window to create, no fade-in like QToolTip on macOS).
        self.card = QLabel(self)
        self.card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.card.setStyleSheet(
            f"QLabel {{ background: {t.PANEL}; color: {t.TEXT}; border: 1px solid {t.LINE};"
            " border-radius: 6px; padding: 6px 9px; }")
        self.card.hide()

    def sizeHint(self) -> QSize:
        return QSize(2 * BLOCK_W + BLOCK_GAP + 2 * (MARGIN + SIDE), HEADER_H + ROWS * ROW_H + 2 * MARGIN)

    def show_board(self, board: Board, index: int, issues: list[Issue], focus_pin: int | None) -> None:
        self.board, self.board_index, self.issues, self.focus_pin = board, index, issues, focus_pin
        if self.card.isVisible() and self.hover_pin:
            self._show_card(self.hover_pin)
        self.update()

    # ------------------------------------------------------------ geometry

    def pin_rect(self, pin: int) -> QRectF:
        """Screen rectangle of a pin box."""
        block = 0 if pin <= 50 else 1
        idx = pin - 1 - 50 * block
        row, odd = idx // 2, idx % 2 == 0
        x0 = MARGIN + SIDE + block * (BLOCK_W + BLOCK_GAP)
        odd_x = x0 + LABEL_W + 6
        x = odd_x if odd else odd_x + PIN_W + SPINE
        y = MARGIN + HEADER_H + row * ROW_H + (ROW_H - PIN_H) / 2
        return QRectF(x, y, PIN_W, PIN_H)

    def pin_at(self, pos: QPoint | QPointF) -> int | None:
        """The pin under ``pos``, by arithmetic (this runs on every mouse move)."""
        x, y = pos.x(), pos.y()
        row = int((y - MARGIN - HEADER_H) // ROW_H)
        if not 0 <= row < ROWS:
            return None
        for block in (0, 1):
            odd_x = MARGIN + SIDE + block * (BLOCK_W + BLOCK_GAP) + LABEL_W + 6
            for even, left in ((0, odd_x), (1, odd_x + PIN_W + SPINE)):
                pin = 50 * block + 2 * row + 1 + even
                if left <= x < left + PIN_W and self.pin_rect(pin).contains(QPointF(x, y)):
                    return pin
        return None

    def _row_rect(self, pin: int) -> QRectF:
        """Everything drawn for one pin: its box plus the labels beside it."""
        r = self.pin_rect(pin)
        if pin % 2 == 1:  # left column: labels to the left
            return QRectF(r.left() - 6 - LABEL_W, r.top() - 3, LABEL_W + 6 + r.width() + 2, r.height() + 6)
        return QRectF(r.left() - 2, r.top() - 3, r.width() + 2 + 56 + LABEL_W, r.height() + 6)

    # ------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:
        if self.board is None:
            return
        dirty = QRectF(event.rect())  # hover repaints only a small area: skip the rest
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for block in range(2):
            x0 = MARGIN + SIDE + block * (BLOCK_W + BLOCK_GAP)
            p.setPen(t.qc(t.DIM))
            p.setFont(self.font())
            p.drawText(QRectF(x0, MARGIN, BLOCK_W, 16), Qt.AlignmentFlag.AlignCenter,
                       f"pins {50 * block + 1}–{50 * block + 50}  ·  {'outputs' if block == 0 else 'inputs'}")
            first, last = self.pin_rect(50 * block + 1), self.pin_rect(50 * block + 50)
            housing = QRectF(first.left() - 5, first.top() - 5,
                             last.right() - first.left() + 10, last.bottom() - first.top() + 10)
            p.setPen(QPen(t.qc("#464954"), 1))
            p.setBrush(t.qc(t.HOUSING))
            p.drawRoundedRect(housing, 8, 8)
            for pin in range(50 * block + 1, 50 * block + 51):
                if self._row_rect(pin).intersects(dirty):
                    self._paint_pin(p, pin)
        p.end()

    def _paint_pin(self, p: QPainter, pin: int) -> None:
        board = self.board
        s = signal_at(pin)
        line = s.line if s.kind is SignalKind.DIO else None
        a = board.lines.get(line) if line else None
        sev = worst_on(self.issues, self.board_index, line) if line else None
        rect = self.pin_rect(pin)

        if a:
            fill, text = (t.OUTPUT, t.TEXT) if direction_of(line) is Direction.OUTPUT else (t.INPUT, t.TEXT)
        elif s.kind is SignalKind.VCC:
            fill, text = t.VCC, t.VCC_TEXT
        elif s.kind is SignalKind.GND:
            fill, text = t.GND, t.DIM
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
            pen = QPen(t.qc("#50535c"), 1)
        p.setPen(pen)
        p.setBrush(t.qc(fill))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.setPen(t.qc(text))
        p.setFont(self.mono)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(pin))

        # Line name (P2.3) beside the box, signal name beyond it.
        left = (pin % 2 == 1)
        cy = rect.center().y()
        sig_rect = (QRectF(rect.left() - 52, cy - 9, 46, 18) if left
                    else QRectF(rect.right() + 6, cy - 9, 46, 18))
        align = (Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
        p.setPen(t.qc(t.TEXT if a else t.DIM))
        p.setFont(self.mono_bold if a else self.mono)
        p.drawText(sig_rect, align, str(s))
        if a:
            name_rect = (QRectF(rect.left() - 6 - LABEL_W, cy - 9, LABEL_W - 50, 18) if left
                         else QRectF(rect.right() + 56, cy - 9, LABEL_W - 50, 18))
            color = (t.ERROR if sev is Severity.ERROR else t.WARN if sev is Severity.WARNING
                     else t.OUTPUT_TEXT if direction_of(line) is Direction.OUTPUT else t.INPUT_TEXT)
            p.setPen(t.qc(color))
            p.setFont(self.mono)
            name = a.name.strip() or "(no name)"
            elided = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(name_rect.width()))
            p.drawText(name_rect, align, elided)

    # ------------------------------------------------------------ input

    def tooltip_for(self, pin: int) -> str:
        """Hover details, one short fact per line so the card stays narrow."""
        s = signal_at(pin)
        if s.kind is not SignalKind.DIO:
            return f"pin {pin} · {s}\nsupply pin"
        a = self.board.lines.get(s.line)
        if a is None:
            return f"pin {pin} · {s}\nfree\ndouble-click to add"
        text = f"pin {pin} · {s}\n{a.name.strip() or '(no name)'}\n{direction_of(s.line).value}"
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
        left = pin % 2 == 1
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