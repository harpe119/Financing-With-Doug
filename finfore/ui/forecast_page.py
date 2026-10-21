"""Forecast page — the signature view.

History (muted) hands off to the Monte Carlo fan (teal) at a dashed "today"
line; the deterministic path runs through it in amber. Scenario knobs reshape
the fan; goals render as target lines with arrival estimates.
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape

import numpy as np
from PyQt6.QtCore import QDate, QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog,
                             QFormLayout, QGridLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QSizePolicy, QSlider, QSplitter,
                             QVBoxLayout, QWidget)

from ..forecast import (MATERIAL_MIN, STARTER_BUFFER, SUSTAINED_DAYS, T_DOF,
                        ForecastResult, backtest_cash, crunch_causes,
                        goal_plan_enabled, run_forecast, sensitivity)
from ..insights import emergency_fund_target, essential_monthly
from ..history import category_spending, networth_history
from ..forecast_history import ForecastHistory, SETTING as HISTORY_SETTING
from ..events import EVENT_REPEATS
from ..models import OneOffEvent, Scenario
from ..store import Store
from ..util import fmt_money, fmt_day
from . import charts, prefs, theme, video
from .widgets import (Card, CollapsibleCard, FlowLayout, IntSpinBox, LegendToggles,
                      NERD_LABEL, NERD_TIP, SeriesRegistry, WrappedCheckBox, explain_button, fan_entries,
                      hint, info_opener, vsplit)
from .account_dialog import _money_spin
from ..data_quality import data_freshness
from .data_quality import DataQualityCard

_HORIZONS = [("6 months", 183), ("1 year", 365), ("2 years", 730),
             ("3 years", 1095), ("5 years", 1825), ("10 years", 3650)]
_COMPARISON_TIP_SETTING = "forecast_comparison_tip_seen"

# How much of the x-axis history is allowed to take. This is the forecast page:
# the projection is the subject and the history is there to show where it is
# handing off from. A fixed year of lead-in gave a 6-month projection two
# thirds of the chart to history, so the thing being asked about was drawn
# smallest. Scaling with the horizon keeps the handoff visible without letting
# the past dominate the future.
HISTORY_FRACTION = 0.30
HISTORY_MIN_DAYS = 45
HISTORY_MAX_DAYS = 366


def history_window(horizon_days: int) -> int:
    """Days of history to show beside a projection of this length."""
    return int(min(HISTORY_MAX_DAYS,
                   max(HISTORY_MIN_DAYS, horizon_days * HISTORY_FRACTION)))


def _forecast_legend_entries(color, extras):
    labels = {"band80": "Middle 80% of outcomes", "band50": "Middle 50% of outcomes",
              "median": "Middle outcome"}
    return [(key, labels.get(key, label), hue, kind)
            for key, label, hue, kind in fan_entries(color, extras=extras)]


class _ForecastIllustration(QWidget):
    """An explanatory fan, without balances or axes that could imply real data."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Illustration: your cash history joins a widening range "
                               "of possible future balances at today. No actual data is shown.")
        self.setToolTip("Illustration only. Try the demo to explore a working forecast.")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        left, start, end = 12.0, width * .35, width - 12.0
        middle = height * .47
        span = end - start
        for spread, opacity in ((height * .30, 35), (height * .16, 55)):
            band = QPainterPath(QPointF(start, middle))
            band.cubicTo(start + span * .35, middle - spread * .35,
                         start + span * .7, middle - spread * .8, end, middle - spread)
            band.lineTo(end, middle + spread)
            band.cubicTo(start + span * .7, middle + spread * .8,
                         start + span * .35, middle + spread * .35, start, middle)
            painter.fillPath(band, theme.qcolor(theme.BLUE, opacity))
        history = QPainterPath(QPointF(left, middle + 12))
        for fraction, offset in ((.2, 2), (.35, 9), (.53, -8), (.7, 4), (1, 0)):
            history.lineTo(left + (start - left) * fraction, middle + offset)
        painter.setPen(QPen(theme.qcolor(theme.MUTED), 1.6))
        painter.drawPath(history)
        future = QPainterPath(QPointF(start, middle))
        future.cubicTo(start + span * .35, middle - 4,
                       start + span * .7, middle - 9, end, middle - 12)
        painter.setPen(QPen(theme.qcolor(theme.BLUE), 2))
        painter.drawPath(future)
        painter.setPen(QPen(theme.qcolor(theme.MUTED), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(start, 10), QPointF(start, height - 34))
        painter.setPen(theme.qcolor(theme.TEXT))
        painter.setBrush(theme.qcolor(theme.TEXT))
        painter.drawEllipse(QPointF(start, middle), 3, 3)
        painter.setPen(theme.qcolor(theme.MUTED))
        painter.drawText(QRectF(left, height - 25, start - left - 20, 22),
                         Qt.AlignmentFlag.AlignLeft, "Your history")
        painter.drawText(QRectF(start - 28, height - 25, 56, 22),
                         Qt.AlignmentFlag.AlignCenter, "Today")
        painter.drawText(QRectF(start + 36, height - 25, end - start - 36, 22),
                         Qt.AlignmentFlag.AlignRight, "Possible futures")


class _EventNameLabel(QLabel):
    """Keep a saved name literal without pushing its actions out of view."""

    def __init__(self, text: str):
        super().__init__()
        self._full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()))


class SliderRow(QWidget):
    def __init__(self, label: str, lo: int = 0, hi: int = 200, start: int = 100):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self.setMinimumHeight(26)
        self.name = label
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(start)
        self.label.setBuddy(self.slider)
        self.slider.setAccessibleName(label)
        self.pct = QLabel(f"{start}%")
        self.pct.setMinimumWidth(40)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        self.monthly = hint("")
        self.monthly.setTextFormat(Qt.TextFormat.PlainText)
        self.monthly.hide()
        self._monthly_reference: float | None = None
        self.slider.valueChanged.connect(self._update_caption)
        self._start = start
        self._stacked = None
        self._arrange()
        self._update_caption()

    def set_monthly_reference(self, amount: float | None) -> None:
        self._monthly_reference = amount
        self.monthly.setVisible(amount is not None)
        if amount is None:
            self.monthly.clear()
        self._update_caption()

    def _update_caption(self, *_args) -> None:
        percent = self.slider.value()
        self.pct.setText(f"{percent}%")
        description = f"{self.name}: {percent}% of forecast inputs; 100% leaves them unchanged."
        if self._monthly_reference is not None:
            amount = self._monthly_reference * self.value()
            change = amount - self._monthly_reference
            text = f"≈ {fmt_money(amount)}/mo"
            if abs(change) >= .005:
                text += f" · {fmt_money(abs(change))} {'more' if change > 0 else 'less'}"
            self.monthly.setText(text)
            description += f" Approximate monthly amount: {text}."
        self.slider.setAccessibleDescription(description)
        self.slider.setToolTip(description +
            " Uses this forecast's scheduled flows and weighted spending weeks. "
            "Loan-account payments, investing and what-if events are separate.")
        self._fit_height()

    def _fit_height(self) -> None:
        # Reserve every wrapped line, including the monthly estimate. A grid
        # can otherwise compress an ignored-width category label below its
        # required height when this row lives inside nested collapsible cards.
        spacing = self.layout().spacing()
        label_width = (max(1, self.width() - self.pct.minimumWidth() - spacing)
                       if self._stacked else max(88,
                           self.label.fontMetrics().horizontalAdvance(self.name)))
        label_height = max(self.label.fontMetrics().height(),
                           self.label.heightForWidth(label_width))
        self.label.setMinimumHeight(label_height)
        slider_height = max(18, self.slider.sizeHint().height())
        height = max(label_height, self.pct.sizeHint().height())
        height = height + spacing + slider_height if self._stacked else max(height, slider_height)
        if not self.monthly.isHidden():
            monthly_height = max(0, self.monthly.heightForWidth(max(1, self.width())))
            self.monthly.setMinimumHeight(monthly_height)
            height += spacing + monthly_height
        else:
            self.monthly.setMinimumHeight(0)
        margins = self.layout().contentsMargins()
        height += margins.top() + margins.bottom()
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def _arrange(self) -> None:
        label_width = max(88, self.label.fontMetrics().horizontalAdvance(self.name))
        stacked = self.width() < label_width + 100 + self.pct.minimumWidth() + 16
        if stacked == self._stacked:
            self._fit_height()
            self.layout().activate()
            return
        self._stacked = stacked
        lay = self.layout()
        for widget in (self.label, self.slider, self.pct, self.monthly):
            lay.removeWidget(widget)
        for column in range(3):
            lay.setColumnMinimumWidth(column, 0)
            lay.setColumnStretch(column, 0)
        if stacked:
            lay.addWidget(self.label, 0, 0)
            lay.addWidget(self.pct, 0, 1)
            lay.addWidget(self.slider, 1, 0, 1, 2)
            lay.addWidget(self.monthly, 2, 0, 1, 2)
            lay.setColumnStretch(0, 1)
        else:
            lay.addWidget(self.label, 0, 0)
            lay.addWidget(self.slider, 0, 1)
            lay.addWidget(self.pct, 0, 2)
            lay.addWidget(self.monthly, 1, 0, 1, 3)
            lay.setColumnMinimumWidth(0, label_width)
            lay.setColumnStretch(1, 1)
        self.updateGeometry()
        self._fit_height()
        # Commit the new grid before returning from the resize. On Windows,
        # nested scroll layouts can otherwise retain the previous horizontal
        # slider geometry even though the row has switched to stacked mode.
        lay.activate()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._arrange()

    def minimumSizeHint(self):
        # The horizontal layout's natural label width must not prevent the
        # row from receiving a narrower size and switching to stacked mode.
        # Otherwise the scroll area can get stuck wider than its viewport.
        size = super().minimumSizeHint()
        size.setWidth(0)
        return size

    def value(self) -> float:
        return self.slider.value() / 100.0

    def reset(self) -> None:
        self.slider.setValue(self._start)


# A dip short enough that a *thing* caused it, rather than a way of living.
# Past this, "what caused it" has no answer worth acting on and the honest
# response is the sensitivity panel, not a list of rent payments.
CAUSE_MAX_DAYS = 60

_SOURCE_LABEL = {
    "recurring": "recurring bill",
    "loan": "loan payment",
    "event": "planned what-if",
    "investment": "investment contribution",
}


