"""Insights page — the habits view.

Three stacked panels: a habits score (letter grade + a plain factual line on
whether you're living within your means), a composition donut (where the money
goes, subscriptions carved out), and dollar-ranked reduction suggestions.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ..insights import (breakdown, monthly_aggregates, spending_score,
                        suggestions, window_note)
from ..store import Store
from ..util import fmt_money
from . import theme
from .widgets import Card, DonutChart, hint, hsplit, nerd_button, vsplit

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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.empty = hint("Import transactions to calculate monthly spending and the spending score. "
                          "Only complete months are included.")
        root.addWidget(self.empty)

        # ---- score panel ----
        self.score_card = Card("Spending habits")
        # The score is two benchmarked sub-scores blended with an interaction
        # penalty, and saying that on the card helped nobody. What goes on the
        # card is the grade and the sentence; the ratios, the targets they are
        # measured against and the weighting live behind the button.
        nrow = QHBoxLayout()
        nrow.addStretch(1)
        nrow.addWidget(nerd_button(self._nerd_panel))
        self.score_card.body.addLayout(nrow)
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
            "This score is provisional because much of the spending is uncategorized. "
            "Review those transactions before relying on the score.")
        self.provisional.setVisible(False)
        self.components = QLabel("")
        self.components.setObjectName("statSub")
        self.components.setWordWrap(True)
        self.explain = hint(
            "The score compares discretionary spending with necessities and savings "
            "with income. It uses the 50/30/20 guideline: 50% of income for needs, "
            "30% for wants, and 20% saved. This is a benchmark, not a personal budget.")
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
        comp_card = Card("Average monthly spending")
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

        sug_card = Card("Possible spending changes")
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
            c.setMinimumHeight(330 if c is self.score_card else 300)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        root.addWidget(vsplit(self.score_card, bottom, sizes=[330, 380]), 1)

    # ---------------------------------------------------------------
    def set_simple(self, on: bool) -> None:
        """Drop the score's arithmetic; keep the grade and the sentence.

        "how you spend 8.5/10 · how you're doing 4.4/10" is the grade shown
        working, and the working is only interesting once you already trust the
        grade. Same for the two-year weighting note. Both are in For nerds.
        """
        self._simple = on
        self.components.setVisible(not on)
        self.window_note.setVisible(not on)

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
        self._agg, self._score = agg, sc
        col = _grade_color(sc.value)
        self.grade.setText(sc.letter)
        self.grade.setStyleSheet(f"font-size: 58px; font-weight: 700; color: {col};")
        self.grade_num.setText(f"{sc.value:.1f} / 10")
        self.means.setText(sc.means_line)
        self.means.setStyleSheet(f"font-size: 17px; font-weight: 600; color: {col};")
        self.detail.setText(sc.detail)
        # Two plainly-named halves and no acronym. "FESR 0.62 vs 0.60 target"
        # is a precise statement of something the reader has no unit for; "for
        # every $1 on necessities you spend 62c on wants" is the same fact in
        # money they recognise.
        self.components.setText(
            f"how you spend  {sc.habit_component:.1f}/10   ·   "
            f"how you're doing  {sc.means_component:.1f}/10")
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

    # ---------------- for nerds ----------------
    def _nerd_panel(self) -> tuple[str, str]:
        """How the grade is actually constructed, for whoever wants to argue."""
        title = "Habits score · how the grade is built"
        sc = getattr(self, "_score", None)
        agg = getattr(self, "_agg", None)
        if sc is None or agg is None:
            return title, "<p>Nothing scored yet — import some transactions.</p>"
        from ..insights import (FESR_TARGET, HALF_LIFE_MONTHS, LOOKBACK_MONTHS,
                                SAVINGS_TARGET, weight_shares)
        sh = weight_shares()
        rows = [
            "<h3>Two sub-scores, blended</h3>",
            "<table>",
            f"<tr><th>Wants ÷ needs (FESR)</th>"
            f"<td>{sc.fesr:.2f} <span class='m'>· target ≤ "
            f"{FESR_TARGET:.2f}, the 30/50 of 50/30/20</span></td></tr>",
            f"<tr><th>→ how you spend</th>"
            f"<td>{sc.habit_component:.2f} / 10</td></tr>",
            f"<tr><th>Savings rate</th>"
            f"<td>{sc.savings_rate * 100:.1f}% <span class='m'>· target "
            f"{SAVINGS_TARGET * 100:.0f}%</span></td></tr>",
            f"<tr><th>→ how you're doing</th>"
            f"<td>{sc.means_component:.2f} / 10</td></tr>",
            f"<tr><th>Overspending penalty</th>"
            f"<td>−{sc.danger_penalty:.2f}</td></tr>",
            f"<tr><th>Final</th><td><b>{sc.value:.2f} / 10 "
            f"({sc.letter})</b></td></tr>",
            f"<tr><th>Quadrant</th><td><code>{sc.quadrant}</code></td></tr>",
            "</table>",
            f"<p class='m'>The penalty is an interaction term: it only bites "
            f"when both halves are weak, so a shortfall caused by circumstance "
            f"is not punished the way the same shortfall caused by "
            f"discretionary spending is.</p>",
            f"<p class='m'>FESR compares wants to <i>needs</i> rather than to "
            f"income on purpose — grading someone against their income "
            f"punishes a low earner for a circumstance rather than a habit. It "
            f"is scale-free, though, so wants and needs can inflate together "
            f"and the ratio still reads as fine; the shares of income are what "
            f"catch that, and the verdict consults them before blaming or "
            f"absolving spending habits.</p>",
            "<h3>The monthly figures being scored</h3><table>",
            f"<tr><th>Income</th><td>{fmt_money(agg.income)}</td></tr>",
            f"<tr><th>Necessities</th><td>{fmt_money(agg.essential)}</td></tr>",
            f"<tr><th>Wants</th><td>{fmt_money(agg.frivolous)}</td></tr>",
            f"<tr><th>Giving</th><td>{fmt_money(agg.giving)} "
            f"<span class='m'>· own bucket, kept out of the ratio</span>"
            f"</td></tr>",
            f"<tr><th>Not yet categorised</th><td>{fmt_money(agg.unknown)} "
            f"({sc.unknown_share * 100:.0f}% of spending)</td></tr>",
            f"<tr><th>Left over</th>"
            f"<td>{fmt_money(agg.surplus, signed=True)}</td></tr>",
            "</table>",
            "<h3>The window</h3>",
            f"<p>{agg.n_months} complete months contribute, up to "
            f"{LOOKBACK_MONTHS}, each weighted by an exponential decay with a "
            f"{HALF_LIFE_MONTHS:.0f}-month half-life. The most recent quarter "
            f"therefore carries about {sh[3] * 100:.0f}% of the answer, the "
            f"last six months about {sh[6] * 100:.0f}%, and anything beyond a "
            f"year is a rounding error.</p>",
            f"<p class='m'>A hard \"last three months\" window would throw away "
            f"usable history and put a cliff at the boundary — a big purchase "
            f"silently stops counting the day it ages out. The decay has no "
            f"edge to fall off. The current, incomplete month is "
            f"{'excluded' if agg.partial_current else 'included'}.</p>",
        ]
        if sc.provisional:
            rows.append(
                f"<p style='color:{theme.AMBER}'>Provisional: "
                f"{sc.unknown_share * 100:.0f}% of spending is still "
                f"uncategorised and excluded from the ratio, which is above the "
                f"25% threshold at which the grade stops being trustworthy.</p>")
        return title, "".join(rows)

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
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            card.setMinimumHeight(100)
            self.sug_box.addWidget(card)
        self.sug_box.addStretch(1)
