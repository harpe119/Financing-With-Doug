"""Posting dates, incomplete exports and conservative migration; fictional data only."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import numpy as np
import pandas as pd
from finfore.history import (account_balance_history, cash_history, current_accounts,
                             networth_history, reconcile, transaction_balances, acknowledge, unacknowledged)
from finfore.import_matching import PostingDateReviewRequired
from finfore.ingest import ImportConfig, guess_mapping, make_hashes, parse_transactions
from finfore.models import Account, AccountType, Goal
from finfore.pipeline import import_csv, import_with_config, reimport_with_saved_mapping
from finfore.store import Store

HEADERS=['Posting Date','Transaction Date','Amount','Credit Debit','type','Type Group',
         'Reference','Instructed Currency','Currency Exchange Rate','Instructed Amount',
         'Description','Category','Check Serial Number','Card Ending','Rewards Total','Rewards Type']


class PostedLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'fictional.db')
        self.aid=self.store.add_account(Account('Checking',AccountType.CHECKING,100,date(2026,9,4)))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def export(self, rows, headers=HEADERS, name='bank.csv'):
        p=self.root/name
        pd.DataFrame(rows,columns=headers).to_csv(p,index=False)
        return str(p)

    def row(self, posted, trans, amount, desc='Shop', ref=''):
        values={'Posting Date':posted,'Transaction Date':trans,'Amount':abs(amount),
                'Credit Debit':'Debit' if amount<0 else 'Credit','Description':desc,'Reference':ref}
        return [values.get(k,'') for k in HEADERS]

    def legacy(self,rows):
        df=pd.DataFrame(rows,columns=['date','description','amount'])
        df['date']=pd.to_datetime(df.date)
        df['account_id']=self.aid
        df['import_hash']=make_hashes(df)
        self.store.add_transactions(df)
        return self.store.transactions()

    def test_supplied_headers_weekend_purchase_moves_cash_on_posting_date(self):
        path=self.export([self.row('2026-09-07','2026-09-05',-1000,'Purchase','p'),
                          self.row('2026-09-07','2026-09-07',1000,'Payroll','i')])
        self.assertEqual(guess_mapping(HEADERS)['date'],'Posting Date')
        self.assertEqual(guess_mapping(HEADERS)['indicator'],'Credit Debit')
        self.assertEqual(import_csv(self.store,path,self.aid),(2,0))
        frame=account_balance_history(self.store)
        np.testing.assert_allclose(frame.loc['2026-09-04':'2026-09-07',self.aid],[100]*4)
        tx=self.store.transactions()
        self.assertEqual(tx.iloc[0].transaction_date,pd.Timestamp('2026-09-05'))
        self.assertEqual(tx.iloc[0].posting_date,pd.Timestamp('2026-09-07'))
        self.assertEqual(tx.iloc[0].source_reference,'p')

    def test_posting_date_is_used_exactly_including_weekends_and_holidays(self):
        p=self.export([self.row('2026-09-06','2026-09-05',-10)])
        import_csv(self.store,p,self.aid)
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-06'))
        self.assertEqual(current_accounts(self.store)[0].balance,90)

    def test_blank_posting_date_is_unposted_and_does_not_change_cash(self):
        p=self.export([self.row('','2026-09-05',-120,ref='auth')])
        self.assertEqual(import_csv(self.store,p,self.aid),(0,0))
        self.assertEqual(current_accounts(self.store)[0].balance,100)
        r=self.store.get_setting(f'import_report:{self.aid}')
        self.assertEqual(r['source_rows'],1)
        self.assertEqual(r['pending'][0]['reference'],'auth')
        self.assertEqual(r['pending'][0]['amount'],-120)

    def test_changed_pending_amount_and_reference_do_not_create_double_debit(self):
        p=self.export([self.row('','2026-09-05',-20,ref='auth')])
        import_csv(self.store,p,self.aid)
        p=self.export([self.row('2026-09-07','2026-09-05',-24,ref='settled')])
        self.assertEqual(reimport_with_saved_mapping(self.store,self.aid,p),(1,0))
        self.assertEqual(current_accounts(self.store)[0].balance,76)
        self.assertEqual(len(self.store.transactions()),1)
        self.assertEqual(self.store.get_setting(f'import_report:{self.aid}')['pending'],[])

    def test_explicit_pending_and_cancelled_rows_stay_outside_ledger(self):
        p=self.export([['2026-09-05','Auth',-20,'Pending'],['2026-09-05','Void',-30,'Cancelled'],
                       ['2026-09-05','Purchase',-10,'Posted']],['Date','Description','Amount','Status'])
        self.assertEqual(import_csv(self.store,p,self.aid),(1,0))
        self.assertEqual(current_accounts(self.store)[0].balance,90)
        report=self.store.get_setting(f'import_report:{self.aid}')
        self.assertEqual((len(report['pending']),report['excluded']),(1,1))

    def test_vanished_pending_row_has_no_lingering_financial_effect(self):
        import_csv(self.store,self.export([self.row('','2026-09-05',-20)]),self.aid)
        import_csv(self.store,self.export([self.row('2026-09-05','2026-09-05',-5)]),self.aid)
        self.assertEqual(current_accounts(self.store)[0].balance,95)

    def test_invalid_date_aborts_whole_import_without_settings_changes(self):
        p=self.export([['bad','Deposit',1000],['2026-09-05','Shop',-500]],['Date','Description','Amount'])
        with self.assertRaisesRegex(ValueError,r'row\(s\) 2'):
            import_csv(self.store,p,self.aid)
        self.assertTrue(self.store.transactions().empty)
        self.assertIsNone(self.store.get_import_mapping(self.aid))

    def test_old_database_upgrade_preserves_financial_rows_until_review(self):
        old=self.root/'old.db'
        with sqlite3.connect(old) as con:
            con.executescript('''CREATE TABLE accounts (id INTEGER PRIMARY KEY,name TEXT,type TEXT,balance REAL,
                balance_date TEXT,apr REAL DEFAULT 0,annual_return REAL DEFAULT .07,volatility REAL DEFAULT .15,
                monthly_contribution REAL DEFAULT 0,monthly_payment REAL DEFAULT 0,payment_day INTEGER DEFAULT 1);
                CREATE TABLE transactions (id INTEGER PRIMARY KEY,account_id INTEGER REFERENCES accounts(id),date TEXT,
                description TEXT,amount REAL,category TEXT,import_hash TEXT UNIQUE);
                INSERT INTO accounts(id,name,type,balance,balance_date) VALUES (1,'Old checking','checking',100,'2026-09-04');
                INSERT INTO transactions VALUES(1,1,'2026-09-05','Shop',-20,'Shopping','old-hash');''')
        con.close()  # sqlite's transaction context does not close the connection
        store=Store(old)
        try:
            self.assertEqual(store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))
            self.assertEqual(store.transactions().iloc[0].date_basis,'legacy')
            self.assertFalse(store.accounts()[0].allow_overdraft)
            p=self.export([self.row('2026-09-07','2026-09-05',-20)])
            with self.assertRaises(PostingDateReviewRequired):
                import_csv(store,p,1)
            self.assertEqual(store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))
        finally:
            store.close()

    def test_invalid_amount_does_not_disappear_in_debit_credit_mode(self):
        p=self.export([['2026-09-05','Shop','oops','']],['Date','Description','Debit','Credit'])
        with self.assertRaisesRegex(ValueError,'Invalid date or amount'):
            import_csv(self.store,p,self.aid)

    def test_repeat_import_categorizes_only_new_rows(self):
        from finfore.learning import apply_chain
        rows=[self.row('2026-09-07','2026-09-05',-10,'Shop1'),self.row('2026-09-07','2026-09-05',-20,'Shop2')]
        import_csv(self.store,self.export(rows),self.aid)
        with patch('finfore.learning.apply_chain',wraps=apply_chain) as classify:
            self.assertEqual(import_csv(self.store,self.export(rows),self.aid),(0,2))
            classify.assert_not_called()
            rows.append(self.row('2026-09-08','2026-09-08',-30,'Shop3'))
            self.assertEqual(import_csv(self.store,self.export(rows),self.aid),(1,2))
            self.assertEqual(len(classify.call_args.args[0]),1)

    def test_failed_categorization_rolls_back_approved_date_repair(self):
        self.legacy([['2026-09-05','Shop',-20]])
        p=self.export([self.row('2026-09-07','2026-09-05',-20),self.row('2026-09-08','2026-09-08',-30,'New shop')])
        cfg=ImportConfig(p,self.aid,col_date='Posting Date',col_desc='Description',col_amount='Amount',
                         col_indicator='Credit Debit',sign_mode='indicator')
        with patch('finfore.learning.apply_chain',side_effect=ValueError('forced categorization failure')):
            with self.assertRaisesRegex(ValueError,'forced categorization failure'):
                import_with_config(self.store,cfg,approve_date_updates=True)
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))
        self.assertEqual(len(self.store.transactions()),1)

    def test_malformed_csv_row_cannot_be_skipped(self):
        p=self.root/'bad.csv'
        p.write_text('Date,Description,Amount\n2026-09-05,Good,-10\n2026-09-05,Missing,1000,extra\n')
        with self.assertRaises(ValueError):
            import_csv(self.store,str(p),self.aid)
        self.assertTrue(self.store.transactions().empty)

    def test_overlong_first_record_cannot_be_mistaken_for_an_index(self):
        p=self.root/'bad-first.csv'
        p.write_text('Date,Description,Amount\n2026-09-05,Shop,-10,extra\n')
        with self.assertRaises(ValueError):
            import_csv(self.store,str(p),self.aid)
        self.assertTrue(self.store.transactions().empty)

    def test_invalid_row_after_preview_is_still_atomic(self):
        rows=[['2026-09-05',f'Purchase{i}',-1] for i in range(60)]
        rows.append(['bad','Payroll',1000])
        with self.assertRaises(ValueError):
            import_csv(self.store,self.export(rows,['Date','Description','Amount']),self.aid)
        self.assertTrue(self.store.transactions().empty)

    def test_pending_rows_do_not_change_forecast_start_and_remain_visible_as_a_concern(self):
        from finfore.forecast import run_forecast
        from finfore.data_quality import inspect_data_quality
        import_csv(self.store,self.export([self.row('','2026-09-05',-20)]),self.aid)
        self.assertEqual(run_forecast(self.store,14,20,seed=9).cash_det[0],100)
        check=next(c for c in inspect_data_quality(self.store).checks if c.key=='unposted_imports')
        self.assertTrue(check.needs_review)

    def test_wider_duplicate_review_lists_date_shift_without_selecting_removal(self):
        from finfore.dedup import suggest
        self.legacy([['2026-09-05','Shop',20],['2026-09-08','Shop',20]])
        self.assertEqual(suggest(self.store,self.aid),[])
        reports=suggest(self.store,self.aid,window_days=7)
        self.assertEqual(len(reports[0].pairs),1)
        self.assertEqual(reports[0].suggested,[])

    def test_legacy_repair_requires_review_and_keeps_ids_and_human_decisions(self):
        tx=self.legacy([['2026-09-05','Shop',-20]])
        tid=int(tx.iloc[0].id)
        original_hash=self.store.con.execute('SELECT import_hash FROM transactions').fetchone()[0]
        self.store.set_categories_bulk({tid:'Business (reimbursable)'})
        gid=self.store.add_goal(Goal('Vacation',100,'savings'))
        self.store.con.execute("INSERT INTO goal_activity(goal_id,when_date,action,amount,account_id,transaction_id,transaction_hash) VALUES (?,?,?,?,?,?,?)",
                               (gid,'2026-09-05','purchase',20,self.aid,tid,original_hash))
        self.store.con.commit()
        self.store.save_import_mapping(self.aid,dict(delimiter=',',col_date='Transaction Date',col_desc='Description',
                                                   col_amount='Amount',col_indicator='Credit Debit',sign_mode='indicator'))
        p=self.export([self.row('2026-09-07','2026-09-05',-20,ref='bank-ref')])
        with self.assertRaises(PostingDateReviewRequired) as ctx:
            reimport_with_saved_mapping(self.store,self.aid,p)
        self.assertEqual(ctx.exception.changes[0]['old_date'],'2026-09-05')
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))
        self.store.enable_undo()
        self.assertEqual(reimport_with_saved_mapping(self.store,self.aid,p,approve_date_updates=True),(0,1))
        row=self.store.transactions().iloc[0]
        self.assertEqual((int(row.id),row.category,int(row.category_reviewed)),(tid,'Business (reimbursable)',1))
        self.assertEqual(row.date,pd.Timestamp('2026-09-07'))
        self.assertEqual(row.transaction_date,pd.Timestamp('2026-09-05'))
        self.assertEqual(row.source_reference,'bank-ref')
        activity=self.store.goal_activity(gid)[0]
        self.assertEqual(activity['transaction_id'],tid)
        self.assertEqual(activity['transaction_hash'],original_hash)
        self.assertEqual(self.store.get_import_mapping(self.aid)['col_date'],'Posting Date')
        self.assertEqual(reimport_with_saved_mapping(self.store,self.aid,p),(0,1))
        self.store.undo.undo()  # repeated import is a no-op, including report
        # A repeated import updates corrected count, so undo may first restore that report.
        if self.store.transactions().iloc[0].date == pd.Timestamp('2026-09-07'):
            self.store.undo.undo()
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))

    def test_identical_purchases_migrate_together_without_losing_multiplicity(self):
        self.legacy([['2026-09-05','Shop',-20]]*2)
        p=self.export([self.row('2026-09-07','2026-09-05',-20)]*2)
        cfg=ImportConfig(p,self.aid,col_date='Posting Date',col_desc='Description',col_amount='Amount',
                         col_indicator='Credit Debit',sign_mode='indicator')
        self.assertEqual(import_with_config(self.store,cfg,approve_date_updates=True),(0,2))
        self.assertEqual(len(self.store.transactions()),2)
        self.assertTrue(self.store.transactions().date.eq(pd.Timestamp('2026-09-07')).all())

    def test_ambiguous_legacy_purchase_dates_block_import(self):
        self.legacy([['2026-09-05','Shop',-20]]*2)
        p=self.export([self.row('2026-09-07','2026-09-05',-20),self.row('2026-09-08','2026-09-05',-20)])
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            import_csv(self.store,p,self.aid)
        self.assertEqual(len(self.store.transactions()),2)

    def test_preexisting_old_and_posted_copies_are_not_silently_merged(self):
        self.legacy([['2026-09-05','Shop',-20],['2026-09-07','Shop',-20]])
        with self.assertRaisesRegex(ValueError,'both purchase-date and posting-date'):
            import_csv(self.store,self.export([self.row('2026-09-07','2026-09-05',-20)]),self.aid)

    def test_removed_legacy_rows_stay_suppressed_after_date_mapping_changes(self):
        tx=self.legacy([['2026-09-05','Shop',-20]])
        self.store.delete_transactions([int(tx.iloc[0].id)])
        self.assertEqual(import_csv(self.store,self.export([self.row('2026-09-07','2026-09-05',-20)]),self.aid),(0,1))

    def test_failed_sql_write_rolls_back_rows_repairs_mapping_and_report(self):
        self.legacy([['2026-09-05','Shop',-20]])
        self.store.con.execute("CREATE TRIGGER refuse_import BEFORE INSERT ON settings BEGIN SELECT RAISE(ABORT,'forced failure'); END")
        p=self.export([self.row('2026-09-07','2026-09-05',-20),self.row('2026-09-08','2026-09-08',100,'Deposit')])
        cfg=ImportConfig(p,self.aid,col_date='Posting Date',col_desc='Description',col_amount='Amount',
                         col_indicator='Credit Debit',sign_mode='indicator')
        with self.assertRaises(sqlite3.IntegrityError):
            import_with_config(self.store,cfg,approve_date_updates=True)
        tx=self.store.transactions()
        self.assertEqual(len(tx),1)
        self.assertEqual(tx.iloc[0].date,pd.Timestamp('2026-09-05'))
        self.assertIsNone(self.store.get_import_mapping(self.aid))

    def test_middle_history_negative_close_is_flagged_even_with_positive_opening(self):
        self.legacy([['2026-09-05','Purchase',-200],['2026-09-07','Deposit',200]])
        issues=reconcile(self.store)
        self.assertEqual(len(issues),1)
        self.assertEqual((issues[0].opening,issues[0].minimum),(100,-100))
        self.assertIn('unable to overdraw',issues[0].message)
        self.assertEqual(float(account_balance_history(self.store)[self.aid].min()),-100)
        account=self.store.account(self.aid)
        account.allow_overdraft=True
        self.store.update_account(account)
        self.assertEqual(reconcile(self.store),[])

    def test_negative_credit_card_opening_is_valid_debt(self):
        account=self.store.account(self.aid)
        account.type=AccountType.CREDIT
        account.balance=-500
        self.store.update_account(account)
        self.legacy([['2026-09-05','Purchase',-100],['2026-09-06','Payment',100]])
        self.assertEqual(reconcile(self.store),[])
        self.assertEqual(networth_history(self.store).debt.iloc[-1],500)

    def test_observation_gap_is_detected_without_fabricating_an_adjustment(self):
        self.legacy([['2026-09-01','Purchase',-1000],['2026-09-06','Deposit',500]])
        self.store.add_snapshot(self.aid,date(2026,9,5),100)
        self.store.add_snapshot(self.aid,date(2026,9,7),100)
        issues=reconcile(self.store)
        self.assertTrue(issues[0].snapshot_gaps)
        self.assertEqual(issues[0].snapshot_gaps[-1]['difference'],-500)
        self.assertIn('observed balances',issues[0].message)
        self.assertEqual(len(self.store.transactions()),2)

    def test_changed_middle_dip_reopens_acknowledged_warning(self):
        self.legacy([['2026-09-05','Purchase',-200],['2026-09-07','Deposit',200]])
        acknowledge(self.store,reconcile(self.store))
        self.assertFalse(unacknowledged(self.store))
        self.legacy([['2026-09-06','Purchase2',-20],['2026-09-07','Deposit2',20]])
        self.assertTrue(unacknowledged(self.store))

    def test_unknown_intraday_order_has_positive_daily_close(self):
        self.legacy([['2026-09-05','Purchase',-1000],['2026-09-05','Paycheck',1000]])
        balances=transaction_balances(self.store)
        self.assertEqual(balances.after.tolist(),[-900,100])
        self.assertEqual(balances.day_close.tolist(),[100,100])
        self.assertTrue(balances.unordered.all())
        self.assertEqual(reconcile(self.store),[])

    def test_independent_randomized_daily_ledger_arithmetic(self):
        rng=np.random.default_rng(42)
        days=pd.date_range('2026-01-01','2026-03-31')
        for _ in range(50):
            flows=rng.integers(-5000,5000,len(days))/100
            expected=1000+np.cumsum(flows)
            k=int(rng.integers(len(days)))
            account=Account('Test',AccountType.SAVINGS,float(expected[k]),days[k].date(),id=1)
            tx=pd.DataFrame(dict(account_id=1,date=days,amount=flows))
            np.testing.assert_allclose(cash_history(tx,account,days),expected,atol=1e-9)


class PostedLedgerUITests(PostedLedgerTests):
    # Only UI-specific methods belong here; inherited accounting tests remain
    # in the main class, not duplicated by this suite loader.
    @classmethod
    def setUpClass(cls):
        from test_usability import desktop_test_app
        cls.app=desktop_test_app()

    def test_actual_dashboard_curves_use_posting_dates(self):
        from PyQt6.QtCore import QSettings
        from finfore.ui import prefs
        from finfore.ui.dashboard import DashboardPage
        p=self.export([self.row('2026-09-07','2026-09-05',-1000,'Purchase'),
                       self.row('2026-09-07','2026-09-07',1000,'Payroll')])
        import_csv(self.store,p,self.aid)
        class Main:
            def __getattr__(self,key): return lambda *args,**kwargs:None
        settings=QSettings(str(self.root/'ui.ini'),QSettings.Format.IniFormat)
        with patch.object(prefs,'app_settings',lambda:settings):
            page=DashboardPage(self.store,Main())
            page.refresh()
            self.assertEqual(min(page.hist_series._groups['cash'][0].getData()[1]),100)
            page._history_mode='accounts'
            page._render_history(self.store.transactions(),True)
            self.assertEqual(min(page.hist_series._groups[f'account:{self.aid}'][0].getData()[1]),100)
            page.close()

    def test_import_mapping_and_balance_basis_are_visible(self):
        from finfore.ui.import_dialog import ImportDialog,ImportDetailsDialog
        from finfore.ui.account_dialog import AccountDialog
        from finfore.ui.accounts import UpdateBalanceDialog
        p=self.export([self.row('2026-09-07','2026-09-05',-10)])
        dialog=ImportDialog(self.store)
        dialog.path.setText(p)
        dialog._reload()
        self.assertEqual(dialog.c_date.currentData(),'Posting Date')
        self.assertEqual(dialog.c_transaction_date.currentData(),'Transaction Date')
        self.assertEqual(dialog.c_reference.currentData(),'Reference')
        account=AccountDialog(account=self.store.account(self.aid))
        self.assertEqual(account.balance_label.text(),'Balance at end of day')
        account.overdraft.setChecked(True)
        self.assertTrue(account.get_account().allow_overdraft)
        balance=UpdateBalanceDialog(self.store.account(self.aid))
        from PyQt6.QtWidgets import QLabel
        self.assertTrue(any('Available balance' in w.text() for w in balance.findChildren(QLabel)))
        import_csv(self.store,self.export([self.row('','2026-09-05',-10)]),self.aid)
        details=ImportDetailsDialog(self.store)
        from PyQt6.QtWidgets import QTableWidget
        self.assertEqual(details.findChild(QTableWidget).rowCount(),1)
        for widget in (dialog,account,balance,details): widget.close()

    def test_cancel_date_review_changes_nothing(self):
        from finfore.ui.import_dialog import ImportDialog
        self.legacy([['2026-09-05','Shop',-20]])
        dialog=ImportDialog(self.store)
        dialog.path.setText(self.export([self.row('2026-09-07','2026-09-05',-20)]))
        dialog._reload()
        with patch('finfore.ui.import_dialog.confirm_posting_dates',return_value=False):
            dialog._import()
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))
        self.assertIsNone(self.store.get_import_mapping(self.aid))
        dialog.close()

    def test_approved_gui_date_review_is_one_complete_undo_step(self):
        from finfore.ui.import_dialog import ImportDialog
        self.legacy([['2026-09-05','Shop',-20]])
        self.store.enable_undo()
        dialog=ImportDialog(self.store)
        dialog.path.setText(self.export([self.row('2026-09-07','2026-09-05',-20)]))
        dialog._reload()
        with patch('finfore.ui.import_dialog.confirm_posting_dates',return_value=True), \
             patch('finfore.ui.import_dialog.QMessageBox.information'):
            dialog._import()
        self.assertEqual(dialog.result_counts,(0,1))
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-07'))
        self.store.undo.undo()
        self.assertEqual(self.store.transactions().iloc[0].date,pd.Timestamp('2026-09-05'))
        self.assertIsNone(self.store.get_import_mapping(self.aid))
        self.assertFalse(self.store.undo.can_undo)
        dialog.close()


def run_tests():
    loader=unittest.defaultTestLoader
    suite=loader.loadTestsFromTestCase(PostedLedgerTests)
    for name in ('test_actual_dashboard_curves_use_posting_dates','test_import_mapping_and_balance_basis_are_visible',
                 'test_cancel_date_review_changes_nothing','test_approved_gui_date_review_is_one_complete_undo_step'):
        suite.addTest(PostedLedgerUITests(name))
    return unittest.TextTestRunner(verbosity=1).run(suite)


if __name__=='__main__':
    raise SystemExit(not run_tests().wasSuccessful())
