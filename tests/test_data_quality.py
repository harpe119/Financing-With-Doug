"""Synthetic evidence-check regressions; no personal financial data required."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finfore.data_quality import inspect_data_quality
from finfore.models import Account, AccountType, OneOffEvent, RecurringItem
from finfore.recurrence import normalize_desc
from finfore.store import Store


class DataQualityTests(unittest.TestCase):
    today = date(2026, 10, 11)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "quality.db")
        self._seq = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def account(self, name="Checking", kind=AccountType.CHECKING, days=0):
        return self.store.add_account(Account(name, kind, balance=1000,
            balance_date=self.today - timedelta(days=days)))

    def transactions(self, aid, dates, amount=-10, category="Groceries", description="ALDI"):
        rows = []
        for d in dates:
            self._seq += 1
            rows.append(dict(account_id=aid, date=d, amount=amount,
                category=category, description=description, import_hash=f"fixture-{self._seq}"))
        self.store.add_transactions(pd.DataFrame(rows))

    def check(self, key):
        return next(c for c in inspect_data_quality(self.store, self.today).checks if c.key == key)

    def test_empty_store_never_claims_a_reliable_forecast(self):
        report = inspect_data_quality(self.store, self.today)
        self.assertEqual(report.as_of, self.today)
        self.assertIsNone(report.last_transaction)
        self.assertEqual({c.key for c in report.concerns},
                         {"balances", "recent_transactions", "history_length"})
        self.assertEqual(report.summary, "3 checks need review")

    def test_fresh_balances_and_long_continuous_history(self):
        aid = self.account()
        dates = list(pd.date_range("2026-01-01", self.today, freq="7D")) + [self.today]
        self.transactions(aid, dates)
        report = inspect_data_quality(self.store, self.today)
        self.assertFalse(report.concerns)
        self.assertEqual(report.first_transaction, date(2026, 1, 1))
        self.assertEqual(report.last_transaction, self.today)
        self.assertEqual(report.summary, "No gaps flagged by these checks")

    def test_balance_threshold_and_latest_snapshot(self):
        aid = self.account(days=30)
        self.assertFalse(self.check("balances").needs_review)
        a = self.store.account(aid)
        a.balance_date -= timedelta(days=1)
        self.store.update_account(a)
        self.assertTrue(self.check("balances").needs_review)
        self.store.add_snapshot(aid, self.today, 1200)
        self.assertFalse(self.check("balances").needs_review)
        self.store.add_snapshot(aid, self.today + timedelta(days=1), 1200)
        self.assertIn("is in the future", self.check("balances").detail)

    def test_stale_ledger_is_checked_per_account(self):
        a, b = self.account(), self.account("Quiet savings", AccountType.SAVINGS)
        self.transactions(a, [self.today])
        self.transactions(b, [self.today - timedelta(days=31)])
        check = self.check("recent_transactions")
        self.assertTrue(check.needs_review)
        self.assertIn("Quiet savings: latest transaction", check.detail)
        self.assertNotIn("Checking: latest transaction", check.detail)
        self.assertIn("quiet account may be complete", check.detail)

    def test_quiet_months_do_not_claim_missing_statements(self):
        aid = self.account()
        self.transactions(aid, [date(2026, 7, 15), self.today])
        check = self.check("quiet_months")
        self.assertTrue(check.needs_review)
        self.assertIn("Aug 2026, Sep 2026", check.detail)
        self.assertNotIn("Jul 2026,", check.detail)
        self.assertIn("may be quiet months or missing history", check.detail)
        self.transactions(aid, [date(2026, 8, 1), date(2026, 9, 1)], category="Transfer")
        self.assertFalse(self.check("quiet_months").needs_review)

    def test_gaps_are_account_specific_and_bounded_to_twelve_months(self):
        a, b = self.account(), self.account("Savings", AccountType.SAVINGS)
        self.transactions(a, pd.date_range("2025-01-01", self.today, freq="7D"))
        self.transactions(b, [date(2024, 1, 1), self.today])
        check = self.check("quiet_months")
        self.assertTrue(check.needs_review)
        self.assertIn("Savings: Oct 2025", check.detail)
        self.assertNotIn("Checking:", check.detail)
        self.assertNotIn("Sep 2025", check.detail)

    def test_loan_and_investment_do_not_require_transaction_ledgers(self):
        aid = self.account()
        self.account("Loan", AccountType.LOAN)
        self.account("Investments", AccountType.INVESTMENT)
        self.transactions(aid, [self.today])
        self.assertFalse(self.check("recent_transactions").needs_review)
        self.assertFalse(self.check("quiet_months").needs_review)

    def test_short_history_is_not_padded_by_transfers(self):
        aid = self.account()
        self.transactions(aid, [self.today - timedelta(days=200)], category="Transfer")
        self.transactions(aid, [self.today - timedelta(days=1), self.today])
        check = self.check("history_length")
        self.assertTrue(check.needs_review)
        self.assertIn("span 2 days", check.detail)

    def test_category_amounts_share_window_and_exclusions(self):
        aid = self.account()
        self.transactions(aid, [self.today], amount=-25, category="Misc")
        self.transactions(aid, [self.today], amount=-75)
        self.transactions(aid, [self.today], amount=20, category="Other income")
        self.transactions(aid, [self.today], amount=-500, category="Transfer")
        self.transactions(aid, [self.today], amount=0, category="Misc")
        self.transactions(aid, [self.today - timedelta(days=180)], amount=-1000, category="Misc")
        self.transactions(aid, [self.today + timedelta(days=1)], amount=-1000, category="Misc")
        inv = self.account("Investments", AccountType.INVESTMENT)
        self.transactions(inv, [self.today], amount=-1000, category="Misc")
        check = self.check("categories")
        self.assertTrue(check.needs_review)
        self.assertIn("no category: 2", check.detail)
        self.assertIn("$25.00 of money out (25.0%", check.detail)
        self.assertIn("$20.00 of money in", check.detail)
        self.assertIn("not omitted from the forecast", check.detail)

    def test_correcting_a_category_removes_the_flag_without_cached_state(self):
        aid = self.account()
        self.transactions(aid, [self.today], category="Misc")
        self.assertTrue(self.check("categories").needs_review)
        self.store.set_category(self.store.transactions()["id"].tolist(), "Groceries")
        self.assertFalse(self.check("categories").needs_review)

    def test_no_spending_does_not_divide_by_zero(self):
        aid = self.account()
        self.transactions(aid, [self.today], amount=20, category="Other income")
        detail = self.check("categories").detail
        self.assertIn("$0.00 of money out", detail)
        self.assertNotIn("nan", detail)

    def test_unassigned_calendar_outflows_include_events_but_not_income(self):
        self.account()
        self.store.add_event(OneOffEvent("Repair", self.today + timedelta(days=1), -100))
        self.store.add_event(OneOffEvent("Bonus", self.today + timedelta(days=1), 500))
        self.store.add_event(OneOffEvent("Past", self.today, -300))
        self.store.add_event(OneOffEvent("Later", self.today + timedelta(days=31), -300))
        ignored = self.store.add_event(OneOffEvent("Inactive", self.today + timedelta(days=1), -300))
        self.store.update_event(ignored, active=False)
        check = self.check("unassigned")
        self.assertIn("to pay from: 1, totaling $100 between", check.detail)
        self.assertIn("not the calendar's account-specific balances", check.detail)

    def test_confirmed_recurring_payment_is_not_unassigned(self):
        aid = self.account()
        self.transactions(aid, [self.today], amount=-100, category="Housing", description="RENT")
        self.store.merge_recurring([RecurringItem(normalize_desc("RENT") + "|-", "Rent",
            "monthly", -100, self.today, days_of_month=[12])])
        self.assertFalse(self.check("unassigned").needs_review)

    def test_invalid_future_dates_are_separate_and_cannot_refresh_a_ledger(self):
        aid = self.account()
        self.transactions(aid, [self.today + timedelta(days=1)])
        self.store.con.execute("INSERT INTO transactions (account_id,date,description,amount,category,import_hash) VALUES (?,?,?,?,?,?)",
            (aid, "not a date", "Bad date", -10, "Misc", "invalid"))
        self.store.con.commit()
        report = inspect_data_quality(self.store, self.today)
        self.assertIsNone(report.last_transaction)
        self.assertTrue(self.check("recent_transactions").needs_review)
        self.assertIn("Unreadable transaction dates: 1. Transactions dated after today: 1", self.check("dates").detail)

    def test_quality_inspection_does_not_write_to_the_database(self):
        aid = self.account(days=100)
        self.transactions(aid, [self.today], category="Misc")
        before = self.store.con.total_changes
        inspect_data_quality(self.store, self.today)
        self.assertEqual(self.store.con.total_changes, before)


def run_tests():
    return unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DataQualityTests))


if __name__ == "__main__":
    sys.exit(0 if run_tests().wasSuccessful() else 1)
