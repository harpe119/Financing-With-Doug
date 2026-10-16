"""Provenance, human labels, scope control and protected financial categories."""
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finfore.category_review import needs_attention, reviewed_dataset
from finfore.categorize import BUSINESS_CATEGORY
from finfore.learning import (apply_chain, feedback_key, full_chain, record_corrections,
    recategorize_all, uncategorized_clusters)
from finfore.models import Account, AccountType
from finfore.pipeline import refresh_derived
from finfore.store import Store


class CategoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'review.db'
        self.store = Store(self.path)
        self.aid = self.store.add_account(Account('Checking', AccountType.CHECKING, 5000, date.today()))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, desc, amount=-50, category='Misc', auto=False, aid=None, when=None, identity=None):
        identity = identity or f'row-{len(self.store.transactions())}'
        frame = pd.DataFrame([dict(account_id=aid or self.aid, description=desc, amount=amount,
            category=category, date=when or date.today(), import_hash=identity)])
        if auto:
            frame = apply_chain(frame, full_chain(self.store))
        self.store.add_transactions(frame)
        return int(self.store.con.execute('SELECT id FROM transactions WHERE import_hash=?', (identity,)).fetchone()[0])

    def row(self, txid):
        return self.store.transactions().set_index('id').loc[txid]

    def test_new_import_explains_keyword_fallback_broad_merchant_and_exact_saved_rule(self):
        rent = self.add('APARTMENT RENT', auto=True)
        unknown = self.add('MYSTERY VENDOR', auto=True)
        amazon = self.add('AMAZON MKTPL', auto=True)
        self.assertEqual(self.row(rent).category_source, 'keyword')
        self.assertIn('RENT', self.row(rent).category_reason)
        self.assertEqual(self.row(unknown).category_source, 'fallback')
        self.assertEqual(self.row(amazon).category_source, 'broad_merchant')
        self.store.review_transactions([amazon], 'Groceries')
        new = self.add('AMAZON MKTPL #44', auto=True)
        self.assertEqual(self.row(new).category, 'Groceries')
        self.assertEqual(self.row(new).category_source, 'saved_rule')
        self.assertFalse(self.row(new).category_reviewed)
        self.assertEqual(len(reviewed_dataset(self.store)), 1)

    def test_similarity_is_unreviewed_and_never_becomes_a_human_training_label(self):
        source = self.add('BLUE BOTTLE COFFEE')
        self.store.review_transactions([source], 'Dining')
        near = self.add('SQ *BLUE BOTTLE 042', auto=True)
        self.assertEqual(self.row(near).category_source, 'similarity')
        self.assertIn('BLUE BOTTLE COFFEE', self.row(near).category_reason)
        self.assertIn('not a probability', self.row(near).category_reason)
        self.assertTrue(needs_attention(self.store.transactions()).loc[self.store.transactions().id.eq(near)].iloc[0])
        self.assertEqual(list(reviewed_dataset(self.store).transaction_id), [source])

    def test_one_off_corrections_preserve_existing_rule_but_export_both_labels(self):
        a = self.add('AMAZON MKTPL')
        self.store.review_transactions([a], 'Shopping', remember=True)
        b = self.add('AMAZON MKTPL #10', auto=True)
        self.store.review_transactions([b], 'Groceries', remember=False)
        c = self.add('AMAZON MKTPL #20', auto=True)
        self.assertEqual(self.row(c).category, 'Shopping')
        data = reviewed_dataset(self.store)
        self.assertEqual(list(data.label), ['Shopping', 'Groceries'])
        self.assertEqual(data.merchant_group.nunique(), 1)
        self.assertEqual(list(self.store.category_feedback().remember), [1, 0])

    def test_one_off_transfer_override_is_protected_during_refresh_and_full_recategorization(self):
        aid = self.store.add_account(Account('Savings', AccountType.SAVINGS, 1000, date.today()))
        left = self.add('TRANSFER TO SAVINGS', category='Debt payment')
        right = self.add('BANK CREDIT', amount=50, category='Other income', aid=aid)
        self.store.review_transactions([left], 'Housing', remember=False)
        refresh_derived(self.store, checkpoint=False)
        recategorize_all(self.store, only_unknown=False)
        self.assertEqual(self.row(left).category, 'Housing')
        self.assertNotEqual(self.row(right).category, 'Transfer')
        self.assertEqual(self.store.learned_categories(), {})

    def test_transfer_pair_has_evidence_but_is_not_verified_training_data(self):
        aid = self.store.add_account(Account('Savings', AccountType.SAVINGS, 1000, date.today()))
        left = self.add('TRANSFER TO SAVINGS')
        right = self.add('BANK CREDIT', amount=50, category='Other income', aid=aid)
        refresh_derived(self.store, checkpoint=False)
        for txid in (left, right):
            self.assertEqual(self.row(txid).category_source, 'transfer_pair')
            self.assertFalse(self.row(txid).category_reviewed)
        self.assertTrue(reviewed_dataset(self.store).empty)
        self.store.review_transactions([left, right], remember=False)
        self.assertEqual(len(reviewed_dataset(self.store)), 2)

    def test_confirmation_changes_no_bank_amounts_and_is_undoable(self):
        txid = self.add('RENT', category='Housing')
        self.store.enable_undo()
        before = self.row(txid)
        result = self.store.review_transactions([txid], remember=False)
        after = self.row(txid)
        self.assertEqual((result['changed'], result['reviewed']), (0,1))
        for key in ('amount','date','account_id','description'):
            self.assertEqual(before[key], after[key])
        self.assertTrue(after.category_reviewed)
        self.store.undo.undo()
        self.assertFalse(self.row(txid).category_reviewed)
        self.assertTrue(self.store.category_feedback().empty)
        self.store.undo.redo()
        self.store.close()
        self.store = Store(self.path)
        self.assertTrue(self.row(txid).category_reviewed)

    def test_feedback_failure_rolls_back_entire_decision(self):
        txid = self.add('MYSTERY')
        self.store.enable_undo()
        with patch.object(self.store, '_write_category_feedback', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.review_transactions([txid], 'Dining')
        self.assertEqual(self.row(txid).category, 'Misc')
        self.assertFalse(self.row(txid).category_reviewed)
        self.assertFalse(self.store.undo.can_undo)
        self.assertTrue(self.store.category_feedback().empty)

    def test_missing_selection_and_invalid_batch_cannot_partially_write(self):
        txid = self.add('MYSTERY')
        for ids, category in [([txid, 999], 'Dining'), ([txid], ''),
                              ([txid], {txid:'Dining', 999:'Shopping'})]:
            with self.assertRaises(ValueError): self.store.review_transactions(ids, category)
        self.assertFalse(self.row(txid).category_reviewed)
        self.assertEqual(self.row(txid).category, 'Misc')

    def test_conflicting_confirmations_require_explicit_one_off_scope(self):
        a = self.add('AMAZON MKTPL', category='Shopping')
        b = self.add('AMAZON MKTPL #10', category='Groceries')
        with self.assertRaisesRegex(ValueError, 'Uncheck Remember'):
            self.store.review_transactions([a,b])
        self.assertTrue(self.store.category_feedback().empty)
        self.store.review_transactions([a,b], remember=False)
        self.assertEqual(set(reviewed_dataset(self.store).label), {'Shopping','Groceries'})
        self.assertEqual(self.store.learned_categories(), {})

    def test_dataset_excludes_business_split_deposits_unknown_and_future_transactions(self):
        ids = [self.add('Business flight', -100, BUSINESS_CATEGORY),
               self.add('Salary', 1000, 'Income'), self.add('Unknown', -10, 'Misc'),
               self.add('Future rent', -100, 'Housing', when=date.today()+timedelta(days=1)),
               self.add('Groceries', -20, 'Groceries')]
        self.store.set_business_reimbursement(ids[1], 200)
        self.store.review_transactions(ids, remember=False)
        frame = reviewed_dataset(self.store)
        self.assertEqual(list(frame.transaction_id), [ids[-1]])
        self.assertEqual(frame.iloc[0].amount, -20)
        self.assertEqual(frame.iloc[0].account_type, 'checking')
        self.assertEqual(frame.iloc[0].label_source, 'human_review')

    def test_past_cutoff_cannot_leak_a_label_recorded_later(self):
        yesterday = date.today()-timedelta(days=1)
        txid = self.add('GROCERIES', category='Groceries', when=yesterday)
        self.store.review_transactions([txid], remember=False)
        self.assertTrue(reviewed_dataset(self.store, as_of=yesterday).empty)
        self.assertEqual(len(reviewed_dataset(self.store, as_of=date.today())), 1)

    def test_export_groups_refunds_with_purchases_and_never_includes_future_cashflows(self):
        a = self.add('RETAILER STORE 123', -50, 'Shopping')
        b = self.add('RETAILER STORE 456', 20, 'Shopping')
        future = self.add('FUTURE VENDOR', -100, 'Travel', when=date.today()+timedelta(days=5))
        self.store.review_transactions([a,b,future], remember=False)
        frame = reviewed_dataset(self.store, as_of=date.today()+timedelta(days=10))
        self.assertEqual(set(frame.transaction_id), {a,b})
        self.assertEqual(frame.merchant_group.nunique(), 1)

    def test_legacy_merchant_feedback_cannot_become_transaction_truth(self):
        txid = self.add('Merchant', category='Shopping')
        record_corrections(self.store, self.store.transactions(), 'Shopping')
        self.assertTrue(reviewed_dataset(self.store).empty)
        self.assertFalse(self.row(txid).category_reviewed)
        self.store.review_transactions([txid])
        self.assertEqual(len(reviewed_dataset(self.store)), 1)

    def test_duplicate_import_cannot_reset_review_state_or_provenance(self):
        txid = self.add('MYSTERY', identity='unique')
        self.store.review_transactions([txid], 'Groceries', remember=False)
        self.add('MYSTERY', auto=True, identity='unique')
        self.assertEqual(self.row(txid).category, 'Groceries')
        self.assertEqual(self.row(txid).category_source, 'manual')
        self.assertTrue(self.row(txid).category_reviewed)

    def test_migration_preserves_legacy_rules_without_inventing_human_reviews(self):
        path = Path(self.temp.name)/'old.db'
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE category_feedback(id INTEGER PRIMARY KEY,norm_key TEXT,description TEXT,amount REAL,account_type TEXT,old_category TEXT,new_category TEXT,source TEXT,created_at TEXT)')
            con.execute("INSERT INTO category_feedback VALUES (1,'MYSTERY|-','MYSTERY',-50,'checking','Misc','Groceries','manual','2026-01-01')")
        old = Store(path)
        try:
            self.assertEqual(old.learned_categories(), {'MYSTERY|-':'Groceries'})
            self.assertEqual(old.category_feedback().iloc[0].remember, 1)
            self.assertTrue(reviewed_dataset(old).empty)
        finally: old.close()


def run_tests():
    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CategoryReviewTests))

if __name__ == '__main__': unittest.main()
