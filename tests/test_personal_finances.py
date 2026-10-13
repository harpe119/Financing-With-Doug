"""Independent budget arithmetic and reimbursable-business regressions."""
from __future__ import annotations

import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finfore.categorize import BUSINESS_CATEGORY, categorize, personal_transactions
from finfore.forecast import run_forecast, scheduled_flows
from finfore.forecast_history import ForecastHistory
from finfore.goals import surplus_monthly
from finfore.history import category_spending, networth_history, spending_frame, weekly_category_matrix
from finfore.insights import bucket_of, category_monthly, monthly_aggregates, monthly_breakdown, spending_score
from finfore.learning import (LearnedRules, NearestMerchant, record_corrections,
                             training_frame, uncategorized_clusters, label_cluster)
from finfore.models import Account, AccountType
from finfore.recurrence import detect, personal_recurring
from finfore.store import Store


class FinanceDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 12)


class PersonalFinanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "personal.db")
        self.aid = self.store.add_account(Account("Checking", AccountType.CHECKING,
            balance=6000, balance_date=date(2026, 10, 12)))
        self.sequence = 0
        self.clock = patch("finfore.insights.date", FinanceDate)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.store.close()
        self.temp.cleanup()

    def add(self, when, amount, category, description="Shop"):
        self.sequence += 1
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.aid,
            date=when, description=description, amount=amount, category=category,
            import_hash=f"personal-{self.sequence}")]))
        return int(self.store.con.execute("SELECT last_insert_rowid()").fetchone()[0])

    def personal_history(self):
        for ts in pd.date_range("2026-01-01", "2026-10-12"):
            d = ts.date()
            self.add(d, -10, "Groceries", "Daily shop")
            if d.day == 15:
                self.add(d, 3000, "Income", "Acme payroll")
            if d.day == 1:
                self.add(d, -900, "Housing", "Rent")
        self.store.merge_recurring(detect(self.store.transactions()))

    def test_monthly_breakdown_reconciles_with_independent_ledger_weights(self):
        for month in (8, 9):
            self.add(date(2026, month, 1), 8000, "Income", "Paycheck including reimbursement")
            paycheck = int(self.store.transactions().iloc[-1]["id"])
            self.store.set_business_reimbursement(paycheck, 1000)
            for category, cost in (("Housing", 2000), ("Groceries", 900),
                                   ("Utilities", 400), ("Insurance", 500),
                                   ("Fuel", 300), ("Transport", 200),
                                   ("Debt payment", 500), ("Health & fitness", 500),
                                   ("Dining", 200), ("Charitable donation", 100),
                                   ("Misc", 50), (BUSINESS_CATEGORY, 1000), ("Transfer", 2000)):
                self.add(date(2026, month, 2), -cost, category)
            self.add(date(2026, month, 3), 50, "Dining", "Refund")
            self.add(date(2026, month, 4), 9999, "Misc", "Unclassified inflow")
            self.add(date(2026, month, 5), -500, "Income", "Negative income")
        self.add(date(2026, 10, 1), -99999, "Housing", "Partial month")
        self.add(date(2026, 11, 1), -99999, "Housing", "Future month")
        original = self.store.transactions()
        evidence = monthly_breakdown(original)
        self.assertEqual(evidence.aggregates, monthly_aggregates(original))
        weight = 2 ** (-1/3)
        self.assertAlmostEqual(evidence.month_shares[pd.Period("2026-08")], weight/(1+weight))
        self.assertAlmostEqual(evidence.month_shares[pd.Period("2026-09")], 1/(1+weight))
        for group, expected in (("income", 7000), ("essential", 5300), ("frivolous", 150),
                                ("giving", 100), ("unknown", 50)):
            rows = evidence.groups[group]
            self.assertAlmostEqual(rows.contribution.sum(), expected)
            self.assertAlmostEqual(getattr(evidence.aggregates, group), expected)
            for _, row in rows.iterrows():
                sign = 1 if group == "income" else -1
                share = (weight if row.date.month == 8 else 1)/(1+weight)
                self.assertAlmostEqual(row.contribution, sign*row.amount*share)
        income = evidence.groups["income"]
        self.assertTrue((income.ledger_amount == 8000).all())
        self.assertTrue((income.reimbursement_excluded == 1000).all())
        self.assertTrue((income.amount == 7000).all())
        pd.testing.assert_frame_equal(original, self.store.transactions())

    def test_breakdown_preserves_refunds_and_common_month_denominator(self):
        self.add(date(2026, 8, 1), 2000, "Income")
        self.add(date(2026, 9, 1), 2000, "Income")
        self.add(date(2026, 9, 2), -100, "Groceries")
        self.add(date(2026, 9, 3), 500, "Housing", "Refund exceeding spending")
        details = monthly_breakdown(self.store.transactions())
        weight = 2**(-1/3)
        rows = details.groups["essential"]
        self.assertAlmostEqual(rows.contribution.sum(), -400/(1+weight))
        self.assertEqual(details.aggregates.essential, 0)
        # Do not floor individual categories: a credit in Housing offsets Groceries.
        groceries = rows[rows.category == "Groceries"].contribution.sum()
        self.assertAlmostEqual(groceries, 100/(1+weight))
        newest = monthly_breakdown(self.store.transactions(), window=1, half_life=6)
        self.assertEqual(newest.aggregates.half_life, 6)
        self.assertAlmostEqual(newest.groups["essential"].contribution.sum(), -400)
        self.assertIsNone(monthly_breakdown(pd.DataFrame()))

    def test_spare_monthly_matches_independent_weighted_arithmetic(self):
        # August weighs 2**(-1/3), September weighs 1. Current October and
        # future November must not enter. Include a real categorized refund,
        # loan minimum, charitable gift, unknown expense and internal move.
        for month, salary, rent, groceries, dining in (
                (8, 3000, 1000, 400, 300), (9, 4000, 1100, 500, 200)):
            d = date(2026, month, 15)
            for value, cat in ((salary, "Income"), (-rent, "Housing"),
                    (-groceries, "Groceries"), (-dining, "Dining"),
                    (50, "Groceries"), (-200, "Debt payment"),
                    (-100, "Charitable donation"), (-75, "Misc"),
                    (-1000, "Transfer"), (900, BUSINESS_CATEGORY),
                    (-900, BUSINESS_CATEGORY)):
                self.add(d, value, cat)
        self.add(date(2026, 10, 10), 99999, "Income")
        self.add(date(2026, 11, 1), 99999, "Income")
        weight = 2 ** (-1 / 3)
        expected_income = (3000 * weight + 4000) / (weight + 1)
        expected_spending = ((1000 + 400 + 300 - 50 + 200 + 100 + 75) * weight
                            + 1100 + 500 + 200 - 50 + 200 + 100 + 75) / (weight + 1)
        agg = monthly_aggregates(self.store.transactions())
        self.assertAlmostEqual(agg.income, expected_income)
        self.assertAlmostEqual(agg.spending, expected_spending)
        self.assertAlmostEqual(surplus_monthly(self.store), expected_income - expected_spending)
        self.assertEqual(agg.n_months, 2)

    def test_deficit_and_missing_history_do_not_allocate_spare_money(self):
        self.assertEqual(surplus_monthly(self.store), 0)
        self.add(date(2026, 9, 15), 100, "Income")
        self.add(date(2026, 9, 15), -500, "Housing")
        self.assertEqual(surplus_monthly(self.store), 0)

    def test_failure_to_read_budget_is_not_reported_as_zero_spare(self):
        with patch.object(self.store, "transactions", side_effect=RuntimeError("Read failed")):
            with self.assertRaisesRegex(RuntimeError, "Read failed"):
                surplus_monthly(self.store)

    def test_business_expense_and_repayment_leave_personal_metrics_unchanged(self):
        self.personal_history()
        tx = self.store.transactions()
        baseline = monthly_aggregates(tx)
        score = spending_score(baseline).value
        categories = category_monthly(tx)
        for month in range(1, 10):
            self.add(date(2026, month, 20), -1800, BUSINESS_CATEGORY, "United airline")
            self.add(date(2026, month, 28), 1800, BUSINESS_CATEGORY, "Expense repayment")
        actual = monthly_aggregates(self.store.transactions())
        self.assertEqual(actual, baseline)
        self.assertEqual(spending_score(actual).value, score)
        self.assertEqual(category_monthly(self.store.transactions()), categories)
        self.assertEqual(bucket_of(BUSINESS_CATEGORY), "excluded")
        self.assertNotIn(BUSINESS_CATEGORY, category_spending(self.store.transactions()).index)

    def test_business_only_does_not_create_a_personal_score(self):
        for month in (7, 8, 9):
            self.add(date(2026, month, 15), -2000, BUSINESS_CATEGORY)
        self.assertIsNone(monthly_aggregates(self.store.transactions()))
        self.assertEqual(category_monthly(self.store.transactions()), {})
        self.assertEqual(surplus_monthly(self.store), 0)

    def test_weekly_samples_exclude_both_sides_of_business_trip(self):
        self.personal_history()
        tx = self.store.transactions()
        keys = {it.key for it in self.store.recurring()}
        W, inc = weekly_category_matrix(tx, keys)
        for month in range(1, 10):
            self.add(date(2026, month, 20), -2000, BUSINESS_CATEGORY, "Trip hotel")
            self.add(date(2026, month, 28), 2000, BUSINESS_CATEGORY, "Trip repayment")
        actual_W, actual_inc = weekly_category_matrix(self.store.transactions(), keys)
        pd.testing.assert_frame_equal(actual_W, W)
        pd.testing.assert_series_equal(actual_inc, inc)

    def test_forecast_is_unchanged_by_completed_business_trips(self):
        self.personal_history()
        baseline = run_forecast(self.store, 180, 100, as_of=date(2026, 10, 12))
        for month in range(1, 10):
            self.add(date(2026, month, 20), -1200, BUSINESS_CATEGORY, "Trip flight")
            self.add(date(2026, month, 28), 1200, BUSINESS_CATEGORY, "Trip repayment")
        actual = run_forecast(self.store, 180, 100, as_of=date(2026, 10, 12))
        np.testing.assert_array_equal(actual.cash_det, baseline.cash_det)
        for p in baseline.cash_pct:
            np.testing.assert_array_equal(actual.cash_pct[p], baseline.cash_pct[p])

    def test_business_label_removes_previously_detected_schedule(self):
        self.personal_history()
        for month in range(1, 10):
            self.add(date(2026, month, 20), -1200, "Travel", "United airline")
        self.store.merge_recurring(detect(self.store.transactions()))
        tx = self.store.transactions()
        trips = tx[tx["description"] == "United airline"]
        self.assertTrue(any(it.key == "UNITED AIRLINE|-" and it.active for it in self.store.recurring()))
        self.store.set_category(trips["id"].tolist(), BUSINESS_CATEGORY)
        corrected = personal_recurring(self.store.transactions(), self.store.recurring())
        self.assertFalse(next(it for it in corrected if it.key == "UNITED AIRLINE|-").active)
        self.assertFalse(any(it.key == "UNITED AIRLINE|-" for it in detect(self.store.transactions())))
        self.assertFalse(any(f.label == "United Airline" for f in scheduled_flows(
            self.store, date(2026, 10, 12), date(2026, 12, 31))))
        normal = run_forecast(self.store, 90, 40, as_of=date(2026, 10, 12))
        refit = run_forecast(self.store, 90, 40, as_of=date(2026, 10, 12),
                            history=ForecastHistory("weeks", 26, refit_recurring=True))
        self.assertEqual(normal.monthly_recurring_net, refit.monthly_recurring_net)

    def test_paycheck_reimbursement_is_excluded_without_altering_deposit(self):
        for month in range(1, 10):
            self.add(date(2026, month, 15), 3500, "Income", "Acme payroll")
        self.store.merge_recurring(detect(self.store.transactions()))
        raw = self.store.transactions()
        for tx_id in raw["id"]:
            self.store.set_business_reimbursement(int(tx_id), 500)
        after = self.store.transactions()
        np.testing.assert_array_equal(after["amount"], raw["amount"])
        self.assertAlmostEqual(monthly_aggregates(after).income, 3000)
        personal = personal_transactions(after)
        self.assertTrue(personal["amount"].eq(3000).all())
        pd.testing.assert_frame_equal(personal_transactions(personal), personal)
        self.assertEqual(detect(after)[0].amount, 3000)
        fitted = personal_recurring(after, self.store.recurring())
        self.assertEqual(fitted[0].amount, 3000)
        self.assertEqual(self.store.recurring()[0].amount, 3500)
        res = run_forecast(self.store, 90, 20, as_of=date(2026, 10, 12))
        self.assertEqual(res.monthly_recurring_net, 3000)
        with patch("finfore.forecast.date", FinanceDate):
            flows = scheduled_flows(self.store, date(2026, 10, 12), date(2026, 11, 30))
        self.assertTrue(flows)
        self.assertTrue(all(f.amount == 3000 for f in flows))

    def test_paycheck_markers_persist_reimport_and_can_be_undone(self):
        self.add(date(2026, 9, 15), 3500, "Income", "Acme payroll")
        tx_id = int(self.store.transactions().iloc[0]["id"])
        undo = self.store.enable_undo()
        self.store.set_business_reimbursement(tx_id, 500)
        undo.undo()
        self.assertEqual(self.store.transactions().iloc[0]["business_reimbursement"], 0)
        undo.redo()
        self.assertEqual(self.store.transactions().iloc[0]["business_reimbursement"], 500)
        self.store.add_transactions(pd.DataFrame([dict(account_id=self.aid,
            date=date(2026, 9, 15), description="Acme payroll", amount=3500,
            category="Income", import_hash="personal-1")]))
        self.assertEqual(len(self.store.transactions()), 1)
        path = self.store.path
        self.store.close()
        self.store = Store(path)
        self.assertEqual(self.store.transactions().iloc[0]["business_reimbursement"], 500)
        self.store.set_business_reimbursement(tx_id, 0)
        self.assertEqual(self.store.transactions().iloc[0]["amount"], 3500)
        self.assertEqual(self.store.transactions().iloc[0]["business_reimbursement"], 0)

    def test_invalid_reimbursement_cannot_change_the_ledger(self):
        self.add(date(2026, 9, 15), 3500, "Income")
        tx_id = int(self.store.transactions().iloc[0]["id"])
        for value in (-1, 3501, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.store.set_business_reimbursement(tx_id, value)
        self.assertEqual(self.store.transactions().iloc[0]["amount"], 3500)
        self.store.set_category([tx_id], BUSINESS_CATEGORY)
        with self.assertRaises(ValueError):
            self.store.set_business_reimbursement(tx_id, 500)

    def test_fully_reimbursed_deposit_has_no_personal_income_or_score(self):
        tx_id = self.add(date(2026, 9, 15), 500, "Other income")
        self.store.set_business_reimbursement(tx_id, 500)
        self.assertTrue(personal_transactions(self.store.transactions()).empty)
        self.assertIsNone(monthly_aggregates(self.store.transactions()))
        self.assertEqual(self.store.transactions().iloc[0]["amount"], 500)

    def test_reimbursement_metadata_migrates_and_cascades_with_transaction(self):
        # Old databases lack this table; opening adds it without rewriting rows.
        self.store.con.execute("DROP TABLE business_reimbursements")
        self.store.con.commit()
        path = self.store.path
        self.store.close()
        self.store = Store(path)
        self.add(date(2026, 9, 15), 3500, "Income")
        tx_id = int(self.store.transactions().iloc[0]["id"])
        self.store.set_business_reimbursement(tx_id, 500)
        backup = self.store.backup_to(Path(self.temp.name) / "backup.db")
        with closing(sqlite3.connect(backup)) as con:
            self.assertEqual(con.execute("SELECT amount FROM business_reimbursements").fetchone()[0], 500)
        self.store.delete_account(self.aid)
        self.assertEqual(self.store.con.execute("SELECT count(*) FROM business_reimbursements").fetchone()[0], 0)

    def test_business_is_not_learned_as_a_merchant_wide_category(self):
        self.add(date(2026, 9, 15), -1000, "Travel", "United airline")
        self.assertEqual(record_corrections(self.store, self.store.transactions(), BUSINESS_CATEGORY), 0)
        self.assertEqual(LearnedRules.from_store(self.store).predict("United airline", -1000), None)
        self.assertEqual(categorize("United airline", -1000), "Travel")
        self.store.add_category_feedback([dict(norm_key="UNITED AIRLINE|-",
            description="United airline", amount=-1000, new_category=BUSINESS_CATEGORY)])
        self.assertIsNone(LearnedRules.from_store(self.store).predict("United airline", -1000))
        self.assertIsNone(NearestMerchant.from_store(self.store).predict("United airline", -1000))
        self.assertTrue(training_frame(self.store).empty)

    def test_business_review_queue_label_does_not_teach_future_imports(self):
        self.add(date(2026, 9, 15), -1000, "Misc", "Trip cost")
        clusters = uncategorized_clusters(self.store, months=None)
        self.assertEqual(label_cluster(self.store, clusters[0], BUSINESS_CATEGORY), 1)
        self.assertTrue(self.store.category_feedback().empty)
        self.assertEqual(self.store.transactions().iloc[0]["category"], BUSINESS_CATEGORY)

    def test_real_cash_history_still_contains_unreimbursed_business_outflow(self):
        account = self.store.account(self.aid)
        account.balance_date = date(2026, 1, 1)
        self.store.update_account(account)
        self.add(date(2026, 9, 15), -1200, BUSINESS_CATEGORY, "Work flight")
        self.assertEqual(networth_history(self.store)["cash"].iloc[-1], 4800)
        self.assertTrue(spending_frame(self.store.transactions()).empty)
        res = run_forecast(self.store, 90, 20, as_of=date(2026, 10, 12))
        self.assertTrue(np.all(res.cash_det == 4800))


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PersonalFinanceTests))


if __name__ == "__main__":
    sys.exit(0 if run_tests().wasSuccessful() else 1)
