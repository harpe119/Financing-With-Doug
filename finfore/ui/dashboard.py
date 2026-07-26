"""Dashboard page — where the ledger becomes a picture."""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from ..history import (category_spending, headline_stats, monthly_cashflow,
                       networth_history, reconcile)
from ..insights import emergency_fund_target
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .widgets import (Card, LegendToggles, SeriesRegistry, StatCard,
                      hint, hsplit, vsplit)


class DashboardPage(QWidget):
    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.card_nw = StatCard("Net worth")
        self.card_cash = StatCard("Net cash")
        self.card_inv = StatCard("Investments")
        self.card_debt = StatCard("Debt")
        self.card_save = StatCard("Savings rate · 90d")
        for c in (self.card_nw, self.card_cash, self.card_inv,
                  self.card_debt, self.card_save):
            row.addWidget(c)
        lay.addLayout(row)

        # The empty dashboard is the first thing a new user sees, and "add
        # accounts and import a CSV" assumes they know what either means. The
        # guide is one click away from exactly the moment it is needed.
        empty_row = QHBoxLayout()
        self.empty = hint("No data yet — add accounts and import a bank CSV, "
                          "or load a demo dataset from the File menu.")
        empty_row.addWidget(self.empty, 1)
        self.guide_btn = QPushButton("Show me how")
        self.guide_btn.setObjectName("primary")
        self.guide_btn.setToolTip("Open the Getting started guide (F1)")
        self.guide_btn.clicked.connect(lambda: self.main.show_guide())
        empty_row.addWidget(self.guide_btn)
        lay.addLayout(empty_row)

        # A reconstruction that has been forced somewhere impossible must say
        # so. The alternative is a chart that quietly shows a savings account
        # at −$1,469 for three years, which reads as the program being broken
        # rather than as the export being incomplete.
        self.warn = QLabel("")
        self.warn.setObjectName("ledgerWarning")
        self.warn.setWordWrap(True)
        self.warn.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.warn.setVisible(False)
        lay.addWidget(self.warn)

        hist_card = Card("Net worth history")
        self.plot_hist = charts.new_plot(legend=True)
        self.hist_series = SeriesRegistry()
        self.hist_legend = LegendToggles(
            [("networth", "net worth", theme.ACCENT, "line"),
             ("cash", "net cash", theme.BLUE, "line"),
             ("invest", "investments", theme.PURPLE, "line"),
             ("debt", "debt (shown negative)", theme.RED, "line"),
             ("zero", "$0", theme.RED, "line"),
             ("starter", "starter buffer", theme.AMBER, "dash"),
             ("emergency", "emergency fund", theme.ACCENT, "dash")],
            on_toggle=lambda k, on: self.hist_series.set_visible(k, on))
        self.plot_hist.setMinimumHeight(120)
        hist_card.body.addWidget(self.plot_hist)
        hist_card.body.addWidget(self.hist_legend)

        flow_card = Card("Monthly cash flow")
        self.plot_flow = charts.new_plot(legend=True)
        self.plot_flow.setMinimumHeight(100)
        flow_card.body.addWidget(self.plot_flow)
        cat_card = Card("Spending by category · last 90 days")
        self.plot_cat = charts.new_plot(date_axis=False, money_left=False,
                                        money_bottom=True)
        self.plot_cat.setMinimumHeight(100)
        cat_card.body.addWidget(self.plot_cat)

        # Draggable dividers rather than fixed 3:2 and 1:1 splits. Which chart
        # deserves the room depends on what is being looked at right now, and
        # that is not a question this file can answer once for everybody.
        bottom = hsplit(flow_card, cat_card, sizes=[500, 500])
        for c in (hist_card, flow_card, cat_card):
            c.setMinimumHeight(120)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        lay.addWidget(vsplit(hist_card, bottom, sizes=[430, 300]), 1)

    # ---------------------------------------------------------------
    def _render_warnings(self) -> None:
        """Explain a history the arithmetic has forced somewhere impossible."""
        try:
            issues = reconcile(self.store)
        except Exception:
            issues = []
        if not issues:
            self.warn.setVisible(False)
            return
        rows = ["<b>This history doesn't add up, and the chart is showing it "
                "honestly rather than hiding it.</b>"]
        for i in issues:
            rows.append(
                f"<div style='margin-top:5px'>· <b>{i.account}</b>: the "
                f"imported rows total <b>{fmt_money(i.net_flow, signed=True)}</b> "
                f"since {i.since:%b %Y}, against a current balance of "
                f"{fmt_money(i.entered)}. Those only fit if the account started "
                f"at <b>{fmt_money(i.opening)}</b>.</div>")
            if i.duplicates:
                rows.append(
                    f"<div style='margin-left:12px'>{i.duplicates} row"
                    f"{'s' if i.duplicates != 1 else ''} look like the same "
                    f"transaction posted twice under different wording "
                    f"({fmt_money(i.duplicate_value, signed=True)} between "
                    f"them) — banks often do this around a system change, and "
                    f"the duplicate check can't catch it because the "
                    f"descriptions differ.</div>")
        rows.append(
            "<div style='margin-top:5px'>Usual causes, in order: the export is "
            "missing withdrawals or doesn't reach back far enough; the same "
            "transaction was exported twice; or the balance / as-of date on "
            "the Accounts page needs correcting. Adding a "
            "<b>Snapshot</b> with a known past balance re-anchors the history "
            "from that date.</div>")
        self.warn.setText("".join(rows))
        self.warn.setVisible(True)

    def refresh(self) -> None:
        store = self.store
        st = headline_stats(store)
        self.card_nw.set_money(st["networth"], colored=True)
        self.card_cash.set_money(st["cash"])
        self.card_inv.set_money(st["investments"])
        self.card_debt.set_money(-st["debt"] if st["debt"] else 0.0,
                                 colored=st["debt"] > 0)
        if st["savings_rate"] is not None:
            self.card_save.set_text(
                f"{st['savings_rate'] * 100:.0f}%",
                color=theme.POSITIVE if st["savings_rate"] >= 0 else theme.NEGATIVE,
                sub=f"burn ≈ {fmt_money(st['burn'])}/mo" if st["burn"] else None)
        else:
            self.card_save.set_text("—")

        tx = store.transactions()
        has_data = bool(store.accounts()) or not tx.empty
        self.empty.setVisible(not has_data)
        self.guide_btn.setVisible(not has_data)
        self._render_warnings()

        # history
        p = self.plot_hist
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        h = networth_history(store)
        if len(h) > 1 and has_data:
            reg = self.hist_series
            reg.reset()
            ef = emergency_fund_target(store, 3)
            reg.add("zero", charts.zero_floor(p))
            reg.add("starter", charts.soft_floor(
                p, 1000.0, "$1.0k · starter buffer", theme.AMBER,
                slot="starter"))
            if ef > 1050.0:
                reg.add("emergency", charts.soft_floor(
                    p, ef, f"{charts._short(ef)} · 3-month emergency fund",
                    theme.ACCENT, slot="emergency"))
            x = charts.to_ts(h.index)
            reg.add("networth", charts.line(p, x, h["networth"].values,
                                            theme.ACCENT, 2.4, "Net worth"))
            reg.add("cash", charts.line(p, x, h["cash"].values, theme.BLUE,
                                        1.3, "Net cash"))
            if np.any(h["investments"].values > 0):
                reg.add("invest", charts.line(p, x, h["investments"].values,
                                              theme.PURPLE, 1.3, "Investments"))
            if np.any(h["debt"].values > 0):
                reg.add("debt", charts.line(p, x, -h["debt"].values, theme.RED,
                                            1.3, "Debt"))
            reg.apply()
            charts.lock_view(
                p, float(x[0]), float(x[-1]),
                [h["networth"].values, h["cash"].values,
                 h["investments"].values, -h["debt"].values],
                include_y=[0.0, 1000.0, ef])

        # monthly cash flow — zero is the break-even line between the bars
        p = self.plot_flow
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        mc = monthly_cashflow(tx)
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
                             include_y=[0.0])

        # categories
        p = self.plot_cat
        p.clear()
        cs = category_spending(tx)
        if len(cs):
            charts.category_bars(p, list(cs.index), cs.values)
            p.autoRange()
