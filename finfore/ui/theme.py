"""Visual theme — a dark "instrument panel" look.

One accent (mint-teal) reserved for money-positive and primary actions; amber
for projections; muted red for debt and outflows. All charts inherit the same
ink background so plots feel embedded, not pasted in.
"""
from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PyQt6.QtGui import QColor

@dataclass(frozen=True)
class Palette:
    """Every colour the interface uses, by what it is for.

    One object per theme. The stylesheet, the Qt palette and pyqtgraph are all
    built from it, so a second theme is a second Palette, not a hunt through the
    code for hex values. Names are roles, not hues: ``warning_wash`` is the fill
    behind an amber alert whatever colour that turns out to be in another theme.
    """
    # surfaces, darkest to lightest
    bg_darker: str          # rail, menu bar, tab strip, status bar
    bg: str                 # the page
    field: str              # inputs, tables, reading surfaces (inset)
    surface: str            # cards, dialogs
    surface2: str           # buttons and other raised controls
    border: str             # container edges, dividers
    border_strong: str      # edges of anything you can operate: >=3:1
    # text
    text: str
    muted: str              # labels, hints, axis text
    rail_text: str          # sidebar labels, a step below text
    disabled_text: str      # a control that cannot be used now: >=4.5:1 on surface
    # one job each (DESIGN.md, The One Job Rule)
    accent: str             # good, or do this
    accent_dim: str         # the fill partner of accent: primary buttons, chosen items
    selection: str          # a selected table row
    selection_text: str     # text on a selected row: >=4.5:1 on selection
    amber: str              # estimate, or look closer
    red: str                # money out, debt, destructive
    blue: str               # recorded history, information
    purple: str             # investments
    reference: str          # neutral reference lines such as the $0 floor
    comparison: str         # the "unchanged controls" line a scenario is read against
    giving: str             # charitable giving in the spending breakdown
    goal_mint: str          # the fourth goal colour on the funding map
    # states and washes
    hover_fill: str
    hover_edge: str
    pressed_fill: str
    primary_hover: str
    primary_text: str
    danger_hover_text: str
    rail_selected: str
    rail_hover: str
    text_selection: str
    alt_row: str
    grid: str
    slider_groove: str
    scroll_hover: str
    warning_wash: str       # behind an amber alert strip
    info_wash: str          # behind a blue alert strip
    banner_fill: str        # the demo bar itself: loud amber in every theme
    banner_ink: str         # text and button on the amber demo bar
    banner_edge: str
    banner_hover: str

    def qss_names(self) -> dict[str, str]:
        """The stylesheet's placeholders: each field, in capitals."""
        return {name.upper(): value for name, value in self.__dict__.items()}


DARK = Palette(
    bg_darker="#0C1014", bg="#101418", field="#12171D", surface="#171D24",
    surface2="#1E2630", border="#2A3340", border_strong="#5C6B80",
    text="#E8ECF1", muted="#8B98A9", rail_text="#B9C2CE", disabled_text="#7A8798",
    accent="#35C4A0", accent_dim="#1F4D41", selection="#277058", selection_text="#E8ECF1",
    amber="#E0A83C",
    red="#E06C5B", blue="#5B9CE0", purple="#9B7EDE", reference="#9AA4B2",
    comparison="#C7D0DB", giving="#4FB3A4", goal_mint="#7FD4C1",
    hover_fill="#232D39", hover_edge="#7A8CA4", pressed_fill="#1A222B",
    primary_hover="#256052", primary_text="#D9F5EC", danger_hover_text="#EE7F6F",
    rail_selected="#1B252E", rail_hover="#141A21", text_selection="#2E5E52",
    alt_row="#151B22", grid="#202932", slider_groove="#232C37", scroll_hover="#3A4657",
    warning_wash="#2A2010", info_wash="#12202E", banner_fill="#E0A83C",
    banner_ink="#1A1206", banner_edge="#8A6415", banner_hover="#2A200A",
)

