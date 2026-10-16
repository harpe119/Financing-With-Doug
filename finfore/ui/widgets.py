"""Small reusable widgets."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import isfinite

from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QPainter, QPen, QValidator
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFrame,
                             QHBoxLayout, QLabel, QLayout, QMenu, QPushButton,
                             QSizePolicy, QSpinBox, QSplitter, QTextBrowser,
                             QStyle, QStyleOptionButton, QStylePainter,
                             QVBoxLayout, QWidget)

from . import theme
from ..util import fmt_money


class FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new row when it runs out of width.

    Qt ships nothing like this, and a QHBoxLayout cannot shrink below the sum
    of its children's minimum widths — it forces its parent wider instead. A
    nine-entry chart legend in an HBox is therefore enough on its own to push a
    page past the window and start overlapping whatever sits beside it. Wrapping
    turns "too narrow" into "two rows" instead.
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:            # noqa: N802 (Qt override)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):               # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):               # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):              # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:        # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:        # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:                # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:             # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_h = area.x(), area.y(), 0
        for item in self._items:
            w = item.sizeHint()
            if row_h and x + w.width() > area.right() + 1:
                x = area.x()
                y += row_h + self._spacing
                row_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), w))
            x += w.width() + self._spacing
            row_h = max(row_h, w.height())
        return y + row_h - rect.y() + m.bottom()


class WrappedCheckBox(QCheckBox):
    """A native checkbox whose caption can wrap in a narrow container."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.caption = QLabel(text, self)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        self.caption.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._caption_layout = QHBoxLayout(self)
        self._caption_layout.addWidget(self.caption)
        self._caption_layout.setSpacing(0)
        self._update_caption_margin()
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _update_caption_margin(self) -> None:
        style = self.style()
        inset = (style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self)
                 + style.pixelMetric(QStyle.PixelMetric.PM_CheckBoxLabelSpacing, None, self))
        self._caption_layout.setContentsMargins(inset + 2, 2, 2, 2)

    def sizeHint(self) -> QSize:
        size = self._caption_layout.sizeHint()
        size.setHeight(self.heightForWidth(self.width()))
        return size

    def minimumSizeHint(self) -> QSize:
        return self._caption_layout.minimumSize()

    def heightForWidth(self, width: int) -> int:
        indicator = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, None, self)
        return max(indicator + 4, self._caption_layout.totalHeightForWidth(width))

    def paintEvent(self, event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_CheckBox, option)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (hasattr(self, "_caption_layout") and event.type() in
                (QEvent.Type.StyleChange, QEvent.Type.FontChange)):
            self._update_caption_margin()
            self.updateGeometry()


