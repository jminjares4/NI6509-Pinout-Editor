# NI 6509 Pinout Editor

Map signals to the 100-pin connector of NI PCIe-6509 digital I/O boards and
let the tool write the JSON. Nobody edits the JSON by hand, and a project with
errors can't be saved.

Python 3.10+ and PyQt6. Runs on Windows, macOS and Linux.

## Install and run

```
pip install PyQt6

python3 app.py                         # open the editor
python3 app.py stand_4_boards.json     # open a file
```

On Windows use `python` in place of `python3`.

## Using the editor

The window has two tabs: **NI 6509** (the boards and their connectors) and
**JSON** (the whole project, exactly as Save writes it). The Checks panel
underneath covers the whole project.

- **Boards**: one tab per card, with its signal count; the tab text turns red
  or amber when that board has a problem. Each board has a name (it's how the
  file identifies the card). *+ Add board* adds the next free `Board N`.
  *Remove board* asks first and can be undone; the last board can't be
  removed.
- **Connector**: click a pin to edit it, double-click a free pin to assign
  it, Delete removes, Esc deselects.
- **Direction is fixed by the pin**, the way these 6509s are wired: pins
  1-50 (ports P0-P5) are **outputs** and pins 51-100 (P6-P11) are **inputs**.
  The connector labels each block, and direction isn't editable anywhere.
- **Pin tab**: name, note, move to another line (moving across the two
  blocks changes the signal's direction, which the tab shows).
- **Signals tab**: every signal on every board; filter; double-click Name or
  Note to edit in place.
- **Checks** (bottom): click an issue to jump to it.
- **Undo/Redo** (Ctrl+Z / Ctrl+Shift+Z) cover every change, including board
  removal. Typing in a field is one undo step, not one per letter.
- **Drag and drop**: drop a `.json` anywhere on the window and its boards and
  signals fill the connector. Non-JSON files are ignored; if you have unsaved
  changes you're asked first.
- Open, Save, Save As, **Open Recent**, and a prompt before closing or opening
  with unsaved changes.

### What counts as an error (blocks Save)

- a signal with no name
- a signal name used twice anywhere in the project (case-insensitive)
- a board with no name, or two boards with the same name

Warnings (don't block Save): signal names that aren't identifier-shaped
(`2 FAST`), and names with spaces at the start or end (saved trimmed).

### Problems in a file

When you open a file, the editor reads all of it, loads everything it can and
lists every mismatch in **Checks** with its line number in the file and what
was done about it. There are no pop-ups. For example:

```
file line 19: Board 1 · signal "ABORT": "Pqw.x" is not a 6509 line ... — left out
file line 11: Board 1 · signal "H": says "output", but P11.0 (pin 66) is an input ... — kept on P11.0 as an input
file line 9:  Board 1 · signal "F": unknown field "dirction" (did you mean "direction"?) — ignored
```

What it checks: broken JSON (with the line and column), the same key twice in
one object (JSON silently keeps the last), unknown or misspelled fields,
lines that don't exist (`P12.0`, `P2.8`, `P3,4`), supply pins (49, 50, 99,
100), a pin written as text (`"47"`), `line` and `pin` both given, two signals
on one line, a missing name, a `direction` that isn't `"input"`/`"output"`
or doesn't match its pin, and a note that isn't text.

File problems block Save, so nothing is lost by accident. Then either:

- fix the file in a text editor and click **Reload file** (Ctrl+R), or
- click **Leave out & continue** to accept what was loaded (entries marked
  "left out" or "ignored" won't be saved). Undo reverses it.

## File format

```json
{
  "boards": [
    {
      "name": "Board 1",
      "signals": [
        { "name": "ARM",       "line": "P0.1",  "direction": "output" },
        { "name": "HEATER_ON", "line": "P11.7", "direction": "input", "note": "K3 aux contact" }
      ]
    }
  ]
}
```

Each board has a `name` and a list of `signals`. A signal is its name, the
DIO `line` it maps to (or a connector `pin`), and an optional `note`. The
editor also writes `direction` (P0-P5 output, P6-P11 input) so the file reads
on its own; if a file includes it, it must match the line.

*File → Export JSON Schema* writes a JSON Schema (2020-12) for the format,
including the direction rule, for other programs that read these files. The
schema checks structure only; the editor also checks the cross-field rules
above, which JSON Schema can't express.

Older files still open: `"buttons"` is read as `"signals"`, the single-board
shape (signals at the top level) becomes Board 1, and the retired fields
`selected`, `device` and `card` are dropped, and so is the old top-level
project `"name"`. The editor lists what saving will
change and marks the project unsaved, so saving rewrites the file in the
current format.

## Layout

```
app.py              main window (run this)
connector.py        connector drawing (QPainter)
theme.py            colors and stylesheet
model.py            boards, rules, JSON load/save, undo history (no Qt)
pinout.py           NI 6509 pin map (pure arithmetic)
schema.py           JSON Schema for the file format
ni6509_test.json    sample project
```

Source for the pin map: NI PCIe-6509 pinout (ni.com/docs).