"""Оформление: фирменная гамма (красный, графит, белый) и стиль элементов интерфейса."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette

RED = "#D7262D"          # фирменный красный
RED_DARK = "#B01E24"
RED_TINT = "#FCE9EA"
GRAPHITE = "#1F2023"     # графит: верхняя панель, текст
GRAPHITE_2 = "#2D2F33"
GRAPHITE_3 = "#44474D"
BG = "#F2F3F5"           # фон окна
CARD = "#FFFFFF"
BORDER = "#DCDFE3"
GRID = "#ECEEF1"
TEXT = "#1F2023"
MUTED = "#6B7078"
AMBER = "#E9A800"        # прижимы и подвижные элементы

# ячейки таблицы этапов
CELL_PART_ON = "#F6C3C5"
CELL_PART_ON_OTHER = "#FBE6E7"
CELL_MOV_ON = "#FBE0A0"
CELL_MOV_ON_OTHER = "#FDF3D8"
CELL_CURRENT = "#FFFFFF"
CELL_OTHER = "#F4F5F7"
CELL_LOCKED = "#E2E4E8"
CELL_GROUP = "#E9EBEE"
CELL_GROUP_CURRENT = "#DDE0E5"

VIEW_BG_BOTTOM = "#FFFFFF"
VIEW_BG_TOP = "#E6E9ED"

QSS = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}

/* верхняя панель */
QToolBar#topbar {{
    background: {GRAPHITE}; border: none; border-bottom: 3px solid {RED};
    padding: 6px 12px; spacing: 4px;
}}
QToolBar#topbar QToolButton {{
    color: #FFFFFF; background: transparent; border: none; border-radius: 6px; padding: 7px 12px;
}}
QToolBar#topbar QToolButton:hover {{ background: {GRAPHITE_3}; }}
QToolBar#topbar QToolButton:pressed {{ background: {GRAPHITE_2}; }}
QToolBar#topbar QToolButton#primaryTool {{ background: {RED}; font-weight: 600; }}
QToolBar#topbar QToolButton#primaryTool:hover {{ background: {RED_DARK}; }}
QToolBar#topbar QLabel#brand {{ color: #FFFFFF; font-size: 12pt; font-weight: 700; padding-right: 18px; }}
QToolBar#topbar::separator {{ background: {GRAPHITE_3}; width: 1px; margin: 6px 8px; }}

/* карточки */
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 12pt; font-weight: 700; }}
QLabel#stepTitle {{ font-size: 13pt; font-weight: 700; color: {RED}; }}
QLabel#hint {{ color: {MUTED}; }}
QLabel#fieldLabel {{ color: {MUTED}; font-weight: 600; }}
QLabel#chip {{ background: {RED_TINT}; color: {RED_DARK}; border-radius: 6px; padding: 5px 9px; }}
QLabel#chipMuted {{ background: {CELL_OTHER}; color: {MUTED}; border-radius: 6px; padding: 4px 8px; }}

/* кнопки */
QPushButton {{
    background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 14px; min-height: 18px;
}}
QPushButton:hover {{ border-color: {RED}; color: {RED}; }}
QPushButton:pressed {{ background: {RED_TINT}; }}
QPushButton:disabled {{ color: #B5B8BD; background: #F6F7F8; border-color: #E6E7EA; }}
QPushButton#primary {{ background: {RED}; color: #FFFFFF; border-color: {RED}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {RED_DARK}; border-color: {RED_DARK}; color: #FFFFFF; }}
QPushButton#nav {{ background: {GRAPHITE_2}; color: #FFFFFF; border-color: {GRAPHITE_2}; font-weight: 600; }}
QPushButton#nav:hover {{ background: {GRAPHITE_3}; border-color: {GRAPHITE_3}; color: #FFFFFF; }}
QPushButton#nav:disabled {{ background: #D7D9DD; border-color: #D7D9DD; color: #F7F7F7; }}

/* поля ввода */
QLineEdit, QComboBox {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {RED}; selection-color: #FFFFFF;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {RED}; }}
QComboBox QAbstractItemView {{
    background: {CARD}; border: 1px solid {BORDER}; outline: 0;
    selection-background-color: {RED_TINT}; selection-color: {TEXT};
}}
QCheckBox {{ spacing: 7px; }}

/* списки и таблицы */
QTreeWidget, QTableWidget {{
    background: {CARD}; border: none; gridline-color: {GRID}; outline: 0;
    alternate-background-color: #FAFAFB;
}}
QTreeWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:hover {{ background: #F5F6F8; }}
QTreeWidget::item:selected {{ background: {RED_TINT}; color: {TEXT}; }}
QHeaderView::section {{
    background: #F6F7F9; border: none; border-bottom: 1px solid {BORDER}; border-right: 1px solid {GRID};
    padding: 5px 4px;
}}
QTableCornerButton::section {{ background: #F6F7F9; border: none; border-bottom: 1px solid {BORDER}; }}

/* меню, подсказки, строка состояния */
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {RED_TINT}; color: {RED_DARK}; }}
QMenu::item:disabled {{ color: #B5B8BD; }}
QMenu::separator {{ height: 1px; background: {GRID}; margin: 4px 8px; }}
QToolTip {{ background: {GRAPHITE}; color: #FFFFFF; border: none; padding: 6px 8px; }}
QStatusBar {{ background: {CARD}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QSplitter::handle {{ background: transparent; }}

/* полосы прокрутки */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C7CBD1; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:horizontal {{ background: #C7CBD1; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #A8ADB4; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


def apply(app):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor(CARD))
    pal.setColor(QPalette.AlternateBase, QColor("#FAFAFB"))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(CARD))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(RED))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ToolTipBase, QColor(GRAPHITE))
    pal.setColor(QPalette.ToolTipText, QColor("#FFFFFF"))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(QSS)
