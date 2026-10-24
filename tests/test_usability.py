"""Regression checks for backups and the simplified desktop workflow."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PyQt6.QtCore import QDate, QEvent, QPoint, QSettings, Qt, QTimer
from PyQt6.QtGui import QFont, QFontDatabase, QValidator
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from finfore.models import Account, AccountType, Goal, OneOffEvent, RecurringItem
from finfore.calendar import upcoming_schedule
from finfore.recurrence import normalize_desc
from finfore.store import Store
from finfore.ui import prefs
from finfore.ui.main_window import MainWindow
from finfore.ui.widgets import MoneySpinBox
from finfore.ui import manual, video


def desktop_test_app():
    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen plugin does not enumerate system fonts. Without
    # registration, every glyph becomes a wide missing-character box and the
    # layout tests measure those boxes rather than the shipped interface.
    if sys.platform == "win32" and not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            path = fonts / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        if "Segoe UI" not in QFontDatabase.families():
            raise RuntimeError("Windows layout tests require the installed Segoe UI font.")
        app.setFont(QFont("Segoe UI", 9))
    return app


class CurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        self.spin = MoneySpinBox()
        self.spin.setRange(-1000000, 1000000)
        self.spin.setDecimals(2)
        self.spin.setPrefix("$ ")
        self.spin.setValue(100)
        self.spin.show()
        self.spin.setFocus()
        self.app.processEvents()
        self.spin.selectAll()

    def tearDown(self):
        self.spin.close()

    def type_amount(self, text):
        QTest.keyClicks(self.spin.lineEdit(), text)
        QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Return)
        return self.spin.value()

    def test_fixed_decimal_and_commit_not_per_digit(self):
        changes = []
        self.spin.valueChanged.connect(changes.append)
        QTest.keyClicks(self.spin.lineEdit(), "1234")
        self.assertEqual(self.spin.lineEdit().text(), "$ 12.34")
        self.assertEqual(self.spin.value(), 100)
        self.assertEqual(changes, [])
        QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(self.spin.value(), 12.34)
        self.assertEqual(changes, [12.34])

    def test_replace_negative_backspace_and_delete(self):
        self.assertEqual(self.type_amount("-1234"), -12.34)
        self.spin.lineEdit().deselect()
        QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Backspace)
        QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(self.spin.value(), -1.23)
        self.spin.selectAll()
        self.assertEqual(self.type_amount("500"), 5)
        QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Delete)
        QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Return)
        self.assertEqual(self.spin.value(), 0)

    def test_paste_digits_and_formatted_dollars(self):
        for text, expected in (("1234", 12.34), ("$1,234.56", 1234.56),
                               ("-12.34", -12.34), ("1,234", 1234)):
            self.spin.selectAll()
            self.app.clipboard().setText(text)
            QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClick(self.spin.lineEdit(), Qt.Key.Key_Return)
            self.assertEqual(self.spin.value(), expected, text)

    def test_programmatic_dollars_and_percentage_decimal_entry(self):
        self.spin.setValue(1234.56)
        self.assertEqual(self.spin.value(), 1234.56)
        self.spin.setPrefix("")
        self.spin.setSuffix(" %")
        self.spin.setDecimals(4)
        self.spin.selectAll()
        self.assertEqual(self.type_amount("6.4875"), 6.4875)
        for text in ("nan", "inf", "-Infinity", "1e999"):
            self.assertEqual(self.spin.validate(text, len(text))[0], QValidator.State.Invalid)

    def test_decimal_keys_and_maximum(self):
        self.assertEqual(self.type_amount("$1234.56"), 1234.56)
        self.spin.selectAll()
        self.spin.setMaximum(10)
        self.assertEqual(self.type_amount("99999"), 10)

    def test_focus_loss_commits_and_positive_only_rejects_minus(self):
        QTest.keyClicks(self.spin.lineEdit(), "1234")
        self.spin.clearFocus()
        self.app.processEvents()
        self.assertEqual(self.spin.value(), 12.34)
        self.spin.setMinimum(0)
        self.spin.selectAll()
        self.assertEqual(self.type_amount("-500"), 5)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "calendar.db")
        self.today = date(2026, 1, 30)
        self.checking = self.store.add_account(Account("Checking", AccountType.CHECKING,
            balance=1000, balance_date=date(2026, 1, 1)))
        self.card = self.store.add_account(Account("Card", AccountType.CREDIT,
            balance=-200, balance_date=self.today))
        self.store.add_transactions(pd.DataFrame([
            dict(account_id=self.checking, date=date(2026, 1, 15), description="PAYROLL",
                 amount=500, category="Income", import_hash="pay"),
            dict(account_id=self.checking, date=date(2026, 1, 20), description="RENT",
                 amount=-100, category="Housing", import_hash="rent"),
            dict(account_id=self.card, date=self.today, description="CARD BILL",
                 amount=-20, category="Utilities", import_hash="card"),
        ]))
        self.pattern("PAYROLL", 500, kind="income", day=31)
        self.pattern("RENT", -100, day=31)
        self.pattern("CARD BILL", -20, day=31)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def pattern(self, name, amount, kind="bill", day=31, active=True):
        item = RecurringItem(f"{normalize_desc(name)}|{'+' if amount > 0 else '-'}",
            name, "monthly", amount, date(2025, 12, 31), days_of_month=[day],
            active=active, kind=kind)
        self.store.merge_recurring([item])

    def test_month_end_dates_signed_balances_and_prior_month_flows(self):
        result = upcoming_schedule(self.store, date(2026, 2, 28), self.today)
        self.assertEqual(result.opening_balances, {self.checking: 1400, self.card: -200})
        self.assertEqual({e.when for e in result.entries}, {date(2026, 1, 31), date(2026, 2, 28)})
        self.assertEqual(len(result.entries), 6)
        self.assertEqual(result.closing_balances[self.checking], 2200)
        self.assertEqual(result.closing_balances[self.card], -240)
        self.assertEqual([e.balance_after for e in result.entries if e.label == "PAYROLL"], [1800, 2200])

    def test_unknown_and_ambiguous_accounts_are_not_invented(self):
        self.pattern("UNKNOWN", -40)
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.card,
            date=self.today, description="RENT", amount=-100, category="Housing", import_hash="other-rent")]))
        self.store.add_event(OneOffEvent("Repair", date(2026, 1, 31), -50))
        result = upcoming_schedule(self.store, date(2026, 1, 31), self.today)
        for entry in result.entries:
            if entry.label in ("RENT", "UNKNOWN", "Repair"):
                self.assertIsNone(entry.account_id)
                self.assertIsNone(entry.balance_after)
        self.assertEqual(result.closing_balances[self.checking], 1900)

    def test_inactive_ignored_and_today_are_excluded(self):
        self.pattern("Inactive", -99, active=False)
        self.pattern("Ignored", -99, kind="ignore")
        self.store.add_event(OneOffEvent("Today", self.today, -50))
        self.store.add_event(OneOffEvent("Disabled", date(2026, 1, 31), -50, active=False))
        result = upcoming_schedule(self.store, date(2026, 1, 31), self.today)
        self.assertEqual({e.label for e in result.entries}, {"PAYROLL", "RENT", "CARD BILL"})
        self.assertEqual(upcoming_schedule(self.store, self.today, self.today).entries, ())

    def test_latest_snapshot_and_transfer_not_used_for_assignment(self):
        self.store.add_snapshot(self.checking, date(2026, 1, 25), 2000)
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.checking,
            date=date(2026, 1, 26), description="TRANSFER", amount=-40,
            category="Transfer", import_hash="transfer")]))
        self.pattern("TRANSFER", -40)
        result = upcoming_schedule(self.store, date(2026, 1, 31), self.today)
        self.assertEqual(result.opening_balances[self.checking], 1960)
        transfer = next(e for e in result.entries if e.label == "TRANSFER")
        self.assertIsNone(transfer.account_id)


class VideoTests(unittest.TestCase):
    def test_frozen_build_resolves_its_bundled_video(self):
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / "assets"
            assets.mkdir()
            expected = assets / "monte_carlo.mp4"
            expected.touch()
            with patch.object(sys, "_MEIPASS", folder, create=True):
                self.assertEqual(video.video_path(), expected)

    def test_video_exists_and_opens_through_system_player(self):
        self.assertTrue(video.video_path().is_file())
        with patch.object(video.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.assertTrue(video.play())
            # Qt normalizes local URLs to forward slashes, including Windows
            # UNC paths. Compare filesystem paths, not their string spelling.
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), video.video_path())
        spec = (Path(__file__).resolve().parent.parent / "packaging/doug.spec").read_text()
        self.assertIn('("../assets/monte_carlo.mp4", "assets")', spec)

    def test_progress_video_is_bundled_and_uses_system_player(self):
        path = video.video_path("progress")
        self.assertTrue(path.is_file())
        with patch.object(video.QDesktopServices, "openUrl", return_value=True) as opener:
            self.assertTrue(video.play(kind="progress"))
            self.assertEqual(Path(opener.call_args.args[0].toLocalFile()), path)
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / "assets"
            assets.mkdir()
            expected = assets / "progress.mp4"
            expected.touch()
            with patch.object(sys, "_MEIPASS", folder, create=True):
                self.assertEqual(video.video_path("progress"), expected)
        spec = (Path(__file__).resolve().parent.parent / "packaging/doug.spec").read_text()
        self.assertIn('("../assets/progress.mp4", "assets")', spec)
        with patch.object(video, "video_path", return_value=None), \
                patch.object(video.QMessageBox, "warning") as warning:
            self.assertFalse(video.play(kind="progress"))
            self.assertIn("progress", warning.call_args.args[2])

    def test_missing_video_and_missing_player_report_failure(self):
        with patch.object(video, "video_path", return_value=None), \
                patch.object(video.QMessageBox, "warning") as warning:
            self.assertFalse(video.play())
            warning.assert_called_once()
        with patch.object(video.QDesktopServices, "openUrl", return_value=False), \
                patch.object(video.QMessageBox, "warning") as warning:
            self.assertFalse(video.play())
            self.assertIn(str(video.video_path()), warning.call_args.args[2])


class ManualTests(unittest.TestCase):
    def test_source_manual_is_a_pdf_and_opens_in_the_system_reader(self):
        path = manual.manual_path()
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        with patch.object(manual.QDesktopServices, "openUrl", return_value=True) as opener:
            self.assertTrue(manual.open_manual())
            url = opener.call_args.args[0]
            self.assertTrue(url.isLocalFile())
            self.assertEqual(Path(url.toLocalFile()), path)

    def test_frozen_installation_opens_its_own_manual_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder) / "Doug & family é"
            expected = bundle / "docs" / "manual" / "financing_with_doug_manual.pdf"
            expected.parent.mkdir(parents=True)
            expected.write_bytes(manual.manual_path().read_bytes())
            with patch.object(sys, "_MEIPASS", str(bundle), create=True), \
                    patch.object(manual.QDesktopServices, "openUrl", return_value=True) as opener:
                self.assertTrue(manual.open_manual())
                self.assertEqual(Path(opener.call_args.args[0].toLocalFile()), expected)

    def test_missing_manual_reports_the_problem_without_opening_a_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys, "_MEIPASS", folder, create=True), \
                    patch.object(manual.QDesktopServices, "openUrl") as opener, \
                    patch.object(manual.QMessageBox, "warning") as warning:
                self.assertFalse(manual.open_manual())
                opener.assert_not_called()
                self.assertIn("missing", warning.call_args.args[2])

    def test_reader_failure_provides_the_file_to_open_directly(self):
        with patch.object(manual.QDesktopServices, "openUrl", return_value=False), \
                patch.object(manual.QMessageBox, "warning") as warning:
            self.assertFalse(manual.open_manual())
            self.assertIn(str(manual.manual_path()), warning.call_args.args[2])


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "source.db")
        self.store.con.execute("PRAGMA journal_mode=WAL")
        self.store.con.execute("PRAGMA wal_autocheckpoint=0")
        self.store.add_account(Account("Checking", AccountType.CHECKING,
                                       balance=1000, balance_date=date.today()))
        self.store.add_transactions(pd.DataFrame([{
            "account_id": 1, "date": date.today(), "description": "Groceries",
            "amount": -20, "category": "Groceries", "import_hash": "backup-row",
        }]))
        self.store.add_snapshot(1, date.today(), 1000)
        self.store.add_goal(Goal("Buffer", 2000))
        self.store.add_event(OneOffEvent("Repair", date.today() + timedelta(days=10), -500))
        self.store.set_setting("plan_policy", "weighted")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_backup_includes_committed_wal_and_all_tables(self):
        self.assertTrue(Path(str(self.store.path) + "-wal").exists())
        target = self.store.backup_to(self.root / "backup.db")
        with sqlite3.connect(target) as backup:
            tables = self.store.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for table in tables:
                name = table[0]
                self.assertEqual(
                    [tuple(row) for row in self.store.con.execute(f'SELECT * FROM "{name}"')],
                    backup.execute(f'SELECT * FROM "{name}"').fetchall())
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("PRAGMA foreign_key_check").fetchall(), [])
        backup.close()
        restored = Store(target)
        try:
            self.assertEqual(restored.accounts()[0].balance, 1000)
            self.assertEqual(len(restored.transactions()), 1)
            self.assertEqual(restored.events()[0].amount, -500)
        finally:
            restored.close()

    def test_live_database_and_alias_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            self.store.backup_to(self.store.path)
        alias = self.root / "alias.db"
        os.link(self.store.path, alias)
        with self.assertRaises(ValueError):
            self.store.backup_to(alias)
        self.assertEqual(self.store.accounts()[0].name, "Checking")

    def test_failed_write_preserves_previous_backup(self):
        target = self.root / "backup.db"
        self.store.backup_to(target)
        before = target.read_bytes()
        self.store.add_goal(Goal("New goal", 3000))
        with patch("finfore.store.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.backup_to(target)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".doug-backup-*")), [])

    def test_open_wal_destination_is_rejected(self):
        other = Store(self.root / "other.db")
        try:
            other.con.execute("PRAGMA journal_mode=WAL")
            other.add_goal(Goal("Do not overwrite", 100))
            with self.assertRaises(ValueError):
                self.store.backup_to(other.path)
            self.assertEqual(other.goals()[0].name, "Do not overwrite")
        finally:
            other.close()


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prefs_patch = patch.object(
            prefs, "app_settings",
            lambda: QSettings(str(self.root / "prefs.ini"), QSettings.Format.IniFormat))
        self.prefs_patch.start()
        self.slot_errors = []
        self.hook_patch = patch.object(
            sys, "excepthook", lambda kind, exc, tb: self.slot_errors.append(exc))
        self.hook_patch.start()
        self.win = MainWindow(Store(self.root / "mine.db"))
        self.win.show()
        self.app.processEvents()
        self.fc = next(p for p in self.win.pages if type(p).__name__ == "ForecastPage")

    def tearDown(self):
        self.win.close()
        # Finish zero-delay editor/focus callbacks while their widgets and
        # database are still alive, then dispose of the native window.
        QTest.qWait(1)
        self.win.store.close()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.prefs_patch.stop()
        self.hook_patch.stop()
        self.temp.cleanup()
        self.assertEqual(self.slot_errors, [], "Unexpected exception in a Qt callback")

    def add_account(self):
        self.win.store.add_account(Account("Checking", AccountType.CHECKING,
                                           balance=2000, balance_date=date.today()))

    def test_explanation_buttons_open_the_correct_videos(self):
        with patch.object(video, "play", return_value=True) as player:
            self.fc.start_video_btn.click()
            player.assert_called_once_with(self.fc)
            player.reset_mock()
            self.win.show_page("Progress")
            progress = self.win.current_page()
            progress.video_action.trigger()
            player.assert_called_once_with(progress, kind="progress")

    def test_help_menu_opens_the_bundled_manual(self):
        help_menu = next(a.menu() for a in self.win.menuBar().actions()
                         if a.text().replace("&", "") == "Help")
        action = next(a for a in help_menu.actions() if a.text() == "User manual (PDF)")
        with patch.object(manual.QDesktopServices, "openUrl", return_value=True) as opener:
            action.trigger()
            self.assertEqual(Path(opener.call_args.args[0].toLocalFile()), manual.manual_path())

    def test_spending_help_opens_category_and_transaction_evidence(self):
        from finfore.ui.spending_details import SpendingDetailsDialog
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=date.today().replace(day=1)-timedelta(days=2),
                 amount=5000, category="Income", description="Paycheck", import_hash="details-income"),
            dict(account_id=1, date=date.today().replace(day=1)-timedelta(days=1),
                 amount=-2000, category="Housing", description="Apartment rent", import_hash="details-rent"),
            dict(account_id=1, date=date.today().replace(day=1)-timedelta(days=1),
                 amount=50, category="Housing", description="Rent credit <not html>", import_hash="details-credit"),
        ]))
        self.win.show_page("Spending")
        spending = self.win.current_page()
        opened = []
        def capture_dialog(dialog):
            opened.append(dialog)
            return 0
        with patch.object(SpendingDetailsDialog, "exec", capture_dialog):
            spending.how_btn.click()
            self.assertEqual(len(opened), 1)
            dialog = opened[0]
            self.assertEqual(dialog.tabs.currentIndex(), 0)
            essentials = dialog.tree.topLevelItem(1)
            self.assertEqual(essentials.text(0), "Needs")
            self.assertEqual(essentials.text(3), "$1,950.00")
            housing = essentials.child(0)
            self.assertEqual(housing.childCount(), 0)  # load transaction children on demand
            housing.setExpanded(True)
            self.assertEqual(housing.childCount(), 2)
            self.assertEqual(sum(housing.child(i).data(3, Qt.ItemDataRole.UserRole)
                                 for i in range(2)), 1950)
            self.assertTrue(any("<not html>" in housing.child(i).text(0) for i in range(2)))
            self.assertIn("100.0000%", housing.child(0).toolTip(0))
            dialog.close()

    def test_transaction_running_balances_keep_hidden_rows_in_calculation(self):
        aid = self.win.store.add_account(Account("Checking", AccountType.CHECKING,
                                                500, date(2026, 9, 3)))
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=aid, date=date(2026, 9, 1), description="Early purchase", amount=-250,
                 category="Shopping", import_hash="debug-purchase"),
            dict(account_id=aid, date=date(2026, 9, 1), description="Paycheck", amount=1000,
                 category="Income", import_hash="debug-pay"),
            dict(account_id=aid, date=date(2026, 9, 2), description="Business flight", amount=-100,
                 category="Business (reimbursable)", import_hash="debug-business"),
            dict(account_id=aid, date=date(2026, 9, 3), description="Transfer", amount=-350,
                 category="Transfer", import_hash="debug-transfer"),
        ]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        self.assertTrue(page.table.isColumnHidden(5))
        page.f_since.setCurrentIndex(page.f_since.findData(None))
        page.show_balances.setChecked(True)
        self.assertFalse(page.table.isColumnHidden(5))
        page.search.setText("Early purchase")
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 5).data(Qt.ItemDataRole.UserRole), -50)
        self.assertIn("Before: $200.00", page.table.item(0, 5).toolTip())
        self.assertIn("below $0", page.table.item(0, 5).toolTip())
        page.search.clear()
        page.negative_only.setChecked(True)
        # This day's close is positive; unknown intraday order must not be
        # presented as a confirmed negative daily balance.
        self.assertEqual(page.table.rowCount(), 0)
        page.negative_only.setChecked(False)
        page.balance_order.setCurrentIndex(page.balance_order.findData("oldest"))
        self.assertEqual(page.table.item(0, 2).text(), "Early purchase")
        self.assertEqual(page.table.item(3, 5).data(Qt.ItemDataRole.UserRole), 500)
        page.show_balances.setChecked(False)
        self.assertTrue(page.table.isColumnHidden(5))
        self.assertFalse(page.negative_only.isVisibleTo(page))

    def test_dashboard_selected_account_lines_preserve_household_totals(self):
        checking = self.win.store.add_account(Account("Checking", AccountType.CHECKING, 1000, date.today()))
        invest = self.win.store.add_account(Account("401k", AccountType.INVESTMENT, 20000, date.today()))
        savings = self.win.store.add_account(Account("Savings", AccountType.SAVINGS, 5000, date.today()))
        self.win.store.add_snapshot(invest, date.today()-timedelta(days=30), 19000)
        self.win.show_page("Dashboard")
        page = self.win.current_page()
        page.refresh()
        before = page.card_nw.value.text()
        self.win.store.undo.clear()
        page.history_view.setCurrentIndex(page.history_view.findData("accounts"))
        page.history_account_actions[savings].setChecked(False)
        self.assertEqual(set(page._hist_data), {f"account:{checking}", f"account:{invest}"})
        self.assertEqual(set(page._account_history.columns), {checking, invest})
        self.assertEqual(page.card_nw.value.text(), before)
        self.assertFalse(self.win.store.undo.can_undo)
        self.assertEqual(page._hist_data[f"account:{checking}"][-1], 1000)
        self.assertEqual(page._hist_data[f"account:{invest}"][-1], 20000)
        self.assertEqual([item[0] for item in page.plot_hist._readout.series], ["Checking", "401k"])
        page.refresh()
        self.assertEqual(page._selected_account_ids, {checking, invest})
        self.assertEqual(page._history_mode, "accounts")
        page.history_view.setCurrentIndex(page.history_view.findData("totals"))
        self.assertEqual(page._hist_data["networth"][-1], 21000)
        self.assertEqual(page.card_nw.value.text(), before)
        page._set_chart_accounts(set())
        self.assertFalse(page._hist_data)
        self.assertIsNone(page.plot_hist._readout)
        self.assertIn("Select one or more", page.hist_note.text())
        self.assertTrue(page.hist_empty.isVisibleTo(page))
        page._set_chart_accounts(None)
        self.assertEqual(page._hist_data["networth"][-1], 26000)

    def test_transactions_duplicate_review_respects_selected_account(self):
        self.add_account()
        self.win.show_page("Transactions")
        page = self.win.current_page()
        page.f_account.setCurrentIndex(page.f_account.findData(1))
        with patch("finfore.ui.accounts.DuplicateDialog") as dialog:
            dialog.return_value.exec.return_value = 0
            page.duplicates_btn.trigger()
            dialog.assert_called_once_with(self.win.store, page, account_id=1)

    def test_dashboard_chart_selection_is_scoped_to_each_database(self):
        self.add_account()
        page = self.win.pages[0]
        page.refresh()
        page.history_view.setCurrentIndex(page.history_view.findData("accounts"))
        page._set_chart_accounts(set())
        other = Store(self.root / "other.db")
        other.add_account(Account("Other checking", AccountType.CHECKING, 800, date.today()))
        self.win._switch_store(other, None)
        page = self.win.pages[0]
        page.refresh()
        self.assertEqual(page._history_mode, "totals")
        self.assertIsNone(page._selected_account_ids)
        self.assertFalse(page.hist_empty.isVisibleTo(page))
        self.assertEqual(page._hist_data["networth"][-1], 800)

    def test_account_debug_options_fit_minimum_window(self):
        from finfore.ui import theme
        previous = self.app.styleSheet()
        theme.apply(self.app)
        self.addCleanup(self.app.setStyleSheet, previous)
        self.add_account()
        self.win.resize(980, 620)
        self.win.show_page("Transactions")
        page = self.win.current_page()
        page.show_balances.setChecked(True)
        QTest.qWait(50)
        self.assertTrue(page.show_balances.isChecked())
        self.assertIn(page.duplicates_btn, page.tools_menu.actions())
        for widget in (page.negative_only, page.balance_order):
            self.assertTrue(widget.isVisibleTo(page))
            self.assertGreaterEqual(widget.height(), widget.sizeHint().height())
            self.assertLessEqual(widget.mapTo(page, QPoint(widget.width(), 0)).x(), page.width())
        self.assertGreater(page.table.height(), 160)
        self.assertGreaterEqual(page.table.columnWidth(2), 250)
        self.win.show_page("Dashboard")
        dash = self.win.current_page()
        QTest.qWait(50)
        for widget in (dash.history_view, dash.history_accounts_btn):
            self.assertGreaterEqual(widget.height(), widget.sizeHint().height())
            self.assertLessEqual(widget.mapTo(dash.hist_card, QPoint(widget.width(), 0)).x(), dash.hist_card.width())

    def test_spending_plan_budget_is_shared_with_goals_and_undoable(self):
        self.add_account()
        self.win.store.add_goal(Goal("Vacation", 2000, "savings"))
        self.win.show_page("Plan")
        page = self.win.current_page()
        self.assertIn("one complete month of your statements", page.setup.text())
        self.assertEqual(page.verdict.text(), "Monthly plan not ready")
        self.assertEqual(page.table.item(0, 1).text(), "—")
        self.assertFalse(page.save_btn.isEnabled())
        for key in ("debt", "frivolous", "giving", "unknown"):
            page.checks[key].setChecked(True)
        page.checks["income"].setChecked(True)
        page.boxes["income"].setValue(4000)
        page.checks["essential"].setChecked(True)
        page.boxes["essential"].setValue(2000)
        self.win.store.set_setting("plan_surplus", 600)
        page.refresh()
        self.assertIn("1,400.00", page.verdict.text())
        self.assertIn("Unsaved", page.state.text())
        self.win.show_page("Accounts")
        self.win.show_page("Plan")
        self.assertEqual(page.boxes["income"].value(), 4000)
        page.save_btn.click()
        self.win.show_page("Goals")
        goals = self.win.current_page()
        self.assertEqual(goals.surplus.value(), 600)
        self.assertIn("Monthly Plan", goals.surplus_note.text())
        self.assertIn("2,000.00 available", goals.surplus_note.text())
        self.win.show_page("Plan")
        self.win.undo()
        self.assertIsNone(self.win.store.get_setting("spending_plan"))
        self.assertEqual(page.boxes["income"].value(), 0)
        self.win.redo()
        self.assertEqual(page.boxes["income"].value(), 4000)

    def test_goal_activity_records_actual_funding_without_bank_writes(self):
        from finfore.ui.goal_funding import GoalFundingDialog
        self.add_account()
        gid = self.win.store.add_goal(Goal("Computer", 2000, "savings"))
        self.win.show_page("Plan")
        page = self.win.current_page()
        self.assertTrue(page.activity_btn.isEnabled())
        dlg = GoalFundingDialog(self.win.store, gid, self.win)
        dlg.account.setCurrentIndex(dlg.account.findData(1))
        dlg.amount.setValue(100000)
        dlg._record()
        self.assertIn("exceeds the goal target", dlg.error.text())
        self.assertEqual(dlg.amount.value(), 100000)
        dlg.amount.setValue(250)
        dlg._record()
        self.assertEqual(dlg.table.rowCount(), 1)
        self.assertNotIn("E06C5B", dlg.error.styleSheet())
        self.assertEqual(self.win.store.goals()[0].saved_amount, 250)
        self.assertEqual(len(self.win.store.transactions()), 0)
        dlg.close()
        page.refresh()
        self.assertEqual(page.goal_table.item(0, 2).text(), "$250.00")
        self.assertEqual(page.goal_table.item(0, 3).text(), "$250.00")
        self.win.undo()
        self.assertEqual(page.goal_table.item(0, 2).text(), "$0.00")

    def test_plan_minimum_size_and_database_switch_clear_drafts(self):
        self.win.resize(980, 620)
        self.win.show_page("Plan")
        page = self.win.current_page()
        self.app.processEvents()
        self.assertLessEqual(page.scroll.widget().width(), page.scroll.viewport().width())
        self.assertTrue(page.save_btn.isVisibleTo(self.win))
        page.checks["income"].setChecked(True)
        page.boxes["income"].setValue(1234)
        other = Store(self.root / "other-plan.db")
        self.win._switch_store(other, None)
        self.assertIs(page.store, other)
        self.assertEqual(page.boxes["income"].value(), 0)
        self.assertFalse(page._dirty)

    def test_sections_and_nested_pages_refresh(self):
        self.assertEqual([self.win.nav.item(i).text() for i in range(self.win.nav.count())],
                         ["Home", "Accounts", "Bills & payday", "Plan", "Forecast"])
        for name, page_type, parent in (("Spending", "InsightsPage", "Home"),
                ("Recurring", "RecurringPage", "Bills & payday"),
                ("Progress", "ProgressPage", "Forecast"),
                ("Calendar", "CalendarPage", "Bills & payday"),
                ("Transactions", "TransactionsPage", "Accounts"),
                ("Balances", "AccountsPage", "Accounts"),
                ("Accounts", "AccountsPage", "Accounts"),
                ("Goals", "GoalsPage", "Plan"),
                ("Monthly budget", "PlanPage", "Plan"),
                ("Plan", "PlanPage", "Plan")):
            self.win.show_page(name)
            self.assertEqual(type(self.win.current_page()).__name__, page_type)
            self.assertEqual(self.win.nav.currentItem().text(), parent)
        self.win.show_page("Dashboard")
        self.win.dashboard_tabs.setCurrentIndex(0)
        spending = self.win.pages[4]
        with patch.object(spending, "refresh") as refresh:
            self.win.dashboard_tabs.setCurrentWidget(spending)
            refresh.assert_called_once()

    def test_forecast_toolbar_and_legend_fit_at_minimum_size(self):
        self.add_account()
        self.win.resize(980, 620)
        self.win.show_page("Forecast")
        self.win._toggle_mode()
        # Nested splitter sizing takes several queued layout passes on Windows.
        # Wait for the actual geometry, rather than assuming 50 ms is enough
        # while the full suite is running. A persistent overlap still fails.
        for _ in range(40):
            QTest.qWait(25)
            chart_bottom = self.fc.plot_cash.mapTo(
                self.fc.cash_card, QPoint(0, self.fc.plot_cash.height())).y()
            legend_top = self.fc.cash_legend.mapTo(self.fc.cash_card, QPoint(0, 0)).y()
            if (self.fc.chart_split.height() >= self.fc.chart_split.minimumHeight()
                    and legend_top >= chart_bottom):
                break
        self.assertGreaterEqual(self.fc.chart_split.height(), self.fc.chart_split.minimumHeight())
        for button in (self.fc.explain_btn, self.fc.reset_btn):
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
        chart_bottom = self.fc.plot_cash.mapTo(self.fc.cash_card, QPoint(0, self.fc.plot_cash.height())).y()
        legend_top = self.fc.cash_legend.mapTo(self.fc.cash_card, QPoint(0, 0)).y()
        self.assertGreaterEqual(legend_top, chart_bottom)

    def test_forecast_empty_start_opens_account_editor_without_leaving_forecast(self):
        self.win.show_page("Forecast")
        page = self.fc
        self.assertTrue(page.start_panel.isVisibleTo(page))
        self.assertFalse(page.forecast_split.isVisibleTo(page))
        self.assertFalse(page.plot_cash.isVisibleTo(page))
        self.assertFalse(page.income_slider.isVisibleTo(page))
        self.assertFalse(page.run_btn.isEnabled())
        with patch("finfore.ui.accounts.AccountDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            page.start_account_btn.click()
        self.assertEqual(self.win.store.accounts(), [])
        self.assertTrue(page.start_panel.isVisibleTo(page))
        account = Account("First checking", AccountType.CHECKING,
                          balance=1500, balance_date=date.today())
        with patch("finfore.ui.accounts.AccountDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.get_account.return_value = account
            page.start_account_btn.setFocus()
            QTest.keyClick(page.start_account_btn, Qt.Key.Key_Space)
        self.assertIs(self.win.current_page(), page)
        self.assertEqual(self.win.store.accounts()[0].name, "First checking")
        self.assertFalse(page.start_panel.isVisibleTo(page))
        self.assertTrue(page.forecast_split.isVisibleTo(page))
        self.assertTrue(page.setup_import_btn.isVisibleTo(page))
        self.assertIn("Everyday spending is omitted", page.setup_description.text())
        # One account and no statements: good news is held back, and the page says why.
        self.assertEqual(page.risk.text(), "Too early to say where your cash is headed.")
        self.assertIn("fewer than four weeks of statements", page.risk_detail.text())

    def test_forecast_demo_opens_separate_ledger_and_returns_to_empty_setup(self):
        self.win.show_page("Forecast")
        page = self.fc
        real_path = self.win.store.path
        page.start_demo_btn.click()
        self.assertEqual(self.win.demo_key, "balanced")
        self.assertNotEqual(self.win.store.path, real_path)
        self.assertIs(self.win.current_page(), page)
        self.assertIsNotNone(page._res)
        self.assertFalse(page.start_panel.isVisibleTo(page))
        self.win.return_to_real()
        self.assertIsNone(self.win.demo_key)
        self.assertEqual(self.win.store.path, real_path)
        self.assertEqual(self.win.store.accounts(), [])
        self.assertTrue(page.start_panel.isVisibleTo(page))
        self.assertIsNone(page._res)

    def test_forecast_import_guidance_uses_only_transaction_accounts(self):
        checking = Account("Checking", AccountType.CHECKING,
                           balance=1000, balance_date=date.today())
        savings = Account("Savings", AccountType.SAVINGS,
                          balance=2000, balance_date=date.today())
        loan = Account("Loan", AccountType.LOAN, balance=5000, balance_date=date.today())
        for account in (checking, savings, loan):
            self.win.store.add_account(account)
        self.win.show_page("Forecast")
        with patch.object(self.win, "_pick_import_account", return_value=checking) as picker, \
             patch.object(self.win, "import_for_account") as importer:
            self.fc.setup_import_btn.click()
        self.assertEqual([a.name for a in picker.call_args.args[0]], ["Checking", "Savings"])
        importer.assert_called_once_with(checking)
        with patch.object(self.win, "_pick_import_account", return_value=None), \
             patch.object(self.win, "import_for_account") as importer:
            self.fc.setup_import_btn.click()
        importer.assert_not_called()

    def test_forecast_loan_only_setup_keeps_existing_projection_available(self):
        self.win.store.add_account(Account("Loan", AccountType.LOAN,
            balance=5000, balance_date=date.today(), monthly_payment=200))
        self.win.show_page("Forecast")
        page = self.fc
        self.assertTrue(page.forecast_split.isVisibleTo(page))
        self.assertIsNotNone(page._res)
        self.assertTrue(page.setup_account_btn.isVisibleTo(page))
        self.assertFalse(page.setup_import_btn.isVisibleTo(page))
        self.assertIn("credit-card", page.setup_description.text())

    def test_forecast_short_history_guidance_reveals_controls_and_recovers(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        page.history_pick.setCurrentIndex(page.history_pick.findData("since"))
        page.history_since.setDate(QDate.currentDate().addDays(-3))
        page.run()
        self.assertLess(page._res.history_weeks, 4)
        self.assertTrue(page.setup_guidance.isVisibleTo(page))
        self.assertTrue(page.setup_history_btn.isVisibleTo(page))
        page.setup_history_btn.click()
        QTest.qWait(40)
        self.assertFalse(self.win.simple_mode)
        self.assertTrue(page.history_card.isVisibleTo(page))
        self.assertTrue(page.history_pick.isVisibleTo(page))
        self.assertTrue(page.history_pick.hasFocus())
        page.history_pick.setCurrentIndex(page.history_pick.findData("all"))
        page.run()
        self.assertGreaterEqual(page._res.history_weeks, 4)
        self.assertFalse(page.setup_guidance.isVisibleTo(page))

    def test_forecast_comparison_tip_is_optional_and_remembers_dismissal(self):
        from finfore.demo import load_demo
        from finfore.ui.forecast_page import _COMPARISON_TIP_SETTING
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        page.open_controls()            # in Simple view the controls are a drawer
        self.assertTrue(page.comparison_tip.isVisibleTo(page))
        page.dismiss_tip_btn.click()
        self.assertFalse(page.comparison_tip.isVisibleTo(page))
        self.assertTrue(prefs.app_settings().value(_COMPARISON_TIP_SETTING, False, type=bool))
        page.refresh()
        self.assertFalse(page.comparison_tip.isVisibleTo(page))
        from finfore.ui.forecast_page import ForecastPage
        another = ForecastPage(self.win.store, self.win)
        self.assertTrue(another._comparison_tip_seen)
        another.deleteLater()

    def test_forecast_first_successful_spending_comparison_finishes_tip(self):
        from finfore.demo import load_demo
        from finfore.ui import forecast_page
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        category = next(row for row in page._cat_sliders
                        if page._res.monthly_spending_reference.get(row.name, 0) > 0)
        category.slider.setValue(80)
        with patch.object(forecast_page, "run_forecast", side_effect=RuntimeError("failed")):
            page.run()
        self.assertFalse(page._comparison_tip_seen)
        page.run()
        self.assertTrue(page.cash_compare.isVisibleTo(page))
        self.assertTrue(page._comparison_tip_seen)
        self.assertFalse(page.comparison_tip.isVisibleTo(page))
        page._reset_scenario()
        page.run()
        self.assertFalse(page.comparison_tip.isVisibleTo(page))

    def test_forecast_verdict_and_next_action_stay_visible_while_charts_scroll(self):
        from finfore.ui import theme
        previous = self.app.styleSheet()
        theme.apply(self.app)
        self.addCleanup(self.app.setStyleSheet, previous)
        self.win.store.add_account(Account("Checking", AccountType.CHECKING,
            balance=-5000, balance_date=date.today()))
        self.win.show_page("Forecast")
        page = self.fc
        self.assertTrue(page.cause_btn.isVisible())
        self.assertIn(page._res.shortfall.headline, page.risk.text())
        if page._res.history_weeks < 4:
            self.assertIn("Day-to-day spending is omitted", page.risk.text())
        for warning in page._res.warnings:
            self.assertIn(warning, page.forecast_warning.text())
            self.assertTrue(page.forecast_warning.isVisible())
        page.open_controls()
        page.event_card.set_expanded(True)
        for width, height in ((980, 620), (1280, 900)):
            self.win.resize(width, height)
            QTest.qWait(40)
            for widget in (page.risk, page.cause_btn, page.risk_toggle):
                origin = widget.mapTo(self.win, QPoint(0, 0))
                self.assertGreaterEqual(origin.x(), 0)
                self.assertGreaterEqual(origin.y(), 0)
                self.assertLessEqual(origin.x() + widget.width(), self.win.width())
                self.assertLessEqual(origin.y() + widget.height(), self.win.height())
            before = page.risk.mapTo(self.win, QPoint(0, 0))
            scrollbar = page.chart_scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            QTest.qWait(20)
            self.assertEqual(page.risk.mapTo(self.win, QPoint(0, 0)), before)
            control_scroll = page.event_card.parentWidget().parentWidget().parentWidget()
            viewport = control_scroll.viewport()
            for widget in (page.e_name, page.e_amount, page.e_when, page.add_event_btn,
                           page.income_slider.pct):
                left = widget.mapTo(viewport, QPoint(0, 0)).x()
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(left + widget.width(), viewport.width())
            self.assertGreater(page.e_name.width(), 150)
        page.risk_toggle.click()
        self.assertTrue(page.risk_details.isVisible())
        self.assertIn(page._res.shortfall.detail, page.risk_detail.text())
        page.risk_toggle.click()
        self.assertTrue(page.risk_details.isHidden())
        page.view_pick.setCurrentIndex(page.view_pick.findData("networth"))
        self.assertTrue(page.risk_panel.isHidden())
        page.view_pick.setCurrentIndex(page.view_pick.findData("cash"))
        self.assertTrue(page.risk_panel.isVisible())

    def test_forecast_controls_fit_when_splitter_is_narrowed(self):
        from finfore.ui import theme
        previous = self.app.styleSheet()
        theme.apply(self.app)
        self.addCleanup(self.app.setStyleSheet, previous)
        self.add_account()
        category = "Household supplies, maintenance and occasional home repairs"
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1,
            date=date.today(), description="Home repair", amount=-100, category=category,
            import_hash="narrow-forecast-category")]))
        self.win.resize(980, 620)
        self.win.show_page("Forecast")
        self.win._toggle_mode()
        page = self.fc
        page.history_card.set_expanded(True)
        page.history_pick.setCurrentIndex(page.history_pick.findData("since"))
        page.debt_card.set_expanded(True)
        page.event_card.set_expanded(True)
        page._update_timer.stop()
        page.layout().itemAt(0).widget().setSizes([240, 700])
        QTest.qWait(50)
        scroll = page.event_card.parentWidget().parentWidget().parentWidget()
        viewport = scroll.viewport()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0,
            f"viewport={viewport.width()}, contents={scroll.widget().width()}, "
            f"event minimum={page.event_card.minimumSizeHint().width()}, "
            f"repeat minimum={page.e_repeat.minimumSizeHint().width()}")
        for widget in (page.overlay, page.paths, page.ef_months, page.history_pick,
                       page.history_since, page.history_refit, page.snowball,
                       page.extra_loan, page.extra_invest, page.e_name, page.e_amount,
                       page.e_when, page.add_event_btn, page.income_slider.pct):
            left = widget.mapTo(viewport, QPoint(0, 0)).x()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(left + widget.width(), viewport.width())
        row = next(s for s in page._cat_sliders if s.name == category)
        self.assertGreaterEqual(row.label.height(), row.label.heightForWidth(row.label.width()))
        self.assertGreaterEqual(row.slider.width(), 100)
        self.assertGreater(row.slider.y(), row.label.y())
        row.slider.setValue(75)
        self.assertEqual(row.pct.text(), "75%")
        self.assertEqual(row.value(), .75)
        from PyQt6.QtWidgets import QStyle, QStyleOptionSlider
        scroll.ensureWidgetVisible(row.slider)
        QTest.qWait(20)
        option = QStyleOptionSlider()
        row.slider.initStyleOption(option)
        handle = row.slider.style().subControlRect(QStyle.ComplexControl.CC_Slider,
            option, QStyle.SubControl.SC_SliderHandle, row.slider)
        target = QPoint(row.slider.width() - 12, handle.center().y())
        QTest.mousePress(row.slider, Qt.MouseButton.LeftButton, pos=handle.center())
        QTest.mouseMove(row.slider, target, delay=20)
        QTest.mouseRelease(row.slider, Qt.MouseButton.LeftButton, pos=target)
        self.assertGreater(row.slider.value(), 75)
        self.assertEqual(row.pct.text(), f"{row.slider.value()}%")
        # The whole wrapped caption toggles the original native checkbox;
        # keyboard behavior and its accessible text remain intact.
        scroll.ensureWidgetVisible(page.snowball)
        QTest.qWait(20)
        checked = page.snowball.isChecked()
        QTest.mouseClick(page.snowball, Qt.MouseButton.LeftButton,
                         pos=QPoint(page.snowball.width() - 8, page.snowball.height() - 4))
        self.assertEqual(page.snowball.isChecked(), not checked)
        page.snowball.setFocus()
        QTest.keyClick(page.snowball, Qt.Key.Key_Space)
        self.assertEqual(page.snowball.isChecked(), checked)
        self.assertEqual(page.snowball.text(), "Roll freed payments into the next loan")
        page._update_timer.stop()
        self.win.resize(1280, 900)
        page.layout().itemAt(0).widget().setSizes([356, 900])
        QTest.qWait(50)
        legend_bottom = page.cash_legend.mapTo(page.chart_scroll.viewport(),
            QPoint(0, page.cash_legend.height())).y()
        self.assertLessEqual(legend_bottom, page.chart_scroll.viewport().height())
        label = page.data_quality.status
        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.assertLessEqual(label.y() + label.height(), page.data_quality.height())
        page.view_pick.setCurrentIndex(page.view_pick.findData("analysis"))
        page._set_analysis("backtest")
        self.win.resize(980, 620)
        page.layout().itemAt(0).widget().setSizes([450, 320])
        QTest.qWait(50)
        viewport = page.chart_scroll.viewport()
        self.assertEqual(page.chart_scroll.horizontalScrollBar().maximum(), 0)
        for widget in (page.btn_tornado, page.btn_backtest, page.bt_lookback,
                       page.analysis_note):
            left = widget.mapTo(viewport, QPoint(0, 0)).x()
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(left + widget.width(), viewport.width())

    def test_forecast_long_details_scroll_without_displacing_verdict(self):
        self.add_account()
        self.win.resize(980, 620)
        self.win.show_page("Forecast")
        page = self.fc
        # Exercise account-dependent text growth without changing the model.
        details = " · ".join(f"Long loan name {i}: payment $200/month, interest $2,500"
                             for i in range(40))
        warnings = "Review the recurring payment linked to this account. " * 20
        page.summary.setText(details)
        page.forecast_warning.setText(warnings)
        page.forecast_warning.show()
        page.risk_toggle.click()
        QTest.qWait(50)
        self.assertEqual((self.win.width(), self.win.height()), (980, 620))
        self.assertGreaterEqual(page.chart_scroll.viewport().height(), 250)
        self.assertEqual(page.summary.text(), details)
        self.assertEqual(page.forecast_warning.text(), warnings)
        for label in (page.summary, page.forecast_warning):
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.assertGreaterEqual(page.risk_details.height(),
                                page.summary.y() + page.summary.height())
        previous_bottom = 0
        layout = page.chart_scroll.widget().layout()
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and widget.isVisibleTo(page):
                self.assertGreaterEqual(widget.y(), previous_bottom)
                previous_bottom = widget.y() + widget.height()
        self.assertGreaterEqual(page.chart_scroll.widget().height(), previous_bottom)
        before = page.risk.mapTo(self.win, QPoint(0, 0))
        scrollbar = page.chart_scroll.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(scrollbar.maximum())
        self.app.processEvents()
        self.assertEqual(page.risk.mapTo(self.win, QPoint(0, 0)), before)
        page.view_pick.setCurrentIndex(page.view_pick.findData("networth"))
        self.assertTrue(page.risk_details.isHidden())
        page.view_pick.setCurrentIndex(page.view_pick.findData("cash"))
        self.assertTrue(page.risk_details.isVisible())

    def test_forecast_explains_slider_dollars_and_unchanged_control_comparison(self):
        from finfore.demo import load_demo
        from finfore.ui import forecast_page
        from finfore.util import fmt_money
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        reference = page._res.monthly_income_reference
        page.income_slider.slider.setValue(120)
        self.assertIn(fmt_money(reference * 1.2), page.income_slider.monthly.text())
        self.assertIn("more", page.income_slider.monthly.text())
        self.assertIn("120%", page.income_slider.slider.accessibleDescription())
        results = []
        real_run = forecast_page.run_forecast

        def record(*args, **kwargs):
            result = real_run(*args, **kwargs)
            results.append(result)
            return result

        with patch.object(forecast_page, "run_forecast", side_effect=record):
            page.run()
        self.assertGreaterEqual(len(results), 2)
        scenario, original = results[:2]
        self.assertTrue(page.cash_compare.isVisible())
        self.assertIn(fmt_money(scenario.cash_pct[50][-1]), page.cash_compare.text())
        self.assertIn(fmt_money(original.cash_pct[50][-1]), page.cash_compare.text())
        self.assertIn(fmt_money(scenario.cash_pct[50][-1] - original.cash_pct[50][-1],
                               signed=True), page.cash_compare.text())
        self.assertIn("Solid: these controls", page.cash_compare.text())
        self.assertIn("unchanged controls", page.cash_compare.text())
        self.assertIn("saved what-if events", page.overlay.toolTip())
        self.assertIn("not a guaranteed balance", page.cash_compare.toolTip())
        page.overlay.setChecked(False)
        page.run()
        self.assertTrue(page.cash_compare.isHidden())
        page.overlay.setChecked(True)
        page._reset_scenario()
        page.run()
        # Resetting temporary controls keeps the saved funding mode, whose
        # effect is still compared with existing habits without that plan.
        self.assertTrue(page.cash_compare.isVisible())
        self.assertIn('without the plan', page.cash_compare.text())
        page.follow_goals.setChecked(False)
        page.snowball.setChecked(False)
        page.run()
        self.assertTrue(page.cash_compare.isVisible())
        page._update_timer.stop()

    def test_forecast_goals_buffer_and_analysis_messages_explain_their_scope(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        page.show_analysis.setChecked(True)
        goal_items = page.cash_series._groups["goals"]
        self.assertTrue(all(item.label.format.startswith("Goal: ") for item in goal_items))
        self.assertIn("cash buffer", page.cash_series._groups["emergency"][0].label.format)
        for label in page._goal_labels.values():
            self.assertTrue(label.wordWrap())
            self.assertIn("not a probability range", label.toolTip())
        self.assertIn("$200/month", page.analysis_note.text())
        self.assertIn("average inputs", page.analysis_note.text())
        self.assertEqual(page.e_amount.accessibleName(), "Event amount")
        with patch.object(page, "_render_tornado", side_effect=RuntimeError("internal detail")):
            page._run_analysis()
        self.assertIn("Refresh the forecast", page.analysis_note.text())
        self.assertNotIn("internal detail", page.analysis_note.text())
        page._run_analysis()
        self.assertEqual(page.analysis_note.toolTip(), "")

    def test_forecast_event_validation_preserves_inputs_and_saves_once(self):
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        page.event_card.set_expanded(True)
        page._add_event()
        self.assertIn("name", page.event_error.text())
        page.e_name.setText("旅行 ✈️ <b>Bonus</b> فاتورة")
        page._add_event()
        self.assertIn("non-zero", page.event_error.text())
        page.e_amount.setValue(-123.45)
        original_date = page.e_when.date()
        with patch.object(page.store, "add_event", side_effect=sqlite3.OperationalError("readonly")):
            page._add_event()
        self.assertEqual(page.e_name.text(), "旅行 ✈️ <b>Bonus</b> فاتورة")
        self.assertEqual(page.e_amount.value(), -123.45)
        self.assertEqual(page.e_when.date(), original_date)
        self.assertIn("entries are still here", page.event_error.text())
        self.assertEqual(page.store.events(), [])
        page._add_event()
        page._add_event()
        self.assertEqual(len(page.store.events()), 1)
        event_row = page.event_box.itemAt(0).widget()
        label = event_row.findChild(QLabel)
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("<b>Bonus</b>", label.toolTip())

    def test_forecast_validates_event_date_again_after_midnight(self):
        from finfore.ui import forecast_page
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        page.e_name.setText("Bill")
        page.e_amount.setValue(-100)
        page.e_when.setDate(QDate.currentDate().addDays(1))
        from unittest.mock import Mock
        clock = Mock(wraps=date)
        clock.today.return_value = date.today() + timedelta(days=1)
        with patch.object(forecast_page, "date", clock):
            page._add_event()
        self.assertIn("future date", page.event_error.text())
        self.assertEqual(page.store.events(), [])

    def test_forecast_creates_repeating_purchase_with_optional_end(self):
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        page.event_card.set_expanded(True)
        page.e_name.setText("Regular purchase")
        page.e_amount.setValue(-123.45)
        page.e_repeat.setCurrentIndex(page.e_repeat.findData("weekly"))
        page.e_when.setDate(QDate.currentDate().addDays(1))
        page.e_until_enabled.setChecked(True)
        page.e_until.setDate(QDate.currentDate())
        page._add_event()
        self.assertIn("end date", page.event_error.text())
        self.assertEqual(page.store.events(), [])
        page.e_until.setDate(QDate.currentDate().addDays(15))
        page._add_event()
        event = page.store.events()[0]
        self.assertEqual(event.repeat, "weekly")
        self.assertEqual(event.until, date.today()+timedelta(days=15))
        self.assertEqual(event.amount, -123.45)
        details = page.event_box.itemAt(0).widget().findChildren(QLabel)[1]
        self.assertIn("$123.45", details.text())
        self.assertIn("Weekly", details.text())
        self.assertIn("through", details.text())
        self.assertEqual(page.e_repeat.currentData(), "once")

    def test_forecast_edits_an_event_in_place_and_retains_its_disabled_state(self):
        self.add_account()
        store = self.win.store
        original = OneOffEvent("Computer", date.today()+timedelta(days=5), -2000, active=False)
        eid = store.add_event(original)
        self.win.show_page("Forecast")
        page = self.fc
        row = page.event_box.itemAt(0).widget()
        next(b for b in row.findChildren(QPushButton) if b.text() == "Edit").click()
        self.assertEqual(page.e_name.text(), "Computer")
        self.assertEqual(page.add_event_btn.text(), "Save changes")
        page.e_name.setText("Computer installments")
        page.e_amount.setValue(-200)
        page.e_repeat.setCurrentIndex(page.e_repeat.findData("monthly"))
        page.e_when.setDate(QDate.currentDate().addDays(10))
        page._add_event()
        self.assertEqual(len(store.events()), 1)
        event = store.events()[0]
        self.assertEqual((event.id, event.label, event.repeat, event.amount),
                         (eid, "Computer installments", "monthly", -200))
        self.assertFalse(event.active)
        self.assertIsNone(page._editing_event)
        self.assertEqual(page.add_event_btn.text(), "Add event")
        self.assertTrue(page.cancel_event_btn.isHidden())
        store.undo.undo()
        self.assertEqual(store.events(), [original])

    def test_forecast_cancel_edit_and_editing_an_existing_past_start(self):
        self.add_account()
        store = self.win.store
        e = OneOffEvent("Subscription", date.today()-timedelta(days=60), -30, repeat="monthly")
        eid = store.add_event(e)
        self.win.show_page("Forecast")
        page = self.fc
        page._edit_event(eid, store, e)
        page.e_amount.setValue(-45)
        page.cancel_event_btn.click()
        self.assertEqual(store.events(), [e])
        page._edit_event(eid, store, e)
        page.e_amount.setValue(-40)
        page._add_event()
        event = store.events()[0]
        self.assertEqual(event.when, e.when)
        self.assertEqual(event.repeat, "monthly")
        self.assertEqual(event.amount, -40)
        self.assertFalse(page.event_error.isVisible())

    def test_forecast_event_edit_failure_keeps_entries_and_retry_updates_once(self):
        self.add_account()
        store = self.win.store
        e = OneOffEvent("Membership", date.today()+timedelta(days=2), -20)
        eid = store.add_event(e)
        self.win.show_page("Forecast")
        page = self.fc
        page._edit_event(eid, store, e)
        page.e_name.setText("New membership")
        page.e_amount.setValue(-50)
        page.e_repeat.setCurrentIndex(page.e_repeat.findData("quarterly"))
        with patch.object(store, "update_event", side_effect=sqlite3.OperationalError("readonly")):
            page._add_event()
        self.assertEqual(page.e_name.text(), "New membership")
        self.assertEqual(page.e_repeat.currentData(), "quarterly")
        self.assertEqual(page.add_event_btn.text(), "Save changes")
        self.assertEqual(store.events(), [e])
        self.assertIn("entries are still here", page.event_error.text())
        page._add_event()
        self.assertEqual(len(store.events()), 1)
        self.assertEqual(store.events()[0].label, "New membership")
        self.assertEqual(store.events()[0].repeat, "quarterly")

    def test_forecast_stale_edit_does_not_overwrite_a_newer_event(self):
        self.add_account()
        store = self.win.store
        e = OneOffEvent("Membership", date.today()+timedelta(days=2), -20)
        eid = store.add_event(e)
        self.win.show_page("Forecast")
        page = self.fc
        page._edit_event(eid, store, e)
        page.e_name.setText("Unsaved change")
        store.update_event(eid, amount=-30)
        page._add_event()
        self.assertEqual(store.events()[0].label, "Membership")
        self.assertEqual(store.events()[0].amount, -30)
        self.assertEqual(page.e_name.text(), "Unsaved change")
        self.assertIn("changed or was removed", page.event_error.text())
        page._remove_event(eid, store, e)  # old row snapshot is stale too
        self.assertEqual(len(store.events()), 1)

    def test_forecast_switching_databases_discards_event_editor_state(self):
        self.add_account()
        store = self.win.store
        e = OneOffEvent("Membership", date.today()+timedelta(days=2), -20, repeat="annual")
        eid = store.add_event(e)
        self.win.show_page("Forecast")
        page = self.fc
        page._edit_event(eid, store, e)
        other = Store(self.root/"other-events.db")
        self.win._switch_store(other, demo_key=None)
        self.assertIsNone(page._editing_event)
        self.assertEqual(page.e_name.text(), "")
        self.assertEqual(page.e_repeat.currentData(), "once")
        self.assertEqual(page.add_event_btn.text(), "Add event")
        page._edit_event(eid, store, e)
        self.assertIsNone(page._editing_event)
        self.assertEqual(other.events(), [])

    def test_forecast_repeating_event_editor_fits_a_narrow_control_column(self):
        self.add_account()
        store = self.win.store
        e = OneOffEvent("Regular purchase", date.today()+timedelta(days=1),
                        -123.45, repeat="monthly", until=date.today()+timedelta(days=365))
        eid = store.add_event(e)
        self.win.show_page("Forecast")
        page = self.fc
        page.open_controls()
        self.win.resize(980, 620)
        page.forecast_split.setSizes([240,900])
        page._edit_event(eid, store, e)
        QTest.qWait(80)
        viewport = page.control_scroll.viewport()
        self.assertEqual(page.control_scroll.horizontalScrollBar().maximum(), 0)
        for control in (page.e_name, page.e_repeat, page.e_when, page.e_until,
                        page.add_event_btn, page.cancel_event_btn):
            x = control.mapTo(viewport, QPoint(0,0)).x()
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x+control.width(), viewport.width())
        for button in (page.add_event_btn, page.cancel_event_btn):
            self.assertGreaterEqual(button.width(), button.sizeHint().width())

    def test_forecast_failure_clears_old_answer_and_retry_recovers(self):
        from finfore.ui import forecast_page
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        page.income_slider.slider.setValue(120)
        with patch.object(forecast_page, "run_forecast", side_effect=RuntimeError("bad data")):
            page.run()
        self.assertIsNone(page._res)
        self.assertFalse(page.risk_panel.isVisibleTo(page))
        self.assertEqual(page.plot_cash.listDataItems(), [])
        self.assertFalse(page.cash_compare.isVisibleTo(page))
        self.assertTrue(page.page_error.isVisibleTo(page))
        self.assertTrue(page.retry_btn.isVisibleTo(page))
        self.assertFalse(page.chart_scroll.isVisibleTo(page))
        self.app.processEvents()
        self.assertLessEqual(page.retry_btn.y() - page.page_error.geometry().bottom(), 20)
        self.assertIn("Refresh forecast", page.page_error.text())
        self.assertTrue(page.run_btn.isEnabled())
        self.assertEqual(page.income_slider.slider.value(), 120)
        page.retry_btn.click()
        self.assertIsNotNone(page._res)
        self.assertTrue(page.chart_scroll.isVisibleTo(page))
        self.assertFalse(page.page_error.isVisibleTo(page))
        self.assertFalse(page._running)
        self.assertEqual(page.run_btn.text(), "Refresh forecast")

    def test_forecast_load_failure_blocks_event_writes_until_retry(self):
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        with patch.object(page.store, "events", side_effect=sqlite3.OperationalError("unavailable")):
            page.refresh()
        self.assertIsNone(page._res)
        self.assertFalse(page.add_event_btn.isEnabled())
        page.e_name.setText("Keep this")
        page.e_amount.setValue(-10)
        with patch.object(page.store, "add_event") as save:
            page._add_event()
        save.assert_not_called()
        page.run_btn.click()
        self.assertTrue(page.add_event_btn.isEnabled())
        self.assertEqual(page.e_name.text(), "Keep this")

    def test_forecast_failed_history_save_restores_saved_controls(self):
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        page.store.con.execute("""CREATE TRIGGER reject_history AFTER INSERT ON settings
            WHEN NEW.key = 'forecast_history'
            BEGIN SELECT RAISE(ABORT, 'write failed'); END""")
        page.store.con.commit()
        page.store.undo.clear()
        page.history_pick.setCurrentIndex(page.history_pick.findData(13))
        self.assertEqual(page.history_pick.currentData(), "auto")
        self.assertFalse(page.store.con.in_transaction)
        self.assertFalse(page.store.undo.can_undo)
        self.assertIn("history setting could not be saved", page.page_error.text())
        page.run()
        self.assertIsNotNone(page._res)

    def test_forecast_event_failure_restores_checkbox_and_stale_rows_cannot_write(self):
        from PyQt6.QtWidgets import QCheckBox
        self.add_account()
        store = self.win.store
        store.add_event(OneOffEvent("Old ledger bill", date.today() + timedelta(days=10), -100))
        self.win.show_page("Forecast")
        page = self.fc
        row = page.event_box.itemAt(0).widget()
        with patch.object(store, "update_event", side_effect=sqlite3.OperationalError("readonly")):
            row.findChild(QCheckBox).setChecked(False)
        self.assertTrue(store.events()[0].active)
        row = page.event_box.itemAt(0).widget()
        self.assertTrue(row.findChild(QCheckBox).isChecked())
        old_remove = row.findChild(QPushButton)
        other = Store(self.root / "other.db")
        other.add_event(OneOffEvent("New ledger bill", date.today() + timedelta(days=11), -200))
        page.store = other
        try:
            page.reset_for_store()
            old_remove.click()
            self.assertEqual(other.events()[0].label, "New ledger bill")
        finally:
            page.store = store
            other.close()

    def test_forecast_payment_move_is_atomic_and_one_undo_step(self):
        from types import SimpleNamespace
        from finfore.ui.forecast_page import CrunchDialog
        from PyQt6.QtWidgets import QDateEdit
        store = self.win.store
        sf = SimpleNamespace(recovers=True, median_days=3, headline="Brief dip",
                             low_date=date.today() + timedelta(days=5), median_depth=100)
        flow = SimpleNamespace(label="Rent <b>literal</b>", when=sf.low_date,
                               amount=-100, source="recurring", movable=True)
        dlg = CrunchDialog(store, sf, [flow], None, self.fc)
        to = dlg.findChild(QDateEdit)
        store.con.execute("""CREATE TRIGGER reject_second_event BEFORE INSERT ON events
            WHEN NEW.amount < 0 BEGIN SELECT RAISE(ABORT, 'write failed'); END""")
        store.con.commit()
        store.undo.clear()
        dlg._move(flow, to)
        self.assertEqual(store.events(), [])
        self.assertFalse(dlg.applied)
        self.assertFalse(store.undo.can_undo)
        self.assertIn("could not be saved", dlg.move_error.text())
        store.con.execute("DROP TRIGGER reject_second_event")
        store.con.commit()
        dlg._move(flow, to)
        dlg._move(flow, to)
        self.assertTrue(dlg.applied)
        events = store.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(e.amount for e in events), 0)
        store.undo.undo()
        self.assertEqual(store.events(), [])
        dlg.deleteLater()

    def test_forecast_event_changes_rollback_after_database_rejection(self):
        self.add_account()
        store = self.win.store
        event_id = store.add_event(OneOffEvent("Bill", date.today() + timedelta(days=10), -100))
        self.win.show_page("Forecast")
        for operation in ("UPDATE", "DELETE"):
            store.con.execute(f"""CREATE TRIGGER reject_event_{operation} AFTER {operation} ON events
                BEGIN SELECT RAISE(ABORT, 'write failed'); END""")
        store.con.commit()
        store.undo.clear()
        self.fc._toggle_event(event_id, False)
        self.assertTrue(store.events()[0].active)
        self.assertFalse(store.con.in_transaction)
        self.fc._remove_event(event_id)
        self.assertEqual(len(store.events()), 1)
        self.assertFalse(store.con.in_transaction)
        self.assertFalse(store.undo.can_undo)
        self.assertIn("could not be changed", self.fc.event_error.text())

    def test_navigation_links_refresh_only_the_requested_subpage(self):
        forecast = self.fc
        progress = self.win.pages[7]
        self.win.show_page('Accounts')
        with patch.object(forecast, 'refresh') as projection_refresh, \
             patch.object(progress, 'refresh') as progress_refresh:
            self.win.show_page('Progress')
            self.assertIs(self.win.current_page(), progress)
            projection_refresh.assert_not_called()
            progress_refresh.assert_called_once()
            self.win.show_page('Projection')
            self.assertIs(self.win.current_page(), forecast)
            projection_refresh.assert_called_once()
            progress_refresh.assert_called_once()

    def test_forecast_only_calculates_analysis_when_requested(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        page = self.fc
        with patch.object(page, '_render_tornado', wraps=page._render_tornado) as analysis:
            self.win.show_page('Forecast')
            self.assertEqual(analysis.call_count, 0)
            self.assertTrue(page._analysis_dirty)
            page.show_analysis.setChecked(True)
            self.assertEqual(analysis.call_count, 1)
            page._apply_view()
            self.assertEqual(analysis.call_count, 1)
            page.show_analysis.setChecked(False)
            page.run()
            self.assertEqual(analysis.call_count, 1)
            page.show_analysis.setChecked(True)
            self.assertEqual(analysis.call_count, 2)
            page._update_timer.stop()

    def test_forecast_analysis_failure_drops_old_statistics(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        page.show_analysis.setChecked(True)
        self.assertTrue(page._sens)
        with patch.object(page, "_render_tornado", side_effect=RuntimeError("failed")):
            page._run_analysis()
        self.assertEqual(page._sens, [])
        self.assertEqual(page.plot_analysis.listDataItems(), [])
        self.assertNotIn("<h3>Sensitivity</h3>", page._nerd_panel()[1])
        page._run_analysis()
        self.assertTrue(page._sens)
        self.assertIn("$200/month", page._nerd_panel()[1])

    def test_forecast_busy_guard_prevents_reentrant_calculations(self):
        from finfore.ui import forecast_page
        self.add_account()
        self.win.show_page("Forecast")
        page = self.fc
        real_run = forecast_page.run_forecast
        def simulate(*args, **kwargs):
            self.assertFalse(page.run_btn.isEnabled())
            self.assertEqual(page.run_btn.text(), "Calculating…")
            page.run()
            return real_run(*args, **kwargs)
        with patch.object(forecast_page, "run_forecast", side_effect=simulate) as compute:
            page.run()
        self.assertEqual(compute.call_count, 1)
        self.assertTrue(page.run_btn.isEnabled())

    def test_forecast_long_event_name_keeps_amount_and_actions_visible(self):
        self.add_account()
        name = "<b>旅行 فاتورة 🧳</b> " + "VeryLongUnbrokenName" * 12
        self.win.store.add_event(OneOffEvent(name, date.today() + timedelta(days=10), -999999.99))
        self.win.show_page("Forecast")
        page = self.fc
        page.open_controls()
        page.event_card.set_expanded(True)
        from PyQt6.QtWidgets import QSplitter, QScrollArea
        outer = next(s for s in page.findChildren(QSplitter)
                     if s.orientation() == Qt.Orientation.Horizontal)
        self.win.resize(980, 620)
        outer.setSizes([240, 900])
        QTest.qWait(100)
        row = page.event_box.itemAt(0).widget()
        label, details = row.findChildren(QLabel)
        button = row.findChild(QPushButton)
        self.assertEqual(label.toolTip(), name)
        self.assertEqual(label.accessibleName(), name)
        self.assertIn("…", label.text())
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())
        self.assertGreaterEqual(details.height(), details.heightForWidth(details.width()))
        self.assertLess(label.geometry().right(), button.x())
        control_scroll = next(s for s in page.findChildren(QScrollArea) if s is not page.chart_scroll)
        self.assertEqual(control_scroll.horizontalScrollBar().maximum(), 0)

    def test_forecast_update_preserves_axes_and_reset_fits_extreme_comparison(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Forecast")
        page = self.fc
        page.view_pick.setCurrentIndex(page.view_pick.findData("both"))
        before = [plot.viewRange() for plot in (page.plot_cash, page.plot_nw)]
        page.extra_invest.setValue(1_000_000)
        page.run()
        self.assertIsNotNone(page._res)
        self.assertEqual([plot.viewRange() for plot in (page.plot_cash, page.plot_nw)], before)
        for plot, registry in ((page.plot_cash, page.cash_series), (page.plot_nw, page.nw_series)):
            plot.reset_axes_btn.click()
            line = registry._groups["baseline"][0]
            low, high = plot.viewRange()[1]
            self.assertGreaterEqual(float(line.yData.min()), low)
            self.assertLessEqual(float(line.yData.max()), high)

    def test_forecast_history_is_advanced_persistent_and_per_database(self):
        from finfore.forecast_history import ForecastHistory, SETTING
        self.add_account()
        self.win.show_page("Forecast")
        self.assertFalse(self.fc.history_card.isVisibleTo(self.fc))
        self.win._toggle_mode()
        self.fc.history_card.set_expanded(True)
        self.app.processEvents()
        self.assertTrue(self.fc.history_card.isVisibleTo(self.fc))
        self.fc.history_pick.setCurrentIndex(self.fc.history_pick.findData(13))
        self.assertEqual(self.win.store.get_setting(SETTING)["weeks"], 13)
        self.fc.history_pick.setCurrentIndex(self.fc.history_pick.findData("since"))
        chosen = date.today() - timedelta(days=90)
        chosen_qdate = QDate(chosen.year, chosen.month, chosen.day)
        self.fc.history_since.setDate(chosen_qdate)
        self.fc.history_refit.setChecked(True)
        self.fc.refresh()
        saved = ForecastHistory.from_setting(self.win.store.get_setting(SETTING))
        self.assertEqual(saved.since, chosen)
        self.assertTrue(saved.refit_recurring)
        self.assertTrue(self.fc.history_since_row.isVisibleTo(self.fc))
        self.fc._reset_scenario()
        self.assertEqual(ForecastHistory.from_setting(self.win.store.get_setting(SETTING)), saved)

        other = Store(self.root / "other.db")
        self.win._switch_store(other, demo_key=None)
        self.assertEqual(self.fc.history_pick.currentData(), "auto")
        self.assertFalse(self.fc.history_refit.isChecked())
        self.assertFalse(self.fc.history_since_row.isVisibleTo(self.fc))
        self.win._switch_store(Store(self.root / "mine.db"), demo_key=None)
        self.assertEqual(self.fc.history_pick.currentData(), "since")
        self.assertEqual(self.fc.history_since.date(), chosen_qdate)
        self.assertTrue(self.fc.history_refit.isChecked())

    def test_forecast_history_controls_fit_and_review_opens_patterns(self):
        self.add_account()
        self.win.show_page("Forecast")
        self.win._toggle_mode()
        self.fc.history_card.set_expanded(True)
        self.fc.history_pick.setCurrentIndex(self.fc.history_pick.findData("since"))
        content = self.fc.history_card._content
        for width, height in ((1280, 800), (980, 620)):
            self.win.resize(width, height)
            self.app.processEvents()
            for control in (self.fc.history_pick, self.fc.history_since, self.fc.history_refit):
                start = control.mapTo(content, QPoint(0, 0)).x()
                self.assertGreaterEqual(start, 0)
                self.assertLessEqual(start + control.width(), content.width())
                self.assertGreaterEqual(control.height(), control.sizeHint().height())
        button = next(b for b in self.fc.history_card.findChildren(QPushButton)
                      if b.text() == "Review detected bills and pay")
        button.click()
        self.assertEqual(type(self.win.current_page()).__name__, "RecurringPage")

    def test_calendar_filters_and_database_switch(self):
        self.add_account()
        self.win.store.add_event(OneOffEvent("Repair", date.today() + timedelta(days=1), -100))
        self.win.show_page("Calendar")
        calendar = self.win.current_page()
        tomorrow = QDate.currentDate().addDays(1)
        calendar.calendar.setCurrentPage(tomorrow.year(), tomorrow.month())
        calendar.refresh()
        self.assertEqual(calendar.table.rowCount(), 1)
        self.assertEqual(calendar.table.item(0, 2).text(), "Unassigned")
        calendar.account_pick.setCurrentIndex(calendar.account_pick.findData(1))
        self.assertEqual(calendar.table.rowCount(), 0)
        self.assertEqual(calendar.balance.value.text(), "$2,000")
        calendar.account_pick.setCurrentIndex(calendar.account_pick.findData("unassigned"))
        self.assertEqual(calendar.table.rowCount(), 1)
        calendar._select_day(tomorrow)
        calendar.month_btn.click()
        self.assertIsNone(calendar._day)
        self.win._switch_store(Store(self.root / "empty-calendar.db"), demo_key=None)
        self.assertEqual(calendar.table.rowCount(), 0)
        self.assertTrue(all(page.store is self.win.store for page in self.win.pages))
        self.assertEqual(calendar.account_pick.count(), 2)

    def test_data_quality_is_visible_in_simple_view_and_refreshes(self):
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([dict(
            account_id=1, date=date.today(), description="Mystery", amount=-20,
            category="Misc", import_hash="quality-category")]))
        self.win.show_page("Projection")
        card = self.fc.data_quality
        self.assertTrue(card.isVisible())
        self.assertTrue(card.review_btn.isVisible())
        self.assertIn("Categories to review", card.detail.text())
        page = self.win.pages[2]
        tx_id = int(self.win.store.transactions().iloc[0]["id"])
        self.win.show_page("Transactions")
        page._recategorize([tx_id], "Shopping")
        self.win.show_page("Projection")
        self.assertNotIn("Categories to review", card.detail.text())
        self.win.undo()
        self.assertIn("Categories to review", card.detail.text())
        self.win._switch_store(Store(self.root / "quality-empty.db"), demo_key=None)
        self.assertIsNone(card.report.last_transaction)
        self.assertNotIn("Categories to review", card.detail.text())
        self.assertEqual(card.report.summary, "3 checks need review")

    def test_data_quality_details_show_checks_and_navigate(self):
        self.add_account()
        self.win.show_page("Projection")
        card = self.fc.data_quality
        observed = []

        def inspect_dialog():
            dialog = self.app.activeModalWidget()
            if not isinstance(dialog, QDialog):
                observed.append("missing dialog")
                return
            labels = [label.text() for label in dialog.findChildren(QLabel)]
            observed.append(sum(text in ("Review", "No issue flagged") for text in labels))
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Review transactions")
            button.click()

        QTimer.singleShot(0, inspect_dialog)
        card.review_btn.click()
        self.assertEqual(observed, [8])
        self.assertEqual(type(self.win.current_page()).__name__, "TransactionsPage")

    def test_data_quality_rechecks_saved_events_when_forecast_runs(self):
        self.add_account()
        self.win.show_page("Projection")
        card = self.fc.data_quality

        def unassigned():
            return next(c for c in card.report.checks if c.key == "unassigned")

        self.assertFalse(unassigned().needs_review)
        event_id = self.win.store.add_event(OneOffEvent(
            "Repair", date.today() + timedelta(days=1), -100))
        self.fc.run()
        self.assertTrue(unassigned().needs_review)
        self.win.store.delete_event(event_id)
        self.fc.run()
        self.assertFalse(unassigned().needs_review)

    def test_review_filter_one_off_scope_confirmation_and_undo(self):
        page = self.transaction_hardening_fixture()
        page.f_review.setCurrentIndex(page.f_review.findData('attention'))
        self.assertGreater(page.table.rowCount(), 0)
        page.remember_category.setChecked(False)
        page.table.selectRow(0)
        txid = page._selected_ids()[0]
        page.confirm_btn.click()
        self.assertTrue(self.win.store.transactions().set_index('id').loc[txid].category_reviewed)
        self.assertEqual(self.win.store.learned_categories(), {})
        page.f_review.setCurrentIndex(page.f_review.findData('reviewed'))
        self.assertEqual(page.table.rowCount(), 1)
        page.show_sources.setChecked(True)
        self.assertEqual(page.table.item(0,6).text(), 'Reviewed by you')
        self.win.undo()
        self.assertEqual(page.table.rowCount(), 0)
        page._clear_filters()
        self.assertEqual(page.f_review.currentData(), 'all')

    def test_transaction_confirmation_does_not_teach_rules_and_attention_opens_full_queue(self):
        page = self.transaction_hardening_fixture()
        self.assertFalse(page.remember_category.isChecked())
        page.remember_category.setChecked(True)
        page.table.selectRow(0)
        txid = page._selected_ids()[0]
        page.confirm_btn.click()
        self.assertEqual(self.win.store.learned_categories(), {})
        self.assertIn('Future import rules unchanged', page.learned_lbl.text())
        self.assertIn('Reviewed', page.table.item(0, 2).data(Qt.ItemDataRole.AccessibleTextRole))
        page._recategorize([txid], 'Dining')
        self.assertIn('Dining', self.win.store.learned_categories().values())
        page.f_account.setCurrentIndex(page.f_account.findData(1))
        page.search.setText('does not exist')
        page.attention_btn.click()
        self.assertEqual(page.f_review.currentData(), 'attention')
        self.assertEqual(page.search.text(), '')
        self.assertIsNone(page.f_since.currentData())
        self.assertEqual(page.table.rowCount(), 2)

    def test_transaction_clear_filters_resets_negative_only_without_changing_balances(self):
        page = self.transaction_hardening_fixture()
        page.show_balances.setChecked(True)
        before = page._balances.copy()
        page.negative_only.setChecked(True)
        self.assertEqual(page.table.rowCount(), 0)
        page.empty_action.click()
        self.assertFalse(page.negative_only.isChecked())
        self.assertEqual(page.table.rowCount(), 3)
        self.assertTrue(page.show_balances.isChecked())
        pd.testing.assert_frame_equal(page._balances, before)

    def test_transaction_keyboard_leaves_table_and_only_commits_an_explicit_category(self):
        page = self.transaction_hardening_fixture()
        page.table.selectRow(0)
        page.table.setCurrentCell(0, 2)
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Tab)
        focus = self.app.focusWidget()
        self.assertIsNotNone(focus)
        self.assertIsNot(focus, page.table)
        self.assertFalse(page.table.isAncestorOf(focus))
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        focus = self.app.focusWidget()
        self.assertIsNotNone(focus)
        self.assertFalse(focus is page.table or page.table.isAncestorOf(focus))
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Return)
        box = page.table.cellWidget(0, page.CAT_COL)
        self.assertTrue(box.view().isVisible())
        QTest.keyClick(box.view(), Qt.Key.Key_Escape)
        self.assertTrue(self.win.store.category_feedback().empty)
        box.setFocus()
        QTest.keyClick(box, Qt.Key.Key_Down)
        self.assertTrue(box.view().isVisible())
        self.assertTrue(self.win.store.category_feedback().empty)
        QTest.keyClick(box.view(), Qt.Key.Key_Home)
        for _ in range(box.findText('Dining')):
            QTest.keyClick(box.view(), Qt.Key.Key_Down)
        QTest.keyClick(box.view(), Qt.Key.Key_Return)
        QTest.qWait(30)
        self.assertEqual(self.win.store.transactions().sort_values('date').iloc[-1].category, 'Dining')
        self.assertEqual(len(self.win.store.category_feedback()), 1)
        self.assertEqual(self.win.store.learned_categories(), {})
        self.assertIs(self.app.focusWidget(), page.table)

    def test_transaction_diagnostics_preserve_description_and_help_does_not_resize_page(self):
        from finfore.ui import theme
        from finfore.ui.widgets import InfoDialog
        previous = self.app.styleSheet()
        theme.apply(self.app)
        self.addCleanup(self.app.setStyleSheet, previous)
        page = self.transaction_hardening_fixture()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=date.today()-timedelta(days=i), description=f'Extra merchant {i}',
                 amount=-10, category='Misc', import_hash=f'scroll-fit-{i}') for i in range(30)]))
        page.refresh()
        self.win.resize(980, 620)
        QTest.qWait(50)
        self.assertEqual(page.table.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(sum(page.table.columnWidth(c) for c in range(7)
            if not page.table.isColumnHidden(c)), page.table.viewport().width())
        page.show_balances.setChecked(True)
        page.show_sources.setChecked(True)
        page.f_since.setCurrentIndex(page.f_since.findData('custom'))
        QTest.qWait(50)
        self.assertGreaterEqual(page.table.columnWidth(2), 250)
        self.assertGreater(page.table.height(), 170)
        self.assertLessEqual(page.filter_host.geometry().bottom(), page.balance_controls.geometry().top())
        before = page.table.geometry()
        with patch.object(InfoDialog, 'exec', return_value=0) as dialog:
            page.category_help_btn.trigger()
            dialog.assert_called_once()
        self.assertEqual(page.table.geometry(), before)
        page.table.selectRow(0)
        QTest.qWait(30)
        self.assertGreaterEqual(page.scope_note.width(), page.bulk_actions.width() - 2)

    def test_reviewed_export_excludes_automatic_rows_and_protects_database(self):
        page = self.transaction_hardening_fixture()
        txid = int(page._df.iloc[0].id)
        self.win.store.review_transactions([txid], remember=False)
        target = self.root / 'reviewed.csv'
        with patch('finfore.ui.transactions.QFileDialog.getSaveFileName', return_value=(str(target),'CSV')):
            page._export_reviewed()
        frame = pd.read_csv(target)
        self.assertEqual(list(frame.transaction_id), [txid])
        self.assertEqual(list(frame.label_source), ['human_review'])
        with patch('finfore.ui.transactions.QFileDialog.getSaveFileName', return_value=(str(self.win.store.path),'CSV')), \
                patch.object(page, '_show_action_error') as error:
            page._export_reviewed()
        error.assert_called_once()
        self.assertEqual(len(self.win.store.transactions()), 3)

    def test_merchant_review_preview_scope_and_suggestions_need_explicit_acceptance(self):
        from finfore.learning import record_corrections
        from finfore.ui.review_dialog import ReviewUncategorizedDialog
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1,date=date.today()-timedelta(days=age),description=desc,amount=-30,
                 category=cat,import_hash=f'preview-{i}')
            for i,(age,desc,cat) in enumerate([(1,'SQ *BLUE BOTTLE 042','Misc'),
                (900,'SQ *BLUE BOTTLE 042','Misc'),(1,'MYSTERY VENDOR','Misc')])]))
        record_corrections(self.win.store, pd.DataFrame([dict(account_id=1,
            description='BLUE BOTTLE COFFEE',amount=-10,category='Misc')]), 'Dining')
        dialog = ReviewUncategorizedDialog(self.win.store, ['Dining','Shopping'], self.win)
        self.assertFalse(dialog.apply_btn.isEnabled())
        i = next(i for i,c in enumerate(dialog._clusters) if c.suggestion)
        self.assertEqual(dialog._boxes[i].currentIndex(), 0)
        dialog.table.selectRow(i)
        self.assertEqual(dialog.preview.rowCount(), 1)
        dialog.include_older.setChecked(True)
        self.assertEqual(dialog.preview.rowCount(), 2)
        dialog.include_older.setChecked(False)
        dialog.remember.setChecked(False)
        dialog._accept_suggested()
        dialog.resize(900, 700)
        dialog.show()
        QTest.qWait(30)
        self.assertGreater(dialog.status.width(), 400)
        self.assertTrue(dialog.apply_btn.isEnabled())
        self.assertIn('1 transaction ready', dialog.pending_note.text())
        dialog.include_older.setChecked(True)
        self.assertIn('2 transactions ready', dialog.pending_note.text())
        dialog.include_older.setChecked(False)
        dialog._apply()
        self.assertFalse(dialog.apply_btn.isEnabled())
        tx = self.win.store.transactions()
        self.assertEqual(int(tx.category.eq('Dining').sum()), 1)
        self.assertEqual(int(tx.category_reviewed.sum()), 1)
        self.assertEqual(tx.sort_values('date').iloc[0].category, 'Misc')
        dialog.close()

    def test_one_undo_reverts_category_and_learned_correction(self):
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([dict(
            account_id=1, date=date.today(), description="MYSTERY SHOP", amount=-20,
            category="Misc", import_hash="category-undo")]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        tx_id = int(self.win.store.transactions().iloc[0]["id"])
        page._recategorize([tx_id], "Shopping")
        self.assertEqual(self.win.store.transactions().iloc[0]["category"], "Shopping")
        self.assertEqual(len(self.win.store.category_feedback()), 1)
        self.win.undo()
        self.assertEqual(self.win.store.transactions().iloc[0]["category"], "Misc")
        self.assertTrue(self.win.store.category_feedback().empty)
        self.win.redo()
        self.assertEqual(self.win.store.transactions().iloc[0]["category"], "Shopping")
        self.assertEqual(len(self.win.store.category_feedback()), 1)

    def test_setup_continues_until_transactions_are_imported(self):
        dash = self.win.pages[0]
        add_btn, import_btn = dash.steps[0]["btn"], dash.steps[1]["btn"]
        self.assertTrue(dash.welcome.isVisible())
        self.assertTrue(add_btn.isVisible())
        self.assertFalse(import_btn.isVisible())
        self.assertFalse(dash.stats_w.isVisible())
        self.assertFalse(dash.hist_card.isVisible())
        self.add_account()
        dash.refresh()
        self.assertTrue(dash.welcome.isVisible())
        self.assertTrue(import_btn.isVisible())
        self.assertFalse(add_btn.isVisible())
        # Exactly one primary action at each stage.
        self.assertEqual(import_btn.objectName(), "primary")
        self.assertTrue(dash.stats_w.isVisible())
        self.assertFalse(dash.hist_card.isVisible(), "a flat one-point history is not a chart")
        self.win.store.add_transactions(pd.DataFrame([{
            "account_id": 1, "date": date.today(), "description": "Income",
            "amount": 100, "category": "Income", "import_hash": "setup",
        }]))
        dash.refresh()
        self.assertFalse(dash.welcome.isVisible())
        self.assertTrue(dash.hist_card.isVisible())

    def test_empty_dashboard_shows_no_fake_zeroes(self):
        dash = self.win.pages[0]
        self.assertEqual(dash.card_nw.value.text(), "—")
        self.assertEqual(dash.card_nw.value.styleSheet(), "")
        self.assertFalse(dash.verdict_band.isVisible())

    def test_stat_cards_keep_a_sub_line_so_values_stay_aligned(self):
        dash = self.win.pages[0]
        self.add_account()
        dash.refresh()
        heights = {c.sub.height() for c in (dash.card_nw, dash.card_cash,
                                            dash.card_inv, dash.card_debt,
                                            dash.card_save)}
        self.assertTrue(all(c.sub.isVisible() for c in (dash.card_nw, dash.card_save)))
        self.assertEqual(len({c.value.y() for c in (dash.card_cash, dash.card_inv,
                                                    dash.card_debt, dash.card_save)}), 1)
        self.assertIn("Net worth", dash.card_nw.accessibleName())

    def test_dashboard_forward_line_range_and_category_links(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Dashboard")
        dash = self.win.pages[0]
        dash.refresh()
        self.assertTrue(dash.verdict_band.isVisible())
        self.assertIn("estimate", dash.verdict_detail.text())
        self.assertIn("90 days", dash.verdict.text())
        # Range buttons reframe the chart and survive a refresh.
        vb = dash.plot_hist.getViewBox()
        span = lambda: (vb.viewRange()[0][1] - vb.viewRange()[0][0]) / 86400
        dash.range_btns[0].click()
        self.assertAlmostEqual(span(), 90, delta=2)
        dash.refresh()
        self.assertAlmostEqual(span(), 90, delta=2)
        self.assertIn("last 3 months", dash.hist_note.text())
        # A category bar leads to that category's transactions.
        # The category chart now lives, folded, on the Spending tab.
        self.win.show_page("Spending")
        spending = self.win.current_page()
        spending.refresh()
        name = spending.charts._cats.index[1]
        spending.charts._open_category(name)
        self.assertEqual(self.win.current_page().f_category.currentData(), name)
        self.assertEqual(self.win.current_page().f_since.currentData(), 90)

    def test_net_worth_chart_preserves_axes_until_reset_and_can_expand(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show()
        self.win.show_page("Dashboard")
        dash = self.win.pages[0]
        dash.refresh()
        vb = dash.plot_hist.getViewBox()
        y_span = lambda: vb.viewRange()[1][1] - vb.viewRange()[1][0]
        full = y_span()
        before = vb.viewRange()
        # Layer changes retain both axes; reset fits the remaining layers.
        for key in ("debt", "zero", "starter", "emergency"):
            if key in dash.hist_legend.actions:
                dash.hist_legend.actions[key].setChecked(False)
        self.assertEqual(vb.viewRange(), before)
        dash.plot_hist.reset_axes_btn.click()
        self.assertLess(y_span(), full * 0.6)
        before = vb.viewRange()
        for key in ("debt", "zero", "starter", "emergency"):
            if key in dash.hist_legend.actions:
                dash.hist_legend.actions[key].setChecked(True)
        self.assertEqual(vb.viewRange(), before)
        dash.plot_hist.reset_axes_btn.click()
        self.assertAlmostEqual(y_span(), full, delta=full * 0.01)
        # Expanding hands the chart the whole page; restoring puts it back.
        small = dash.plot_hist.height()
        dash.expand_btn.click()
        QTest.qWait(50)
        self.assertTrue(dash._expanded)
        self.assertFalse(dash.verdict_band.isVisible())
        self.assertFalse(dash.stats_w.isVisible())
        self.assertGreater(dash.plot_hist.height(), small + 32)
        dash.refresh()                      # a tab switch must not undo it
        self.assertFalse(dash.stats_w.isVisible())
        dash.expand_btn.click()
        QTest.qWait(50)
        self.assertFalse(dash._expanded)
        self.assertTrue(dash.verdict_band.isVisible())
        self.assertTrue(dash.stats_w.isVisible())
        self.assertEqual(dash.expand_btn.text(), "Expand")

    # ---- Accounts page ---------------------------------------------------
    def _accounts_page(self):
        self.win.show_page("Accounts")
        page = self.win.pages[1]
        page.refresh()
        QTest.qWait(30)
        return page

    def _account_rows(self, page):
        out = {}
        for r in range(page.table.rowCount()):
            it = page.table.item(r, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) is not None:
                out[it.text()] = r
        return out

    def test_accounts_page_groups_rows_and_ties_to_net_worth(self):
        from finfore.history import headline_stats
        store = self.win.store
        store.add_account(Account("Everyday", AccountType.CHECKING, balance=1500.25,
                                  balance_date=date.today()))
        store.add_account(Account("Card", AccountType.CREDIT, balance=-400.5,
                                  balance_date=date.today()))
        store.add_account(Account("Car loan", AccountType.LOAN, balance=7000, apr=0.06,
                                  monthly_payment=300, balance_date=date.today()))
        page = self._accounts_page()
        texts = [page.table.item(r, 0).text() if page.table.item(r, 0) else ""
                 for r in range(page.table.rowCount())]
        self.assertIn("WHAT YOU HAVE", texts)
        self.assertIn("WHAT YOU OWE", texts)
        self.assertLess(texts.index("WHAT YOU HAVE"), texts.index("Everyday"))
        self.assertLess(texts.index("Card"), texts.index("Total you owe"))
        self.assertIn("Net worth", texts)
        net_row = texts.index("Net worth")
        shown = page.table.item(net_row, 2).text().replace("$", "").replace(",", "")
        self.assertAlmostEqual(float(shown), headline_stats(store)["networth"], places=2)
        # Debts show as positive amounts, with cents.
        self.assertEqual(page.table.item(texts.index("Card"), 2).text(), "$400.50")

    def test_accounts_actions_follow_the_visible_selection(self):
        store = self.win.store
        for n in ("One", "Two"):
            store.add_account(Account(n, AccountType.CHECKING, balance=100,
                                      balance_date=date.today()))
        page = self._accounts_page()
        rows = self._account_rows(page)
        self.assertFalse(page.sel_bar.isVisible())
        self.assertIsNone(page._selected())
        page.table.selectRow(rows["One"])
        self.assertTrue(page.sel_bar.isVisible())
        self.assertEqual(page._selected().name, "One")
        # Clearing the selection leaves nothing for Edit or Delete to act on,
        # even though the table still has a current row.
        page.table.clearSelection()
        self.assertIsNone(page._selected())
        self.assertFalse(page.sel_bar.isVisible())
        # Arrow keys step over the group headings and totals.
        page.table.setFocus()
        page.table.selectRow(rows["One"])
        QTest.keyClick(page.table, Qt.Key.Key_Down)
        self.assertEqual(page._selected().name, "Two")

    def test_accounts_enter_edits_and_delete_asks_with_the_safe_default(self):
        from finfore.ui import accounts as accounts_ui
        store = self.win.store
        store.add_account(Account("Only", AccountType.CHECKING, balance=100,
                                  balance_date=date.today()))
        page = self._accounts_page()
        page.table.setFocus()
        page.table.selectRow(self._account_rows(page)["Only"])
        opened = []
        with patch.object(accounts_ui.AccountDialog, "exec",
                          lambda self: opened.append(self.name.text())
                          or QDialog.DialogCode.Rejected):
            QTest.keyClick(page.table, Qt.Key.Key_Return)
        self.assertEqual(opened, ["Only"])
        seen = {}

        def fake_exec(box):
            seen["default"] = box.defaultButton().text()
            seen["escape"] = box.escapeButton().text()
            seen["buttons"] = [b.text() for b in box.buttons()]
            seen["text"] = box.text()
            return 0
        with patch.object(accounts_ui.QMessageBox, "exec", fake_exec):
            QTest.keyClick(page.table, Qt.Key.Key_Delete)
        self.assertEqual(seen["default"], "Keep it")
        self.assertEqual(seen["escape"], "Keep it")
        self.assertEqual(seen["buttons"], ["Delete account", "Keep it"])
        self.assertIn("Undo", seen["text"])
        self.assertEqual(len(store.accounts()), 1, "declining must keep the account")

    def test_accounts_empty_state_then_first_account_is_selected(self):
        from finfore.ui import accounts as accounts_ui
        page = self._accounts_page()
        self.assertTrue(page.empty.isVisible())
        self.assertFalse(page.table.isVisible())
        self.assertFalse(page.check_btn.isVisible())
        self.assertEqual(page.empty_btn.objectName(), "primary")

        class Stub:
            def __init__(self, parent=None, account=None): ...
            def exec(self): return QDialog.DialogCode.Accepted
            def get_account(self):
                return Account("First", AccountType.CHECKING, balance=250,
                               balance_date=date.today())
        with patch.object(accounts_ui, "AccountDialog", Stub):
            page._add()
        self.assertFalse(page.empty.isVisible())
        self.assertEqual(page._selected().name, "First")
        # The next step is already offered: this account has no transactions.
        self.assertTrue(page.sel_bar.isVisible())
        self.assertEqual(page.csv_btn.objectName(), "primary")
        self.assertIn("No transactions imported yet", page.sel_status.text())

    def test_accounts_status_column_and_one_step_balance_update(self):
        from finfore.ui import accounts as accounts_ui
        store = self.win.store
        aid = store.add_account(Account(
            "Old", AccountType.SAVINGS, balance=500,
            balance_date=date.today() - timedelta(days=60)))
        # Recent activity, so the stale *balance* is the only thing wrong.
        store.add_transactions(pd.DataFrame([dict(
            account_id=aid, date=date.today() - timedelta(days=2), amount=-5,
            category="Groceries", description="ALDI", import_hash="old-1")]))
        page = self._accounts_page()
        row = self._account_rows(page)["Old"]
        self.assertIn("Balance 60 days old", page.table.item(row, 4).text())
        page.table.selectRow(row)
        self.assertEqual(page.balance_btn.objectName(), "primary")

        class Stub:
            def __init__(self, account, parent=None): ...
            def exec(self): return QDialog.DialogCode.Accepted
            def get_values(self): return 612.34, date.today()
        with patch.object(accounts_ui, "UpdateBalanceDialog", Stub):
            page._update_balance()
        acct = store.account(aid)
        self.assertEqual((acct.balance, acct.balance_date), (612.34, date.today()))
        row = self._account_rows(page)["Old"]
        self.assertEqual(page.table.item(row, 3).text(), "Today")
        self.assertIn("$612.34", page.table.item(row, 2).text())

    def test_editing_a_balance_moves_its_date_to_today_unless_set_by_hand(self):
        from finfore.ui.account_dialog import AccountDialog
        old = date.today() - timedelta(days=20)
        acct = Account("Old", AccountType.CHECKING, balance=500, balance_date=old)
        acct.id = 1
        dlg = AccountDialog(None, account=acct)
        self.assertEqual(dlg.balance_date.date().toPyDate(), old)
        self.assertTrue(dlg.date_hint.isHidden())
        dlg.balance.setValue(650)
        self.assertEqual(dlg.balance_date.date().toPyDate(), date.today())
        self.assertFalse(dlg.date_hint.isHidden())
        dlg.balance.setValue(500)                       # change it back
        self.assertEqual(dlg.balance_date.date().toPyDate(), old)
        self.assertTrue(dlg.date_hint.isHidden())
        mine = date.today() - timedelta(days=3)
        dlg.balance_date.setDate(QDate(mine.year, mine.month, mine.day))
        dlg.balance.setValue(777)
        self.assertEqual(dlg.balance_date.date().toPyDate(), mine, "a date chosen by hand stays")
        self.assertEqual([b.text() for b in dlg.findChildren(QPushButton)],
                         ["Save changes", "Cancel"])

    def test_simple_view_hides_balance_history_but_keeps_check_my_balances(self):
        store = self.win.store
        store.add_account(Account("A", AccountType.CHECKING, balance=1,
                                  balance_date=date.today()))
        page = self._accounts_page()
        self.win.simple_mode = True
        self.win._apply_mode()
        self.assertTrue(page._simple)
        self.assertFalse(page.act_snap.isVisible())
        self.assertTrue(page.check_btn.isVisible())
        self.win.simple_mode = False
        self.win._apply_mode()
        self.assertFalse(page._simple)

    def test_dialogs_put_enter_on_the_safe_button_and_tie_labels_to_fields(self):
        from finfore.ui import accounts as accounts_ui
        from finfore.ui.account_dialog import AccountDialog
        from finfore.ui.import_dialog import ImportDialog
        store = self.win.store
        aid = store.add_account(Account("A", AccountType.CHECKING, balance=1,
                                        balance_date=date.today()))
        for dlg_type in (accounts_ui.BalanceCheckDialog, accounts_ui.DuplicateDialog):
            dlg = dlg_type(store)
            default = next(b for b in dlg.findChildren(QPushButton) if b.isDefault())
            self.assertEqual(default.text(), "Close", dlg_type.__name__)
        imp = ImportDialog(store)
        self.assertEqual(next(b for b in imp.findChildren(QPushButton)
                              if b.isDefault()).text(), "Import")
        snap = accounts_ui.SnapshotManagerDialog(store, store.account(aid))
        self.assertEqual(next(b for b in snap.findChildren(QPushButton)
                              if b.isDefault()).text(), "Close")
        # Every field label in the form dialogs points at its field.
        names = {"Name", "Type", "Balance", "Amount owed", "Balance as of", "APR",
                 "Monthly payment", "Payment day of month", "Expected yearly return",
                 "Monthly contribution", "File", "Account", "Date", "Description",
                 "Amount", "Credit/debit indicator", "Debit / money out",
                 "Credit / money in", "Column separator", "Amount signs"}
        for dlg in (AccountDialog(None), ImportDialog(store)):
            labels = [l for l in dlg.findChildren(QLabel) if l.text() in names]
            self.assertTrue(labels)
            self.assertTrue(all(l.buddy() is not None for l in labels),
                            [l.text() for l in labels if l.buddy() is None])

    def test_disabled_primary_and_focused_checkbox_look_different(self):
        from PyQt6.QtWidgets import QCheckBox, QVBoxLayout, QWidget
        from finfore.ui import theme
        app = QApplication.instance()
        previous = app.styleSheet()
        theme.apply(app)
        self.addCleanup(app.setStyleSheet, previous)
        host = QWidget()
        lay = QVBoxLayout(host)
        btn = QPushButton("Import")
        btn.setObjectName("primary")
        first, second = QCheckBox("First"), QCheckBox("Second")
        for w in (btn, first, second):
            lay.addWidget(w)
        host.show()
        host.activateWindow()
        QTest.qWait(60)
        enabled = btn.grab().toImage()
        btn.setEnabled(False)
        QTest.qWait(30)
        self.assertNotEqual(enabled, btn.grab().toImage(), "a disabled primary must look disabled")
        second.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.qWait(30)
        unfocused = first.grab().toImage()
        first.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.qWait(30)
        self.assertNotEqual(unfocused, first.grab().toImage(), "focus must show on a checkbox")
        host.close()

    def test_dashboard_stat_row_reflows_instead_of_clipping(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Dashboard")
        dash = self.win.pages[0]
        self.win.resize(980, 620)
        QTest.qWait(50)
        self.assertTrue(dash._stats_narrow)
        self.assertLessEqual(dash.body.minimumSizeHint().width(),
                             dash.scroll.viewport().width())
        self.win.resize(1280, 900)
        QTest.qWait(50)
        self.assertFalse(dash._stats_narrow)

    def test_home_leads_with_the_verdict_and_keeps_a_readable_chart_below_the_figures(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        self.win.show_page("Dashboard")
        dash = self.win.pages[0]
        dash.refresh()
        for width, height in ((1280,900),(980,620)):
            self.win.resize(width,height)
            QTest.qWait(60)
            self.assertGreaterEqual(dash.plot_hist.height(),280)
            self.assertLessEqual(dash.plot_hist.minimumHeight(),320)
            top = lambda w: w.mapTo(dash.body,QPoint(0,0)).y()
            self.assertLess(top(dash.verdict_band), top(dash.stats_w))
            self.assertLess(top(dash.stats_w), top(dash.hist_card))
            self.assertEqual(dash.scroll.horizontalScrollBar().maximum(),0)

    def test_dashboard_warning_stays_visible_with_details_available_on_demand(self):
        from finfore.history import LedgerIssue
        from finfore.ui import dashboard
        self.add_account()
        issue = LedgerIssue(account="Checking", net_flow=5000, since=date.today(),
                                entered=100, opening=-4900, duplicates=0)
        dash = self.win.pages[0]
        with patch.object(dashboard,"unacknowledged",return_value=[issue]):
            dash._render_warnings()
        self.assertFalse(dash.warn_row.isHidden())
        self.assertIn("does not reconcile",dash.warn_summary.text())
        self.assertTrue(dash.warn.isHidden())
        dash.warn_details.click()
        self.assertFalse(dash.warn.isHidden())
        self.assertIn("Checking",dash.warn.text())
        self.assertIn("$4,900",dash.warn.text())

    def test_buttons_show_keyboard_focus(self):
        from finfore.demo import load_demo
        load_demo(self.win.store, "balanced")
        # The suite does not style the app by default; the focus ring is part of
        # the stylesheet, so apply it for this test and put things back after.
        from finfore.ui import theme
        app = QApplication.instance()
        previous = app.styleSheet()
        theme.apply(app)
        self.addCleanup(app.setStyleSheet, previous)
        self.win.show()
        self.win.activateWindow()
        QTest.qWait(50)
        self.win.show_page("Dashboard")
        dash = self.win.pages[0]
        btn = dash.verdict_btn
        btn.clearFocus()
        QTest.qWait(20)
        before = btn.grab().toImage()
        btn.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.qWait(20)
        after = btn.grab().toImage()
        self.assertNotEqual(before, after, "a focused button must look different")

    def test_sliders_debounce_and_reset_is_available_in_simple_view(self):
        self.add_account()
        self.win.show_page("Forecast")
        # Simple view opens on the answer: the controls are one click away, and the
        # button that re-runs what already updates by itself is not offered.
        self.assertFalse(self.fc.control_scroll.isVisibleTo(self.fc))
        self.assertTrue(self.fc.try_btn.isVisible())
        self.fc.try_btn.click()
        self.assertTrue(self.fc.reset_btn.isVisible())
        self.assertFalse(self.fc.run_btn.isVisibleTo(self.fc))
        self.assertFalse(self.fc.debt_card.isVisible())
        calls = []
        self.fc._update_timer.timeout.disconnect()
        self.fc._update_timer.timeout.connect(lambda: calls.append(self.fc._scenario()))
        self.fc.income_slider.slider.setValue(110)
        self.fc.income_slider.slider.setValue(120)
        self.fc.extra_loan.setValue(200)
        QTest.qWait(450)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[-1].income_multiplier, 1.2)
        self.assertEqual(calls[-1].extra_loan_payment, 200)
        self.fc.income_slider.slider.setSliderDown(True)
        self.fc.income_slider.slider.setValue(130)
        QTest.qWait(450)
        self.assertEqual(len(calls), 1)
        self.fc.income_slider.slider.setSliderDown(False)
        QTest.qWait(450)
        self.assertEqual(len(calls), 2)
        self.fc.reset_btn.click()
        QTest.qWait(450)
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[-1].is_baseline())
        self.fc.income_slider.slider.setValue(140)
        self.win.show_page("Dashboard")
        QTest.qWait(450)
        self.assertEqual(len(calls), 3)

    def test_database_switch_clears_scenarios_and_stale_results(self):
        self.add_account()
        self.win.show_page("Forecast")
        self.fc.income_slider.slider.setValue(140)
        self.fc.extra_invest.setValue(300)
        self.assertTrue(self.fc._update_timer.isActive())
        self.win._switch_store(Store(self.root / "empty.db"), demo_key=None)
        self.assertTrue(self.fc._scenario().is_baseline())
        self.assertFalse(self.fc._update_timer.isActive())
        self.assertIsNone(self.fc._res)
        self.assertEqual(self.fc.plot_cash.listDataItems(), [])
        self.assertFalse(self.fc.run_btn.isEnabled())

    def goal_income_fixture(self):
        self.add_account()
        when = (pd.Timestamp(date.today().replace(day=1)) - pd.offsets.MonthBegin(1)).date()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=when, description="Salary", amount=2000,
                 category="Income", import_hash="goal-income"),
            dict(account_id=1, date=when, description="Rent", amount=-1000,
                 category="Housing", import_hash="goal-rent")]))
        self.win.store.add_goal(Goal("Savings", 10000))
        return when

    def test_detected_goal_surplus_stays_automatic_and_updates_after_import(self):
        when = self.goal_income_fixture()
        self.win.show_page("Goals")
        goals = self.win.current_page()
        self.assertEqual(goals.surplus.value(), 1000)
        self.assertIsNone(self.win.store.get_setting("plan_surplus"))
        goals.refresh()
        goals.policy.setCurrentIndex(goals.policy.findData("weighted"))
        self.assertIsNone(self.win.store.get_setting("plan_surplus"))
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1,
            date=when, description="Bonus", amount=500, category="Other income",
            import_hash="goal-bonus")]))
        goals.refresh()
        self.assertEqual(goals.surplus.value(), 1500)
        self.assertEqual(goals._plan.surplus, 1500)
        self.assertIsNone(self.win.store.get_setting("plan_surplus"))
        self.assertIn("$2,500", goals.surplus_note.text())
        self.assertIn("$1,000", goals.surplus_note.text())

    def test_manual_goal_surplus_and_same_value_use_detected(self):
        when = self.goal_income_fixture()
        self.win.store.set_setting("plan_surplus", 700)
        self.win.show_page("Goals")
        goals = self.win.current_page()
        goals.refresh()
        self.assertEqual(goals.surplus.value(), 700)
        self.assertIn("Manual amount", goals.surplus_note.text())
        goals.surplus.setValue(1000)
        self.assertEqual(self.win.store.get_setting("plan_surplus"), 1000)
        goals._use_detected()  # same spinner value: no valueChanged signal
        self.assertIsNone(self.win.store.get_setting("plan_surplus"))
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1,
            date=when, description="Bonus", amount=500, category="Other income",
            import_hash="goal-bonus")]))
        goals.refresh()
        self.assertEqual(goals.surplus.value(), 1500)
        self.assertNotIn("Manual amount", goals.surplus_note.text())

    def test_business_category_is_individual_and_not_taught_for_all_travel(self):
        from finfore.categorize import BUSINESS_CATEGORY
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1,
            date=date.today(), description="United airline", amount=-1000,
            category="Travel", import_hash="business-flight")]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        self.assertIn(BUSINESS_CATEGORY, page._cats)
        self.assertEqual(page.set_cat.currentIndex(), -1)
        self.assertFalse(page.apply_category_btn.isEnabled())
        page.set_cat.setCurrentIndex(page.set_cat.findText(BUSINESS_CATEGORY))
        self.assertFalse(page.apply_category_btn.isEnabled())
        page.table.selectRow(0)
        self.assertTrue(page.apply_category_btn.isEnabled())
        tx_id = int(self.win.store.transactions().iloc[0]["id"])
        page._recategorize([tx_id], BUSINESS_CATEGORY)
        self.assertEqual(self.win.store.transactions().iloc[0]["category"], BUSINESS_CATEGORY)
        self.assertTrue(self.win.store.category_feedback().empty)
        self.win.undo()
        self.assertEqual(self.win.store.transactions().iloc[0]["category"], "Travel")

    def test_transaction_batch_controls_follow_selection_and_apply_only_to_selected_rows(self):
        from PyQt6.QtWidgets import QTableWidgetSelectionRange
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=date.today() - timedelta(days=i),
                 description=f"Merchant {i}", amount=-20,
                 category="Groceries", import_hash=f"layout-batch-{i}")
            for i in range(3)]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        self.assertTrue(page.bulk_actions.isHidden())
        page.set_cat.setCurrentIndex(page.set_cat.findText("Dining"))
        self.assertFalse(page.apply_category_btn.isEnabled())
        page.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 4), True)
        self.assertFalse(page.bulk_actions.isHidden())
        self.assertEqual(page.selected_lbl.text(), "2 transactions selected")
        page.apply_category_btn.click()
        categories = self.win.store.transactions().set_index("description")["category"]
        self.assertEqual(categories["Merchant 0"], "Dining")
        self.assertEqual(categories["Merchant 1"], "Dining")
        self.assertEqual(categories["Merchant 2"], "Groceries")
        page.table.clearSelection()
        self.assertTrue(page.bulk_actions.isHidden())
        self.assertFalse(page.apply_category_btn.isEnabled())
        from finfore.ui.widgets import InfoDialog
        before = page.table.height()
        with patch.object(InfoDialog, 'exec', return_value=0) as help_dialog:
            page.category_help_btn.trigger()
            help_dialog.assert_called_once()
        page.search.setText("no matching merchant")
        self.assertEqual(page.table.rowCount(), 0)
        self.assertTrue(page.bulk_actions.isHidden())
        self.assertGreaterEqual(page.table.height(), before)

    def test_paycheck_reimbursement_dialog_keeps_bank_deposit_and_supports_undo(self):
        from finfore.ui.transactions import BusinessReimbursementDialog
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1,
            date=date.today(), description="Salary", amount=3500,
            category="Income", import_hash="business-deposit")]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        tx_id = int(self.win.store.transactions().iloc[0]["id"])
        dialog = BusinessReimbursementDialog(3500, 0, page)
        dialog.amount.setValue(500)
        page.table.selectRow(0)
        self.assertFalse(page.reimbursement_actions.isHidden())
        with patch("finfore.ui.transactions.BusinessReimbursementDialog", return_value=dialog), \
                patch.object(dialog, "exec", return_value=QDialog.DialogCode.Accepted):
            page.reimbursement_btn.click()
        tx = self.win.store.transactions().iloc[0]
        self.assertEqual(tx["amount"], 3500)
        self.assertEqual(tx["category"], "Income")
        self.assertEqual(tx["business_reimbursement"], 500)
        self.assertIn("$500", page.table.item(0, 4).toolTip())
        self.assertIn("Includes $500 reimbursement", page.table.item(0, 2).text())
        self.assertIn("$3,000", page.reimbursement_note.text())
        page.table.clearSelection()
        self.assertTrue(page.reimbursement_actions.isHidden())
        page.table.selectRow(0)
        dialog.amount.setValue(0)
        with patch("finfore.ui.transactions.BusinessReimbursementDialog", return_value=dialog), \
                patch.object(dialog, "exec", return_value=QDialog.DialogCode.Accepted):
            page.reimbursement_btn.click()
        self.assertEqual(self.win.store.transactions().iloc[0]["business_reimbursement"], 0)
        self.assertNotIn("Includes", page.table.item(0, 2).text())
        self.assertEqual(self.win.store.transactions().iloc[0]["amount"], 3500)
        self.win.undo()
        self.assertEqual(self.win.store.transactions().iloc[0]["business_reimbursement"], 500)
        self.assertIn("Includes $500 reimbursement", page.table.item(0, 2).text())
        self.win.undo()
        self.assertEqual(self.win.store.transactions().iloc[0]["business_reimbursement"], 0)

    def test_transaction_empty_states_import_reset_all_filters_and_retry_failed_read(self):
        self.win.show_page("Transactions")
        page = self.win.current_page()
        self.assertEqual(page._empty_mode, "new")
        with patch.object(self.win, "open_import") as importer:
            page.empty_action.click()
            importer.assert_called_once()
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=date.today()-timedelta(days=days),
                 description=f"Merchant {days}", amount=-20, category="Groceries",
                 import_hash=f"empty-recovery-{days}") for days in (0, 500)]))
        page.refresh()
        self.assertEqual(page.table.rowCount(), 1)
        page.f_account.setCurrentIndex(page.f_account.findData(1))
        page.f_category.setCurrentIndex(page.f_category.findData("Groceries"))
        page.f_since.setCurrentIndex(page.f_since.findData("custom"))
        page.search.setText("absent merchant")
        self.assertEqual(page._empty_mode, "filtered")
        page.empty_action.click()
        self.assertEqual(page.table.rowCount(), 2)
        self.assertIsNone(page.f_since.currentData())
        self.assertIsNone(page.f_account.currentData())
        self.assertIsNone(page.f_category.currentData())
        self.assertEqual(page.search.text(), "")
        self.assertTrue(page.empty_state.isHidden())
        with patch.object(self.win.store, "transactions", side_effect=RuntimeError("internal diagnostic")):
            page.refresh()
        self.assertEqual(page._empty_mode, "error")
        self.assertNotIn("internal diagnostic", page.empty_message.text())
        page.empty_action.click()
        self.assertEqual(page.table.rowCount(), 2)
        self.assertTrue(page.empty_state.isHidden())

    def transaction_hardening_fixture(self):
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=date.today()-timedelta(days=i+1),
                 description=f"Original {i}", amount=-20,
                 category="Groceries", import_hash=f"harden-{i}") for i in range(3)]))
        self.win.show_page("Transactions")
        return self.win.current_page()

    def test_transaction_selection_tracks_ids_after_import_and_category_filter_change(self):
        page = self.transaction_hardening_fixture()
        page.table.selectRow(1)
        selected = page._selected_ids()
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1,
            date=date.today(), description="Newer import", amount=-50,
            category="Groceries", import_hash="newer-import")]))
        page.refresh()
        self.assertEqual(page._selected_ids(), selected)
        page.f_category.setCurrentIndex(page.f_category.findData("Groceries"))
        self.assertEqual(page._selected_ids(), [])
        page.table.selectRow(1)
        page.table.cellWidget(1, page.CAT_COL).setCurrentText("Dining")
        page.table.cellWidget(1, page.CAT_COL).textActivated.emit('Dining')
        QTest.qWait(20)
        self.assertEqual(page._selected_ids(), [])
        self.assertTrue(page.bulk_actions.isHidden())
        page.table.selectAll()
        page.set_cat.setCurrentText("Dining")
        page.apply_category_btn.click()
        self.assertEqual(page.f_category.currentData(), "Groceries")
        self.assertEqual(page.table.rowCount(), 0)
        self.assertEqual(page._empty_mode, "filtered")

    def test_transaction_feedback_failure_rolls_back_category_and_review_state(self):
        page = self.transaction_hardening_fixture()
        self.win.store.undo.clear()
        self.win.refresh_header()
        page.table.selectRow(0)
        with patch.object(self.win.store, "_write_category_feedback", side_effect=RuntimeError("feedback write failed")), \
                patch.object(page, "_show_action_error") as error:
            page.table.cellWidget(0, page.CAT_COL).setCurrentText("Dining")
            page.table.cellWidget(0, page.CAT_COL).textActivated.emit('Dining')
            QTest.qWait(20)
        error.assert_called_once()
        self.assertFalse(self.win.a_undo.isEnabled())
        self.assertEqual(page.table.cellWidget(0, page.CAT_COL).currentText(), "Groceries")

    def test_transaction_failed_inline_save_restores_actual_category(self):
        page = self.transaction_hardening_fixture()
        with patch.object(self.win.store, "review_transactions", side_effect=PermissionError("read only")), \
                patch.object(page, "_show_action_error") as error:
            page.table.cellWidget(0, page.CAT_COL).setCurrentText("Dining")
            page.table.cellWidget(0, page.CAT_COL).textActivated.emit('Dining')
            QTest.qWait(20)
        error.assert_called_once()
        self.assertEqual(page.table.cellWidget(0, page.CAT_COL).currentText(), "Groceries")
        self.assertTrue(self.win.store.category_feedback().empty)

    def test_transaction_deleted_rows_cannot_teach_categories_or_open_stale_deposit(self):
        page = self.transaction_hardening_fixture()
        tx_id = page._df.iloc[0]["id"]
        self.win.store.delete_transactions([int(tx_id)])
        with patch.object(page, "_show_action_error"):
            page._recategorize([int(tx_id)], "Dining")
        self.assertTrue(self.win.store.category_feedback().empty)
        self.assertEqual(page.table.rowCount(), 2)
        with patch.object(page, "_show_action_error"):
            page._edit_business_reimbursement(int(tx_id))

    def test_transaction_failed_read_cannot_be_bypassed_by_search(self):
        page = self.transaction_hardening_fixture()
        with patch.object(self.win.store, "transactions", side_effect=RuntimeError("read failed")):
            page.refresh()
        page.search.setText("Original")
        self.assertEqual(page.table.rowCount(), 0)
        self.assertEqual(page._empty_mode, "error")
        page.empty_action.click()
        self.assertEqual(page.table.rowCount(), 3)

    def test_transaction_store_switch_discards_selection_and_batch_choice(self):
        page = self.transaction_hardening_fixture()
        page.table.selectRow(0)
        page.set_cat.setCurrentText("Dining")
        other = Store(self.root / "other-transactions.db")
        aid = other.add_account(Account("Other", AccountType.CHECKING, 1000, date.today()))
        other.add_transactions(pd.DataFrame([dict(account_id=aid, date=date.today(),
            description="Different ledger", amount=-10, category="Groceries", import_hash="other-ledger")]))
        self.win._switch_store(other, demo_key=None)
        self.assertEqual(page._selected_ids(), [])
        self.assertEqual(page.set_cat.currentIndex(), -1)
        self.assertTrue(page.bulk_actions.isHidden())

    def test_transaction_reimbursement_dialog_cannot_save_into_a_switched_ledger(self):
        from finfore.ui.transactions import BusinessReimbursementDialog
        self.add_account()
        self.win.store.add_transactions(pd.DataFrame([dict(account_id=1, date=date.today(),
            description="Original deposit", amount=3500, category="Income", import_hash="old-deposit")]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        tx_id = int(page._df.iloc[0]["id"])
        other = Store(self.root / "other-deposit.db")
        aid = other.add_account(Account("Other", AccountType.CHECKING, 1000, date.today()))
        other.add_transactions(pd.DataFrame([dict(account_id=aid, date=date.today(),
            description="Other deposit", amount=3500, category="Income", import_hash="new-deposit")]))
        dialog = BusinessReimbursementDialog(3500, 0, page)
        dialog.amount.setValue(500)
        def switch():
            self.win._switch_store(other, demo_key=None)
            return QDialog.DialogCode.Accepted
        with patch("finfore.ui.transactions.BusinessReimbursementDialog", return_value=dialog), \
                patch.object(dialog, "exec", side_effect=switch), \
                patch.object(page, "_show_action_error") as error:
            page._edit_business_reimbursement(tx_id)
        error.assert_called_once()
        self.assertEqual(other.transactions().iloc[0]["business_reimbursement"], 0)
        self.assertEqual(other.transactions().iloc[0]["amount"], 3500)

    def test_transaction_context_menus_release_native_objects(self):
        from PyQt6.QtWidgets import QMenu
        page = self.transaction_hardening_fixture()
        permanent_menus = set(page.findChildren(QMenu))
        page.table.selectRow(0)
        with patch.object(QMenu, "exec", return_value=None):
            for _ in range(5):
                page._context_menu(page.table.visualItemRect(page.table.item(0, 0)).center())
        QTest.qWait(30)
        self.assertEqual(set(page.findChildren(QMenu)), permanent_menus)

    def test_transaction_preferences_and_mid_render_failures_recover_without_blocked_controls(self):
        page = self.transaction_hardening_fixture()
        with patch.object(self.win.store, "category_feedback", side_effect=PermissionError("preferences unavailable")):
            self.assertTrue(page.refresh())
        self.assertEqual(page.table.rowCount(), 3)
        original = page._category_editor
        count = 0
        def broken_editor(row):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("render interrupted")
            return original(row)
        with patch.object(page, "_category_editor", side_effect=broken_editor):
            page.refresh()
        self.assertEqual(page.table.rowCount(), 0)
        self.assertEqual(page._empty_mode, "error")
        self.assertFalse(page._suspend_cell_signal)
        self.assertFalse(page.table.selectionModel().signalsBlocked())
        self.assertTrue(page.table.updatesEnabled())
        page.empty_action.click()
        page.table.selectRow(0)
        tx_id = page._selected_ids()[0]
        page.set_cat.setCurrentText("Dining")
        page.apply_category_btn.click()
        self.assertEqual(self.win.store.transactions().set_index("id").loc[tx_id, "category"], "Dining")

    def test_transaction_large_ledger_unicode_search_and_undated_history(self):
        self.add_account()
        special = "Café 東京 مرحبا 🧾 [.*] " + "long merchant description " * 10
        self.win.store.add_transactions(pd.DataFrame([
            dict(account_id=1, date=date.today()-timedelta(days=i % 300),
                 description=special if i == 0 else f"Merchant {i}",
                 amount=-1e9 if i == 0 else -20, category="Groceries",
                 import_hash=f"large-ledger-{i}") for i in range(1200)]))
        self.win.show_page("Transactions")
        page = self.win.current_page()
        self.assertEqual(page.table.rowCount(), 25)
        page._show_more()
        self.assertEqual(page.table.rowCount(), 50)
        for text in ("東京", "مرحبا", "🧾", "[.*]"):
            page.search.setText(text)
            self.assertEqual(page.table.rowCount(), 1)
            self.assertEqual(page.table.item(0, 2).text(), special)
        tx_id = int(self.win.store.transactions().query("description == @special").iloc[0]["id"])
        # Old or externally edited ledgers can contain dates the importer now rejects.
        self.win.store.con.execute("UPDATE transactions SET date='' WHERE id=?", (tx_id,))
        self.win.store.con.commit()
        page.refresh()
        self.assertEqual(page.table.rowCount(), 0)
        self.assertIn("1 without a date hidden", page.count_lbl.text())
        self.assertNotIn("older than", page.count_lbl.text())
        page.f_since.setCurrentIndex(page.f_since.findData(None))
        self.assertEqual(page.table.rowCount(), 1)
        self.assertEqual(page.table.item(0, 0).text(), "—")

    def test_goal_link_and_single_account_aware_import(self):
        # Both account types must remain reachable through the same command.
        self.add_account()
        loan_id = self.win.store.add_account(Account(
            "Loan", AccountType.LOAN, balance=500, balance_date=date.today()))
        with patch.object(self.win, "_pick_import_account",
                          return_value=self.win.store.account(loan_id)), \
                patch.object(self.win, "_import_balance_history") as balance_import:
            self.win.open_import()
            self.assertEqual(balance_import.call_args.args[0].id, loan_id)
        with patch.object(self.win, "_pick_import_account",
                          return_value=self.win.store.account(1)), \
                patch("finfore.ui.main_window.ImportDialog") as cash_import:
            cash_import.return_value.exec.return_value = 0
            self.win.open_import()
            self.assertEqual(cash_import.call_args.kwargs["account_id"], 1)
        self.win.show_page("Forecast")
        self.fc.manage_goals_btn.click()
        self.assertEqual(self.win.nav.currentItem().text(), "Plan")
        self.assertEqual(type(self.win.current_page()).__name__, "GoalsPage")
        file_menu = self.win.menuBar().actions()[0].menu()
        self.assertTrue(any(a.text() == "Back up data…" for a in file_menu.actions()))
        self.assertFalse(any("Import balance history" in a.text() for a in file_menu.actions()))
        self.assertEqual(self.fc.e_when.displayFormat(), "MMM d, yyyy")
        self.assertGreater(self.fc.e_when.minimumDate(), QDate.currentDate())

    def test_layer_choices_survive_forecast_updates(self):
        self.add_account()
        self.win.show_page("Forecast")
        self.assertIsNone(self.fc.plot_cash.plotItem.legend)
        self.fc.cash_legend.actions["median"].setChecked(False)
        self.assertFalse(self.fc.cash_series.visible["median"])
        self.fc.run()
        self.assertFalse(self.fc.cash_legend.states()["median"])
        self.assertTrue(all(not item.isVisible()
                            for item in self.fc.cash_series._groups["median"]))

    def test_goal_controls_remain_usable_at_minimum_window_size(self):
        self.add_account()
        self.win.store.add_goal(Goal("Vacation", 2000, "savings"))
        self.win.resize(980, 620)
        self.win.show_page("Goals")
        self.app.processEvents()
        goals = self.win.current_page()
        self.assertTrue(goals.ladder.isVisible())
        self.assertGreaterEqual(goals.table.height(), 100)
        self.assertGreaterEqual(goals.by_date.height(), goals.by_date.sizeHint().height())
        for button in goals.findChildren(QPushButton):
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())

    def test_goal_dialog_records_separate_purchase_savings_and_validates_amounts(self):
        from finfore.ui.goals_page import GoalDialog
        dlg = GoalDialog(self.win)
        self.assertEqual(dlg.kind.currentData(), "savings")
        dlg._ok()
        self.assertIn("name", dlg.error.text())
        dlg.name.setText("Vacation")
        dlg._ok()
        self.assertIn("greater than", dlg.error.text())
        dlg.amount.setValue(2000)
        dlg.saved.setValue(500)
        self.assertEqual(dlg.values(), ("Vacation", 2000, "savings", 500))
        dlg.saved.setValue(2001)
        dlg._ok()
        self.assertIn("more than the goal amount", dlg.error.text())
        legacy = GoalDialog(self.win, Goal("Cash milestone", 5000, "cash"))
        self.assertEqual(legacy.kind.currentData(), "cash")
        self.assertTrue(legacy.saved.isHidden())
        dlg.close()
        legacy.close()

    def test_goals_show_existing_and_extra_payments_and_update_custom_dates(self):
        store = self.win.store
        aid = store.add_account(Account("Car", AccountType.LOAN, 10000,
            date.today(), monthly_payment=300))
        store.set_setting("plan_surplus", 200)
        store.add_goal(Goal("Vacation", 2000, "savings"))
        store.set_plan_order([f"loan:{aid}"])
        self.win.show_page("Goals")
        page = self.win.current_page()
        self.assertEqual(page.table.item(0, 5).text(), "$500")
        self.assertIn("$300/mo existing + $200/mo extra = $500/mo total", page.payment_note.text())
        page.policy.setCurrentIndex(page.policy.findData("custom"))
        first_eta = page.table.item(0, 6).text()
        page._split_boxes[f"goal:{store.goals()[0].id}"].setValue(100)
        self.assertNotEqual(page.table.item(0, 6).text(), first_eta)
        self.assertIn("$300/mo existing + $0.00/mo extra", page.payment_note.text())
        self.assertEqual(page.table.horizontalHeaderItem(5).text(), "Share → Extra /mo")

    def test_custom_goal_shares_display_the_calculated_monthly_dollars(self):
        store = self.win.store
        first = store.add_goal(Goal("House", 10000, "savings"))
        second = store.add_goal(Goal("Car", 10000, "savings"))
        store.set_plan_order([f"goal:{first}", f"goal:{second}"])
        store.set_setting("plan_surplus", 350)
        self.win.show_page("Goals")
        page = self.win.current_page()
        page.policy.setCurrentIndex(page.policy.findData("custom"))
        for box in page._split_boxes.values():
            self.assertEqual(box.prefix(), "")
            box.setValue(100)
        for key in (f"goal:{first}", f"goal:{second}"):
            self.assertEqual(page._plan.by_key(key).monthly, 175)
            self.assertIn("$175.00", page._split_amounts[key].text())
            self.assertEqual(store.plan_allocations()[key], 100)
        # Changing a share must update the visible dollar result without
        # replacing the input the user is editing.
        editor = page._split_boxes[f"goal:{first}"]
        editor.setValue(0)
        self.assertIs(page._split_boxes[f"goal:{first}"], editor)
        self.assertIn("$0.00", page._split_amounts[f"goal:{first}"].text())
        self.assertIn("$350.00", page._split_amounts[f"goal:{second}"].text())
        page.surplus.setValue(400)
        self.assertIn("$400.00", page._split_amounts[f"goal:{second}"].text())

    def test_goal_planner_distinguishes_no_targets_from_all_targets_complete(self):
        self.win.show_page("Goals")
        page = self.win.current_page()
        self.assertIn("No goals or loans yet", page.faster.text())
        self.assertFalse(page.by_date.isEnabled())
        self.assertFalse(page.edit_btn.isEnabled())
        self.assertFalse(page.delete_btn.isEnabled())
        gid = self.win.store.add_goal(Goal("Completed reserve", 100, "savings", saved_amount=100))
        page.refresh()
        self.assertIn("already met", page.faster.text())
        self.assertIn("already complete", page.cascade.text())
        self.assertTrue(page.edit_btn.isEnabled())
        self.assertTrue(page.delete_btn.isEnabled())
        self.assertFalse(page.by_date.isEnabled())
        self.assertIn("Completed reserve", page.selection_note.text())
        page.funding_map.select_goal(gid)
        self.assertIn("Completed reserve", page.funding_map.selection_note.text())
        self.assertIn("Completed reserve", page.edit_btn.accessibleName())

    def test_forecast_directs_purchase_goals_to_the_plan_without_wrong_balance_eta(self):
        self.add_account()
        self.win.store.add_goal(Goal("Vacation", 2000, "savings", saved_amount=500))
        self.win.show_page("Forecast")
        self.assertEqual(self.fc._goal_labels, {})
        text = " ".join(label.text() for label in self.fc.findChildren(QLabel))
        self.assertIn("$500 set aside", text)
        self.assertNotIn("No goals yet", text)


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))


if __name__ == "__main__":
    unittest.main()
