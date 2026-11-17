"""Quiet dark surfaces and a consistent green accent."""

ACCENT = "#3ddc84"
ACCENT_DARK = "#2a9d5f"
BG = "#15191e"
BG_ALT = "#1b2128"
CARD = "#222a33"
BORDER = "#34404d"
TEXT = "#e8e8e8"
MUTED = "#9aa0a6"
WARN = "#f1c40f"
DANGER = "#e74c3c"

STYLESHEET = f"""
* {{ font-size: 10pt; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QFrame#Header {{ background: {BG_ALT}; border-bottom: 1px solid {BORDER}; }}
QLabel#AppTitle {{ font-size: 16pt; font-weight: 700; color: {ACCENT}; background: transparent; }}
QLabel#Muted, QLabel.muted {{ color: {MUTED}; background: transparent; }}
QLabel {{ background: transparent; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: {BG_ALT}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 13px 20px; font-size: 10pt; font-weight: 600;
               border-bottom: 3px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 3px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#Card QLabel {{ background: transparent; }}
QLabel#CardTitle {{ font-size: 12pt; font-weight: 700; }}
QLabel#BigValue {{ font-size: 18pt; font-weight: 700; }}
QLabel#PageTitle {{ font-size: 15pt; font-weight: 700; }}
QListWidget#Sidebar {{ background: {BG_ALT}; border: none; border-right: 1px solid {BORDER}; padding: 6px; outline: 0; }}
QListWidget#Sidebar::item {{ padding: 10px 8px; border-radius: 6px; margin: 2px 0; }}
QListWidget#Sidebar::item:selected {{ background: {CARD}; color: {ACCENT}; border-left: 3px solid {ACCENT}; }}
QListWidget#Sidebar::item:hover:!selected {{ background: {CARD}; }}
QPushButton {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BG_ALT}; }}
QPushButton:disabled {{ color: #666; border-color: #333; }}
QPushButton#Primary {{ background: {ACCENT_DARK}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT}; color: #102015; }}
QPushButton#Danger {{ background: #7a2a22; border-color: {DANGER}; color: white; font-weight: 600; }}
QPushButton#Danger:hover {{ background: {DANGER}; }}
QPushButton#Segment {{ border-radius: 0; padding: 7px 22px; }}
QPushButton#Segment:checked {{ background: {ACCENT_DARK}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QSpinBox, QComboBox, QLineEdit {{ background: {BG_ALT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px; }}
QComboBox QAbstractItemView {{ background: {CARD}; selection-background-color: {ACCENT_DARK}; }}
QCheckBox::indicator {{ width: 38px; height: 20px; border-radius: 10px; background: {BORDER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; }}
QTreeWidget {{ background: {BG_ALT}; border: 1px solid {BORDER}; border-radius: 8px; alternate-background-color: {CARD}; }}
QTreeWidget::item {{ padding: 3px; }}
QTreeWidget::item:selected {{ background: {ACCENT_DARK}; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none; padding: 6px; font-weight: 600; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 4px; height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QPlainTextEdit {{ background: {BG_ALT}; border: 1px solid {BORDER}; border-radius: 6px; font-family: monospace; font-size: 9pt; }}
QStatusBar {{ background: {BG_ALT}; color: {MUTED}; }}
"""


def temp_color(c: float | None) -> str:
    if c is None:
        return MUTED
    if c < 60:
        return ACCENT
    if c < 80:
        return WARN
    return DANGER
