"""Linked-page regressions using only temporary fictional ledgers."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pandas as pd
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import QDialog

from finfore.categorize import BUSINESS_CATEGORY
from finfore.history import current_accounts, headline_stats
from finfore.models import Account, AccountType, Goal, RecurringItem
from finfore.progress import capture
from finfore.spending_plan import calculate_plan
from finfore.ui import prefs
from finfore.ui.main_window import MainWindow
from finfore.util import add_months, fmt_money
from test_usability import desktop_test_app


class LinkedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        from finfore.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'fictional.db')
        self.settings = QSettings(str(Path(self.tmp.name) / 'prefs.ini'), QSettings.Format.IniFormat)
        self.prefs = patch.object(prefs, 'app_settings', lambda: self.settings)
        self.prefs.start()
        self.today = date.today()
        old = self.today - timedelta(days=2)
        self.aid = self.store.add_account(Account('Checking', AccountType.CHECKING, 5000, old))
        self.inv = self.store.add_account(Account('Investment', AccountType.INVESTMENT, 10000, old,
                                                annual_return=0, volatility=0))
        self.loan = self.store.add_account(Account('Loan', AccountType.LOAN, 1000, old,
                                                 monthly_payment=50))
        self.cash_goal = self.store.add_goal(Goal('Cash milestone', 20000, 'cash'))
        self.purchase = self.store.add_goal(Goal('Vacation', 2000, 'savings'))
        self.prior = add_months(self.today.replace(day=1), -1)
        for i, (amount, category) in enumerate([(4000,'Income'), (-1000,'Housing'), (-200,'Dining')]):
            self.tx(amount, category, self.prior, f'history{i}')
        self.win = MainWindow(self.store)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()
        self.store.close()
        self.prefs.stop()
        self.tmp.cleanup()

    def tx(self, amount, category, when, key):
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.aid, date=when,
            description=key, amount=amount, category=category, import_hash=key)]))
        return int(self.store.transactions().loc[lambda f: f.description.eq(key), 'id'].iloc[0])

    def visit(self, name):
        # Switch away so this exercises the normal navigation refresh.
        if self.win.current_page() is self.win.pages[1]:
            self.win.show_page('Transactions')
        else:
            self.win.show_page('Accounts')
        self.win.show_page(name)
        self.app.processEvents()
        return self.win.current_page()

    def assert_balances_agree(self, cash, investment, loan):
        expected = cash + investment - loan
        accounts = self.visit('Accounts')
        row = next(r for r in range(accounts.table.rowCount())
                   if accounts.table.item(r,0) and accounts.table.item(r,0).text() == 'Checking')
        self.assertEqual(accounts.table.item(row,2).text(), f'${cash:,.2f}')
        dash = self.visit('Dashboard')
        self.win.dashboard_tabs.setCurrentIndex(0)
        dash = self.win.pages[0]
        self.assertEqual(dash.card_cash.value.text(), fmt_money(cash))
        self.assertEqual(dash.card_inv.value.text(), fmt_money(investment))
        self.assertEqual(dash.card_debt.value.text(), fmt_money(loan))
        self.assertEqual(self.win.header_nw.text(), f'net worth  {fmt_money(expected)}')
        np.testing.assert_allclose(dash._h.iloc[-1][['cash','investments','debt','networth']],
                                   [cash, investment, loan, expected])
        goals = self.visit('Goals')
        self.assertEqual(goals._plan.by_key(f'goal:{self.cash_goal}').target.progress, min(20000,cash))
        self.assertEqual(goals._plan.by_key(f'loan:{self.loan}').target.amount, loan)
        forecast = self.visit('Projection')
        self.assertIsNotNone(forecast._res)
        self.assertEqual(forecast._res.cash_det[0], cash)
        self.assertEqual(forecast._res.networth_det[0], expected)
        cp = capture(self.store, with_forecast=False)
        self.assertEqual((cp.cash,cp.investments,cp.debt,cp.networth),
                         (cash,investment,loan,expected))

    def test_later_transactions_and_snapshots_reach_all_balance_views(self):
        self.assert_balances_agree(5000,10000,1000)
        self.tx(200,'Income',self.today-timedelta(days=1),'new deposit')
        self.assert_balances_agree(5200,10000,1000)
        self.store.add_snapshot(self.aid,self.today,6000)
        self.store.add_snapshot(self.inv,self.today,12000)
        self.store.add_snapshot(self.loan,self.today,900)
        self.assert_balances_agree(6000,12000,900)
        # Derived values must not be written back as bank observations.
        self.assertEqual(self.store.account(self.aid).balance,5000)

    def test_update_balance_overrides_same_day_snapshot_and_undo_restores_both(self):
        self.store.add_snapshot(self.aid,self.today,6000)
        self.visit('Accounts')._select_id(self.aid)
        self.store.undo.clear()
        with patch('finfore.ui.accounts.UpdateBalanceDialog.exec', return_value=QDialog.DialogCode.Accepted), \
             patch('finfore.ui.accounts.UpdateBalanceDialog.get_values', return_value=(7000,self.today)):
            self.win.pages[1]._update_balance()
        self.assertEqual(self.store.snapshots(self.aid).balance.iloc[-1],7000)
        self.assert_balances_agree(7000,10000,1000)
        # Checkpoints added by assertions are independent undo steps.
        while self.store.undo.can_undo and self.store.account(self.aid).balance == 7000:
            self.win.undo()
        self.assert_balances_agree(6000,10000,1000)

    def test_terms_only_edit_does_not_replace_a_newer_snapshot(self):
        self.store.add_snapshot(self.inv,self.today,12000)
        account = self.store.account(self.inv)
        account.annual_return = .08
        self.store.update_account(account)
        self.assertEqual(next(a for a in current_accounts(self.store) if a.id == self.inv).balance,12000)

    def test_category_correction_updates_spending_plan_goals_and_forecast_sample(self):
        for week in range(1,13):
            self.tx(-25,'Shopping',self.today-timedelta(weeks=week),f'variable week {week}')
        before = calculate_plan(self.store)
        self.assertGreater(before.amounts['essential'],0)
        spending = self.visit('Spending')
        self.assertAlmostEqual(spending._agg.essential,before.amounts['essential'],places=2)
        old_surplus = self.visit('Goals').surplus.value()
        original = self.visit('Projection')._res
        self.assertGreater(original.monthly_spending_reference.get('Housing',0),0)
        txpage = self.visit('Transactions')
        tid = int(self.store.transactions().loc[lambda f: f.description.eq('history1'),'id'].iloc[0])
        txpage._recategorize([tid],BUSINESS_CATEGORY)
        self.assertEqual(self.visit('Spending')._agg.essential,0)
        plan = self.visit('Plan')
        self.assertEqual(plan.boxes['essential'].value(),0)
        self.assertAlmostEqual(self.visit('Goals').surplus.value(),
                               old_surplus + before.amounts['essential'],places=1)
        forecast = self.visit('Projection')
        self.assertEqual(forecast._res.monthly_spending_reference.get('Housing',0),0)
        self.assertGreater(forecast._res.cash_det[-1],original.cash_det[-1])
        self.assertEqual(headline_stats(self.store)['cash'],5000)

    def test_goal_funding_and_shared_allowance_update_in_both_directions(self):
        goals = self.visit('Goals')
        goals.surplus.setValue(250)
        plan = self.visit('Plan')
        self.assertEqual(plan._allowance(), 250)
        plan._save()
        goals = self.visit('Goals')
        self.assertEqual(goals.surplus.value(),250)
        goals.surplus.setValue(400)
        plan = self.visit('Plan')
        self.assertEqual(plan._allowance(),400)
        self.assertIn('$400', plan.allowance_line.text())
        self.store.record_goal_activity(self.purchase,'contribute',300,self.today,self.aid)
        goals = self.visit('Goals')
        self.assertEqual(goals._plan.by_key(f'goal:{self.purchase}').target.progress,300)
        plan = self.visit('Plan')
        row = plan._goal_ids.index(self.purchase)
        self.assertEqual(plan.goal_table.item(row,3).text(),'$300.00')
        self.assertEqual(self.store.account(self.aid).balance,5000)

    def test_plan_category_budget_reaches_goals_and_forecast_only_after_save(self):
        # The allowance saves as it is typed, so set it first; this test is about
        # the budget rows, which still take effect only when the plan is saved.
        self.store.set_setting('plan_surplus', 400)
        original = self.visit('Projection')._res
        before = self.store.transactions()
        plan = self.visit('Plan')
        plan.checks['category:Dining'].setChecked(True)
        plan.boxes['category:Dining'].setValue(50)
        plan.checks['income'].setChecked(True)
        plan.boxes['income'].setValue(4500)
        plan.checks['giving'].setChecked(True)
        plan.boxes['giving'].setValue(100)
        self.assertEqual(plan.boxes['frivolous'].value(), 50)
        unsaved = self.visit('Projection')._res
        self.assertFalse(unsaved.spending_plan_applied)
        np.testing.assert_array_equal(unsaved.cash_det, original.cash_det)
        plan = self.visit('Plan')
        plan.save_btn.click()
        goals = self.visit('Goals')
        self.assertEqual(goals.surplus.value(), 400)
        self.assertIn('Monthly Plan', goals.surplus_note.text())
        forecast = self.visit('Projection')
        self.assertTrue(forecast.follow_goals.isChecked())
        self.assertTrue(forecast._res.spending_plan_applied)
        self.assertEqual(forecast._res.monthly_income_reference, 4500)
        self.assertEqual(forecast._res.monthly_spending_reference['Dining'], 50)
        self.assertEqual(forecast._res.monthly_spending_reference['Charitable donation'], 100)
        self.assertIn('without the plan', forecast.cash_compare.text())
        self.assertIn('baseline', forecast.cash_series._groups)
        forecast.follow_goals.setChecked(False)
        forecast.run()
        self.assertFalse(forecast._res.spending_plan_applied)
        pd.testing.assert_frame_equal(before, self.store.transactions())
        self.assertEqual(self.store.goals()[1].saved_amount, 0)

    def test_category_plan_undo_and_shared_allowance_merge_preserve_drafts(self):
        self.visit('Goals').surplus.setValue(250)
        plan = self.visit('Plan')
        plan.save_btn.click()
        plan.checks['category:Dining'].setChecked(True)
        plan.boxes['category:Dining'].setValue(50)
        goals = self.visit('Goals')
        goals.surplus.setValue(400)
        plan = self.visit('Plan')
        self.assertTrue(plan.checks['category:Dining'].isChecked())
        self.assertEqual(plan.boxes['category:Dining'].value(), 50)
        self.assertEqual(plan._allowance(), 400)
        plan.save_btn.click()
        self.win.undo()
        self.assertNotIn('category:Dining', self.store.get_setting('spending_plan'))
        self.win.redo()
        self.assertEqual(self.store.get_setting('spending_plan')['category:Dining'], 50)

    def test_recurring_toggle_updates_calendar_forecast_and_header_undo(self):
        item = RecurringItem('FICTIONAL BILL|-','Fictional bill','weekly',-50,
                             self.today-timedelta(days=6),kind='bill')
        self.store.merge_recurring([item])
        forecast = self.visit('Projection')
        before = forecast._res.monthly_recurring_net
        recurring = self.visit('Recurring')
        self.store.undo.clear()
        self.win.refresh_header()
        self.assertFalse(self.win.a_undo.isEnabled())
        recurring.table.item(0,0).setCheckState(Qt.CheckState.Unchecked)
        self.assertTrue(self.win.a_undo.isEnabled())
        self.assertGreater(self.visit('Projection')._res.monthly_recurring_net,before)
        calendar = self.visit('Calendar')
        calendar.calendar.showNextMonth()
        self.assertFalse(any(entry.label == item.name for entry in calendar._schedule.entries))

    def test_shared_allowance_change_preserves_unrelated_plan_draft(self):
        self.visit('Goals').surplus.setValue(250)
        plan = self.visit('Plan')
        plan._save()
        plan.checks['reserve'].setChecked(True)
        plan.boxes['reserve'].setValue(500)
        self.assertTrue(plan._dirty)
        goals = self.visit('Goals')
        goals.surplus.setValue(400)
        self.assertTrue(self.win.a_undo.isEnabled())
        plan = self.visit('Plan')
        self.assertTrue(plan.checks['reserve'].isChecked())
        self.assertEqual(plan.boxes['reserve'].value(),500)
        self.assertEqual(plan._allowance(),400)
        self.assertTrue(plan._dirty)
        self.assertNotIn('reserve',self.store.get_setting('spending_plan'))
        plan._reload()
        self.assertFalse(plan.checks['reserve'].isChecked())
        self.assertEqual(plan._allowance(),400)

    def test_the_budget_tab_has_no_allowance_of_its_own_and_can_adopt_what_the_budget_leaves(self):
        plan = self.visit('Plan')
        self.assertFalse(hasattr(plan, 'goal_budget'))
        available = max(0.0, calculate_plan(self.store).available)
        self.assertIsNone(self.store.get_setting('plan_surplus'))
        self.assertIn('No allowance is chosen yet', plan.allowance_line.text())
        self.assertIn(fmt_money(available).replace('.00', ''), plan.allowance_line.text().replace('.00', ''))
        goals = self.visit('Goals')
        goals.surplus.setValue(available + 100)
        plan = self.visit('Plan')
        self.assertIn('Your allowance is', plan.allowance_line.text())
        self.assertFalse(plan.use_btn.isHidden())
        self.assertIn('as my allowance', plan.use_btn.text())
        self.store.undo.clear()
        self.win.refresh_header()
        plan.use_btn.click()
        self.assertEqual(self.store.get_setting('plan_surplus'), round(available, 2))
        self.assertTrue(plan.use_btn.isHidden())
        self.assertTrue(self.win.a_undo.isEnabled())
        self.assertEqual(self.visit('Goals').surplus.value(), round(available, 2))
        self.win.undo()
        self.assertEqual(self.store.get_setting('plan_surplus'), available + 100)

    def test_saving_the_plan_keeps_a_chosen_allowance_and_otherwise_adopts_the_budget(self):
        self.visit('Goals').surplus.setValue(333)
        plan = self.visit('Plan')
        plan.save_btn.click()
        self.assertEqual(self.store.get_setting('plan_surplus'), 333)
        self.store.set_setting('plan_surplus', None)
        plan = self.visit('Plan')
        plan.checks['reserve'].setChecked(True)
        plan.boxes['reserve'].setValue(10)
        plan.save_btn.click()
        saved = self.store.get_setting('plan_surplus')
        self.assertAlmostEqual(saved, max(0.0, calculate_plan(self.store).available), places=2)

    def test_account_commitments_update_plan_goals_and_projection(self):
        accounts = self.visit('Accounts')
        original = self.visit('Projection')._res
        accounts = self.visit('Accounts')
        accounts._select_id(self.inv)
        account = self.store.account(self.inv)
        account.monthly_contribution = 200
        with patch('finfore.ui.accounts.AccountDialog.exec',return_value=QDialog.DialogCode.Accepted), \
             patch('finfore.ui.accounts.AccountDialog.get_account',return_value=account):
            accounts._edit()
        self.assertEqual(self.visit('Plan').boxes['investing'].value(),200)
        changed = self.visit('Projection')._res
        self.assertLess(changed.cash_det[-1],original.cash_det[-1])
        accounts = self.visit('Accounts')
        accounts._select_id(self.loan)
        account = self.store.account(self.loan)
        account.monthly_payment = 250
        with patch('finfore.ui.accounts.AccountDialog.exec',return_value=QDialog.DialogCode.Accepted), \
             patch('finfore.ui.accounts.AccountDialog.get_account',return_value=account):
            accounts._edit()
        self.assertEqual(self.visit('Plan').boxes['debt'].value(),250)
        self.assertEqual(self.visit('Goals')._plan.by_key(f'loan:{self.loan}').target.required,250)
        self.assertEqual(self.visit('Projection')._res.loans[0].payment,250)

    def test_linking_a_recurring_payment_updates_goals_forecast_and_calendar(self):
        self.store.merge_recurring([RecurringItem('CAR PAYMENT|-','Car payment','biweekly',-125,
            self.today-timedelta(days=10),kind='loan_payment')])
        item = self.store.recurring()[0]
        before = self.visit('Projection')._res
        self.assertTrue(any("isn't linked" in warning for warning in before.warnings))
        recurring = self.visit('Recurring')
        recurring._set_kind(item.id,'loan_payment',self.loan)
        payment = 125 * 30.44 / 14
        self.assertAlmostEqual(self.visit('Goals')._plan.by_key(f'loan:{self.loan}').target.required,payment)
        forecast = self.visit('Projection')
        self.assertAlmostEqual(forecast._res.loans[0].payment,payment)
        self.assertFalse(any("isn't linked" in warning for warning in forecast._res.warnings))
        self.assertIn('before loan payments and investing',forecast.summary.text())
        calendar = self.visit('Calendar')
        calendar.calendar.showNextMonth()
        self.assertTrue(any(entry.label == item.name for entry in calendar._schedule.entries))

    def test_active_payment_without_a_loan_account_is_not_lost_from_calendar(self):
        self.store.delete_account(self.loan)
        self.store.merge_recurring([RecurringItem('CAR PAYMENT|-','Car payment','weekly',-125,
            self.today-timedelta(days=6),kind='loan_payment')])
        forecast = self.visit('Projection')
        self.assertLess(forecast._res.monthly_recurring_net,0)
        calendar = self.visit('Calendar')
        calendar.calendar.showNextMonth()
        self.assertTrue(any(entry.label == 'Car payment' for entry in calendar._schedule.entries))

    def test_goal_target_and_recorded_funding_reach_forecast_goal_details(self):
        from PyQt6.QtWidgets import QLabel
        self.visit('Projection')
        self.store.update_goal(self.cash_goal,name='Updated cash goal',amount=25000)
        self.store.record_goal_activity(self.purchase,'contribute',300,self.today,self.aid)
        forecast = self.visit('Projection')
        text = ' '.join(label.text() for label in forecast.findChildren(QLabel))
        self.assertIn('Updated cash goal',text)
        self.assertIn('$25,000',text)
        self.assertIn('$300 set aside',text)
        self.assertEqual(forecast._res.goal_outcomes[0].goal.amount,25000)

    def test_goals_allowance_and_priority_change_the_actual_forecast_curve(self):
        account = self.store.account(self.loan)
        account.apr = .12
        self.store.update_account(account)
        self.store.set_plan_order([f'loan:{self.loan}',f'goal:{self.purchase}',f'goal:{self.cash_goal}'])
        goals = self.visit('Goals')
        goals.surplus.setValue(100)
        forecast = self.visit('Projection')
        self.assertTrue(forecast.follow_goals.isChecked())
        self.assertFalse(forecast.extra_loan.isEnabled())
        first = forecast._res
        curve = forecast.cash_series._groups['median'][0].getData()[1].copy()
        self.assertEqual(first.goal_plan_budget,100)
        goals = self.visit('Goals')
        goals.surplus.setValue(400)
        forecast = self.visit('Projection')
        second = forecast._res
        updated_curve = forecast.cash_series._groups['median'][0].getData()[1]
        self.assertFalse(np.array_equal(curve,updated_curve))
        self.assertLess(second.loans[0].payoff,first.loans[0].payoff)
        self.assertLess(second.loans[0].interest_paid,first.loans[0].interest_paid)
        self.assertIn('Goals funding plan',forecast.summary.text())
        self.assertEqual(second.goal_plan_budget,400)
        goals = self.visit('Goals')
        goals.table.selectRow(goals._keys.index(f'goal:{self.purchase}'))
        goals._move(-1)
        forecast = self.visit('Projection')
        self.assertFalse(np.array_equal(updated_curve,forecast.cash_series._groups['median'][0].getData()[1]))
        self.assertGreater(forecast._res.loans[0].payoff,second.loans[0].payoff)

    def test_goals_mode_can_be_disabled_and_undo_restores_the_mode(self):
        goals = self.visit('Goals')
        goals.surplus.setValue(250)
        forecast = self.visit('Projection')
        self.assertIsNotNone(forecast._res.goal_plan_budget)
        self.store.undo.clear()
        forecast.follow_goals.setChecked(False)
        forecast.run()
        self.assertIsNone(forecast._res.goal_plan_budget)
        self.assertTrue(forecast.extra_loan.isEnabled())
        self.win.undo()
        self.assertTrue(forecast.follow_goals.isChecked())
        self.assertEqual(forecast._res.goal_plan_budget,250)

    def test_savings_earmarks_do_not_become_household_spending(self):
        from finfore.forecast import run_forecast
        from finfore.models import Scenario
        self.store.delete_account(self.loan)
        first = run_forecast(self.store,365,20,Scenario(follow_goal_plan=True))
        self.store.set_setting('plan_surplus',1000)
        self.store.set_plan_order([f'goal:{self.purchase}',f'goal:{self.cash_goal}'])
        second = run_forecast(self.store,365,20,Scenario(follow_goal_plan=True))
        np.testing.assert_array_equal(first.cash_det,second.cash_det)
        np.testing.assert_array_equal(first.networth_det,second.networth_det)

    def test_goal_plan_payments_conserve_cash_and_principal_without_double_charge(self):
        from finfore.forecast import run_forecast
        from finfore.goals import build_plan
        from finfore.models import Scenario
        loan = self.store.account(self.loan)
        loan.monthly_payment = 100
        self.store.update_account(loan)
        self.store.set_setting('plan_surplus',200)
        self.store.set_plan_order([f'loan:{self.loan}',f'goal:{self.purchase}',f'goal:{self.cash_goal}'])
        plan = build_plan(self.store,surplus=200)
        np.testing.assert_allclose(plan.loan_payments[f'loan:{self.loan}'][:4],[300,300,300,100])
        result = run_forecast(self.store,365,20,Scenario(follow_goal_plan=True))
        self.assertEqual(result.cash_det[-1],4000)
        self.assertEqual(result.loans[0].interest_paid,0)
        np.testing.assert_allclose(result.networth_det,14000)
        debt = result.cash_det + 10000 - result.networth_det
        self.assertGreaterEqual(float(debt.min()),0)
        self.assertEqual(debt[-1],0)

    def test_current_goals_plan_does_not_leak_into_past_projections(self):
        from finfore.forecast import run_forecast
        from finfore.models import Scenario
        baseline = run_forecast(self.store,90,20,as_of=self.prior)
        self.store.set_setting('plan_surplus',2000)
        result = run_forecast(self.store,90,20,Scenario(follow_goal_plan=True),as_of=self.prior)
        self.assertIsNone(result.goal_plan_budget)
        np.testing.assert_array_equal(baseline.cash_det,result.cash_det)
        np.testing.assert_array_equal(baseline.networth_det,result.networth_det)

    def test_goal_plan_mode_save_failure_restores_control_and_can_retry(self):
        forecast = self.visit('Projection')
        self.assertTrue(forecast.follow_goals.isChecked())
        with patch.object(self.store,'set_setting',side_effect=RuntimeError('unavailable')):
            forecast.follow_goals.setChecked(False)
        self.assertTrue(forecast.follow_goals.isChecked())
        self.assertFalse(forecast.extra_loan.isEnabled())
        self.assertIsNone(forecast._res)
        self.assertIn('could not be saved',forecast.page_error.text())
        forecast.refresh()
        self.assertIsNotNone(forecast._res)

    def test_csv_update_and_repeat_import_propagate_without_moving_anchor(self):
        self.assert_balances_agree(5000,10000,1000)
        path = Path(self.tmp.name) / 'fictional-update.csv'
        path.write_text('Date,Description,Amount\n'
            f'{self.today-timedelta(days=1)},Fictional new deposit,300\n',encoding='utf-8')
        self.store.save_import_mapping(self.aid,dict(delimiter=',',dayfirst=False,
            sign_mode='standard',col_date='Date',col_desc='Description',col_amount='Amount',
            col_debit=None,col_credit=None,col_indicator=None))
        for _ in range(2):
            accounts = self.visit('Accounts')
            accounts._select_id(self.aid)
            with patch('finfore.ui.accounts.QFileDialog.getOpenFileName',return_value=(str(path),'')), \
                 patch('finfore.ui.accounts.QMessageBox.information'):
                accounts._update_csv()
            self.assert_balances_agree(5300,10000,1000)
            self.assertEqual(len(self.store.transactions()),4)
        self.assertEqual(self.store.account(self.aid).balance,5000)


def run_tests():
    return unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(LinkedPageTests))


if __name__ == '__main__':
    unittest.main()
