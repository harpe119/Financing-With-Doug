"""Small reusable widgets."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                             QLabel, QLayout, QPushButton, QSizePolicy,
                             QSpinBox, QSplitter, QVBoxLayout, QWidget)

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
    """Amount field with optional right-to-left (teller-style) digit entry.

    With ``cents_entry`` the decimal point is fixed and typed digits shift in
    from the right, the way an ATM, a card terminal and every banking app
    behave: ``1234`` reads as ``$12.34``, and a further ``00`` makes it
    ``$1,234.00``. The point is that there is no moment where the field holds a
    half-finished number that is wrong by a factor of a hundred — which is
    exactly how ``71520.00`` gets entered by someone who typed the digits they
    meant into a field that had a ``0.00`` sitting in it already.

    Typing the amount out in full still works unchanged: the decimal key is
    ignored rather than rejected, so ``1234.56`` delivers the same six digits
    and lands on ``$1,234.56``.
    """

    def __init__(self, *args, cents_entry: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._cents_entry = cents_entry

    def set_cents_entry(self, on: bool) -> None:
        self._cents_entry = on

    def _units(self) -> int:
        """Current value as an integer count of the smallest unit (cents)."""
        return int(round(self.value() * (10 ** self.decimals())))

    def _fresh(self) -> bool:
        """True when the whole number is selected, so typing starts over.

        Compared against ``cleanText()`` — the value without prefix or suffix —
        because ``QAbstractSpinBox.selectAll()`` deliberately leaves the ``"$ "``
        prefix out of the selection. Comparing against the full line-edit text
        instead meant this never returned True for a field showing money, so
        typing into a populated field appended to the existing digits rather
        than replacing them: clicking a balance of $71,520.00 and typing "500"
        produced $71,520,005.00. It went unnoticed because a field sitting at
        $0.00 gives the same answer either way, and zero is the common case.

        A *pending* select also counts. The select-all is posted through a
        zero-timer (Qt restores its own selection after focusIn, so doing it
        inline gets undone), which leaves a window where the field has focus
        but nothing is highlighted yet — a keystroke landing in that window
        would append to the old value. Asking "has the reset been consumed?"
        rather than "is the highlight painted?" closes the race.
        """
        if self._pending_select:
            return True
        sel = self.lineEdit().selectedText()
        core = self.cleanText()
        # Containment, not equality: selectAll() on the spin box yields just
        # "71,520.00" while dragging across the field yields "$ 71,520.00", and
        # both mean the same thing to the person doing it.
        return bool(sel) and bool(core) and core in sel

    def _commit(self, units: int) -> None:
        self._pending_select = False
        self.setValue(units / (10 ** self.decimals()))
        le = self.lineEdit()
        le.deselect()
        # Park the caret at the end. Nothing is typed *at* the caret in this
        # mode, but leaving it mid-number looks like the field is editable
        # there when it is not.
        le.setCursorPosition(len(le.text()))

    def keyPressEvent(self, event) -> None:      # noqa: N802
        if not self._cents_entry:
            super().keyPressEvent(event)
            return
        mods = event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier
        if mods != Qt.KeyboardModifier.NoModifier:
            super().keyPressEvent(event)          # Ctrl+A/C/V and friends
            return
        text = event.text()
        if text.isdigit():
            base = 0 if self._fresh() else self._units()
            self._commit(int(math.copysign(abs(base) * 10 + int(text), base or 1)))
            return
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            units = 0 if self._fresh() else self._units()
            self._commit(int(math.copysign(abs(units) // 10, units or 1)))
            return
        if text in ("-", "+") and self.minimum() < 0:
            self._commit(-abs(self._units()) if text == "-" else abs(self._units()))
            return
        if text in (".", ","):
            return          # the point is fixed; there is nothing to place
        super().keyPressEvent(event)


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

    def set_expanded(self, on: bool) -> None:
        self.header.setChecked(on)
        self._sync()


class StatCard(Card):
    """Headline number with a caption and optional sub-line."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.value = QLabel("—")
        self.value.setObjectName("statValue")
        self.sub = QLabel("")
        self.sub.setObjectName("statSub")
        self.sub.setVisible(False)
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

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
        self.sub.setVisible(bool(sub))
        self.sub.setText(sub or "")


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


def hint(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("hint")
    l.setWordWrap(True)
    l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return l


class Swatch(QWidget):
    """A small colour chip used to label chart bands."""

    def __init__(self, color: str, alpha: int = 255, width: int = 22,
                 height: int = 11, line: bool = False, dashed: bool = False):
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
            if self._dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            y = self.height() // 2
            p.drawLine(0, y, self.width(), y)
        else:
            p.setPen(QPen(Qt.PenStyle.NoPen))
            p.setBrush(QBrush(c))
            p.drawRoundedRect(0, 0, self.width(), self.height(), 2, 2)
        p.end()


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
    """Clickable chart legend.

    Doubles as the key and the visibility control: the swatch says what the
    shading means, the checkbox turns it off. Explaining the bands matters
    because an unlabelled gradient invites reading the median as a prediction
    rather than as the middle of a distribution; being able to hide them
    matters because five overlapping series on one axis is unreadable.
    """

    def __init__(self, entries: list[tuple], on_toggle=None, parent=None):
        """entries: (key, label, color, kind) with kind in
        {'band_outer', 'band_inner', 'line', 'dash'}."""
        super().__init__(parent)
        self._on_toggle = on_toggle
        self.boxes: dict[str, QCheckBox] = {}
        # One wrapping row of self-contained entries. Each entry is its own
        # widget so the layout can only ever break *between* entries, never
        # between a swatch and the label that explains it.
        lay = FlowLayout(self, margin=2, spacing=10)
        for key, label, color, kind in entries:
            alpha = {"band_outer": 34, "band_inner": 62}.get(kind, 255)
            is_line = kind in ("line", "dash")
            entry = QWidget()
            row = QHBoxLayout(entry)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            box = QCheckBox()
            box.setChecked(True)
            box.setToolTip(f"Show or hide: {label}")
            box.stateChanged.connect(
                lambda st, k=key: self._emit(k, bool(st)))
            row.addWidget(box)
            row.addWidget(Swatch(color, alpha, line=is_line,
                                 dashed=(kind == "dash")))
            lbl = QLabel(label)
            lbl.setObjectName("hint")
            row.addWidget(lbl)
            lay.addWidget(entry)
            self.boxes[key] = box
        # heightForWidth has to be advertised on the policy as well as the
        # layout, or the parent allocates only sizeHint()'s height and clips
        # every row after the first.
        pol = QSizePolicy(QSizePolicy.Policy.Preferred,
                          QSizePolicy.Policy.Minimum)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

    def _emit(self, key: str, on: bool) -> None:
        if self._on_toggle:
            self._on_toggle(key, on)

    def states(self) -> dict[str, bool]:
        return {k: b.isChecked() for k, b in self.boxes.items()}


def fan_entries(color: str, extras: list[tuple] | None = None) -> list[tuple]:
    """Standard Monte Carlo legend entries, plus any chart-specific ones."""
    return [
        ("band80", "80% of outcomes (P10–P90)", color, "band_outer"),
        ("band50", "50% of outcomes (P25–P75)", color, "band_inner"),
        ("median", "median (P50)", color, "line"),
    ] + list(extras or [])


