"""The connector drawing, painted with QPainter.

Draws whichever board it is shown: the 100-pin connector of the 6509 as two
blocks of 50 (the layout of the pinout figure in the NI user guide), a DSUB
as one block with pins 1..n down the left and the rest down the right. The
layout comes from the board type's ``blocks`` (see ``pinout.py``). The widget
only draws and reports clicks; it never changes the project itself.

Hovering repaints only the pin boxes involved.
"""

from __future__ import annotations

import functools

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from model import Board, Direction, Issue, Severity, worst_on
from pinout import DEFAULT_TYPE, BoardType, SignalKind, board_type
import theme as t

ROW_H = 19
PIN_W, PIN_H = 36, 16
LABEL_W = 150  # channel name plus signal name, beside each pin
SIG_W = 54     # of which the channel name ("P11.7", "AISENSE")
SPINE = 6
BLOCK_GAP = 16
HEADER_H = 20
MARGIN = 8
SIDE = 44  # extra room left and right, on boards that label their direction groups there


class _Layout:
    """Where everything goes for one board type, worked out once."""

    def __init__(self, bt: BoardType) -> None:
        self.rects: dict[int, QRectF] = {}
        self.left: dict[int, bool] = {}  # pin -> its labels are on its left
        self.blocks: list[tuple[float, str, QRectF, tuple[int, ...]]] = []
        rows = 0
        top = MARGIN + HEADER_H
        side = SIDE if any(bt.group(line) for line in bt.lines) else 0
        #: Room beside each pin: its channel and its signal's name, or the channel alone.
        self.label_w = label_w = LABEL_W if bt.names_beside_pins else SIG_W + 56
        self.block_w = block_w = 2 * (label_w + 6 + PIN_W) + SPINE
        for n, block in enumerate(bt.blocks):
            x0 = MARGIN + side + n * (block_w + BLOCK_GAP)
            left_x = x0 + label_w + 6
            longest = max(len(block.left), len(block.right))
            rows = max(rows, longest)
            for pins, x, is_left in ((block.left, left_x, True),
                                     (block.right, left_x + PIN_W + SPINE, False)):
                # The shorter row of a DSUB sits half a step lower, like the real thing.
                y0 = top + (ROW_H / 2 if block.stagger and len(pins) < longest else 0)
                for row, pin in enumerate(pins):
                    self.rects[pin] = QRectF(x, y0 + row * ROW_H + (ROW_H - PIN_H) / 2, PIN_W, PIN_H)
                    self.left[pin] = is_left
            housing = QRectF(left_x - 5, top + (ROW_H - PIN_H) / 2 - 5,
                             2 * PIN_W + SPINE + 10, (longest - 1) * ROW_H + PIN_H + 10)
            self.blocks.append((x0, block.caption, housing, block.left + block.right))
        self.size = QSize(len(bt.blocks) * block_w + (len(bt.blocks) - 1) * BLOCK_GAP
                          + 2 * (MARGIN + side), HEADER_H + rows * ROW_H + 2 * MARGIN)


@functools.cache
def layout_of(bt: BoardType) -> _Layout:
    return _Layout(bt)


