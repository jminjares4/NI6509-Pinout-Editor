"""Pin maps for the supported NI boards. Pure data and arithmetic, no I/O.

Each ``BoardType`` knows its connector (which pin carries what), the channels
you can put a signal on, how those channels are written in the file, and
which direction each one has.

Boards belong to one of two areas: HWIL (NI 6509, MicroGate serial card,
Concurrent analog output card) or Ground Software (the cDAQ modules).

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

The MicroGate SyncLink GT4e and the Concurrent analog output card are set up
differently from a pin map: a serial port has a name and a setup, an analog
channel has a name and a range. Their real connector pinouts are NOT in here
yet (see ``SERIAL_PINS`` and ``AnalogOut``).

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

    #: Which area of the editor the board lives in, and what its button says.
    domain = "Ground Software"
    kind = ""
    #: An extra setting each signal carries, chosen from a list ("range" on
    #: the analog card, "setup" on a serial port). Empty: no such setting.
    option_key = ""
    option_label = ""
    options: tuple[str, ...] = ()
    #: False where input/output means nothing (a serial port is both).
    writes_direction = True
    pin_word = "pin"
    #: False for a drawing that shows which pins are used but not what they are called.
    names_beside_pins = True

    def __init__(self, key: str, title: str, connector: str, blocks: tuple[Block, ...],
                 labels: list[str], prefix: str, *, direction: Direction | None,
                 group_size: int = 0, aux: dict[str, str] | None = None) -> None:
        self.key = key              # written as "type" in the file, e.g. "NI 9205"
        self.kind = self.kind or key
        self.title = title
        self.connector = connector
        self.blocks = blocks
        self.prefix = prefix
        self._direction = direction  # None: set by the user (NI 9401)
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

    def pin_text(self, pin: int) -> str:
        """What is written in a pin's box."""
        return str(pin)

    def describe(self, line: Line) -> str:
        """A channel in words, for the editing box: ``Pin 15  ·  P2.0``."""
        return f"Pin {self.pin_of(line)}  ·  {line}"

    @property
    def example(self) -> str:
        return str(self.lines[min(5, len(self.lines) - 1)])

    @property
    def expected(self) -> str:
        """The channels in words: ``AI0-AI31``."""
        return f"{self.lines[0]}-{self.lines[-1]}"

    def parse(self, text: str) -> Line:
        """``AI5``, ``ai5`` or ``AI 5``. Raises ``ValueError`` saying why not."""
        line = self._by_text.get(re.sub(r"\s+", "", str(text)).upper())
        if line is None:
            raise ValueError(f'"{text}" is not an {self.key} channel (expected {self.expected}, '
                             f'e.g. "{self.example}")')
        return line

    # ------------------------------------------------------------ direction

    def fixed_direction(self, line: Line) -> Direction | None:
        """The direction the board gives this channel, or ``None`` when the
        user chooses it."""
        return self._direction

    def group(self, line: Line) -> str | None:
        """Channels that must share one direction (``DIO0-DIO3``), or
        ``None`` when the direction is fixed."""
        if self._direction is not None or not self._group_size:
            return None
        first = line.index // self._group_size * self._group_size
        return f"{self.prefix}{first}-{self.prefix}{first + self._group_size - 1}"


# ---------------------------------------------------------------- NI 6509

class Ni6509(BoardType):
    """NI PCIe-6509: 96 DIO lines as 12 ports of 8, written ``P<port>.<line>``.

    Direction is fixed by the port, the way these boards are wired here:
    P0-P5 (pins 1-48) are outputs, P6-P11 (pins 51-98) are inputs.
    """

    LINES_PER_PORT = 8
    OUTPUT_PORTS = range(0, 6)

    def __init__(self) -> None:
        self.key = "NI 6509"
        self.kind, self.domain = "NI 6509", "HWIL"
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

    def fixed_direction(self, line: Line) -> Direction:
        port = line.index // self.LINES_PER_PORT
        return Direction.OUTPUT if port in self.OUTPUT_PORTS else Direction.INPUT

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
    "AI", direction=Direction.INPUT,
    aux={"DO0": "the module's digital output; not mapped by this editor",
         "PFI0": "trigger / digital input; not mapped by this editor",
         "AISENSE": "reference for NRSE measurements"},
)

NI_9264 = BoardType(
    "NI 9264", "NI 9264 with DSUB", "37-pin DSUB", _dsub(37, "analog outputs"),
    _run("AO", 0, 8) + ["NC", "NC"] + _run("AO", 8, 8) + ["COM"]   # pins 1-19
    + ["COM"] * 18,                                                # pins 20-37
    "AO", direction=Direction.OUTPUT,
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
    "DO", direction=Direction.OUTPUT,
)

NI_6509 = Ni6509()


# ---------------------------------------------------------------- MicroGate serial card

#: The ways a serial port can be set up, and which signals each one uses.
#: PLACEHOLDERS: replace with the setups your stands really use.
SERIAL_SETUPS: dict[str, tuple[str, ...]] = {
    "Async 8N1, external clock": ("TxD", "RxD", "TxC", "RxC"),
    "Async 8N1, 115200 bps": ("TxD", "RxD"),
    "HDLC, 1 Mbps internal clock": ("TxD", "RxD", "AUXCLK", "RxC"),
    "HDLC, external clock": ("TxD", "RxD", "TxC", "RxC"),
}

