# NI Board Pinout Editor

Map signals to the connectors of NI boards and let the tool write the JSON.
**Each board has its own config file.** Nobody edits the JSON by hand, and a
board with errors can't be saved.

Python 3.10+ and PyQt6. Runs on Windows, macOS and Linux.

## Boards

| Type      | Connector     | Channels            | Direction                                   |
|-----------|---------------|---------------------|---------------------------------------------|
| NI 6509   | 100-pin SCSI  | `P0.0`-`P11.7`      | fixed: P0-P5 outputs, P6-P11 inputs         |
| NI 9205   | 37-pin DSUB   | `AI0`-`AI31`        | fixed: inputs                               |
| NI 9264   | 37-pin DSUB   | `AO0`-`AO15`        | fixed: outputs                              |
| NI 9401   | 25-pin DSUB   | `DIO0`-`DIO7`       | you choose, per group: DIO0-3 and DIO4-7    |
| NI 9476   | 37-pin DSUB   | `DO0`-`DO31`        | fixed: outputs                              |

The C Series modules (92xx, 94xx) are mapped for their **DSUB** connectors;
the spring-terminal versions number their terminals differently and are not
covered.

On the NI 9205, `AISENSE`, `PFI0` and `DO0` are shown on the connector but
can't be given a signal; only the 32 AI channels can.

## Install and run

```
pip install -r requirements.txt             # PyQt6, the only dependency

python3 app.py                              # open the editor
python3 app.py sample_stand                 # open every board file in a folder
python3 app.py AI_module.json DO_module.json    # open particular boards
```

On Windows use `python` in place of `python3`.

## Giving it to the team

The editor starts in well under a second. Keep it that way by picking one of
these two; both were timed at about 0.2 s to a drawn window.

**Where people have Python:** put this folder on a shared drive (or in the
repo) and run `python3 app.py`. On Windows, a shortcut to
`pythonw app.py` opens it without a console window.

**Where they don't:** package it as a folder with PyInstaller, built once on
each operating system you support:

```
pip install pyinstaller
pyinstaller --noconfirm --windowed --onedir --name BoardEditor app.py
```

That makes `dist/BoardEditor/`. Zip it or copy it to the share; people run
`BoardEditor` (`BoardEditor.exe` on Windows) inside it. No Python needed.

Don't use `--onefile`. A single .exe looks tidier but unpacks itself to a
temporary folder on every launch: it measured about four times slower to start
here, and on Windows a virus scanner re-checking the unpacked files each time
makes it worse.

## One file per board

The window is a workspace: open as many board files as you like and work on
them side by side. Each board remembers its own file.

- **Open** (or drag and drop) takes several files at once, or a folder,
  which means every `.json` in it. Boards are added next to the ones already
  open; a file that is already open is just shown. One Undo takes an Open
  back.
- **Save** (Ctrl+S) writes the board you are looking at to its file.
  **Save As** gives it a different file. **Save All** (Ctrl+Alt+S) writes
  every board that has changes.
- A **new board** has no file until it is saved. Save asks where; Save All
  asks once for a folder and names each new file after its board
  (`AI module` → `AI_module.json`).
- A board with unsaved changes shows a **•** on its tab, and the toolbar
  shows how many boards are unsaved. The file name is beside the board's
  title; hover it for the full path.
- **Close board** takes a board out of the window. Its file stays on disk.
  It asks first if the board has unsaved changes, and Undo brings the board
  back.
- Renaming a board doesn't rename its file; use Save As for that.
- Two open boards can't share a file, and can't share a name.
- A save replaces the file in one step: if it fails (disk full, no
  permission), the file on disk is exactly what it was before.
- **New** closes everything and starts with one empty board.

The checks still look across every open board: a signal name used on two
boards is an error on both, whichever files they live in.

## Using the editor

There is one **panel per board type** (NI 6509, NI 9205, NI 9264, NI 9401,
NI 9476), each holding only the open boards of that type, and a **JSON**
panel showing the current board's file exactly as Save writes it. The Checks
list underneath covers all open boards.

- **Panels**: each shows how many boards of that type are open. A panel with
  none says so and offers to add the first one. A board's type is the panel
  it is in.
- **Boards**: inside a panel, one tab per board with its signal count. A
  board with a problem is marked ✖ (error) or ⚠ (warning) on its tab, and so
  is its panel, so you can find it from anywhere. *+ Add* adds the next free
  `Board N` to the panel. The last board can't be closed.
- **Connector**: click a pin to edit it, double-click a free pin to assign
  it, Delete removes, Esc deselects. Supply, ground/COM and NC pins can be
  selected to see what they are, but can't carry a signal.
- **Direction** comes from the board (see the table above) and isn't
  editable, except on an **NI 9401**: the Pin tab has an input/output picker
  there, and it applies to the pin's whole group (DIO0-3 or DIO4-7), because
  the module sets direction four lines at a time.