class _SelectOnClickMixin:
    """Make the first click (or tab) into a spin box replace its value.

    A spin box pre-filled with ``0.00`` behaves like a text field: clicking it
    drops a caret next to a zero the user then has to notice and delete, so a
    typed ``71520.00`` silently lands as ``71520.000.00``-ish garbage or, worse,
    a plausible-looking wrong number. Selecting the existing value on entry
    makes typing replace it, which is what every other "enter an amount here"
    field in the world does. Clicks on the step arrows are left alone so they
    still step.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending_select = False

    def focusInEvent(self, event) -> None:      # noqa: N802
        super().focusInEvent(event)
        self._pending_select = True
        QTimer.singleShot(0, self._select_if_pending)

    def _select_if_pending(self) -> None:
        if self._pending_select:
            self._pending_select = False
            self.selectAll()

    def mousePressEvent(self, event) -> None:   # noqa: N802
        first = self._pending_select
        self._pending_select = False
        super().mousePressEvent(event)
        # Only when the click landed on the text, not on the up/down arrows —
        # otherwise stepping would keep re-selecting and fight the user.
        if first and self.lineEdit().geometry().contains(event.pos()):
            self.selectAll()


class MoneySpinBox(_SelectOnClickMixin, QDoubleSpinBox):
    """Currency uses fixed cents entry; percentage fields use normal decimals.

    Currency keystrokes shift the digits: 1 → $0.01, 12 → $0.12, 123 → $1.23.
    Values are committed on Enter or focus loss. Programmatic setValue calls
    still take dollars, and pasted formatted amounts retain their dollar value.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Don't re-read the text on every keystroke; wait for Enter or focus
        # loss. This is what stops the field fighting the cursor.
        self.setKeyboardTracking(False)
        self.lineEdit().installEventFilter(self)
        self.lineEdit().textEdited.connect(self._format_paste)

    def _currency_mode(self) -> bool:
        return "$" in self.prefix() and self.decimals() == 2

    def _write_cents(self, cents: int, negative: bool = False) -> None:
        amount = (-cents if negative else cents) / 100
        amount = min(self.maximum(), max(self.minimum(), amount))
        text = self.textFromValue(amount)
        # Keep a typed minus sign at zero until the next digit arrives.
        if negative and amount == 0 and self.minimum() < 0 and not text.startswith("-"):
            text = "-" + text
        self.lineEdit().setText(self.prefix() + text + self.suffix())
        self.lineEdit().setCursorPosition(len(self.lineEdit().text()) - len(self.suffix()))

    def _money_key(self, event) -> bool:
        if not self._currency_mode() or event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
                | Qt.KeyboardModifier.AltModifier):
            return False
        key, char = event.key(), event.text()
        if char not in "0123456789-+.,$ " and key not in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            return False
        # Empty event text (e.g. Enter) is not a currency character.
        if not char and key not in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            return False
        self._pending_select = False
        if char in (".", ",", "$", " "):
            return True
        editor = self.lineEdit()
        core = self._core(editor.text())
        try:
            value = Decimal(core or "0")
            cents = int(abs(value) * 100)
        except (InvalidOperation, ValueError, OverflowError):
            cents = 0
        negative = core.startswith("-")
        selected = editor.hasSelectedText()
        if char.isdigit() and char.isascii():
            cents = (0 if selected else cents) * 10 + int(char)
            negative = negative and not selected
        elif key == Qt.Key.Key_Backspace:
            cents = 0 if selected else cents // 10
        elif key == Qt.Key.Key_Delete:
            cents = 0
        elif char in ("-", "+"):
            if selected:
                cents = 0
            negative = char == "-" and self.minimum() < 0
        # Decimal and grouping keys leave the fixed decimal position intact.
        self._write_cents(cents, negative)
        return True

    def keyPressEvent(self, event) -> None:
        if self._money_key(event):
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.KeyPress:
            if self._money_key(event):
                return True
        return super().eventFilter(watched, event)

    def _format_paste(self, text: str) -> None:
        if self._currency_mode() and self.validate(text, len(text))[0] == QValidator.State.Acceptable:
            value = self.valueFromText(text)
            self._write_cents(round(abs(value) * 100), value < 0)

    def _core(self, text: str) -> str:
        """The number part: prefix, suffix, currency and separators removed."""
        s = str(text)
        for fix in (self.prefix(), self.suffix()):
            if fix and fix.strip() and fix.strip() in s:
                s = s.replace(fix.strip(), "")
        return s.replace("$", "").replace(",", "").replace(" ", "").strip()

    def validate(self, text, pos):               # noqa: N802
        core = self._core(text)
        if core in ("", "-", "+", ".", "-.", "+."):
            return (QValidator.State.Intermediate, text, pos)
        try:
            value = Decimal(core)
            if not value.is_finite() or not isfinite(float(value)):
                raise ValueError("non-finite amount")
        except (ValueError, InvalidOperation):
            return (QValidator.State.Invalid, text, pos)
        return (QValidator.State.Acceptable, text, pos)

    def valueFromText(self, text) -> float:      # noqa: N802
        core = self._core(text)
        try:
            value = float(core)
            if not isfinite(value):
                return float(self.value())
            if self._currency_mode() and "." not in core and "," not in text:
                value /= 100
            return value
        except ValueError:
            return float(self.value())

    def fixup(self, text) -> str:                # noqa: N802
        """Turn an abandoned half-number into something valid."""
        core = self._core(text)
        return "0" if core in ("", "-", "+", ".", "-.", "+.") else core


class IntSpinBox(_SelectOnClickMixin, QSpinBox):
    pass


