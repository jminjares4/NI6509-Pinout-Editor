"""Pin map for the NI PCI/PXI/PCIe-6509 100-pin I/O connector.

Source: NI PCIe-6509 pinout, ni.com/docs. The connector is two blocks of 50:

* pins 1-48 carry ports 0-5, pins 51-98 carry ports 6-11
* odd pins carry P2, P1, P0 (P8, P7, P6) top to bottom,
  even pins carry P5, P4, P3 (P11, P10, P9)
* within a port, line 7 sits on the lowest pin and line 0 on the highest
* pins 49 and 99 are +5 V, pins 50 and 100 are GND

Pure arithmetic, no I/O. This mirrors ``pinout.rs`` in the Rust version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

PORTS = 12
LINES_PER_PORT = 8
PIN_COUNT = 100

_LINE_RE = re.compile(r"^\s*[Pp]?(\d+)\.(\d+)\s*$")


@dataclass(frozen=True, order=True)
class Line:
    """One digital I/O line, e.g. ``Line(3, 4)`` is P3.4.

    ``frozen`` makes it hashable (usable as a dict key); ``order`` sorts by
    port, then line, so dicts of lines serialize in P0.0, P0.1, ... order.
    """

    port: int
    line: int

    def __post_init__(self) -> None:
        if not (0 <= self.port < PORTS and 0 <= self.line < LINES_PER_PORT):
            raise ValueError(f"P{self.port}.{self.line} is not a 6509 line")

    @classmethod
    def parse(cls, text: str) -> Line:
        """Accepts ``P3.4``, ``p3.4`` or ``3.4``."""
        m = _LINE_RE.match(str(text))
        if m:
            port, line = int(m.group(1)), int(m.group(2))
            if port < PORTS and line < LINES_PER_PORT:
                return cls(port, line)
        raise ValueError(
            f'"{text}" is not a 6509 line (expected P<0-11>.<0-7>, e.g. "P3.4")'
        )

    @property
    def pin(self) -> int:
        """Connector pin (1-100) that carries this line."""
        base = 0 if self.port < 6 else 50
        p = self.port % 6
        start, group = (1, 2 - p) if p < 3 else (2, 5 - p)
        return base + start + 16 * group + 2 * (7 - self.line)

    def __str__(self) -> str:
        return f"P{self.port}.{self.line}"


class SignalKind(Enum):
    DIO = "dio"
    VCC = "vcc"
    GND = "gnd"


@dataclass(frozen=True)
class Signal:
    kind: SignalKind
    line: Line | None = None

    def __str__(self) -> str:
        if self.kind is SignalKind.DIO:
            return str(self.line)
        return "+5 V" if self.kind is SignalKind.VCC else "GND"


def signal_at(pin: int) -> Signal | None:
    """What a connector pin carries; ``None`` outside 1-100."""
    if pin in (49, 99):
        return Signal(SignalKind.VCC)
    if pin in (50, 100):
        return Signal(SignalKind.GND)
    if not (1 <= pin <= 48 or 51 <= pin <= 98):
        return None
    idx, offset = (pin - 1, 0) if pin <= 48 else (pin - 51, 6)
    odd_column = idx % 2 == 0
    row = idx // 2
    group = row // 8
    line = 7 - row % 8
    port = (2 - group if odd_column else 5 - group) + offset
    return Signal(SignalKind.DIO, Line(port, line))


def all_lines() -> list[Line]:
    return [Line(p, n) for p in range(PORTS) for n in range(LINES_PER_PORT)]
