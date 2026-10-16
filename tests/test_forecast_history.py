"""History cut-offs, schedule refits and backtest lookahead regressions."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finfore.forecast import backtest_cash, run_forecast, scheduled_flows, sensitivity
from finfore.forecast_history import ForecastHistory, SETTING
from finfore.history import weekly_category_matrix
from finfore.models import Account, AccountType, Scenario
from finfore.recurrence import detect
from finfore.store import Store


class ForecastDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 12)


class ForecastHistoryTests(unittest.TestCase):
    today = date(2026, 10, 12)  # Monday, after a complete Sunday-ended week
    changed = date(2026, 7, 13)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.db"
        self.store = Store(self.path)
        aid = self.store.add_account(Account("Checking", AccountType.CHECKING,
            balance=6000, balance_date=self.today))
        rows = []
        # Daily coverage gives unambiguous full-week boundaries. Spending is
        # much lower after the move. Same-key salary changes, rent changes key.
        for ts in pd.date_range("2024-10-14", self.today):
            d = ts.date()
            rows.append(dict(date=d, description="Daily shop", amount=-10 if d >= self.changed else -100,
                             category="Groceries"))
            if d.day == 15:
                rows.append(dict(date=d, description="Acme payroll",
                    amount=3000 if d >= self.changed else 1800, category="Salary"))
            if d.day == 1:
                recent = d >= self.changed
                rows.append(dict(date=d, description="New landlord" if recent else "Old landlord",
                    amount=-900 if recent else -600, category="Rent"))
        for i, row in enumerate(rows):
            row.update(account_id=aid, import_hash=f"history-{i}")
        self.store.add_transactions(pd.DataFrame(rows))
        # Do not treat the daily shop as scheduled; only salary and rent.
        self.store.merge_recurring([it for it in detect(self.store.transactions())
                                   if it.key != "DAILY SHOP|-"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save(self, history):
        self.store.set_setting(SETTING, history.to_setting())

    def forecast(self, history=None, as_of=None):
        return run_forecast(self.store, 90, 100, as_of=as_of or self.today, history=history)

    def test_default_is_identical_to_explicit_legacy_selection(self):
        default = self.forecast()
        legacy = self.forecast(ForecastHistory())
        self.assertEqual(default.history_weeks, 78)
        for p in default.cash_pct:
            np.testing.assert_array_equal(default.cash_pct[p], legacy.cash_pct[p])

    def test_slider_monthly_references_use_selected_history_and_schedules(self):
        from finfore.categorize import categorize
        policy = ForecastHistory("since", since=self.changed, refit_recurring=True)
        res = self.forecast(policy)
        # Complete weeks contain $10/day of groceries; scheduled rent and
        # salary are counted once, outside those resampled weeks.
        self.assertAlmostEqual(res.monthly_income_reference, 3000)
        self.assertAlmostEqual(res.monthly_spending_reference["Groceries"], 10 * 30.44)
        self.assertAlmostEqual(res.monthly_spending_reference[categorize("New landlord", -900)], 900)
        changed = run_forecast(self.store, 90, 100, as_of=self.today, history=policy,
            scenario=Scenario(income_multiplier=1.2, category_multipliers={"Groceries": .8}))
        self.assertAlmostEqual(changed.monthly_income_reference, 3000)
        self.assertAlmostEqual(changed.monthly_spending_reference["Groceries"], 10 * 30.44)
        # The additional cash is independently explained by scaled salary
        # occurrences and daily variable-spending savings.
        days = res.dates[1:]
        paydays = sum(d.day == 15 for d in days)
        expected = 600 * paydays + 2 * len(days)
        self.assertAlmostEqual(changed.cash_det[-1] - res.cash_det[-1], expected)

    def test_short_history_dollar_reference_omits_variable_spending(self):
        policy = ForecastHistory("since", since=self.today - timedelta(days=12))
        res = self.forecast(policy)
        self.assertLess(res.history_weeks, 4)
        self.assertNotIn("Groceries", res.monthly_spending_reference)
        self.assertTrue(any("not modeled" in warning for warning in res.warnings))

    def test_recent_period_excludes_expensive_old_weeks(self):
        res = self.forecast(ForecastHistory("weeks", 13))
        self.assertEqual(res.history_weeks, 13)
        self.assertEqual(res.history_first, self.changed)
        self.assertEqual(res.history_last, self.today - timedelta(days=1))
        # Only variable spend differs; the stored schedules are retained.
        old = self.forecast()
        self.assertEqual(res.monthly_recurring_net, old.monthly_recurring_net)
        self.assertGreater(res.cash_det[-1], old.cash_det[-1] + 1000)

    def test_since_cutoff_excludes_partial_week(self):
        res = self.forecast(ForecastHistory("since", since=date(2026, 7, 15)))
        self.assertEqual(res.history_first, date(2026, 7, 20))
        self.assertEqual(res.history_weeks, 12)

    def test_all_history_removes_legacy_sample_cap(self):
        self.assertEqual(self.forecast(ForecastHistory("all")).history_weeks, 104)

    def test_recurring_refit_uses_new_salary_and_drops_old_rent(self):
        policy = ForecastHistory("since", since=self.changed, refit_recurring=True)
        saved = self.store.recurring()
        fitted, warnings = policy.patterns(self.store.transactions(), saved, self.today)
        by_key = {it.key: it for it in fitted}
        self.assertEqual(by_key["ACME PAYROLL|+"].amount, 3000)
        self.assertEqual(by_key["NEW LANDLORD|-"].amount, -900)
        self.assertNotIn("OLD LANDLORD|-", by_key)
        self.assertTrue(any("omitted" in w for w in warnings))
        self.assertEqual(self.forecast(policy).monthly_recurring_net, 2100)
        self.assertEqual(self.store.recurring(), saved)

    def test_refit_keeps_disabled_ignore_and_loan_links(self):
        payroll = next(it for it in self.store.recurring() if it.kind == "income")
        rent = next(it for it in self.store.recurring() if it.key == "NEW LANDLORD|-")
        self.store.update_recurring(payroll.id, active=False)
        self.store.update_recurring(rent.id, kind="ignore", linked_account_id=1)
        fitted, _ = ForecastHistory("weeks", 13, refit_recurring=True).patterns(
            self.store.transactions(), self.store.recurring(), self.today)
        by_key = {it.key: it for it in fitted}
        self.assertFalse(by_key[payroll.key].active)
        self.assertEqual(by_key[rent.key].kind, "ignore")
        self.assertEqual(by_key[rent.key].linked_account_id, 1)
        res = self.forecast(ForecastHistory("weeks", 13, refit_recurring=True))
        self.assertEqual(res.monthly_recurring_net, 0)
        self.assertAlmostEqual(res.cash_det[-1] - res.cash_det[0], -900)

    def test_missing_known_patterns_never_enter_random_week_sample(self):
        # A two-week selection cannot redetect monthly salary/rent. Capture
        # the bootstrap's excluded keys rather than infer it from a wide fan.
        with patch("finfore.forecast.weekly_category_matrix", wraps=weekly_category_matrix) as matrix:
            res = self.forecast(ForecastHistory("since", since=date(2026, 10, 1), refit_recurring=True))
        self.assertIn("ACME PAYROLL|+", matrix.call_args.kwargs["exclude_keys"])
        self.assertIn("OLD LANDLORD|-", matrix.call_args.kwargs["exclude_keys"])
        self.assertTrue(any("Under 4" in w for w in res.warnings))

    def test_zero_weeks_and_partial_edges_have_correct_frequency(self):
        tx = pd.DataFrame([
            {"date": pd.Timestamp(d), "description": "Shop", "amount": -a, "category": "Groceries"}
            for d, a in (("2026-08-04", 999), ("2026-08-10", 70),
                         ("2026-08-30", 140), ("2026-09-01", 999))])
        W, inc = weekly_category_matrix(tx, set(), max_weeks=None,
            start_date=date(2026, 8, 4), end_date=date(2026, 9, 1))
        self.assertEqual(W["Groceries"].tolist(), [70, 0, 140])
        self.assertEqual(inc.tolist(), [0, 0, 0])

    def test_window_never_extends_past_an_asof_date(self):
        before_move = date(2026, 6, 29)
        res = self.forecast(ForecastHistory("weeks", 13, refit_recurring=True), as_of=before_move)
        self.assertLessEqual(res.history_last, before_move)
        self.assertGreaterEqual(res.history_first, before_move - timedelta(weeks=13))
        self.assertEqual(res.monthly_recurring_net, 1200)

    def test_cutoff_after_asof_returns_no_sample_instead_of_old_data(self):
        res = self.forecast(ForecastHistory("since", since=self.changed), as_of=date(2026, 6, 29))
        self.assertEqual(res.history_weeks, 0)
        self.assertTrue(any("Under 4" in w for w in res.warnings))

    def test_saved_setting_drives_forecast_and_attribution(self):
        policy = ForecastHistory("since", since=self.changed, refit_recurring=True)
        self.save(policy)
        self.assertEqual(self.forecast().monthly_recurring_net, 2100)
        with patch("finfore.forecast.date") as today:
            today.today.return_value = self.today
            flows = scheduled_flows(self.store, self.today, date(2026, 11, 12))
        self.assertFalse(any(f.label == "Old Landlord" for f in flows))
        self.assertTrue(any(f.amount == 3000 for f in flows))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(ForecastHistory.from_setting(self.store.get_setting(SETTING)), policy)

    def test_sensitivity_keeps_the_selected_history(self):
        self.save(ForecastHistory("weeks", 13, refit_recurring=True))
        with patch("finfore.forecast.date", ForecastDate), \
             patch("finfore.forecast.weekly_category_matrix", wraps=weekly_category_matrix) as matrix:
            sensitivity(self.store, Scenario(), 60, ["Groceries"])
        # Each lever reuses the same selected empirical weeks.
        self.assertEqual(matrix.call_count, 1)
        for call in matrix.call_args_list:
            self.assertEqual(call.kwargs["start_date"], self.changed)
            self.assertEqual(call.kwargs["end_date"], self.today)

    def test_backtest_rolling_window_is_relative_to_the_refit_date(self):
        self.save(ForecastHistory("weeks", 13))
        with patch("finfore.forecast.date", ForecastDate), \
             patch("finfore.forecast.weekly_category_matrix", wraps=weekly_category_matrix) as matrix:
            bt = backtest_cash(self.store, lookback_days=365, n_paths=20)
        self.assertIsNotNone(bt)
        self.assertEqual(matrix.call_args.kwargs["end_date"], bt.as_of)
        self.assertEqual(matrix.call_args.kwargs["start_date"], bt.as_of - timedelta(weeks=13))
        self.assertLess(matrix.call_args.args[0]["date"].max().date(), self.changed)

    def test_known_patterns_only_leave_complete_zero_spend_weeks(self):
        tx = self.store.transactions()
        excluded = {f"{name}|{sign}" for name, sign in
                    (("DAILY SHOP", "-"), ("ACME PAYROLL", "+"),
                     ("OLD LANDLORD", "-"), ("NEW LANDLORD", "-"))}
        W, inc = weekly_category_matrix(tx, excluded, max_weeks=None,
            start_date=self.changed, end_date=self.today)
        self.assertEqual(len(W), 13)
        self.assertEqual(float(W.sum().sum()), 0)
        self.assertEqual(float(inc.sum()), 0)

    def test_backtest_since_date_after_refit_date_is_unavailable(self):
        self.save(ForecastHistory("since", since=self.changed))
        self.assertIsNone(backtest_cash(self.store, lookback_days=365, n_paths=20))

    def test_invalid_saved_selection_returns_safe_default(self):
        for value in (None, "broken", {"mode": "unknown"}, {"mode": "since", "since": "bad"},
                      {"mode": "weeks", "weeks": -2}, {"mode": "since"}):
            self.assertEqual(ForecastHistory.from_setting(value), ForecastHistory())


def run_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ForecastHistoryTests)
    return unittest.TextTestRunner(verbosity=1).run(suite)


if __name__ == "__main__":
    sys.exit(0 if run_tests().wasSuccessful() else 1)
