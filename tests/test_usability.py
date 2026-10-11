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
from PyQt6.QtCore import QDate, QPoint, QSettings, Qt, QTimer
from PyQt6.QtGui import QValidator
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from finfore.models import Account, AccountType, Goal, OneOffEvent, RecurringItem
from finfore.calendar import upcoming_schedule
from finfore.recurrence import normalize_desc
from finfore.store import Store
from finfore.ui import prefs
from finfore.ui.main_window import MainWindow
from finfore.ui.widgets import MoneySpinBox
from finfore.ui import video


class CurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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

    def test_missing_video_and_missing_player_report_failure(self):
        with patch.object(video, "video_path", return_value=None), \
                patch.object(video.QMessageBox, "warning") as warning:
            self.assertFalse(video.play())
            warning.assert_called_once()
        with patch.object(video.QDesktopServices, "openUrl", return_value=False), \
                patch.object(video.QMessageBox, "warning") as warning:
            self.assertFalse(video.play())
            self.assertIn(str(video.video_path()), warning.call_args.args[2])


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
        cls.app = QApplication.instance() or QApplication([])

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
        self.win.store.close()
        self.app.processEvents()
        self.prefs_patch.stop()
        self.hook_patch.stop()
        self.temp.cleanup()
        self.assertEqual(self.slot_errors, [], "Unexpected exception in a Qt callback")

    def add_account(self):
        self.win.store.add_account(Account("Checking", AccountType.CHECKING,
                                           balance=2000, balance_date=date.today()))

    def test_six_sections_and_nested_pages_refresh(self):
        self.assertEqual([self.win.nav.item(i).text() for i in range(self.win.nav.count())],
                         ["Dashboard", "Accounts", "Transactions", "Bills & payday", "Goals", "Forecast"])
        for name, page_type, parent in (("Spending", "InsightsPage", "Dashboard"),
                ("Recurring", "RecurringPage", "Bills & payday"),
                ("Progress", "ProgressPage", "Forecast"),
                ("Calendar", "CalendarPage", "Bills & payday")):
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
        self.app.processEvents()
        self.assertGreaterEqual(self.fc.chart_split.height(), self.fc.chart_split.minimumHeight())
        for button in (self.fc.video_btn, self.fc.reset_btn):
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())
        chart_bottom = self.fc.plot_cash.mapTo(self.fc.cash_card, QPoint(0, self.fc.plot_cash.height())).y()
        legend_top = self.fc.cash_legend.mapTo(self.fc.cash_card, QPoint(0, 0)).y()
        self.assertGreaterEqual(legend_top, chart_bottom)

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
        self.assertEqual(observed, [7])
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
        self.assertTrue(dash.setup.isVisible())
        self.assertTrue(dash.accounts_btn.isVisible())
        self.assertFalse(dash.import_btn.isVisible())
        self.add_account()
        dash.refresh()
        self.assertTrue(dash.setup.isVisible())
        self.assertTrue(dash.import_btn.isVisible())
        self.assertFalse(dash.accounts_btn.isVisible())
        self.win.store.add_transactions(pd.DataFrame([{
            "account_id": 1, "date": date.today(), "description": "Income",
            "amount": 100, "category": "Income", "import_hash": "setup",
        }]))
        dash.refresh()
        self.assertFalse(dash.setup.isVisible())

    def test_sliders_debounce_and_reset_is_available_in_simple_view(self):
        self.add_account()
        self.win.show_page("Forecast")
        self.assertTrue(self.fc.reset_btn.isVisible())
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
        self.assertEqual(self.win.nav.currentItem().text(), "Goals")
        file_menu = self.win.menuBar().actions()[0].menu()
        self.assertTrue(any(a.text() == "Back up data…" for a in file_menu.actions()))
        self.assertFalse(any("Import balance history" in a.text() for a in file_menu.actions()))
        self.assertEqual(self.fc.e_when.displayFormat(), "d MMM yyyy")
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
        self.win.resize(980, 620)
        self.win.show_page("Goals")
        self.app.processEvents()
        goals = self.win.current_page()
        self.assertGreaterEqual(goals.table.height(), 100)
        self.assertGreaterEqual(goals.by_date.height(), goals.by_date.sizeHint().height())
        for button in goals.findChildren(QPushButton):
            self.assertGreaterEqual(button.width(), button.sizeHint().width(), button.text())


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))


if __name__ == "__main__":
    unittest.main()
