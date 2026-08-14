"""Progress page — did the last projection hold, and is anything better?

Every other page answers a question about now. This one is about the gap
between two nows, which is the only place the app can be caught being wrong,
and the only place a slow improvement becomes visible.
"""
from __future__ import annotations

from datetime import date

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QMessageBox, QPushButton, QSizePolicy,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..progress import (ALPHA, MIN_WEEKS, N_BOOTSTRAP, N_PERMUTATIONS, capture,
                        category_shifts, check_path, score_move,
                        significance_since)
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .widgets import (Card, StatCard, hint, hsplit, info_button, nerd_button,
                      vsplit)

_STATUS_COLOR = {
    "ahead": theme.ACCENT, "on-path": theme.BLUE,
    "behind": theme.AMBER, "broken": theme.PURPLE,
}

# Two words on this page are jargon nobody arrives already knowing, and both of
# them sit on buttons. "Checkpoint" sounds like a save file, which is close
# enough to be dangerous — the obvious guess is that clearing them loses your
# data. "Reconstruct history" sounds like it edits the ledger. Neither does.
# The strip under the toolbar says what they are before anybody has to press
# one to find out, and the full explanation is one click away.
_WHAT_IS_THIS = f"""
<h3>What this page is doing</h3>
<p>Every other page in this app answers a question about <i>now</i>. This one
answers questions about the gap between two <i>nows</i>: <b>was the old
forecast any good</b>, and <b>did anything actually change</b>. Both need
something written down in the past to compare against, which is what a
checkpoint is.</p>

<h4>A checkpoint</h4>
<p>A dated snapshot, taken automatically <b>every time you import a CSV</b>. It
records three things:</p>
<p>· your <b>balance sheet</b> that day — net worth, cash, investments, debt;<br>
· your <b>habits score</b> and the pieces it was built from;<br>
· the <b>forecast Doug was making at that moment</b>, in full.</p>
<p>The third one is the point of the whole exercise. A projection is only a
claim if you write it down <i>before</i> the outcome is known. Anyone can refit
a model afterwards and marvel at how well it fits — that is how models flatter
themselves, and it is exactly what this page refuses to do.</p>
<p>The first two cannot be recovered after the fact either: the score weights
recent months more heavily, so once newer months exist there is no way to
recompute what it said last March.</p>
<p><b>Take a checkpoint now</b> records one on demand — useful just before you
change something deliberately, so you have a clean line to measure from.</p>

<h4>Reconstructing history</h4>
<p>If you imported two years of statements on your first run, you own every
scrap of data this page needs and none of the checkpoints — and would otherwise
wait months for it to say anything at all. <b>Reconstruct history</b> works out
what the checkpoints <i>would have said</i> on a series of past dates, roughly
two months apart.</p>
<p>The care taken here is the whole reason to trust the result. Each
reconstructed point is built <b>only from data that existed on its own
date</b>: the recurring patterns are re-detected from the truncated ledger
rather than reused from today's, the behavioural sample is cut off at that
date, and the starting balance is read from the reconstructed history rather
than from today. A retroactive "projection" built with hindsight would make
this page report Doug as far better than he is, which is the one thing it
exists to prevent.</p>
<p>It adds nothing that already exists, so pressing it twice is harmless.</p>

<h4>What none of this touches</h4>
<p>Checkpoints are a separate list from your money. Taking one, reconstructing
them, or clearing every last one <b>never</b> alters an account, a transaction,
a goal or an imported row. <b>Clear checkpoints</b> empties this page and
nothing else — a new one arrives at your next import, and Reconstruct history
can rebuild the old ones whenever you like.</p>
"""


