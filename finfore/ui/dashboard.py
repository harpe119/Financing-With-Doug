"""Dashboard page — where the ledger becomes a picture."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QMenu,
                             QPushButton, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from ..alerts import annual_total, bill_changes, summarise
from ..calendar import upcoming_schedule
from ..data_quality import BALANCE_REMINDER_DAYS
from ..forecast import run_forecast
from ..history import (acknowledge, category_spending, headline_stats,
                       monthly_cashflow, networth_history, account_balance_history, unacknowledged)
from ..insights import emergency_fund_target, essential_monthly
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .widgets import (Card, ChartKey, CollapsibleCard, LegendToggles,
                      SeriesRegistry, StatCard, FlowLayout, hint, hsplit, info_button,
                      vsplit)

# How far ahead the "where is this heading" line looks. Ninety days is far
# enough to see a squeeze coming and near enough that the answer is not mostly
# the model's assumptions; the Forecast page is the place for longer views.
LOOKAHEAD_DAYS = 90
LOOKAHEAD_PATHS = 300

# Chart heights below which a plot stops being readable. The page scrolls
# rather than squeezing a chart under these.
# Below this page width the "kept from income" card drops to its own row.
STATS_WIDE_MIN = 880
HIST_PLOT_MIN = 320
BOTTOM_PLOT_MIN = 110

# Net worth history windows: label, days (None = the whole ledger).
_RANGES = (("3 months", 90.0, "the last 3 months"),
           ("1 year", 365.0, "the last year"),
           ("All", None, "the whole ledger"))


def _approx_money(v: float) -> str:
    """Round a projected figure to what it can honestly claim.

    A forecast of $20,208 implies precision the model does not have; $20,200
    does not. Negative values keep their sign in front of the dollar sign.
    """
    a = abs(v)
    step = 100 if a >= 2000 else 10 if a >= 200 else 1
    r = int(round(a / step) * step)
    return f"\u2212${r:,}" if v < 0 and r else f"${r:,}"


def _date_text(d) -> str:
    d = pd.Timestamp(d)
    return f"{d:%b} {d.day}"


def _span_text(months: float) -> str:
    if months >= 1.0:
        return f"{months:.1f} months"
    days = round(months * 30.4)
    return "less than a day" if days < 1 else f"{days} day{'s' if days != 1 else ''}"


class DashboardPage(QWidget):
    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._issues: list = []      # what the warning strip is currently about
        self._range_days: float | None = 365.0
        self._selected_account_ids = None
        self._history_mode = "totals"
        self._chart_h = None
        self._h = None               # the net worth frame the chart shows
        self._cats = None            # every category the chart could show
        self._cat_fit = 0            # …and how many of them fit right now
        self._expanded = False       # net worth chart has the whole page
        self._hist_data: dict = {}   # layer key -> plotted values
        self._hist_floors: dict = {} # layer key -> reference value

        # The whole page scrolls below the height where the charts stop being
        # readable, instead of squeezing three plots into a strip.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.body = QWidget()
        self.body.setObjectName("page")
        self.scroll.setWidget(self.body)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        # --- where this is heading ------------------------------------------
        # One plain sentence a person can read aloud, above the numbers. It
        # reuses the Forecast page's own engine, so the two never disagree, and
        # it is always labelled as an estimate.
        self.verdict_band = QFrame()
        self.verdict_band.setObjectName("verdictBand")
        vb = QVBoxLayout(self.verdict_band)
        vb.setContentsMargins(16, 12, 16, 12)
        vb.setSpacing(4)
        vrow = QHBoxLayout()
        vrow.setSpacing(12)
        self.verdict = QLabel("")
        self.verdict.setObjectName("verdict")
        self.verdict.setWordWrap(True)
        vrow.addWidget(self.verdict, 1)
        self.verdict_btn = QPushButton("See the forecast")
        self.verdict_btn.clicked.connect(
            lambda: self.main.show_page("Projection"))
        vrow.addWidget(self.verdict_btn, 0, Qt.AlignmentFlag.AlignTop)
        vb.addLayout(vrow)
        self.verdict_detail = hint("")
        vb.addWidget(self.verdict_detail)
        self.verdict_band.setVisible(False)
        lay.addWidget(self.verdict_band)

        # --- the figures, read as an equation -------------------------------
        # Net worth is what you own minus what you owe, and the cards are laid
        # out so that is visible: the hero, then the three numbers it is made
        # of. Kept-from-income is a different kind of figure and stands apart.
        self.card_nw = StatCard("Net worth", swatch=theme.ACCENT, hero=True)
        self.card_cash = StatCard("Cash", swatch=theme.BLUE)
        self.card_inv = StatCard("Investments", swatch=theme.PURPLE,
                                 dashed=True)
        self.card_debt = StatCard("You owe", swatch=theme.RED, dashed="dot")
        self.card_save = StatCard("Kept from income")
        self.stats_w = QWidget()
        self.stats_grid = QGridLayout(self.stats_w)
        g = self.stats_grid
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        g.addWidget(self.card_nw, 0, 0)
        col = 1
        for op, card in (("=", self.card_cash), ("+", self.card_inv),
                         ("−", self.card_debt)):
            sym = QLabel(op)
            sym.setObjectName("statOp")
            sym.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sym.setFixedWidth(14)
            g.addWidget(sym, 0, col)
            g.addWidget(card, 0, col + 1)
            g.setColumnStretch(col + 1, 3)
            col += 2
        g.setColumnStretch(0, 5)
        g.setColumnMinimumWidth(7, 2)       # a little extra air before the odd one out
        self._stats_narrow: bool | None = None
        self._place_kept(narrow=False)
        self.stats_w.setVisible(False)
        lay.addWidget(self.stats_w)

        # --- first run -------------------------------------------------------
        # The empty dashboard is the first thing a new user sees. It says what
        # Doug is for, names the three steps, and offers exactly one next
        # action; the rest of the page is hidden until there is something real
        # to show on it.
        self.welcome = self._build_welcome()
        lay.addWidget(self.welcome, 1)

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
        # ...and must then let the reader put it down. A warning that cannot be
        # dismissed stops being read: it becomes part of the furniture, and the
        # next one — about a different account, for a different reason — is
        # furniture too. Acknowledging is recorded against the numbers as they
        # stand, so it silences this discrepancy rather than this account.
        self.warn_row = QWidget()
        warning_layout = QVBoxLayout(self.warn_row)
        warning_layout.setContentsMargins(0, 0, 0, 0)
        warning_layout.setSpacing(8)
        wrl = QHBoxLayout()
        wrl.setContentsMargins(0, 0, 0, 0)
        wrl.setSpacing(8)
        self.warn = QLabel("")
        self.warn.setObjectName("ledgerWarning")
        self.warn.setWordWrap(True)
        self.warn.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.warn.hide()
        self.warn_summary = QLabel("")
        self.warn_summary.setObjectName("ledgerWarning")
        self.warn_summary.setWordWrap(True)
        self.warn_summary.setTextFormat(Qt.TextFormat.PlainText)
        wrl.addWidget(self.warn_summary, 1)
        self.warn_details = QPushButton("Details")
        self.warn_details.setCheckable(True)
        self.warn_details.setAccessibleName("Show history reconciliation details")
        self.warn_details.toggled.connect(self.warn.setVisible)
        wrl.addWidget(self.warn_details, 0, Qt.AlignmentFlag.AlignTop)
        self.warn_ack = QPushButton("Got it")
        self.warn_ack.setToolTip(
            "Stop showing this. It comes back if the numbers change, and "
            "Accounts → Check my balances always has the full detail.")
        self.warn_ack.clicked.connect(self._ack_warning)
        wrl.addWidget(self.warn_ack, 0, Qt.AlignmentFlag.AlignTop)
        warning_layout.addLayout(wrl)
        warning_layout.addWidget(self.warn)
        self.warn_row.setVisible(False)
        lay.addWidget(self.warn_row)

        # --- net worth history ----------------------------------------------
        hist_card = Card("Net worth history")
        self.hist_card = hist_card
        hist_card.body.removeWidget(hist_card.title)
        heading = QHBoxLayout()
        heading.setSpacing(12)
        heading.addWidget(hist_card.title)
        self.data_chip = hint("")
        self.data_chip.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(self.data_chip, 1)
        hist_card.body.addLayout(heading)
        self.history_controls = QWidget()
        selection = FlowLayout(self.history_controls, spacing=8)
        selection.addWidget(QLabel("Chart view"))
        self.history_view = QComboBox()
        self.history_view.addItem("Combined totals", "totals")
        self.history_view.addItem("Separate accounts", "accounts")
        self.history_view.setAccessibleName("Dashboard history chart view")
        self.history_view.setToolTip("Plot combined totals or a separate balance line for each selected account.")
        self.history_view.currentIndexChanged.connect(self._history_mode_changed)
        selection.addWidget(self.history_view)
        self.history_accounts_btn = QPushButton("All accounts")
        self.history_accounts_btn.setObjectName("small")
        self.history_accounts_menu = QMenu(self.history_accounts_btn)
        self.history_accounts_btn.setMenu(self.history_accounts_menu)
        self.history_account_actions = {}
        selection.addWidget(self.history_accounts_btn)
        self.range_group = QButtonGroup(self)
        self.range_group.setExclusive(True)
        self.range_btns: list[QPushButton] = []
        for label, days, phrase in _RANGES:
            b = QPushButton(label)
            b.setObjectName("range")
            b.setCheckable(True)
            b.setChecked(days == self._range_days)
            b.setToolTip(f"Show {phrase}")
            b.clicked.connect(lambda _c=False, d=days: self._set_range(d))
            self.range_group.addButton(b)
            self.range_btns.append(b)
            selection.addWidget(b)
        self.expand_btn = QPushButton("Expand")
        self.expand_btn.setObjectName("range")
        self.expand_btn.setCheckable(True)
        self.expand_btn.setToolTip(
            "Give the net worth chart the whole page, for a taller, clearer "
            "view of its ups and downs")
        self.expand_btn.clicked.connect(self._set_expanded)
        selection.addWidget(self.expand_btn)
        control_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        control_policy.setHeightForWidth(True)
        self.history_controls.setSizePolicy(control_policy)
        hist_card.body.addWidget(self.history_controls)
        self.plot_hist = charts.new_plot()
        self.plot_hist.setAccessibleName("Net worth history chart")
        self.hist_empty = QLabel("Choose accounts above to display their balance history.", self.plot_hist.viewport())
        self.hist_empty.setObjectName("hint")
        self.hist_empty.setWordWrap(True)
        self.hist_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hist_empty.setStyleSheet(f"background: {theme.BG};")
        self.hist_empty.hide()
        self.plot_hist.viewport().installEventFilter(self)
        self.hist_series = SeriesRegistry()
        self.hist_legend = LegendToggles(
            [("networth", "net worth", theme.ACCENT, "line"),
             ("cash", "cash", theme.BLUE, "line"),
             ("invest", "investments", theme.PURPLE, "dash"),
             ("debt", "what you owe (drawn below $0)", theme.RED, "dot"),
             ("zero", "$0", theme.REFERENCE, "line"),
             ("starter", "starter buffer", theme.AMBER, "dash"),
             ("emergency", "emergency fund", theme.ACCENT, "dash")],
            on_toggle=self._toggle_layer)
        self.plot_hist.setMinimumHeight(HIST_PLOT_MIN)
        hist_card.body.addWidget(self.plot_hist, 1)
        hist_card.body.addWidget(self.hist_legend)
        self.account_legend = QWidget()
        self.account_legend_box = QVBoxLayout(self.account_legend)
        self.account_legend_box.setContentsMargins(0, 0, 0, 0)
        self.account_legend.hide()
        hist_card.body.addWidget(self.account_legend)
        self.hist_note = hint("")
        hist_card.body.addWidget(self.hist_note)

        # Foldable, because the net-worth history is the chart people actually
        # sit and read and the other two are references you glance at. A
        # divider can already give history most of the page, but it cannot give
        # it *all* of the page — two cards will not shrink past their own
        # minimum height. Folding them to their title strips will, and the
        # chevron is the same control the Forecast page's scenario cards use.
        flow_card = CollapsibleCard("Monthly cash flow", expanded=False)
        self.plot_flow = charts.new_plot()
        self.plot_flow.setAccessibleName("Monthly cash flow chart")
        self.plot_flow.setMinimumHeight(BOTTOM_PLOT_MIN)
        flow_card.body.addWidget(self.plot_flow, 1)
        flow_card.body.addWidget(ChartKey([("Income", theme.ACCENT, "bar"),
                                           ("Spending", theme.RED, "bar")]))
        self.flow_summary = ""
        cat_card = CollapsibleCard("Spending by category · last 90 days",
                                   expanded=False)
        self.plot_cat = charts.new_plot(date_axis=False, money_left=False,
                                        money_bottom=True)
        self.plot_cat.setAccessibleName("Spending by category chart")
        self.plot_cat.setMinimumHeight(BOTTOM_PLOT_MIN)
        self.plot_cat.installEventFilter(self)
        cat_card.body.addWidget(self.plot_cat, 1)
        self.cat_summary = ""
        self.flow_card, self.cat_card = flow_card, cat_card
        for c in (flow_card, cat_card):
            c.header.setToolTip("Fold this away and give the net worth history "
                                "the room")
            c.header.clicked.connect(self._sync_split)

        # A plot's own size hint is a 640x480 default. Left to count, three of
        # them make the page claim well over a thousand pixels; ignoring it
        # leaves each plot with exactly the minimum height set above.
        for plot in (self.plot_hist, self.plot_flow, self.plot_cat):
            plot.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Ignored)

        # Draggable dividers rather than fixed 3:2 and 1:1 splits. Which chart
        # deserves the room depends on what is being looked at right now, and
        # that is not a question this file can answer once for everybody.
        bottom = hsplit(flow_card, cat_card, sizes=[500, 500])
        for c in (hist_card, flow_card, cat_card):
            c.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Expanding)
        self.split = vsplit(hist_card, bottom, sizes=[600, 48])
        self.split.setVisible(False)
        # History leads; balances and the forecast are supporting information.
        # The two reference charts remain available through their disclosures.
        lay.removeWidget(self.stats_w)
        lay.removeWidget(self.verdict_band)
        lay.addWidget(self.split, 2)
        lay.addWidget(self.stats_w)
        lay.addWidget(self.verdict_band)
        self.scroll.viewport().installEventFilter(self)
        self.hist_card.installEventFilter(self)

    # ---------------------------------------------------------------
    def _place_kept(self, narrow: bool) -> None:
        """Seat the savings-rate card beside the equation, or under it.

        Five cards and three operators need about 860px. Narrower than that the
        row would run off the edge of a page that does not scroll sideways, so
        the card that is not part of the equation drops to a row of its own.
        """
        if narrow == self._stats_narrow:
            return
        self._stats_narrow = narrow
        g = self.stats_grid
        g.removeWidget(self.card_save)
        if narrow:
            g.addWidget(self.card_save, 1, 0, 1, 7)
            g.setColumnStretch(8, 0)
        else:
            g.addWidget(self.card_save, 0, 8)
            g.setColumnStretch(8, 4)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place_kept(self.width() < STATS_WIDE_MIN)
        self._fit_history_height()

    def _fit_history_height(self) -> None:
        """Keep history readable instead of letting secondary content crush it."""
        width = max(0, self.scroll.viewport().width() - 36)
        height = max(HIST_PLOT_MIN, min(460, round(width * .38)))
        if self.plot_hist.minimumHeight() != height:
            self.plot_hist.setMinimumHeight(height)
        # QSplitter asks for minimumSizeHint at the card's *minimum width*,
        # where the toolbar/key wrap into several rows. Use their height at
        # the actual page width; otherwise the chart grows past a short screen.
        chrome = max(0, self.hist_card.heightForWidth(width))
        required = height + chrome
        if self.hist_card.minimumHeight() != required:
            self.hist_card.setMinimumHeight(required)

    # ---------------------------------------------------------------
    def _build_welcome(self) -> QWidget:
        """The first-run panel: what Doug does, three steps, one next action."""
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.addStretch(1)
        mid = QHBoxLayout()
        mid.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(640)
        card.setMinimumWidth(420)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 24, 28, 22)
        cl.setSpacing(12)

        title = QLabel("Let’s see where your money is headed")
        title.setObjectName("welcomeTitle")
        title.setWordWrap(True)
        cl.addWidget(title)
        intro = QLabel(
            "Doug reads the statements you download from your bank, shows "
            "where your money stands today, and projects where your habits "
            "are taking it.")
        intro.setWordWrap(True)
        cl.addWidget(intro)
        cl.addWidget(hint("Everything stays on this computer. Doug never "
                          "connects to your bank or moves money."))

        sep = QFrame()
        sep.setObjectName("hsep")
        sep.setFrameShape(QFrame.Shape.HLine)
        cl.addWidget(sep)

        self.steps: list[dict] = []
        spec = (
            ("Add your accounts",
             "Enter each account and what is in it today.",
             "Add an account", lambda: self.main.show_page("Accounts")),
            ("Import a statement",
             "Download a CSV file from your bank’s website, then choose it here.",
             "Import a CSV…", lambda: self.main.open_import()),
            ("See where you’re headed",
             "Your dashboard and forecast fill in on their own.",
             None, None),
        )
        for n, (head, body, btn_text, action) in enumerate(spec, start=1):
            r = QHBoxLayout()
            r.setSpacing(12)
            mark = QLabel(f"{n}.")
            mark.setObjectName("stepTitle")
            mark.setFixedWidth(26)
            mark.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            r.addWidget(mark)
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(head)
            t.setObjectName("stepTitle")
            col.addWidget(t)
            d = hint(body)
            col.addWidget(d)
            r.addLayout(col, 1)
            btn = None
            if btn_text:
                btn = QPushButton(btn_text)
                btn.clicked.connect(action)
                r.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
            cl.addLayout(r)
            self.steps.append({"mark": mark, "title": t, "desc": d, "btn": btn,
                               "n": n})

        links = QHBoxLayout()
        links.setSpacing(4)
        self.demo_btn = QPushButton("Explore with a demo first")
        self.demo_btn.setObjectName("link")
        self.demo_btn.setToolTip("Opens fictional data in a separate file; "
                                 "your own data is untouched")
        self.demo_btn.clicked.connect(
            lambda: self.main._load_demo("balanced", confirm=False))
        links.addWidget(self.demo_btn)
        self.guide_btn = QPushButton("Getting started guide")
        self.guide_btn.setObjectName("link")
        self.guide_btn.setToolTip("Open the Getting started guide (F1)")
        self.guide_btn.clicked.connect(lambda: self.main.show_guide())
        links.addWidget(self.guide_btn)
        links.addStretch(1)
        cl.addLayout(links)

        mid.addWidget(card, 10)
        mid.addStretch(1)
        outer.addLayout(mid)
        outer.addStretch(2)
        return wrap

    def _sync_welcome(self, has_accounts: bool) -> None:
        """Mark the step the person is on: one primary button, the rest quiet."""
        current = 2 if has_accounts else 1
        for s in self.steps:
            n, btn = s["n"], s["btn"]
            done = n < current
            s["title"].setText(
                s["title"].text().removesuffix(" — done") + (" — done" if done else ""))
            s["mark"].setObjectName("stepDone" if done else "stepTitle")
            s["mark"].style().unpolish(s["mark"])
            s["mark"].style().polish(s["mark"])
            if btn is not None:
                btn.setVisible(n == current)
                btn.setObjectName("primary" if n == current else "")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        self.demo_btn.setEnabled(True)

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
            strip = max(self.flow_card.minimumSizeHint().height(),
                        self.flow_card.header.sizeHint().height() + 24)
            self.split.setSizes([max(1, total - strip), strip])
        else:
            self.split.setSizes([int(total * 0.58), int(total * 0.42)])

    def _selected_chart_accounts(self):
        return [a for a in self.store.accounts()
                if self._selected_account_ids is None or a.id in self._selected_account_ids]

    def reset_for_store(self):
        self._selected_account_ids = None
        self._history_mode = "totals"
        self._chart_h = self._h = None
        self._hist_data = {}
        self._hist_floors = {}

    def _load_chart_selection(self):
        settings = self.store.get_setting("dashboard_history_view", {})
        if not isinstance(settings, dict):
            settings = {}
        self._history_mode = settings.get("mode", "totals")
        if self._history_mode not in ("totals", "accounts"):
            self._history_mode = "totals"
        ids = settings.get("account_ids")
        self._selected_account_ids = {i for i in ids if isinstance(i, int)} if isinstance(ids, list) else None
        self.history_view.blockSignals(True)
        self.history_view.setCurrentIndex(self.history_view.findData(self._history_mode))
        self.history_view.blockSignals(False)
        menu = self.history_accounts_menu
        menu.clear()
        menu.addAction("All accounts", lambda: self._set_chart_accounts(None))
        menu.addAction("Clear selection", lambda: self._set_chart_accounts(set()))
        menu.addSeparator()
        self.history_account_actions = {}
        for account in self.store.accounts():
            action = menu.addAction(account.name.replace("&", "&&"))
            action.setCheckable(True)
            action.setChecked(self._selected_account_ids is None or account.id in self._selected_account_ids)
            action.toggled.connect(lambda on, aid=account.id: self._toggle_chart_account(aid, on))
            self.history_account_actions[account.id] = action
        self._update_account_button()

    def _update_account_button(self):
        selected = self._selected_chart_accounts()
        total = len(self.store.accounts())
        self.history_accounts_btn.setText("All accounts" if self._selected_account_ids is None
                                         else f"{len(selected)} of {total} accounts")
        names = ", ".join(a.name for a in selected) or "No accounts selected"
        self.history_accounts_btn.setToolTip(f"Select the accounts shown in this chart. {names}")
        self.hist_card.title.setText("ACCOUNT BALANCE HISTORY" if self._history_mode == "accounts" else "NET WORTH HISTORY")
        self.plot_hist.setAccessibleName("Account balance history chart" if self._history_mode == "accounts"
                                        else "Net worth history chart")

    def _history_mode_changed(self):
        self._history_mode = self.history_view.currentData()
        self._save_chart_selection()

    def _toggle_chart_account(self, account_id, on):
        ids = {a.id for a in self.store.accounts()} if self._selected_account_ids is None else set(self._selected_account_ids)
        if on:
            ids.add(account_id)
        else:
            ids.discard(account_id)
        self._set_chart_accounts(ids)

    def _set_chart_accounts(self, ids):
        self._selected_account_ids = ids
        for aid, action in self.history_account_actions.items():
            action.blockSignals(True)
            action.setChecked(ids is None or aid in ids)
            action.blockSignals(False)
        self._save_chart_selection()

    def _save_chart_selection(self):
        # A chart filter is a view preference, not a financial edit to undo.
        with self.store.undo_suspended():
            self.store.set_setting("dashboard_history_view", {
                "mode": self._history_mode,
                "account_ids": sorted(self._selected_account_ids) if self._selected_account_ids is not None else None})
        self._update_account_button()
        self._render_history(self.store.transactions(), bool(self.store.accounts()))

    def _render_history(self, tx, has_data):
        h = networth_history(self.store, self._selected_account_ids) if self._selected_account_ids is not None else self._h
        self._chart_h = h
        accounts = self._selected_chart_accounts()
        if self._selected_account_ids is not None:
            tx = tx[tx.account_id.isin({a.id for a in accounts})]
        p = self.plot_hist
        self.hist_empty.setGeometry(p.viewport().rect())
        self.hist_empty.setVisible(not accounts)
        if not accounts:
            self.hist_empty.raise_()
        p.clear()
        charts.unlock_view(p)
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        self.hist_series.reset()
        self._hist_data = {}
        self._hist_floors = {}
        charts.detach_readout(p)
        p.getViewBox()._window_ctx = None
        if self._history_mode == "accounts":
            self._render_account_lines(accounts, tx)
            self._update_hist_note()
            self._apply_expanded()
            return
        self.account_legend.hide()
        self.hist_legend.setVisible(bool(accounts))
        last_tx = tx["date"].max() if not tx.empty else pd.NaT
        self._render_data_chip(last_tx)
        if len(h) > 1 and has_data and accounts:
            reg = self.hist_series
            reg.reset()
            ef = emergency_fund_target(self.store, 3)
            reg.add("zero", charts.zero_floor(p))
            reg.add("starter", charts.soft_floor(
                p, 1000.0, "$1.0k · starter buffer", theme.AMBER,
                slot="starter"))
            if ef > 1050.0:
                reg.add("emergency", charts.soft_floor(
                    p, ef, f"{charts._short(ef)} · 3-month emergency fund",
                    theme.ACCENT, slot="emergency"))
            x = charts.to_ts(h.index)
            # Hue alone is not enough to tell three thin lines apart (blue and
            # violet collapse together for some colour-blind readers), so the
            # series differ in stroke as well: solid, dashed, dotted.
            reg.add("networth", charts.line(p, x, h["networth"].values,
                                            theme.ACCENT, 2.6, "Net worth"))
            reg.add("cash", charts.line(p, x, h["cash"].values, theme.BLUE,
                                        1.7, "Cash"))
            series = [("Net worth", theme.ACCENT, h["networth"].values),
                      ("Cash", theme.BLUE, h["cash"].values)]
            if np.any(h["investments"].values > 0):
                reg.add("invest", charts.line(p, x, h["investments"].values,
                                              theme.PURPLE, 1.7, "Investments",
                                              dashed=True))
                series.append(("Investments", theme.PURPLE,
                               h["investments"].values))
            if np.any(h["debt"].values > 0):
                reg.add("debt", charts.line(p, x, -h["debt"].values, theme.RED,
                                            2.0, "What you owe", dashed="dot"))
                series.append(("You owe", theme.RED, h["debt"].values))
            if pd.notna(last_tx) and (pd.Timestamp.today().normalize()
                                      - last_tx).days > 7:
                reg.add("dataend", charts.data_end_line(p, last_tx.date()))
            reg.apply()
            # Opens on the chosen window (a year by default) — where the detail
            # people came for lives — and zooms out to the whole ledger.
            self._hist_data = {"networth": h["networth"].values,
                               "cash": h["cash"].values,
                               "invest": h["investments"].values,
                               "debt": -h["debt"].values}
            self._hist_floors = {"zero": 0.0, "starter": 1000.0,
                                 "emergency": ef}
            y_series, include_y = self._hist_frame()
            charts.lock_view(
                p, float(x[0]), float(x[-1]), y_series, include_y=include_y,
                initial_days=self._range_days, x_data=x)
            charts.attach_readout(p, x, series)
        self._update_hist_note()
        self._apply_expanded()


    def _render_account_lines(self, accounts, tx):
        p = self.plot_hist
        self.hist_legend.hide()
        while self.account_legend_box.count():
            item = self.account_legend_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        frame = account_balance_history(self.store, {a.id for a in accounts})
        self._account_history = frame
        self._chart_h = None
        self._render_data_chip(tx.date.max() if not tx.empty else pd.NaT)
        if not accounts or frame.empty:
            self.account_legend.hide()
            return
        self.account_legend.show()
        x = charts.to_ts(frame.index)
        entries, series = [], []
        colors = {"checking": theme.BLUE, "savings": theme.ACCENT,
                  "investment": theme.PURPLE, "credit": theme.RED, "loan": theme.RED}
        for account in accounts:
            color = colors.get(account.type.value, theme.BLUE)
            dashed = (False, True, "dot")[account.id % 3]
            if account.type.value in ("credit", "loan"):
                dashed = "dot"
            values = frame[account.id].to_numpy(dtype=float)
            key = f"account:{account.id}"
            curve = charts.line(p, x, values, color, 2.1, dashed=dashed)
            if len(x) == 1:
                curve.setSymbol("o")
                curve.setSymbolBrush(color)
            self.hist_series.add(key, curve)
            self._hist_data[key] = values
            entries.append((account.name, color, "dot" if dashed == "dot" else "dash" if dashed else "line"))
            series.append((account.name, color, values))
        legend = ChartKey(entries)
        for label in legend.findChildren(QLabel):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.account_legend_box.addWidget(legend)
        self.hist_series.add("zero", charts.zero_floor(p))
        self._hist_floors = {"zero": 0.0}
        charts.lock_view(p, float(x[0]), float(x[-1]), list(self._hist_data.values()),
                         include_y=[0.0], initial_days=self._range_days, x_data=x)
        charts.attach_readout(p, x, series)

    def _hist_frame(self):
        """The series and reference lines the vertical range should fit.

        Only layers that are switched on count: hide the debt line or the
        floors and the axis stretches to the net worth, cash and investment
        lines that are left.
        """
        vis = self.hist_series.visible
        y = [v for k, v in self._hist_data.items() if vis.get(k, True)]
        inc = [v for k, v in self._hist_floors.items() if vis.get(k, True)]
        if not y:
            y = list(self._hist_data.values())
        return y, inc

    def _toggle_layer(self, key: str, on: bool) -> None:
        self.hist_series.set_visible(key, on)
        if self._hist_data:
            y, inc = self._hist_frame()
            charts.reframe(self.plot_hist, y, inc, self._range_days)

    def _set_expanded(self, on: bool) -> None:
        """Give the net worth chart the whole page, or put the page back."""
        self._expanded = bool(on)
        self.expand_btn.setChecked(self._expanded)
        self.expand_btn.setText("Restore" if self._expanded else "Expand")
        if self._expanded:
            self._apply_expanded()
        else:
            self.refresh()

    def _apply_expanded(self) -> None:
        """Hide everything but the history while it is expanded.

        The figures, the outlook line and the other two charts are one click
        away again; what the person asked for is height, and everything else on
        the page is what was taking it.
        """
        self.expand_btn.setVisible(not self.split.isHidden() or self._expanded)
        if not self._expanded:
            self.split.widget(1).setVisible(True)
            return
        for w in (self.verdict_band, self.stats_w, self.alert_row,
                  self.warn_row, self.welcome):
            w.setVisible(False)
        self.split.widget(1).setVisible(False)

    def _set_range(self, days: float | None) -> None:
        self._range_days = days
        charts.show_window(self.plot_hist, days)
        self._update_hist_note()

    def _open_category(self, name: str) -> None:
        self.main.show_page("Transactions")
        page = self.main.current_page()
        fn = getattr(page, "show_category", None)
        if fn is not None:
            fn(name, 90)

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
            "pattern under Bills &amp; payday → Recurring patterns stops it being checked.</p>")
        return "What changed without being asked", "".join(rows)

    def _ack_warning(self) -> None:
        try:
            acknowledge(self.store, self._issues)
        except Exception:
            return
        self.warn_row.setVisible(False)
        self.main.status(
            "Dismissed. It will reappear if the figures change — the full "
            "detail is always on Accounts → Check my balances.")

    def _render_warnings(self) -> None:
        """Explain a history the arithmetic has forced somewhere impossible."""
        try:
            issues = unacknowledged(self.store)
        except Exception:
            issues = []
        self._issues = issues
        if not issues:
            self.warn_row.setVisible(False)
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
        rows.append(
            "<div style='margin-top:5px'><b>Got it</b> hides this until the "
            "numbers change. The full detail stays on Accounts → Check my "
            "balances.</div>")
        self.warn.setText("".join(rows))
        count = len(issues)
        self.warn_summary.setText(
            f"History needs review: {count} account{'s do' if count != 1 else ' does'} "
            "not reconcile with the imported transactions.")
        self.warn_row.setVisible(True)

    # ---------------------------------------------------------------
    def _render_verdict(self, tx) -> None:
        """One sentence on where cash is heading, with the range behind it.

        It is the Forecast page's engine on a short horizon, so the two pages
        cannot disagree, and it is worded as an estimate every time. If the
        projection cannot be produced the band simply is not shown: a missing
        sentence is better than a wrong one.
        """
        self.verdict_band.setVisible(False)
        if tx.empty:
            return
        try:
            ess = essential_monthly(self.store)
            res = run_forecast(self.store, LOOKAHEAD_DAYS, LOOKAHEAD_PATHS,
                               emergency_target=ess * 3, essential_monthly=ess)
            sf = res.shortfall
            lo = float(res.cash_pct[10][-1])
            hi = float(res.cash_pct[90][-1])
        except Exception:
            return
        month = f"{sf.low_date:%B}" if sf and sf.low_date else None
        level = sf.level if sf else "ok"
        caution = level in ("crunch", "watch", "high")
        level_ok = level in ("ok", "thin", "brief")
        between = f"between {_approx_money(lo)} and {_approx_money(hi)}"
        rng = (f"In {LOOKAHEAD_DAYS} days your cash will most likely be {between}."
               if level_ok else f"By then it will most likely be {between}.")
        if level == "high":
            head = (f"There’s a high risk your cash runs short in the next "
                    f"{LOOKAHEAD_DAYS} days.")
        elif level == "watch":
            head = "Worth watching: some outcomes run short."
        elif level == "crunch":
            head = (f"A tight patch is likely around {month}, then it clears."
                    if month else "A tight patch is likely, then it clears.")
        elif level == "brief":
            head = "A short dip is possible, but nothing serious."
        elif level == "thin":
            head = "Your cash stays above zero, but it gets thin at times."
        else:
            head = "You’re on track."
        self.verdict.setText(f"{head} {rng}")

        bits = []
        if res.runway_months is not None:
            bits.append(f"Cash covers about {_span_text(res.runway_months)} "
                        f"of essentials.")
        bits.extend(self._next_events())
        caveat = "An estimate from your own history, not a promise."
        dates = tx["date"].dropna()
        if len(dates):
            span = (dates.max() - dates.min()).days + 1
            if span < 90:
                caveat += (f" Only {span} day{'s' if span != 1 else ''} of "
                           f"history so far, so treat it as rough.")
        self.verdict_detail.setText(
            ("  ·  ".join(bits) + "\n" if bits else "") + caveat)
        self.verdict_btn.setText("See what could change this" if caution
                                 else "See the forecast")
        self.verdict_band.setProperty("caution", "true" if caution else "false")
        self.verdict_band.style().unpolish(self.verdict_band)
        self.verdict_band.style().polish(self.verdict_band)
        self.verdict_band.setVisible(True)

    def _next_events(self) -> list[str]:
        """The next bill and the next payday, from the same schedule as the
        Bills & payday calendar."""
        try:
            entries = upcoming_schedule(
                self.store, date.today() + timedelta(days=35)).entries
        except Exception:
            return []
        out = []
        bills = [e for e in entries if e.amount < 0]
        if bills:
            e = bills[0]
            out.append(f"Next bill: {e.label[:28]}, {fmt_money(-e.amount)} "
                       f"on {_date_text(e.when)}.")
        income = [e for e in entries if e.amount > 0]
        if income:
            big = max(e.amount for e in income)
            e = next(x for x in income if x.amount >= big * 0.5)
            out.append(f"Next payday: {fmt_money(e.amount)} on "
                       f"{_date_text(e.when)}.")
        return out

    def _update_hist_note(self) -> None:
        """A sentence for the net worth chart, for anyone who cannot read it."""
        if not self._selected_chart_accounts():
            self.hist_note.setText("Select one or more accounts to show their balance history.")
            self.plot_hist.setAccessibleDescription(self.hist_note.text())
            return
        if self._history_mode == "accounts":
            text = ("Each line is one selected account. Investments and loans use dated balance snapshots; "
                    "cash accounts use the bank ledger. Money owed is below $0. Overview totals include all accounts.")
            self.hist_note.setText(text)
            self.plot_hist.setAccessibleDescription(text)
            return
        h = self._chart_h
        if h is None or len(h) < 2:
            self.hist_note.setText("")
            self.plot_hist.setAccessibleDescription("")
            return
        phrase = next(p for _l, d, p in _RANGES if d == self._range_days)
        nw = h["networth"]
        end = float(nw.iloc[-1])
        if self._range_days:
            start_ts = nw.index[-1] - pd.Timedelta(days=self._range_days)
            start = float(nw.iloc[0] if start_ts <= nw.index[0]
                          else nw.asof(start_ts))
        else:
            start = float(nw.iloc[0])
        d = end - start
        word = "up" if d >= 0 else "down"
        label = "net worth" if self._selected_account_ids is None else "selected accounts’ net worth"
        text = (f"Over {phrase}, {label} went {word} {fmt_money(abs(d))}: "
                f"from {fmt_money(start)} to {fmt_money(end)}.")
        self.hist_note.setText(text)
        self.plot_hist.setAccessibleDescription(text)

    def refresh(self) -> None:
        store = self.store
        st = headline_stats(store)
        tx = store.transactions()
        has_accounts = bool(store.accounts())
        has_data = has_accounts or not tx.empty
        h = networth_history(store)
        self._h = h

        # Net worth: what you own minus what you owe, and how it has moved.
        sub = "What you own, minus what you owe."
        if len(h) > 31 and has_data:
            nwh = h["networth"]
            then = float(nwh.asof(nwh.index[-1] - pd.Timedelta(days=30)))
            d = float(nwh.iloc[-1]) - then
            if abs(d) >= 1:
                sub += (f" {'Up' if d > 0 else 'Down'} {fmt_money(abs(d))} "
                        f"in 30 days.")
        self.card_nw.set_money(st["networth"] if has_data else None,
                               colored=has_data, sub=sub)
        self.card_cash.set_money(st["cash"] if has_data else None,
                                 sub="Checking and savings")
        self.card_inv.set_money(st["investments"] if has_data else None,
                                sub="Investment accounts")
        if has_data and st["debt"] > 0:
            self.card_debt.set_text(fmt_money(st["debt"]), color=theme.NEGATIVE,
                                    sub="Cards and loans")
        else:
            self.card_debt.set_money(st["debt"] if has_data else None,
                                     sub="Cards and loans")
        rate = st["savings_rate"]
        if rate is not None:
            note = ("Last 3 months · " +
                    (f"you spend about {fmt_money(st['burn'])} a month"
                     if rate >= 0 and st["burn"] else
                     "more went out than came in"))
            self.card_save.set_text(
                f"{rate * 100:.0f}%",
                color=theme.POSITIVE if rate >= 0 else theme.NEGATIVE,
                sub=note)
        else:
            self.card_save.set_text("—", sub="Needs a few months of history")

        # What the page shows depends on how far along the person is.
        moves = len(h) > 1 and float(h["networth"].nunique()) > 1
        show_charts = (not tx.empty) or (moves and has_data)
        self.stats_w.setVisible(has_data)
        self.welcome.setVisible(tx.empty)
        self.welcome.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred if show_charts
            else QSizePolicy.Policy.Expanding)
        self._sync_welcome(has_accounts)
        self.split.setVisible(show_charts)
        self._render_verdict(tx)
        self._render_alerts()
        self._render_warnings()

        self._load_chart_selection()
        self._render_history(tx, has_data)

        # monthly cash flow — zero is the break-even line between the bars
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
                             x_data=mx, zoom_y=False)
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

        # categories
        self._cats = category_spending(tx)
        self._cat_fit = 0
        self._draw_categories()

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
        silently dropped; the Spending tab has the whole breakdown.
        """
        return int(min(n, max(3, (self.plot_cat.height() - 36) // 17)))

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.scroll.viewport() and ev.type() == QEvent.Type.Resize:
            self._fit_history_height()
        if obj is getattr(self, "hist_card", None) and ev.type() == QEvent.Type.LayoutRequest:
            self._fit_history_height()
        if obj is self.plot_hist.viewport() and ev.type() == QEvent.Type.Resize:
            self.hist_empty.setGeometry(self.plot_hist.viewport().rect())
        if (obj is getattr(self, "plot_cat", None) and ev.type() == QEvent.Type.Resize
                and self._cats is not None and len(self._cats)):
            fit = self._bars_that_fit(len(self._cats))
            if fit != self._cat_fit:
                self._draw_categories()
        return super().eventFilter(obj, ev)

    def _render_data_chip(self, last_tx) -> None:
        """Say how fresh the data is; amber once it is stale enough to matter."""
        if pd.isna(last_tx):
            self.data_chip.setText("")
            return
        age = (pd.Timestamp.today().normalize() - last_tx).days
        text = f"Data through {_date_text(last_tx)}"
        stale = age > BALANCE_REMINDER_DAYS
        if stale:
            text += f" · {age} days ago, so recent months may be missing"
        self.data_chip.setText(text)
        self.data_chip.setStyleSheet(
            f"color: {theme.AMBER};" if stale else "")
