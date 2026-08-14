"""Main window: sidebar navigation over stacked pages."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget,
                             QMainWindow, QMessageBox, QPushButton, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from ..history import headline_stats
from ..store import Store
from ..util import fmt_money
from . import prefs, theme
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
    def __init__(self, store: Store, real_db_path=None):
        super().__init__()
        self.store = store
        # Remembered so a demo can be a detour rather than a destination: the
        # window can always find its way back to the file it started on.
        self.real_db_path = Path(real_db_path or store.path)
        self.demo_key: str | None = None
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
        self.mode_btn = QPushButton("")
        self.mode_btn.setObjectName("small")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.clicked.connect(self._toggle_mode)

        # Undo lives in the header as well as on the menu. A menu item is
        # enough for someone who already knows the change can be taken back;
        # the point of a button is that it is *visible the moment there is
        # something to undo*, which is exactly when a person who has just
        # mistyped a balance is looking at the screen wondering what they did.
        # Hidden when the stack is empty, so it never reads as a live control
        # that does nothing.
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setObjectName("small")
        self.undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.undo_btn.setVisible(False)
        self.undo_btn.clicked.connect(self.undo)

        hl.addWidget(title)
        hl.addSpacing(6)
        hl.addWidget(ver)
        hl.addSpacing(10)
        hl.addWidget(tag)
        hl.addStretch(1)
        hl.addWidget(self.undo_btn)
        hl.addSpacing(10)
        hl.addWidget(self.mode_btn)
        hl.addSpacing(10)
        hl.addWidget(self.header_nw)
        outer.addWidget(header)

        self.banner = QLabel("")
        self.banner.setObjectName("demoBanner")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        outer.addWidget(self.banner)

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

        self._settings = prefs.app_settings()
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
        self.nav.setCurrentRow(0)
        self.statusBar().showMessage(f"Database: {store.path}")

    # ---------------------------------------------------------------
    def _toggle_mode(self) -> None:
        self.simple_mode = not self.simple_mode
        self._settings.setValue("simple_mode", self.simple_mode)
        self._apply_mode()
        self.status("Simple view — the advanced panels are hidden, and every "
                    "number they produce is still under For nerds."
                    if self.simple_mode else
                    "Everything shown.")

    def _apply_mode(self, refresh: bool = True) -> None:
        """Tell every page which face to wear.

        Pages opt in by defining ``set_simple``; the ones that are already a
        single task — Accounts, Transactions — have nothing to hide and simply
        do not implement it.
        """
        on = self.simple_mode
        self.mode_btn.setChecked(not on)
        self.mode_btn.setText("Simple view" if on else "Everything")
        self.mode_btn.setToolTip(
            "Showing the essentials. Click for the advanced panels — path "
            "counts, sensitivity, backtesting, checkpoint tables."
            if on else
            "Showing everything. Click to hide the advanced panels and keep "
            "the pages to what most people need.")
        for p in self.pages:
            fn = getattr(p, "set_simple", None)
            if fn is not None:
                try:
                    fn(on)
                except Exception:
                    pass          # a page that cannot simplify must still work
        if refresh:
            self.refresh_current()

    # ---------------------------------------------------------------
    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        a_imp = QAction("Import CSV…", self)
        a_imp.triggered.connect(self.open_import)
        a_bal = QAction("Import balance history (loan / investment)…", self)
        a_bal.triggered.connect(self.import_balance_history)
        for a in (a_imp, a_bal):
            m_file.addAction(a)
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

        m_help = self.menuBar().addMenu("&Help")
        a_guide = QAction("Getting started", self)
        a_guide.setShortcut("F1")
        a_guide.triggered.connect(self.show_guide)
        m_help.addAction(a_guide)
        m_help.addSeparator()
        a_update = QAction("Check for updates…", self)
        a_update.setToolTip("Ask GitHub whether a newer version has been "
                            "released, and install it if so")
        a_update.triggered.connect(self.check_for_updates)
        m_help.addAction(a_update)
        m_help.addSeparator()
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
        self._sync_undo()

    def status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 6000)

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
                        f"<p style='color:#8B98A9'>This opens in its own "
                        f"separate file. Your own data is untouched and one "
                        f"menu click away.</p>"
                        f"<p style='color:#8B98A9'>Building it takes a few "
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
        for p in self.pages:
            p.store = store
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
            return
        from ..demo import profile
        self.banner.setText(
            f"  DEMO DATA — “{profile(self.demo_key).name}”. Nothing here is "
            f"yours; your own data is untouched. "
            f"File → Return to my own data.")
        self.banner.setVisible(True)

    # ---------------- month in review ----------------
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
        what = ("this demo" if self.demo_key else "your")
        ok = QMessageBox.question(
            self, "Reset all data",
            f"Delete all accounts, transactions, patterns, and goals from "
            f"{what} database?\n\nCtrl+Z will bring it all back while this "
            f"window stays open — but once you close the app, it is gone.")
        if ok == QMessageBox.StandardButton.Yes:
            self.store.clear_all()
            self.refresh_current()
            self.status("Database cleared. Ctrl+Z undoes this.")

    def _doug_says(self) -> None:
        from .splash import doug_fact
        box = QMessageBox(self)
        box.setWindowTitle("Doug says")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<p style='min-width:320px'>{doug_fact()}</p>")
        box.exec()

    def show_guide(self) -> None:
        from .guide import show_guide
        show_guide(self)

    # ---------------- updates ----------------
    def check_for_updates(self) -> None:
        from .update_dialog import check_now
        check_now(self)

    def check_for_updates_quietly(self) -> None:
        """Called once after the window is up. Silent unless there is news."""
        from .update_dialog import check_quietly
        check_quietly(self)

    def _about(self) -> None:
        from .. import __version__
        box = QMessageBox(self)
        box.setWindowTitle("About Doug")
        box.setTextFormat(Qt.TextFormat.RichText)
        from .splash import AUTHOR
        box.setText(
            f"<h3>Financing with Doug v{__version__}</h3>"
            f"<p style='color:#8B98A9'>Created by <b>{AUTHOR}</b></p>"
            f"<p>A local, private personal-finance modeling tool. All data "
            f"stays in a SQLite file on this machine.</p>"
            f"<p>Doug was originally a machine-learning model that was, by "
            f"every available metric, bad at its job. Doug has since been "
            f"rewritten more times than anyone involved would like to admit, "
            f"and now handles Monte Carlo simulation, recurrence detection and "
            f"adaptive categorisation. Doug startd life on Caleb's computer "
            f"as a simple AI that couldn't even count correctly. Since then, "
            f"Doug has learned how to perform rocket calculations and even spent "
            f"a semester as glorified toaster and fitness coach.</p>"
            f"<p style='color:#8B98A9'>See CHANGELOG.md in the app folder for "
            f"what changed between versions.</p>")
        box.exec()

    def _methodology(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Methodology")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(_METHODOLOGY)
        box.exec()
