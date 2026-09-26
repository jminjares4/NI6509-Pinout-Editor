"""Colors and the Qt stylesheet. One dark theme, tuned for a lab monitor."""

from PyQt6.QtGui import QColor

BG = "#1b1c20"
PANEL = "#22242a"
RAISED = "#2c2f37"
LINE = "#3a3d46"
TEXT = "#e4e6eb"
DIM = "#8a8f9b"
ACCENT = "#60a5fa"
INPUT = "#2f6eba"
INPUT_TEXT = "#9dc0f2"
OUTPUT = "#8e54c4"
OUTPUT_TEXT = "#c9a8f0"
FREE = "#30323a"
VCC = "#783428"
VCC_TEXT = "#ffc8aa"
GND = "#121214"
HOUSING = "#2a2c33"
OK = "#4ade80"
WARN = "#fbbf24"
ERROR = "#f87171"


def qc(hex_color: str) -> QColor:
    return QColor(hex_color)


STYLESHEET = f"""
* {{ color: {TEXT}; font-size: 10pt; }}
QMainWindow, QWidget#central {{ background: {BG}; }}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {RAISED}; border-radius: 4px; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {INPUT}; }}
QMenu::item:disabled {{ color: {DIM}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}

QToolBar {{ background: {PANEL}; border: 0; border-bottom: 1px solid {LINE}; padding: 4px 8px; spacing: 6px; }}
QToolBar QToolButton {{ padding: 5px 12px; border-radius: 6px; background: {RAISED}; }}
QToolBar QToolButton:hover {{ background: #3a3e48; }}
QToolBar QToolButton:disabled {{ color: {DIM}; background: {PANEL}; }}
QToolBar QLabel {{ color: {DIM}; }}

QLineEdit, QPlainTextEdit, QComboBox {{
  background: #111216; border: 1px solid {LINE}; border-radius: 6px; padding: 4px 8px;
  selection-background-color: {INPUT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; selection-background-color: {INPUT}; }}

QPushButton {{ background: {RAISED}; border: 1px solid transparent; border-radius: 6px; padding: 5px 14px; }}
QPushButton:hover {{ background: #3a3e48; }}
QPushButton:pressed {{ background: #454a55; }}
QPushButton:disabled {{ color: {DIM}; background: {PANEL}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton[role="primary"] {{ background: {INPUT}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #3a7cc9; }}
QPushButton[role="danger"] {{ background: transparent; color: {ERROR}; border: 1px solid rgba(248,113,113,110); }}
QPushButton[role="danger"]:hover {{ background: rgba(248,113,113,30); }}
QPushButton[role="fix"] {{ background: transparent; color: {WARN}; border: 1px solid rgba(251,191,36,110); }}

QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #6b7080; background: #111216;
                        border-radius: 4px; }}
QCheckBox::indicator:checked {{ background: {INPUT}; border-color: {INPUT}; }}
QTableWidget::indicator {{ width: 14px; height: 14px; }}

QTabWidget::pane {{ border: 0; border-top: 1px solid {LINE}; }}
QTabBar::tab {{ background: transparent; padding: 6px 14px; margin-right: 4px; border-radius: 6px; color: {DIM}; }}
QTabBar::tab:selected {{ background: {INPUT}; color: {TEXT}; }}
QTabBar::tab:hover:!selected {{ background: {RAISED}; color: {TEXT}; }}
QTabBar#boardTabs::tab {{ border: 1px solid {LINE}; padding: 6px 14px; font-weight: 600; }}
QTabBar#boardTabs::tab:selected {{ border-color: {INPUT}; }}

QTableWidget {{ background: {PANEL}; alternate-background-color: #262830; gridline-color: {LINE};
               border: 1px solid {LINE}; border-radius: 6px; selection-background-color: {INPUT}; }}
QHeaderView::section {{ background: {PANEL}; color: {DIM}; border: 0; border-bottom: 1px solid {LINE}; padding: 4px 6px; }}
QListWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px; }}
QListWidget::item {{ padding: 3px 6px; }}
QListWidget::item:hover {{ background: {RAISED}; }}

QScrollArea {{ border: 0; background: {BG}; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {BG}; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: #454954; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {LINE}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; color: {DIM}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}

QLabel[role="dim"] {{ color: {DIM}; }}
QLabel[role="h1"] {{ font-size: 16pt; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 14pt; font-weight: 600; }}
QLabel[role="mono"] {{ color: {DIM}; }}  /* font set in code: the system fixed-width font */
QLabel[role="chip-ok"] {{ color: {OK}; border: 1px solid {OK}; border-radius: 9px; padding: 1px 9px; }}
QLabel[role="chip-warn"] {{ color: {WARN}; border: 1px solid {WARN}; border-radius: 9px; padding: 1px 9px; }}
QLabel[role="chip-err"] {{ color: {ERROR}; border: 1px solid {ERROR}; border-radius: 9px; padding: 1px 9px; }}
"""