class CrunchDialog(QDialog):
    """What is digging the hole, and one gesture to climb out of it.

    A dated warning immediately provokes "caused by what?", and until now the
    page could not answer: it knew the low point was in August and nothing
    else. The projection has always known — it replays every bill and pay
    cheque by name to build the path — it simply threw the names away.

    The move is the point, though. Knowing that rent lands two days before
    payday is only useful if the next step is easy, so each movable outflow
    gets a date box and an Apply: shifting one is modelled as the pair of
    offsetting one-off events it really is — the money back on the old date,
    out again on the new one — which the projection already understands and
    which shows up in the what-if list where it can be undone.
    """

    def __init__(self, store: Store, sf, causes, next_in, parent=None):
        super().__init__(parent)
        self.store = store
        self.applied = False
        # Two quite different situations arrive here. A *timing* problem is one
        # payment landing on the wrong side of payday, and rescheduling it is
        # the actual fix. A *structural* one is more going out than coming in
        # for years, where rescheduling moves the hole rather than filling it —
        # so the same list of bills is shown, because knowing what leaves is
        # useful either way, and the Move buttons are withheld. Offering a fix
        # the app knows will not work is worse than offering none.
        self.timing = sf.recovers and sf.median_days <= CAUSE_MAX_DAYS
        self.setWindowTitle("What's causing this")
        self.setMinimumWidth(560)
        self.resize(640, 540)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        head = QLabel(sf.headline)
        head.setObjectName("headline")
        head.setWordWrap(True)
        lay.addWidget(head)

        when = fmt_day(sf.low_date) if sf.low_date else "the low point"
        if self.timing:
            intro = [f"Around <b>{when}</b> the balance bottoms out near "
                     f"<b>{fmt_money(-sf.median_depth)}</b>."]
            if next_in is not None:
                intro.append(f"The next money in is {escape(next_in.label)} on "
                             f"<b>{fmt_day(next_in.when, year=False)}</b> "
                             f"({fmt_money(next_in.amount)}) — anything moved "
                             f"past that date is paid out of it instead.")
        else:
            intro = [f"This is not one payment landing at a bad moment. The "
                     f"balance stays under for about "
                     f"<b>{sf.median_days:.0f} days</b>, which means more is "
                     f"going out than is coming in — so no amount of "
                     f"rescheduling will fix it, and this window only shows "
                     f"what the money is currently committed to."]
        blurb = QLabel(" ".join(intro))
        blurb.setWordWrap(True)
        blurb.setObjectName("hint")
        lay.addWidget(blurb)

        lay.addWidget(QLabel("Scheduled to leave over the next few weeks:"
                             if not self.timing else
                             "Scheduled to leave in the run-up:"))
        host = QWidget()
        rows = QVBoxLayout(host)
        rows.setContentsMargins(0, 0, 6, 0)
        rows.setSpacing(6)
        self._movers: list[tuple] = []
        for f in causes[:8]:
            rows.addWidget(self._row(f, next_in))
        if not causes:
            rows.addWidget(hint("Nothing dated is scheduled in that window — "
                                "the dip is day-to-day spending rather than "
                                "any one payment, so there is nothing here to "
                                "move. The category sliders are the lever."))
        rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)

        self.move_error = hint("")
        self.move_error.setTextFormat(Qt.TextFormat.PlainText)
        self.move_error.setStyleSheet(f"color: {theme.RED};")
        self.move_error.hide()
        lay.addWidget(self.move_error)

        lay.addWidget(hint(
            "Changing a payment date changes the forecast only. The amount is unchanged. "
            "Contact the payee before changing an actual payment date."
            if self.timing else
            "Use the income and spending controls to compare changes. The Sensitivity "
            "panel estimates how much each control changes the projected result."))

        bar = QHBoxLayout()
        bar.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        lay.addLayout(bar)

    def _row(self, f, next_in) -> QWidget:
        w = QWidget()
        rl = QHBoxLayout(w)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        title = _EventNameLabel(f"{f.label} · {fmt_money(f.amount)}")
        title.setStyleSheet(f"color: {theme.RED};")
        sub = QLabel(f"{fmt_day(f.when, year=False, weekday=True)} · "
                     f"{_SOURCE_LABEL.get(f.source, f.source)}")
        sub.setObjectName("hint")
        col.addWidget(title)
        col.addWidget(sub)
        rl.addLayout(col, 1)
        if f.movable and self.timing:
            to = QDateEdit()
            to.setCalendarPopup(True)
            to.setDisplayFormat("MMM d, yyyy")
            # Default: the day after the next money lands, which is the move
            # that actually fixes a timing problem.
            target = (next_in.when + timedelta(days=1)) if next_in is not None \
                else f.when + timedelta(days=7)
            to.setDate(QDate(target.year, target.month, target.day))
            to.setToolTip("Pay it on this date instead")
            go = QPushButton("Move")
            go.setObjectName("small")
            go.clicked.connect(lambda _c, ff=f, dd=to: self._move(ff, dd))
            rl.addWidget(to)
            rl.addWidget(go)
            self._movers.append((f, to, go))
        return w

    def _move(self, f, date_edit) -> None:
        if self.applied:
            return
        qd = date_edit.date()
        target = date(qd.year(), qd.month(), qd.day())
        if target <= date.today():
            self.move_error.setText("Choose a future payment date.")
            self.move_error.show()
            date_edit.setFocus()
            return
        if target == f.when:
            return
        # The pair the projection already understands: the money stays in on the
        # original date, and goes out on the new one.
        try:
            self.store.add_events([
                OneOffEvent(f"{f.label} — not paid {fmt_day(f.when, year=False)}",
                            f.when, abs(f.amount)),
                OneOffEvent(f"{f.label} — paid {fmt_day(target, year=False)} instead",
                            target, -abs(f.amount))])
        except Exception as error:
            self.move_error.setText("The payment move could not be saved. "
                                    "Check that this ledger is writable, then try Move again.")
            self.move_error.setToolTip(str(error))
            self.move_error.show()
            return
        self.applied = True
        self.accept()


