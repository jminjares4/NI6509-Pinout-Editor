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
NC_TEXT = "#566070"
AUX = "#3a3829"      # pins the board uses itself (AISENSE, PFI0)
HOUSING = "#1a1d25"
HOUSING_EDGE = "#2c313d"
PIN_EDGE = "#3a4150"
# Status
OK = "#3ddc97"
WARN = "#f5b942"
ERROR = "#ff6b6b"


def qc(hex_color: str) -> QColor:
    return QColor(hex_color)


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

QMenuBar {{ background: {BG}; border-bottom: 1px solid {LINE}; padding: 2px 6px; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {RAISED}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 6px; }}
QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 6px 8px; }}

QToolBar {{ background: {BG}; border: 0; border-bottom: 1px solid {LINE}; padding: 5px 12px; spacing: 4px; }}
QToolBar::separator {{ width: 1px; background: {LINE}; margin: 6px 8px; }}
QToolBar QToolButton {{ padding: 6px 12px; border-radius: 8px; background: transparent; }}
QToolBar QToolButton:hover {{ background: {RAISED}; }}
QToolBar QToolButton:pressed {{ background: {HOVER}; }}
QToolBar QToolButton:disabled {{ color: {MUTED}; }}
QToolBar QToolButton#primary {{ background: {INPUT}; color: #ffffff; font-weight: 600; padding: 6px 16px; }}
QToolBar QToolButton#primary:hover {{ background: #3b7be0; }}
QToolBar QLabel {{ color: {DIM}; }}

QLineEdit, QPlainTextEdit, QComboBox {{
  background: {FIELD}; border: 1px solid {LINE}; border-radius: 8px; padding: 6px 10px;
  selection-background-color: {INPUT};
}}
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{ border-color: {PIN_EDGE}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
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
QPushButton[role="danger"] {{ background: transparent; color: {ERROR}; border: 1px solid {_rgba(ERROR, 80)}; }}
QPushButton[role="danger"]:hover {{ background: {_rgba(ERROR, 28)}; }}
QPushButton[role="danger"]:disabled {{ color: {MUTED}; border-color: {LINE}; }}
QPushButton[role="fix"] {{ background: transparent; color: {WARN}; border: 1px solid {_rgba(WARN, 90)}; }}
QPushButton[role="fix"]:hover {{ background: {_rgba(WARN, 28)}; }}

QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {PIN_EDGE}; background: {FIELD};
                        border-radius: 4px; }}
QCheckBox::indicator:checked {{ background: {INPUT}; border-color: {INPUT}; }}
QTableWidget::indicator {{ width: 14px; height: 14px; }}

/* Tab text colors are set in code (app._tint_tabs), so problems can show. */
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ background: transparent; padding: 6px 14px; margin-right: 4px; border-radius: 8px; }}
QTabBar::tab:selected {{ background: {RAISED}; }}
QTabBar::tab:hover:!selected {{ background: {_rgba("#ffffff", 10)}; }}
QTabBar#panels::tab {{ border-radius: 0; padding: 9px 14px 8px 14px; margin-right: 2px; font-weight: 600;
                       border-bottom: 2px solid transparent; }}
QTabBar#panels::tab:selected {{ background: transparent; border-bottom: 2px solid {ACCENT}; }}
QTabBar#panels::tab:hover:!selected {{ background: transparent; border-bottom: 2px solid {PIN_EDGE}; }}
QTabBar#boardTabs::tab {{ border: 1px solid {LINE}; padding: 5px 14px; font-weight: 600; }}
QTabBar#boardTabs::tab:selected {{ background: {_rgba(ACCENT, 34)}; border-color: {ACCENT}; }}

QTableWidget {{ background: transparent; alternate-background-color: {_rgba("#ffffff", 5)};
               gridline-color: transparent; border: 0; selection-background-color: {_rgba(ACCENT, 46)}; }}
QTableWidget::item {{ padding: 2px 6px; }}
QTableWidget QLineEdit {{ padding: 0 4px; border-radius: 0; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {DIM}; border: 0; border-bottom: 1px solid {LINE};
                        padding: 6px; }}
QListWidget {{ background: transparent; border: 0; outline: 0; }}
QListWidget::item {{ padding: 4px 8px; border-radius: 6px; }}
QListWidget::item:hover, QListWidget::item:selected {{ background: {RAISED}; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 8px; height: 8px; margin: 0; }}
QScrollBar::handle {{ background: {PIN_EDGE}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {BG}; border-top: 1px solid {LINE}; color: {DIM}; }}
QStatusBar::item {{ border: 0; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}

QLabel[role="dim"] {{ color: {DIM}; }}
QLabel[role="h1"] {{ font-size: 15pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="mono"] {{ color: {DIM}; }}  /* font set in code: the system fixed-width font */
{_chip("chip-ok", OK)}{_chip("chip-warn", WARN)}{_chip("chip-err", ERROR)}"""
