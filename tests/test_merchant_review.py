"""Batch review by exact name, keeping personal and business decisions separate."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QTableWidget

from finfore.categorize import BUSINESS_CATEGORY
from finfore.models import Account, AccountType
from finfore.pipeline import import_csv
from finfore.ui.import_dialog import ImportDialog
from finfore.ui.merchant_review import MerchantReviewDialog
from test_onboard import Fixture


class MerchantReviewTests(Fixture, unittest.TestCase):
    def seed(self, merchant='Chipotle Mexican Grill', category='Dining'):
        aid = self.store.add_account(Account('Checking', AccountType.CHECKING, 5000, date.today()))
        other = self.store.add_account(Account('Savings', AccountType.SAVINGS, 1000, date.today()))
        self.store.add_transactions(pd.DataFrame([
            dict(account_id=account, date=date.today()-timedelta(days=age),
                 description=name, amount=-amount, category=label, import_hash=key)
            for account, age, name, amount, label, key in [
                (aid, 1, merchant, 20, category, 'first'),
                (other, 900, merchant.upper(), 25, category, 'older'),
                (aid, 2, merchant, 30, category, 'checked'),
                (aid, 3, merchant + ' other', 40, category, 'different')]]))
        ids = {key: int(rowid) for key, rowid in self.store.con.execute('SELECT import_hash,id FROM transactions')}
        self.store.review_transactions([ids['checked']], category, remember=False)
        page = self.go('Transactions')
        return ids, page

    def dialog(self, ids, page):
        dialog = MerchantReviewDialog(self.store, ids['first'], page._cats, page)
        return dialog

    def test_chipotle_review_includes_old_dates_and_other_accounts_but_preserves_checked_entries(self):
        ids, page = self.seed()
        before = self.store.transactions().set_index('id').copy()
        self.store.undo.clear()
        dialog = self.dialog(ids, page)
        self.assertEqual(set(dialog._selected_ids()), {ids['first'], ids['older']})
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertTrue(dialog.apply_btn.isEnabled())
        self.assertFalse(dialog.remember.isChecked())
        dialog._apply()
        after = self.store.transactions().set_index('id')
        self.assertEqual(int(after.category_reviewed.sum()), 3)
        pd.testing.assert_series_equal(after.loc[ids['checked']], before.loc[ids['checked']])
        pd.testing.assert_series_equal(after.loc[ids['different']], before.loc[ids['different']])
        pd.testing.assert_frame_equal(after[['amount', 'date', 'account_id', 'description']],
                                      before[['amount', 'date', 'account_id', 'description']])
        self.assertEqual(self.store.learned_categories(), {})
        self.store.undo.undo()
        pd.testing.assert_frame_equal(self.store.transactions().set_index('id'), before)

    def test_delta_travel_and_business_are_reviewed_as_separate_groups(self):
        ids, page = self.seed('Delta', 'Travel')
        self.store.set_category([ids['older']], BUSINESS_CATEGORY, source='keyword')
        before = self.store.transactions().set_index('id').loc[ids['older']].copy()
        dialog = self.dialog(ids, page)
        self.assertEqual(dialog._selected_ids(), [ids['first']])
        self.assertFalse(dialog.remember.isEnabled())
        dialog._apply()
        after = self.store.transactions().set_index('id')
        pd.testing.assert_series_equal(after.loc[ids['older']], before)
        self.assertEqual(after.loc[ids['first'], 'category'], 'Travel')
        self.assertTrue(after.loc[ids['first'], 'category_reviewed'])
        dialog.category.setCurrentText(BUSINESS_CATEGORY)
        dialog.same_category_btn.click()
        self.assertEqual(dialog._selected_ids(), [ids['older']])
        dialog._apply()
        self.assertTrue(self.store.transactions().set_index('id').loc[ids['older'], 'category_reviewed'])
        self.assertEqual(self.store.learned_categories(), {})

    def test_unknown_entries_need_explicit_category_and_selection(self):
        ids, page = self.seed(category='Misc')
        dialog = self.dialog(ids, page)
        self.assertEqual(dialog._selected_ids(), [])
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.category.setCurrentText('Dining')
        self.assertFalse(dialog.apply_btn.isEnabled())
        dialog.unknown_btn.click()
        self.assertEqual(set(dialog._selected_ids()), {ids['first'], ids['older']})
        dialog.clear_btn.click()
        self.assertEqual(dialog._selected_ids(), [])
        dialog.same_category_btn.click()
        self.assertEqual(dialog._selected_ids(), [])

    def test_individual_checkboxes_and_remember_are_explicit(self):
        ids, page = self.seed()
        dialog = self.dialog(ids, page)
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.ItemDataRole.UserRole) == ids['older']:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog._selected_ids(), [ids['first']])
        self.assertTrue(dialog.remember.isEnabled())
        dialog.remember.setChecked(True)
        dialog._apply()
        self.assertFalse(self.store.transactions().set_index('id').loc[ids['older'], 'category_reviewed'])
        self.assertEqual(list(self.store.learned_categories().values()), ['Dining'])

    def test_stale_preview_cannot_overwrite_a_new_human_review(self):
        ids, page = self.seed('Delta', 'Travel')
        dialog = self.dialog(ids, page)
        self.store.review_transactions([ids['first']], BUSINESS_CATEGORY, remember=False)
        before = self.store.transactions().copy()
        with patch('finfore.ui.merchant_review.QMessageBox.warning') as warning:
            dialog._apply()
        warning.assert_called_once()
        pd.testing.assert_frame_equal(self.store.transactions(), before)
        dialog.reload_btn.click()
        self.assertFalse(dialog.remember.isEnabled())
        self.assertNotIn(ids['first'], dialog._selected_ids())

    def test_failed_save_keeps_checks_and_rolls_back_review(self):
        ids, page = self.seed()
        dialog = self.dialog(ids, page)
        before = self.store.transactions().copy()
        with patch.object(self.store, '_write_category_feedback', side_effect=sqlite3.OperationalError('busy')), \
                patch('finfore.ui.merchant_review.QMessageBox.warning') as warning:
            dialog._apply()
        warning.assert_called_once()
        pd.testing.assert_frame_equal(self.store.transactions(), before)
        self.assertEqual(set(dialog._selected_ids()), {ids['first'], ids['older']})
        self.assertTrue(dialog.apply_btn.isEnabled())

    def test_checkboxes_work_from_keyboard_and_preserve_individual_choices(self):
        ids, page = self.seed()
        dialog = self.dialog(ids, page)
        dialog.show()
        self.settle()
        row = next(i for i in range(dialog.table.rowCount())
                   if dialog.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == ids['older'])
        dialog.table.setCurrentCell(row, 0)
        dialog.table.setFocus()
        QTest.keyClick(dialog.table, Qt.Key.Key_Space)
        self.assertEqual(dialog._selected_ids(), [ids['first']])
        QTest.keyClick(dialog.table, Qt.Key.Key_Space)
        self.assertEqual(set(dialog._selected_ids()), {ids['first'], ids['older']})

    def test_a_new_business_exception_prevents_remembering_from_a_stale_preview(self):
        ids, page = self.seed()
        dialog = self.dialog(ids, page)
        for i in range(dialog.table.rowCount()):
            item = dialog.table.item(i, 0)
            if item.data(Qt.ItemDataRole.UserRole) == ids['older']:
                item.setCheckState(Qt.CheckState.Unchecked)
        dialog.remember.setChecked(True)
        self.store.review_transactions([ids['older']], BUSINESS_CATEGORY, remember=False)
        with patch('finfore.ui.merchant_review.QMessageBox.warning') as warning:
            dialog._apply()
        warning.assert_called_once()
        self.assertFalse(self.store.transactions().set_index('id').loc[ids['first'], 'category_reviewed'])
        self.assertEqual(self.store.learned_categories(), {})

    def test_business_entries_in_a_rule_name_variant_prevent_remembering(self):
        ids, page = self.seed('Delta 123', 'Travel')
        aid = self.store.accounts()[0].id
        self.store.add_transactions(pd.DataFrame([dict(account_id=aid, date=date.today(),
            description='DELTA 456', amount=-300, category=BUSINESS_CATEGORY, import_hash='business-variant')]))
        dialog = self.dialog(ids, page)
        self.assertEqual(dialog.table.rowCount(), 3)  # exact names only in the review preview
        self.assertFalse(dialog.remember.isEnabled())  # remembered rules have broader matches
        dialog._apply()
        tx = self.store.transactions()
        self.assertFalse(tx.loc[tx.description.eq('DELTA 456'), 'category_reviewed'].iloc[0])
        self.assertEqual(self.store.learned_categories(), {})

    def test_page_action_opens_preview_for_exactly_one_selected_transaction(self):
        ids, page = self.seed()
        self.assertFalse(page.review_merchant_btn.isEnabled())
        for row in range(page.table.rowCount()):
            if page.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == ids['first']:
                page.table.selectRow(row)
                break
        self.assertTrue(page.review_merchant_btn.isEnabled())
        self.assertTrue(page.review_merchant_action.isEnabled())
        with patch('finfore.ui.transactions.MerchantReviewDialog') as dialog:
            page.review_merchant_btn.click()
        self.assertEqual(dialog.call_args.args[:2], (self.store, ids['first']))
        dialog.return_value.exec.assert_called_once()
        dialog.return_value.deleteLater.assert_called_once()

    def test_header_import_dialog_keeps_reviews_after_overlapping_import_and_refresh(self):
        aid = self.store.add_account(Account('Checking', AccountType.CHECKING, 5000, date.today()))
        path = Path(self.tmp.name) / 'bank.csv'
        initial = pd.DataFrame([
            dict(Date=(date.today()-timedelta(days=3)).isoformat(), Description='Chipotle Mexican Grill', Amount=-20),
            dict(Date=(date.today()-timedelta(days=2)).isoformat(), Description='Delta', Amount=-300)])
        initial.to_csv(path, index=False)
        import_csv(self.store, str(path), aid)
        ids = self.store.transactions().id.tolist()
        self.store.review_transactions(ids, {ids[0]: 'Dining', ids[1]: BUSINESS_CATEGORY}, remember=False)
        before = self.store.transactions().set_index('id').copy()
        page = self.go('Transactions')
        self.store.undo.clear()
        incoming = initial.copy()
        incoming.loc[0, 'Description'] = 'CHIPOTLE MEXICAN GRILL'
        pd.concat([incoming, pd.DataFrame([dict(Date=date.today().isoformat(),
            Description='Chipotle Mexican Grill', Amount=-25)])], ignore_index=True).to_csv(path, index=False)

        def import_from_dialog(dialog):
            self.assertEqual(dialog.account.currentData(), aid)
            dialog.path.setText(str(path))
            dialog._reload()
            dialog._import()
            self.assertEqual(dialog.result_counts, (1, 2))
            return dialog.result()

        with patch.object(ImportDialog, 'exec', import_from_dialog), \
                patch('finfore.ui.import_dialog.QMessageBox.information') as message:
            self.win.open_import()  # the entry point used by both File and the header
        self.settle()
        after = self.store.transactions().set_index('id')
        pd.testing.assert_frame_equal(after.loc[ids], before)
        pd.testing.assert_frame_equal(page._df.set_index('id').loc[ids], before)
        self.assertEqual(int(after.category_reviewed.sum()), 2)
        self.assertIn('Previously reviewed entries kept: 2', message.call_args.args[2])
        self.win.undo()
        pd.testing.assert_frame_equal(self.store.transactions().set_index('id'), before)

    def test_table_keeps_existing_reviews_read_only_and_scopes_all_matching_rows(self):
        ids, page = self.seed()
        dialog = self.dialog(ids, page)
        self.assertIn('3 matching transactions', dialog.summary.text())
        for i in range(dialog.table.rowCount()):
            item = dialog.table.item(i, 0)
            if item.data(Qt.ItemDataRole.UserRole) == ids['checked']:
                self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertIsInstance(dialog.table, QTableWidget)


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(MerchantReviewTests))


if __name__ == '__main__':
    unittest.main()