def _clear_layout(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class ForecastPage(QWidget):
    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._cat_sliders: list[SliderRow] = []
        self._simple = False
        self._analysis = "tornado"
        self._analysis_dirty = True
        self._goal_labels: dict[int, QLabel] = {}
        # Last run's results, kept so the "for nerds" window can describe what
        # is currently on screen rather than recomputing it (a second forecast
        # with a fresh seed would quote different numbers to the ones the page
        # is showing, which is worse than useless in an explanation).
        self._res: ForecastResult | None = None
        self._base: ForecastResult | None = None
        self._bt = None
        self._sens: list = []
        self._ef_target = 0.0
        self._ef_months = 3
        self._changing_controls = False
        self._running = False
        self._refresh_needed = False
        self._events_ready = False
        self._editing_event: tuple[Store, OneOffEvent] | None = None
        self._comparison_tip_seen = prefs.app_settings().value(
            _COMPARISON_TIP_SETTING, False, type=bool)
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(300)
        self._update_timer.timeout.connect(self.run)

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(0)
        # A splitter rather than two fixed columns. The control column used to
        # be pinned at 374px whatever the window did, so narrowing the window
        # took the space out of the charts alone until the cards could not
        # shrink any further and started overlapping the column beside them.
        # A splitter lets either side give, and lets the user decide which.
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        root.addWidget(split)
        self.forecast_split = split
        self.start_panel = self._build_start_panel()
        self.start_panel.hide()
        root.addWidget(self.start_panel)

        # ---------- left: controls ----------
        controls = QWidget()
        cv = QVBoxLayout(controls)
        cv.setContentsMargins(0, 0, 6, 0)
        cv.setSpacing(12)

        proj = Card("Projection")
        proj.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        form = QFormLayout()
        form.setSpacing(8)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.horizon = QComboBox()
        for label, days in _HORIZONS:
            self.horizon.addItem(label, days)
        self.horizon.setCurrentIndex(3)
        self.paths = IntSpinBox()
        self.paths.setRange(100, 2000)
        self.paths.setSingleStep(100)
        self.paths.setValue(400)
        self.ef_months = IntSpinBox()
        self.ef_months.setRange(1, 12)
        self.ef_months.setValue(3)
        self.ef_months.setSuffix(" months")
        self.ef_months.setToolTip(
            "Cash buffer, drawn on the cash chart. Sized from your "
            "own essential spending — bills, groceries, insurance, medical, "
            "fuel and loan payments — not a generic figure.")
        self.horizon.setAccessibleName("Forecast length")
        self.paths.setAccessibleName("Number of simulated outcomes")
        self.ef_months.setAccessibleName("Cash buffer in months of essential spending")
        form.addRow("Forecast length", self.horizon)
        # Wrapped rather than added straight to the form, so Simple view can
        # hide the label and the spinner as one thing.
        self.paths_row = QWidget()
        prl = QFormLayout(self.paths_row)
        prl.setContentsMargins(0, 0, 0, 0)
        prl.setSpacing(8)
        prl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        prl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        prl.addRow("Monte Carlo paths", self.paths)
        form.addRow(self.paths_row)
        form.addRow("Cash buffer", self.ef_months)
        proj.body.addLayout(form)
        self.overlay = WrappedCheckBox("Compare unchanged controls")
        self.overlay.setToolTip(
            "Compare with income and spending at 100%, extra loan payments and investing "
            "at $0, and loan-payment rollover enabled. Both use the same history setting "
            "and saved what-if events. The comparison leaves the saved Plan and Goals "
            "funding disabled so you can see their combined effect.")
        self.overlay.setChecked(True)
        proj.body.addWidget(self.overlay)
        self.run_btn = QPushButton("Refresh forecast")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run)
        self.reset_btn = QPushButton("Reset controls")
        self.reset_btn.setObjectName("small")
        self.reset_btn.setToolTip(
            "Restore income and spending to 100%, extra loan payments and investing to $0, "
            "and loan-payment rollover to on. Saved events and forecast history stay as set.")
        self.reset_btn.clicked.connect(self._reset_scenario)
        proj.body.addWidget(self.reset_btn)
        proj.body.addWidget(self.run_btn)
        cv.addWidget(proj)

        self.history_card = CollapsibleCard("Forecast history", expanded=False)
        self.history_card.setSizePolicy(QSizePolicy.Policy.Preferred,
                                        QSizePolicy.Policy.Minimum)
        self.history_pick = QComboBox()
        self.history_pick.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.history_pick.setMinimumContentsLength(10)
        for label, key in (("Automatic (up to 78 recorded weeks)", "auto"),
                           ("Last 3 months (13 weeks)", 13),
                           ("Last 6 months (26 weeks)", 26),
                           ("Last year (52 weeks)", 52),
                           ("Last 18 months (78 weeks)", 78),
                           ("Last 2 years (104 weeks)", 104),
                           ("All available history", "all"),
                           ("Since a date…", "since")):
            self.history_pick.addItem(label, key)
        self.history_pick.setToolTip(self.history_pick.currentText())
        self.history_pick.currentTextChanged.connect(self.history_pick.setToolTip)
        self.history_pick.setAccessibleName("History used for random spending weeks")
        self.history_card.body.addWidget(QLabel("Sample spending weeks from"))
        self.history_card.body.addWidget(self.history_pick)
        self.history_since_row = QWidget()
        hsl = QFormLayout(self.history_since_row)
        hsl.setContentsMargins(0, 0, 0, 0)
        hsl.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        hsl.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.history_since = QDateEdit(QDate.currentDate().addMonths(-6))
        self.history_since.setCalendarPopup(True)
        self.history_since.setDisplayFormat("MMM d, yyyy")
        self.history_since.setMaximumDate(QDate.currentDate())
        self.history_since.setAccessibleName("First date of forecast history")
        hsl.addRow("Start date", self.history_since)
        self.history_card.body.addWidget(self.history_since_row)
        self.history_refit = WrappedCheckBox("Re-estimate paydays and bills too")
        self.history_refit.setToolTip(
            "Detect schedules and amounts using only this period. Saved patterns "
            "are unchanged; disabled patterns and loan links are respected. "
            "Short periods may miss quarterly or yearly bills.")
        self.history_card.body.addWidget(self.history_refit)
        self.history_card.body.addWidget(hint(
            "After a move or job change, choose its date. Check the option above "
            "to replace old income and bills in this forecast as well."))
        self.history_note = hint("")
        self.history_card.body.addWidget(self.history_note)
        review = QPushButton("Review detected bills and pay")
        review.setObjectName("small")
        review.clicked.connect(lambda: self.main.show_page("Recurring"))
        self.history_card.body.addWidget(review)
        self._load_history()
        self.history_pick.currentIndexChanged.connect(self._history_changed)
        self.history_since.dateChanged.connect(self._history_changed)
        self.history_refit.toggled.connect(self._history_changed)
        cv.addWidget(self.history_card)

        spend = CollapsibleCard("Income and spending", expanded=True)
        self.income_slider = SliderRow("Income", 50, 150, 100)
        spend.body.addWidget(self.income_slider)
        self.cat_box = QVBoxLayout()
        self.cat_box.setSpacing(6)
        spend.body.addLayout(self.cat_box)
        self.comparison_tip = QWidget()
        tip_layout = QVBoxLayout(self.comparison_tip)
        tip_layout.setContentsMargins(0, 4, 0, 4)
        tip_layout.setSpacing(6)
        tip = hint("Try changing one spending control. Compare the result "
                   "with the dashed line for unchanged controls.")
        tip.setTextFormat(Qt.TextFormat.PlainText)
        tip_layout.addWidget(tip)
        self.dismiss_tip_btn = QPushButton("Dismiss tip")
        self.dismiss_tip_btn.setObjectName("small")
        self.dismiss_tip_btn.clicked.connect(self._dismiss_comparison_tip)
        tip_layout.addWidget(self.dismiss_tip_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.comparison_tip.hide()
        spend.body.addWidget(self.comparison_tip)
        spend.body.addWidget(hint("100% leaves forecast inputs unchanged. "
                                  "Amounts are monthly estimates. Changes update automatically."))
        cv.addWidget(spend)

        debt = CollapsibleCard("Debt and investing", expanded=False)
        dform = QFormLayout()
        dform.setSpacing(8)
        dform.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        dform.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.extra_loan = _money_spin(0, 1e6)
        self.extra_invest = _money_spin(0, 1e6)
        dform.addRow("Extra to loans /mo", self.extra_loan)
        dform.addRow("Extra investing /mo", self.extra_invest)
        debt.body.addLayout(dform)
        self.snowball = WrappedCheckBox("Roll freed payments into the next loan")
        self.snowball.setChecked(True)
        debt.body.addWidget(self.snowball)
        cv.addWidget(debt)
        self.debt_card = debt

        ev = CollapsibleCard("What-if events", expanded=False)
        self.event_box = QVBoxLayout()
        self.event_box.setSpacing(3)
        ev.body.addLayout(self.event_box)
        event_form = QFormLayout()
        event_form.setSpacing(8)
        event_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        event_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.e_name = QLineEdit()
        self.e_name.setPlaceholderText("New car, medical bill, bonus…")
        self.e_amount = _money_spin(-1_000_000, 1_000_000)
        self.e_when = QDateEdit()
        self.e_when.setCalendarPopup(True)
        self.e_when.setDate(QDate.currentDate().addMonths(6))
        self.e_when.setDisplayFormat("MMM d, yyyy")
        self.e_when.setMinimumDate(QDate.currentDate().addDays(1))
        self.e_name.setAccessibleName("Event name")
        self.e_amount.setAccessibleName("Event amount")
        self.e_when.setAccessibleName("Event date")
        self.e_repeat = QComboBox()
        # Match the history selector: do not cache the widest item's size on
        # first show, before fonts or the control-column width have settled.
        self.e_repeat.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.e_repeat.setMinimumContentsLength(10)
        for key, label in EVENT_REPEATS.items():
            self.e_repeat.addItem(label, key)
        self.e_repeat.setAccessibleName("Event repeat interval")
        self.e_repeat.setToolTip(
            "The amount applies to each occurrence. Month-based repeats keep the "
            "original day, using the last day in shorter months.")
        self.e_when_label = QLabel("Date")
        self.e_until_enabled = QCheckBox("End date")
        self.e_until_enabled.setAccessibleName("Set an end date for this event")
        self.e_until = QDateEdit(QDate.currentDate().addYears(1))
        self.e_until.setCalendarPopup(True)
        self.e_until.setDisplayFormat("MMM d, yyyy")
        self.e_until.setAccessibleName("Event end date, inclusive")
        self.e_until_enabled.toggled.connect(self.e_until.setEnabled)
        self.e_until.setEnabled(False)
        self.e_repeat.currentIndexChanged.connect(self._sync_event_repeat)
        event_form.addRow("Name", self.e_name)
        event_form.addRow("Amount", self.e_amount)
        event_form.addRow("Repeat", self.e_repeat)
        event_form.addRow(self.e_when_label, self.e_when)
        event_form.addRow(self.e_until_enabled, self.e_until)
        ev.body.addLayout(event_form)
        self.event_edit_note = hint("")
        self.event_edit_note.setTextFormat(Qt.TextFormat.PlainText)
        self.event_edit_note.hide()
        ev.body.addWidget(self.event_edit_note)
        event_actions = QWidget()
        erow = FlowLayout(event_actions, spacing=6)
        add_ev = QPushButton("Add event")
        add_ev.clicked.connect(self._add_event)
        erow.addWidget(add_ev)
        self.add_event_btn = add_ev
        self.cancel_event_btn = QPushButton("Cancel edit")
        self.cancel_event_btn.clicked.connect(self._cancel_event_edit)
        self.cancel_event_btn.hide()
        erow.addWidget(self.cancel_event_btn)
        action_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        action_policy.setHeightForWidth(True)
        event_actions.setSizePolicy(action_policy)
        ev.body.addWidget(event_actions)
        self.event_error = hint("")
        self.event_error.setTextFormat(Qt.TextFormat.PlainText)
        self.event_error.setStyleSheet(f"color: {theme.RED};")
        self.event_error.hide()
        ev.body.addWidget(self.event_error)
        ev.body.addWidget(hint(
            "Use a negative amount for a cost and a positive amount for income. "
            "Repeating events apply that amount each time, in addition to your "
            "existing spending. They do not create ledger transactions."))
        self._sync_event_repeat()
        cv.addWidget(ev)
        self.event_card = ev

        goals = CollapsibleCard("Goals", expanded=False)
        self.follow_goals = WrappedCheckBox("Use Goals funding plan")
        self.follow_goals.setToolTip('Apply the saved monthly budget from Plan and the allowance, '
            'priority and allocation policy from Goals. Scenario adjustments apply on top. '
            'Reserves stay in cash; goal earmarks retain their underlying cash or investment holdings. '
            'future purchases from those reserves are not assumed.')
        self.follow_goals.toggled.connect(self._goal_plan_changed)
        goals.body.addWidget(self.follow_goals)
        goals.body.addWidget(hint('Uses your saved Plan income, spending and investment contributions, '
            'plus Goals loan repayments. Turn this off to forecast existing habits with the separate '
            'extra-loan and rollover controls.'))
        self.cash_view = QComboBox()
        self.cash_view.setAccessibleName("Cash to show")
        self.cash_view.addItem("Bank cash", "bank")
        self.cash_view.addItem("Free cash after goal funding", "free")
        self.cash_view.setToolTip(
            "Bank cash includes savings set aside for goals. Free cash subtracts recorded cash "
            "earmarks and, when the plan is enabled, future savings-goal funding and the Plan's "
            "irregular-expense reserve. Investment earmarks and balance milestones are not "
            "subtracted from cash. Planned goal funding is reserved on the first of each month; "
            "no future purchases are assumed. This changes the display, not your plan.")
        cash_view_label = QLabel("Cash to show")
        cash_view_label.setBuddy(self.cash_view)
        goals.body.addWidget(cash_view_label)
        goals.body.addWidget(self.cash_view)
        self.cash_view.currentIndexChanged.connect(self._cash_view_changed)
        self.goal_box = QVBoxLayout()
        self.goal_box.setSpacing(8)
        goals.body.addLayout(self.goal_box)
        add_row = QHBoxLayout()
        self.manage_goals_btn = QPushButton("Manage goals…")
        self.manage_goals_btn.setObjectName("small")
        self.manage_goals_btn.clicked.connect(lambda: self.main.show_page("Goals"))
        add_row.addWidget(self.manage_goals_btn)
        add_row.addStretch(1)
        goals.body.addLayout(add_row)
        cv.addWidget(goals)
        cv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(240)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(controls)
        self.control_scroll = scroll
        split.addWidget(scroll)

        # ---------- right: charts ----------
        chart_col = QWidget()
        # Ignored horizontally: the charts take whatever room is left rather
        # than reporting a width the splitter then has to honour.
        chart_col.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
        right = QVBoxLayout(chart_col)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)

        picker_host = QWidget()
        picker = FlowLayout(picker_host, spacing=8)
        self.view_row = QWidget()
        vrl = QHBoxLayout(self.view_row)
        vrl.setContentsMargins(0, 0, 0, 0)
        vrl.setSpacing(6)
        vrl.addWidget(QLabel("Show"))
        self.view_pick = QComboBox()
        self.view_pick.setAccessibleName("Chart to show")
        for lbl, key in (("Cash + net worth", "both"), ("Cash only", "cash"),
                         ("Net worth only", "networth"),
                         ("Analysis only", "analysis")):
            self.view_pick.addItem(lbl, key)
        self.view_pick.setToolTip("Show cash and net worth together on the same axes, "
                                  "or focus on one measure")
        self.view_pick.currentIndexChanged.connect(self._apply_view)
        vrl.addWidget(self.view_pick)
        picker.addWidget(self.view_row)
        self.show_analysis = QCheckBox("Analysis panel")
        self.show_analysis.setChecked(False)
        self.show_analysis.stateChanged.connect(lambda _s: self._apply_view())
        picker.addWidget(self.show_analysis)
        # In Simple view the controls stay out of the way until asked for: the page
        # opens on the answer and the chart, with one button for "what if".
        self.try_btn = QPushButton("Try a change")
        self.try_btn.setObjectName("small")
        self.try_btn.setCheckable(True)
        self.try_btn.setToolTip("Change income or spending and watch the forecast move")
        self.try_btn.toggled.connect(self._apply_drawer)
        self.try_btn.setVisible(False)
        picker.addWidget(self.try_btn)
        # The page says what it thinks; this says how it knows. The video and the
        # written explanation, with everything quantitative that used to crowd
        # the risk caption, share one menu.
        self.explain_btn = explain_button([
            ("video", "Watch the video", lambda: video.play(self),
             "Open the forecast video in your video player"),
            ("how", NERD_LABEL, info_opener(NERD_LABEL, self._nerd_panel, self),
             NERD_TIP)])
        self.video_action = self.explain_btn.actions_by_key["video"]
        self.video_action.setEnabled(video.video_path() is not None)
        picker.addWidget(self.explain_btn)
        right.addWidget(picker_host)
        self.data_quality = DataQualityCard(self.main)
        right.addWidget(self.data_quality)
        # Cash leads. It is the account that actually runs out, and the floors
        # below (zero, starter buffer, emergency fund) are cash concepts — net
        # worth can look healthy while the chequing account is empty.
        cash_card = Card("Cash + net worth — projection")
        self.cash_view_note = hint("")
        self.cash_view_note.setTextFormat(Qt.TextFormat.PlainText)
        cash_card.body.addWidget(self.cash_view_note)
        self.cash_compare = hint("")
        self.cash_compare.setTextFormat(Qt.TextFormat.PlainText)
        self.cash_compare.hide()
        self.plot_cash = charts.new_plot()
        self.plot_cash.setAccessibleName("Cash projection chart")
        self.plot_cash.setMinimumHeight(180 + charts.AXIS_ALLOWANCE)
        cash_card.body.addWidget(self.plot_cash)
        # Both measures share one plot, date axis and dollar axis.
        self.plot_nw = self.plot_cash
        self.cash_series = SeriesRegistry()
        self.cash_legend = LegendToggles(
            _forecast_legend_entries(theme.BLUE, extras=[
                ("history", "bank cash history", theme.BLUE, "line"),
                ("baseline", "unchanged controls · middle outcome", theme.BLUE, "dash"),
                ("zero", "$0 · debt below this", theme.REFERENCE, "line"),
                ("starter", "starter buffer", theme.AMBER, "dash"),
                ("emergency", "cash buffer · essential spending", theme.ACCENT, "dash"),
                ("goals", "saved goals", theme.AMBER, "dash"),
            ]),
            on_toggle=lambda k, on: self.cash_series.set_visible(k, on))
        cash_card.body.addWidget(self.cash_legend)
        self.cash_legend_title = hint("Bank cash")
        cash_card.body.insertWidget(cash_card.body.indexOf(self.cash_legend), self.cash_legend_title)

        # Keep the cash answer above the scrolling charts. The headline and
        # next action lead; the supporting figures remain available on demand.
        self.risk_panel = QWidget()
        self.risk_panel.setSizePolicy(QSizePolicy.Policy.Preferred,
                                      QSizePolicy.Policy.Maximum)
        verdict = QVBoxLayout(self.risk_panel)
        verdict.setContentsMargins(0, 0, 0, 0)
        verdict.setSpacing(8)
        self.risk = QLabel("")
        self.risk.setObjectName("headline")
        self.risk.setWordWrap(True)
        self.risk.setTextFormat(Qt.TextFormat.PlainText)
        verdict.addWidget(self.risk)
        # "Caused by what?" is the next question every dated warning provokes,
        # and it only has an answer worth giving for a bounded dip — so the
        # button appears exactly when there is something nameable to name.
        risk_actions = QWidget()
        risk_row = FlowLayout(risk_actions, spacing=8)
        self.cause_btn = QPushButton("What's causing this?")
        self.cause_btn.setObjectName("small")
        self.cause_btn.setToolTip(
            "The bills and payments scheduled in the run-up to the low point, "
            "and a way to move one past payday")
        self.cause_btn.clicked.connect(self._show_causes)
        self.cause_btn.setVisible(False)
        risk_row.addWidget(self.cause_btn)
        self.risk_toggle = QPushButton("Forecast details")
        self.risk_toggle.setObjectName("link")
        self.risk_toggle.setCheckable(True)
        self.risk_toggle.toggled.connect(self._toggle_risk_details)
        risk_row.addWidget(self.risk_toggle)
        verdict.addWidget(risk_actions)
        self.risk_details = QWidget()
        details = QVBoxLayout(self.risk_details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(8)
        self.risk_detail = hint("")
        self.risk_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.risk_detail.setStyleSheet(f"color: {theme.TEXT}; font-size: 13px;")
        details.addWidget(self.risk_detail)
        self.summary = hint("")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setToolTip(
            "Scheduled cash flow excludes variable spending, loan-account payments, "
            "investment contributions and all saved what-if events, including repeats.")
        details.addWidget(self.summary)
        # Supporting text can grow with the number of accounts. Let it scroll
        # with the charts so opening details never consumes the whole window.
        right.insertWidget(0, self.risk_details)
        self.risk_details.hide()
        self.risk_panel.hide()
        self.cash_card = cash_card

        self.nw_compare = hint("")
        self.nw_compare.setTextFormat(Qt.TextFormat.PlainText)
        self.nw_compare.hide()
        self.nw_legend_title = hint("Net worth")
        cash_card.body.addWidget(self.nw_legend_title)
        self.nw_series = SeriesRegistry()
        self.nw_legend = LegendToggles(
            _forecast_legend_entries(theme.ACCENT, extras=[
                ("history", "net worth history", theme.ACCENT, "line"),
                ("det", "average inputs · no random variation", theme.AMBER, "dash"),
                ("baseline", "unchanged controls · middle outcome", theme.ACCENT, "dash"),
                ("zero", "$0 · net worth", theme.RED, "line"),
                ("goals", "saved goals", theme.AMBER, "dash"),
            ]),
            on_toggle=lambda k, on: self.nw_series.set_visible(k, on))
        cash_card.body.addWidget(self.nw_legend)
        cash_card.body.addWidget(self.cash_compare)
        cash_card.body.addWidget(self.nw_compare)
        for legend in (self.cash_legend, self.nw_legend):
            meanings = {
                "Middle 80% of outcomes": "At each date, 10% of simulated balances are "
                    "below this band and 10% above it. It does not contain 80% of entire paths.",
                "Middle 50% of outcomes": "At each date, 25% of simulated balances are "
                    "below this band and 25% above it.",
                "Middle outcome": "The median: at each date, half the simulated balances "
                    "are above this line and half below.",
            }
            for label in legend.findChildren(QLabel):
                if label.text() in meanings:
                    label.setToolTip(meanings[label.text()])

        # ----- analysis: tornado + backtest -----
        an = Card("Sensitivity and backtesting")
        tabs_host = QWidget()
        tabs = FlowLayout(tabs_host, spacing=8)
        self.btn_tornado = QPushButton("Sensitivity")
        self.btn_tornado.setToolTip("Compare how individual controls change ending net worth")
        self.btn_tornado.setObjectName("small")
        self.btn_tornado.setCheckable(True)
        self.btn_tornado.setChecked(True)
        self.btn_backtest = QPushButton("Backtest")
        self.btn_backtest.setToolTip("Rebuild an earlier forecast and compare it with recorded cash")
        self.btn_backtest.setObjectName("small")
        self.btn_backtest.setCheckable(True)
        self.btn_tornado.clicked.connect(lambda: self._set_analysis("tornado"))
        self.btn_backtest.clicked.connect(lambda: self._set_analysis("backtest"))
        tabs.addWidget(self.btn_tornado)
        tabs.addWidget(self.btn_backtest)
        self.bt_lookback = QComboBox()
        for lbl, d in (("6 months back", 180), ("9 months back", 270),
                       ("1 year back", 365), ("2 years back", 730)):
            self.bt_lookback.addItem(lbl, d)
        self.bt_lookback.setVisible(False)
        self.bt_lookback.currentIndexChanged.connect(lambda _i: self._run_analysis())
        tabs.addWidget(self.bt_lookback)
        an.body.addWidget(tabs_host)
        self.plot_analysis = charts.new_plot(date_axis=False, money_left=False,
                                             money_bottom=True)
        self.plot_analysis.setAccessibleName("Sensitivity analysis chart")
        self.plot_analysis.setMinimumHeight(100 + charts.AXIS_ALLOWANCE)
        an.body.addWidget(self.plot_analysis)
        self.analysis_note = hint("")
        an.body.addWidget(self.analysis_note)
        self.analysis_card = an

        self.chart_split = vsplit(cash_card, an, sizes=[700, 260])
        self.chart_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        for c in (cash_card, an):
            c.setMinimumHeight(300 if c is cash_card else 260)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        right.addWidget(self.chart_split, 1)

        chart_scroll = QScrollArea()
        chart_scroll.setWidgetResizable(True)
        chart_scroll.setMinimumWidth(320)
        chart_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chart_scroll.setWidget(chart_col)
        self.chart_scroll = chart_scroll
        results = QWidget()
        result_layout = QVBoxLayout(results)
        result_layout.setContentsMargins(12, 0, 0, 0)
        result_layout.setSpacing(16)
        self.page_error = hint("")
        self.page_error.setTextFormat(Qt.TextFormat.PlainText)
        self.page_error.setStyleSheet(f"color: {theme.RED};")
        self.page_error.hide()
        result_layout.addWidget(self.page_error)
        self.retry_btn = QPushButton("Refresh forecast")
        self.retry_btn.setObjectName("small")
        self.retry_btn.clicked.connect(self.refresh)
        self.retry_btn.hide()
        result_layout.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.setup_guidance = QWidget()
        setup_layout = QVBoxLayout(self.setup_guidance)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(6)
        self.setup_title = QLabel()
        self.setup_title.setTextFormat(Qt.TextFormat.PlainText)
        self.setup_title.setObjectName("headline")
        self.setup_title.setWordWrap(True)
        self.setup_description = hint("")
        self.setup_description.setTextFormat(Qt.TextFormat.PlainText)
        setup_layout.addWidget(self.setup_title)
        setup_layout.addWidget(self.setup_description)
        setup_actions = QWidget()
        setup_buttons = FlowLayout(setup_actions, spacing=8)
        self.setup_import_btn = QPushButton("Import transactions…")
        self.setup_import_btn.setObjectName("small")
        self.setup_import_btn.clicked.connect(self._import_spending_history)
        self.setup_history_btn = QPushButton("Choose history")
        self.setup_history_btn.setObjectName("small")
        self.setup_history_btn.clicked.connect(self._reveal_history)
        self.setup_account_btn = QPushButton("Add an account…")
        self.setup_account_btn.setObjectName("small")
        self.setup_account_btn.clicked.connect(self.main.open_account)
        for button in (self.setup_history_btn, self.setup_import_btn, self.setup_account_btn):
            setup_buttons.addWidget(button)
        setup_layout.addWidget(setup_actions)
        self.setup_guidance.hide()
        result_layout.addWidget(self.setup_guidance)
        result_layout.addWidget(self.risk_panel)
        self.forecast_warning = hint("")
        self.forecast_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.forecast_warning.setStyleSheet(f"color: {theme.AMBER}; font-size: 13px;")
        self.forecast_warning.hide()
        right.insertWidget(0, self.forecast_warning)
        self.empty_note = hint("")
        self.empty_note.hide()
        result_layout.addWidget(self.empty_note)
        result_layout.addWidget(chart_scroll, 1)
        result_layout.addStretch(0)
        split.addWidget(results)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([356, 900])

        # QSplitter does not pass height-for-width through to its cards.
        # Reserve the full wrapped text/legend height inside the scroll area.
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._fit_chart_content)
        for widget in (chart_scroll.viewport(), chart_col, self.risk_panel, self.risk_details,
                       self.data_quality, cash_card, an):
            widget.installEventFilter(self)

        # Coalesce keyboard/spinner edits. Mouse drags update on release so a
        # slow, long-horizon simulation cannot interrupt the drag itself.
        self._connect_slider(self.income_slider)
        for control in (self.paths, self.ef_months, self.extra_loan,
                        self.extra_invest):
            control.valueChanged.connect(self._schedule_run)
        self.horizon.currentIndexChanged.connect(self._schedule_run)
        self.overlay.toggled.connect(self._schedule_run)
        self.snowball.toggled.connect(self._schedule_run)

    # ---------------------------------------------------------------
    def _build_start_panel(self) -> QWidget:
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        content = QWidget()
        content.setMaximumWidth(620)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QLabel("See where your cash could go")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        title.setWordWrap(True)
        layout.addWidget(title)
        description = hint("Start with today’s balances, then import your bank statements. "
                           "Your paychecks, bills, and spending history shape the forecast.")
        description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(description)
        layout.addWidget(_ForecastIllustration())
        actions = QWidget()
        buttons = FlowLayout(actions, spacing=10)
        self.start_account_btn = QPushButton("Add your first account…")
        self.start_account_btn.setObjectName("primary")
        self.start_account_btn.clicked.connect(self.main.open_account)
        self.start_demo_btn = QPushButton("Try a demo")
        self.start_demo_btn.setToolTip("Explore fictional data in a separate ledger. "
                                      "Return to your own data from the File menu.")
        self.start_demo_btn.clicked.connect(self._try_demo)
        buttons.addWidget(self.start_account_btn)
        buttons.addWidget(self.start_demo_btn)
        layout.addWidget(actions)
        self.start_video_btn = QPushButton("Watch the video")
        self.start_video_btn.setObjectName("small")
        self.start_video_btn.clicked.connect(lambda: video.play(self))
        layout.addWidget(self.start_video_btn, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(content, 10)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        return panel

    def _set_empty(self, empty: bool) -> None:
        self.start_panel.setVisible(empty)
        self.forecast_split.setVisible(not empty)

    def _try_demo(self) -> None:
        self.main._load_demo("balanced", confirm=False)

    def _import_spending_history(self) -> None:
        accounts = [account for account in self.store.accounts() if account.type.is_cashlike]
        target = (accounts[0] if len(accounts) == 1 else
                  self.main._pick_import_account(accounts) if accounts else None)
        if target is not None:
            self.main.import_for_account(target)

    def _reveal_history(self) -> None:
        if self.main.simple_mode:
            self.main._toggle_mode()
        self.history_card.set_expanded(True)
        QTimer.singleShot(0, self._scroll_to_history)

    def _scroll_to_history(self) -> None:
        self.control_scroll.ensureWidgetVisible(self.history_pick, 0, 12)
        self.history_pick.setFocus()

    def _dismiss_comparison_tip(self) -> None:
        self._comparison_tip_seen = True
        prefs.app_settings().setValue(_COMPARISON_TIP_SETTING, True)
        self.comparison_tip.hide()

    def _update_onboarding(self, res: ForecastResult) -> None:
        limited = res.history_weeks < 4
        self.setup_guidance.setVisible(limited)
        if limited:
            has_cash = any(account.type.is_cashlike for account in self.store.accounts())
            selected = self.history_pick.currentData() not in ("auto", "all")
            self.setup_import_btn.setVisible(has_cash)
            self.setup_account_btn.setVisible(not has_cash)
            self.setup_history_btn.setVisible(has_cash and selected)
            if not has_cash:
                self.setup_title.setText("Add the accounts you spend from")
                self.setup_description.setText(
                    "A checking, savings, or credit-card account and its transactions "
                    "let the forecast include your everyday spending.")
            elif selected:
                self.setup_title.setText("Choose the weeks behind this forecast")
                self.setup_description.setText(
                    "This period has fewer than four complete spending weeks. "
                    "Everyday spending is omitted. Choose a longer period or import more statements.")
            else:
                self.setup_title.setText("Add the weeks behind this forecast")
                self.setup_description.setText(
                    "Everyday spending is omitted. Import statements covering at least "
                    "four complete weeks to include it.")
        useful_categories = [row for row in self._cat_sliders
                             if res.monthly_spending_reference.get(row.name, 0) > 0]
        can_compare = not limited and bool(useful_categories) and self.overlay.isChecked()
        if (can_compare and not self._comparison_tip_seen
                and any(row.value() != 1 for row in useful_categories)):
            self._dismiss_comparison_tip()
        self.comparison_tip.setVisible(can_compare and not self._comparison_tip_seen)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self._layout_timer.start(0)
        return super().eventFilter(watched, event)

    def _fit_chart_content(self) -> None:
        column = self.chart_scroll.widget()
        width = self.chart_scroll.viewport().width()
        # The fixed verdict sits outside the chart scroller. Reserve all its
        # wrapped lines when the controls leave a narrow result column.
        risk_height = max(0, self.risk.heightForWidth(self.risk.width()))
        if self.risk.minimumHeight() != risk_height:
            self.risk.setMinimumHeight(risk_height)
        panel_height = self._column_height(self.risk_panel.layout(), self.risk_panel.width())
        if self.risk_panel.minimumHeight() != panel_height:
            self.risk_panel.setMinimumHeight(panel_height)
        for label in (self.forecast_warning, self.risk_detail, self.summary):
            height = max(0, label.heightForWidth(width))
            if label.minimumHeight() != height:
                label.setMinimumHeight(height)
        # A nested details layout otherwise reports its compressed height to
        # the outer layout, even when its labels need more lines at this width.
        details_height = self._column_height(self.risk_details.layout(), width)
        if self.risk_details.minimumHeight() != details_height:
            self.risk_details.setMinimumHeight(details_height)
        for card, base, plot in ((self.cash_card, 300, self.plot_cash),
                                (self.analysis_card, 260, self.plot_analysis)):
            required = max(base, self._column_height(card.layout(), card.width(), plot))
            if card.minimumHeight() != required:
                card.setMinimumHeight(required)
        visible = [card for card in (self.cash_card, self.analysis_card)
                   if card.isVisibleTo(self)]
        minimum = (sum(card.minimumHeight() for card in visible)
                   + self.chart_split.handleWidth() * max(0, len(visible) - 1))
        if self.chart_split.minimumHeight() != minimum:
            self.chart_split.setMinimumHeight(minimum)
        height = self._column_height(column.layout(), width)
        if column.minimumHeight() != max(0, height):
            column.setMinimumHeight(max(0, height))

    @staticmethod
    def _column_height(layout, width: int, plot=None) -> int:
        """Height of a vertical stack, reserving minimum rather than preferred plot size.

        Qt caches nested height-for-width values across splitter resizes. Read
        each visible item directly so new text cannot overlap the next item.
        """
        margins = layout.contentsMargins()
        inner_width = max(1, width - margins.left() - margins.right())
        heights = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.isEmpty():
                continue
            widget = item.widget()
            if widget is plot and plot is not None:
                height = plot.minimumHeight()
            elif (widget is not None and widget.sizePolicy().verticalPolicy()
                  == QSizePolicy.Policy.Ignored):
                height = widget.minimumHeight()
            elif widget is not None:
                wrapped = widget.heightForWidth(inner_width)
                height = max(widget.minimumHeight(),
                             wrapped if wrapped >= 0 else widget.sizeHint().height())
            else:
                nested = item.layout()
                wrapped = nested.totalHeightForWidth(inner_width) if nested else -1
                height = wrapped if wrapped >= 0 else item.sizeHint().height()
            heights.append(height)
        return (margins.top() + margins.bottom() + sum(heights)
                + layout.spacing() * max(0, len(heights) - 1))

    def set_simple(self, on: bool) -> None:
        """Hide the machinery, keep the answer.

        What goes is the apparatus of *running* a simulation — how many paths,
        which charts, sensitivity, backtesting, the debt-and-investing knobs.
        What stays is the question people came with: how long, what if I spent
        less, what if this bill landed, and does the money hold up. None of the
        hidden panels are the answer; they are ways of interrogating it, and
        the numbers they produce are all still under For nerds.
        """
        self._simple = on
        self.try_btn.setVisible(on)
        if not on:
            self.try_btn.setChecked(False)
        # Changes apply as they are made, so a button to apply them is only a
        # question ("do I press this?") for someone who was handed the page.
        self.run_btn.setVisible(not on)
        self._apply_drawer()
        self.paths_row.setVisible(not on)
        self.history_card.setVisible(not on)
        self.overlay.setVisible(not on)
        self.debt_card.setVisible(not on)
        self.view_row.setVisible(not on)
        self.show_analysis.setVisible(not on)
        if on:
            # Cash is the account that actually runs out, and the floors under
            # the chart are cash concepts — it is the right single chart.
            i = self.view_pick.findData("cash")
            if i >= 0:
                self.view_pick.setCurrentIndex(i)
            self.show_analysis.setChecked(False)
        self._apply_view()

    def _apply_drawer(self, *_args) -> None:
        """In Simple view the controls are a drawer; in Everything they are the column."""
        wanted = (not self._simple) or self.try_btn.isChecked()
        was_open = self.control_scroll.isVisibleTo(self)
        self.try_btn.setText("Hide changes" if self.try_btn.isChecked() else "Try a change")
        self.control_scroll.setVisible(wanted)
        if wanted and not was_open:
            total = max(self.forecast_split.width(), 700)
            self.forecast_split.setSizes([356, total - 356])

    def open_controls(self) -> None:
        """Show the controls, whichever view this is. Links that mean "what if" use it."""
        if self._simple:
            self.try_btn.setChecked(True)

    def refresh(self) -> None:
        try:
            self._refresh_content()
        except Exception as error:
            self._events_ready = False
            self.add_event_btn.setEnabled(False)
            self._forecast_failed(error, "The ledger could not be loaded. "
                                  "Check that it is available, then select Refresh forecast.")

    def _forecast_failed(self, error, message: str) -> None:
        self._set_empty(False)
        self._update_timer.stop()
        self._clear_results()
        self.data_quality.clear()
        self._refresh_needed = True
        self.chart_scroll.hide()
        self.run_btn.setEnabled(True)
        self.page_error.setText(message)
        self.page_error.setToolTip(str(error))
        self.page_error.show()
        self.retry_btn.show()

    def _refresh_content(self) -> None:
        self._update_timer.stop()
        self._refresh_needed = False
        self.page_error.hide()
        self.retry_btn.hide()
        self.chart_scroll.show()
        self._load_history()
        self.follow_goals.blockSignals(True)
        self.follow_goals.setChecked(goal_plan_enabled(self.store))
        self.follow_goals.blockSignals(False)
        self.cash_view.blockSignals(True)
        cash_view = self.store.get_setting('forecast_cash_view', 'bank')
        self.cash_view.setCurrentIndex(max(0, self.cash_view.findData(cash_view)))
        self.cash_view.blockSignals(False)
        self._sync_goal_plan_controls()
        # rebuild category sliders, preserving positions by name
        prev = {s.name: s.slider.value() for s in self._cat_sliders}
        _clear_layout(self.cat_box)
        self._cat_sliders = []
        cs = category_spending(self.store.transactions(), days=180, top=6)
        for cat in cs.index:
            if cat == "Debt payment":
                continue
            row = SliderRow(cat)
            if cat in prev:
                row.slider.setValue(prev[cat])
            self.cat_box.addWidget(row)
            self._cat_sliders.append(row)
            self._connect_slider(row)
        # Simple view offers the three biggest categories, plus any other one that is
        # currently set away from 100%, so a change made in Everything is never hidden.
        for rank, row in enumerate(self._cat_sliders):
            row.setVisible(not self._simple or rank < 3 or row.value() != 1)

        self._rebuild_goals()
        self._rebuild_events()
        self._apply_view()

        has_accounts = bool(self.store.accounts())
        self._set_empty(not has_accounts)
        self.run_btn.setEnabled(has_accounts)
        if has_accounts:
            self.run()
        else:
            self.data_quality.refresh(self.store)
            self._clear_results()

    def _rebuild_goals(self) -> None:
        _clear_layout(self.goal_box)
        self._goal_labels = {}
        for g in self.store.goals():
            roww = QWidget()
            rl = QHBoxLayout(roww)
            rl.setContentsMargins(0, 0, 0, 0)
            col = QVBoxLayout()
            col.setSpacing(1)
            title = QLabel(f"Goal: {g.name} — {fmt_money(g.amount)}"
                           f"  ·  {dict(cash='cash', networth='net worth', savings='savings').get(g.kind, g.kind)}")
            title.setWordWrap(True)
            title.setTextFormat(Qt.TextFormat.PlainText)
            eta = hint("Refresh the forecast for an estimate.")
            eta.setTextFormat(Qt.TextFormat.PlainText)
            eta.setToolTip("The estimate uses average inputs. Band crossings show when the "
                           "upper and lower edges of the 80% band first reach the goal; "
                           "they are not a probability range for the arrival date.")
            col.addWidget(title)
            col.addWidget(eta)
            rl.addLayout(col, 1)
            self.goal_box.addWidget(roww)
            if g.kind == "savings":
                eta.setText(f"{fmt_money(g.saved_amount)} set aside. See Goals for "
                            "planned contributions and completion dates.")
                eta.setToolTip("This forecast does not transfer money into savings goals.")
            else:
                self._goal_labels[g.id] = eta
        if not self.store.goals():
            self.goal_box.addWidget(hint("No goals yet — create one on the Goals page."))

    def _reset_scenario(self) -> None:
        self._changing_controls = True
        try:
            self.income_slider.reset()
            for s in self._cat_sliders:
                s.reset()
            self.extra_loan.setValue(0)
            self.extra_invest.setValue(0)
            self.snowball.setChecked(True)
        finally:
            self._changing_controls = False
        self._schedule_run()

    def _connect_slider(self, row: SliderRow) -> None:
        row.slider.valueChanged.connect(
            lambda _v: self._schedule_run() if not row.slider.isSliderDown() else None)
        row.slider.sliderPressed.connect(self._update_timer.stop)
        row.slider.sliderReleased.connect(self._schedule_run)

    def _schedule_run(self, *_args) -> None:
        if (not self._changing_controls and self.isVisible()
                and self.run_btn.isEnabled()):
            self._update_timer.start()

    def hideEvent(self, event) -> None:
        self._update_timer.stop()
        super().hideEvent(event)

    def _clear_results(self) -> None:
        self.comparison_tip.hide()
        self.setup_guidance.hide()
        self._res = self._bt = None
        self._base = None
        self._sens = []
        for label in (self.cash_compare, self.nw_compare):
            label.clear()
            label.hide()
        for row in (self.income_slider, *self._cat_sliders):
            row.set_monthly_reference(None)
        for plot in (self.plot_cash, self.plot_analysis):
            plot.clear()
        self.cash_series.reset()
        self.nw_series.reset()
        self.risk.clear()
        self.risk_detail.clear()
        self.risk_toggle.setChecked(False)
        self.risk_panel.hide()
        self.forecast_warning.clear()
        self.forecast_warning.hide()
        self.empty_note.clear()
        self.empty_note.hide()
        self.analysis_note.clear()
        self.analysis_note.setToolTip("")
        self.summary.clear()
        self.history_note.clear()
        self.cause_btn.hide()
        for label in self._goal_labels.values():
            label.setText("Refresh the forecast for an estimate.")
            label.setStyleSheet("")

    def _toggle_risk_details(self, expanded: bool) -> None:
        self.risk_details.setVisible(expanded and self.risk_panel.isVisibleTo(self))
        self.risk_toggle.setText("Hide details" if expanded else "Forecast details")

    def reset_for_store(self) -> None:
        """A demo's what-if controls and plotted results must not follow it home."""
        self._reset_scenario()
        self._update_timer.stop()
        self._clear_results()
        self.data_quality.clear()
        self.page_error.hide()
        self.retry_btn.hide()
        self.event_error.hide()
        self._cancel_event_edit()
        self.e_when.setDate(QDate.currentDate().addMonths(6))
        self._events_ready = False
        self._refresh_needed = True
        try:
            self._load_history()
            self._rebuild_goals()
            self._rebuild_events()
        except Exception as error:
            self.add_event_btn.setEnabled(False)
            self._forecast_failed(error, "The ledger could not be loaded. "
                                  "Check that it is available, then select Refresh forecast.")

    def _scenario(self) -> Scenario:
        return Scenario(
            category_multipliers={s.name: s.value() for s in self._cat_sliders},
            income_multiplier=self.income_slider.value(),
            extra_loan_payment=0.0 if self.follow_goals.isChecked() else self.extra_loan.value(),
            extra_investing=self.extra_invest.value(),
            snowball=True if self.follow_goals.isChecked() else self.snowball.isChecked(),
            follow_goal_plan=self.follow_goals.isChecked(),
        )

    def _sync_goal_plan_controls(self) -> None:
        following = self.follow_goals.isChecked()
        self.extra_loan.setEnabled(not following)
        self.snowball.setEnabled(not following)

    def _goal_plan_changed(self, checked) -> None:
        try:
            self.store.set_setting('forecast_goal_plan', bool(checked))
        except Exception as error:
            self.follow_goals.blockSignals(True)
            self.follow_goals.setChecked(not checked)
            self.follow_goals.blockSignals(False)
            self._sync_goal_plan_controls()
            self._forecast_failed(error, 'The Goals plan setting could not be saved. Refresh the forecast to try again.')
            return
        self._sync_goal_plan_controls()
        self.main.refresh_header()
        self._schedule_run()

    def _cash_view_changed(self, *_args) -> None:
        try:
            self.store.set_setting('forecast_cash_view', self.cash_view.currentData())
        except Exception as error:
            self.cash_view.blockSignals(True)
            saved = self.store.get_setting('forecast_cash_view', 'bank')
            self.cash_view.setCurrentIndex(max(0, self.cash_view.findData(saved)))
            self.cash_view.blockSignals(False)
            self._forecast_failed(error, 'The cash view could not be saved. Refresh the forecast to try again.')
            return
        if self._res is not None:
            self._draw_projection()

    def _load_history(self) -> None:
        history = ForecastHistory.from_setting(self.store.get_setting(HISTORY_SETTING))
        controls = (self.history_pick, self.history_since, self.history_refit)
        for control in controls:
            control.blockSignals(True)
        key = history.weeks if history.mode == "weeks" else history.mode
        index = self.history_pick.findData(key)
        if index < 0:
            self.history_pick.addItem(f"Last {history.weeks} weeks", key)
            index = self.history_pick.findData(key)
        self.history_pick.setCurrentIndex(max(0, index))
        d = history.since or date.today() - timedelta(days=183)
        self.history_since.setDate(QDate(d.year, d.month, d.day))
        self.history_refit.setChecked(history.refit_recurring)
        self.history_since_row.setVisible(history.mode == "since")
        for control in controls:
            control.blockSignals(False)

    def _history_changed(self, *_args) -> None:
        key = self.history_pick.currentData()
        qd = self.history_since.date()
        history = ForecastHistory(
            mode="weeks" if isinstance(key, int) else key,
            weeks=key if isinstance(key, int) else 78,
            since=date(qd.year(), qd.month(), qd.day()) if key == "since" else None,
            refit_recurring=self.history_refit.isChecked(),
        )
        self.history_since_row.setVisible(key == "since")
        try:
            self.store.set_setting(HISTORY_SETTING, history.to_setting())
        except Exception as error:
            try:
                self._load_history()
            except Exception:
                pass
            self._forecast_failed(error, "The history setting could not be saved. "
                                  "Check that this ledger is writable, then select Refresh forecast.")
            return
        self.main.refresh_header()
        self._schedule_run()

    # ---------------- what-if events ----------------
    def _rebuild_events(self) -> None:
        self._events_ready = False
        self.add_event_btn.setEnabled(False)
        evs = self.store.events()
        _clear_layout(self.event_box)
        store = self.store
        for e in evs:
            roww = QWidget()
            rl = QHBoxLayout(roww)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            chk = QCheckBox()
            chk.setChecked(e.active)
            chk.setToolTip("Include this event in the projection")
            chk.setAccessibleName(f"Include {e.label} in the forecast")
            chk.stateChanged.connect(
                lambda st, event=e, source=store: self._toggle_event(
                    event.id, bool(st), source, event))
            col = theme.RED if e.is_expense else theme.ACCENT
            col_layout = QVBoxLayout()
            col_layout.setSpacing(2)
            lbl = _EventNameLabel(e.label)
            lbl.setStyleSheet(f"color: {col if e.active else theme.MUTED};")
            schedule = (f"Once · {fmt_day(e.when)}" if e.repeat == "once" else
                        f"{EVENT_REPEATS.get(e.repeat, e.repeat)} from {fmt_day(e.when)}")
            if e.until is not None:
                schedule += f" through {fmt_day(e.until)}"
            details = QLabel(f"{'−' if e.is_expense else '+'}${abs(e.amount):,.2f} · {schedule}")
            details.setTextFormat(Qt.TextFormat.PlainText)
            details.setObjectName("hint")
            details.setWordWrap(True)
            col_layout.addWidget(lbl)
            col_layout.addWidget(details)
            edit = QPushButton("Edit")
            edit.setObjectName("small")
            edit.setToolTip(f"Edit event: {e.label}")
            edit.setAccessibleName(f"Edit event: {e.label}")
            edit.clicked.connect(lambda _c, event=e, source=store:
                                 self._edit_event(event.id, source, event))
            rm = QPushButton("✕")
            rm.setObjectName("small")
            rm.setFixedWidth(26)
            rm.setToolTip(f"Remove event: {e.label}")
            rm.setAccessibleName(f"Remove event: {e.label}")
            rm.clicked.connect(lambda _c, event=e, source=store:
                               self._remove_event(event.id, source, event))
            rl.addWidget(chk)
            rl.addLayout(col_layout, 1)
            actions = QVBoxLayout()
            actions.setSpacing(4)
            actions.addWidget(edit, 0, Qt.AlignmentFlag.AlignRight)
            actions.addWidget(rm, 0, Qt.AlignmentFlag.AlignRight)
            rl.addLayout(actions)
            self.event_box.addWidget(roww)
        if not evs:
            self.event_box.addWidget(hint("None yet — add one below."))
        self._events_ready = True
        self.add_event_btn.setEnabled(True)

    def _event_problem(self, message: str, error=None, field=None) -> None:
        self.event_error.setText(message)
        self.event_error.setToolTip(str(error) if error is not None else "")
        self.event_error.show()
        if field is not None:
            field.setFocus()

    def _event_current(self, event_id, source, expected) -> bool:
        if not self._events_ready or (source is not None and source is not self.store):
            return False
        current = next((e for e in self.store.events() if e.id == event_id), None)
        return current is not None and (expected is None or current == expected)

    def _after_event_write(self) -> None:
        try:
            self._rebuild_events()
        except Exception as error:
            self._forecast_failed(error, "The event was saved, but the ledger could not be reloaded. "
                                  "Select Refresh forecast to reload it.")
            return
        if self.run_btn.isEnabled():
            self.run()
        self.main.refresh_header()

    def _sync_event_repeat(self) -> None:
        repeating = self.e_repeat.currentData() != "once"
        self.e_when_label.setText("Starts on" if repeating else "Date")
        self.e_until_enabled.setVisible(repeating)
        self.e_until.setVisible(repeating)

    def _cancel_event_edit(self) -> None:
        self._editing_event = None
        self.e_name.clear()
        self.e_amount.setValue(0)
        self.e_repeat.setCurrentIndex(0)
        self.e_until_enabled.setChecked(False)
        self.e_when.setMinimumDate(QDate.currentDate().addDays(1))
        self.add_event_btn.setText("Add event")
        self.cancel_event_btn.hide()
        self.event_edit_note.hide()
        self.event_error.hide()

    def _edit_event(self, event_id, source=None, expected=None) -> None:
        try:
            if not self._event_current(event_id, source, expected):
                return
            event = next(e for e in self.store.events() if e.id == event_id)
        except Exception as error:
            self._event_problem("The event could not be loaded. Select Refresh forecast and try Edit again.", error)
            return
        self._editing_event = (self.store, event)
        self.e_name.setText(event.label)
        self.e_amount.setValue(event.amount)
        # QDateTimeEdit rejects minima before year 100, silently keeping the
        # previous future-only limit. That would move an old schedule anchor.
        self.e_when.setMinimumDate(QDate(100, 1, 1))
        self.e_when.setDate(QDate(event.when.year, event.when.month, event.when.day))
        self.e_repeat.setCurrentIndex(max(0, self.e_repeat.findData(event.repeat)))
        until = (QDate(event.until.year, event.until.month, event.until.day)
                 if event.until is not None else self.e_when.date().addYears(1))
        self.e_until.setDate(until if until.isValid() else self.e_when.date())
        self.e_until_enabled.setChecked(event.until is not None)
        self.add_event_btn.setText("Save changes")
        self.cancel_event_btn.show()
        self.event_edit_note.setText("Editing saved event")
        self.event_edit_note.setToolTip(event.label)
        self.event_edit_note.show()
        self.event_error.hide()
        self.e_name.setFocus()
        self.e_name.selectAll()
        self.event_card.set_expanded(True)
        QTimer.singleShot(0, self._reveal_event_editor)

    def _reveal_event_editor(self) -> None:
        if self._editing_event is not None and self._editing_event[0] is self.store:
            self.control_scroll.ensureWidgetVisible(self.e_name, 0, 16)

    def _add_event(self) -> None:
        if not self._events_ready:
            return
        name = self.e_name.text().strip()
        if not name:
            self._event_problem("Enter a name for this event.", field=self.e_name)
            return
        if not self.e_amount.hasAcceptableInput():
            self._event_problem("Enter an amount between −$1,000,000 and $1,000,000.",
                                field=self.e_amount)
            return
        typed_amount = self.e_amount.valueFromText(self.e_amount.text())
        if not self.e_amount.minimum() <= typed_amount <= self.e_amount.maximum():
            self._event_problem("Enter an amount between −$1,000,000 and $1,000,000.",
                                field=self.e_amount)
            return
        self.e_amount.interpretText()
        amt = self.e_amount.value()
        if abs(amt) < 0.01:
            self._event_problem("Enter a non-zero amount: negative for a cost, positive for income.",
                                field=self.e_amount)
            return
        qd = self.e_when.date()
        when = date(qd.year(), qd.month(), qd.day())
        repeat = self.e_repeat.currentData()
        editing = self._editing_event
        # Retain a past schedule anchor while editing an established series.
        # Unchanged past one-offs may also have their name/amount corrected.
        needs_future = (editing is None or
                        (repeat == "once" and when != editing[1].when))
        if needs_future and when <= date.today():
            self._event_problem("Choose a future date for this event.", field=self.e_when)
            return
        until = None
        if repeat != "once" and self.e_until_enabled.isChecked():
            qd = self.e_until.date()
            until = date(qd.year(), qd.month(), qd.day())
            if until < when:
                self._event_problem("Choose an end date on or after the start date.", field=self.e_until)
                return
        try:
            if editing is None:
                self.store.add_event(OneOffEvent(name, when, amt, repeat=repeat, until=until))
            else:
                source, expected = editing
                if not self._event_current(expected.id, source, expected):
                    self._event_problem("This event changed or was removed while you were editing. "
                                        "Your entries are kept. Cancel edit, then reopen Edit to load it again.")
                    return
                self.store.update_event(expected.id, label=name, when=when, amount=amt,
                                        repeat=repeat, until=until)
        except Exception as error:
            self._event_problem("The event could not be saved. Your entries are still here. "
                                "Check that this ledger is writable, then try saving again.", error)
            return
        self._cancel_event_edit()
        self._after_event_write()

    def _toggle_event(self, event_id: int, on: bool, source=None, expected=None) -> None:
        self._change_event(event_id, source, expected, active=on)

    def _remove_event(self, event_id: int, source=None, expected=None) -> None:
        self._change_event(event_id, source, expected)

    def _change_event(self, event_id, source, expected, **fields) -> None:
        try:
            if not self._event_current(event_id, source, expected):
                return
            if fields:
                self.store.update_event(event_id, **fields)
            else:
                self.store.delete_event(event_id)
        except Exception as error:
            self._event_problem("The event could not be changed. "
                                "Check that this ledger is writable, then try again.", error)
            try:
                self._rebuild_events()
            except Exception as load_error:
                self._forecast_failed(load_error, "The events could not be loaded. "
                                      "Select Refresh forecast to try again.")
            return
        self.event_error.hide()
        self._after_event_write()

    def _apply_view(self) -> None:
        """Show only the charts the user asked for, and give them the room."""
        key = self.view_pick.currentData() or "both"
        show_analysis = self.show_analysis.isChecked()
        if key == "analysis":
            show_cash = show_nw = False
            show_analysis = True
        else:
            show_cash = key in ("both", "cash")
            show_nw = key in ("both", "networth")
        self.cash_card.setVisible(show_cash or show_nw)
        self.risk_panel.setVisible(show_cash and self._res is not None
                                   and self._res.shortfall is not None)
        self.risk_details.setVisible(self.risk_toggle.isChecked()
                                     and self.risk_panel.isVisibleTo(self))
        for widget in (self.cash_legend_title, self.cash_legend, self.cash_view_note):
            widget.setVisible(show_cash)
        for widget in (self.nw_legend_title, self.nw_legend):
            widget.setVisible(show_nw)
        self.analysis_card.setVisible(show_analysis)
        visible_cards = [card for card, visible in zip(
            (self.cash_card, self.analysis_card),
            (show_cash or show_nw, show_analysis)) if visible]
        self.chart_split.setMinimumHeight(
            sum(card.minimumHeight() for card in visible_cards)
            + self.chart_split.handleWidth() * max(0, len(visible_cards) - 1))
        # Hidden panes take no space. The weights divide the visible charts;
        # minimum card heights keep plots and notes readable. The scroll area
        # absorbs overflow instead of changing the main window's minimum size.
        weights = [5, 5 if key == "analysis" else 2]
        total = max(1, self.chart_split.height())
        live = [w if vis else 0 for w, vis in
                zip(weights, (show_cash or show_nw, show_analysis))]
        s = sum(live) or 1
        self.chart_split.setSizes([int(total * w / s) for w in live])
        if self._res is not None:
            self._draw_projection()
        self._maybe_run_analysis()

    # ---------------- analysis panel ----------------
    def _maybe_run_analysis(self) -> None:
        if (self._analysis_dirty and self._res is not None
                and self.analysis_card.isVisibleTo(self)):
            self._run_analysis()

    def _set_analysis(self, which: str) -> None:
        if which != self._analysis:
            self.plot_analysis.forget_view()
        self._analysis = which
        self.btn_tornado.setChecked(which == "tornado")
        self.btn_backtest.setChecked(which == "backtest")
        self.bt_lookback.setVisible(which == "backtest")
        self._run_analysis()

    def _run_analysis(self) -> None:
        self._analysis_dirty = False
        self.analysis_note.setToolTip("")
        self.analysis_note.setStyleSheet("")
        which = getattr(self, "_analysis", "tornado")
        if which == "tornado":
            self._sens = []
        else:
            self._bt = None
        p = self.plot_analysis
        p.clear()
        if self._res is None:
            self.analysis_note.setText("Refresh the forecast before running this analysis.")
            return
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        p.getAxis("left").setTicks(None)
        # The two analyses draw different things on the same widget, so undo
        # whatever the other one left behind before drawing.
        charts.unlock_view(p)
        p.setMouseEnabled(x=True, y=True)
        try:
            if which == "tornado":
                self._render_tornado(p)
            else:
                self._render_backtest(p)
        except Exception as e:
            p.clear()
            if which == "tornado":
                self._sens = []
            else:
                self._bt = None
            self.analysis_note.setText("This analysis could not be calculated. "
                                       "Refresh the forecast to try again.")
            self.analysis_note.setToolTip(f"Details: {e}")

    def _render_tornado(self, p) -> None:
        cats = [s.name for s in self._cat_sliders]
        if not cats:
            self.analysis_note.setText("Import some transactions first.")
            return
        H = self.horizon.currentData()
        sens = sensitivity(self.store, self._scenario(), H, cats)
        if not sens:
            self.analysis_note.setText("No controls to compare. Import transactions first.")
            return
        sens = sens[:8]
        self._sens = sens
        levers = [x.lever for x in sens]
        lows = np.array([x.worst for x in sens])
        highs = np.array([x.best for x in sens])
        charts.tornado(p, levers, lows, highs)
        p.autoRange()
        top = sens[0]
        self.analysis_note.setText(
            f"Bars show changes in ending net worth over {self.horizon.currentText()}, "
            "with one control changed at a time: income/spending by ±20%, or extra "
            "payments/investing by $100/month (down to $0). Uses average inputs without "
            f"random variation. {top.lever} has the largest spread: {fmt_money(top.swing)}.")

    def _render_backtest(self, p) -> None:
        days = self.bt_lookback.currentData()
        bt = backtest_cash(self.store, lookback_days=days,
                           n_paths=max(200, self.paths.value() // 2))
        self._bt = bt
        if bt is None:
            self.analysis_note.setText(
                "Not enough history to refit that far back — import more, or "
                "choose a shorter look-back.")
            return
        x = charts.to_ts(bt.dates)
        p.getAxis("bottom").setTicks(None)
        charts.backtest_plot(p, x, bt.actual, bt.pct)
        charts.lock_view(p, float(x[0]), float(x[-1]),
                         [bt.actual, bt.pct[10], bt.pct[90]], include_y=[0.0])
        col = (theme.MUTED if bt.thin else
               theme.ACCENT if bt.coverage_80 >= 0.75 else
               theme.AMBER if bt.coverage_80 >= 0.5 else theme.RED)
        # The plain version. Coverage against its own target is the one figure
        # that answers "should I believe this?"; the second band, the ending
        # error and the bias term are measurements of that answer and live in
        # the For nerds window instead.
        self.analysis_note.setText(
            f"Forecast refitted at {bt.as_of.strftime('%B %Y')} using earlier "
            f"cash transactions and compared with recorded balances. Actual cash "
            f"was inside the 80% band on {bt.coverage_80 * 100:.0f}% of days. "
            f"Median absolute error: {fmt_money(bt.median_abs_error)}. "
            f"Loan and investment settings remain current; this is a retrospective check. "
            f"{bt.note}")
        self.analysis_note.setStyleSheet(f"color: {col};")

    def _render_risk(self, res, ef_target: float, months: int) -> None:
        """Say plainly whether the paths run dry — and mean the same thing by it.

        This line used to be driven by one number: the share of simulated paths
        that touch zero at any point. That is a real measurement and a terrible
        headline. Ten dollars in the account, a fifteen-dollar bill on Thursday
        and a new job paying six figures a month scores 100% and printed *high
        risk of running out of cash*, which is not merely unhelpful — it teaches
        people that the warning means nothing, so the one that matters gets
        ignored too.

        ``forecast.assess_shortfall`` now decides, from how deep the dips go,
        how long they last and whether they come back. The percentages have not
        gone anywhere; they are one click away, under *For nerds*, where they
        are a measurement rather than a verdict.
        """
        sf = res.shortfall
        self.forecast_warning.setText("\n".join(res.warnings))
        self.forecast_warning.setVisible(bool(res.warnings))
        if sf is None:
            self.risk.setText("")
            self.risk_detail.clear()
            self.risk_panel.hide()
            self.risk_details.hide()
            self.cause_btn.setVisible(False)
            return
        # Amber, like Home's verdict: a forecast is "look closer", never coral.
        col = {"high": theme.AMBER, "crunch": theme.AMBER, "watch": theme.AMBER,
               "brief": theme.BLUE, "thin": theme.AMBER}.get(sf.level,
                                                             theme.ACCENT)

        text = sf.detail
        if res.runway_months is not None:
            text += (f"  Today's balance alone covers about "
                     f"{res.runway_months:.1f} months of essentials.")
        # Dating the breach only earns its alarm when the breach is material —
        # run_forecast applies the same threshold before setting the day. The
        # crunch wording already carries its own date, so it is left out here.
        if sf.level in ("high", "watch") and res.first_breach_day is not None:
            when = res.dates[min(res.first_breach_day, len(res.dates) - 1)]
            text += (f"  The earliest it shows up in 1 run in 10 is "
                     f"{when.strftime('%B %Y')}.")
        headline = sf.headline
        if res.history_weeks < 4 and sf.level in ("ok", "thin", "brief"):
            # With fewer than four weeks of statements everyday spending is left out,
            # so "cash holds up" only means the balances and scheduled bills do. Good
            # news waits for evidence; bad news (a real shortfall) never does.
            headline = "Too early to say where your cash is headed."
            col = theme.TEXT
            text = ("Doug has your balances but fewer than four weeks of statements, "
                    "so everyday spending is left out. The chart shows balances plus "
                    "scheduled bills and paychecks: a floor, not a forecast. Import "
                    "at least four complete weeks to see a range.")
        else:
            if res.history_weeks < 4:
                headline += "\nDay-to-day spending is omitted."
            fresh = data_freshness(self.store.transactions(),
                                   demo=bool(getattr(self.main, "demo_key", None)))
            if fresh.stale:
                headline += (f"\nBased on statements through "
                             f"{fresh.last:%b} {fresh.last.day}, {fresh.age_days} days ago.")
        self.risk.setText(headline)
        self.risk.setStyleSheet(f"color: {col};")
        self.risk_detail.setText(text)
        self.empty_note.hide()
        self.risk_panel.setVisible(self.view_pick.currentData() in ("cash", "both"))
        # Shown whenever the cash line actually goes under, which is the only
        # time the question arises. It was briefly restricted to short dips, on
        # the theory that a structural shortfall has no nameable cause — which
        # confused an argument about *framing* with one about usefulness, and
        # hid the button on almost every real ledger. Knowing what is scheduled
        # to leave next month is worth having either way; what changes with the
        # verdict is whether rescheduling one of them is offered as a fix, and
        # that is the dialog's job to get right.
        self.cause_btn.setVisible(
            sf.low_date is not None
            and sf.level in ("brief", "crunch", "watch", "high"))

    def _show_causes(self) -> None:
        res = getattr(self, "_res", None)
        sf = res.shortfall if res is not None else None
        if sf is None or sf.low_date is None:
            return
        source = self.store
        try:
            causes, next_in = crunch_causes(source, sf.low_date,
                                            sf.median_days, self._scenario())
            dlg = CrunchDialog(source, sf, causes, next_in, self)
            dlg.exec()
        except Exception as error:
            self.page_error.setText("The scheduled payments could not be loaded. "
                                    "Select Refresh forecast to try again.")
            self.page_error.setToolTip(str(error))
            self.page_error.show()
            self.retry_btn.show()
            return
        if dlg.applied and self.store is source:
            self.main.status("Payment moved — added as a pair of what-if "
                             "events, which you can undo in the list.")
            self._after_event_write()

    # ---------------- for nerds ----------------
    def _nerd_panel(self) -> tuple[str, str]:
        """Everything the page deliberately does not say out loud."""
        if self._analysis_dirty and self._res is not None:
            self._run_analysis()
        res = getattr(self, "_res", None)
        if res is None:
            return ("Forecast · the numbers underneath",
                    "<p>Run a forecast first — there is nothing to take apart "
                    "yet.</p>")
        H = self.horizon.currentData()
        n = self.paths.value()
        months = getattr(self, "_ef_months", self.ef_months.value())
        ef_target = getattr(self, "_ef_target", 0.0)
        sf = res.shortfall
        b = res.cash_breach or {}
        m = theme.MUTED
        rows = [
            f"<h3>How the fan is built</h3>",
            f"<p>{res.history_weeks} complete historical weeks are available "
            f"under the selected history setting. "
            + (f"They span {fmt_day(res.history_first)} to "
               f"{fmt_day(res.history_last)}. " if res.history_first else "")
            + ("Paydays and bills were re-estimated from this period.</p>"
               if res.recurring_refitted else
               "Paydays and bills use your saved recurring patterns.</p>"),
            f"<p>{n:,} Monte Carlo paths over {H:,} days ({H / 365:.1f} years), "
            f"on a daily grid. Each path is one simulated outcome under the "
            f"selected assumptions. The shaded bands are percentiles "
            f"taken <i>across</i> paths at each day, so the P90 line is not any "
            f"single path — it is the level 10% of them exceed on that day.</p>",
            f"<p class='m'>Four layers stack into every path: detected "
            f"recurring flows replayed at their median amount; loans amortised "
            f"monthly at APR/12 on their own payment day; investments as "
            f"geometric Brownian motion with Student-<i>t</i> shocks "
            f"(ν = {T_DOF:.0f}, with heavier tails than a normal "
            f"distribution); and remaining spending bootstrapped as "
            f"whole historical weeks, which preserves the correlation between "
            f"categories inside a week.</p>",
            f"<p class='m'>One drift offset is drawn per path and held for its "
            f"whole length to represent uncertainty in mean weekly net flow. "
            f"Its standard error is estimated as s/√n; the resulting "
            f"cumulative uncertainty grows linearly in time. Independent "
            f"week-to-week noise grows approximately as √t instead. "
            f"Under recency weighting, n is "
            f"Kish's effective sample size 1/Σw², not the raw week count.</p>",
        ]
        if sf is not None:
            rows += [
                "<h3>Shortfall — the numbers behind the sentence</h3>",
                "<table>",
                f"<tr><th>Touches zero at any point</th>"
                f"<td>{sf.prob_any * 100:.1f}% of paths</td></tr>",
                f"<tr><th>Dips more than {fmt_money(sf.material)} under</th>"
                f"<td>{sf.prob_material * 100:.1f}%</td></tr>",
                f"<tr><th>Under water ≥ {SUSTAINED_DAYS} days</th>"
                f"<td>{sf.prob_sustained * 100:.1f}%</td></tr>",
                f"<tr><th>Still under on the final day</th>"
                f"<td>{sf.prob_unrecovered * 100:.1f}%</td></tr>",
                f"<tr><th>Typical worst dip (of paths that dip)</th>"
                f"<td>{fmt_money(sf.median_depth)} for "
                f"{sf.median_days:.0f} days</td></tr>",
                f"<tr><th>Ending cash</th>"
                f"<td>P10 {fmt_money(sf.end_cash_p10)} · "
                f"P50 {fmt_money(sf.end_cash_p50)}</td></tr>",
                f"<tr><th>Verdict</th><td><code>{sf.level}</code></td></tr>",
                "</table>",
                f"<p class='m'>The materiality threshold is "
                f"{fmt_money(sf.material)}: the greater of "
                f"{fmt_money(MATERIAL_MIN)} and a tenth of a month of your own "
                f"essential spending. This is a reporting threshold, not "
                f"a bank overdraft limit. Even a smaller dip may matter. "
                f"Depth, duration, and recovery are measured separately "
                f"to distinguish brief low balances from sustained shortfalls.</p>",
            ]
        if b:
            floors = [("$0", "zero"), (fmt_money(STARTER_BUFFER), "starter"),
                      (f"{months}-month fund ({fmt_money(ef_target)})",
                       "emergency")]
            rows.append("<h3>Floor breach probabilities</h3><table>")
            for label, key in floors:
                if key in b:
                    rows.append(f"<tr><th>Below {label} at any point</th>"
                                f"<td>{b[key] * 100:.1f}%</td></tr>")
            rows.append("</table>")
            if res.min_cash_pct:
                pcts = " · ".join(
                    f"P{p} {fmt_money(res.min_cash_pct[p], signed=True)}"
                    for p in sorted(res.min_cash_pct))
                rows.append(f"<p class='m'>Distribution of each path's "
                            f"<i>lowest</i> point: {pcts}. This is a running "
                            f"minimum, not an end-state — it is the question "
                            f'"how bad does it get on the way" rather than '
                            f'"where do I finish".</p>')
        bt = getattr(self, "_bt", None)
        if bt is not None:
            rows += [
                "<h3>Backtest results</h3>",
                f"<p>Refit as of {bt.as_of:%b %Y} using only data that existed "
                f"then ({bt.train_days // 7} weeks of it), then scored against "
                f"what happened over {bt.n_days} days.</p><table>"
                f"<tr><th>Inside the 80% band</th>"
                f"<td>{bt.coverage_80 * 100:.1f}% <span class='m'>"
                f"(nominal 80%)</span></td></tr>"
                f"<tr><th>Inside the 50% band</th>"
                f"<td>{bt.coverage_50 * 100:.1f}% <span class='m'>"
                f"(nominal 50%)</span></td></tr>"
                f"<tr><th>Median absolute error</th>"
                f"<td>{fmt_money(bt.median_abs_error)}</td></tr>"
                f"<tr><th>Bias, mean(actual − P50)</th>"
                f"<td>{fmt_money(bt.bias, signed=True)} <span class='m'>"
                f"(&gt;0 = the model was too pessimistic)</span></td></tr>"
                "</table>",
                f"<p class='m'>Three lookahead leaks are closed explicitly: "
                f"recurring patterns are re-detected from the truncated ledger, "
                f"the behavioural bootstrap is truncated the same way, and "
                f"starting cash is read from the reconstructed history at that "
                f"date. One is not: loan and investment balances are "
                f"current-day, which is why only cash is scored.</p>",
            ]
        sens = getattr(self, "_sens", None)
        if sens:
            rows.append("<h3>Sensitivity</h3><table>"
                        "<tr><th>Lever</th><th>Worst</th><th>Best</th>"
                        "<th>Swing</th></tr>")
            for s in sens[:8]:
                rows.append(
                    f"<tr><td>{escape(s.lever)}</td>"
                    f"<td>{fmt_money(s.worst, signed=True)}</td>"
                    f"<td>{fmt_money(s.best, signed=True)}</td>"
                    f"<td>{fmt_money(s.swing)}</td></tr>")
            rows.append("</table><p class='m'>Each lever is moved ±20% (or "
                        "±$100/month for the dollar levers, clipped at zero) with everything "
                        "else held fixed, on the deterministic path only — "
                        "sensitivity is a question about the mean response, and "
                        "Monte Carlo noise would swamp the small differences "
                        "without adding anything.</p>")
        rows.append(f"<p style='color:{m}'>Every figure here comes from your "
                    f"own ledger. None of it is financial advice.</p>")
        return "Forecast · the numbers underneath", "".join(rows)

    # ---------------------------------------------------------------
    def run(self) -> None:
        if self._running:
            return
        if self._refresh_needed:
            self.refresh()
            return
        self._running = True
        self._update_timer.stop()
        self.page_error.hide()
        self.retry_btn.hide()
        self.run_btn.setEnabled(False)
        self.run_btn.setText("Calculating…")
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.run_btn.repaint()
        try:
            self._calculate()
        except Exception as error:
            self._forecast_failed(error, "The forecast could not be calculated. "
                                  "Select Refresh forecast to try again. Your controls and events are kept.")
        finally:
            self._running = False
            self.run_btn.setText("Refresh forecast")
            self.run_btn.setEnabled(self._refresh_needed or self._res is not None)
            self.unsetCursor()

    def _draw_projection(self) -> None:
        """Draw the selected measures together without changing the simulation."""
        res, base = self._res, self._base
        if res is None:
            return
        key = self.view_pick.currentData()
        show_cash, show_nw = key in ("cash", "both"), key in ("networth", "both")
        free = self.cash_view.currentData() == "free"
        noun = "Free cash" if free else "Bank cash"
        cash_pct = res.free_cash_pct if free else res.cash_pct
        base_cash = (base.free_cash_pct if free else base.cash_pct) if base else None
        title = f"{noun} + net worth" if show_cash and show_nw else noun if show_cash else "Net worth"
        self.cash_card.title.setText(f"{title} — projection".upper())
        self.plot_cash.setAccessibleName(
            "Cash and net worth projection chart" if show_cash and show_nw else
            "Cash projection chart" if show_cash else "Net worth projection chart")
        self.cash_legend_title.setText(noun)
        self.cash_view_note.setText(
            "After goal earmarks and planned reserves. Cash warnings use bank cash."
            if free else "Bank cash includes money set aside for goals.")
        self.cash_view_note.setToolTip(self.cash_view.toolTip() +
            " Bank cash history and buffer lines are hidden in the free-cash view.")
        self.cash_legend.actions["zero"].setText(
            "$0 · no free cash left" if free else "$0 · debt below this")
        self.cash_legend.actions["history"].setEnabled(not free)
        for name in ("starter", "emergency", "goals"):
            self.cash_legend.actions[name].setEnabled(not free)
        # On shared axes there is only one zero line, owned by the cash
        # layers. Do not offer a second control that has no item to toggle.
        self.nw_legend.actions["zero"].setEnabled(not show_cash)
        self.nw_legend.actions["zero"].setToolTip(
            "Use the cash Layers menu for the shared zero line." if show_cash else "")

        hist = networth_history(self.store)
        tail = hist.tail(min(len(hist), history_window(len(res.dates) - 1)))
        hx, fx = charts.to_ts(tail.index), charts.to_ts(res.dates)
        goals = self.store.goals()
        p = self.plot_cash
        p.clear()
        self.cash_series.reset()
        self.nw_series.reset()
        values, include = [], [0.0]
        if show_cash:
            reg = self.cash_series
            reg.add("zero", charts.zero_floor(p, "$0 · no free cash left" if free
                                              else "$0 · debt below this"))
            if not free:
                if not self._simple:
                    reg.add("starter", charts.soft_floor(
                        p, STARTER_BUFFER, f"{charts._short(STARTER_BUFFER)} · starter buffer",
                        theme.AMBER, slot="starter"))
                if self._ef_target > STARTER_BUFFER * 1.05:
                    reg.add("emergency", charts.soft_floor(
                        p, self._ef_target,
                        f"{charts._short(self._ef_target)} · {self._ef_months}-month cash buffer",
                        theme.ACCENT, slot="emergency"))
                if len(tail) > 1:
                    reg.add("history", charts.line(p, hx, tail["cash"].values, theme.BLUE, 1.2))
                    values.append(tail["cash"].values)
                for g in goals:
                    if g.kind == "cash":
                        reg.add("goals", charts.target_line(p, g.amount, f"Goal: {g.name} (cash)"))
                        include.append(g.amount)
                include.extend([STARTER_BUFFER, self._ef_target or None])
            outer, inner, med = charts.fan_parts(p, fx, cash_pct, theme.BLUE,
                                                f"{noun} · middle outcome")
            reg.add("band80", outer)
            reg.add("band50", inner)
            reg.add("median", med)
            if base_cash is not None:
                reg.add("baseline", charts.line(p, fx, base_cash[50], theme.BLUE, 1.2,
                                                f"{noun} · without plan / unchanged controls",
                                                dashed=True))
                values.append(base_cash[50])
            values.extend([cash_pct[10], cash_pct[90]])
            reg.apply()
        if show_nw:
            reg = self.nw_series
            if not show_cash:
                reg.add("zero", charts.zero_floor(p, "$0 · net worth"))
            if len(tail) > 1:
                reg.add("history", charts.line(p, hx, tail["networth"].values, theme.ACCENT, 1.2))
                values.append(tail["networth"].values)
            outer, inner, med = charts.fan_parts(p, fx, res.networth_pct, theme.ACCENT,
                                                "Net worth · middle outcome")
            reg.add("band80", outer)
            reg.add("band50", inner)
            reg.add("median", med)
            reg.add("det", charts.line(p, fx, res.networth_det, theme.AMBER, 1.4, dashed=True))
            if base is not None:
                reg.add("baseline", charts.line(p, fx, base.networth_pct[50], theme.ACCENT, 1.2,
                                                "Net worth · without plan / unchanged controls",
                                                dashed=True))
                values.append(base.networth_pct[50])
            for g in goals:
                if g.kind == "networth":
                    reg.add("goals", charts.target_line(p, g.amount, f"Goal: {g.name} (net worth)"))
                    include.append(g.amount)
            values.extend([res.networth_pct[10], res.networth_pct[90], res.networth_det])
            reg.apply()
        if show_cash or show_nw:
            charts.today_line(p)
            # One home range includes both metrics. Redraws preserve the user's
            # current pan and zoom; Reset axes applies the updated home range.
            charts.lock_view(p, float(hx[0]) if len(hx) else float(fx[0]),
                             float(fx[-1]), values, include_y=include)

        following = res.goal_plan_budget is not None
        for label, series, original, name, visible in (
                (self.cash_compare, cash_pct[50], base_cash[50] if base_cash else None,
                 noun.lower(), show_cash),
                (self.nw_compare, res.networth_pct[50], base.networth_pct[50] if base else None,
                 "net worth", show_nw)):
            label.setVisible(visible and original is not None)
            if original is not None:
                ending, unchanged = float(series[-1]), float(original[-1])
                comparison = "without the plan" if following else "unchanged"
                label.setText(
                    f"Middle {name} outcome on {fmt_day(res.dates[-1])}: "
                    f"{fmt_money(ending)} with these controls · {fmt_money(unchanged)} {comparison} "
                    f"({fmt_money(ending - unchanged, signed=True)}).\n"
                    f"Solid: these controls · dashed: unchanged controls"
                    + (" without the plan." if following else "."))
                label.setToolTip(
                    "The middle outcome is the median: half the simulated balances are "
                    "above it on that date and half below. It is not a guaranteed balance. "
                    + self.overlay.toolTip())
        self._layout_timer.start(0)

    def _calculate(self) -> None:
        self._update_timer.stop()
        self.data_quality.refresh(self.store)
        if not self.store.accounts():
            self._clear_results()
            self._set_empty(True)
            return
        self._set_empty(False)
        store = self.store
        scenario = self._scenario()
        H = self.horizon.currentData()
        months = self.ef_months.value()
        ess = essential_monthly(store)
        ef_target = max(0.0, ess * months)
        res = run_forecast(store, H, self.paths.value(), scenario,
                           emergency_target=ef_target, essential_monthly=ess)
        self._bt = None
        self._sens = []
        self._analysis_dirty = True
        self._res, self._ef_target, self._ef_months = res, ef_target, months
        self.income_slider.set_monthly_reference(res.monthly_income_reference)
        for row in self._cat_sliders:
            row.set_monthly_reference(res.monthly_spending_reference.get(row.name, 0.0))
        if res.history_first:
            self.history_note.setText(
                f"{res.history_weeks} complete weeks · "
                f"{fmt_day(res.history_first)} to {fmt_day(res.history_last)}. "
                + ("Paydays and bills use this period too." if res.recurring_refitted
                   else "Paydays and bills use your saved recurring patterns."))
            if res.history_weeks < 4:
                self.history_note.setText(self.history_note.text() +
                    " Under 4 weeks: variable spending is omitted. "
                    "Choose a longer period or import recent data.")
        else:
            self.history_note.setText("No complete spending weeks in this period. "
                                      "Choose a longer period or import recent data.")
        if res.spending_plan_applied:
            self.history_note.setText(self.history_note.text() + ' Saved monthly Plan applied. '
                'Existing bill dates and spending variation are scaled to the budget. '
                'Amounts without usable history are spread evenly across days.')
        base: ForecastResult | None = None
        if self.overlay.isChecked() and (scenario.follow_goal_plan or not scenario.is_baseline() or not scenario.snowball):
            base = run_forecast(store, H, self.paths.value(),
                                Scenario(),
                                emergency_target=ef_target,
                                essential_monthly=ess)

        self._base = base
        self._draw_projection()

        self._render_risk(res, ef_target, months)
        self._maybe_run_analysis()

        # ----- goal ETAs -----
        fmt = "%b %Y"
        for out in res.goal_outcomes:
            lbl = self._goal_labels.get(out.goal.id)
            if lbl is None:
                continue
            if out.already_met:
                lbl.setText("Already met")
                lbl.setStyleSheet(f"color: {theme.ACCENT};")
                continue
            lbl.setStyleSheet("")
            if out.det is None and out.early is None:
                lbl.setText("Not reached within this forecast. Try a longer forecast length.")
            else:
                mid = out.det.strftime(fmt) if out.det else "beyond this forecast"
                early = out.early.strftime(fmt) if out.early else "—"
                late = out.late.strftime(fmt) if out.late else "beyond this forecast"
                lbl.setText(f"Average inputs: {mid}\nBand crossings: {early} – {late}")

        # ----- summary -----
        bits = [f"Paydays and bills ≈ {fmt_money(res.monthly_recurring_net, signed=True)}/mo "
                "before loan payments and investing"]
        if res.goal_plan_budget is not None:
            bits.insert(0, f"Goals funding plan · {fmt_money(res.goal_plan_budget)}/mo extra · "
                        "loan repayments applied to this graph")
        if res.spending_plan_applied:
            bits.insert(0, 'Saved monthly Plan income, spending & investing applied')
        for lo in res.loans:
            when = lo.payoff.strftime(fmt) if lo.payoff else "beyond horizon"
            bits.append(f"{lo.account.name}: {fmt_money(lo.payment)}/mo → paid off "
                        f"{when} (interest {fmt_money(lo.interest_paid)})")
        self.summary.setText("   ·   ".join(bits))
        self._update_onboarding(res)