class ConnectorWidget(QWidget):
    pinClicked = pyqtSignal(int)
    deletePressed = pyqtSignal()
    escapePressed = pyqtSignal()
    #: Up or Down: step to the previous (-1) or next (+1) channel.
    stepPressed = pyqtSignal(int)
    #: Click on the selected pin again, or on empty space: clear the selection.
    cleared = pyqtSignal()
    #: A click anywhere on a read-only drawing (see ``readonly``).
    pressed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.board: Board | None = None
        self.board_index = 0
        self.issues: list[Issue] = []
        self.focus_pin: int | None = None
        self.hover_pin: int | None = None
        #: A drawing that only shows (a serial port's connector): no hover,
        #: no pin selection, any click just reports ``pressed``.
        self.readonly = False
        self.bt = board_type(DEFAULT_TYPE)
        self.lay = layout_of(self.bt)
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.mono.setPointSizeF(9)
        self.mono_bold = QFont(self.mono)
        self.mono_bold.setBold(True)
        self.setFixedSize(self.lay.size)
    def sizeHint(self) -> QSize:
        return self.lay.size

    def show_board(self, board: Board, index: int, issues: list[Issue], focus_pin: int | None) -> None:
        self.board, self.board_index, self.issues, self.focus_pin = board, index, issues, focus_pin
        if board.bt is not self.bt:
            # A different connector: new layout, new size, nothing hovered.
            self.bt = board.bt
            self.lay = layout_of(self.bt)
            self.hover_pin = None
            self.setFixedSize(self.lay.size)
        self.update()

    # ------------------------------------------------------------ geometry

    def pin_rect(self, pin: int) -> QRectF:
        """Screen rectangle of a pin box."""
        return self.lay.rects[pin]

    def pin_at(self, pos: QPoint | QPointF) -> int | None:
        """The pin under ``pos``."""
        return next((pin for pin, r in self.lay.rects.items() if r.contains(QPointF(pos))), None)

    def _row_rect(self, pin: int) -> QRectF:
        """Everything drawn for one pin: its box plus the labels beside it."""
        r = self.pin_rect(pin)
        if self.lay.left[pin]:
            return QRectF(r.left() - 6 - self.lay.label_w, r.top() - 3, self.lay.label_w + 6 + r.width() + 2, r.height() + 6)
        return QRectF(r.left() - 2, r.top() - 3, r.width() + 8 + self.lay.label_w, r.height() + 6)

    # ------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:
        if self.board is None:
            return
        dirty = QRectF(event.rect())  # hover repaints only a small area: skip the rest
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for x0, caption, housing, pins in self.lay.blocks:
            p.setPen(QColor(t.DIM))
            p.setFont(self.font())
            p.drawText(QRectF(x0, MARGIN, self.lay.block_w, 16), Qt.AlignmentFlag.AlignCenter, caption)
            p.setPen(QPen(QColor(t.HOUSING_EDGE), 1))
            p.setBrush(QColor(t.HOUSING))
            p.drawRoundedRect(housing, 8, 8)
            self._paint_groups(p, pins)
            for pin in pins:
                if self._row_rect(pin).intersects(dirty):
                    self._paint_pin(p, pin)
        p.end()

    def _paint_groups(self, p: QPainter, pins: tuple[int, ...]) -> None:
        """Channels that share one direction (NI 9401: DIO0-3, DIO4-7) get a
        tinted band in that direction's color, labelled IN or OUT."""
        groups: dict[str, list[int]] = {}
        for pin in pins:
            line = self.bt.pins[pin].line
            if line and self.bt.group(line):
                groups.setdefault(self.bt.group(line), []).append(pin)
        for members in groups.values():
            output = self.board.direction(self.bt.pins[members[0]].line) is Direction.OUTPUT
            first, last = self.pin_rect(members[0]), self.pin_rect(members[-1])
            band = QRectF(first.left() - 3, first.top() - 0.5, PIN_W + 12 + LABEL_W, last.bottom() - first.top() + 1)
            color = QColor(t.OUTPUT if output else t.INPUT)
            p.setPen(QPen(color, 1))
            color.setAlpha(34)
            p.setBrush(color)
            p.drawRoundedRect(band, 6, 6)
            p.setPen(QColor(t.OUTPUT_TEXT if output else t.INPUT_TEXT))
            p.setFont(self.mono_bold)
            p.drawText(QRectF(band.right() + 6, band.top(), SIDE, band.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "OUT" if output else "IN")

    def _paint_pin(self, p: QPainter, pin: int) -> None:
        board = self.board
        s = self.bt.pins[pin]
        line = s.line
        a = board.lines.get(line) if line else None
        sev = worst_on(self.issues, self.board_index, line) if line else None
        rect = self.pin_rect(pin)
        output = bool(a) and board.direction(line) is Direction.OUTPUT

        if a:
            fill, text = (t.OUTPUT if output else t.INPUT), t.ON_PIN
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
            pen = QPen(QColor("#ffffff"), 2)
        elif sev is Severity.ERROR:
            pen = QPen(QColor(t.ERROR), 2)
        elif sev is Severity.WARNING:
            pen = QPen(QColor(t.WARN), 1.5)
        elif pin == self.hover_pin:
            pen = QPen(QColor(t.ACCENT), 1.5)
        else:
            pen = QPen(QColor(t.PIN_EDGE), 1)
        p.setPen(pen)
        p.setBrush(QColor(fill))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        p.setPen(QColor(text))
        p.setFont(self.mono)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.bt.pin_text(pin))
        if pin == self.focus_pin:  # the selected pin: a second ring in the accent color
            p.setPen(QPen(QColor(t.ACCENT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2.5, -2.5, 2.5, 2.5), 6, 6)

        # Channel name (P2.3, AI5) beside the box, signal name beyond it.
        left = self.lay.left[pin]
        cy = rect.center().y()
        sig_rect = (QRectF(rect.left() - 6 - SIG_W, cy - 9, SIG_W, 18) if left
                    else QRectF(rect.right() + 6, cy - 9, SIG_W, 18))
        align = (Qt.AlignmentFlag.AlignRight if left else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
        p.setPen(QColor(t.TEXT if a else t.NC_TEXT if s.kind is SignalKind.NC else t.DIM))
        p.setFont(self.mono_bold if a else self.mono)
        p.drawText(sig_rect, align, str(s))
        if a and not self.readonly:  # a read-only drawing shows which pins, by color, not names
            name_w = LABEL_W - SIG_W - 4
            name_rect = (QRectF(rect.left() - 6 - LABEL_W, cy - 9, name_w, 18) if left
                         else QRectF(rect.right() + 6 + SIG_W + 4, cy - 9, name_w, 18))
            color = (t.ERROR if sev is Severity.ERROR else t.WARN if sev is Severity.WARNING
                     else t.OUTPUT_TEXT if output else t.INPUT_TEXT)
            p.setPen(QColor(color))
            p.setFont(self.mono)
            name = a.name.strip() or "(no name)"
            elided = p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(name_rect.width()))
            p.drawText(name_rect, align, elided)

    # ------------------------------------------------------------ input

    def _box_area(self, pin: int) -> QRect:
        return self.pin_rect(pin).adjusted(-5, -5, 5, 5).toAlignedRect()

    def _set_hover(self, pin: int | None) -> None:
        if pin == self.hover_pin:
            return
        old, self.hover_pin = self.hover_pin, pin
        for changed in (old, pin):
            if changed:
                self.update(self._box_area(changed))
        self.setCursor(Qt.CursorShape.PointingHandCursor if pin else Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self.readonly:
            self._set_hover(self.pin_at(e.position()))

    def leaveEvent(self, _e) -> None:
        self._set_hover(None)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.readonly:
            self.pressed.emit()
            return
        self.setFocus()
        pin = self.pin_at(e.position())
        if pin is None or pin == self.focus_pin:
            self.cleared.emit()  # empty space, or the selected pin again: deselect
        else:
            self.pinClicked.emit(pin)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deletePressed.emit()
        elif e.key() == Qt.Key.Key_Escape:
            self.escapePressed.emit()
        elif e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.stepPressed.emit(1 if e.key() == Qt.Key.Key_Down else -1)
        else:
            super().keyPressEvent(e)