# The same instrument in daylight: a cool off-white page, white cards, slate
# text, and the same five hues darkened until each reads at 4.5:1 or better on
# white. Tones step the same way as the dark theme (chrome, page, inset field,
# card, raised control) and there are still no shadows. Every pair is checked
# by tests/test_appearance.py.
LIGHT = Palette(
    bg_darker="#E9EDF2", bg="#F3F5F8", field="#F6F8FA", surface="#FFFFFF",
    surface2="#FBFCFD", border="#D6DCE4", border_strong="#7B8797",
    text="#1C2430", muted="#556274", rail_text="#2D3846", disabled_text="#606C7C",
    accent="#08735A", accent_dim="#D4EFE6", selection="#1F7A63", selection_text="#FFFFFF",
    amber="#9C5B00", red="#B53A2E", blue="#2563B5", purple="#6A4CC0", reference="#6B7685",
    comparison="#7E8998", giving="#147466", goal_mint="#2F9E82",
    hover_fill="#EEF2F6", hover_edge="#5E6B7C", pressed_fill="#E3E8EE",
    primary_hover="#C2E8DB", primary_text="#075842", danger_hover_text="#8F2A20",
    rail_selected="#DDEBE5", rail_hover="#E1E6EC", text_selection="#BFE4D8",
    alt_row="#F9FAFB", grid="#E6EAF0", slider_groove="#D3D9E1", scroll_hover="#A9B4C2",
    warning_wash="#FFF5E2", info_wash="#E8F1FB", banner_fill="#E0A83C",
    banner_ink="#1A1206", banner_edge="#8A6415", banner_hover="#2A200A",
)
PALETTES = {"dark": DARK, "light": LIGHT}
_current = DARK

# The names the rest of the app imports. They are the dark theme's values.
BG = DARK.bg
BG_DARKER = DARK.bg_darker
SURFACE = DARK.surface
SURFACE2 = DARK.surface2
FIELD = DARK.field
BORDER = DARK.border
BORDER_STRONG = DARK.border_strong
SELECTION = DARK.selection
TEXT = DARK.text
MUTED = DARK.muted
RAIL_TEXT = DARK.rail_text
BANNER_INK = DARK.banner_ink
BANNER_EDGE = DARK.banner_edge
DISABLED_TEXT = DARK.disabled_text
ACCENT = DARK.accent
ACCENT_DIM = DARK.accent_dim
AMBER = DARK.amber
RED = DARK.red
BLUE = DARK.blue
PURPLE = DARK.purple
REFERENCE = DARK.reference
COMPARISON = DARK.comparison
GIVING = DARK.giving
WARNING_WASH = DARK.warning_wash
INFO_WASH = DARK.info_wash
POSITIVE, NEGATIVE = ACCENT, RED

# Goals on the funding map are told apart by colour, in this order.
GOAL_COLORS = (ACCENT, BLUE, PURPLE, DARK.goal_mint, COMPARISON)

# The spending groups' colours, wherever they are drawn (donut, legend).
SPENDING_COLORS = {"Needs": BLUE, "Wants": AMBER, "Other wants": AMBER,
                   "Subscriptions": PURPLE, "Giving": GIVING, "Uncategorized": MUTED}
MONO_FAMILIES = ["Cascadia Code", "SF Mono", "Consolas", "DejaVu Sans Mono", "monospace"]


def mono_font(px: int = 13, bold: bool = True):
    """The readout face as a QFont, for table cells and other painted text."""
    from PyQt6.QtGui import QFont
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return f


def current() -> Palette:
    return _current


# Every colour any palette uses, to the role it plays there. The first role
# declared wins when two share a value (text before selection_text), which is
# the one inline styles and stored colours mean.
_ROLE_OF: dict[str, str] = {}
for _palette in (DARK, LIGHT):
    for _role, _value in _palette.__dict__.items():
        _ROLE_OF.setdefault(_value.upper(), _role)

