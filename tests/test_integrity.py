"""Import parsing, schedules, debt in the projection and id-keyed state."""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finfore.forecast import run_forecast, scheduled_flows
from finfore.goals import _cut_candidates
from finfore.history import networth_history, weekly_category_matrix
from finfore.ingest import guess_mapping, parse_amount, parse_dates
from finfore.models import Account, AccountType, Goal, RecurringItem
from finfore.recurrence import occurrences
from finfore.spending_plan import calculate_plan
from finfore.store import Store
from finfore.util import add_months


class ImportParsingTests(unittest.TestCase):
    def test_value_date_is_never_the_amount_column(self):
        g = guess_mapping(['Posting Date', 'Value Date', 'Narrative', 'Debit', 'Credit'])
        self.assertEqual(g['amount'], '')
        self.assertEqual((g['debit'], g['credit']), ('Debit', 'Credit'))

    def test_currency_text_does_not_hide_the_sign(self):
        for text in ('$-45.00', '$(45.00)', 'USD -45.00', '-$45.00', '(45.00) USD', '45.00-'):
            self.assertEqual(parse_amount(text), -45.0, text)
        self.assertEqual(parse_amount('$1,234.56'), 1234.56)
        self.assertEqual(parse_amount('45.00 USD'), 45.0)

    def test_dates_in_the_other_order_are_refused_not_swapped(self):
        with self.assertRaisesRegex(ValueError, 'Day-first'):
            parse_dates(pd.Series(['13/01/2026', '05/02/2026']), dayfirst=False)
        with self.assertRaisesRegex(ValueError, 'Day-first'):
            parse_dates(pd.Series(['01/13/2026']), dayfirst=True)
        self.assertEqual(parse_dates(pd.Series(['13/01/2026', '05/02/2026']), dayfirst=True).tolist(),
                         [pd.Timestamp(2026, 1, 13), pd.Timestamp(2026, 2, 5)])
        self.assertEqual(parse_dates(pd.Series(['2026-01-13', '2026-02-05 10:00']), dayfirst=False)
                         .dt.date.tolist(), [date(2026, 1, 13), date(2026, 2, 5)])


class ScheduleTests(unittest.TestCase):
    def test_late_month_end_posting_keeps_the_next_month_end(self):
        pay = RecurringItem('PAY|+', 'Pay', 'monthly', 3000, date(2026, 11, 2), [30])
        self.assertEqual(occurrences(pay, date(2026, 11, 2), date(2027, 1, 31)),
                         [date(2026, 11, 30), date(2026, 12, 30), date(2027, 1, 30)])
        early = RecurringItem('RENT|-', 'Rent', 'monthly', -900, date(2026, 10, 29), [1])
        self.assertEqual(occurrences(early, date(2026, 10, 29), date(2026, 12, 15)),
                         [date(2026, 12, 1)])

    def test_early_semimonthly_posting_is_not_scheduled_again(self):
        pay = RecurringItem('PAY|+', 'Pay', 'semimonthly', 1500, date(2026, 11, 28), [1, 15])
        self.assertEqual(occurrences(pay, date(2026, 11, 28), date(2026, 12, 31)),
                         [date(2026, 12, 15)])


class WeeklySampleTests(unittest.TestCase):
    def test_bill_only_weeks_count_as_zero_but_import_gaps_do_not(self):
        start = date(2025, 1, 6)
        rows = []
        for week in range(30):
            when = start + timedelta(weeks=week)
            if 10 <= week < 20:
                continue                        # nothing imported for ten weeks
            rows.append(dict(date=when, description='RENT', amount=-500.0, category='Housing'))
            if week % 2 == 0:
                rows.append(dict(date=when, description='GROCER', amount=-100.0,
                                 category='Groceries'))
        tx = pd.DataFrame(rows)
        tx['date'] = pd.to_datetime(tx['date'])
        tx['account_id'], tx['id'] = 1, range(len(tx))
        W, _ = weekly_category_matrix(tx, {'RENT|-'}, max_weeks=None)
        self.assertEqual(len(W), 18)                # 20 covered weeks less two edges
        self.assertAlmostEqual(W['Groceries'].mean(), 50.0, delta=6)


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'integrity.db')
        self.today = date.today()
        self.cash = self.store.add_account(Account('Checking', AccountType.CHECKING, 5000, self.today))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def loan(self, balance=2000, payment=100, apr=0.0):
        return self.store.add_account(Account('Loan', AccountType.LOAN, balance, self.today,
                                              apr=apr, monthly_payment=payment))


