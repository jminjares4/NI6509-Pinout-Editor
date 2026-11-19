# Board Setup

Set up the boards of a test stand and let the tool write the JSON. Nobody
edits the JSON by hand, and nothing with a problem in it can be saved.

Python 3.10+ and PyQt6. Runs on Windows, macOS and Linux.

## Read this first: two placeholders

Two things in this version are stand-ins until the real data is put in.
Both are in `pinout.py`:

- **MicroGate connector pins.** Each port is drawn with the generic EIA-530
  pin arrangement (`SERIAL_PINS`), not MicroGate's own pin table. The drawing
  says "not verified". **Do not wire from it** until it has been checked
  against the SyncLink GT4e hardware manual.
- **MicroGate setups.** The choices in a port's Setup menu
  (`SERIAL_SETUPS`) are examples. Replace them with the setups your stands
  really use.

The analog output card is drawn as channels 0-31, not as connector pins: its
connector pinout is not in the tool at all. The file a MicroGate or analog
card saves uses field names chosen here (`setup`, `range`); check them
against whatever reads the file.

## Boards

| Area            | Kind          | Board                       | You set                                   |
|-----------------|---------------|-----------------------------|-------------------------------------------|
| HWIL            | NI 6509       | PCIe-6509, 100-pin          | a name per pin (P0-P5 outputs, P6-P11 inputs) |
| HWIL            | MicroGate     | SyncLink GT4e, 4 ports      | a name and a setup per port               |
| HWIL            | Analog output | Concurrent CCURAOCC, 32 ch. | a name and a range per channel            |
| Ground Software | NI 9205       | cDAQ, 37-pin DSUB           | a name per pin (analog inputs)            |
| Ground Software | NI 9264       | cDAQ, 37-pin DSUB           | a name per pin (analog outputs)           |
| Ground Software | NI 9401       | cDAQ, 25-pin DSUB           | a name per pin; direction per group of four |
| Ground Software | NI 9476       | cDAQ, 37-pin DSUB           | a name per pin (digital outputs)          |

The cDAQ modules are mapped for their **DSUB** connectors; the spring-terminal
versions number their terminals differently and are not covered.

## Install and run

```
pip install -r requirements.txt             # PyQt6, the only dependency

python3 app.py                              # open the editor
python3 app.py examples                     # open the two example files
```

On Windows use `python` in place of `python3`.

## Using it

Three panes, left to right:

1. **The list of boards.** HWIL on top, Ground Software below; under each, the
   kinds of board and your cards of that kind. Click one to show it. The
   button at the bottom adds another card of the kind you are on.
2. **The board**, drawn as its connector. Click what you want to set.
3. **The side panel**, which edits what you clicked:
   - **A pin** (NI 6509, cDAQ modules): type its name. On an NI 9401 the
     panel also has the direction, which applies to the pin's whole group of
     four.
   - **A port** (MicroGate): type its name and pick its setup. The port's
     connector lights up the pins that setup uses: purple for outputs, blue
     for inputs.
   - **A channel** (analog output): type its name and pick its range.

A name puts it in use; an empty name takes it out of use again, and so does
the button under the name. **Enter** moves on to the next pin, so a run of
them can be named from the keyboard; the **Up** and **Down** arrows step
through the pins both ways; **Esc** finishes.

Under that, the panel lists everything in use on the card. Click a row to go
to it.

Other things on the screen:

- **Edit** appears in the side panel while a file is view-only; it is the same
  switch as Edit in the toolbar.
- **Search signals**, above the list of boards, finds a signal on any card by
  its name, channel or pin number. Click a result to go to it.
- A problem with a pin (a name used twice, a name that isn't
  identifier-shaped) shows right under its name as you type.
- On an NI 9401, each group of four pins that shares a direction has a tinted
  band labelled IN or OUT.

At the bottom of the side panel: the card's name, the file it is in, and
*Remove card*. The next Save takes a removed card out of its file; Undo brings
it back.

The toolbar has four buttons:

- **Open** opens config files. Pick several, or drop files or a folder
  anywhere on the window.
- **Edit** (Ctrl+E) turns changes on and off. Files open **view-only**: you
  can click through every card and pin, but nothing can be changed, added or
  removed until you turn Edit on. Starting with no file, Edit is already on.
- **Save** writes every file whose cards changed. There is one file per area:
  the HWIL cards in one, the Ground Software modules in another. A new card
  joins its area's file; if that area has no file yet, you are asked once
  where to put it (`hwil.json`, `ground_software.json`).
