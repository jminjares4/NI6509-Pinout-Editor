"""JSON Schema for a board file, so other programs (test software, scripts) can
check a file's structure without this package.

A file is one board. Built from the board types in ``pinout.py``: the board's
``type`` decides which channels, pins and directions its signals may use. A board without a
``type`` is an NI 6509 (files from before the field existed).

The schema checks shape and types. The cross-field rules (signal names unique
across all boards, board names unique, NI 9401 channels in one group sharing a
direction) can't be expressed in JSON Schema, so the editor checks those
itself.
"""

from __future__ import annotations

from typing import Any

from pinout import BOARD_TYPES, DEFAULT_TYPE, BoardType, Direction, SignalKind


def _signal(bt: BoardType) -> dict[str, Any]:
    """Schema for one signal on a board of type ``bt``."""
    not_lines = [pin for pin, s in bt.pins.items() if s.kind is not SignalKind.LINE]
    outputs = [(str(l), bt.pin_of(l)) for l in bt.lines if bt.fixed_direction(l) is Direction.OUTPUT]
    inputs = [(str(l), bt.pin_of(l)) for l in bt.lines if bt.fixed_direction(l) is Direction.INPUT]
    direction: dict[str, Any] = {"enum": ["input", "output"]}
    rules: list[dict[str, Any]] = []
    if outputs and inputs:
        # Fixed by the channel: say which is which, by line and by pin.
        for found, value in ((outputs, "output"), (inputs, "input")):
            rules.append({"if": {"required": ["line"],
                                 "properties": {"line": {"pattern": "^[Pp]?(%s)$" % "|".join(
                                     name[1:].replace(".", "\\.") for name, _ in found)}}},
                          "then": {"properties": {"direction": {"const": value}}}})
            rules.append({"if": {"required": ["pin"],
                                 "properties": {"pin": {"enum": [pin for _, pin in found]}}},
                          "then": {"properties": {"direction": {"const": value}}}})
    elif outputs or inputs:
        direction = {"const": "output" if outputs else "input"}
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "description": "Signal name, e.g. ARM"},
            "line": {"type": "string", "pattern": bt.line_pattern,
                     "description": f'{bt.expected}, e.g. {bt.example}'},
            "pin": {"type": "integer", "minimum": 1, "maximum": bt.pin_count,
                    "not": {"enum": not_lines},
                    "description": "Connector pin that carries a channel"},
            "direction": direction,
            "note": {"type": "string"},
        },
        "oneOf": [
            {"required": ["line"], "not": {"required": ["pin"]}},
            {"required": ["pin"], "not": {"required": ["line"]}},
        ],
    }
    if rules:
        schema["allOf"] = rules
    if bt.fixed_direction(bt.lines[0]) is None:
        schema["required"] = ["name", "direction"]  # nothing else says which way it goes
    return schema


def _key(bt: BoardType) -> str:
    return "signal_" + bt.short


def _board() -> dict[str, Any]:
    cases = []
    for bt in BOARD_TYPES.values():
        is_type: dict[str, Any] = {"required": ["type"], "properties": {"type": {"const": bt.key}}}
        if bt.key == DEFAULT_TYPE:
            is_type = {"anyOf": [{"not": {"required": ["type"]}}, is_type]}
        cases.append({"if": is_type,
                      "then": {"properties": {"signals": {"items": {"$ref": f"#/$defs/{_key(bt)}"}}}}})
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "signals"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "description": "e.g. Board 1"},
            "type": {"enum": list(BOARD_TYPES), "default": DEFAULT_TYPE,
                     "description": "Board model; decides the channels its signals can use"},
            "signals": {"type": "array"},
        },
        "allOf": cases,
    }


SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "ni-board.schema.json",
    "title": "NI board signal mapping (one board per file)",
    "description": (
        "The signals mapped to the channels of one NI board (" + ", ".join(BOARD_TYPES) + "). "
        "Structure only: the editor also checks the rules that span signals or files (signal "
        "names unique across the open boards, board names unique, NI 9401 channels DIO0-3 and "
        "DIO4-7 each sharing one direction)."
    ),
    **_board(),
    "$defs": {_key(bt): _signal(bt) for bt in BOARD_TYPES.values()},
}
