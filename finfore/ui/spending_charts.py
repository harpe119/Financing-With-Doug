"""Monthly cash flow and spending by category, folded under the Spending tab.

They used to sit under the net worth chart on Home. Home now leads with the
answer, and these two charts are about spending, so they live where spending
does. Both start folded; nothing about them changed in the move, including the
category bars that open that category's transactions.
"""
from __future__ import annotations

import pandas as pd
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ..history import category_spending, monthly_cashflow
from ..util import fmt_money
from . import charts, theme
from .widgets import ChartKey, CollapsibleCard

# Tall enough to read once unfolded: the data area plus the axis.
PLOT_MIN = 170 + charts.AXIS_ALLOWANCE


class SpendingCharts(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._cats = None            # every category the chart could show
        self._cat_fit = 0            # …and how many of them fit right now
        self.flow_summary = ""
        self.cat_summary = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        self.flow_card = CollapsibleCard("Monthly cash flow", expanded=False)
        self.plot_flow = charts.new_plot()
        self.plot_flow.setAccessibleName("Monthly cash flow chart")
        self.flow_card.body.addWidget(self.plot_flow, 1)
        self.flow_card.body.addWidget(ChartKey([("Income", theme.ACCENT, "bar"),
                                                ("Spending", theme.RED, "bar")]))

        self.cat_card = CollapsibleCard("Spending by category · last 90 days",
                                        expanded=False)
        self.plot_cat = charts.new_plot(date_axis=False, money_left=False,
                                        money_bottom=True)
        self.plot_cat.setAccessibleName("Spending by category chart")
        self.plot_cat.installEventFilter(self)
        self.cat_card.body.addWidget(self.plot_cat, 1)

        # A plot's own size hint is a 640x480 default, so each is given exactly
        # the height set here. A fixed height rather than an ignored size hint:
        # the cash flow card also holds a wrapping key, and a layout that wraps
        # measures its height from its parts' hints, which for an ignored one
        # is zero, so the opened card came up with the plot squeezed out of it.
        for plot in (self.plot_flow, self.plot_cat):
            plot.setFixedHeight(PLOT_MIN)
            plot.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Fixed)
        lay.addWidget(self.flow_card)
        lay.addWidget(self.cat_card)

    def refresh(self, tx) -> None:
        self._render_flow(tx)
        self._cats = category_spending(tx)
        self._cat_fit = 0
        self._draw_categories()

    # ---- monthly cash flow: zero is the break-even line between the bars ----
    def _render_flow(self, tx) -> None:
        p = self.plot_flow
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        mc = monthly_cashflow(tx)
        self.flow_summary = ""
        if len(mc):
            months = list(mc["month_start"])
            charts.month_bars(p, months, mc["income"].values,
                              mc["spending"].values)
            mx = charts.to_ts(months)
            # Half a bar-group of slack at each end so the outermost bars are
            # not clipped by the axis they sit against.
            pad = 86400.0 * 16
            charts.lock_view(p, float(mx[0]) - pad, float(mx[-1]) + pad,
                             [mc["income"].values, mc["spending"].values],
                             include_y=[0.0],
                             initial_days=charts.HISTORY_INITIAL_DAYS + 30,
                             x_data=mx, zoom_y=False, lock_y=True)
            charts.attach_readout(
                p, mx, [("Income", theme.ACCENT, mc["income"].values),
                        ("Spending", theme.RED, mc["spending"].values)],
                label=lambda ts: pd.Timestamp(ts, unit="s").strftime("%B %Y"),
                tolerance=86400.0 * 16)
            full = mc[mc["month_start"] < pd.Timestamp.today().replace(day=1)]
            full = full.tail(3)
            if len(full):
                self.flow_summary = (
                    f"Over the last {len(full)} full month"
                    f"{'s' if len(full) != 1 else ''}, about "
                    f"{fmt_money(float(full['income'].mean()))} came in and "
                    f"{fmt_money(float(full['spending'].mean()))} went out "
                    f"each month.")
        self.plot_flow.setAccessibleDescription(self.flow_summary)

    # ---- spending by category ----------------------------------------------
    def _open_category(self, name: str) -> None:
        self.main.show_page("Transactions")
        page = self.main.current_page()
        fn = getattr(page, "show_category", None)
        if fn is not None:
            fn(name, 90)

    def _draw_categories(self) -> None:
        """Draw as many category bars as the plot is tall enough to label.

        Nine bars in a short plot leave their labels overprinting each other
        or dropped at random, so a short plot shows its biggest few instead.
        """
        p = self.plot_cat
        p.clear()
        cs = self._cats
        self.cat_summary = ""
        if cs is not None and len(cs):
            fit = self._bars_that_fit(len(cs))
            self._cat_fit = fit
            shown = cs.iloc[:fit]
            names = list(shown.index)
            charts.category_bars(p, names, shown.values)
            charts.on_bar_click(p, names, self._open_category)
            p.autoRange()
            self.cat_summary = (
                f"{names[0]} is the biggest category: "
                f"{fmt_money(float(shown.values[0]))} in the last 90 days.")
        p.setAccessibleDescription(self.cat_summary)

    def _bars_that_fit(self, n: int) -> int:
        """How many category rows the plot can label legibly.

        A 12px label needs about 17px of row, plus room for the axis below.
        Fewer rows with every label drawn beats more rows with half of them
        silently dropped; the rest of this page has the whole breakdown.
        """
        return int(min(n, max(3, (self.plot_cat.height() - 36) // 17)))

    def eventFilter(self, obj, ev) -> bool:
        if (obj is self.plot_cat and ev.type() == QEvent.Type.Resize
                and self._cats is not None and len(self._cats)):
            fit = self._bars_that_fit(len(self._cats))
            if fit != self._cat_fit:
                self._draw_categories()
        return super().eventFilter(obj, ev)