class DonutChart(QWidget):
    """A donut/pie chart drawn with QPainter (no plotting dependency).

    slices: list of (label, value, hex_color). The center shows an optional
    caption and total. Zero/empty renders a muted placeholder ring.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slices: list[tuple[str, float, str]] = []
        self._center_top = ""
        self._center_bottom = ""
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, slices, center_top: str = "", center_bottom: str = ""):
        self._slices = [(l, float(v), c) for l, v, c in slices if v > 0]
        self._center_top = center_top
        self._center_bottom = center_bottom
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 8
        x = (self.width() - side) / 2
        y = (self.height() - side) / 2
        outer = QRectF(x, y, side, side)
        thickness = side * 0.26
        inner = QRectF(x + thickness, y + thickness,
                       side - 2 * thickness, side - 2 * thickness)

        total = sum(v for _, v, _ in self._slices)
        if total <= 0:
            p.setPen(QPen(QColor(theme.BORDER), thickness))
            p.drawEllipse(outer.adjusted(thickness / 2, thickness / 2,
                                         -thickness / 2, -thickness / 2))
        else:
            start = 90 * 16                      # start at 12 o'clock
            for _lbl, val, col in self._slices:
                span = -int(round(val / total * 360 * 16))
                p.setBrush(QColor(col))
                p.setPen(QPen(QColor(theme.BG), 2))
                p.drawPie(outer, start, span)
                start += span
            # punch the hole
            p.setBrush(QColor(theme.SURFACE))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(inner)

        # center text
        if self._center_top or self._center_bottom:
            p.setPen(QColor(theme.MUTED))
            f = QFont()
            f.setPointSize(8)
            p.setFont(f)
            p.drawText(inner, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
                       if self._center_bottom else Qt.AlignmentFlag.AlignCenter,
                       self._center_top)
            if self._center_bottom:
                p.setPen(QColor(theme.TEXT))
                f2 = QFont()
                f2.setPointSize(13)
                f2.setBold(True)
                p.setFont(f2)
                p.drawText(inner, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                           self._center_bottom)
        p.end()


class Card(QFrame):
    """Bordered surface with an optional small-caps title."""

    def __init__(self, title: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)
        if title:
            t = QLabel(title.upper())
            self.title = t
            t.setObjectName("cardTitle")
            self.body.addWidget(t)


class CollapsibleCard(QFrame):
    """A card whose contents fold away behind its title.

    The forecast controls are genuinely numerous — projection, income and
    category sliders, debt, events, goals — and most of them are set once and
    left alone. Folding them keeps everything reachable without everything
    being on screen at once.
    """

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(8)

        self.header = QPushButton(f"▾  {title.upper()}")
        self.header.setObjectName("cardToggle")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.clicked.connect(self._sync)
        outer.addWidget(self.header)

        self._content = QWidget()
        self.body = QVBoxLayout(self._content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addWidget(self._content)

        self._title = title
        self._sync()

    def _sync(self) -> None:
        on = self.header.isChecked()
        self._content.setVisible(on)
        self.header.setText(f"{'▾' if on else '▸'}  {self._title.upper()}")

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, on: bool) -> None:
        self.header.setChecked(on)
        self._sync()


class StatCard(Card):
    """Headline number with a caption and a sub-line that explains it.

    The sub-line slot is always occupied, even when empty, so a row of cards
    keeps its numbers on one baseline instead of the one card that has a
    sub-line sitting higher than the rest. ``swatch`` is the chart colour of
    the series this number is read against, so a figure can be traced to its
    line without a legend.
    """

    def __init__(self, title: str, parent=None, swatch: str | None = None,
                 hero: bool = False, dashed: bool = False):
        super().__init__(None, parent)
        self._title_text = title
        head = QHBoxLayout()
        head.setSpacing(7)
        if swatch:
            head.addWidget(Swatch(swatch, line=True, dashed=dashed, width=18))
        self.title = QLabel(title.upper())
        self.title.setObjectName("cardTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.body.addLayout(head)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(6)
        self.value = QLabel("—")
        self.value.setObjectName("statHero" if hero else "statValue")
        self.sub = QLabel(" ")
        self.sub.setObjectName("statSub")
        self.sub.setWordWrap(True)
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)
        self.body.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._announce()

    def set_money(self, x: float | None, colored: bool = False,
                  sub: str | None = None) -> None:
        if x is None:
            self.set_text("—", sub=sub)
            return
        color = None
        if colored:
            color = theme.POSITIVE if x >= 0 else theme.NEGATIVE
        self.set_text(fmt_money(x), color=color, sub=sub)

    def set_text(self, text: str, color: str | None = None,
                 sub: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.sub.setText(sub or " ")
        self._announce()

    def _announce(self) -> None:
        """What a screen reader says for this card: its title, value, note."""
        note = self.sub.text().strip()
        self.setAccessibleName(f"{self._title_text}: {self.value.text()}"
                               + (f". {note}" if note else ""))


def split(orientation, *panes, sizes=None, collapsible: bool = False) -> QSplitter:
    """A draggable divider between panes.

    Every stacked pair of cards in this app is a fixed ratio decided at build
    time, which is a guess about what the reader wants to look at. A splitter
    makes it their call instead: drag the gap to shrink the notes and give the
    chart the room, then drag it back. Panes keep a minimum size so a stray
    drag cannot make one vanish with no way to recover it.
    """
    s = QSplitter(orientation)
    s.setChildrenCollapsible(collapsible)
    s.setHandleWidth(SPLIT_HANDLE)
    for p in panes:
        s.addWidget(p)
    if sizes:
        s.setSizes(list(sizes))
    return s


def vsplit(*panes, sizes=None, collapsible: bool = False) -> QSplitter:
    """Panes stacked top to bottom, divider drags vertically."""
    return split(Qt.Orientation.Vertical, *panes, sizes=sizes,
                 collapsible=collapsible)


def hsplit(*panes, sizes=None, collapsible: bool = False) -> QSplitter:
    """Panes side by side, divider drags horizontally."""
    return split(Qt.Orientation.Horizontal, *panes, sizes=sizes,
                 collapsible=collapsible)


SPLIT_HANDLE = 10


def pane(*widgets, spacing: int = 12, margins=(0, 0, 0, 0)) -> QWidget:
    """Wrap widgets in a plain vertical container fit to drop into a splitter."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


