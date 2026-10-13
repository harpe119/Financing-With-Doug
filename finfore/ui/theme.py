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
BORDER_STRONG = "#5C6B80"   # control edges: >=3:1 against page, card and field
SELECTION = "#277058"       # a selected row: 3:1 against the field, text on it 5:1
TEXT = "#E8ECF1"
MUTED = "#8B98A9"
ACCENT = "#35C4A0"      # money / primary
ACCENT_DIM = "#1F4D41"
AMBER = "#E0A83C"       # deterministic projection
RED = "#E06C5B"         # debt / outflow
BLUE = "#5B9CE0"        # history
PURPLE = "#9B7EDE"      # investments

POSITIVE, NEGATIVE = ACCENT, RED
REFERENCE = "#9AA4B2"   # neutral reference lines ($0 floor): not a money meaning
MONO_FAMILIES = ["Cascadia Code", "SF Mono", "Consolas", "DejaVu Sans Mono", "monospace"]


def mono_font(px: int = 13, bold: bool = True):
    """The readout face as a QFont, for table cells and other painted text."""
    from PyQt6.QtGui import QFont
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return f


def qcolor(hexstr: str, alpha: int = 255) -> QColor:
    c = QColor(hexstr)
    c.setAlpha(alpha)
    return c


QSS = f"""
#cardToggle {{
    background: transparent;
    color: {MUTED};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
    text-align: left;
    padding: 2px 4px;
    border: 1px solid transparent;
    border-radius: 6px;
}}
#cardToggle:hover {{ color: {TEXT}; }}
#cardToggle:focus {{ color: {TEXT}; border: 1px solid {ACCENT}; }}

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
    font-size: 21px; font-weight: 600;
    font-family: "Cascadia Code", "SF Mono", "Consolas", "DejaVu Sans Mono", monospace;
}}
QLabel#statHero {{
    font-size: 30px; font-weight: 600;
    font-family: "Cascadia Code", "SF Mono", "Consolas", "DejaVu Sans Mono", monospace;
}}
QLabel#statSub {{ color: {MUTED}; font-size: 12px; }}
QLabel#statOp {{ color: {MUTED}; font-size: 18px; }}
QLabel#verdict {{ font-size: 16px; font-weight: 600; }}
QLabel#welcomeTitle {{ font-size: 20px; font-weight: 600; }}
QLabel#pageTitle {{ font-size: 17px; font-weight: 600; }}
QFrame#selBar {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 10px;
}}
QLabel#stepTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#stepDone {{ color: {ACCENT}; font-size: 14px; font-weight: 700; }}
QLabel#staleChip {{ color: {AMBER}; font-size: 12px; }}

QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#verdictBand {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#verdictBand[caution="true"] {{ border: 1px solid {AMBER}; }}
QFrame#hsep {{ background: {BORDER}; max-height: 1px; border: none; }}

QListWidget#nav {{
    background: {BG_DARKER}; border: 1px solid transparent; padding: 6px; outline: 0;
}}
QListWidget#nav:focus {{ border: 1px solid {ACCENT}; }}
QListWidget#nav::item {{
    padding: 10px 14px; border-radius: 8px; color: #B9C2CE; margin: 2px 4px;
}}
QListWidget#nav::item:selected {{ background: #1B252E; color: {ACCENT}; }}
QListWidget#nav::item:hover:!selected {{ background: #141A21; }}

QPushButton {{
    background: {SURFACE2}; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: #232D39; border-color: #7A8CA4; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; background: #232D39; }}
QPushButton:pressed {{ background: #1A222B; }}
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled {{
    color: #7A8798; background: {SURFACE}; border: 1px solid {BORDER};
    font-weight: 400;
}}
QPushButton#primary {{
    background: {ACCENT_DIM}; border-color: {ACCENT};
    color: #D9F5EC; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #256052; }}
QPushButton#primary:focus {{ border: 1px solid {TEXT}; background: #256052; }}
QPushButton#link {{
    background: transparent; border: 1px solid transparent; color: {ACCENT};
    padding: 4px 6px; border-radius: 6px;
}}
QPushButton#link:hover {{ color: {TEXT}; background: transparent; }}
QPushButton#link:focus {{ border: 1px solid {ACCENT}; background: transparent; }}
QPushButton#range {{
    padding: 2px 10px; border-radius: 6px; font-size: 12px; color: {MUTED};
}}
QPushButton#range:checked {{ background: {ACCENT_DIM}; color: {TEXT}; border-color: {ACCENT}; }}
QPushButton#danger {{ border-color: {RED}; color: {RED}; }}
QPushButton#small {{ padding: 3px 9px; border-radius: 6px; font-size: 12px; }}
QPushButton#danger:hover, QPushButton#danger:focus {{ color: #EE7F6F; }}
QPushButton#danger:focus {{ border: 1px solid {TEXT}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {FIELD}; border: 1px solid {BORDER_STRONG}; border-radius: 7px;
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
/* Qt's file browser uses tree/list views rather than our ledger tables. */
QFileDialog QTreeView, QFileDialog QListView {{
    background: {FIELD}; alternate-background-color: {SURFACE}; color: {TEXT};
    selection-background-color: {SELECTION}; selection-color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px;
}}
QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected {{
    background: {SELECTION}; color: {TEXT};
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
QTableWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
QTableWidget:focus {{ border: 1px solid {ACCENT}; }}
QTableWidget::item:disabled {{ background: transparent; }}
QHeaderView::section {{
    background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
    padding: 7px 6px; color: {MUTED}; font-weight: 700; font-size: 12px;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QCheckBox {{
    spacing: 7px; padding: 2px; border: 1px solid transparent; border-radius: 6px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {BORDER_STRONG}; background: {FIELD};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox:focus {{ border: 1px solid {ACCENT}; }}

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

QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{ background: {BG_DARKER}; color: {MUTED}; padding: 8px 18px; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:focus {{ border-bottom: 2px solid {ACCENT}; }}
QCalendarWidget QWidget {{ background: {SURFACE}; color: {TEXT}; }}
QCalendarWidget QToolButton {{ background: {SURFACE2}; color: {TEXT}; border: none; padding: 4px; }}
QCalendarWidget QAbstractItemView {{ background: {FIELD}; selection-background-color: {ACCENT_DIM}; }}

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
    background: {BORDER}; background-clip: content;
}}
QSplitter::handle:vertical {{
    image: none; border-top: 4px solid transparent; border-bottom: 4px solid transparent;
    background: {BORDER}; background-clip: content;
}}
QSplitter::handle:hover {{ background: {ACCENT}; background-clip: content; }}
QSplitter::handle:pressed {{ background: {ACCENT}; background-clip: content; }}

QToolTip {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px;
}}
QStatusBar {{ background: {BG_DARKER}; color: {MUTED}; }}
QMessageBox {{ background: {SURFACE}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def apply(app) -> None:
    from PyQt6.QtGui import QPalette
    pal = app.palette()
    # Unstyled Qt controls (including file browsers) must inherit both sides
    # of the dark theme, rather than light text on a system-white background.
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, FIELD),
        (QPalette.ColorRole.AlternateBase, SURFACE),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, SURFACE2),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, SELECTION),
        (QPalette.ColorRole.HighlightedText, TEXT),
        (QPalette.ColorRole.ToolTipBase, SURFACE),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
        (QPalette.ColorRole.Link, BLUE),
        (QPalette.ColorRole.LinkVisited, PURPLE),
    ):
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
    pg.setConfigOptions(background=BG, foreground=MUTED, antialias=True)