- **Undo** (Ctrl+Z; redo is Ctrl+Shift+Z). Typing a name is one step.

A card with unsaved changes has a **•** in the list, and the toolbar says
*unsaved changes*.

### The bottom line

One line says how things stand: `✓ No problems. Ready to save.`, or the first
problem. Click it to go straight to that pin, port or channel, in whichever
area it is. A card with a problem is marked ✖ (error) or ⚠ (warning) in the
list.

Errors block Save:

- a signal with no name (a setup or range picked, but nothing typed)
- a name used twice anywhere on the open cards (case-insensitive)
- a card with no name, or two cards with the same name

Warnings don't: names that aren't identifier-shaped (`2 FAST`), and names
with spaces at the start or end (saved trimmed).

### Problems in a file

When you open a file, the editor reads all of it, loads everything it can and
shows each mismatch on the bottom line with the file name, the signal and
what was done about it, for example:

```
hwil.json: Analog card 1 · signal "B": "CH99" is not an CCURAOCC channel (expected CH0-CH31, e.g. "CH5") — left out
```

They block Save so nothing is lost by accident. Fix the file and open it
again, or click **Leave out & continue** to accept what was read (Undo
reverses it). A file that can't be read at all (broken JSON, not a board,
over 5 MB) isn't opened, and the bottom line says why.

## File format

A file is a list of cards under `"boards"`. `examples/hwil.json`:

```json
{
  "boards": [
    {
      "name": "DIO card 1",
      "type": "NI 6509",
      "signals": [
        { "name": "POWER_EN",  "line": "P0.0", "direction": "output" },
        { "name": "HEATER_ON", "line": "P6.0", "direction": "input" }
      ]
    },
    {
      "name": "Serial card 1",
      "type": "MicroGate GT4e",
      "signals": [
        { "name": "IMU_LINK", "line": "PORT0", "setup": "Async 8N1, external clock" }
      ]
    },
    {
      "name": "Analog card 1",
      "type": "CCURAOCC",
      "signals": [
        { "name": "FIN_1_POS", "line": "CH0", "direction": "output", "range": "-10 to 10 V" }
      ]
    }
  ]
}
```

`examples/ground_software.json` is the same shape with the four cDAQ modules.

`type` is one of `NI 6509`, `MicroGate GT4e`, `CCURAOCC`, `NI 9205`,
`NI 9264`, `NI 9401`, `NI 9476`. A signal is its `name` and the `line` it is
on (a pin's channel, `PORT0`-`PORT3`, or `CH0`-`CH31`). Pin boards also write
`direction` so the file reads on its own; a serial port writes its `setup`
and an analog channel its `range`. A `note` in a file is kept when the file
is saved again.

A file holding a single card on its own (no `"boards"`) also opens; saving
writes it back in the form above. A board with no `type` is an NI 6509.

## Giving it to the team

**Where people have Python:** put this folder on a shared drive (or in the
repo) and run `python3 app.py`. On Windows, a shortcut to `pythonw app.py`
opens it without a console window.

**Where they don't:** package it as a folder with PyInstaller, built once on
each operating system you support:

```
pip install pyinstaller
pyinstaller --noconfirm --windowed --onedir --name BoardSetup app.py
```

That makes `dist/BoardSetup/`. Don't use `--onefile`: it unpacks itself on
every launch and starts several times slower.

## Layout

```
app.py              the window (run this)
connector.py        the pin drawing (QPainter), used for every board
theme.py            colors and stylesheet
model.py            cards, rules, reading config files, undo history (no Qt)
pinout.py           the boards: pin maps, serial setups, analog ranges (no Qt)
requirements.txt    PyQt6
examples/           hwil.json and ground_software.json: one small file per area
```

To add a board, describe it in `pinout.py` and add it to `BOARD_TYPES`, with
its `domain` (area) and `kind` (its row in the list). The window and file checks follow
from that.

## What the program touches

It reads the config files you open and writes the config files you save.
Nothing else: no network access, no other programs started. A save goes
through Qt's `QSaveFile`, so a failed save leaves the old file untouched.

Sources: NI PCIe-6509 pinout; NI-9205, NI-9264 and NI-9476 Getting Started
(DSUB pinouts); NI-9401 Getting Started; Concurrent CP-DA3218 data sheet
(channel count and output ranges).
