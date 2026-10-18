"""Recorded goal locations, investment holdings and native map interactions."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QBoxLayout, QDialog, QLabel

from finfore.forecast import run_forecast
from finfore.funding import cash_capacity, funding_capacity, funding_overview, goal_buckets
from finfore.models import Account, AccountType, Goal, Scenario
from finfore.store import Store
from finfore.ui import prefs
from finfore.ui.goal_funding_map import GoalFundingMap
from test_usability import desktop_test_app


class FundingFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'funding.db')
        self.today = date.today()
        self.aids = [self.store.add_account(Account(name, kind, 1000, self.today,
            annual_return=0, volatility=0)) for name, kind in
            [('Checking', AccountType.CHECKING), ('Savings', AccountType.SAVINGS),
             ('Money market', AccountType.SAVINGS), ('CD', AccountType.INVESTMENT)]]
        self.gids = [self.store.add_goal(Goal(name, target, 'savings')) for name, target in
                     [('Baseline', 1000), ('Funded goal', 1500), ('House', 10000), ('New goal', 500)]]
        self.store.set_plan_order([f'goal:{gid}' for gid in self.gids])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, goal, account, amount, action='contribute'):
        self.store.record_goal_activity(self.gids[goal], action, amount,
                                        self.today, self.aids[account])

    def allocate_example(self):
        for goal, account, value in [(0, 0, 1000), (1, 1, 1000), (1, 2, 500),
                                     (2, 2, 500), (2, 3, 1000)]:
            self.record(goal, account, value)


class FundingOverviewTests(FundingFixture, unittest.TestCase):
    def test_four_accounts_reconcile_to_four_thousand_without_counting_targets_twice(self):
        self.allocate_example()
        view = funding_overview(self.store)
        self.assertEqual((view.cash, view.investments, view.unassigned), (3000, 1000, 0))
        self.assertEqual(sum(g.saved for g in view.goals), 4000)
        self.assertEqual([g.status for g in view.goals], ['Funded', 'Funded', 'Partially funded', 'Not funded'])
        self.assertEqual([g.remaining for g in view.goals], [0, 0, 8500, 500])
        self.assertEqual(view.goals[2].accounts, {self.aids[2]: 500, self.aids[3]: 1000})
        self.assertEqual([a.unassigned for a in view.accounts], [0, 0, 0, 0])
        with self.assertRaises(ValueError):
            self.record(3, 3, 1)

    def test_unlocated_savings_are_not_inferred_and_locating_does_not_add_savings(self):
        gid = self.gids[2]
        self.store.update_goal(gid, saved_amount=1500)
        before = self.store.transactions()
        view = funding_overview(self.store)
        self.assertEqual(view.goals[2].unlocated, 1500)
        self.assertEqual(view.goals[2].accounts, {})
        self.assertEqual(sum(a.unassigned for a in view.accounts), 4000)
        self.assertEqual(view.unassigned, 2500)
        self.record(2, 3, 1000, 'assign')
        goal = funding_overview(self.store).goals[2]
        self.assertEqual((goal.saved, goal.unlocated, goal.accounts[self.aids[3]]), (1500, 500, 1000))
        self.assertEqual(len(self.store.transactions()), len(before))
        self.assertTrue(all(self.store.account(aid).balance == 1000 for aid in self.aids))
        self.assertEqual(cash_capacity(self.store)[1], 2500)
        self.assertEqual(funding_capacity(self.store)[1], 2500)

    def test_locating_investments_does_not_sell_them_or_increase_forecast_cash(self):
        self.store.update_goal(self.gids[2], saved_amount=1000)
        before = run_forecast(self.store, 60, 20, Scenario(follow_goal_plan=True))
        self.record(2, 3, 1000, 'assign')
        after = run_forecast(self.store, 60, 20, Scenario(follow_goal_plan=True))
        np.testing.assert_array_equal(before.cash_det, after.cash_det)
        np.testing.assert_array_equal(before.networth_det, after.networth_det)
        self.assertEqual(after.cash_det[0], 3000)
        self.assertEqual(after.networth_det[0], 4000)
        self.assertEqual(cash_capacity(self.store)[1], 3000)
        self.assertEqual(funding_capacity(self.store)[1], 3000)

    def test_purchases_and_releases_change_progress_and_held_money_separately(self):
        self.record(1, 1, 1000)
        self.record(1, 1, 400, 'purchase')
        goal = funding_overview(self.store).goals[1]
        self.assertEqual((goal.saved, goal.spent, goal.remaining), (600, 400, 500))
        self.record(1, 1, 200, 'release')
        goal = funding_overview(self.store).goals[1]
        self.assertEqual((goal.saved, goal.spent, goal.remaining), (400, 400, 700))

    def test_falling_balance_is_reported_without_silently_changing_goal_records(self):
        self.allocate_example()
        self.store.add_snapshot(self.aids[3], self.today, 800)
        view = funding_overview(self.store)
        self.assertEqual(view.unassigned, -200)
        self.assertEqual(next(a for a in view.accounts if a.id == self.aids[3]).unassigned, -200)
        self.assertEqual(view.goals[2].saved, 1500)
        self.assertEqual(view.goals[2].accounts[self.aids[3]], 1000)

    def test_milestones_do_not_claim_the_same_funding_again_and_delete_preserves_unknown_location(self):
        self.allocate_example()
        self.store.add_goal(Goal('Cash baseline milestone', 1000, 'cash'))
        self.assertEqual(len(funding_overview(self.store).goals), 4)
        self.assertEqual(funding_overview(self.store).unassigned, 0)
        self.store.enable_undo()
        self.store.delete_account(self.aids[3])
        self.assertEqual(goal_buckets(self.store, self.gids[2])[None], 1000)
        self.assertEqual(funding_overview(self.store).goals[2].unlocated, 1000)
        self.store.undo.undo()
        self.assertEqual(funding_overview(self.store).goals[2].unlocated, 0)
        self.assertEqual(funding_overview(self.store).unassigned, 0)

    def test_card_debt_reduces_funding_capacity_without_inventing_goal_claims(self):
        from finfore.goals import build_plan
        self.store.add_account(Account('Card', AccountType.CREDIT, -5000, self.today))
        self.assertEqual(funding_capacity(self.store)[1], -1000)
        self.assertEqual(funding_overview(self.store).unassigned, -1000)
        self.assertFalse(any('Goal savings recorded exceed' in w
                             for w in build_plan(self.store, surplus=0).warnings))


class FundingMapTests(FundingFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        super().setUp()
        self.widget = GoalFundingMap()
        self.widget.resize(1060, 650)

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        super().tearDown()

    def refresh(self):
        self.widget.refresh(self.store)
        self.widget.show()
        self.app.processEvents()

    def test_read_only_refresh_selection_and_keyboard_navigation(self):
        self.allocate_example()
        changes = self.store.con.total_changes
        self.refresh()
        self.assertIn('$4,000.00', self.widget.summary.text())
        self.assertEqual(self.store.con.total_changes, changes)
        self.widget.group.button(self.gids[2]).click()
        self.assertEqual(self.widget.account_chart.selected, self.gids[2])
        self.assertTrue(self.widget.record_btn.isEnabled())
        self.assertFalse(self.widget.locate_btn.isEnabled())
        chart = self.widget.account_chart
        chart.setFocus()
        QTest.keyClick(chart, Qt.Key.Key_Right)
        self.assertEqual(self.widget.selected_goal, self.gids[3])
        self.widget.clear_btn.click()
        self.assertIsNone(self.widget.selected_goal)
        self.assertEqual(self.store.con.total_changes, changes)

    def test_selecting_an_account_segment_highlights_its_goal(self):
        self.allocate_example()
        self.refresh()
        chart = self.widget.account_chart
        rect = next(rect for rect, gid, _text in chart.hit_regions if gid == self.gids[1])
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
        self.assertEqual(self.widget.selected_goal, self.gids[1])
        self.assertTrue(self.widget.group.button(self.gids[1]).isChecked())

    def test_unlocated_action_is_explicit_and_milestones_are_explained(self):
        self.store.update_goal(self.gids[2], saved_amount=1500)
        self.store.add_goal(Goal('Baseline milestone', 1000, 'cash'))
        self.refresh()
        self.widget.select_goal(self.gids[2])
        self.assertIn('$1,500.00', self.widget.location_note.text())
        self.assertTrue(self.widget.locate_btn.isEnabled())
        calls = []
        self.widget.recordRequested.connect(lambda gid, locate: calls.append((gid, locate)))
        self.widget.locate_btn.click()
        self.assertEqual(calls, [(self.gids[2], True)])
        text = ' '.join(label.text() for label in self.widget.milestones.findChildren(QLabel))
        self.assertIn('do not reserve an account', text)
        self.assertEqual(funding_overview(self.store).goals[2].unlocated, 1500)

    def test_actual_locate_dialog_can_split_existing_savings_into_a_cd(self):
        from finfore.ui.main_window import MainWindow
        from finfore.ui.goal_funding import GoalFundingDialog
        self.store.update_goal(self.gids[2], saved_amount=1500)
        settings = str(Path(self.temp.name) / 'prefs.ini')
        with patch.object(prefs, 'app_settings', lambda: QSettings(settings, QSettings.Format.IniFormat)):
            win = MainWindow(self.store)
            win.show()
            win.show_page('Goals')
            page = win.current_page()
            page.funding_map.select_goal(self.gids[2])
            def locate(dialog):
                self.assertEqual(dialog.action.currentData(), 'assign')
                self.assertIsNone(dialog.account.currentData())
                self.assertEqual(dialog.amount.value(), 1500)
                dialog.account.setCurrentIndex(dialog.account.findData(self.aids[3]))
                self.assertIn('does not make it spendable cash', dialog.context.text())
                dialog.amount.setValue(1000)
                dialog._record()
                return QDialog.DialogCode.Accepted
            with patch.object(GoalFundingDialog, 'exec', locate):
                page.funding_map.locate_btn.click()
            self.assertEqual(page.funding_map.overview.goals[2].unlocated, 500)
            self.assertEqual(page.funding_map.overview.goals[2].saved, 1500)
            win.close()
            win.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_empty_state_and_selection_reset_when_the_database_changes(self):
        self.refresh()
        self.widget.select_goal(self.gids[0])
        other = Store(Path(self.temp.name) / 'empty.db')
        try:
            self.widget.refresh(other)
            self.assertIsNone(self.widget.selected_goal)
            self.assertEqual(self.widget.rows, {})
            self.assertIn('$0.00', self.widget.summary.text())
            self.assertFalse(self.widget.record_btn.isEnabled())
        finally:
            other.close()

    def test_minimum_width_stacks_the_map_and_keeps_full_names_as_plain_text(self):
        self.allocate_example()
        account = self.store.account(self.aids[2])
        account.name = '<Money market> with a long account name for household savings'
        self.store.update_account(account)
        self.store.update_goal(self.gids[2], name='<House> with a long descriptive target name')
        self.refresh()
        self.widget.resize(740, self.widget.height())
        self.app.processEvents()
        self.assertEqual(self.widget.columns.direction(), QBoxLayout.Direction.TopToBottom)
        self.assertLessEqual(self.widget.minimumSizeHint().width(), 740)
        labels = self.widget.rows[self.gids[2]].findChildren(QLabel)
        self.assertTrue(any(account.name in label.text() for label in labels))
        self.assertTrue(all(label.textFormat() == Qt.TextFormat.PlainText for label in labels))


def run_tests():
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(FundingOverviewTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(FundingMapTests)])
    return unittest.TextTestRunner().run(suite)


if __name__ == '__main__':
    unittest.main()
