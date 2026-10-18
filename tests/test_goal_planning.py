"""Goal progress, existing loan commitments and budget conservation."""
from datetime import date
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finfore.forecast import run_forecast
from finfore.goals import build_plan, deadline_plan, surplus_monthly, targets
from finfore.models import Account, AccountType, Goal, RecurringItem
from finfore.store import Store
from finfore.util import add_months


class GoalPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "goals.db")
        self.today = date.today()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def loan(self, balance=1000, payment=0, apr=0):
        return self.store.add_account(Account("Car", AccountType.LOAN, balance,
                                              self.today, monthly_payment=payment, apr=apr))

    def pattern(self, aid, amount=-200, cadence="monthly", key="car|-", active=True):
        self.store.merge_recurring([RecurringItem(key, key, cadence, amount,
            self.today, kind="loan_payment", active=active, linked_account_id=aid)])

    def test_linked_payment_replaces_account_fallback_and_releases_after_payoff(self):
        aid = self.loan(payment=100)
        self.pattern(aid)
        gid = self.store.add_goal(Goal("Vacation", 500, "savings"))
        self.store.set_plan_order([f"goal:{gid}", f"loan:{aid}"])
        plan = build_plan(self.store, surplus=0)
        car = plan.by_key(f"loan:{aid}")
        self.assertEqual(car.target.required, 200)
        self.assertEqual(car.monthly, 0)
        self.assertEqual(car.months, 4)  # five installments, including this month
        self.assertEqual(plan.by_key(f"goal:{gid}").months, 7)
        self.assertEqual(plan.cascades[0].amount, 200)

    def test_active_patterns_sum_at_their_monthly_cadence_without_adding_fallback(self):
        aid = self.loan(payment=999)
        self.pattern(aid, -100, "biweekly")
        self.pattern(aid, -50, key="second|-", cadence="monthly")
        self.pattern(aid, -800, key="old|-", active=False)
        self.pattern(None, -9999, key="unlinked|-")
        self.assertAlmostEqual(targets(self.store)[0].required, 100*30.44/14 + 50)
        res = run_forecast(self.store, 30, 20)
        self.assertAlmostEqual(res.loans[0].payment, 100*30.44/14 + 50)

    def test_deactivated_pattern_uses_account_payment_and_missing_payment_is_explicit(self):
        aid = self.loan(payment=80)
        self.pattern(aid, active=False)
        self.assertEqual(targets(self.store)[0].required, 80)
        account = self.store.accounts()[0]
        account.monthly_payment = 0
        self.store.update_account(account)
        plan = build_plan(self.store, surplus=0, horizon_months=12)
        self.assertTrue(any("no existing payment" in w for w in plan.warnings))
        self.assertIsNone(plan.allocations[0].eta)

    def test_recorded_payment_is_subtracted_once_and_total_includes_extra(self):
        aid = self.loan(payment=200)
        checking = self.store.add_account(Account("Checking", AccountType.CHECKING,
                                                  0, self.today))
        month = add_months(self.today.replace(day=1), -1)
        self.store.add_transactions(pd.DataFrame([
            dict(account_id=checking, date=month, description=name, amount=amount,
                 category=cat, import_hash=name)
            for name, amount, cat in [("Paycheck", 3000, "Income"),
                ("Rent", -2000, "Housing"), ("Car", -200, "Debt payment")]]))
        self.assertEqual(surplus_monthly(self.store), 800)
        car = build_plan(self.store).by_key(f"loan:{aid}")
        self.assertEqual(car.monthly, 800)
        self.assertEqual(car.total_monthly, 1000)
        self.assertEqual(car.months, 0)

    def test_loan_interest_is_paid_before_principal(self):
        self.loan(balance=1000, payment=100, apr=.12)
        plan = build_plan(self.store, surplus=0, horizon_months=1)
        self.assertAlmostEqual(plan.progress[plan.allocations[0].target.key][0], .09)

    def test_purchase_progress_belongs_to_the_goal_and_survives_reordering(self):
        self.store.add_account(Account("Checking", AccountType.CHECKING, 10000, self.today))
        vacation = self.store.add_goal(Goal("Vacation", 2000, "savings", saved_amount=600))
        computer = self.store.add_goal(Goal("Computer", 1500, "savings", saved_amount=100))
        cash = self.store.add_goal(Goal("Cash milestone", 10000, "cash"))
        self.store.set_plan_order([f"goal:{vacation}", f"goal:{computer}", f"goal:{cash}"])
        first = {t.key:t.progress for t in targets(self.store)}
        self.store.set_plan_order(list(reversed(self.store.plan_order())))
        self.assertEqual(first, {t.key:t.progress for t in targets(self.store)})
        self.assertEqual(first[f"goal:{vacation}"], 600)
        self.assertEqual(first[f"goal:{computer}"], 100)
        self.assertEqual(first[f"goal:{cash}"], 10000)  # household milestone includes earmarks

    def test_over_reserved_savings_warn_and_do_not_create_cash(self):
        self.store.add_goal(Goal("Vacation", 2000, "savings", saved_amount=100))
        plan = build_plan(self.store, surplus=0)
        self.assertTrue(any("exceed current funding balances" in w for w in plan.warnings))
        self.assertEqual(plan.allocations[0].target.progress, 100)
        self.assertEqual(self.store.accounts(), [])

    def test_spare_money_remaining_at_completion_is_used_in_the_same_month(self):
        for name in ("Vacation", "Computer"):
            self.store.add_goal(Goal(name, 100, "savings"))
        plan = build_plan(self.store, surplus=200, horizon_months=1)
        self.assertTrue(all(a.months == 0 for a in plan.allocations))
        self.assertEqual(sum(a.monthly for a in plan.allocations), 200)

    def test_unused_final_loan_payment_can_fund_a_purchase(self):
        self.loan(balance=50, payment=200)
        gid = self.store.add_goal(Goal("Computer", 150, "savings"))
        plan = build_plan(self.store, surplus=0, horizon_months=1)
        self.assertEqual(plan.by_key(f"goal:{gid}").months, 0)

    def test_saved_amount_persists_and_is_undoable(self):
        gid = self.store.add_goal(Goal("Vacation", 2000, "savings", saved_amount=200))
        self.store.enable_undo()
        self.store.update_goal(gid, saved_amount=400)
        self.assertEqual(self.store.goals()[0].saved_amount, 400)
        self.store.undo.undo()
        self.assertEqual(self.store.goals()[0].saved_amount, 200)
        self.store.undo.redo()
        self.store.close()
        self.store = Store(self.root / "goals.db")
        self.assertEqual(self.store.goals()[0].saved_amount, 400)

    def test_old_schema_preserves_balance_based_goals(self):
        path = self.root / "legacy.db"
        with closing(sqlite3.connect(path)) as con, con:
            con.execute("CREATE TABLE goals(id INTEGER PRIMARY KEY, name TEXT, amount REAL, kind TEXT)")
            con.execute("INSERT INTO goals VALUES(1, 'Emergency fund', 5000, 'cash')")
        old = Store(path)
        try:
            self.assertEqual(old.goals(), [Goal("Emergency fund", 5000, "cash", id=1)])
        finally:
            old.close()

    def test_forecast_does_not_call_purchase_funded_from_unassigned_cash(self):
        self.store.add_account(Account("Checking", AccountType.CHECKING, 10000, self.today))
        self.store.add_goal(Goal("Vacation", 1000, "savings"))
        self.store.add_goal(Goal("Cash milestone", 2000, "cash"))
        res = run_forecast(self.store, 30, 20)
        self.assertEqual([o.goal.name for o in res.goal_outcomes], ["Cash milestone"])
        from finfore.report import MonthReport, _outlook
        report = MonthReport(self.today.replace(day=1), self.today)
        _outlook(self.store, report)
        vacation = next(g for g in report.goals if g[0] == "Vacation")
        self.assertIn("set aside", vacation[2])
        self.assertNotIn("already there", vacation[2])

    def test_deadline_includes_waiting_and_interest_using_the_actual_plan(self):
        aid = self.loan(balance=2000, payment=50, apr=.12)
        gid = self.store.add_goal(Goal("Computer", 1000, "savings"))
        self.store.set_plan_order([f"loan:{aid}", f"goal:{gid}"])
        plan = build_plan(self.store, surplus=200)
        by = add_months(self.today.replace(day=1), 4)
        dl = deadline_plan(self.store, plan, f"goal:{gid}", by)
        # Standalone $1000/5 looks fully funded at $200/mo, but the car goes first.
        self.assertGreater(dl.shortfall, 0)
        alt = build_plan(self.store, surplus=200, extra_monthly=dl.shortfall)
        self.assertLessEqual(alt.by_key(f"goal:{gid}").eta, by)
        self.assertEqual(dl.rank, 2)


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(GoalPlanningTests))


if __name__ == "__main__":
    unittest.main()