- **Pin tab**: name, note, move to another channel on the same board.
- **Signals tab**: every signal on every open board; filter; double-click
  Name or Note to edit in place. Clicking a row goes to that board,
  switching panel if needed.
- **Checks** (bottom): click an issue to jump to it, in whichever panel it is.
- **Undo/Redo** (Ctrl+Z / Ctrl+Shift+Z) cover every change, including
  opening and closing boards. Typing in a field is one undo step, not one
  per letter.

### What counts as an error (blocks Save)

- a signal with no name
- a signal name used twice on the open boards (case-insensitive)
- a board with no name, or two boards with the same name

An error blocks saving the board it is on; other boards still save. Save All
is refused while any open board has an error.

Warnings (don't block Save): signal names that aren't identifier-shaped
(`2 FAST`), and names with spaces at the start or end (saved trimmed).

### Problems in a file

When you open a file, the editor reads all of it, loads everything it can and
lists every mismatch in **Checks** with the file name, the line number and
what was done about it. There are no pop-ups. For example:

```
DIO_card_1.json line 19: DIO card 1 · signal "ABORT": "Pqw.x" is not a 6509 line ... — left out
DIO_card_1.json line 11: DIO card 1 · signal "H": says "output", but P11.0 (pin 66) is an input ... — kept on P11.0 as an input
AI_module.json line 9: AI module · signal "X": "AI32": there is no AI32 on an NI 9205 (channels are AI0-AI31) — left out
TTL_module.json line 14: TTL module · signal "B": says "input", but DIO0-DIO3 share one direction ... — kept on DIO1 as an output
```

What it checks: the same key twice in one object (JSON silently keeps the
last), unknown or misspelled fields, an unknown board `type`, channels that
don't exist on that board (`P12.0`, `AI32`, an `AO` name on a 9205), pins
that can't carry a signal (supply, COM, NC, AISENSE), a pin written as text
(`"47"`), `line` and `pin` both given, two signals on one channel, a missing
name, a `direction` that isn't `"input"`/`"output"` or doesn't match its
channel, two NI 9401 signals in one group with different directions, and a
note that isn't text.

A file that can't be read at all (broken JSON, not a board, over 5 MB) isn't
opened; the status bar says which file and why. Files saved with a byte-order
mark (Windows Notepad) read fine.

File problems block saving that board, so nothing is lost by accident. Then
either:

- fix the file in a text editor, go to its board and click **Reload this
  board** (Ctrl+R), or
- click **Leave out & continue** to accept what was loaded (entries marked
  "left out" or "ignored" won't be saved). Undo reverses it.

## File format

One board per file:

```json
{
  "name": "AI module",
  "type": "NI 9205",
  "signals": [
    { "name": "TANK_PRESS", "line": "AI0", "direction": "input", "note": "0-10 V" },
    { "name": "LINE_PRESS", "line": "AI1", "direction": "input" }
  ]
}
```

A board is its `name`, its `type` (one of the five in the table at the top)
and a list of `signals`. A signal is its name, the channel (`line`) it maps
to (or a connector `pin`), and an optional `note`. The editor also writes
`direction` so the file reads on its own; where the board fixes the
direction, a file that includes it must match. On an NI 9401 `direction` is
required, since nothing else says which way the line goes.

*File → Export JSON Schema* writes a JSON Schema (2020-12) for a board file,
including each board type's channels, pins and direction rule, for other
programs that read these files. The schema checks structure only; the editor
also checks the rules that span signals or files, which JSON Schema can't
express.

### Older files

Older files still open, and saving rewrites them in the current format:

- A file with several boards under `"boards"` opens as that many boards, all
  unsaved and without a file of their own. **Save All** asks for a folder and
  writes one file per board. The original file is left as it was.
- A board with no `type` is an NI 6509.
- `"buttons"` is read as `"signals"`; the retired fields `selected`, `device`
  and `card` are dropped.

The status bar says what saving will change.

## Layout

```
app.py              main window (run this)
connector.py        connector drawing (QPainter), any board type
theme.py            colors and stylesheet
model.py            boards, rules, reading board files, undo history (no Qt)
pinout.py           the board types and their pin maps (no Qt)
schema.py           JSON Schema for a board file
requirements.txt    PyQt6
sample_stand/       sample: six boards, one file each, every board type
ni6509_test.json    sample of the older format (four 6509s in one file)
test_detect.json    the same with a mistake in it, to see Checks at work
```

## What the program touches

It reads the board files you open and writes the board files you save, plus
the schema file if you export it. It remembers your recent files through Qt's
settings store (the registry on Windows, a small config file elsewhere).
Nothing else: no network access, no other programs started, no files deleted.

To add a board type, write its pin table in `pinout.py` and add it to
`BOARD_TYPES`; the connector drawing, file checks and schema follow from it.

Sources for the pin maps (ni.com/docs): NI PCIe-6509 pinout; NI-9205, NI-9264
and NI-9476 Getting Started (DSUB pinouts); NI-9401 Getting Started.
