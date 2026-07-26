"""Insights page — the habits view.

Three stacked panels: a habits score (letter grade + a plain factual line on
whether you're living within your means), a composition donut (where the money
goes, subscriptions carved out), and dollar-ranked reduction suggestions.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ..insights import (breakdown, monthly_aggregates, spending_score,
                        suggestions, window_note)
from ..store import Store
from ..util import fmt_money
from . import theme
from .widgets import Card, DonutChart, hint, hsplit, vsplit

_KIND_ACCENT = {
    "subscriptions": theme.PURPLE, "trim": theme.AMBER,
    "categorize": theme.BLUE, "info": theme.MUTED, "praise": theme.ACCENT,
}


def _grade_color(score: float) -> str:
    if score >= 7.0:
        return theme.ACCENT          # living within means
    if score >= 3.0:
        return theme.AMBER           # room to improve (caution, not alarm)
    return theme.RED                 # genuinely dire


class InsightsPage(QWidget):
    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.empty = hint("Import a few weeks of transactions (or load the demo "
                          "dataset) to see your spending breakdown, a habits "
                          "score, and tailored suggestions.")
        root.addWidget(self.empty)

        # ---- score panel ----
        self.score_card = Card("Spending habits")
        srow = QHBoxLayout()
        srow.setSpacing(18)
        self.grade = QLabel("—")
        self.grade.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grade.setFixedWidth(150)
        self.grade.setStyleSheet("font-size: 58px; font-weight: 700;")
        gcol = QVBoxLayout()
        gcol.setSpacing(0)
        gcol.addStretch(1)
        gcol.addWidget(self.grade)
        self.grade_num = QLabel("")
        self.grade_num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grade_num.setObjectName("statSub")
        gcol.addWidget(self.grade_num)
        gcol.addStretch(1)
        srow.addLayout(gcol)

        tcol = QVBoxLayout()
        tcol.setSpacing(6)
        tcol.addStretch(1)
        self.means = QLabel("")
        self.means.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.means.setWordWrap(True)
        self.detail = hint("")
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.provisional = hint(
            "Grade is provisional — a large share of spending is still "
            "uncategorized and is excluded from the ratio. Use "
            "Transactions → Review uncategorized to fix the biggest ones fast.")
        self.provisional.setVisible(False)
        self.components = QLabel("")
        self.components.setObjectName("statSub")
        self.components.setWordWrap(True)
        self.explain = hint(
            "Two things are scored separately: how you spend (wants against "
            "essentials, benchmarked to the 50/30/20 guideline of 0.60) and how "
            "you're doing (net income against the 20% savings target). A tight "
            "month with disciplined spending still grades well; the same "
            "shortfall caused by discretionary spending does not.")
        # Say how far back this looks, in the terms people actually ask about.
        # "24 months" on its own invites the reading that two years are treated
        # evenly; the half-life is what makes the recent quarter half the answer.
        self.window_note = hint("")
        tcol.addWidget(self.means)
        tcol.addWidget(self.verdict)
        tcol.addWidget(self.detail)
        tcol.addWidget(self.components)
        tcol.addWidget(self.provisional)
        tcol.addWidget(self.window_note)
        tcol.addWidget(self.explain)
        tcol.addStretch(1)
        srow.addLayout(tcol, 1)
        self.score_card.body.addLayout(srow)

        # ---- composition + suggestions ----
        comp_card = Card("Where your money goes · monthly average, recent months weighted heavier")
        crow = QHBoxLayout()
        self.donut = DonutChart()
        self.donut.setMinimumSize(210, 210)
        crow.addWidget(self.donut, 1)
        self.legend = QVBoxLayout()
        self.legend.setSpacing(7)
        self.legend.addStretch(1)
        legend_host = QWidget()
        legend_host.setLayout(self.legend)
        legend_host.setFixedWidth(210)
        crow.addWidget(legend_host)
        comp_card.body.addLayout(crow)

        sug_card = Card("Ways to free up cash")
        self.sug_scroll = QScrollArea()
        self.sug_scroll.setWidgetResizable(True)
        self.sug_host = QWidget()
        self.sug_box = QVBoxLayout(self.sug_host)
        self.sug_box.setContentsMargins(0, 0, 6, 0)
        self.sug_box.setSpacing(8)
        self.sug_scroll.setWidget(self.sug_host)
        sug_card.body.addWidget(self.sug_scroll)

        # The score panel is the tallest block of prose on the page and the one
        # most often in the way once it has been read. Putting it above a
        # divider lets it be folded down to a strip without losing it.
        bottom = hsplit(comp_card, sug_card, sizes=[500, 500])
        for c in (self.score_card, comp_card, sug_card):
            c.setMinimumHeight(90)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        root.addWidget(vsplit(self.score_card, bottom, sizes=[330, 380]), 1)

    # ---------------------------------------------------------------
    def refresh(self) -> None:
        tx = self.store.transactions()
        agg = monthly_aggregates(tx)
        has = bool(agg is not None and agg.income > 0)
        self.empty.setVisible(not has)
        self.score_card.setVisible(has)
        if not has:
            self._clear_legend()
            self._clear_suggestions()
            self.donut.set_data([])
            return

        # score
        sc = spending_score(agg)
        col = _grade_color(sc.value)
        self.grade.setText(sc.letter)
        self.grade.setStyleSheet(f"font-size: 58px; font-weight: 700; color: {col};")
        self.grade_num.setText(f"{sc.value:.1f} / 10")
        self.means.setText(sc.means_line)
        self.means.setStyleSheet(f"font-size: 17px; font-weight: 600; color: {col};")
        self.detail.setText(sc.detail)
        pen = (f"   −  overspending penalty {sc.danger_penalty:.1f}"
               if sc.danger_penalty >= 0.05 else "")
        self.components.setText(
            f"habits (FESR {sc.fesr:.2f} vs 0.60 target)  {sc.habit_component:.1f}/10"
            f"      means (saving {sc.savings_rate * 100:.0f}% vs 20% target)  "
            f"{sc.means_component:.1f}/10{pen}")
        self.verdict.setText(sc.headline)
        self.verdict.setStyleSheet(f"font-size: 13px; color: {col};")
        self.provisional.setVisible(sc.provisional)
        self.window_note.setText(window_note(agg))

        # composition
        slices = breakdown(agg)
        self.donut.set_data(slices, "spending", fmt_money(agg.spending))
        self._rebuild_legend(slices, agg.spending)

        # suggestions
        self._rebuild_suggestions(suggestions(agg, self.store.recurring(), tx))

    # ---- legend ----
    def _clear_legend(self) -> None:
        while self.legend.count():
            it = self.legend.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _rebuild_legend(self, slices, total: float) -> None:
        self._clear_legend()
        for lbl, val, col in slices:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(f"background: {col}; border-radius: 3px;")
            name = QLabel(lbl)
            pct = QLabel(f"{val / total * 100:.0f}%  ·  {fmt_money(val)}")
            pct.setObjectName("statSub")
            pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(swatch)
            rl.addWidget(name)
            rl.addStretch(1)
            rl.addWidget(pct)
            self.legend.addWidget(row)
        self.legend.addStretch(1)

    # ---- suggestions ----
    def _clear_suggestions(self) -> None:
        while self.sug_box.count():
            it = self.sug_box.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _rebuild_suggestions(self, sugs) -> None:
        self._clear_suggestions()
        if not sugs:
            self.sug_box.addWidget(hint("No suggestions — nothing stands out."))
            self.sug_box.addStretch(1)
            return
        for s in sugs:
            card = QWidget()
            card.setStyleSheet(
                f"background: {theme.SURFACE2}; border: 1px solid {theme.BORDER};"
                f"border-radius: 8px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 9, 12, 9)
            cl.setSpacing(3)
            head = QHBoxLayout()
            accent = _KIND_ACCENT.get(s.kind, theme.MUTED)
            title = QLabel(s.title)
            title.setStyleSheet(f"font-weight: 600; color: {accent};")
            title.setWordWrap(True)
            head.addWidget(title, 1)
            if s.monthly_impact > 0:
                badge = QLabel(f"~{fmt_money(s.monthly_impact)}/mo")
                badge.setObjectName("statSub")
                badge.setStyleSheet(f"color: {theme.ACCENT};")
                head.addWidget(badge)
            cl.addLayout(head)
            body = QLabel(s.detail)
            body.setWordWrap(True)
            body.setObjectName("muted")
            cl.addWidget(body)
            self.sug_box.addWidget(card)
        self.sug_box.addStretch(1)