def hsep() -> QFrame:
    f = QFrame()
    f.setObjectName("hsep")
    f.setFrameShape(QFrame.Shape.HLine)
    return f


def form_row(form, text: str, widget) -> None:
    """A form row whose label is tied to its field.

    QFormLayout only sets the buddy when the widget can take focus at the
    moment it is added, and the mapping combos in the import dialogs are empty
    until a file is chosen, so their labels were never connected. A tied label
    is what a screen reader announces and what a mnemonic or a click on the
    label uses.
    """
    label = QLabel(text)
    label.setBuddy(widget)
    form.addRow(label, widget)


def hint(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("hint")
    l.setWordWrap(True)
    l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return l


class Swatch(QWidget):
    """A small colour chip used to label chart bands."""

    def __init__(self, color: str, alpha: int = 255, width: int = 22,
                 height: int = 11, line: bool = False, dashed: bool | str = False):
        super().__init__()
        self.setFixedSize(width, height)
        self._color = color
        self._alpha = alpha
        self._line = line
        self._dashed = dashed

    def paintEvent(self, _ev):
        from PyQt6.QtGui import QBrush, QPainter, QPen
        from . import theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.qcolor(self._color, self._alpha)
        if self._line:
            pen = QPen(c, 2.4)
            if self._dashed == "dot":
                pen.setStyle(Qt.PenStyle.DotLine)
            elif self._dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            y = self.height() // 2
            p.drawLine(0, y, self.width(), y)
        else:
            p.setPen(QPen(Qt.PenStyle.NoPen))
            p.setBrush(QBrush(c))
            p.drawRoundedRect(0, 0, self.width(), self.height(), 2, 2)
        p.end()


class ChartKey(QWidget):
    """A chart legend that sits beside the plot instead of over the data.

    The in-plot legend pyqtgraph draws floats on top of whatever is under it,
    which on a short chart is the bars. A row of swatches under the plot costs
    one line of height and never covers a data point.
    """

    def __init__(self, entries: list[tuple], parent=None):
        """entries: (label, color, kind) with kind in {'bar', 'line', 'dash'}."""
        super().__init__(parent)
        lay = FlowLayout(self, margin=2, spacing=14)
        for label, color, kind in entries:
            entry = QWidget()
            row = QHBoxLayout(entry)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            row.addWidget(Swatch(color, 190 if kind == "bar" else 255,
                                 line=kind != "bar", dashed=("dot" if kind == "dot" else kind == "dash")))
            lbl = QLabel(label)
            lbl.setObjectName("hint")
            row.addWidget(lbl)
            lay.addWidget(entry)
        pol = QSizePolicy(QSizePolicy.Policy.Preferred,
                          QSizePolicy.Policy.Minimum)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)