_NAMES = {"BG": "bg", "BG_DARKER": "bg_darker", "SURFACE": "surface",
          "SURFACE2": "surface2", "FIELD": "field", "BORDER": "border",
          "BORDER_STRONG": "border_strong", "SELECTION": "selection", "TEXT": "text",
          "MUTED": "muted", "RAIL_TEXT": "rail_text", "BANNER_INK": "banner_ink",
          "BANNER_EDGE": "banner_edge", "DISABLED_TEXT": "disabled_text",
          "ACCENT": "accent", "ACCENT_DIM": "accent_dim", "AMBER": "amber", "RED": "red",
          "BLUE": "blue", "PURPLE": "purple", "REFERENCE": "reference",
          "COMPARISON": "comparison", "GIVING": "giving", "WARNING_WASH": "warning_wash",
          "INFO_WASH": "info_wash"}


def live(color):
    """A colour taken from any palette, as the current palette has it.

    Widgets that stored a colour when they were built (a legend swatch, a status
    word) keep working across a theme switch: the stored value names a role, and
    the role is looked up again. Anything that is not a palette colour passes through.
    """
    if not isinstance(color, str):
        return color
    role = _ROLE_OF.get(color.upper())
    return getattr(_current, role) if role else color


def live_stylesheet(sheet: str) -> str:
    """An inline stylesheet with every palette colour moved to the current palette."""
    import re
    return re.sub(r"#[0-9A-Fa-f]{6}\b", lambda m: live(m.group(0)), sheet)


def use(palette: Palette, app=None) -> None:
    """Make ``palette`` the current one: the names, the stylesheet, Qt and pyqtgraph.

    Code reads colours as ``theme.ACCENT`` at the moment it draws, so rebinding
    the names here is what makes a redraw come out in the new theme.
    """
    global _current, POSITIVE, NEGATIVE, GOAL_COLORS, SPENDING_COLORS, QSS
    _current = palette
    g = globals()
    for name, role in _NAMES.items():
        g[name] = getattr(palette, role)
    POSITIVE, NEGATIVE = palette.accent, palette.red
    GOAL_COLORS = (palette.accent, palette.blue, palette.purple, palette.goal_mint, palette.comparison)
    SPENDING_COLORS = {"Needs": palette.blue, "Wants": palette.amber, "Other wants": palette.amber,
                       "Subscriptions": palette.purple, "Giving": palette.giving,
                       "Uncategorized": palette.muted}
    QSS = stylesheet(palette)
    if app is not None:
        apply(app, palette)


def qcolor(hexstr: str, alpha: int = 255) -> QColor:
    c = QColor(live(hexstr))
    c.setAlpha(alpha)
    return c


