"""Budget conservation and goal funding without changing the bank ledger."""
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finfore.categorize import BUSINESS_CATEGORY
from finfore.funding import cash_capacity, goal_buckets
from finfore.goals import build_plan, surplus_monthly, targets
from finfore.models import Account, AccountType, Goal, RecurringItem, Scenario
from finfore.forecast import run_forecast
from finfore.spending_plan import calculate_plan
from finfore.store import Store
from finfore.util import add_months, month_firsts_between


class SpendingPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'plan.db'
        self.store = Store(self.path)
        self.today = date.today()
        self.aid = self.store.add_account(Account('Checking', AccountType.CHECKING, 5000, self.today))
        self.gid = self.store.add_goal(Goal('Vacation', 2000, 'savings'))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def tx(self, value, category='Income', when=None, tx_hash=None, aid=None):
        tx_hash = tx_hash or str(len(self.store.transactions()))
        self.store.add_transactions(pd.DataFrame([dict(account_id=aid or self.aid, date=when or self.today,
            description=tx_hash, amount=value, category=category, import_hash=tx_hash)]))
        return int(self.store.transactions().iloc[-1].id)

    def record(self, action, value, aid='default', txid=None, when=None, gid=None):
        return self.store.record_goal_activity(gid or self.gid, action, value, when or self.today,
            self.aid if aid == 'default' else aid, txid)

    def test_recorded_loan_payment_is_budgeted_once_and_commitment_fills_missing_history(self):
        prior = add_months(self.today.replace(day=1), -1)
        self.tx(4000, when=prior)
        self.tx(-2000, 'Housing', prior)
        self.tx(-300, 'Debt payment', prior)
        loan = self.store.add_account(Account('Car', AccountType.LOAN, 5000, self.today, monthly_payment=300))
        plan = calculate_plan(self.store)
        self.assertEqual(plan.amounts['essential'], 2000)
        self.assertEqual(plan.amounts['debt'], 300)
        self.assertEqual(plan.available, 1700)
        account = self.store.account(loan)
        account.monthly_payment = 450
        self.store.update_account(account)
        self.assertEqual(calculate_plan(self.store).available, 1550)

    def test_monthly_plan_conserves_income_and_shares_budget_with_goals_undo_and_reopen(self):
        self.store.enable_undo()
        self.store.save_spending_plan(dict(income=4000, essential=2000, debt=300,
            frivolous=200, giving=100, unknown=0, reserve=100, investing=300), 600)
        plan = calculate_plan(self.store)
        self.assertEqual((plan.available, plan.remaining(600)), (1000, 400))
        self.assertEqual(surplus_monthly(self.store), 1000)
        self.assertEqual(build_plan(self.store, surplus=self.store.get_setting('plan_surplus')).surplus, 600)
        self.store.undo.undo()
        self.assertIsNone(self.store.get_setting('spending_plan'))
        self.store.undo.redo()
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(calculate_plan(self.store).remaining(self.store.get_setting('plan_surplus')), 400)

    def test_empty_history_and_overbudget_remain_explicit(self):
        plan = calculate_plan(self.store, dict(income=1000, essential=1200))
        self.assertIsNone(plan.details)
        self.assertEqual(plan.available, -200)
        self.assertEqual(plan.remaining(100), -300)

    def test_refunds_preserve_group_floor_and_business_and_transfers_excluded(self):
        prior = add_months(self.today.replace(day=1), -1)
        for value, category in [(4000,'Income'), (-2000,'Housing'), (500,'Housing'),
            (-300,'Debt payment'), (-500,'Shopping'), (1000,'Shopping'),
            (-5000,BUSINESS_CATEGORY), (5000,'Transfer')]:
            self.tx(value, category, prior)
        plan = calculate_plan(self.store)
        self.assertEqual(plan.amounts['income'], 4000)
        self.assertEqual(plan.amounts['essential'], 1500)
        self.assertEqual(plan.amounts['frivolous'], 0)
        self.assertEqual(plan.expenses, 1800)

    def test_invalid_budget_does_not_partially_write(self):
        for overrides, budget in [({'income':float('nan')}, 5), ({'wrong':1}, 5),
                                  ({'income':100}, -1), ({'income':100}, float('inf'))]:
            with self.assertRaises(ValueError):
                self.store.save_spending_plan(overrides, budget)
            self.assertIsNone(self.store.get_setting('spending_plan'))
            self.assertIsNone(self.store.get_setting('plan_surplus'))

    def save_budget(self, **changes):
        values = dict(income=4000, essential=2000, debt=0, frivolous=200,
                      giving=100, unknown=0, reserve=100, investing=0)
        values.update(changes)
        self.store.save_spending_plan(values, 600)

    def test_category_cuts_free_room_and_persist_without_changing_transactions(self):
        prior = add_months(self.today.replace(day=1), -1)
        for amount, cat in [(4000, 'Income'), (-2000, 'Housing'),
                            (-300, 'Dining'), (-100, 'Subscriptions'), (-100, 'Charitable donation')]:
            self.tx(amount, cat, prior)
        before = self.store.transactions()
        self.store.save_spending_plan({'income': 4200, 'category:Dining': 100,
                                      'category:Subscriptions': 50}, 500)
        plan = calculate_plan(self.store)
        self.assertEqual(plan.amounts['frivolous'], 150)
        self.assertEqual((plan.available, plan.remaining(500)), (1950, 1450))
        pd.testing.assert_frame_equal(before, self.store.transactions())
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(calculate_plan(self.store).amounts['category:Dining'], 100)
        self.assertEqual(build_plan(self.store, surplus=500).surplus, 500)

    def test_category_overrides_handle_refunds_and_total_override_takes_priority(self):
        prior = add_months(self.today.replace(day=1), -1)
        self.tx(-1000, 'Dining', prior)
        self.tx(500, 'Shopping', prior)
        self.assertEqual(calculate_plan(self.store, {'category:Dining': 200}).amounts['frivolous'], 0)
        self.assertEqual(calculate_plan(self.store, {'category:Shopping': 0}).amounts['frivolous'], 1000)
        self.assertEqual(calculate_plan(self.store, {'frivolous': 300, 'category:Dining': 200}).amounts['frivolous'], 300)

    def test_saved_budget_changes_forecast_only_when_enabled_and_earmarks_stay_cash(self):
        baseline = run_forecast(self.store, 90, 20)
        self.save_budget(**{'category:Dining': 200})
        unchanged = run_forecast(self.store, 90, 20)
        np.testing.assert_array_equal(baseline.cash_det, unchanged.cash_det)
        planned = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        self.assertTrue(planned.spending_plan_applied)
        # Reserves and the $600 goal allowance earmark cash, without spending it.
        self.assertAlmostEqual(planned.cash_det[-1], 5000 + 1700 * 90 / 30.44)
        self.assertEqual(planned.monthly_income_reference, 4000)
        self.assertEqual(planned.monthly_spending_reference['Dining'], 200)
        self.assertEqual(self.store.goals()[0].saved_amount, 0)
        adjusted = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True,
            income_multiplier=1.1, category_multipliers={'Dining': .5}))
        self.assertAlmostEqual(adjusted.cash_det[-1] - planned.cash_det[-1], 500 * 90 / 30.44)

    def test_budget_scales_recurring_and_variable_spending_once(self):
        for week in range(1, 13):
            self.tx(-70, 'Dining', self.today - timedelta(weeks=week), f'Dining {week}')
        self.store.merge_recurring([
            RecurringItem('PAYROLL|+', 'Payroll', 'monthly', 3000, self.today, kind='income'),
            RecurringItem('NETFLIX|-', 'Netflix', 'monthly', -60, self.today)])
        self.save_budget(essential=0, frivolous=150, giving=0,
                         **{'category:Dining': 100, 'category:Subscriptions': 50})
        result = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        self.assertGreaterEqual(result.history_weeks, 4)
        self.assertEqual(result.monthly_income_reference, 4000)
        self.assertAlmostEqual(result.monthly_spending_reference['Dining'], 100)
        self.assertAlmostEqual(result.monthly_spending_reference['Subscriptions'], 50)
        from finfore.recurrence import occurrences
        count = len(occurrences(self.store.recurring()[0], self.today, result.dates[-1]))
        self.assertAlmostEqual(result.cash_det[-1], 5000 + count * (4000 - 50) - 100 * 90 / 30.44)

    def test_planned_investing_moves_cash_to_assets_once(self):
        self.store.add_account(Account('Investing', AccountType.INVESTMENT, 1000,
            self.today, monthly_contribution=100, annual_return=0, volatility=0))
        self.save_budget(investing=250)
        result = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        count = len(month_firsts_between(self.today, result.dates[-1]))
        self.assertAlmostEqual(result.cash_det[-1], 5000 + 1700 * 90 / 30.44 - 250 * count)
        self.assertAlmostEqual(result.networth_det[-1], 6000 + 1700 * 90 / 30.44)

    def test_manual_budget_retains_a_known_bill_date_without_monthly_history(self):
        self.store.merge_recurring([RecurringItem('NETFLIX|-', 'Netflix', 'monthly', -60, self.today)])
        self.save_budget(income=0, essential=0, frivolous=50, giving=0)
        result = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        from finfore.recurrence import occurrences
        days = occurrences(self.store.recurring()[0], self.today, result.dates[-1])
        changes = np.flatnonzero(np.diff(result.cash_det)) + 1
        self.assertEqual([result.dates[i] for i in changes], days)
        self.assertAlmostEqual(result.cash_det[-1], 5000 - 50 * len(days))

    def test_zero_spending_budget_removes_its_historical_variation(self):
        for week in range(1, 13):
            self.tx(-10 * week, 'Dining', self.today - timedelta(weeks=week), f'Dining {week}')
        self.save_budget(income=0, essential=0, frivolous=0, giving=0)
        result = run_forecast(self.store, 90, 50, Scenario(follow_goal_plan=True))
        for values in result.cash_pct.values():
            np.testing.assert_allclose(values, 5000)

    def test_planned_debt_matches_goals_and_forecast_without_double_payment(self):
        loan = self.store.add_account(Account('Loan', AccountType.LOAN, 2000,
            self.today, monthly_payment=100))
        self.store.set_plan_order([f'loan:{loan}', f'goal:{self.gid}'])
        self.save_budget(income=1000, essential=0, debt=200, frivolous=0, giving=0)
        self.store.set_setting('plan_surplus', 100)
        ladder = build_plan(self.store, surplus=100)
        self.assertEqual(ladder.loan_payments[f'loan:{loan}'][0], 300)
        result = run_forecast(self.store, 365, 20, Scenario(follow_goal_plan=True))
        self.assertEqual(result.loans[0].payment, 200)
        self.assertEqual(result.loans[0].interest_paid, 0)
        self.assertAlmostEqual(result.cash_det[-1], 5000 + 1000 * 365 / 30.44 - 2000)
        np.testing.assert_allclose(result.networth_det,
            3000 + np.arange(366) * 1000 / 30.44)

    def test_current_budget_never_changes_a_historical_forecast(self):
        past = self.today - timedelta(days=60)
        first = run_forecast(self.store, 30, 20, as_of=past)
        self.save_budget(income=100000)
        second = run_forecast(self.store, 30, 20, Scenario(follow_goal_plan=True), as_of=past)
        self.assertFalse(second.spending_plan_applied)
        np.testing.assert_array_equal(first.cash_det, second.cash_det)

    def test_plan_cannot_reduce_loan_obligations_or_invent_an_investment_account(self):
        loan = self.store.add_account(Account('Loan', AccountType.LOAN, 2000,
            self.today, monthly_payment=100))
        self.save_budget(debt=50, investing=250)
        self.store.set_setting('plan_surplus', 0)
        ladder = build_plan(self.store, surplus=0)
        self.assertEqual(ladder.loan_payments[f'loan:{loan}'][0], 100)
        result = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        self.assertTrue(any('less than the existing loan payments' in w for w in result.warnings))
        self.assertTrue(any('no investment account' in w for w in result.warnings))

    def test_cash_dip_explanation_uses_the_same_planned_schedule_as_the_graph(self):
        from finfore.forecast import scheduled_flows
        self.store.merge_recurring([
            RecurringItem('PAYROLL|+', 'Payroll', 'monthly', 3000, self.today, kind='income'),
            RecurringItem('NETFLIX|-', 'Netflix', 'monthly', -60, self.today)])
        loan = self.store.add_account(Account('Loan', AccountType.LOAN, 2000,
            self.today, monthly_payment=100))
        self.store.add_account(Account('Investing', AccountType.INVESTMENT, 1000,
            self.today, monthly_contribution=100, annual_return=0, volatility=0))
        self.store.set_plan_order([f'loan:{loan}', f'goal:{self.gid}'])
        self.save_budget(essential=0, debt=200, frivolous=100, giving=0, investing=250)
        self.store.set_setting('plan_surplus', 100)
        scenario = Scenario(follow_goal_plan=True)
        result = run_forecast(self.store, 90, 20, scenario)
        flows = scheduled_flows(self.store, self.today, result.dates[-1], scenario)
        self.assertEqual(flows, result.scheduled)
        self.assertAlmostEqual(result.cash_det[-1] - result.cash_det[0], sum(f.amount for f in flows))
        self.assertTrue(all(f.amount == -250 for f in flows if f.source == 'investment'))
        self.assertTrue(all(f.amount == -100 for f in flows if f.label == 'Netflix'))
        self.assertTrue(all(f.amount == -300 for f in flows if f.source == 'loan'))

    def test_funding_earmarks_cash_and_purchase_retains_progress_without_touching_ledger(self):
        self.tx(-400, 'Travel')
        before = self.store.transactions().copy()
        self.record('contribute', 700)
        self.record('purchase', 400)
        goal = self.store.goals()[0]
        self.assertEqual((goal.saved_amount, goal.spent_amount), (300, 400))
        self.assertEqual(targets(self.store)[0].progress, 700)
        self.assertEqual(goal_buckets(self.store, self.gid)[self.aid], 300)
        pd.testing.assert_frame_equal(before, self.store.transactions())
        self.assertEqual(self.store.account(self.aid).balance, 5000)
        self.record('release', 200)
        self.assertEqual(targets(self.store)[0].progress, 500)
        self.assertEqual(cash_capacity(self.store)[0][self.aid], 4900)

    def test_source_account_and_household_limits_prevent_double_earmarks(self):
        small = self.store.add_account(Account('Savings', AccountType.SAVINGS, 100, self.today))
        second = self.store.add_goal(Goal('Computer', 10000, 'savings'))
        self.record('contribute', 100, aid=small)
        with self.assertRaises(ValueError):
            self.record('contribute', 1, aid=small, gid=second)
        self.store.add_goal(Goal('Existing reserve', 5000, 'savings', saved_amount=5000))
        with self.assertRaises(ValueError):
            self.record('contribute', 1)
        self.assertEqual(len(self.store.goal_activity()), 1)

    def test_locating_existing_savings_does_not_add_progress_and_release_cannot_overdraw_bucket(self):
        self.store.update_goal(self.gid, saved_amount=500)
        self.record('assign', 300)
        self.assertEqual(self.store.goals()[0].saved_amount, 500)
        self.assertEqual(goal_buckets(self.store, self.gid), {self.aid:300, None:200})
        with self.assertRaises(ValueError):
            self.record('release', 201, aid=None)
        self.record('release', 200, aid=None)
        self.assertEqual(self.store.goals()[0].saved_amount, 300)
        self.assertEqual(goal_buckets(self.store, self.gid)[None], 0)

    def test_linked_transaction_can_be_split_but_not_reused_across_goals_or_reimport(self):
        txid = self.tx(500, tx_hash='pay')
        second = self.store.add_goal(Goal('Computer', 1000, 'savings'))
        self.record('contribute', 300, txid=txid)
        self.record('contribute', 200, txid=txid, gid=second)
        with self.assertRaises(ValueError):
            self.record('contribute', 1, txid=txid)
        self.store.delete_transactions([txid], suppress=False)
        self.assertIsNone(self.store.goal_activity(self.gid)[0]['transaction_id'])
        new_id = self.tx(500, tx_hash='pay')
        with self.assertRaises(ValueError):
            self.record('contribute', 1, txid=new_id)
        self.assertEqual(len(self.store.goal_activity()), 2)

    def test_linked_purchase_cannot_be_reused_and_preserves_completed_goal(self):
        self.record('contribute', 2000)
        txid = self.tx(-2000, 'Travel')
        self.record('purchase', 2000, txid=txid)
        self.assertEqual(self.store.goals()[0].saved_amount, 0)
        self.assertTrue(targets(self.store)[0].complete)
        self.assertEqual(cash_capacity(self.store)[1], 5000)
        with self.assertRaises(ValueError):
            self.record('contribute', 1)

    def test_link_validation_rejects_business_reimbursements_wrong_direction_account_date(self):
        biz = self.tx(500, BUSINESS_CATEGORY)
        with self.assertRaises(ValueError): self.record('contribute', 500, txid=biz)
        pay = self.tx(600)
        self.store.set_business_reimbursement(pay, 200)
        with self.assertRaises(ValueError): self.record('contribute', 401, txid=pay)
        with self.assertRaises(ValueError): self.record('contribute', 100, txid=pay, aid=None)
        with self.assertRaises(ValueError): self.record('contribute', 100, txid=pay, when=self.today-timedelta(days=1))
        spend = self.tx(-100, 'Dining')
        with self.assertRaises(ValueError): self.record('contribute', 100, txid=spend)
        self.record('contribute', 400, txid=pay)
        with self.assertRaises(ValueError): self.record('purchase', 100, txid=pay)

    def test_account_delete_preserves_goal_savings_as_unlocated_and_undo_restores_links(self):
        self.record('contribute', 400)
        self.store.enable_undo()
        self.store.delete_account(self.aid)
        self.assertEqual(goal_buckets(self.store, self.gid), {None:400})
        self.store.undo.undo()
        self.assertEqual(goal_buckets(self.store, self.gid)[self.aid], 400)

    def test_activity_is_atomic_undoable_and_blocks_snapshot_edits(self):
        self.store.enable_undo()
        self.record('contribute', 400)
        self.store.undo.undo()
        self.assertEqual(self.store.goals()[0].saved_amount, 0)
        self.assertEqual(self.store.goal_activity(), [])
        self.store.undo.redo()
        with self.assertRaises(ValueError): self.store.update_goal(self.gid, saved_amount=500)
        with self.assertRaises(ValueError): self.store.update_goal(self.gid, kind='cash')
        with self.assertRaises(ValueError): self.store.update_goal(self.gid, amount=300)
        self.store.update_goal(self.gid, name='Trip', amount=3000)
        self.assertEqual(self.store.goals()[0].saved_amount, 400)
        for action, value, when in [('contribute',float('nan'),self.today), ('release',401,self.today),
                                    ('assign',1,self.today), ('contribute',1,self.today+timedelta(days=1))]:
            with self.assertRaises(ValueError): self.record(action,value,when=when)
        self.assertEqual(len(self.store.goal_activity()), 1)
        self.store.clear_all()
        self.assertEqual(self.store.goal_activity(), [])

    def test_migration_preserves_old_saved_amount_and_new_purchase_column_defaults_zero(self):
        path = Path(self.temp.name) / 'old.db'
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE goals (id INTEGER PRIMARY KEY, name TEXT, amount REAL, kind TEXT, saved_amount REAL)')
            con.execute("INSERT INTO goals VALUES(1,'Trip',2000,'savings',700)")
        old = Store(path)
        try:
            self.assertEqual(old.goals()[0].saved_amount, 700)
            self.assertEqual(old.goals()[0].spent_amount, 0)
            self.assertEqual(goal_buckets(old, 1), {None:700})
        finally:
            old.close()


def run_tests():
    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SpendingPlanTests))

if __name__ == '__main__':
    unittest.main()
