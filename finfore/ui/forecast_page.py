"""Forecast page — the signature view.

History (muted) hands off to the Monte Carlo fan (teal) at a dashed "today"
line; the deterministic path runs through it in amber. Scenario knobs reshape
the fan; goals render as target lines with arrival estimates.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit,
                             QDoubleSpinBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QSizePolicy, QSlider, QSplitter,
                             QVBoxLayout, QWidget)

from ..forecast import (STARTER_BUFFER, ForecastResult, backtest_cash,
                        run_forecast, sensitivity)
from ..insights import emergency_fund_target, essential_monthly
from ..history import category_spending, networth_history
from ..models import Goal, OneOffEvent, Scenario
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .widgets import (Card, CollapsibleCard, IntSpinBox, LegendToggles,
                      SeriesRegistry, fan_entries, hint, vsplit)
from .account_dialog import _money_spin

_HORIZONS = [("6 months", 183), ("1 year", 365), ("2 years", 730),
             ("3 years", 1095), ("5 years", 1825), ("10 years", 3650)]

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


class SliderRow(QWidget):
    def __init__(self, label: str, lo: int = 0, hi: int = 200, start: int = 100):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self.setMinimumHeight(26)
        self.name = label
        lbl = QLabel(label)
        lbl.setMinimumWidth(88)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(start)
        self.pct = QLabel("100%")
        self.pct.setMinimumWidth(40)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        self.slider.valueChanged.connect(
            lambda v: self.pct.setText(f"{v}%"))
        self._start = start
        lay.addWidget(lbl)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.pct)

    def value(self) -> float:
        return self.slider.value() / 100.0

    def reset(self) -> None:
        self.slider.setValue(self._start)


def _clear_layout(lay) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
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
        self._analysis = "tornado"
        self._goal_labels: dict[int, QLabel] = {}

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

        # ---------- left: controls ----------
        controls = QWidget()
        cv = QVBoxLayout(controls)
        cv.setContentsMargins(0, 0, 6, 0)
        cv.setSpacing(12)

        proj = Card("Projection")
        form = QFormLayout()
        form.setSpacing(8)
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
            "Emergency-fund floor, drawn on the cash chart. Sized from your "
            "own essential spending — bills, groceries, insurance, medical, "
            "fuel and loan payments — not a generic figure.")
        form.addRow("Horizon", self.horizon)
        form.addRow("Monte Carlo paths", self.paths)
        form.addRow("Emergency fund", self.ef_months)
        proj.body.addLayout(form)
        self.overlay = QCheckBox("Overlay baseline comparison")
        self.overlay.setChecked(True)
        proj.body.addWidget(self.overlay)
        self.run_btn = QPushButton("Run forecast")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run)
        self.ef_months.valueChanged.connect(
            lambda _v: self.run() if self.run_btn.isEnabled() else None)
        proj.body.addWidget(self.run_btn)
        cv.addWidget(proj)

        spend = CollapsibleCard("Scenario · income && spending", expanded=True)
        self.income_slider = SliderRow("Income", 50, 150, 100)
        spend.body.addWidget(self.income_slider)
        self.cat_box = QVBoxLayout()
        self.cat_box.setSpacing(6)
        spend.body.addLayout(self.cat_box)
        spend.body.addWidget(hint("Sliders scale both recurring bills and "
                                  "variable spending in that category."))
        cv.addWidget(spend)

        debt = CollapsibleCard("Scenario · debt && investing", expanded=False)
        dform = QFormLayout()
        dform.setSpacing(8)
        self.extra_loan = _money_spin(0, 1e6)
        self.extra_invest = _money_spin(0, 1e6)
        dform.addRow("Extra to loans /mo", self.extra_loan)
        dform.addRow("Extra investing /mo", self.extra_invest)
        debt.body.addLayout(dform)
        self.snowball = QCheckBox("Roll freed payments into the next loan")
        self.snowball.setChecked(True)
        debt.body.addWidget(self.snowball)
        reset = QPushButton("Reset scenario")
        reset.setObjectName("small")
        reset.clicked.connect(self._reset_scenario)
        debt.body.addWidget(reset)
        cv.addWidget(debt)

        ev = CollapsibleCard("What-if events", expanded=False)
        self.event_box = QVBoxLayout()
        self.event_box.setSpacing(3)
        ev.body.addLayout(self.event_box)
        erow = QHBoxLayout()
        self.e_name = QLineEdit()
        self.e_name.setPlaceholderText("New car, medical bill, bonus…")
        self.e_amount = _money_spin(-1_000_000, 1_000_000)
        self.e_when = QDateEdit()
        self.e_when.setCalendarPopup(True)
        self.e_when.setDate(QDate.currentDate().addMonths(6))
        self.e_when.setDisplayFormat("MMM yyyy")
        add_ev = QPushButton("Add")
        add_ev.setObjectName("small")
        add_ev.clicked.connect(self._add_event)
        erow.addWidget(self.e_name, 1)
        erow.addWidget(self.e_amount)
        erow.addWidget(self.e_when)
        erow.addWidget(add_ev)
        ev.body.addLayout(erow)
        ev.body.addWidget(hint(
            "Negative for a cost, positive for a windfall. Smooth trends can't "
            "answer \"can I absorb this?\" — a dated impulse can."))
        cv.addWidget(ev)

        goals = CollapsibleCard("Goals", expanded=False)
        self.goal_box = QVBoxLayout()
        self.goal_box.setSpacing(8)
        goals.body.addLayout(self.goal_box)
        add_row = QHBoxLayout()
        self.g_name = QLineEdit()
        self.g_name.setPlaceholderText("Goal name")
        self.g_name.setMinimumWidth(92)
        self.g_amount = _money_spin(0, 1e9)
        self.g_kind = QComboBox()
        self.g_kind.addItem("Net cash", "cash")
        self.g_kind.addItem("Net worth", "networth")
        g_add = QPushButton("Add")
        g_add.setObjectName("small")
        g_add.clicked.connect(self._add_goal)
        add_row.addWidget(self.g_name, 2)
        add_row.addWidget(self.g_amount, 1)
        add_row.addWidget(self.g_kind)
        add_row.addWidget(g_add)
        goals.body.addLayout(add_row)
        cv.addWidget(goals)
        cv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(240)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(controls)
        split.addWidget(scroll)

        # ---------- right: charts ----------
        chart_col = QWidget()
        # Ignored horizontally: the charts take whatever room is left rather
        # than reporting a width the splitter then has to honour.
        chart_col.setSizePolicy(QSizePolicy.Policy.Ignored,
                                QSizePolicy.Policy.Preferred)
        chart_col.setMinimumWidth(320)
        right = QVBoxLayout(chart_col)
        right.setContentsMargins(12, 0, 0, 0)
        right.setSpacing(12)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Show"))
        self.view_pick = QComboBox()
        for lbl, key in (("Cash + net worth", "both"), ("Cash only", "cash"),
                         ("Net worth only", "networth"),
                         ("Analysis only", "analysis")):
            self.view_pick.addItem(lbl, key)
        self.view_pick.setToolTip("Hide charts you aren't using to give the "
                                  "rest more room")
        self.view_pick.currentIndexChanged.connect(self._apply_view)
        picker.addWidget(self.view_pick)
        self.show_analysis = QCheckBox("Analysis panel")
        self.show_analysis.setChecked(True)
        self.show_analysis.stateChanged.connect(lambda _s: self._apply_view())
        picker.addWidget(self.show_analysis)
        picker.addStretch(1)
        right.addLayout(picker)
        # Cash leads. It is the account that actually runs out, and the floors
        # below (zero, starter buffer, emergency fund) are cash concepts — net
        # worth can look healthy while the chequing account is empty.
        cash_card = Card("Net cash position — projection")
        self.plot_cash = charts.new_plot(legend=True)
        self.plot_cash.setMinimumHeight(180)
        cash_card.body.addWidget(self.plot_cash)
        self.cash_series = SeriesRegistry()
        self.cash_legend = LegendToggles(
            fan_entries(theme.BLUE, extras=[
                ("history", "history", theme.MUTED, "line"),
                ("baseline", "baseline scenario", "#C7D0DB", "dash"),
                ("zero", "$0 · debt below this", theme.RED, "line"),
                ("starter", "starter buffer", theme.AMBER, "dash"),
                ("emergency", "emergency fund", theme.ACCENT, "dash"),
                ("goals", "goals", theme.AMBER, "dash"),
            ]),
            on_toggle=lambda k, on: self.cash_series.set_visible(k, on))
        cash_card.body.addWidget(self.cash_legend)
        self.risk = QLabel("")
        self.risk.setWordWrap(True)
        self.risk.setObjectName("statSub")
        cash_card.body.addWidget(self.risk)
        self.cash_card = cash_card

        nw_card = Card("Net worth — projection")
        self.plot_nw = charts.new_plot()
        self.plot_nw.setMinimumHeight(110)
        nw_card.body.addWidget(self.plot_nw)
        self.nw_series = SeriesRegistry()
        self.nw_legend = LegendToggles(
            fan_entries(theme.ACCENT, extras=[
                ("history", "history", theme.MUTED, "line"),
                ("det", "deterministic (no volatility)", theme.AMBER, "dash"),
                ("baseline", "baseline scenario", "#C7D0DB", "dash"),
                ("zero", "$0 · net worth", theme.RED, "line"),
                ("goals", "goals", theme.AMBER, "dash"),
            ]),
            on_toggle=lambda k, on: self.nw_series.set_visible(k, on))
        nw_card.body.addWidget(self.nw_legend)
        self.nw_card = nw_card

        # ----- analysis: tornado + backtest -----
        an = Card("Which lever matters · and can the model be trusted?")
        tabs = QHBoxLayout()
        self.btn_tornado = QPushButton("Sensitivity")
        self.btn_tornado.setObjectName("small")
        self.btn_tornado.setCheckable(True)
        self.btn_tornado.setChecked(True)
        self.btn_backtest = QPushButton("Backtest")
        self.btn_backtest.setObjectName("small")
        self.btn_backtest.setCheckable(True)
        self.btn_tornado.clicked.connect(lambda: self._set_analysis("tornado"))
        self.btn_backtest.clicked.connect(lambda: self._set_analysis("backtest"))
        tabs.addWidget(self.btn_tornado)
        tabs.addWidget(self.btn_backtest)
        tabs.addStretch(1)
        self.bt_lookback = QComboBox()
        for lbl, d in (("6 months back", 180), ("9 months back", 270),
                       ("1 year back", 365), ("2 years back", 730)):
            self.bt_lookback.addItem(lbl, d)
        self.bt_lookback.setVisible(False)
        self.bt_lookback.currentIndexChanged.connect(lambda _i: self._run_analysis())
        tabs.addWidget(self.bt_lookback)
        an.body.addLayout(tabs)
        self.plot_analysis = charts.new_plot(date_axis=False, money_left=False,
                                             money_bottom=True)
        self.plot_analysis.setMinimumHeight(100)
        an.body.addWidget(self.plot_analysis)
        self.analysis_note = hint("")
        an.body.addWidget(self.analysis_note)
        self.analysis_card = an

        # Dividers between the three charts, so any one of them can be given
        # the column at the cost of the others without hiding them outright.
        self.chart_split = vsplit(cash_card, nw_card, an,
                                  sizes=[420, 280, 260])
        for c in (cash_card, nw_card, an):
            c.setMinimumHeight(120)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        right.addWidget(self.chart_split, 1)

        self.summary = hint("")
        right.addWidget(self.summary)
        split.addWidget(chart_col)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([356, 900])

    # ---------------------------------------------------------------
    def refresh(self) -> None:
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

        self._rebuild_goals()
        self._rebuild_events()
        self._apply_view()

        has_accounts = bool(self.store.accounts())
        self.run_btn.setEnabled(has_accounts)
        if has_accounts:
            self.run()
        else:
            self.summary.setText("Add accounts (and import a CSV) to project "
                                 "your net worth — or load the demo dataset "
                                 "from the File menu.")

    def _rebuild_goals(self) -> None:
        _clear_layout(self.goal_box)
        self._goal_labels = {}
        for g in self.store.goals():
            roww = QWidget()
            rl = QHBoxLayout(roww)
            rl.setContentsMargins(0, 0, 0, 0)
            col = QVBoxLayout()
            col.setSpacing(1)
            title = QLabel(f"{g.name} — {fmt_money(g.amount)}"
                           f"  ·  {'cash' if g.kind == 'cash' else 'net worth'}")
            title.setWordWrap(True)
            eta = QLabel("run a forecast for an estimate")
            eta.setObjectName("hint")
            col.addWidget(title)
            col.addWidget(eta)
            rm = QPushButton("✕")
            rm.setObjectName("small")
            rm.setFixedWidth(28)
            rm.clicked.connect(lambda _c, gid=g.id: self._remove_goal(gid))
            rl.addLayout(col, 1)
            rl.addWidget(rm)
            self.goal_box.addWidget(roww)
            self._goal_labels[g.id] = eta
        if not self._goal_labels:
            self.goal_box.addWidget(hint("No goals yet — add one below."))

    def _add_goal(self) -> None:
        name = self.g_name.text().strip()
        if not name or self.g_amount.value() <= 0:
            return
        self.store.add_goal(Goal(name, self.g_amount.value(),
                                 self.g_kind.currentData()))
        self.g_name.clear()
        self.g_amount.setValue(0)
        self._rebuild_goals()
        if self.run_btn.isEnabled():
            self.run()

    def _remove_goal(self, goal_id: int) -> None:
        self.store.delete_goal(goal_id)
        self._rebuild_goals()
        if self.run_btn.isEnabled():
            self.run()

    def _reset_scenario(self) -> None:
        self.income_slider.reset()
        for s in self._cat_sliders:
            s.reset()
        self.extra_loan.setValue(0)
        self.extra_invest.setValue(0)
        self.snowball.setChecked(True)

    def _scenario(self) -> Scenario:
        return Scenario(
            category_multipliers={s.name: s.value() for s in self._cat_sliders},
            income_multiplier=self.income_slider.value(),
            extra_loan_payment=self.extra_loan.value(),
            extra_investing=self.extra_invest.value(),
            snowball=self.snowball.isChecked(),
        )

    # ---------------- what-if events ----------------
    def _rebuild_events(self) -> None:
        _clear_layout(self.event_box)
        evs = self.store.events()
        for e in evs:
            roww = QWidget()
            rl = QHBoxLayout(roww)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            chk = QCheckBox()
            chk.setChecked(e.active)
            chk.setToolTip("Include this event in the projection")
            chk.stateChanged.connect(
                lambda st, eid=e.id: self._toggle_event(eid, bool(st)))
            col = theme.RED if e.is_expense else theme.ACCENT
            lbl = QLabel(f"{e.label} · {fmt_money(e.amount, signed=True)} · "
                         f"{e.when.strftime('%b %Y')}")
            lbl.setStyleSheet(f"color: {col if e.active else theme.MUTED};")
            lbl.setWordWrap(True)
            rm = QPushButton("✕")
            rm.setObjectName("small")
            rm.setFixedWidth(26)
            rm.clicked.connect(lambda _c, eid=e.id: self._remove_event(eid))
            rl.addWidget(chk)
            rl.addWidget(lbl, 1)
            rl.addWidget(rm)
            self.event_box.addWidget(roww)
        if not evs:
            self.event_box.addWidget(hint("None yet — add one below."))

    def _add_event(self) -> None:
        name = self.e_name.text().strip()
        amt = self.e_amount.value()
        if not name or abs(amt) < 0.01:
            return
        qd = self.e_when.date()
        self.store.add_event(OneOffEvent(name, date(qd.year(), qd.month(),
                                                    qd.day()), amt))
        self.e_name.clear()
        self.e_amount.setValue(0)
        self._rebuild_events()
        if self.run_btn.isEnabled():
            self.run()

    def _toggle_event(self, event_id: int, on: bool) -> None:
        self.store.update_event(event_id, active=on)
        self._rebuild_events()
        if self.run_btn.isEnabled():
            self.run()

    def _remove_event(self, event_id: int) -> None:
        self.store.delete_event(event_id)
        self._rebuild_events()
        if self.run_btn.isEnabled():
            self.run()

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
        self.cash_card.setVisible(show_cash)
        self.nw_card.setVisible(show_nw)
        self.analysis_card.setVisible(show_analysis)
        # Splitter sizes, not layout stretch or minimum heights. A hidden pane
        # takes no space in a QSplitter, so the remaining ones expand on their
        # own; these weights only decide how the survivors share it. (Minimum
        # heights would change the window's minimum size and resize the whole
        # window on every view change, which is the bug 1.2.0 fixed.)
        weights = [3 if show_nw else 5, 2 if show_cash else 5,
                   5 if key == "analysis" else 2]
        total = max(1, self.chart_split.height())
        live = [w if vis else 0 for w, vis in
                zip(weights, (show_cash, show_nw, show_analysis))]
        s = sum(live) or 1
        self.chart_split.setSizes([int(total * w / s) for w in live])

    # ---------------- analysis panel ----------------
    def _set_analysis(self, which: str) -> None:
        self._analysis = which
        self.btn_tornado.setChecked(which == "tornado")
        self.btn_backtest.setChecked(which == "backtest")
        self.bt_lookback.setVisible(which == "backtest")
        self._run_analysis()

    def _run_analysis(self) -> None:
        which = getattr(self, "_analysis", "tornado")
        p = self.plot_analysis
        p.clear()
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
            self.analysis_note.setText(f"Could not run this analysis: {e}")

    def _render_tornado(self, p) -> None:
        cats = [s.name for s in self._cat_sliders]
        if not cats:
            self.analysis_note.setText("Import some transactions first.")
            return
        H = self.horizon.currentData()
        sens = sensitivity(self.store, self._scenario(), H, cats)
        if not sens:
            self.analysis_note.setText("No levers to compare.")
            return
        sens = sens[:8]
        levers = [x.lever for x in sens]
        lows = np.array([x.worst for x in sens])
        highs = np.array([x.best for x in sens])
        charts.tornado(p, levers, lows, highs)
        p.autoRange()
        top = sens[0]
        yrs = H / 365.0
        self.analysis_note.setText(
            f"Each bar is a ±20% move in one lever, held for {yrs:.1f} years, "
            f"with everything else fixed. {top.lever} dominates: a swing of "
            f"{fmt_money(top.swing)} in ending net worth. Levers below it move "
            f"the outcome less than that, however they feel day to day.")

    def _render_backtest(self, p) -> None:
        days = self.bt_lookback.currentData()
        bt = backtest_cash(self.store, lookback_days=days,
                           n_paths=max(200, self.paths.value() // 2))
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
        self.analysis_note.setText(
            f"Refit as of {bt.as_of.strftime('%b %Y')} using only data that "
            f"existed then ({bt.train_days // 7} weeks of it), then compared "
            f"against what actually happened. Reality stayed inside the 80% "
            f"band {bt.coverage_80 * 100:.0f}% of the time "
            f"({bt.coverage_50 * 100:.0f}% inside the 50% band); typical miss "
            f"{fmt_money(bt.median_abs_error)}, ending "
            f"{fmt_money(abs(bt.end_error))} "
            f"{'above' if bt.end_error > 0 else 'below'} the median line. "
            f"{bt.note}")
        self.analysis_note.setStyleSheet(f"color: {col};")

    def _render_risk(self, res, ef_target: float, months: int) -> None:
        """Say plainly how often the simulated paths run dry.

        A median line that never dips below zero can still sit on top of a
        distribution where a quarter of paths do. Percentiles answer "where do
        I end up"; this answers "how likely am I to run out on the way", which
        is the question a cash chart is really being asked.
        """
        b = res.cash_breach or {}
        if not b:
            self.risk.setText("")
            return
        zero = b.get("zero", 0.0)
        starter = b.get("starter", 0.0)
        emerg = b.get("emergency")
        worst = res.min_cash_pct.get(10)

        if zero >= 0.5:
            col, lead = theme.RED, "High risk of running out of cash."
        elif zero >= 0.15:
            col, lead = theme.AMBER, "Meaningful risk of running out of cash."
        elif starter >= 0.35:
            col, lead = theme.AMBER, "Cash gets thin, though rarely negative."
        else:
            col, lead = theme.ACCENT, "Cash holds up across the simulations."

        parts = [f"{zero * 100:.0f}% of simulated paths go negative at some "
                 f"point", f"{starter * 100:.0f}% dip under "
                 f"{fmt_money(STARTER_BUFFER)}"]
        if emerg is not None:
            parts.append(f"{emerg * 100:.0f}% fall below the {months}-month "
                         f"fund ({fmt_money(ef_target)})")
        if worst is not None:
            parts.append(f"in the worst 10% of paths the low point is "
                         f"{fmt_money(worst, signed=True)}")
        if res.runway_months is not None:
            parts.insert(0, f"cash covers {res.runway_months:.1f} months of "
                            f"essentials at today's balance")
        text = lead + "  " + " · ".join(parts) + "."
        if res.first_breach_day is not None:
            when = res.dates[min(res.first_breach_day, len(res.dates) - 1)]
            text += (f"  1 in 10 paths are underwater by "
                     f"{when.strftime('%b %Y')}.")
        self.risk.setText(text)
        self.risk.setStyleSheet(f"color: {col}; font-size: 12px;")

    # ---------------------------------------------------------------
    def run(self) -> None:
        store = self.store
        scenario = self._scenario()
        H = self.horizon.currentData()
        months = self.ef_months.value()
        ess = essential_monthly(store)
        ef_target = max(0.0, ess * months)
        res = run_forecast(store, H, self.paths.value(), scenario,
                           emergency_target=ef_target, essential_monthly=ess)
        base: ForecastResult | None = None
        if self.overlay.isChecked() and not scenario.is_baseline():
            base = run_forecast(store, H, self.paths.value(), Scenario(),
                                emergency_target=ef_target,
                                essential_monthly=ess)

        hist = networth_history(store)
        tail = hist.tail(min(len(hist), history_window(H)))
        hx = charts.to_ts(tail.index)
        fx = charts.to_ts(res.dates)
        x_min = float(hx[0]) if len(hx) else float(fx[0])
        x_max = float(fx[-1])
        goals = store.goals()

        # ----- cash chart (primary) -----
        p = self.plot_cash
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        reg = self.cash_series
        reg.reset()
        reg.add("zero", charts.zero_floor(p))
        reg.add("starter", charts.soft_floor(
            p, STARTER_BUFFER, f"{charts._short(STARTER_BUFFER)} · starter buffer",
            theme.AMBER, slot="starter"))
        if ef_target and ef_target > STARTER_BUFFER * 1.05:
            reg.add("emergency", charts.soft_floor(
                p, ef_target,
                f"{charts._short(ef_target)} · {months}-month emergency fund",
                theme.ACCENT, slot="emergency"))
        if len(tail) > 1:
            reg.add("history",
                    charts.line(p, hx, tail["cash"].values, theme.MUTED, 1.6,
                                "History"))
        outer, inner, med = charts.fan_parts(p, fx, res.cash_pct, theme.BLUE,
                                             "Projection · P50")
        reg.add("band80", outer)
        reg.add("band50", inner)
        reg.add("median", med)
        if base is not None:
            reg.add("baseline",
                    charts.line(p, fx, base.cash_pct[50], "#C7D0DB", 1.2,
                                "Baseline · P50", dashed=True))
        charts.today_line(p)
        for g in goals:
            if g.kind == "cash":
                reg.add("goals", charts.target_line(p, g.amount, g.name))
        reg.apply()
        charts.lock_view(
            p, x_min, x_max,
            [res.cash_pct[10], res.cash_pct[90],
             tail["cash"].values if len(tail) > 1 else None],
            include_y=[0.0, STARTER_BUFFER, ef_target or None]
            + [g.amount for g in goals if g.kind == "cash"])

        # ----- net worth chart -----
        p = self.plot_nw
        p.clear()
        reg = self.nw_series
        reg.reset()
        reg.add("zero", charts.zero_floor(p, "$0 · net worth"))
        if len(tail) > 1:
            reg.add("history", charts.line(p, hx, tail["networth"].values,
                                           theme.MUTED, 1.4))
        outer, inner, med = charts.fan_parts(p, fx, res.networth_pct, theme.ACCENT)
        reg.add("band80", outer)
        reg.add("band50", inner)
        reg.add("median", med)
        reg.add("det", charts.line(p, fx, res.networth_det, theme.AMBER, 1.4,
                                   dashed=True))
        if base is not None:
            reg.add("baseline", charts.line(p, fx, base.networth_pct[50],
                                            "#C7D0DB", 1.4, dashed=True))
        charts.today_line(p)
        for g in goals:
            if g.kind == "networth":
                reg.add("goals", charts.target_line(p, g.amount, g.name))
        reg.apply()
        charts.lock_view(
            p, x_min, x_max,
            [res.networth_pct[10], res.networth_pct[90], res.networth_det,
             tail["networth"].values if len(tail) > 1 else None],
            include_y=[0.0] + [g.amount for g in goals if g.kind == "networth"])

        # ----- shortfall risk + analysis -----
        self._render_risk(res, ef_target, months)
        self._run_analysis()

        # ----- goal ETAs -----
        fmt = "%b %Y"
        for out in res.goal_outcomes:
            lbl = self._goal_labels.get(out.goal.id)
            if lbl is None:
                continue
            if out.already_met:
                lbl.setText("already met ✓")
                lbl.setStyleSheet(f"color: {theme.ACCENT};")
                continue
            lbl.setStyleSheet("")
            if out.det is None and out.early is None:
                lbl.setText("beyond this horizon")
            else:
                mid = out.det.strftime(fmt) if out.det else "beyond horizon"
                early = out.early.strftime(fmt) if out.early else "—"
                late = out.late.strftime(fmt) if out.late else ">horizon"
                lbl.setText(f"≈ {mid}   ({early} – {late})")

        # ----- summary -----
        bits = [f"Recurring net ≈ {fmt_money(res.monthly_recurring_net, signed=True)}/mo"]
        for lo in res.loans:
            when = lo.payoff.strftime(fmt) if lo.payoff else "beyond horizon"
            bits.append(f"{lo.account.name}: {fmt_money(lo.payment)}/mo → paid off "
                        f"{when} (interest {fmt_money(lo.interest_paid)})")
        for w in res.warnings:
            bits.append(f"⚠ {w}")
        self.summary.setText("   ·   ".join(bits))
