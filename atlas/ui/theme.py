"""Application stylesheet (dark by default) and reader colour schemes."""
from __future__ import annotations

DARK = {
    "bg": "#1b1d21", "panel": "#22252a", "panel2": "#292d33", "border": "#363b43", "text": "#d8dce2",
    "muted": "#8b93a1", "accent": "#5b9bd5", "accent_text": "#ffffff", "hover": "#30353d", "select": "#2f4a68",
    "danger": "#d9534f", "ok": "#5cb85c", "warn": "#e0a030", "mark": "#f2c94c",
}
LIGHT = {
    "bg": "#f4f5f7", "panel": "#ffffff", "panel2": "#eceef1", "border": "#cfd3d9", "text": "#1f2328",
    "muted": "#636c76", "accent": "#2a6fb5", "accent_text": "#ffffff", "hover": "#e3e6ea", "select": "#cfe2f7",
    "danger": "#c9302c", "ok": "#2e8b3d", "warn": "#b36b00", "mark": "#fff2a8",
}
READER_THEMES = {
    "light": {"bg": "#fbfbf9", "fg": "#1d1d1d", "link": "#1a5fb4"},
    "sepia": {"bg": "#f4ecd8", "fg": "#433422", "link": "#7a4a14"},
    "dark": {"bg": "#16181c", "fg": "#cfd3d8", "link": "#79b0e8"},
}


def palette(name: str) -> dict[str, str]:
    return LIGHT if name == "light" else DARK


def stylesheet(name: str = "dark") -> str:
    c = palette(name)
    return f"""
* {{ font-family: "Segoe UI", sans-serif; font-size: 10pt; }}
QMainWindow, QDialog, QWizard {{ background: {c['bg']}; color: {c['text']}; }}
QWidget {{ color: {c['text']}; }}
QWidget#page, QStackedWidget {{ background: {c['bg']}; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 16pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#muted {{ color: {c['muted']}; }}
QFrame#card {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; }}
QFrame#sidebar {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 14px; border-radius: 5px; margin: 1px 6px; }}
QListWidget#nav::item:hover {{ background: {c['hover']}; }}
QListWidget#nav::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit {{
    background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 4px 6px;
    selection-background-color: {c['select']}; selection-color: {c['text']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; border: 1px solid {c['border']}; selection-background-color: {c['select']}; }}
QPushButton, QToolButton {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 5px 12px; }}
QToolButton {{ padding: 4px 8px; }}
QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; }}
QPushButton:focus, QToolButton:focus, QListWidget:focus, QTreeView:focus, QTableView:focus, QListView:focus {{ border: 1px solid {c['accent']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; background: {c['panel']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QToolButton:checked {{ background: {c['select']}; border: 1px solid {c['accent']}; }}
QTableView, QTreeView, QListView, QListWidget, QTreeWidget, QTableWidget {{ background: {c['panel']}; alternate-background-color: {c['panel2']};
    border: 1px solid {c['border']}; border-radius: 4px; selection-background-color: {c['select']}; selection-color: {c['text']}; outline: 0; }}
QTableView::item:selected, QListView::item:selected, QTreeView::item:selected, QListWidget::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QTableView::item:selected:!active, QListView::item:selected:!active, QTreeView::item:selected:!active {{ background: {c['select']}; color: {c['text']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['border']}; border-radius: 3px; background: {c['panel']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border: 1px solid {c['accent']}; image: none; }}
QHeaderView::section {{ background: {c['panel2']}; border: none; border-right: 1px solid {c['border']}; padding: 5px 8px; font-weight: 600; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 4px; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; border: 1px solid transparent; }}
QTabBar::tab:selected {{ background: {c['panel']}; border: 1px solid {c['border']}; border-bottom-color: {c['panel']}; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
QTabBar::tab:hover {{ background: {c['hover']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 5px; margin-top: 12px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; }}
QProgressBar {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: 4px; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
QMenuBar {{ background: {c['panel']}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {c['select']}; }}
QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px; border-radius: 3px; }}
QToolTip {{ background: {c['panel2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: {c['border']}; }}
QCheckBox:focus, QRadioButton:focus {{ color: {c['accent']}; }}
"""
