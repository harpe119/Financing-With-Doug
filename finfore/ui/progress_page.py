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

from ..progress import (capture, category_shifts, check_path, score_move,
                        significance_since)
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .widgets import Card, StatCard, hint, hsplit, vsplit

_STATUS_COLOR = {
    "ahead": theme.ACCENT, "on-path": theme.BLUE,
    "behind": theme.AMBER, "broken": theme.PURPLE,
}


class ProgressPage(QWidget):
    COLS = ["Taken", "Score", "Net worth", "Income /mo", "Spending /mo",
            "Saved", "Txns"]

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._cps = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Compare against"))
        self.pick = QComboBox()
        self.pick.setMinimumWidth(220)
        self.pick.currentIndexChanged.connect(self._render)
        bar.addWidget(self.pick)
        snap = QPushButton("Take a checkpoint now")
        snap.setObjectName("small")
        snap.setToolTip("Normally taken automatically on each import — this "
                        "records one on demand")
        snap.clicked.connect(self._manual_capture)
        bar.addWidget(snap)
        bar.addStretch(1)
        root.addLayout(bar)

        self.empty = hint(
            "Nothing to compare yet. A checkpoint is recorded automatically "
            "each time you import a CSV: the score, the balance sheet, and the "
            "projection being made at that moment. Once there are two, this "
            "page shows whether the projection held and whether anything "
            "actually improved.")
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

        top = hsplit(path_card, sig_card, sizes=[620, 460])
        bottom = hsplit(hist_card, table_card, sizes=[440, 640])
        for c in (path_card, sig_card, hist_card, table_card):
            c.setMinimumHeight(110)
            c.setMinimumWidth(200)
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        root.addWidget(vsplit(top, bottom, sizes=[400, 300]), 1)

    # ---------------------------------------------------------------
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

    def _manual_capture(self) -> None:
        try:
            capture(self.store, label="manual")
        except Exception as e:
            QMessageBox.warning(self, "Couldn't take a checkpoint", str(e))
            return
        self.refresh()
        self.main.status("Checkpoint recorded.")

    def _selected(self):
        cid = self.pick.currentData()
        return next((c for c in self._cps if c.id == cid), None)

    # ---------------------------------------------------------------
    def _render(self) -> None:
        old = self._selected()
        if old is None or len(self._cps) < 2:
            return
        now = self._cps[-1]

        mv = score_move(old, now)
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
            sub=f"{fmt_money(chk.delta, signed=True)} vs the P50 · "
                f"{chk.inside_80 * 100:.0f}% of days inside the 80% band")
        self.path_note.setText(
            f"{chk.headline}  Cash today is {fmt_money(chk.actual_now)}; that "
            f"projection put today's median at {fmt_money(chk.predicted_now)} "
            f"with an 80% range of {fmt_money(chk.band_low)}–"
            f"{fmt_money(chk.band_high)}. Over {chk.days_elapsed} days, reality "
            f"stayed inside the 80% band {chk.inside_80 * 100:.0f}% of the time "
            f"and the 50% band {chk.inside_50 * 100:.0f}%. A well-calibrated "
            f"forecast should land near 80% and 50% — much higher means it was "
            f"too timid, much lower means it was overconfident.")
        self.path_note.setStyleSheet(f"color: {col};")

    def _render_significance(self, old) -> None:
        sig = significance_since(self.store, old)
        if not sig.enough_data:
            self.card_spend.set_text("—", sub="not enough weeks yet")
            self.sig_head.setText("Not enough weeks to test yet")
            self.sig_head.setStyleSheet(f"color: {theme.MUTED};")
            self.sig_body.setText(sig.note)
            return

        col = (theme.POSITIVE if sig.direction == "improved" else
               theme.NEGATIVE if sig.direction == "worsened" else theme.MUTED)
        self.card_spend.set_text(
            f"{fmt_money(sig.diff, signed=True)}/wk", color=col,
            sub=f"{fmt_money(sig.monthly_diff, signed=True)}/mo · "
                f"p = {sig.p_value:.3f}")

        if sig.significant and sig.direction == "improved":
            head = ("Yes — you really are spending less, and it is not just a "
                    "quiet few weeks.")
        elif sig.significant:
            head = "Yes — spending has genuinely gone up."
        else:
            head = "Too close to call from this many weeks."
        self.sig_head.setText(head)
        self.sig_head.setStyleSheet(f"color: {col};")

        ci = (f"{fmt_money(sig.ci_low, signed=True)} to "
              f"{fmt_money(sig.ci_high, signed=True)}")
        rows = [
            f"<div style='margin-bottom:6px'>{sig.note}</div>",
            f"<div style='color:{theme.MUTED};margin-bottom:3px'>"
            f"Weekly discretionary spend, "
            f"{sig.n_before} weeks before vs {sig.n_after} after:</div>",
            f"<div>· before <b>{fmt_money(sig.mean_before)}</b>/week &nbsp; "
            f"after <b>{fmt_money(sig.mean_after)}</b>/week</div>",
            f"<div>· difference <b>{fmt_money(sig.diff, signed=True)}</b>/week, "
            f"95% confidence {ci}</div>",
            f"<div>· p = {sig.p_value:.3f} "
            f"({'below' if sig.significant else 'above'} the 0.05 threshold)"
            f"</div>",
        ]
        if sig.significant and sig.ci_low < 0 < sig.ci_high:
            rows.append(
                f"<div style='color:{theme.AMBER}'>· the interval still spans "
                f"zero, so treat the size with caution even though the "
                f"direction is clear</div>")
        rows.append(
            f"<div style='color:{theme.MUTED};margin-top:8px'>Tested by "
            f"reshuffling which weeks belong to which period {20_000:,} times "
            f"and asking how often chance alone produces a gap this large. No "
            f"assumption is made that spending is bell-shaped, because it "
            f"isn't. Recurring bills are excluded — a rent rise is not a change "
            f"of habit.</div>")

        shifts = category_shifts(self.store, old, top=4)
        if shifts:
            rows.append(f"<div style='margin-top:8px'><b>Where it came "
                        f"from</b> <span style='color:{theme.MUTED}'>"
                        f"(descriptive, not tested)</span></div>")
            for s in shifts:
                c = theme.POSITIVE if s.diff < 0 else theme.NEGATIVE
                rows.append(
                    f"<div>· {s.category}: "
                    f"<span style='color:{c}'>{fmt_money(s.diff, signed=True)}"
                    f"</span>/week</div>")
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