class SeriesRegistry:
    """Named groups of plot items that can be shown or hidden together.

    A chart is drawn as a pile of pyqtgraph items with no notion of which
    belong together — a fan is three items, a floor is one, history is one.
    Registering them under a key turns "hide the emergency-fund line" into one
    call, and lets visibility survive the full clear-and-redraw that every
    re-run performs.
    """

    def __init__(self):
        self._groups: dict[str, list] = {}
        self.visible: dict[str, bool] = {}

    def add(self, key: str, *items) -> None:
        bucket = self._groups.setdefault(key, [])
        for it in items:
            if it is not None:
                bucket.append(it)
                it.setVisible(self.visible.get(key, True))

    def set_visible(self, key: str, on: bool) -> None:
        self.visible[key] = on
        for it in self._groups.get(key, []):
            it.setVisible(on)

    def apply(self) -> None:
        """Re-assert stored visibility after a redraw."""
        for key, on in self.visible.items():
            for it in self._groups.get(key, []):
                it.setVisible(on)

    def reset(self) -> None:
        """Forget the items but keep the user's choices."""
        self._groups.clear()


class LegendToggles(QWidget):
    """A compact chart key, with visibility controls in a Layers menu."""

    def __init__(self, entries: list[tuple], on_toggle=None, parent=None):
        """entries: (key, label, color, kind) with kind in
        {'band_outer', 'band_inner', 'line', 'dash'}."""
        super().__init__(parent)
        self._on_toggle = on_toggle
        self.actions: dict[str, QAction] = {}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        keys = QWidget()
        lay = FlowLayout(keys, margin=2, spacing=10)
        # The floors and goals already label themselves on the chart. Keep
        # only the key to the main series here; every layer remains switchable.
        main_keys = {"band80", "band50", "median", "networth", "cash", "invest", "debt"}
        self.layers_btn = QPushButton("Layers")
        self.layers_btn.setObjectName("small")
        self.layers_btn.setToolTip("Show or hide individual chart layers")
        menu = QMenu(self.layers_btn)
        self.layers_btn.setMenu(menu)
        for key, label, color, kind in entries:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda on, k=key: self._emit(k, on))
            self.actions[key] = action
            if key not in main_keys:
                continue
            alpha = {"band_outer": 34, "band_inner": 62}.get(kind, 255)
            is_line = kind in ("line", "dash", "dot")
            entry = QWidget()
            row = QHBoxLayout(entry)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(Swatch(color, alpha, line=is_line,
                                 dashed=("dot" if kind == "dot"
                                         else kind == "dash")))
            lbl = QLabel(label)
            lbl.setObjectName("hint")
            row.addWidget(lbl)
            lay.addWidget(entry)
        # heightForWidth has to be advertised on the policy as well as the
        # layout, or the parent allocates only sizeHint()'s height and clips
        # every row after the first.
        pol = QSizePolicy(QSizePolicy.Policy.Preferred,
                          QSizePolicy.Policy.Minimum)
        pol.setHeightForWidth(True)
        keys.setSizePolicy(pol)
        outer.addWidget(keys, 1)
        outer.addWidget(self.layers_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(pol)

    def _emit(self, key: str, on: bool) -> None:
        if self._on_toggle:
            self._on_toggle(key, on)

    def states(self) -> dict[str, bool]:
        return {k: a.isChecked() for k, a in self.actions.items()}


def fan_entries(color: str, extras: list[tuple] | None = None) -> list[tuple]:
    """Standard Monte Carlo legend entries, plus any chart-specific ones."""
    return [
        ("band80", "80% of outcomes (P10–P90)", color, "band_outer"),
        ("band50", "50% of outcomes (P25–P75)", color, "band_inner"),
        ("median", "median (P50)", color, "line"),
    ] + list(extras or [])


# ---------------------------------------------------------------------------
# Explanations that don't belong on the page
# ---------------------------------------------------------------------------
class InfoDialog(QDialog):
    """A scrollable rich-text window for prose the page has no room for.

    Two things use it. The **for nerds** panels hold the statistics — p-values,
    coverage fractions, the effective sample size behind a band — which are all
    real and all worth keeping and none of which belong in front of somebody
    who opened the app to find out whether they can afford a car. The **what
    does this do?** panels explain buttons whose names are honest but not
    self-explanatory, which is most of the Progress page.

    Both are the same shape — a title, a paragraph or several, a way out — so
    they are the same window rather than two near-identical ones.
    """

    def __init__(self, title: str, html: str, parent=None,
                 subtitle: str = "", width: int = 720, height: int = 620):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(440, 300)
        self.resize(width, height)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(9)

        head = QLabel(title)
        head.setStyleSheet("font-size: 17px; font-weight: 600;")
        head.setWordWrap(True)
        lay.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("hint")
            sub.setWordWrap(True)
            lay.addWidget(sub)

        self.view = QTextBrowser()
        self.view.setObjectName("infoBody")
        self.view.setOpenExternalLinks(True)
        # Reading-length styling, as the guide does — this is prose, not a
        # dashboard caption, and the dense page styling is wrong for it.
        self.view.document().setDefaultStyleSheet(
            f"body {{ color: {theme.TEXT}; }}"
            f"h3 {{ color: {theme.ACCENT}; }}"
            f"h4 {{ color: {theme.BLUE}; margin-bottom: 2px; }}"
            f"b {{ color: {theme.TEXT}; }}"
            f"code {{ color: {theme.AMBER}; }}"
            f"td {{ padding-right: 14px; }}"
            f"th {{ color: {theme.MUTED}; text-align: left; padding-right: 14px; }}"
            f".m {{ color: {theme.MUTED}; }}")
        self.view.setHtml(html)
        self.view.moveCursor(self.view.textCursor().MoveOperation.Start)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("primary")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)


