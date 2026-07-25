"""Main window: sidebar navigation over stacked pages."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                             QMessageBox, QSizePolicy, QStackedWidget, QVBoxLayout,
                             QWidget)

from ..history import headline_stats
from ..store import Store
from ..util import fmt_money
from . import theme
from .accounts import AccountsPage
from .dashboard import DashboardPage
from .forecast_page import ForecastPage
from .goals_page import GoalsPage
from .insights_page import InsightsPage
from .import_dialog import ImportDialog
from .progress_page import ProgressPage
from .recurring import RecurringPage
from .transactions import TransactionsPage

_METHODOLOGY = """
<h3>How Doug models your money</h3>
<p><b>History.</b> Cash-account balances are reconstructed from the ledger by
anchoring at your entered balance: B(t) = B(t₀) + F(t) − F(t₀), where F is the
cumulative daily net flow. Loans and investments interpolate through balance
snapshots.</p>
<p><b>Recurring detection.</b> Descriptions are normalized and grouped; groups
with ≥3 events whose inter-arrival intervals match a cadence (weekly …
annual, incl. twice-monthly) within robust tolerances become patterns, scored
by count, interval regularity, and amount stability.</p>
<p><b>Forecast.</b> Active patterns replay forward at median amounts. Loans
amortize monthly at APR/12 on their payment day; extra payment goes to the
highest-APR loan, and freed payments optionally snowball. Investments follow
V<sub>k+1</sub> = V<sub>k</sub>·exp[(μ−σ²/2)Δt + σ√Δt·Z] with monthly
contributions debited from cash.</p>
<p><b>Variable spending.</b> Everything unmatched is bootstrapped: whole
historical weeks of per-category spend are resampled (preserving within-week
correlation), scaled by your scenario sliders, and spread daily. Percentile
bands are computed across paths.</p>
<p><b>Goals.</b> Arrival estimates report the deterministic crossing plus an
optimistic–conservative range (P90/P10 band crossings).</p>
<p style="color:#8B98A9">Projections are statistical estimates from your own
history — not financial advice.</p>
"""


class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.setWindowTitle("Financing with Doug")
        self.resize(1280, 850)
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
        header.setStyleSheet(f"background: {theme.BG_DARKER};"
                             f"border-bottom: 1px solid {theme.BORDER};")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 10, 18, 10)
        title = QLabel("Financing with Doug")
        title.setObjectName("appTitle")
        from .. import __version__
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("appTag")
        # Easter egg: Doug has opinions, and clicking the version number is a
        # harmless place to keep them.
        ver.setCursor(Qt.CursorShape.PointingHandCursor)
        ver.setToolTip("Ask Doug something")
        ver.mousePressEvent = lambda _e: self._doug_says()
        tag = QLabel("local finance · history → forecast")
        tag.setObjectName("appTag")
        self.header_nw = QLabel("")
        self.header_nw.setObjectName("appTag")
        hl.addWidget(title)
        hl.addSpacing(6)
        hl.addWidget(ver)
        hl.addSpacing(10)
        hl.addWidget(tag)
        hl.addStretch(1)
        hl.addWidget(self.header_nw)
        outer.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(184)
        for name in ("Dashboard", "Accounts", "Transactions", "Recurring",
                     "Insights", "Goals", "Forecast", "Progress"):
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
        ]
        for p in self.pages:
            p.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Ignored)
            self.stack.addWidget(p)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)

        self._build_menu()
        self.statusBar().showMessage(f"Database: {store.path}")

    # ---------------------------------------------------------------
    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        a_imp = QAction("Import CSV…", self)
        a_imp.triggered.connect(self.open_import)
        a_bal = QAction("Import balance history (loan / investment)…", self)
        a_bal.triggered.connect(self.import_balance_history)
        a_demo = QAction("Load demo dataset", self)
        a_demo.triggered.connect(self._load_demo)
        a_reset = QAction("Reset all data…", self)
        a_reset.triggered.connect(self._reset)
        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self.close)
        for a in (a_imp, a_bal, a_demo, a_reset):
            m_file.addAction(a)
        m_file.addSeparator()
        m_file.addAction(a_quit)

        m_help = self.menuBar().addMenu("&Help")
        a_meth = QAction("Methodology", self)
        a_meth.triggered.connect(self._methodology)
        m_help.addAction(a_meth)
        a_about = QAction("About Doug", self)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_about)

    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self.pages[row].refresh()
        self.refresh_header()

    def refresh_current(self) -> None:
        self.pages[self.stack.currentIndex()].refresh()
        self.refresh_header()

    def refresh_header(self) -> None:
        st = headline_stats(self.store)
        self.header_nw.setText(
            f"net worth  {fmt_money(st['networth'])}" if st["networth"] else "")

    def status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 6000)

    # ---------------------------------------------------------------
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
            QMessageBox.information(
                self, "Create an account first",
                "Add an account on the Accounts page, then import its CSV "
                "export. Checking, savings, and credit-card accounts take a "
                "transaction export; loans and investments take a balance or "
                "activity export.")
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
        dlg = ImportDialog(self.store, self, account_id=account.id)
        if dlg.exec():
            self.refresh_current()

    def import_balance_history(self) -> None:
        """File → Import balance history: loans and investments."""
        targets = [a for a in self.store.accounts() if not a.type.is_cashlike]
        if not targets:
            QMessageBox.information(
                self, "No loan or investment accounts",
                "Add a loan or investment account on the Accounts page first. "
                "History for those accounts comes from dated balances — either "
                "listed directly, or worked out from an activity export.")
            return
        target = targets[0] if len(targets) == 1 else self._pick_import_account(targets)
        if target is not None:
            self._import_balance_history(target)

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

    def _load_demo(self) -> None:
        if self.store.accounts() or not self.store.transactions().empty:
            ok = QMessageBox.question(
                self, "Load demo",
                "This database already has data. Load the demo on top of it?\n"
                "(For a clean demo, use File → Reset all data first.)")
            if ok != QMessageBox.StandardButton.Yes:
                return
        from ..demo import load_demo
        try:
            info = load_demo(self.store)
        except Exception as e:
            QMessageBox.warning(self, "Demo failed", str(e))
            return
        self.status(f"Demo loaded — {info['transactions']} transactions, "
                    f"{info['recurring']} recurring patterns.")
        self.refresh_current()

    def _reset(self) -> None:
        ok = QMessageBox.question(
            self, "Reset all data",
            "Delete all accounts, transactions, patterns, and goals from this "
            "database? This cannot be undone.")
        if ok == QMessageBox.StandardButton.Yes:
            self.store.clear_all()
            self.refresh_current()
            self.status("Database cleared.")

    def _doug_says(self) -> None:
        from .splash import doug_fact
        box = QMessageBox(self)
        box.setWindowTitle("Doug says")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<p style='min-width:320px'>{doug_fact()}</p>")
        box.exec()

    def _about(self) -> None:
        from .. import __version__
        box = QMessageBox(self)
        box.setWindowTitle("About Doug")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<h3>Financing with Doug v{__version__}</h3>"
            f"<p>A local, private personal-finance modeling tool. All data "
            f"stays in a SQLite file on this machine.</p>"
            f"<p>Doug was originally a machine-learning model that was, by "
            f"every available metric, bad at its job. Doug has since been "
            f"rewritten more times than anyone involved would like to admit, "
            f"and now handles Monte Carlo simulation, recurrence detection and "
            f"adaptive categorisation. Doug is very proud of this. Doug would "
            f"like you to know that Doug still is not a financial advisor.</p>"
            f"<p style='color:#8B98A9'>See CHANGELOG.md in the app folder for "
            f"what changed between versions.</p>")
        box.exec()

    def _methodology(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Methodology")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(_METHODOLOGY)
        box.exec()
