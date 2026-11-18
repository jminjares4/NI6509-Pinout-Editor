"""Pin maps for the supported NI boards. Pure data and arithmetic, no I/O.

Each ``BoardType`` knows its connector (which pin carries what), the channels
you can put a signal on, how those channels are written in the file, and
which direction each one has.

Sources (ni.com/docs):

* NI PCIe-6509: 100-pin connector, two blocks of 50. Pins 1-48 carry ports
  0-5 and pins 51-98 carry ports 6-11; odd pins carry P2, P1, P0 (P8, P7, P6)
  top to bottom, even pins P5, P4, P3 (P11, P10, P9); within a port, line 7
  sits on the lowest pin. Pins 49/99 are +5 V, 50/100 are GND.
* NI 9205 with DSUB, NI 9264 with DSUB, NI 9476 with DSUB: 37-pin DSUB.
* NI 9401: 25-pin DSUB.

The C Series modules are mapped for their DSUB connectors only. The
spring-terminal versions number their terminals differently and are not
covered here.

To add a board: write its pin table below and add it to ``BOARD_TYPES``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


class SignalKind(Enum):
    LINE = "line"    # a channel you can put a named signal on
    POWER = "power"  # +5 V, Vsup
    GND = "gnd"      # GND, COM
    NC = "nc"        # no connection
    AUX = "aux"      # used by the board itself (AISENSE, PFI0...), not mapped here


@dataclass(frozen=True, order=True)
class Line:
    """One channel of a board, e.g. P3.4 on a 6509 or AI5 on a 9205.

    ``frozen`` makes it hashable (usable as a dict key); ``order`` sorts by
    ``index``, so a board's signals save in P0.0, P0.1, ... / AI0, AI1, ...
    order. Get lines from a ``BoardType`` (``parse``, ``signal_at``) instead
    of building them by hand.
    """

    index: int
    text: str

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class Signal:
    """What one connector pin carries."""

    kind: SignalKind
    label: str
    line: Line | None = None
    about: str = ""  # one short phrase for pins that can't carry a signal

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class Block:
    """One drawn group of pins: a left column and a right column."""

    caption: str
    left: tuple[int, ...]
    right: tuple[int, ...]
    stagger: bool = False  # DSUB: the shorter row sits half a step lower


_ABOUT = {
    "COM": "common reference (isolated ground)",
    "GND": "ground",
    "+5 V": "+5 V supply",
    "Vsup": "external supply input",
    "NC": "no connection",
}
_KIND = {"COM": SignalKind.GND, "GND": SignalKind.GND, "+5 V": SignalKind.POWER,
         "Vsup": SignalKind.POWER, "NC": SignalKind.NC}


class BoardType:
    """A board model whose channels are a prefix and a number (AI0, DO17).

    ``labels`` lists what each pin carries, pin 1 first. Every label of the
    form ``<prefix><n>`` becomes a channel; ``COM``, ``Vsup``, ``NC`` and the
    names in ``aux`` are pins that can't carry a signal.
    """

    #: True when one board has both fixed inputs and fixed outputs.
    mixed = False

    def __init__(self, key: str, title: str, connector: str, blocks: tuple[Block, ...],
                 labels: list[str], prefix: str, *, direction: Direction | None,
                 why: str = "", group_size: int = 0, aux: dict[str, str] | None = None) -> None:
        self.key = key              # written as "type" in the file, e.g. "NI 9205"
        self.short = key.split()[-1]
        self.title = title
        self.connector = connector
        self.blocks = blocks
        self.prefix = prefix
        self._direction = direction  # None: set by the user (NI 9401)
        self._why = why
        self._group_size = group_size
        self.pins: dict[int, Signal] = {}
        chan = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        for pin, label in enumerate(labels, start=1):
            m = chan.match(label)
            if m:
                self.pins[pin] = Signal(SignalKind.LINE, label, Line(int(m.group(1)), label))
            elif aux and label in aux:
                self.pins[pin] = Signal(SignalKind.AUX, label, about=aux[label])
            else:
                self.pins[pin] = Signal(_KIND[label], label, about=_ABOUT[label])
        self._finish()

    def _finish(self) -> None:
        self.pin_count = len(self.pins)
        self._pin_of = {s.line: pin for pin, s in self.pins.items() if s.line}
        self.lines = sorted(self._pin_of)
        self._by_text = {str(line).upper(): line for line in self.lines}
        drawn = sorted(p for b in self.blocks for p in b.left + b.right)
        assert drawn == list(range(1, self.pin_count + 1)), f"{self.key}: blocks don't cover the pins"

    # ------------------------------------------------------------ lookups

    def signal_at(self, pin: int) -> Signal | None:
        """What a connector pin carries; ``None`` if there is no such pin."""
        return self.pins.get(pin)

    def pin_of(self, line: Line) -> int:
        return self._pin_of[line]

    @property
    def example(self) -> str:
        return str(self.lines[min(5, len(self.lines) - 1)])

    @property
    def expected(self) -> str:
        """The channels in words: ``AI0-AI31``."""
        return f"{self.lines[0]}-{self.lines[-1]}"

    @property
    def line_pattern(self) -> str:
        """Regular expression for a channel name (used by the JSON Schema)."""
        return "^(" + "|".join(re.escape(str(line)) for line in self.lines) + ")$"

    def parse(self, text: str) -> Line:
        """``AI5``, ``ai5`` or ``AI 5``. Raises ``ValueError`` saying why not."""
        line = self._by_text.get(re.sub(r"\s+", "", str(text)).upper())
        if line is not None:
            return line
        # A near miss ("A5", "5", "AI07", "AI32"): say what was probably meant.
        m = re.fullmatch(r"\s*([A-Za-z]*)[\s._-]*(\d+)\s*", str(text))
        if m and self.prefix.startswith(m.group(1).upper()):
            m = re.search(r"(\d+)\s*$", str(text))
            guess = self._by_text.get(f"{self.prefix}{int(m.group(1))}")
            if guess is None:
                raise ValueError(f'"{text}": there is no {self.prefix}{int(m.group(1))} on an '
                                 f"{self.key} (channels are {self.expected})")
            raise ValueError(f'"{text}" is not an {self.key} channel; did you mean "{guess}"?')
        raise ValueError(f'"{text}" is not an {self.key} channel '
                         f'(expected {self.expected}, e.g. "{self.example}")')

    # ------------------------------------------------------------ direction

    def fixed_direction(self, line: Line) -> Direction | None:
        """The direction the board gives this channel, or ``None`` when the
        user chooses it."""
        return self._direction

    def why_direction(self, line: Line) -> str:
        """Why a fixed channel has the direction it has, in a few words."""
        return self._why

    def group(self, line: Line) -> str | None:
        """Channels that must share one direction (``DIO0-DIO3``), or
        ``None`` when the direction is fixed."""
        if self._direction is not None or not self._group_size:
            return None
        first = line.index // self._group_size * self._group_size
        return f"{self.prefix}{first}-{self.prefix}{first + self._group_size - 1}"

    def default_name(self, board_index: int, line: Line) -> str:
        """``B2_P0_7``, ``B3_AI5``."""
        return f"B{board_index + 1}_" + re.sub(r"\W", "_", str(line))


# ---------------------------------------------------------------- NI 6509

_LINE_RE = re.compile(r"^\s*[Pp]?(\d+)\.(\d+)\s*$")
_LOOSE_LINE = re.compile(r"^\s*[Pp]?\s*(\d+)\s*([.,\-_/: ])\s*(\d+)\s*$")


class Ni6509(BoardType):
    """NI PCIe-6509: 96 DIO lines as 12 ports of 8, written ``P<port>.<line>``.

    Direction is fixed by the port, the way these boards are wired here:
    P0-P5 (pins 1-48) are outputs, P6-P11 (pins 51-98) are inputs.
    """

    mixed = True
    PORTS = 12
    LINES_PER_PORT = 8
    OUTPUT_PORTS = range(0, 6)

    def __init__(self) -> None:
        self.key, self.short = "NI 6509", "6509"
        self.title, self.connector, self.prefix = "PCIe-6509", "100-pin SCSI", "P"
        self.blocks = (
            Block("pins 1–50  ·  outputs", tuple(range(1, 50, 2)), tuple(range(2, 51, 2))),
            Block("pins 51–100  ·  inputs", tuple(range(51, 100, 2)), tuple(range(52, 101, 2))),
        )
        self.pins = {}
        for pin in range(1, 101):
            if pin in (49, 99):
                self.pins[pin] = Signal(SignalKind.POWER, "+5 V", about=_ABOUT["+5 V"])
            elif pin in (50, 100):
                self.pins[pin] = Signal(SignalKind.GND, "GND", about=_ABOUT["GND"])
            else:
                idx, offset = (pin - 1, 0) if pin <= 48 else (pin - 51, 6)
                row = idx // 2
                port = (2 - row // 8 if idx % 2 == 0 else 5 - row // 8) + offset
                line = 7 - row % 8
                self.pins[pin] = Signal(SignalKind.LINE, f"P{port}.{line}",
                                        Line(port * 8 + line, f"P{port}.{line}"))
        self._finish()

    example = "P3.4"
    expected = "P<0-11>.<0-7>"
    line_pattern = r"^[Pp]?([0-9]|1[01])\.[0-7]$"

    def parse(self, text: str) -> Line:
        """Accepts ``P3.4``, ``p3.4`` or ``3.4``."""
        m = _LINE_RE.match(str(text))
        if m and int(m.group(1)) < self.PORTS and int(m.group(2)) < self.LINES_PER_PORT:
            return self._by_text[f"P{int(m.group(1))}.{int(m.group(2))}"]
        m = _LOOSE_LINE.match(str(text))
        if m:
            port, ln = int(m.group(1)), int(m.group(3))
            if port >= self.PORTS:
                raise ValueError(f'"{text}": there is no port {port} (ports are P0-P11)')
            if ln >= self.LINES_PER_PORT:
                raise ValueError(f'"{text}": there is no line {ln} (lines are .0-.7)')
            raise ValueError(f'"{text}" is not a 6509 line; did you mean "P{port}.{ln}"?')
        raise ValueError(f'"{text}" is not a 6509 line (expected P<0-11>.<0-7>, e.g. "P3.4")')

    def fixed_direction(self, line: Line) -> Direction:
        port = line.index // self.LINES_PER_PORT
        return Direction.OUTPUT if port in self.OUTPUT_PORTS else Direction.INPUT

    def why_direction(self, line: Line) -> str:
        return ("pins 1-50 (P0-P5) are outputs" if self.fixed_direction(line) is Direction.OUTPUT
                else "pins 51-100 (P6-P11) are inputs")

    def group(self, line: Line) -> None:
        return None


# ---------------------------------------------------------------- C Series (DSUB)


def _run(prefix: str, first: int, count: int) -> list[str]:
    return [f"{prefix}{n}" for n in range(first, first + count)]


def _dsub(pins: int, what: str) -> tuple[Block, ...]:
    """A DSUB drawn the way the NI pinout figures show it: pins 1..n in the
    left column, the rest in the right column."""
    half = (pins + 1) // 2
    return (Block(f"{pins}-pin DSUB  ·  {what}", tuple(range(1, half + 1)),
                  tuple(range(half + 1, pins + 1)), stagger=True),)


NI_9205 = BoardType(
    "NI 9205", "NI 9205 with DSUB", "37-pin DSUB", _dsub(37, "analog inputs"),
    # pins 1-19
    _run("AI", 0, 8) + ["DO0", "COM"] + _run("AI", 16, 8) + ["AISENSE"]
    # pins 20-37
    + _run("AI", 8, 8) + ["PFI0", "COM"] + _run("AI", 24, 8),
    "AI", direction=Direction.INPUT, why="every NI 9205 channel is an analog input",
    aux={"DO0": "the module's digital output; not mapped by this editor",
         "PFI0": "trigger / digital input; not mapped by this editor",
         "AISENSE": "reference for NRSE measurements"},
)

NI_9264 = BoardType(
    "NI 9264", "NI 9264 with DSUB", "37-pin DSUB", _dsub(37, "analog outputs"),
    _run("AO", 0, 8) + ["NC", "NC"] + _run("AO", 8, 8) + ["COM"]   # pins 1-19
    + ["COM"] * 18,                                                # pins 20-37
    "AO", direction=Direction.OUTPUT, why="every NI 9264 channel is an analog output",
)

NI_9401 = BoardType(
    "NI 9401", "NI 9401", "25-pin DSUB", _dsub(25, "DIO0–3 and DIO4–7 each share a direction"),
    ["COM", "NC", "COM", "COM", "NC", "COM", "COM", "NC", "COM", "COM", "NC", "COM", "COM"]  # 1-13
    + ["DIO0", "NC", "DIO1", "DIO2", "NC", "DIO3", "DIO4", "NC", "DIO5", "DIO6", "NC", "DIO7"],  # 14-25
    "DIO", direction=None, group_size=4,
)

NI_9476 = BoardType(
    "NI 9476", "NI 9476 with DSUB", "37-pin DSUB", _dsub(37, "digital outputs (sourcing)"),
    _run("DO", 0, 8) + ["Vsup", "Vsup"] + _run("DO", 8, 8) + ["COM"]   # pins 1-19
    + _run("DO", 16, 8) + ["Vsup", "Vsup"] + _run("DO", 24, 8),        # pins 20-37
    "DO", direction=Direction.OUTPUT, why="every NI 9476 channel is a digital output",
)

NI_6509 = Ni6509()

#: Every board the editor knows, in the order the menus show them.
BOARD_TYPES: dict[str, BoardType] = {
    t.key: t for t in (NI_6509, NI_9205, NI_9264, NI_9401, NI_9476)
}
#: What a board is when the file doesn't say (files from before "type" existed).
DEFAULT_TYPE = NI_6509.key


def board_type(key: str) -> BoardType:
    return BOARD_TYPES[key]


def find_type(text: str) -> BoardType | None:
    """Match a board type loosely by its model number: ``NI 9205``,
    ``ni-9205``, ``NI9205`` and ``9205`` are all the NI 9205."""
    digits = re.findall(r"\d{4}", str(text))
    if len(digits) != 1:
        return None
    return next((t for t in BOARD_TYPES.values() if t.short == digits[0]), None)
