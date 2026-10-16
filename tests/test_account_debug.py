"""Independent import-overlap and reconstructed account balance regressions."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finfore.history import (account_balance_history, cash_history, cash_anchor,
                             networth_history, transaction_balances)
from finfore.ingest import ImportConfig, make_hashes
from finfore.models import Account, AccountType
from finfore.pipeline import import_with_config, reimport_with_saved_mapping
from finfore.store import Store


class AccountDebugTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'ledger.db')
        self.aid = self.store.add_account(Account('Checking', AccountType.CHECKING,
                                                 500, date(2026, 9, 3)))
        self.cfg = ImportConfig('', self.aid, col_date='Date', col_desc='Description', col_amount='Amount')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def export(self, rows, name='bank.csv'):
        path = self.root / name
        pd.DataFrame(rows, columns=['Date','Description','Amount']).to_csv(path,index=False)
        return replace(self.cfg, path=str(path))

    def rows(self, rows):
        frame = pd.DataFrame(rows, columns=['date','description','amount'])
        frame['date'] = pd.to_datetime(frame.date)
        frame['account_id'] = self.aid
        frame['category'] = 'Misc'
        frame['import_hash'] = [f'legacy-{i}' for i in range(len(frame))]
        self.store.add_transactions(frame)

    def test_overlap_matches_legacy_hashes_and_preserves_corrections(self):
        self.rows([('2026-09-01','Grocery STORE',-100),('2026-09-02','PAYROLL',1000)])
        old = self.store.transactions()
        self.store.set_categories_bulk({int(old.iloc[0].id):'Business (reimbursable)'})
        self.store.set_business_reimbursement(int(old.iloc[1].id),250)
        cfg=self.export([('2026-09-01',' grocery   store ',-100),('2026-09-02','payroll',1000),
                         ('2026-09-03','Rent',-400)])
        self.assertEqual(import_with_config(self.store,cfg),(1,2))
        self.assertEqual(reimport_with_saved_mapping(self.store,self.aid,cfg.path),(0,3))
        tx=self.store.transactions()
        self.assertEqual(len(tx),3)
        self.assertEqual(tx.iloc[0].category,'Business (reimbursable)')
        self.assertEqual(tx.iloc[1].business_reimbursement,250)
        self.assertEqual(list(tx.id[:2]),list(old.id))
        self.assertEqual(float(tx.amount.sum()),500)
        account=self.store.accounts()[0]
        self.assertEqual(account.balance,500)  # importing does not move the balance anchor
        self.assertEqual(account.balance_date,date(2026,9,3))

    def test_identical_purchases_and_reversed_exports_keep_multiplicity(self):
        rows=[('2026-09-01','Coffee',-12.50),('2026-09-01','Coffee',-12.50),
              ('2026-09-01','Refund',12.50)]
        cfg=self.export(rows)
        self.assertEqual(import_with_config(self.store,cfg),(3,0))
        cfg=self.export(list(reversed(rows)))
        self.assertEqual(import_with_config(self.store,cfg),(0,3))
        cfg=self.export(rows+[('2026-09-01','Coffee',-12.50)])
        self.assertEqual(import_with_config(self.store,cfg),(1,3))
        self.assertEqual(len(self.store.transactions()),4)
        # A partially overlapping export must not reduce or duplicate that history.
        cfg=self.export(rows[:1])
        self.assertEqual(import_with_config(self.store,cfg),(0,1))

    def test_meaningful_references_dates_signs_and_accounts_remain_distinct(self):
        rows=[('2026-09-01','Transfer 1234',-100),('2026-09-01','Transfer 4321',-100),
              ('2026-09-02','Transfer 1234',-100),('2026-09-01','Transfer 1234',100)]
        cfg=self.export(rows)
        self.assertEqual(import_with_config(self.store,cfg),(4,0))
        other=self.store.add_account(Account('Savings',AccountType.SAVINGS,0,date(2026,9,3)))
        self.assertEqual(import_with_config(self.store,replace(cfg,account_id=other)),(4,0))

    def test_removed_rows_stay_removed_after_description_format_changes(self):
        cfg=self.export([('2026-09-01','Coffee  Shop',-10),('2026-09-01','Coffee  Shop',-10)])
        self.assertEqual(import_with_config(self.store,cfg),(2,0))
        tx=self.store.transactions()
        self.store.delete_transactions([int(tx.iloc[0].id)])
        cfg=self.export([('2026-09-01','COFFEE SHOP',-10)]*2)
        self.assertEqual(import_with_config(self.store,cfg),(0,2))
        cfg=self.export([('2026-09-01','COFFEE SHOP',-10)]*3)
        self.assertEqual(import_with_config(self.store,cfg),(1,2))
        self.assertEqual(len(self.store.transactions()),2)
        self.store.unsuppress_all()
        self.assertEqual(import_with_config(self.store,cfg),(1,2))

    def test_exact_removed_hash_does_not_consume_another_purchase_slot(self):
        cfg=self.export([('2026-09-01','Coffee',-10)]*3)
        self.assertEqual(import_with_config(self.store,cfg),(3,0))
        tx=self.store.transactions()
        self.store.delete_transactions([int(tx.iloc[2].id)])
        self.assertEqual(import_with_config(self.store,cfg),(0,3))
        cfg=self.export([('2026-09-01','Coffee',-10)]*4)
        self.assertEqual(import_with_config(self.store,cfg),(1,3))

    def test_suppression_migration_retains_old_hashes(self):
        self.store.con.execute('DROP TABLE suppressed')
        self.store.con.execute('CREATE TABLE suppressed (import_hash TEXT PRIMARY KEY,account_id INTEGER,note TEXT NOT NULL,removed_on TEXT NOT NULL)')
        self.store.con.execute("INSERT INTO suppressed VALUES ('old-hash',?, 'removed', '2026-09-01')",(self.aid,))
        self.store.con.commit();self.store.close()
        self.store=Store(self.root/'ledger.db')
        self.assertIn('old-hash',self.store.suppressed_hashes())
        self.assertTrue(self.store.suppressed_transactions().iloc[0][['date','description','amount']].isna().all())

    def test_timestamp_rows_have_distinct_hashes_at_calendar_day_granularity(self):
        frame=pd.DataFrame({'account_id':[self.aid]*2,'date':pd.to_datetime(['2026-09-01 10:00','2026-09-01 11:00']),
                            'description':['Coffee']*2,'amount':[-10.0]*2})
        hashes=make_hashes(frame)
        self.assertEqual(len(set(hashes)),2)

    def test_running_balances_match_independent_end_of_day_anchor(self):
        # September 3 ends at $500, so opening before these rows was $200.
        self.rows([('2026-09-01','Purchase',-250),('2026-09-01','Paycheck',1000),
                   ('2026-09-02','Business flight',-100),('2026-09-03','Transfer',-350),
                   ('2026-09-04','Later purchase',-50)])
        tx=self.store.transactions()
        self.store.set_categories_bulk({int(tx.iloc[2].id):'Business (reimbursable)',int(tx.iloc[3].id):'Transfer'})
        balances=transaction_balances(self.store)
        np.testing.assert_allclose(balances['before'],[200,-50,950,850,500])
        np.testing.assert_allclose(balances['after'],[-50,950,850,500,450])
        account=self.store.accounts()[0]
        index=pd.date_range('2026-09-01','2026-09-04')
        daily=cash_history(self.store.transactions(),account,index,anchor=cash_anchor(self.store,account))
        np.testing.assert_allclose(daily,[950,850,500,450])
        self.store.add_snapshot(self.aid,date(2026,9,4),600)
        moved=transaction_balances(self.store)
        np.testing.assert_allclose(moved['after'],[100,1100,1000,650,600])

    def test_updating_a_newer_export_advances_cash_only_once(self):
        original = [('2026-09-01','Shop',-100),('2026-09-03','Salary',1000)]
        cfg = self.export(original)
        self.assertEqual(import_with_config(self.store,cfg),(2,0))
        cfg = self.export(original + [('2026-09-04','New purchase',-50)])
        self.assertEqual(reimport_with_saved_mapping(self.store,self.aid,cfg.path),(1,2))
        self.assertEqual(networth_history(self.store).iloc[-1]['cash'],450)
        self.assertEqual(reimport_with_saved_mapping(self.store,self.aid,cfg.path),(0,3))
        self.assertEqual(networth_history(self.store).iloc[-1]['cash'],450)

    def test_running_balances_do_not_flag_roundoff_as_negative_cash(self):
        self.rows([('2026-09-01','A',.1),('2026-09-01','B',.2),('2026-09-01','C',-.3)])
        account=self.store.accounts()[0]
        account.balance=0
        self.store.update_account(account)
        balances=transaction_balances(self.store)
        self.assertEqual(list(balances['after']),[.1,.3,0])
        self.assertFalse((balances['after']<0).any())

    def test_separate_account_history_uses_snapshots_and_signed_debt(self):
        first=date.today().replace(day=1)
        invest=self.store.add_account(Account('401k',AccountType.INVESTMENT,12000,date.today()))
        loan=self.store.add_account(Account('Loan',AccountType.LOAN,4000,date.today()))
        self.store.add_snapshot(invest,first,10000)
        self.store.add_snapshot(loan,first,4500)
        frame=account_balance_history(self.store,{self.aid,invest,loan})
        self.assertEqual(set(frame.columns),{self.aid,invest,loan})
        self.assertEqual(frame.loc[pd.Timestamp(first),invest],10000)
        self.assertEqual(frame.iloc[-1][invest],12000)
        self.assertEqual(frame.iloc[-1][loan],-4000)
        chosen=networth_history(self.store,{self.aid,invest})
        self.assertEqual(chosen.iloc[-1].networth,12500)
        empty=account_balance_history(self.store,set())
        self.assertEqual(len(empty.columns),0)


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(AccountDebugTests))


if __name__=='__main__':
    unittest.main()