_QSS_TEMPLATE = """
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
QLabel#verdict {{ font-size: 17px; font-weight: 600; }}
QLabel#welcomeTitle {{ font-size: 20px; font-weight: 600; }}
QLabel#pageTitle {{ font-size: 17px; font-weight: 600; }}
/* A page's one result line, a dialog heading: DESIGN.md's Headline. */
QLabel#headline {{ font-size: 17px; font-weight: 600; }}
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
    padding: 10px 14px; border-radius: 8px; margin: 2px 4px;
}}
QListWidget#nav::item:selected {{ background: {RAIL_SELECTED}; color: {ACCENT}; }}
QListWidget#nav::item:hover:!selected {{ background: {RAIL_HOVER}; }}

QPushButton {{
    background: {SURFACE2}; border: 1px solid {BORDER_STRONG};
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton:hover {{ background: {HOVER_FILL}; border-color: {HOVER_EDGE}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; background: {HOVER_FILL}; }}
QPushButton:pressed {{ background: {PRESSED_FILL}; }}
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled,
QPushButton#dangerSmall:disabled {{
    color: {DISABLED_TEXT}; background: {SURFACE}; border: 1px solid {BORDER};
    font-weight: 400;
}}
QPushButton#primary {{
    background: {ACCENT_DIM}; border-color: {ACCENT};
    color: {PRIMARY_TEXT}; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#primary:focus {{ border: 1px solid {TEXT}; background: {PRIMARY_HOVER}; }}
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
/* A chosen range button already has the teal edge, so focus has to be something else. */
QPushButton#range:checked:focus {{ border: 1px solid {TEXT}; }}
QPushButton#danger {{ border-color: {RED}; color: {RED}; }}
QPushButton#small {{ padding: 3px 9px; border-radius: 6px; font-size: 12px; }}
QPushButton#danger:hover, QPushButton#danger:focus {{ color: {DANGER_HOVER_TEXT}; }}
QPushButton#danger:focus {{ border: 1px solid {TEXT}; }}
/* A destructive button in a row of small ones: the same coral, the compact size.
   One objectName per widget, so it is its own name rather than danger plus small. */
QPushButton#dangerSmall {{
    border-color: {RED}; color: {RED}; padding: 3px 9px; border-radius: 6px; font-size: 12px;
}}
QPushButton#dangerSmall:hover, QPushButton#dangerSmall:focus {{ color: {DANGER_HOVER_TEXT}; }}
QPushButton#dangerSmall:focus {{ border: 1px solid {TEXT}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {FIELD}; border: 1px solid {BORDER_STRONG}; border-radius: 7px;
    padding: 6px 8px; selection-background-color: {TEXT_SELECTION};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus {{ border-color: {ACCENT}; }}
/* An entry that cannot be edited right now reads as unavailable, like a disabled button. */
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QDateEdit:disabled {{
    color: {DISABLED_TEXT}; background: {SURFACE}; border: 1px solid {BORDER};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {MUTED}; margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM}; selection-color: {TEXT}; outline: 0;
}}
/* Qt's file browser uses tree/list views rather than our ledger tables. */
QFileDialog QTreeView, QFileDialog QListView {{
    background: {FIELD}; alternate-background-color: {SURFACE}; color: {TEXT};
    selection-background-color: {SELECTION}; selection-color: {SELECTION_TEXT};
    border: 1px solid {BORDER}; border-radius: 6px;
}}
QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected {{
    background: {SELECTION}; color: {SELECTION_TEXT};
}}
/* Steppers sit inside the field's rounded edge and show which way they go; they
   used to be a blank slab over the corner, with nothing to say a click there
   changes the number. */
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 18px;
    background: transparent; border: none; margin: 1px 1px 0 0; border-top-right-radius: 6px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 18px;
    background: transparent; border: none; margin: 0 1px 1px 0; border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QDateEdit::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,
QDateEdit::down-button:hover {{ background: {HOVER_FILL}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow {{
    image: none; width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-bottom: 4px solid {MUTED};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow {{
    image: none; width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 4px solid {MUTED};
}}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off, QDoubleSpinBox::up-arrow:disabled,
QDoubleSpinBox::up-arrow:off, QDateEdit::up-arrow:disabled,
QDateEdit::up-arrow:off {{ border-bottom-color: {BORDER}; }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off, QDoubleSpinBox::down-arrow:disabled,
QDoubleSpinBox::down-arrow:off, QDateEdit::down-arrow:disabled,
QDateEdit::down-arrow:off {{ border-top-color: {BORDER}; }}
/* A date with a calendar opens like a drop-down list, so it is drawn like one. */
QDateEdit::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right; width: 22px; border: none;
}}
QDateEdit::down-arrow:enabled {{ margin-right: 7px; border-top: 5px solid {MUTED};
    border-left: 4px solid transparent; border-right: 4px solid transparent; }}

/* Entries listed inside one card are parted by a hairline, never a card each. */
QFrame#listEntry {{ background: transparent; border: none; border-top: 1px solid {BORDER}; }}
QFrame#listEntryFirst {{ background: transparent; border: none; }}

QTableWidget {{
    background: {FIELD}; alternate-background-color: {ALT_ROW};
    gridline-color: {GRID}; border: 1px solid {BORDER}; border-radius: 8px;
}}
QTableWidget::item {{ padding: 3px 6px; }}
QTableWidget::item:selected {{ background: {SELECTION}; color: {SELECTION_TEXT}; }}
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

QSlider::groove:horizontal {{ height: 5px; background: {SLIDER_GROOVE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {TEXT_SELECTION}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {ACCENT};
}}
QSlider::handle:horizontal:focus {{ background: {TEXT}; }}

QMenuBar {{ background: {BG_DARKER}; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {RAIL_SELECTED}; border-radius: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}

QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{ background: {BG_DARKER}; color: {MUTED}; padding: 8px 18px; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
/* The selected tab already has the teal underline, so a focused tab is boxed as well. */
QTabBar::tab:focus {{ border: 1px solid {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QCalendarWidget QWidget {{ background: {SURFACE}; color: {TEXT}; }}
QCalendarWidget QToolButton {{ background: {SURFACE2}; color: {TEXT}; border: none; padding: 4px; }}
QCalendarWidget QAbstractItemView {{ background: {FIELD}; selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 5px; min-width: 28px;
}}

/* Ledger reconciliation warning. Amber, not red: the data is suspect, nothing
   is broken, and the user is not in trouble. */
QLabel#ledgerWarning {{
    background: {WARNING_WASH}; color: {TEXT}; border: 1px solid {AMBER};
    border-radius: 6px; padding: 9px 12px; font-size: 12px;
}}

/* The data-quality line when nothing was flagged: quiet, like a hint. */
QLabel#qualityOk {{ color: {MUTED}; font-size: 12px; padding: 6px 0; }}

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
    background: {INFO_WASH}; color: {TEXT}; border: 1px solid {BLUE};
    border-radius: 6px; padding: 8px 12px; font-size: 12px;
}}

/* Demo banner. Deliberately loud: a screen full of someone else's money must
   never be mistakable for your own, and a subtle grey note would be. */
QFrame#demoBannerBar {{ background: {BANNER_FILL}; border-bottom: 1px solid {BANNER_EDGE}; }}
QLabel#demoBanner {{
    background: transparent; color: {BANNER_INK}; font-weight: 700; font-size: 12px;
    padding: 6px 14px;
}}
/* The one button on the bar: dark on the amber, so it reads as part of it. */
QPushButton#bannerButton {{
    background: {BANNER_INK}; color: {BANNER_FILL}; border: 1px solid {BANNER_EDGE};
    border-radius: 6px; padding: 3px 10px; font-size: 12px; font-weight: 700;
}}
QPushButton#bannerButton:hover {{ background: {BANNER_HOVER}; }}
QPushButton#bannerButton:focus {{ border: 1px solid {TEXT}; }}

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


def stylesheet(palette: Palette) -> str:
    """The whole application stylesheet, for one palette."""
    return _QSS_TEMPLATE.format(**palette.qss_names())


QSS = stylesheet(DARK)


def apply(app, palette: Palette = DARK) -> None:
    from PyQt6.QtGui import QPalette
    p = palette
    pal = app.palette()
    # Unstyled Qt controls (including file browsers) must inherit both sides
    # of the dark theme, rather than light text on a system-white background.
    for role, color in (
        (QPalette.ColorRole.Window, p.bg),
        (QPalette.ColorRole.WindowText, p.text),
        (QPalette.ColorRole.Base, p.field),
        (QPalette.ColorRole.AlternateBase, p.surface),
        (QPalette.ColorRole.Text, p.text),
        (QPalette.ColorRole.Button, p.surface2),
        (QPalette.ColorRole.ButtonText, p.text),
        (QPalette.ColorRole.Highlight, p.selection),
        (QPalette.ColorRole.HighlightedText, p.selection_text),
        (QPalette.ColorRole.ToolTipBase, p.surface),
        (QPalette.ColorRole.ToolTipText, p.text),
        (QPalette.ColorRole.PlaceholderText, p.muted),
        (QPalette.ColorRole.Link, p.blue),
        (QPalette.ColorRole.LinkVisited, p.purple),
    ):
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.muted))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(p))
    pg.setConfigOptions(background=p.bg, foreground=p.muted, antialias=True)
