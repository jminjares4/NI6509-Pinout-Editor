"""JSON Schema for the mapping file, so other programs (test software, scripts) can
check a file's structure without this package.

The schema checks shape and types. The cross-field rules (signal names unique
across all boards, board names unique) can't be expressed in JSON Schema, so
the editor checks those itself.
"""

from __future__ import annotations

from typing import Any

SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "ni6509-mapping.schema.json",
    "title": "NI 6509 signal mapping",
    "description": (
        "Signals mapped to the DIO lines of NI PCIe-6509 boards. Structure only: the "
        "editor also checks the cross-field rules (signal names unique across the "
        "project, board names unique)."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["boards"],
    "properties": {
        "boards": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/board"},
        },
    },
    "$defs": {
        "board": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "signals"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "description": "e.g. Board 1"},
                "signals": {"type": "array", "items": {"$ref": "#/$defs/signal"}},
            },
        },
        "signal": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "description": "Signal name, e.g. ARM"},
                "line": {
                    "type": "string",
                    "pattern": "^[Pp]?([0-9]|1[01])\\.[0-7]$",
                    "description": "P<port 0-11>.<line 0-7>, e.g. P3.4",
                },
                "pin": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "not": {"enum": [49, 50, 99, 100]},
                    "description": "Connector pin; 49/99 are +5 V, 50/100 are GND",
                },
                "direction": {"enum": ["input", "output"], "description": "P0-P5 output, P6-P11 input"},
                "note": {"type": "string"},
            },
            "oneOf": [
                {"required": ["line"], "not": {"required": ["pin"]}},
                {"required": ["pin"], "not": {"required": ["line"]}},
            ],
            # Direction is fixed by the line: P0-P5 (pins 1-48) are outputs,
            # P6-P11 (pins 51-98) are inputs.
            "allOf": [
                {"if": {"required": ["line"], "properties": {"line": {"pattern": "^[Pp]?[0-5]\\."}}},
                 "then": {"properties": {"direction": {"const": "output"}}}},
                {"if": {"required": ["line"], "properties": {"line": {"pattern": "^[Pp]?([6-9]|1[01])\\."}}},
                 "then": {"properties": {"direction": {"const": "input"}}}},
                {"if": {"required": ["pin"], "properties": {"pin": {"maximum": 48}}},
                 "then": {"properties": {"direction": {"const": "output"}}}},
                {"if": {"required": ["pin"], "properties": {"pin": {"minimum": 51}}},
                 "then": {"properties": {"direction": {"const": "input"}}}},
            ],
        },
    },
}