#: One port's 25-pin connector, pin 1 first: (signal, direction or None).
#: PLACEHOLDER: this is the generic EIA-530 arrangement, NOT taken from the
#: MicroGate hardware manual. Check it before wiring from the drawing.
_OUT, _IN = Direction.OUTPUT, Direction.INPUT
SERIAL_PINS: list[tuple[str, Direction | None]] = [
    ("SHLD", None), ("TxD-", _OUT), ("RxD-", _IN), ("RTS-", _OUT), ("CTS-", _IN), ("DSR-", _IN), ("GND", None),
    ("DCD-", _IN), ("RxC+", _IN), ("DCD+", _IN), ("AUXCLK+", _OUT), ("TxC+", _IN), ("CTS+", _IN),      # 1-13
    ("TxD+", _OUT), ("TxC-", _IN), ("RxD+", _IN), ("RxC-", _IN), ("NC", None), ("RTS+", _OUT), ("DTR-", _OUT),
    ("NC", None), ("DSR+", _IN), ("DTR+", _OUT), ("AUXCLK-", _OUT), ("NC", None),                       # 14-25
]


class MicroGate(BoardType):
    """MicroGate SyncLink GT4e: four serial ports. A port in use has a name
    and a setup; its "channels" are the ports themselves (PORT0-PORT3)."""

    domain, kind = "HWIL", "MicroGate"
    option_key, option_label, options = "setup", "Setup", tuple(SERIAL_SETUPS)
    writes_direction, pin_word = False, "port"
    PORTS = 4

    def __init__(self) -> None:
        super().__init__("MicroGate GT4e", "MicroGate SyncLink GT4e", "4 serial ports",
                         (Block("ports", (1, 2), (3, 4)),), _run("PORT", 0, self.PORTS), "PORT",
                         direction=Direction.OUTPUT)

    def pin_text(self, pin: int) -> str:
        return str(pin - 1)

    def describe(self, line: Line) -> str:
        return f"Port {line.index}"


class SerialPortView(BoardType):
    """The 25-pin connector of a serial port, for drawing only: it shows
    which pins a port's setup uses. Not a board, and not in ``BOARD_TYPES``."""

    names_beside_pins = False

    def __init__(self) -> None:
        self.key, self.blocks = "serial port", _dsub(25, "EIA-530 layout, not verified")
        self._direction = Direction.OUTPUT  # no direction groups
        self._directions: dict[Line, Direction] = {}
        self.pins = {}
        for pin, (label, direction) in enumerate(SERIAL_PINS, start=1):
            if direction is None:
                self.pins[pin] = Signal(SignalKind.NC if label == "NC" else SignalKind.GND, label)
            else:
                self.pins[pin] = Signal(SignalKind.LINE, label, Line(pin, label))
                self._directions[self.pins[pin].line] = direction
        self._finish()

    def fixed_direction(self, line: Line) -> Direction:
        return self._directions[line]

    def used_by(self, setup: str) -> list[Line]:
        """The pins a setup uses (both wires of each signal)."""
        names = SERIAL_SETUPS.get(setup, ())
        return [s.line for s in self.pins.values() if s.line and s.label.rstrip("+-") in names]


SERIAL_VIEW = SerialPortView()


# ---------------------------------------------------------------- Concurrent analog output card


class AnalogOut(BoardType):
    """Concurrent CCURAOCC (CP-DA3218): 32 analog output channels, each with
    a name and an output range. Drawn as channels 0-31, NOT as connector
    pins: the card's connector pinout is not in here yet."""

    domain, kind = "HWIL", "Analog output"
    option_key, option_label = "range", "Range"
    #: Output ranges per Concurrent's CP-DA3218 data sheet.
    options = ("-10 to 10 V", "-5 to 5 V", "-2.5 to 2.5 V", "0 to 10 V", "0 to 5 V")
    pin_word = "channel"

    def __init__(self) -> None:
        super().__init__("CCURAOCC", "Concurrent CCURAOCC", "32 channels",
                         (Block("32 channels  ·  outputs", tuple(range(1, 17)), tuple(range(17, 33))),),
                         _run("CH", 0, 32), "CH", direction=Direction.OUTPUT)

    def pin_text(self, pin: int) -> str:
        return str(pin - 1)

    def describe(self, line: Line) -> str:
        return f"Channel {line.index}"


MICROGATE = MicroGate()
ANALOG_OUT = AnalogOut()

#: Every board the editor knows, in the order the buttons show them.
BOARD_TYPES: dict[str, BoardType] = {
    t.key: t for t in (NI_6509, MICROGATE, ANALOG_OUT, NI_9205, NI_9264, NI_9401, NI_9476)
}
#: The two areas of the editor, in order.
DOMAINS = ("HWIL", "Ground Software")
#: What a board is when the file doesn't say (files from before "type" existed).
DEFAULT_TYPE = NI_6509.key


def board_type(key: str) -> BoardType:
    return BOARD_TYPES[key]


def find_type(text: str) -> BoardType | None:
    """Match a board type loosely by its model number: ``NI 9205``,
    ``ni-9205``, ``NI9205`` and ``9205`` are all the NI 9205."""
    squash = re.sub(r"[^a-z0-9]", "", str(text).lower())
    for t in BOARD_TYPES.values():  # "microgate gt4e", "CCURAOCC", "ccuraocc"
        if squash == re.sub(r"[^a-z0-9]", "", t.key.lower()):
            return t
    digits = re.findall(r"\d{4}", str(text))
    if len(digits) != 1:
        return None
    return next((t for t in BOARD_TYPES.values() if t.key.endswith(digits[0])), None)
