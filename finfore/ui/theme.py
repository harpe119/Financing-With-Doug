"""Visual theme — a dark "instrument panel" look.

One accent (mint-teal) reserved for money-positive and primary actions; amber
for projections; muted red for debt and outflows. All charts inherit the same
ink background so plots feel embedded, not pasted in.
"""
from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtGui import QColor

BG = "#101418"
BG_DARKER = "#0C1014"
SURFACE = "#171D24"
SURFACE2 = "#1E2630"
FIELD = "#12171D"
BORDER = "#2A3340"
TEXT = "#E8ECF1"
MUTED = "#8B98A9"
ACCENT = "#35C4A0"      # money / primary
ACCENT_DIM = "#1F4D41"
AMBER = "#E0A83C"       # deterministic projection
RED = "#E06C5B"         # debt / outflow
BLUE = "#5B9CE0"        # history
PURPLE = "#9B7EDE"      # investments

POSITIVE, NEGATIVE = ACCENT, RED


def qcolor(hexstr: str, alpha: int = 255) -> QColor:
    c = QColor(hexstr)
    c.setAlpha(alpha)
    return c


QSS = f"""
#cardToggle {{
    background: transparent;
    border: none;
    color: {MUTED};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.1px;
    text-align: left;
    padding: 2px 0;
}}
#cardToggle:hover {{ color: {TEXT}; }}

* {{
    font-family: "Segoe UI", "SF Pro Text", "Ubuntu", "DejaVu Sans", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#appTitle {{ font-size: 17px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#appTag {{ color: {MUTED}; font-size: 12px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#cardTitle {{
    color: {MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px;
}}
QLabel#statValue {{
    font-size: 23px; font-weight: 600;
    font-family: "Cascadia Code", "SF Mono", "Consolas", "DejaVu Sans Mono", monospace;
}}
QLabel#statSub {{ color: {MUTED}; font-size: 11px; }}

QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#hsep {{ background: {BORDER}; max-height: 1px; border: none; }}

QListWidget#nav {{
    background: {BG_DARKER}; border: none; padding: 6px; outline: 0;
}}
QListWidget#nav::item {{
    padding: 10px 14px; border-radius: 8px; color: #B9C2CE; margin: 2px 4px;
}}
QListWidget#nav::item:selected {{ background: #1B252E; color: {ACCENT}; }}
QListWidget#nav::item:hover:!selected {{ background: #141A21; }}

QPushButton {{
    background: {SURFACE2}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: #232D39; border-color: #3A4657; }}
QPushButton:pressed {{ background: #1A222B; }}
QPushButton:disabled {{ color: #5A6572; background: {SURFACE}; }}
QPushButton#primary {{
    background: {ACCENT_DIM}; border-color: {ACCENT};
    color: #D9F5EC; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #256052; }}
QPushButton#danger {{ border-color: {RED}; color: {RED}; }}
QPushButton#small {{ padding: 3px 9px; border-radius: 6px; font-size: 12px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 6px 8px; selection-background-color: #2E5E52;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {MUTED}; margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM}; outline: 0;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QDateEdit::up-button, QDateEdit::down-button {{
    background: {SURFACE2}; border: none; width: 16px;
}}

QTableWidget {{
    background: {FIELD}; alternate-background-color: #151B22;
    gridline-color: #202932; border: 1px solid {BORDER}; border-radius: 8px;
}}
QTableWidget::item {{ padding: 3px 6px; }}
QTableWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}
QHeaderView::section {{
    background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
    padding: 7px 6px; color: {MUTED}; font-weight: 700; font-size: 12px;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {BORDER}; background: {FIELD};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 5px; background: #232C37; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #2E5E52; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {ACCENT};
}}

QMenuBar {{ background: {BG_DARKER}; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: #1B252E; border-radius: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #3A4657; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 5px; min-width: 28px;
}}

/* Ledger reconciliation warning. Amber, not red: the data is suspect, nothing
   is broken, and the user is not in trouble. */
QLabel#ledgerWarning {{
    background: #2A2010; color: {TEXT}; border: 1px solid {AMBER};
    border-radius: 6px; padding: 9px 12px; font-size: 12px;
}}

/* The reading surface inside an InfoDialog — "for nerds" panels and the
   what-does-this-button-do explanations. Inset and slightly darker than the
   card behind it, so several hundred words read as a document rather than as
   more dashboard. */
QTextBrowser#infoBody {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 10px 12px;
}}

/* "A bill went up." News, not an error — nothing is broken and nothing was
   done wrong, so this is informational blue rather than the amber the ledger
   warning uses. It still has to catch the eye: the whole point is that the
   change was invisible on a statement. */
QLabel#billAlert {{
    background: #12202E; color: {TEXT}; border: 1px solid {BLUE};
    border-radius: 6px; padding: 8px 12px; font-size: 12px;
}}

/* Demo banner. Deliberately loud: a screen full of someone else's money must
   never be mistakable for your own, and a subtle grey note would be. */
QLabel#demoBanner {{
    background: {AMBER}; color: #1A1206; font-weight: 700; font-size: 12px;
    padding: 6px 14px; border-bottom: 1px solid #8A6415;
}}

/* Splitter handles have to look grabbable or nobody discovers they can drag.
   A flat gap reads as padding; a centred grip line reads as a control. */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    background: {BORDER};
}}
QSplitter::handle:vertical {{
    image: none; border-top: 4px solid transparent; border-bottom: 4px solid transparent;
    background: {BORDER};
}}
QSplitter::handle:hover {{ background: {ACCENT}; }}
QSplitter::handle:pressed {{ background: {ACCENT}; }}

QToolTip {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px;
}}
QStatusBar {{ background: {BG_DARKER}; color: {MUTED}; }}
QMessageBox {{ background: {SURFACE}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def apply(app) -> None:
    app.setStyleSheet(QSS)
    pg.setConfigOptions(background=BG, foreground=MUTED, antialias=True)
