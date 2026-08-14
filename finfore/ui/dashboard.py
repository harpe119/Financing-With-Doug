"""Dashboard page — where the ledger becomes a picture."""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from ..alerts import annual_total, bill_changes, summarise
from ..history import (category_spending, headline_stats, monthly_cashflow,
                       networth_history, reconcile)
from ..insights import emergency_fund_target
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .widgets import (Card, CollapsibleCard, LegendToggles, SeriesRegistry,
                      StatCard, hint, hsplit, info_button, vsplit)


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

        # Bills that moved on their own. This is the one thing on the dashboard
        # that is *news* rather than a measurement, which is why it gets a strip
        # of its own at the top rather than a number in a card: a subscription
        # that went up four dollars is invisible in a net-worth line and obvious
        # in a sentence. It appears only when there is something to say.
        self.alert_row = QWidget()
        arl = QHBoxLayout(self.alert_row)
        arl.setContentsMargins(0, 0, 0, 0)
        arl.setSpacing(8)
        self.alert = QLabel("")
        self.alert.setObjectName("billAlert")
        self.alert.setWordWrap(True)
        self.alert.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        arl.addWidget(self.alert, 1)
        self.alert_btn = info_button(
            "What changed?", self._changes_panel,
            tooltip="Every recurring charge whose amount has moved, what it "
                    "used to be, and what it now costs you a year")
        arl.addWidget(self.alert_btn)
        self.alert_row.setVisible(False)
        lay.addWidget(self.alert_row)

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

        # Foldable, because the net-worth history is the chart people actually
        # sit and read and the other two are references you glance at. A
        # divider can already give history most of the page, but it cannot give
        # it *all* of the page — two cards will not shrink past their own
        # minimum height. Folding them to their title strips will, and the
        # chevron is the same control the Forecast page's scenario cards use.
        flow_card = CollapsibleCard("Monthly cash flow", expanded=True)
        self.plot_flow = charts.new_plot(legend=True)
        self.plot_flow.setMinimumHeight(100)
        flow_card.body.addWidget(self.plot_flow)
        cat_card = CollapsibleCard("Spending by category · last 90 days",
                                   expanded=True)
        self.plot_cat = charts.new_plot(date_axis=False, money_left=False,
                                        money_bottom=True)
        self.plot_cat.setMinimumHeight(100)
        cat_card.body.addWidget(self.plot_cat)
        self.flow_card, self.cat_card = flow_card, cat_card
        for c in (flow_card, cat_card):
            c.header.setToolTip("Fold this away and give the net worth history "
                                "the room")
            c.header.clicked.connect(self._sync_split)

        # Draggable dividers rather than fixed 3:2 and 1:1 splits. Which chart
        # deserves the room depends on what is being looked at right now, and
        # that is not a question this file can answer once for everybody.
        bottom = hsplit(flow_card, cat_card, sizes=[500, 500])
        hist_card.setMinimumHeight(120)
        # A folded card is a title strip, so its minimum has to allow that.
        for c in (flow_card, cat_card):
            c.setMinimumHeight(34)
        for c in (hist_card, flow_card, cat_card):
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.split = vsplit(hist_card, bottom, sizes=[430, 300])
        lay.addWidget(self.split, 1)

    # ---------------------------------------------------------------
    def _sync_split(self) -> None:
        """Hand the freed height to the history rather than leaving a gap.

        A QSplitter keeps whatever sizes it was given, so folding both cards
        without this leaves the bottom pane the same height with two title
        strips floating at the top of it — the space is freed and nobody gets
        it. Reclaiming it is the entire point of folding them.
        """
        folded = not (self.flow_card.expanded or self.cat_card.expanded)
        # The panes' own sizes, not the splitter's height: height() is the
        # widget's, which includes the handle and — before the window has been
        # laid out even once — is not yet anything at all.
        total = max(1, sum(self.split.sizes()))
        if folded:
            strip = max(self.flow_card.minimumHeight(),
                        self.flow_card.header.sizeHint().height() + 24)
            self.split.setSizes([max(1, total - strip), strip])
        else:
            self.split.setSizes([int(total * 0.58), int(total * 0.42)])

    def _render_alerts(self) -> None:
        """Say plainly which recurring charges moved on their own."""
        try:
            self._changes = bill_changes(self.store)
        except Exception:
            self._changes = []
        self.alert_row.setVisible(bool(self._changes))
        if self._changes:
            self.alert.setText(summarise(self._changes))

    def _changes_panel(self) -> tuple[str, str]:
        changes = getattr(self, "_changes", [])
        if not changes:
            return ("What changed",
                    "<p>Nothing has moved — every recurring charge is still "
                    "what it was.</p>")
        net = annual_total(changes)
        rows = [f"<p>{summarise(changes)}</p>"]
        if abs(net) >= 1:
            rows.append(
                f"<p>Net effect: <b>{fmt_money(abs(net))} a year "
                f"{'more' if net > 0 else 'less'}</b> than before these "
                f"changes.</p>")
        for c in changes:
            col = theme.RED if c.worse else theme.ACCENT
            rows.append(
                f"<h4 style='color:{col}'>{c.headline()}</h4>"
                f"<p>{c.detail()}</p>")
        rows.append(
            "<p class='m'>How this is judged: each charge is compared against "
            "its own history, not against a fixed threshold. A bill that "
            "normally varies — electricity, a phone bill with roaming on it — "
            "has to move by more than three times its usual swing before it is "
            "mentioned at all, which is why a hot August does not appear here "
            "and a subscription going up by two dollars does. Deactivating a "
            "pattern on the Recurring page stops it being checked.</p>")
        return "What changed without being asked", "".join(rows)

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
        self._render_alerts()
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
            # Opens on the last twelve months — where the detail people came
            # for lives — and zooms out to the whole ledger from there.
            charts.lock_view(
                p, float(x[0]), float(x[-1]),
                [h["networth"].values, h["cash"].values,
                 h["investments"].values, -h["debt"].values],
                include_y=[0.0, 1000.0, ef],
                initial_days=charts.HISTORY_INITIAL_DAYS, x_data=x)

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
                             include_y=[0.0],
                             initial_days=charts.HISTORY_INITIAL_DAYS + 30,
                             x_data=mx, zoom_y=False)

        # categories
        p = self.plot_cat
        p.clear()
        cs = category_spending(tx)
        if len(cs):
            charts.category_bars(p, list(cs.index), cs.values)
            p.autoRange()
