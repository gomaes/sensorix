"""Dark, high-density styling that mimics CPUID HWMonitor's look."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# -- palette ---------------------------------------------------------------
BACKGROUND = "#1b1b1d"
SURFACE = "#141416"
SURFACE_ALT = "#1a1a1d"
BORDER = "#2e2e32"
HEADER = "#232326"
TEXT = "#d7d7d9"
TEXT_DIM = "#8b8b90"
GROUP_TEXT = "#7ec4ff"
VALUE_TEXT = "#eaeaec"
SELECTION = "#2b4f7e"
ALARM = "#ff5f56"
ACCENT = "#4ec9b0"

#: One tight row, HWMonitor style.
ROW_HEIGHT = 18

STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
    font-size: 11px;
}}
QMainWindow::separator {{ background: {BORDER}; }}

QTreeWidget {{
    background-color: {SURFACE};
    alternate-background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    outline: 0;
    show-decoration-selected: 1;
}}
QTreeWidget::item {{
    height: {ROW_HEIGHT}px;
    border: 0px;
    padding: 0px 2px;
}}
QTreeWidget::item:selected {{
    background-color: {SELECTION};
    color: #ffffff;
}}

QHeaderView::section {{
    background-color: {HEADER};
    color: {TEXT_DIM};
    padding: 3px 6px;
    border: 0px;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: bold;
}}

QMenuBar {{ background-color: {HEADER}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 9px; background: transparent; }}
QMenuBar::item:selected {{ background-color: {SELECTION}; }}
QMenu {{ background-color: {HEADER}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 4px 22px 4px 22px; }}
QMenu::item:selected {{ background-color: {SELECTION}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 0px; }}

QStatusBar {{ background-color: {HEADER}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0px 6px; }}
QStatusBar::item {{ border: 0px; }}

QScrollBar:vertical {{ background: {SURFACE}; width: 11px; margin: 0px; }}
QScrollBar::handle:vertical {{ background: #3a3a40; min-height: 22px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: #4a4a52; }}
QScrollBar:horizontal {{ background: {SURFACE}; height: 11px; margin: 0px; }}
QScrollBar::handle:horizontal {{ background: #3a3a40; min-width: 22px; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMessageBox, QInputDialog, QDialog {{ background-color: {BACKGROUND}; }}
QPushButton {{
    background-color: {HEADER};
    border: 1px solid {BORDER};
    padding: 4px 12px;
    border-radius: 2px;
}}
QPushButton:hover {{ background-color: #2d2d33; }}
QPushButton:pressed {{ background-color: {SELECTION}; }}
QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    padding: 3px;
}}
QTextEdit {{ background-color: {SURFACE}; border: 1px solid {BORDER}; }}
"""


def apply(app: QApplication) -> None:
    """Install the Fusion style, a dark palette and the stylesheet."""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BACKGROUND))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(HEADER))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(HEADER))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_DIM))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
