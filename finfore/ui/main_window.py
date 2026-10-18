"""Main window: sidebar navigation over stacked pages."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSignalBlocker, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QListWidget, QScrollArea,
                             QMainWindow, QMessageBox, QPushButton, QSizePolicy,
                             QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ..history import headline_stats
from ..store import Store
from ..util import fmt_money
from . import charts, prefs, theme
from .accounts import AccountsPage
from .dashboard import DashboardPage
from .forecast_page import ForecastPage
from .goals_page import GoalsPage
from .insights_page import InsightsPage
from .first_use import NO_ACCOUNTS, READY, setup_stage
from .widgets import tidy_scroll_focus
from .import_dialog import ImportDialog
from .input_behavior import install_entry_wheel_guard
from .progress_page import ProgressPage
from .recurring import RecurringPage
from .transactions import TransactionsPage
from .calendar_page import CalendarPage
from .plan_page import PlanPage

# The choices under View → Appearance, as they are stored. Dark is the default:
# it is what the app has always been, and it is the room it was designed in.
APPEARANCES = (("dark", "Dark"), ("light", "Light"), ("system", "Match my computer"))


def _retint(widget) -> None:
    """Move whatever colours a widget was given by hand to the current palette.

    Most colour comes from the app stylesheet and follows a switch by itself.
    A widget styled in place (a header, a swatch, a coloured word in a label)
    holds the hex it was built with; each one is a palette role, so it is swapped
    for the same role in the new palette.
    """
    sheet = widget.styleSheet()
    if "#" in sheet:
        moved = theme.live_stylesheet(sheet)
        if moved != sheet:
            widget.setStyleSheet(moved)
    if isinstance(widget, QLabel) and widget.textFormat() != Qt.TextFormat.PlainText:
        text = widget.text()
        if "color" in text:
            moved = theme.live_stylesheet(text)
            if moved != text:
                widget.setText(moved)
    if isinstance(widget, charts.pg.PlotWidget):
        charts.restyle_plot(widget)


_METHODOLOGY = f"""
<h3>How Doug models your money</h3>
<p><b>History.</b> Cash-account balances are reconstructed from the ledger by
anchoring at a closing posted balance, excluding pending holds: B(t) = B(t₀) + F(t) − F(t₀), where F is the
cumulative daily net flow. Loans and investments interpolate through balance
snapshots. Bank imports use Posting Date when supplied; Transaction Date is kept
separately. Unposted CSV rows are excluded and can be inspected under
Transactions → Tools → Latest CSV import. Conflicting dated balances and
negative daily closes on accounts that cannot overdraw need review; values are
not forced to zero.</p>
<p><b>Recurring detection.</b> Descriptions are normalized and grouped; groups
with ≥3 events whose inter-arrival intervals match a cadence (weekly …
annual, incl. twice-monthly) within robust tolerances become patterns, scored
by count, interval regularity, and amount stability.</p>
<p><b>Forecast.</b> Active patterns replay forward at median amounts. Loans
amortize monthly at APR/12 on their payment day. Use Goals funding plan applies
the saved allowance and priority to repayments; separate controls can instead
send extra payments to the highest-APR loan and roll freed payments forward. Investments follow
V<sub>k+1</sub> = V<sub>k</sub>·exp[(μ−σ²/2)Δt + σ√Δt·Z] with monthly
contributions debited from cash.</p>
<p><b>Variable spending.</b> Everything unmatched is bootstrapped: whole
historical weeks of per-category spend are resampled (preserving within-week
correlation), scaled by your scenario sliders, and spread daily. Percentile
bands are computed across paths.</p>
<p><b>Goals.</b> Arrival estimates report the deterministic crossing plus an
optimistic–conservative range (P90/P10 band crossings).</p>
<p style="color:{theme.MUTED}">Projections are statistical estimates from your own
history — not financial advice.</p>
"""


class MainWindow(QMainWindow):
    def __init__(self, store: Store, real_db_path=None):
        super().__init__()
        install_entry_wheel_guard(QApplication.instance())
        # The palette is chosen before anything is built, so every widget starts
        # out in it rather than being repainted a moment after it appears.
        self._settings = prefs.app_settings()
        self.appearance = self._settings.value("appearance", "dark", type=str)
        if self.appearance not in dict(APPEARANCES):
            self.appearance = "dark"
        theme.use(self._palette_for(self.appearance), QApplication.instance())
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme)
        self.store = store
        # Remembered so a demo can be a detour rather than a destination: the
        # window can always find its way back to the file it started on.
        self.real_db_path = Path(real_db_path or store.path)
        self.demo_key: str | None = None
        self.setWindowTitle("Financing with Doug")
        self.resize(1280, 900)
        # An explicit minimum takes precedence over minimumSizeHint, which is
        # what was forcing the window open to ~2100x1200: a QStackedWidget
        # adopts the largest size hint among its pages, and the charts and
        # cards inside add up fast. Without this the window grows every time a
        # page or a chart toggle changes the computed hint, and has to be
        # re-fitted by hand.
        self.setMinimumSize(980, 620)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        # Scoped to the header widget itself. An unscoped rule cascades to every
        # label inside it, which drew a rule under the title, the version and
        # the tagline.
        header.setObjectName("header")
        header.setStyleSheet(f"QWidget#header {{ background: {theme.BG_DARKER};"
                             f" border-bottom: 1px solid {theme.BORDER}; }}")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 10, 18, 10)
        title = QLabel("Financing with Doug")
        title.setObjectName("appTitle")
        from .. import __version__
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("appTag")
        self.header_nw = QLabel("")
        self.header_nw.setObjectName("appTag")

        # Simple / Everything. The app has grown a lot of controls that are
        # genuinely useful to the person who built it and pure noise to the
        # person who was handed it — Monte Carlo path counts, a backtest panel,
        # a destructive Clear button. Hiding them is not dumbing the app down:
        # every number they produce is still reachable, and the toggle is one
        # click away in the header rather than buried in a preferences dialog.
        #
        # It defaults to Simple. The app's whole stated purpose is to be
        # approachable to someone's family, and a default that has to be
        # discovered is a default that will not be. Flipping it is remembered.
        #
        # Two lit-when-chosen buttons rather than one that is named after the
        # state it is in: a button reading "Simple view" while in Simple view
        # reads as an action that would change nothing.
        self.mode_box = QWidget()
        self.mode_box.setAccessibleName("View")
        mode_row = QHBoxLayout(self.mode_box)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(4)
        mode_label = QLabel("View")
        mode_label.setObjectName("appTag")
        mode_row.addWidget(mode_label)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_simple = QPushButton("Simple")
        self.mode_all = QPushButton("Everything")
        for btn, simple in ((self.mode_simple, True), (self.mode_all, False)):
            btn.setObjectName("range")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, s=simple: self._choose_mode(s))
            self.mode_group.addButton(btn)
            mode_row.addWidget(btn)

        # Undo lives in the header as well as on the menu. A menu item is
        # enough for someone who already knows the change can be taken back;
        # the point of a button is that it is *visible the moment there is
        # something to undo*, which is exactly when a person who has just
        # mistyped a balance is looking at the screen wondering what they did.
        # Hidden when the stack is empty, so it never reads as a live control
        # that does nothing.
        # Import is the one thing everyone does again and again, and it used to
        # live only in the File menu and a Tools menu on one tab. Here it is on
        # every page, and it opens the same flow the menu does.
        self.import_btn = QPushButton("Import CSV…")
        self.import_btn.setObjectName("small")
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.setToolTip("Bring in a bank CSV (also under File → Import CSV…)")
        self.import_btn.clicked.connect(self.open_import)

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setObjectName("small")
        self.undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_btn.setVisible(False)
        self.undo_btn.clicked.connect(self.undo)

        hl.addWidget(title)
        hl.addSpacing(6)
        hl.addWidget(ver)
        hl.addStretch(1)
        hl.addWidget(self.import_btn)
        hl.addSpacing(10)
        hl.addWidget(self.undo_btn)
        hl.addSpacing(10)
        hl.addWidget(self.mode_box)
        hl.addSpacing(10)
        hl.addWidget(self.header_nw)
        outer.addWidget(header)

        # The way back from a demo is on the bar itself: it used to be a sentence
        # saying where in the File menu to find it.
        self.banner_bar = QFrame()
        self.banner_bar.setObjectName("demoBannerBar")
        self.banner_bar.setVisible(False)
        banner_row = QHBoxLayout(self.banner_bar)
        banner_row.setContentsMargins(0, 0, 14, 0)
        self.banner = QLabel("")
        self.banner.setObjectName("demoBanner")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        banner_row.addWidget(self.banner, 1)
        self.banner_back = QPushButton("Return to my own data")
        self.banner_back.setObjectName("bannerButton")
        self.banner_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.banner_back.clicked.connect(lambda: self.return_to_real())
        banner_row.addWidget(self.banner_back, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self.banner_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(184)
        names = self._rail_names = ("Home", "Accounts", "Bills & payday", "Plan", "Forecast")
        for name in names:
            self.nav.addItem(name)
        self.stack = QStackedWidget()
        # Ignored: take whatever room is going, and do not report a size hint
        # upward. Pages then scale to the window instead of the window scaling
        # to the pages.
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Ignored)
        self.pages = [
            DashboardPage(store, self),
            AccountsPage(store, self),
            TransactionsPage(store, self),
            RecurringPage(store, self),
            InsightsPage(store, self),
            GoalsPage(store, self),
            ForecastPage(store, self),
            ProgressPage(store, self),
            CalendarPage(store, self),
        ]
        dashboard, accounts, transactions, recurring, insights, goals, forecast, progress, calendar = self.pages
        self.dashboard_page = dashboard
        plan = PlanPage(store, self)
        self.pages.append(plan)
        self.dashboard_tabs = QTabWidget()
        self.dashboard_tabs.addTab(dashboard, "Overview")
        self.dashboard_tabs.addTab(insights, "Spending")
        # An account and its transactions are one place: pick the account on
        # Balances, read or fix its ledger on Transactions.
        self.accounts_tabs = QTabWidget()
        self.accounts_tabs.addTab(accounts, "Balances")
        self.accounts_tabs.addTab(transactions, "Transactions")
        self.bills_tabs = QTabWidget()
        self.bills_tabs.addTab(calendar, "Calendar")
        self.bills_tabs.addTab(recurring, "Detected bills && pay")
        self.forecast_tabs = QTabWidget()
        self.forecast_tabs.addTab(forecast, "Projection")
        self.forecast_tabs.addTab(progress, "Track record")
        # Plan is one rail item for the whole monthly-planning loop: the goal
        # ladder first, because it is the tab people come back to, and the
        # budget that sizes its allowance second.
        self.plan_tabs = QTabWidget()
        self.plan_tabs.addTab(goals, "Goals")
        self.plan_tabs.addTab(plan, "Monthly budget")
        self.sections = [self.dashboard_tabs, self.accounts_tabs,
                         self.bills_tabs, self.plan_tabs, self.forecast_tabs]
        # Tabs a relative does not need day to day. Simple view hides them, and
        # anything that links to one reveals it, so a "Review" button on Home
        # never leads nowhere. Revealed tabs stay until the view is toggled.
        self._simple_hidden = {transactions, progress}
        self._revealed: set = set()
        self._destinations = {
            name: (i, None) for i, name in enumerate(names)
        }
        # Pages are reached by name, never by rail position, so a link keeps
        # working when the rail is rearranged. "Plan" stays an alias for the
        # budget page it used to name; the rail item of that name opens the
        # section on whichever of its tabs was used last.
        self._destinations.update({
            "Dashboard": (0, None), "Overview": (0, dashboard),
            "Insights": (0, insights), "Spending": (0, insights),
            "Accounts": (1, accounts), "Balances": (1, accounts),
            "Transactions": (1, transactions),
            "Recurring": (2, recurring), "Calendar": (2, calendar),
            "Plan": (3, plan), "Monthly budget": (3, plan), "Goals": (3, goals),
            "Progress": (4, progress), "Projection": (4, forecast),
        })
        for p in self.pages:
            p.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Ignored)
        # Name each page's scroll area, and take it out of the Tab order when the
        # page has controls to Tab to (see tidy_scroll_focus).
        for page, label in zip(self.pages, (
                "Overview", "Balances", "Transactions", "Detected bills and pay",
                "Spending", "Goals", "Projection", "Track record", "Calendar",
                "Monthly budget")):
            for area in page.findChildren(QScrollArea):
                tidy_scroll_focus(area, f"{label} page" if area.parentWidget() is page
                                  else f"{label} panel")
        for section in self.sections:
            self.stack.addWidget(section)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self._on_nav)

        self.simple_mode = self._settings.value("simple_mode", True, type=bool)
        self._apply_mode(refresh=False)
        # Recording starts here rather than in Store.__init__ so that the demo
        # builder, the test suite and any scripted import — none of which have
        # a person to press Ctrl-Z — pay nothing for it. Both this and the menu
        # have to be in place before the first page refresh, which is what
        # setCurrentRow triggers: the header syncs the undo controls, and they
        # have to exist by then.
        store.enable_undo()
        self._build_menu()
        for tabs in (self.dashboard_tabs, self.accounts_tabs, self.bills_tabs,
                     self.plan_tabs, self.forecast_tabs):
            tabs.currentChanged.connect(
                lambda _index, host=tabs: self.refresh_current()
                if self.stack.currentWidget() is host else None)
        self.nav.setCurrentRow(0)
        self.statusBar().showMessage(f"Database: {store.path}")
        # On a first run the keyboard starts on the step to take. Left alone it
        # starts on the header's Import button, which reads as step one even before
        # there is an account to import into.
        if setup_stage(store) != READY:
            QTimer.singleShot(0, self.dashboard_page.focus_first_step)

    # ---------------------------------------------------------------
    def _sync_tabs(self) -> None:
        """Show each section's tabs for the current view.

        A section left with a single tab shows no tab bar at all, so it reads as
        a plain page rather than as one tab with nowhere to go.
        """
        hidden = set() if not self.simple_mode else self._simple_hidden - self._revealed
        for tabs in (s for s in self.sections if isinstance(s, QTabWidget)):
            if tabs.currentWidget() in hidden:
                first = next(tabs.widget(i) for i in range(tabs.count())
                             if tabs.widget(i) not in hidden)
                with QSignalBlocker(tabs):
                    tabs.setCurrentWidget(first)
            shown = 0
            for i in range(tabs.count()):
                visible = tabs.widget(i) not in hidden
                tabs.setTabVisible(i, visible)
                shown += visible
            tabs.tabBar().setVisible(shown > 1)
            # A hidden tab bar left the QTabWidget itself in the Tab order, and
            # Tab from the rail then never got past it: two sections in Simple view
            # could not be entered from the keyboard.
            tabs.setFocusPolicy(Qt.FocusPolicy.TabFocus if shown > 1
                                else Qt.FocusPolicy.NoFocus)

    def _choose_mode(self, simple: bool) -> None:
        """A click on the view that is already on changes nothing."""
        if simple != self.simple_mode:
            self._toggle_mode()
        else:
            self._apply_mode(refresh=False)     # keep the lit button honest

    def _toggle_mode(self) -> None:
        self._revealed.clear()
        self.simple_mode = not self.simple_mode
        self._settings.setValue("simple_mode", self.simple_mode)
        self._apply_mode()
        self.status("Showing the essentials. Each page's Explain menu has the calculations."
                    if self.simple_mode else
                    "Showing everything, including the advanced controls.")

    def _apply_mode(self, refresh: bool = True) -> None:
        """Tell every page which face to wear.

        Pages opt in by defining ``set_simple``; the ones that are already a
        single task — Accounts, Transactions — have nothing to hide and simply
        do not implement it.
        """
        on = self.simple_mode
        self.mode_simple.setChecked(on)
        self.mode_all.setChecked(not on)
        self.mode_simple.setToolTip(
            "Just the essentials: the pages kept to what most people need.")
        self.mode_all.setToolTip(
            "Everything, including the advanced panels: path counts, sensitivity, "
            "backtesting and checkpoint tables.")
        self._sync_view_menu()
        self._sync_tabs()
        for p in self.pages:
            fn = getattr(p, "set_simple", None)
            if fn is not None:
                try:
                    fn(on)
                except Exception:
                    pass          # a page that cannot simplify must still work
        if refresh:
            self.refresh_current()

    def _sync_view_menu(self) -> None:
        if not hasattr(self, "a_simple"):
            return                      # the menu is built after the first pass
        self.a_simple.setChecked(self.simple_mode)
        self.a_everything.setChecked(not self.simple_mode)
        self.appearance_actions[self.appearance].setChecked(True)

    # ---------------- appearance ----------------
    @staticmethod
    def _palette_for(appearance: str) -> theme.Palette:
        if appearance == "system":
            scheme = QGuiApplication.styleHints().colorScheme()
            return theme.LIGHT if scheme == Qt.ColorScheme.Light else theme.DARK
        return theme.PALETTES.get(appearance, theme.DARK)

    def set_appearance(self, appearance: str) -> None:
        """Dark, Light or Match my computer: remembered, and applied at once."""
        self.appearance = appearance
        self._settings.setValue("appearance", appearance)
        self._sync_view_menu()
        self._apply_palette()
        self.status(f"{dict(APPEARANCES)[appearance]} appearance. "
                    "Change it under View → Appearance.")

    def _on_color_scheme(self, _scheme) -> None:
        if self.appearance == "system":
            self._apply_palette()

    def _apply_palette(self) -> None:
        """Repaint the open window in the chosen palette, in place.

        Nothing is rebuilt: the page, its tab, a drawer left open, what is typed
        into a field and how far a page is scrolled all stay as they were. The
        stylesheet moves everything it styles; hand-set colours are translated
        role for role; the page in view redraws its charts; every other page
        redraws when it is next opened, as it always does.
        """
        palette = self._palette_for(self.appearance)
        if palette is theme.current():
            return
        app = QApplication.instance()
        theme.use(palette, app)
        for widget in app.allWidgets():
            _retint(widget)
        for page in self.pages:
            restyle = getattr(page, "restyle", None)
            if restyle is not None:
                restyle()
        self.refresh_current()

    # ---------------------------------------------------------------
    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        a_imp = QAction("Import CSV…", self)
        a_imp.triggered.connect(self.open_import)
        m_file.addAction(a_imp)
        m_file.addSeparator()

        # Demos live in their own database files. Loading one used to mean
        # writing sample accounts into whatever database was open, which for
        # anybody with real data in it was a one-way door — you could look at
        # the demo or keep your ledger, not both. Switching files instead makes
        # it a view, and going back is instant.
        from ..demo import PROFILES
        m_demo = m_file.addMenu("Load demo dataset")
        for p in PROFILES:
            act = QAction(p.name, self)
            act.setToolTip(p.blurb)
            act.triggered.connect(lambda _c=False, k=p.key: self._load_demo(k))
            m_demo.addAction(act)
        self.a_back = QAction("Return to my own data", self)
        self.a_back.triggered.connect(self.return_to_real)
        self.a_back.setEnabled(False)
        m_file.addAction(self.a_back)
        m_file.addSeparator()

        a_report = QAction("Save a month in review…", self)
        a_report.setToolTip("One printable page about a finished month, in "
                            "plain language, that can leave this app")
        a_report.triggered.connect(self.save_month_report)
        m_file.addAction(a_report)
        a_backup = QAction("Back up data…", self)
        a_backup.setToolTip("Save a complete copy of the current database")
        a_backup.triggered.connect(self.backup_data)
        m_file.addAction(a_backup)
        m_file.addSeparator()

        a_reset = QAction("Reset all data…", self)
        a_reset.triggered.connect(self._reset)
        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_reset)
        m_file.addSeparator()
        m_file.addAction(a_quit)

        m_edit = self.menuBar().addMenu("&Edit")
        self.a_undo = QAction("Undo", self)
        self.a_undo.setShortcut(QKeySequence.StandardKey.Undo)          # Ctrl+Z
        self.a_undo.triggered.connect(self.undo)
        self.a_redo = QAction("Redo", self)
        # Both conventions, because both are muscle memory for somebody, and
        # the platform standard is only ever one of them — on Windows Qt
        # reports Ctrl+Y, so binding the standard key alone would leave
        # Ctrl+Shift+Z dead while the status bar cheerfully advertised it.
        _redo = QKeySequence.keyBindings(QKeySequence.StandardKey.Redo)
        for extra in (QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")):
            if extra not in _redo:
                _redo.append(extra)
        self.a_redo.setShortcuts(_redo)
        self.a_redo.triggered.connect(self.redo)
        m_edit.addAction(self.a_undo)
        m_edit.addAction(self.a_redo)
        # The menu can be opened after a change made from a page that did not
        # route back through refresh_header, so it re-reads the stack on the
        # way open rather than trusting it was told.
        m_edit.aboutToShow.connect(self._sync_undo)

        # View holds the two choices about how the app looks: how much of it to
        # show (the same switch as the header's) and which palette to show it in.
        m_view = self.menuBar().addMenu("&View")
        modes = QActionGroup(self)
        self.a_simple = QAction("Simple", self, checkable=True)
        self.a_simple.setToolTip("Just the essentials")
        self.a_everything = QAction("Everything", self, checkable=True)
        self.a_everything.setToolTip("Every page and the advanced controls")
        for act, simple in ((self.a_simple, True), (self.a_everything, False)):
            act.triggered.connect(lambda _c=False, s=simple: self._choose_mode(s))
            modes.addAction(act)
            m_view.addAction(act)
        m_view.addSeparator()
        m_look = m_view.addMenu("Appearance")
        looks = QActionGroup(self)
        self.appearance_actions = {}
        for key, label in APPEARANCES:
            act = QAction(label, self, checkable=True)
            act.triggered.connect(lambda _c=False, k=key: self.set_appearance(k))
            looks.addAction(act)
            m_look.addAction(act)
            self.appearance_actions[key] = act
        self.appearance_actions["system"].setToolTip(
            "Light or dark, following your computer's own setting")
        self._sync_view_menu()

        m_help = self.menuBar().addMenu("&Help")
        a_guide = QAction("Getting started", self)
        a_guide.setShortcut("F1")
        a_guide.triggered.connect(self.show_guide)
        m_help.addAction(a_guide)
        a_manual = QAction("User manual (PDF)", self)
        a_manual.setToolTip("Open the complete user manual in your PDF reader")
        a_manual.triggered.connect(self.show_manual)
        m_help.addAction(a_manual)
        m_help.addSeparator()
        a_meth = QAction("Methodology", self)
        a_meth.triggered.connect(self._methodology)
        m_help.addAction(a_meth)
        a_about = QAction("About Doug", self)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_about)

        # A bare action on the menu bar rather than an entry inside Help: this
        # is the one menu item a non-technical user is ever told to go and find,
        # and "it's under Help" is a step too many when the whole instruction is
        # "click that". Clicking it runs the check directly — there is no menu
        # to drop down. Kept last so File/Edit/Help stay where muscle memory
        # expects them.
        self.a_update = QAction("Check for updates", self)
        self.a_update.setToolTip("Ask GitHub whether a newer version has been "
                                 "released, and install it if so")
        self.a_update.triggered.connect(self.check_for_updates)
        self.menuBar().addAction(self.a_update)

    def _on_nav(self, row: int) -> None:
        if not 0 <= row < len(self.sections):
            return
        self.stack.setCurrentIndex(row)
        self.refresh_current()

    def current_page(self):
        section = self.stack.currentWidget()
        return section.currentWidget() if isinstance(section, QTabWidget) else section

    def refresh_current(self) -> None:
        page = self.current_page()
        if page is not None:
            page.refresh()
        self.refresh_header()

    def refresh_header(self) -> None:
        st = headline_stats(self.store)
        self.header_nw.setText(
            f"net worth  {fmt_money(st['networth'])}" if st["networth"] else "")
        self._sync_undo()
        self._sync_rail()

    def _sync_rail(self) -> None:
        """Dim the sections that fill in from statements until some are imported.

        They stay clickable, and each page says in words what it is waiting for;
        the rail only says, before anyone clicks, which of them are not worth a
        visit yet. The label is set apart by weight of colour and by italics, so
        it does not rest on colour alone.
        """
        stage = setup_stage(self.store)
        waiting = {} if stage == READY else {
            "Bills & payday": "Fills in once your statements are imported: "
                              "Doug finds the bills and paychecks that repeat in them.",
            "Forecast": ("Add an account and import its statements to see where "
                         "your cash is headed." if stage == NO_ACCOUNTS else
                         "A first forecast can use your balances alone. Import "
                         "statements to bring in your bills, paychecks and spending."),
        }
        for row, name in enumerate(self._rail_names):
            item = self.nav.item(row)
            tip = waiting.get(name, "")
            font = item.font()
            font.setItalic(bool(tip))
            item.setFont(font)
            item.setForeground(QColor(theme.MUTED if tip else theme.RAIL_TEXT))
            item.setToolTip(tip)
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, tip)

    def status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 6000)

    def show_page(self, name: str) -> None:
        """Navigate by name so page links do not depend on sidebar ordering."""
        destination = self._destinations.get(name)
        if destination is None:
            return
        row, page = destination
        section = self.sections[row]
        if (page is not None and self.simple_mode and page in self._simple_hidden
                and page not in self._revealed):
            self._revealed.add(page)
            self._sync_tabs()
        tab_changed = page is not None and section.currentWidget() is not page
        # Resolve the destination before emitting navigation signals. Otherwise
        # a link to Progress first calculates Projection, then loads Progress.
        if tab_changed:
            with QSignalBlocker(section):
                section.setCurrentWidget(page)
        if self.nav.currentRow() != row:
            self.nav.setCurrentRow(row)
        elif tab_changed:
            self.refresh_current()

    # ---------------- undo / redo ----------------
    def _sync_undo(self) -> None:
        """Make the menu items and the header button say what they will do.

        Naming the step — "Undo Import CSV" rather than "Undo" — is the whole
        difference between a control someone trusts and one they are afraid to
        press, because it answers the question they actually have, which is not
        *can* I undo but *what* will disappear if I do.
        """
        u = getattr(self.store, "undo", None)
        can_undo = bool(u and u.can_undo)
        can_redo = bool(u and u.can_redo)
        self.a_undo.setEnabled(can_undo)
        self.a_redo.setEnabled(can_redo)
        self.a_undo.setText(f"Undo {u.undo_label}" if can_undo else "Undo")
        self.a_redo.setText(f"Redo {u.redo_label}" if can_redo else "Redo")
        self.undo_btn.setVisible(can_undo)
        if can_undo:
            self.undo_btn.setText(f"↶  Undo {u.undo_label.lower()}")
            self.undo_btn.setToolTip(
                f"Put things back the way they were before “{u.undo_label}” "
                f"(Ctrl+Z)")

    def undo(self) -> None:
        self._step("undo", "Undid")

    def redo(self) -> None:
        self._step("redo", "Redid")

    def _step(self, direction: str, verb: str) -> None:
        u = getattr(self.store, "undo", None)
        if u is None:
            return
        try:
            label = getattr(u, direction)()
        except Exception as e:
            # Undo rewrites the database file underneath an open connection.
            # If that fails the data on disk is still whatever it was, but the
            # user has to be told the change did not happen — silently doing
            # nothing here would be indistinguishable from having nothing to
            # undo, which is the one thing they must not conclude.
            QMessageBox.warning(
                self, f"Couldn't {direction} that",
                f"{e}\n\nYour data has not been changed.")
            return
        if label is None:
            self.status(f"Nothing left to {direction}.")
            return
        self.refresh_current()
        self.status(f"{verb} “{label}”. "
                    f"{'Ctrl+Shift+Z puts it back.' if direction == 'undo' else ''}")

    # ---------------------------------------------------------------
    def open_account(self) -> None:
        """The Forecast page's own "Add an account" button: the same dialog as add_account."""
        self.add_account()

    def show_attention(self) -> None:
        """Open the Transactions tab on what still needs a look."""
        self.show_page("Transactions")
        _row, transactions = self._destinations["Transactions"]
        transactions.show_attention()

    def show_transactions_for(self, account_id: int) -> None:
        """Open the Transactions tab on one account's ledger."""
        self.show_page("Transactions")
        _row, transactions = self._destinations["Transactions"]
        transactions.show_account(account_id)

    def add_account(self) -> None:
        """Open the add-account dialog from wherever the person is.

        Every "Add an account" used to go to the Accounts page, which then had an
        "Add your first account" button of its own before the dialog opened.
        """
        _row, accounts = self._destinations["Accounts"]
        accounts._add()

    def open_import(self) -> None:
        """Single import entry point for every account type.

        Cash accounts take a transaction CSV; loans and investments take a
        balance or activity CSV. The user shouldn't have to know which dialog
        they need — pick the account, get the right importer.

        The account picker comes *first* whenever both kinds exist. Routing on
        "are there any cash accounts?" instead meant that owning a chequing
        account made every loan and investment unreachable from this menu item:
        the cash importer opened, and its account list — cash-like only — simply
        did not contain them.
        """
        accounts = self.store.accounts()
        if not accounts:
            # An import needs an account to land in, so open the dialog that makes
            # one rather than stopping to say so.
            self.status("Add an account first, then import its statements.")
            self.add_account()
            return
        target = accounts[0] if len(accounts) == 1 else \
            self._pick_import_account(accounts)
        if target is None:
            return
        self.import_for_account(target)

    def import_for_account(self, account) -> None:
        """Open whichever importer suits this account's type."""
        if not account.type.is_cashlike:
            self._import_balance_history(account)
            return
        first = not self.store.has_transactions()
        dlg = ImportDialog(self.store, self, account_id=account.id)
        if dlg.exec():
            self.refresh_current()
            if first and self.store.has_transactions():
                # The first import is the point of the whole setup, so end on
                # what it was for: where the person stands, on Home.
                self.show_page("Overview")
                self.status("Your statements are in. This is where you stand.")

    def _import_balance_history(self, account) -> None:
        from .accounts import SnapshotImportDialog
        dlg = SnapshotImportDialog(self.store, account, self)
        if dlg.exec():
            self.refresh_current()
            self.status(f"Balance history imported for {account.name}.")

    def _pick_import_account(self, options):
        from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                                     QFormLayout)
        dlg = QDialog(self)
        dlg.setWindowTitle("Choose account")
        form = QFormLayout(dlg)
        combo = QComboBox()
        for a in options:
            combo.addItem(f"{a.name}  ({a.type.label})", a.id)
        form.addRow("Account", combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return self.store.account(combo.currentData())
        return None

    # ---------------- demo datasets ----------------
    def demo_db_path(self, key: str) -> Path:
        """Where a demo profile's own database lives, beside the real one."""
        return self.real_db_path.parent / "demos" / f"{key}.db"

    def _load_demo(self, key: str, confirm: bool = True) -> None:
        from ..demo import load_demo, profile
        prof = profile(key)
        path = self.demo_db_path(key)
        fresh = not path.exists()
        if fresh and confirm:
            box = QMessageBox(self)
            box.setWindowTitle(prof.name)
            box.setText(f"<b>{prof.name}</b><p>{prof.detail}</p>"
                        f"<p style='color:{theme.MUTED}'>This opens in its own "
                        f"separate file. Your own data is untouched and one "
                        f"menu click away.</p>"
                        f"<p style='color:{theme.MUTED}'>Building it takes a few "
                        f"seconds the first time.</p>")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setStandardButtons(QMessageBox.StandardButton.Ok
                                   | QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Ok:
                return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            store = Store(path)
            if fresh or not store.accounts():
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    load_demo(store, key)
                finally:
                    QApplication.restoreOverrideCursor()
        except Exception as e:
            QMessageBox.warning(self, "Demo failed", str(e))
            return
        self._switch_store(store, demo_key=key)
        self.status(f"Viewing the “{prof.name}” demo — your own data is safe. "
                    f"File → Return to my own data to go back.")

    def return_to_real(self) -> None:
        if self.demo_key is None:
            return
        self._switch_store(Store(self.real_db_path), demo_key=None)
        self.status("Back on your own data.")

    def _switch_store(self, store: Store, demo_key: str | None) -> None:
        """Point the whole window at a different database.

        The pages are handed the new store rather than rebuilt, so splitter
        positions, filters and chart toggles survive a trip through a demo —
        losing all of that every time someone glanced at an example would make
        the feature annoying enough not to use.
        """
        old = self.store
        self.store = store
        self.demo_key = demo_key
        for plot in self.findChildren(charts.ChartPlot):
            plot.forget_view()
        for p in self.pages:
            p.store = store
            reset = getattr(p, "reset_for_store", None)
            if reset is not None:
                reset()
        # A fresh stack per database. Undo restores a *file*, so carrying steps
        # across a switch would let Ctrl-Z on the demo overwrite the real
        # ledger with a demo snapshot — the one mistake this feature must never
        # make.
        store.enable_undo()
        try:
            old.close()
        except Exception:
            pass
        self.a_back.setEnabled(demo_key is not None)
        self._sync_banner()
        self.refresh_current()
        self.statusBar().showMessage(f"Database: {store.path}")

    def _sync_banner(self) -> None:
        """A demo has to look like a demo.

        Someone glancing at a screen showing an $86,000 brokerage balance
        should never have to wonder whether it is theirs.
        """
        if self.demo_key is None:
            self.banner.setVisible(False)
            self.banner_bar.setVisible(False)
            return
        from ..demo import profile
        self.banner.setText(
            f"  DEMO DATA — “{profile(self.demo_key).name}”. Nothing here is "
            f"yours; your own data is untouched.")
        self.banner.setVisible(True)
        self.banner_bar.setVisible(True)

    # ---------------- month in review ----------------
    def backup_data(self) -> None:
        from datetime import date
        from PyQt6.QtWidgets import QFileDialog

        default = Path.home() / f"{self.store.path.stem}-backup-{date.today():%Y-%m-%d}.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "Back up current data", str(default), "SQLite database (*.db)")
        if not path:
            return
        try:
            self.store.backup_to(path)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't save the backup", str(e))
            return
        QMessageBox.information(
            self, "Backup saved",
            f"A complete copy of {'this demo' if self.demo_key else 'your data'} "
            f"was saved to:\n{path}\n\n"
            "Keep it somewhere safe. It includes accounts, transactions, "
            "goals, learned categories, and database settings.")
        self.status(f"Backup saved to {path}")

    def save_month_report(self) -> None:
        """Write one finished month to a page that can be printed or sent.

        HTML rather than a print dialog on purpose: it opens in any browser on
        any machine with nothing installed, Ctrl-P turns it into a PDF, and it
        can be emailed to a partner who does not have this program. A print
        dialog would produce paper and nothing else.
        """
        from PyQt6.QtWidgets import QFileDialog
        from .. import report as _report

        if not self.store.accounts() and self.store.transactions().empty:
            QMessageBox.information(
                self, "Nothing to report yet",
                "Import a month of transactions first — there is nothing to "
                "review.")
            return
        month = _report.last_complete_month()
        default = Path.home() / f"month-in-review-{month:%Y-%m}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Save {month:%B %Y} in review", str(default),
            "Web page (*.html)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            _report.write_html(self.store, path, month)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't write the report", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()

        box = QMessageBox(self)
        box.setWindowTitle("Month in review saved")
        box.setText(f"{month:%B %Y} saved to:\n{path}")
        box.setInformativeText(
            "It is a single self-contained page — no internet, no other "
            "files. Open it in a browser to read it, or press Ctrl-P there to "
            "turn it into a PDF you can send on.")
        box.setStandardButtons(QMessageBox.StandardButton.Close)
        open_it = box.addButton("Open it now", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(open_it)
        box.exec()
        if box.clickedButton() is open_it:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.status(f"Month in review saved to {path}")

    def _reset(self) -> None:
        from .accounts import confirm
        what = ("this demo" if self.demo_key else "your")
        # Named buttons, and Keep is what Enter and Esc do: a bare Yes/No here
        # made the destructive answer the default.
        if confirm(self, "Reset all data",
                   f"Delete all accounts, transactions, patterns, and goals from "
                   f"{what} database?\n\nCtrl+Z will bring it all back while this "
                   f"window stays open — but once you close the app, it is gone.",
                   yes_text="Delete everything", no_text="Keep my data"):
            self.store.clear_all()
            self.refresh_current()
            self.status("Database cleared. Ctrl+Z undoes this.")

    def show_guide(self) -> None:
        from .guide import show_guide
        show_guide(self)

    def show_manual(self) -> None:
        from .manual import open_manual
        open_manual(self)

    # ---------------- updates ----------------
    def check_for_updates(self) -> None:
        from .update_dialog import check_now
        check_now(self)

    def check_for_updates_quietly(self) -> None:
        """Called once after the window is up. Silent unless there is news."""
        from .update_dialog import check_quietly
        check_quietly(self)

    def _about(self) -> None:
        from .. import __author__, __version__
        box = QMessageBox(self)
        box.setWindowTitle("About Doug")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<h3>Financing with Doug v{__version__}</h3>"
            f"<p style='color:{theme.MUTED}'>Created by <b>{__author__}</b></p>"
            f"<p>A local, private personal-finance modeling tool. All data "
            f"stays in a SQLite file on this machine.</p>"
            f"<p>Import bank CSVs, review recurring bills, track goals, and "
            f"explore forecasts based on your own history.</p>"
            f"<p style='color:{theme.MUTED}'>Help → Getting started explains the "
            f"workflow; Help → Methodology explains the model.</p>")
        box.exec()

    def _methodology(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Methodology")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(theme.live_stylesheet(_METHODOLOGY))
        box.exec()
