"""Colors and the Qt stylesheet. One dark theme, tuned for a lab monitor.

The look in three rules: flat surfaces on a near-black background, cards
(``QFrame#card``) for the things you work in, and one accent color. Blue and
violet are otherwise reserved for what they mean on the connector: input and
output.
"""

from PyQt6.QtGui import QColor

# Surfaces, darkest to lightest
BG = "#0f1115"       # the window
FIELD = "#0b0d11"    # text fields
PANEL = "#161920"    # cards
RAISED = "#1f232c"   # buttons, selected tabs, hover card
HOVER = "#282d39"
LINE = "#262b36"     # hairlines and borders
# Text
TEXT = "#e6e8ee"
ON_PIN = "#ffffff"    # the number on a pin in use
DIM = "#8b93a3"
MUTED = "#566070"    # disabled
ACCENT = "#5b9dff"
# Connector
INPUT = "#2f6fd6"
INPUT_TEXT = "#9cc2ff"
OUTPUT = "#7c5ad9"
OUTPUT_TEXT = "#c4b0ff"
FREE = "#232733"
VCC = "#7a3524"
VCC_TEXT = "#ffc9ad"
GND = "#0a0b0e"
NC = "#14161b"       # no-connection pins
NC_TEXT = "#7d8696"
AUX = "#3a3829"      # pins the board uses itself (AISENSE, PFI0)
HOUSING = "#1a1d25"
HOUSING_EDGE = "#2c313d"
PIN_EDGE = "#3a4150"
# Status
OK = "#3ddc97"
WARN = "#f5b942"
ERROR = "#ff6b6b"


def _rgba(hex_color: str, alpha: int) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _chip(role: str, color: str) -> str:
    return (f'QLabel[role="{role}"] {{ color: {color}; background: {_rgba(color, 26)}; '
            f"border: 1px solid {_rgba(color, 90)}; border-radius: 10px; padding: 2px 10px; }}\n")


STYLESHEET = f"""
* {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, QWidget#central {{ background: {BG}; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}

QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 6px; }}
QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 6px 8px; }}

QToolBar {{ background: {BG}; border: 0; border-bottom: 1px solid {LINE}; padding: 5px 12px; spacing: 4px; }}
QToolBar::separator {{ width: 1px; background: {LINE}; margin: 6px 8px; }}
QToolBar QToolButton {{ padding: 6px 12px; border-radius: 8px; background: transparent; border: 1px solid transparent; }}
QToolBar QToolButton:hover {{ background: {RAISED}; }}
QToolBar QToolButton:pressed {{ background: {HOVER}; }}
QToolBar QToolButton:disabled {{ color: {MUTED}; }}
QToolBar QToolButton:checked {{ background: {_rgba(ACCENT, 46)}; border: 1px solid {ACCENT}; }}
QToolBar QToolButton#primary {{ background: {INPUT}; color: #ffffff; font-weight: 600; padding: 6px 16px; }}
QToolBar QToolButton#primary:hover {{ background: #3b7be0; }}
QToolBar QLabel {{ color: {DIM}; }}

QLineEdit, QComboBox {{
  background: {FIELD}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 10px;
  selection-background-color: {INPUT};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {PIN_EDGE}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QLineEdit:read-only {{ background: transparent; }}
QComboBox:disabled {{ color: {DIM}; background: transparent; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; selection-background-color: {HOVER}; }}

QPushButton {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 14px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ color: {MUTED}; background: transparent; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton[role="primary"] {{ background: {INPUT}; border-color: {INPUT}; color: #ffffff; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #3b7be0; }}
QPushButton[role="ghost"] {{ background: transparent; border: 1px dashed {PIN_EDGE}; color: {DIM}; }}
QPushButton[role="ghost"]:hover {{ color: {TEXT}; border-color: {ACCENT}; }}
QPushButton[role="fix"] {{ background: transparent; color: {WARN}; border: 1px solid {_rgba(WARN, 90)}; }}
QPushButton[role="fix"]:hover {{ background: {_rgba(WARN, 28)}; }}

/* The list of boards down the left. Row colors are set in code, so problems can show. */
QFrame#side {{ background: {FIELD}; border-right: 1px solid {LINE}; }}
QTreeWidget#nav {{ background: transparent; border: 0; outline: 0; font-size: 11pt; }}
QTreeWidget#nav::item {{ padding: 7px 8px; border-radius: 8px; }}
QTreeWidget#nav::item:hover {{ background: {_rgba("#ffffff", 10)}; }}
QTreeWidget#nav::item:selected {{ background: {_rgba(ACCENT, 40)}; }}
QTreeWidget#nav::branch {{ background: transparent; }}
/* What is in use on the card, in the side panel. */
QListWidget#signals {{ background: transparent; border: 0; outline: 0; }}
QListWidget#signals::item {{ padding: 4px 6px; border-radius: 6px; }}
QListWidget#signals::item:hover {{ background: {_rgba("#ffffff", 10)}; }}
QListWidget#signals::item:selected {{ background: {_rgba(ACCENT, 40)}; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 8px; height: 8px; margin: 0; }}
QScrollBar::handle {{ background: {PIN_EDGE}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}

/* One serial port on the MicroGate panel. */
QFrame#cell {{ background: {BG}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#cell[selected="true"] {{ border-color: {ACCENT}; }}
QFrame#cell QLabel {{ background: transparent; border: 0; }}
/* The one line at the bottom that says whether everything is all right. */
QPushButton#statusLine {{ background: transparent; border: 0; text-align: left; padding: 6px 4px; font-size: 11pt; color: {DIM}; }}
QPushButton#statusLine[state="ok"] {{ color: {OK}; }}
QPushButton#statusLine[state="warn"] {{ color: {WARN}; }}
QPushButton#statusLine[state="error"] {{ color: {ERROR}; }}
QComboBox::down-arrow {{ image: none; }}

QLabel[role="dim"] {{ color: {DIM}; }}
QLabel[role="h2"] {{ font-size: 13pt; font-weight: 600; }}
{_chip("chip-warn", WARN)}"""