class ProgressPage(QWidget):
    COLS = ["Taken", "Score", "Net worth", "Income /mo", "Spending /mo",
            "Saved", "Txns"]

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._cps = []
        # Findings from the last render, so the For nerds window quotes exactly
        # what the page is showing rather than recomputing (a second run of a
        # permutation test would give a slightly different p-value, and an
        # explanation that disagrees with the thing it explains is worthless).
        self._sig = self._check = self._move = self._old = self._now = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Compare against"))
        self.pick = QComboBox()
        self.pick.setMinimumWidth(220)
        self.pick.setToolTip("Which past checkpoint everything on this page is "
                             "measured against")
        self.pick.currentIndexChanged.connect(self._render)
        bar.addWidget(self.pick)
        snap = QPushButton("Take a checkpoint now")
        snap.setObjectName("small")
        snap.setToolTip(
            "Record a dated snapshot right now — your balances, your habits "
            "score, and the forecast Doug is making today, saved so it can be "
            "checked against reality later. One is taken automatically on "
            "every import; this is for when you are about to change something "
            "deliberately and want a clean line to measure from.")
        snap.clicked.connect(self._manual_capture)
        bar.addWidget(snap)
        self.fill_btn = QPushButton("Reconstruct history…")
        self.fill_btn.setObjectName("small")
        self.fill_btn.setToolTip(
            "Work out what the checkpoints would have said on a handful of "
            "past dates, using only the data that existed on each of those "
            "dates. For when you have just imported years of statements and "
            "would rather not wait months for this page to fill up. It reads "
            "your ledger; it never changes it.")
        self.fill_btn.clicked.connect(self._backfill)
        bar.addWidget(self.fill_btn)
        self.clear_btn = QPushButton("Clear checkpoints…")
        self.clear_btn.setObjectName("danger")
        self.clear_btn.setToolTip(
            "Empty this page and start it over. Only the snapshots go — your "
            "accounts, transactions, goals and imported rows are untouched.")
        self.clear_btn.clicked.connect(self._clear)
        bar.addWidget(self.clear_btn)
        bar.addStretch(1)
        bar.addWidget(info_button(
            "What is a checkpoint?",
            lambda: ("Checkpoints, and what this page compares", _WHAT_IS_THIS),
            tooltip="What a checkpoint is, what reconstructing history does, "
                    "and what neither of them touches"))
        bar.addWidget(nerd_button(self._nerd_panel))
        root.addLayout(bar)

        # Always on, not just when the page is empty. The buttons above it are
        # the two most misread controls in the app, and a caption that
        # disappears the moment the page has data is a caption that is only
        # ever shown to people who cannot yet use it.
        root.addWidget(hint(
            "A checkpoint is a dated snapshot — your balances, your score, and "
            "the forecast Doug was making that day — saved automatically every "
            "time you import a CSV. This page holds the newest one against an "
            "older one to ask two questions: did that old forecast hold up, and "
            "has anything really changed?   ·   Reconstruct history fills in "
            "checkpoints for dates your ledger already covers, so the page "
            "works today instead of in six months. Nothing here ever alters "
            "your accounts or transactions."))

        self.empty = hint(
            "Nothing to compare yet — this needs two checkpoints and there are "
            "fewer than that. Import a CSV to record one, or press "
            "Reconstruct history if your ledger already reaches back a few "
            "months.")
        root.addWidget(self.empty)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_score = StatCard("Habits score")
        self.card_path = StatCard("Against the old projection")
        self.card_spend = StatCard("Discretionary spending")
        self.card_worth = StatCard("Net worth")
        for c in (self.card_score, self.card_path, self.card_spend,
                  self.card_worth):
            cards.addWidget(c)
        root.addLayout(cards)

        # --- projection vs reality ---
        path_card = Card("Then and now — the old projection against what happened")
        self.plot = charts.new_plot(legend=True)
        self.plot.setMinimumHeight(150)
        path_card.body.addWidget(self.plot)
        self.path_note = hint("")
        self.path_note.setWordWrap(True)
        path_card.body.addWidget(self.path_note)

        # --- significance ---
        sig_card = Card("Is the change real, or is it noise?")
        self.sig_head = QLabel("")
        self.sig_head.setWordWrap(True)
        self.sig_head.setStyleSheet("font-size: 15px; font-weight: 600;")
        sig_card.body.addWidget(self.sig_head)
        self.sig_body = QLabel("")
        self.sig_body.setWordWrap(True)
        self.sig_body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sig_body.setAlignment(Qt.AlignmentFlag.AlignTop)
        sig_card.body.addWidget(self.sig_body, 1)

        # --- score history + checkpoint table ---
        hist_card = Card("Score at every checkpoint")
        self.plot_score = charts.new_plot(money_left=False, legend=False)
        self.plot_score.setMinimumHeight(120)
        hist_card.body.addWidget(self.plot_score)

        table_card = Card("Checkpoints")
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        table_card.body.addWidget(self.table, 1)

        self.hist_card, self.table_card = hist_card, table_card
        top = hsplit(path_card, sig_card, sizes=[620, 460])
        bottom = hsplit(hist_card, table_card, sizes=[440, 640])
        for c in (path_card, sig_card, hist_card, table_card):
            c.setMinimumHeight(110)
            c.setMinimumWidth(200)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        root.addWidget(vsplit(top, bottom, sizes=[400, 300]), 1)

    # ---------------------------------------------------------------
    def set_simple(self, on: bool) -> None:
        """Keep the two verdicts; put away the record-keeping.

        This page answers two questions — did the forecast hold, and did
        anything change — and then shows its filing cabinet: a score-history
        chart, a table of every checkpoint, and a red button that empties both.
        The verdicts are what people come for. The cabinet is for the person
        who wants to audit them, and a destructive button in front of somebody
        still working out what a checkpoint *is* is worse than merely noisy.
        """
        self.clear_btn.setVisible(not on)
        self.hist_card.setVisible(not on)
        self.table_card.setVisible(not on)

    def refresh(self) -> None:
        try:
            self._refresh_inner()
        except Exception as e:
            self.path_note.setText(f"Could not build the comparison: {e}")

    def _refresh_inner(self) -> None:
        self._cps = self.store.checkpoints()
        prev = self.pick.currentData()
        self.pick.blockSignals(True)
        self.pick.clear()
        # Everything except the newest: comparing the latest against itself
        # would report a change of exactly zero and mean nothing.
        for cp in reversed(self._cps[:-1]):
            label = cp.when.strftime("%d %b %Y")
            if cp.label:
                label += f" · {cp.label}"
            self.pick.addItem(label, cp.id)
        i = self.pick.findData(prev)
        self.pick.setCurrentIndex(i if i >= 0 else 0)
        self.pick.blockSignals(False)

        enough = len(self._cps) >= 2
        self.empty.setVisible(not enough)
        for w in (self.card_score, self.card_path, self.card_spend,
                  self.card_worth):
            w.setVisible(enough)
        self._render_table()
        self._render_score_history()
        if enough:
            self._render()
        else:
            self.plot.clear()
            self.path_note.setText("")
            self.sig_head.setText("")
            self.sig_body.setText("")
            # Drop the cached findings too, or the For nerds window keeps
            # describing a comparison the page has stopped showing.
            self._sig = self._check = self._move = self._old = None

    def _manual_capture(self) -> None:
        try:
            capture(self.store, label="manual")
        except Exception as e:
            QMessageBox.warning(self, "Couldn't take a checkpoint", str(e))
            return
        self.refresh()
        self.main.status("Checkpoint recorded.")

    def _backfill(self) -> None:
        """Reconstruct the checkpoints a ledger already has the data for.

        Someone who imports two years of history on their first run owns
        everything this page needs and none of the checkpoints, and would
        otherwise wait months for it to say anything. Each reconstructed point
        is built only from data that existed at its own date, so the
        calibration figures stay honest.
        """
        box = QMessageBox(self)
        box.setWindowTitle("Reconstruct history")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Build checkpoints for dates your ledger already covers?")
        box.setInformativeText(
            "This page needs snapshots from the past to compare against, and "
            "it only starts collecting them once you begin importing. If your "
            "ledger already reaches back a year or two, the snapshots can be "
            "worked out now instead of waited for.\n\n"
            "Up to six dates, about two months apart, are reconstructed. Each "
            "one is computed only from the data that existed on its own date — "
            "the forecasts are refitted as of then, never with hindsight — so "
            "what this page later says about Doug's accuracy stays honest.\n\n"
            "It reads your transactions and writes only checkpoints: no "
            "account, transaction or goal is changed. Dates that already have "
            "a checkpoint are skipped, so pressing this twice is harmless.\n\n"
            "It takes a few seconds.")
        box.setStandardButtons(QMessageBox.StandardButton.Cancel)
        go = box.addButton("Reconstruct", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is not go:
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from ..progress import backfill
            made = backfill(self.store, n=6, spacing_days=55)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't reconstruct", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh()
        self.main.status(
            f"Reconstructed {made} checkpoint{'s' if made != 1 else ''}."
            if made else
            "Nothing to reconstruct — your ledger doesn't reach far enough "
            "back, or those dates already have checkpoints.")

    def _clear(self) -> None:
        """Remove the checkpoints and nothing else.

        Reconstructing history is easy to press twice, or at a spacing that
        turns out not to be useful, and there was no way back — the comparison
        list just kept growing. This resets the page to empty so it can be
        rebuilt, and is careful to say what it does *not* delete: a button next
        to a financial ledger that says "clear" needs to be unambiguous about
        its blast radius.
        """
        cps = self.store.checkpoints()
        if not cps:
            QMessageBox.information(self, "Nothing to clear",
                                    "There are no checkpoints yet.")
            return
        auto = sum(1 for c in cps if c.label not in ("history", "backfilled"))
        built = len(cps) - auto
        box = QMessageBox(self)
        box.setWindowTitle("Clear checkpoints")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"Delete all {len(cps)} checkpoint{'s' if len(cps) != 1 else ''} "
            f"and start this page over?")
        box.setInformativeText(
            f"{built} were reconstructed and {auto} recorded at an import.\n\n"
            f"This removes only the saved snapshots that this page compares. "
            f"Your accounts, transactions, goals and imported CSV data are NOT "
            f"touched.\n\n"
            f"A new checkpoint is recorded on your next import, and "
            f"Reconstruct history can rebuild the older ones at any time.")
        box.setStandardButtons(QMessageBox.StandardButton.Cancel)
        go = box.addButton("Clear checkpoints",
                           QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        n_tx = len(self.store.transactions())
        for c in cps:
            self.store.delete_checkpoint(c.id)
        self.refresh()
        self.main.status(
            f"Cleared {len(cps)} checkpoint{'s' if len(cps) != 1 else ''}. "
            f"{n_tx} transactions untouched.")

    def _selected(self):
        cid = self.pick.currentData()
        return next((c for c in self._cps if c.id == cid), None)

    # ---------------- for nerds ----------------
    def _nerd_panel(self) -> tuple[str, str]:
        """The statistics the page deliberately states in words instead."""
        title = "Progress · how any of this is known"
        sig = getattr(self, "_sig", None)
        chk = getattr(self, "_check", None)
        mv = getattr(self, "_move", None)
        old = getattr(self, "_old", None)
        if sig is None and chk is None:
            return title, ("<p>Nothing measured yet — this needs two "
                           "checkpoints with some time between them.</p>")
        m = theme.MUTED
        rows: list[str] = []

        if sig is not None and sig.enough_data:
            verdict = ("rejected" if sig.significant else "not rejected")
            rows += [
                "<h3>Did spending change? — two-sample permutation test</h3>",
                f"<p>Unit of analysis: <b>weekly discretionary spend</b>. Days "
                f"are too noisy and too autocorrelated — nobody buys groceries "
                f"daily — and months give four data points a year. Recurring "
                f"patterns are excluded, both because a rent rise is not a "
                f"change of habit and because their fixed monthly timing would "
                f"put a four-week cycle into a weekly series and violate the "
                f"independence the test assumes.</p>",
                "<table>",
                f"<tr><th>n before / after</th>"
                f"<td>{sig.n_before} / {sig.n_after} weeks "
                f"<span class='m'>(minimum {MIN_WEEKS} each)</span></td></tr>",
                f"<tr><th>Mean before / after</th>"
                f"<td>{fmt_money(sig.mean_before)} / "
                f"{fmt_money(sig.mean_after)} per week</td></tr>",
                f"<tr><th>Difference</th>"
                f"<td>{fmt_money(sig.diff, signed=True)}/week "
                f"({fmt_money(sig.monthly_diff, signed=True)}/month)</td></tr>",
                f"<tr><th>95% bootstrap CI</th>"
                f"<td>{fmt_money(sig.ci_low, signed=True)} to "
                f"{fmt_money(sig.ci_high, signed=True)} per week</td></tr>",
                f"<tr><th>p-value</th><td><b>{sig.p_value:.4f}</b> "
                f"<span class='m'>· null {verdict} at α = {ALPHA}</span>"
                f"</td></tr>",
                f"<tr><th>Effect size (Cohen's d)</th>"
                f"<td>{sig.effect:+.2f}</td></tr>",
                f"<tr><th>Minimum detectable difference</th>"
                f"<td>{fmt_money(sig.detectable)}/week</td></tr>",
                "</table>",
                f"<p class='m'>The null distribution is built by reshuffling "
                f"which weeks belong to which period {N_PERMUTATIONS:,} times "
                f"and asking how often chance alone produces a gap at least "
                f"this large. A permutation test rather than Welch's t because "
                f"it assumes only exchangeability under the null — no "
                f"normality, no equal variance. Weekly discretionary spend is "
                f"right-skewed with the occasional enormous week, which is "
                f"exactly the shape that makes a t-test's p-value a work of "
                f"fiction at n = 8. The interval is a {N_BOOTSTRAP:,}-resample "
                f"percentile bootstrap.</p>",
                f"<p class='m'>The minimum detectable difference is the answer "
                f"to <i>“so nothing changed, right?”</i> when p is large: with "
                f"these sample sizes, only a shift bigger than "
                f"{fmt_money(sig.detectable)}/week could have been told apart "
                f"from chance at all. A non-significant result is not evidence "
                f"of no change.</p>",
                f"<p class='m'>Category attribution below the verdict carries "
                f"no p-values on purpose. Twenty categories tested at 5% yields "
                f"one false positive per run on average, and presenting that "
                f"beside a real finding would undermine the one number on this "
                f"page that is defensible.</p>",
            ]
        elif sig is not None:
            rows += [
                "<h3>Did spending change?</h3>",
                f"<p>Not testable yet: {sig.n_before} weeks before the "
                f"checkpoint and {sig.n_after} after, against a minimum of "
                f"{MIN_WEEKS} on each side.</p>",
            ]

        if chk is not None:
            rows += [
                "<h3>Was the old forecast calibrated?</h3>",
                f"<p>Scored against the fan stored on "
                f"{chk.made_on:%d %b %Y}, over {chk.days_elapsed} days "
                f"({chk.n_points} daily points).</p><table>"
                f"<tr><th>Inside P10–P90</th>"
                f"<td>{chk.inside_80 * 100:.1f}% <span class='m'>"
                f"(nominal 80%)</span></td></tr>"
                f"<tr><th>Inside P25–P75</th>"
                f"<td>{chk.inside_50 * 100:.1f}% <span class='m'>"
                f"(nominal 50%)</span></td></tr>"
                f"<tr><th>Today vs its P50</th>"
                f"<td>{fmt_money(chk.delta, signed=True)}</td></tr>"
                f"<tr><th>Today's percentile in that fan</th>"
                f"<td>P{chk.percentile_now:.0f}</td></tr>"
                f"<tr><th>Status</th><td><code>{chk.status}</code></td></tr>"
                "</table>",
                f"<p class='m'>Coverage much <i>above</i> nominal means the fan "
                f"was too timid — wide bands are easy to be right inside and "
                f"tell you nothing. Much below means it was overconfident. "
                f"Only cash is scored, because cash is exactly reconstructable "
                f"from the ledger, where loan and investment balances depend on "
                f"snapshots that may never have been taken.</p>",
            ]

        if mv is not None and mv.components:
            rows.append("<h3>Score components</h3><table>"
                        "<tr><th>Component</th><th>Then</th><th>Now</th></tr>")
            for name, a, b in mv.components:
                fmt = ((lambda v: f"{v * 100:.0f}%") if "rate" in name.lower()
                       else (lambda v: f"{v:.2f}") if "FESR" in name
                       else (lambda v: fmt_money(v)))
                rows.append(f"<tr><td>{name}</td><td>{fmt(a)}</td>"
                            f"<td>{fmt(b)}</td></tr>")
            rows.append("</table>")
            rows.append(f"<p class='m'>The score itself is recency-weighted "
                        f"with a 13-week half-life, which is why it is stored "
                        f"at each checkpoint rather than recomputed: once newer "
                        f"months exist there is no way back to what it said on "
                        f"an earlier date.</p>")

        if old is not None:
            rows.append(f"<p class='m'>Everything above compares the newest "
                        f"checkpoint against the one taken "
                        f"{old.when:%d %b %Y}"
                        f"{' (' + old.label + ')' if old.label else ''}. "
                        f"Pick a different one in the toolbar and every figure "
                        f"here changes with it.</p>")
        rows.append(f"<p style='color:{m}'>No scipy anywhere in this: it is on "
                    f"the build's exclude list because pandas drags it in and "
                    f"it doubles the bundle. Everything here runs on numpy, "
                    f"which is not a compromise — a permutation test needs no "
                    f"distributional assumption at all, where a t-test would "
                    f"need one this data does not satisfy.</p>")
        return title, "".join(rows)

    # ---------------------------------------------------------------
    def _render(self) -> None:
        old = self._selected()
        if old is None or len(self._cps) < 2:
            return
        now = self._cps[-1]
        self._old, self._now = old, now

        mv = score_move(old, now)
        self._move = mv
        if mv.to_score is None:
            self.card_score.set_text("—", sub=mv.note)
        else:
            col = (theme.POSITIVE if mv.delta > 0.05 else
                   theme.NEGATIVE if mv.delta < -0.05 else None)
            self.card_score.set_text(
                f"{mv.to_score:.1f}  {now.grade}", color=col,
                sub=f"was {mv.from_score:.1f} {mv.from_grade} · {mv.note}"
                if mv.from_score is not None else mv.note)

        dw = now.networth - old.networth
        self.card_worth.set_money(
            now.networth, colored=False,
            sub=f"{fmt_money(dw, signed=True)} since {old.when.strftime('%b %Y')}")

        self._render_path(old)
        self._render_significance(old)

    def _render_path(self, old) -> None:
        p = self.plot
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        charts.unlock_view(p)

        chk = check_path(self.store, old)
        self._check = chk
        fan = old.forecast_frame()
        if chk is None or fan is None:
            self.card_path.set_text("—", sub="no projection stored")
            self.path_note.setText(
                "That checkpoint has no stored projection, so there is nothing "
                "to score. Checkpoints taken from now on will have one.")
            return

        from ..history import networth_history
        actual = networth_history(self.store)["cash"]
        idx = fan.index[fan.index <= actual.index.max()]
        if len(idx) < 2:
            self.path_note.setText("Not enough elapsed time to score it yet.")
            return
        x = charts.to_ts(idx)
        lo = fan["p10"].reindex(idx).to_numpy(dtype=float)
        hi = fan["p90"].reindex(idx).to_numpy(dtype=float)
        lo2 = fan["p25"].reindex(idx).to_numpy(dtype=float)
        hi2 = fan["p75"].reindex(idx).to_numpy(dtype=float)
        mid = fan["p50"].reindex(idx).to_numpy(dtype=float)
        import pyqtgraph as pg
        p.addItem(pg.FillBetweenItem(pg.PlotDataItem(x, lo), pg.PlotDataItem(x, hi),
                                     brush=pg.mkBrush(theme.qcolor(theme.BLUE, 34))))
        p.addItem(pg.FillBetweenItem(pg.PlotDataItem(x, lo2), pg.PlotDataItem(x, hi2),
                                     brush=pg.mkBrush(theme.qcolor(theme.BLUE, 62))))
        charts.line(p, x, mid, theme.BLUE, 1.6,
                    f"Projected {old.when.strftime('%b %Y')} · P50", dashed=True)
        act = actual.reindex(idx).to_numpy(dtype=float)
        charts.line(p, x, act, theme.TEXT, 2.4, "What actually happened")
        charts.zero_floor(p)
        charts.lock_view(p, float(x[0]), float(x[-1]), [act, lo, hi],
                         include_y=[0.0])

        col = _STATUS_COLOR.get(chk.status, theme.MUTED)
        self.card_path.set_text(
            {"ahead": "Ahead", "on-path": "On path", "behind": "Behind",
             "broken": "Off the map"}.get(chk.status, chk.status),
            color=col,
            sub=f"{fmt_money(chk.delta, signed=True)} against the middle of "
                f"that forecast")
        # Plainly: what it guessed, what happened, and how often it was in the
        # right neighbourhood. The two coverage fractions and the word
        # "calibrated" moved to For nerds — the sentence people need is that
        # reality mostly stayed inside the shading, not the arithmetic of it.
        self.path_note.setText(
            f"{chk.headline}  Cash today is {fmt_money(chk.actual_now)}. Back "
            f"in {chk.made_on.strftime('%B %Y')} that forecast guessed "
            f"{fmt_money(chk.predicted_now)} for today, and reckoned anything "
            f"between {fmt_money(chk.band_low)} and {fmt_money(chk.band_high)} "
            f"was on the cards. Over the {chk.days_elapsed} days since, your "
            f"real balance stayed inside the shaded range "
            f"{chk.inside_80 * 100:.0f}% of the time.")
        self.path_note.setStyleSheet(f"color: {col};")

    def _render_significance(self, old) -> None:
        """Answer "did anything change?" in the words the question was asked in.

        The statistics behind this card are the best work in the app and the
        worst thing to put in front of somebody's brother. A card headed *is
        this real or is it noise* that then says ``p = 0.031`` has answered a
        question nobody asked, in a unit nobody has an intuition for, and the
        one sentence that matters — *yes, and it's about $180 a month* — has to
        compete with it. The p-value, the confidence interval, the permutation
        count and Cohen's d all still exist, still to three decimal places,
        behind the For nerds button.
        """
        sig = significance_since(self.store, old)
        self._sig = sig
        if not sig.enough_data:
            self.card_spend.set_text("—", sub="not enough weeks yet")
            self.sig_head.setText("Not enough weeks to tell yet")
            self.sig_head.setStyleSheet(f"color: {theme.MUTED};")
            self.sig_body.setText(
                f"<div>Comparing two stretches of spending needs at least "
                f"{MIN_WEEKS} full weeks on each side of "
                f"{old.when.strftime('%d %b %Y')}, and there are "
                f"{sig.n_before} and {sig.n_after}. Keep importing — this fills "
                f"in on its own.</div>")
            return

        col = (theme.POSITIVE if sig.direction == "improved" else
               theme.NEGATIVE if sig.direction == "worsened" else theme.MUTED)
        # The monthly figure leads. Nobody budgets by the week, and $43/week is
        # a number you have to do arithmetic on before it means anything.
        self.card_spend.set_text(
            f"{fmt_money(sig.monthly_diff, signed=True)}/mo", color=col,
            sub=f"{fmt_money(sig.diff, signed=True)} a week on day-to-day "
                f"spending")

        if sig.significant and sig.direction == "improved":
            head = ("Yes — you really are spending less, and it is not just a "
                    "quiet few weeks.")
        elif sig.significant:
            head = "Yes — spending has genuinely gone up."
        else:
            head = "Too close to call from this many weeks."
        self.sig_head.setText(head)
        self.sig_head.setStyleSheet(f"color: {col};")

        span = (f"the {sig.n_before} weeks before "
                f"{old.when.strftime('%d %b %Y')} against the {sig.n_after} "
                f"weeks since")
        rows = [
            f"<div style='margin-bottom:7px'>{sig.note}</div>",
            f"<div style='color:{theme.MUTED};margin-bottom:3px'>"
            f"Day-to-day spending — bills and rent left out, because a rent "
            f"rise is not a change of habit. Comparing {span}:</div>",
            f"<div>· before, <b>{fmt_money(sig.mean_before)}</b> a week</div>",
            f"<div>· since, <b>{fmt_money(sig.mean_after)}</b> a week</div>",
            f"<div>· that is <b>{fmt_money(sig.diff, signed=True)}</b> a week, "
            f"or <b>{fmt_money(sig.monthly_diff, signed=True)}</b> a month"
            f"</div>",
        ]
        if sig.significant:
            rows.append(
                f"<div style='color:{theme.MUTED};margin-top:6px'>Doug is "
                f"confident the real figure lies somewhere between "
                f"{fmt_money(sig.ci_low, signed=True)} and "
                f"{fmt_money(sig.ci_high, signed=True)} a week — the direction "
                f"is settled, the exact size less so.</div>")
        if sig.significant and sig.ci_low < 0 < sig.ci_high:
            rows.append(
                f"<div style='color:{theme.AMBER}'>· that range still includes "
                f"zero, so trust the direction more than the amount</div>")

        shifts = category_shifts(self.store, old, top=4)
        if shifts:
            rows.append(f"<div style='margin-top:8px'><b>Where it came "
                        f"from</b> <span style='color:{theme.MUTED}'>"
                        f"(a description of the change, not a second "
                        f"test)</span></div>")
            for s in shifts:
                c = theme.POSITIVE if s.diff < 0 else theme.NEGATIVE
                rows.append(
                    f"<div>· {s.category}: "
                    f"<span style='color:{c}'>{fmt_money(s.diff, signed=True)}"
                    f"</span> a week</div>")
        self.sig_body.setText("".join(rows))

    # ---------------------------------------------------------------
    def _render_score_history(self) -> None:
        p = self.plot_score
        p.clear()
        charts.unlock_view(p)
        pts = [(c.when, c.score) for c in self._cps if c.score is not None]
        if len(pts) < 2:
            return
        x = charts.to_ts([d for d, _ in pts])
        y = np.array([s for _, s in pts], dtype=float)
        charts.line(p, x, y, theme.ACCENT, 2.2, "Score")
        p.plot(x, y, pen=None, symbol="o", symbolSize=6,
               symbolBrush=theme.qcolor(theme.ACCENT))
        charts.soft_floor(p, 7.0, "7.0 · living within means", theme.MUTED,
                          slot="starter")
        charts.lock_view(p, float(x[0]), float(x[-1]), [y],
                         include_y=[0.0, 10.0])

    def _render_table(self) -> None:
        self.table.setRowCount(len(self._cps))
        for r, cp in enumerate(reversed(self._cps)):
            when = cp.when.strftime("%d %b %Y")
            if cp.label:
                when += f"  · {cp.label}"
            score = "—" if cp.score is None else f"{cp.score:.1f} {cp.grade}"
            saved = ("—" if cp.savings_rate is None
                     else f"{cp.savings_rate * 100:.0f}%")
            vals = [when, score, fmt_money(cp.networth), fmt_money(cp.income),
                    fmt_money(cp.spending), saved, str(cp.n_transactions)]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