class DebtProjectionTests(StoreFixture):
    def test_card_debt_stays_in_projected_net_worth(self):
        self.store.add_account(Account('Card', AccountType.CREDIT, -1200, self.today))
        result = run_forecast(self.store, 30, 20)
        self.assertAlmostEqual(result.networth_det[0], float(networth_history(self.store)['networth'].iloc[-1]))
        self.assertAlmostEqual(result.networth_det[0], result.cash_det[0] - 1200)

    def test_card_payment_stays_in_cash_when_every_loan_has_its_payment(self):
        loan = self.loan()
        self.store.merge_recurring([
            RecurringItem('LOAN PMT|-', 'Loan pmt', 'monthly', -100, self.today, kind='loan_payment'),
            RecurringItem('CARD MIN|-', 'Card min', 'monthly', -40, self.today, kind='loan_payment')])
        item = next(it for it in self.store.recurring() if it.key == 'LOAN PMT|-')
        self.store.update_recurring(item.id, linked_account_id=loan)
        result = run_forecast(self.store, 90, 20)
        self.assertTrue(any(f.label == 'Card min' for f in result.scheduled))
        self.assertFalse(any('Card min' in w for w in result.warnings))
        # Reported and scaled as a debt payment, as the plan budgets it.
        self.assertAlmostEqual(result.monthly_spending_reference['Debt payment'], 40)
        # Without the link it may duplicate the loan, so it is left out.
        self.store.update_recurring(item.id, linked_account_id=None)
        result = run_forecast(self.store, 90, 20)
        self.assertFalse(any(f.label == 'Card min' for f in result.scheduled))

    def test_amortization_stops_at_the_horizon_and_schedule_follows_payoff(self):
        self.loan(balance=1000, payment=100, apr=0.12)
        short = run_forecast(self.store, 60, 20)
        paid = [f for f in short.scheduled if f.source == 'loan']
        self.assertIsNone(short.loans[0].payoff)
        self.assertLessEqual(len(paid), 2)
        self.assertLess(short.loans[0].interest_paid, 10 * (len(paid) + .5))
        full = run_forecast(self.store, 400, 20)
        listed = [f for f in scheduled_flows(self.store, self.today, full.dates[-1]) if f.source == 'loan']
        self.assertEqual(listed, [f for f in full.scheduled if f.source == 'loan'])
        self.assertLessEqual(listed[-1].when, full.loans[0].payoff)

    def test_debt_override_below_loan_payments_is_not_counted_as_spare(self):
        self.loan(payment=300)
        prior = add_months(self.today.replace(day=1), -1)
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.cash, date=prior, description='pay',
                                                       amount=4000, category='Income', import_hash='pay')]))
        plan = calculate_plan(self.store, {'debt': 100})
        self.assertEqual(plan.required_debt, 300)
        self.assertEqual(plan.available, calculate_plan(self.store, {'debt': 300}).available)


class StateTests(StoreFixture):
    def test_deleted_account_leaves_nothing_for_the_next_one_with_its_id(self):
        loan = self.loan()
        self.store.merge_recurring([RecurringItem('LOAN PMT|-', 'Loan pmt', 'monthly', -100,
                                                  self.today, kind='loan_payment')])
        self.store.update_recurring(self.store.recurring()[0].id, linked_account_id=loan)
        self.store.set_plan_order([f'loan:{loan}'])
        self.store.set_plan_allocations({f'loan:{loan}': 50})
        self.store.set_setting(f'import_map:{loan}', {'date': 'Date'})
        self.assertEqual(self.store.recurring()[0].linked_account_id, loan)
        self.store.enable_undo()
        self.store.delete_account(loan)
        self.assertIsNone(self.store.recurring()[0].linked_account_id)
        self.assertEqual(self.store.plan_order(), [])
        self.assertEqual(self.store.plan_allocations(), {})
        self.assertIsNone(self.store.get_setting(f'import_map:{loan}'))
        self.store.undo.undo()
        self.assertEqual(self.store.plan_order(), [f'loan:{loan}'])

    def test_deleting_a_release_cannot_reclaim_money_another_goal_now_uses(self):
        first = self.store.add_goal(Goal('Trip', 5000, 'savings'))
        second = self.store.add_goal(Goal('Laptop', 5000, 'savings'))
        self.store.record_goal_activity(first, 'contribute', 3000, self.today, self.cash, None)
        self.store.record_goal_activity(first, 'release', 3000, self.today, self.cash, None)
        self.store.record_goal_activity(second, 'contribute', 4000, self.today, self.cash, None)
        release = next(e for e in self.store.goal_activity(first) if e['action'] == 'release')
        with self.assertRaisesRegex(ValueError, 'no longer free'):
            self.store.delete_goal_activity(release['id'])

    def test_a_purchase_whose_money_has_left_can_still_be_deleted(self):
        goal = self.store.add_goal(Goal('Trip', 1000, 'savings'))
        self.store.record_goal_activity(goal, 'contribute', 1000, self.today, self.cash, None)
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.cash, date=self.today,
            description='AIRLINE', amount=-4800.0, category='Travel', import_hash='trip')]))
        self.store.record_goal_activity(goal, 'purchase', 1000, self.today, self.cash, None)
        purchase = next(e for e in self.store.goal_activity(goal) if e['action'] == 'purchase')
        self.store.delete_goal_activity(purchase['id'])
        self.assertEqual(self.store.goals()[0].spent_amount, 0)

    def test_recurring_wants_are_not_counted_again_inside_their_category(self):
        rows = [dict(account_id=self.cash, date=self.today - timedelta(days=30 * m + 3),
                     description='CHIPOTLE 1123', amount=-60.0, category='Dining',
                     import_hash=f'dining {m}') for m in range(4)]
        self.store.add_transactions(pd.DataFrame(rows))
        self.store.merge_recurring([RecurringItem('CHIPOTLE|-', 'Chipotle', 'monthly', -60,
                                                  self.today - timedelta(days=3))])
        cuts = _cut_candidates(self.store)
        self.assertTrue(any(c.recurring for c in cuts))
        self.assertFalse(any(c.name == 'Dining' and c.monthly >= 5 for c in cuts))


def run_tests():
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in
                               (ImportParsingTests, ScheduleTests, DebtProjectionTests, StateTests))
    return unittest.TextTestRunner(verbosity=1).run(suite)


if __name__ == "__main__":
    unittest.main()