def info_button(label: str, provider, tooltip: str = "",
                subtitle: str = "") -> QPushButton:
    """A small button that opens an :class:`InfoDialog`.

    ``provider`` is called at click time and returns ``(title, html)``, so the
    window always describes the numbers on screen right now rather than
    whatever they were when the page was built. It is allowed to fail — an
    explanation that cannot be produced should say so in the window, not take
    the page down with it.
    """
    btn = QPushButton(label)
    btn.setObjectName("small")
    if tooltip:
        btn.setToolTip(tooltip)

    def _open() -> None:
        try:
            title, html = provider()
        except Exception as e:                       # noqa: BLE001
            title, html = label, f"<p>Nothing to show yet — {e}</p>"
        InfoDialog(title, html, btn.window(), subtitle=subtitle).exec()

    btn.clicked.connect(_open)
    return btn


NERD_LABEL = "How this works"
NERD_TIP = ("The statistics behind this page — how each number was computed, "
            "what was tested, and how much to trust it")


def nerd_button(provider, label: str = NERD_LABEL,
                tooltip: str = NERD_TIP) -> QPushButton:
    """The button that keeps the interesting maths off the main page.

    This app knows a lot about how sure it is, and saying so in place was
    turning ordinary pages into statistics coursework: a p-value in a stat
    card, coverage percentages in a caption, a permutation count in a footnote.
    None of it is wrong and none of it is going away — it moves one click to
    the right, so the page can say *"yes, you really are spending less"* and
    the window behind it can say why anyone should believe that.
    """
    return info_button(label, provider, tooltip)


