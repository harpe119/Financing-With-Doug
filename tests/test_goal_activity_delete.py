"""Deleting one goal activity entry, and saying what each activity does."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from finfore.funding import ACTION_HELP, ACTIONS, goal_buckets
from test_goal_funding_map import FundingFixture


class DeleteActivityTests(FundingFixture, unittest.TestCase):
    def goal(self, i=0):
        return next(g for g in self.store.goals() if g.id == self.gids[i])

    def entries(self, i=0):
        return self.store.goal_activity(self.gids[i])

    def test_deleting_a_contribution_takes_it_out_of_still_saved(self):
        self.record(0, 0, 300)
        self.store.delete_goal_activity(self.entries()[0]['id'])
        self.assertEqual(self.goal().saved_amount, 0)
        self.assertEqual(self.entries(), [])

    def test_deleting_a_purchase_puts_the_money_back_into_still_saved(self):
        self.record(0, 0, 300)
        self.record(0, 0, 100, 'purchase')
        purchase = next(e for e in self.entries() if e['action'] == 'purchase')
        self.store.delete_goal_activity(purchase['id'])
        self.assertEqual((self.goal().saved_amount, self.goal().spent_amount), (300, 0))

    def test_deleting_a_release_restores_the_savings(self):
        self.record(0, 0, 300)
        self.record(0, 0, 50, 'release')
        release = next(e for e in self.entries() if e['action'] == 'release')
        self.store.delete_goal_activity(release['id'])
        self.assertEqual(self.goal().saved_amount, 300)

    def test_deleting_a_location_returns_the_money_to_no_account(self):
        self.store.record_goal_activity(self.gids[0], 'contribute', 200, self.today, None)
        self.record(0, 1, 200, 'assign')
        assign = next(e for e in self.entries() if e['action'] == 'assign')
        self.store.delete_goal_activity(assign['id'])
        self.assertEqual(self.goal().saved_amount, 200)
        self.assertEqual(goal_buckets(self.store, self.gids[0])[None], 200)

    def test_a_contribution_that_was_since_spent_cannot_be_deleted(self):
        self.record(0, 0, 300)
        self.record(0, 0, 250, 'purchase')
        contribution = next(e for e in self.entries() if e['action'] == 'contribute')
        with self.assertRaisesRegex(ValueError, 'Later activity depends'):
            self.store.delete_goal_activity(contribution['id'])
        self.assertEqual(self.goal().saved_amount, 50)

    def test_a_deletion_can_be_undone(self):
        self.store.enable_undo()
        self.record(0, 0, 300)
        self.store.delete_goal_activity(self.entries()[0]['id'])
        self.store.undo.undo()
        self.assertEqual(self.goal().saved_amount, 300)
        self.assertEqual(len(self.entries()), 1)

    def test_every_activity_says_what_it_does(self):
        self.assertEqual(set(ACTION_HELP), set(ACTIONS))
        self.assertIn('does not change', ACTION_HELP['assign'])
        self.assertIn('Purchases', ACTION_HELP['purchase'])


class DeleteActivityDialogTests(FundingFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_usability import desktop_test_app
        cls.app = desktop_test_app()

    def dialog(self):
        from finfore.ui.goal_funding import GoalFundingDialog
        return GoalFundingDialog(self.store, self.gids[0])

    def test_delete_is_enabled_only_with_an_entry_selected_and_removes_it(self):
        self.record(0, 0, 300)
        dlg = self.dialog()
        self.assertFalse(dlg.delete_btn.isEnabled())
        dlg.table.selectRow(0)
        self.assertTrue(dlg.delete_btn.isEnabled())
        with patch('finfore.ui.accounts.confirm', return_value=True):
            dlg.delete_btn.click()
        self.assertEqual(dlg.table.rowCount(), 0)
        self.assertEqual(self.store.goals()[0].saved_amount, 0)
        dlg.deleteLater()

    def test_keeping_it_changes_nothing(self):
        self.record(0, 0, 300)
        dlg = self.dialog()
        dlg.table.selectRow(0)
        with patch('finfore.ui.accounts.confirm', return_value=False):
            dlg.delete_btn.click()
        self.assertEqual(dlg.table.rowCount(), 1)
        dlg.deleteLater()

    def test_the_chosen_activity_is_explained_under_its_name(self):
        dlg = self.dialog()
        for i in range(dlg.action.count()):
            dlg.action.setCurrentIndex(i)
            key = dlg.action.currentData()
            self.assertEqual(dlg.action_help.text(), ACTION_HELP[key])
            self.assertEqual(dlg.action.itemData(i, 3), ACTION_HELP[key])  # ToolTipRole
        dlg.deleteLater()


if __name__ == '__main__':
    unittest.main()
