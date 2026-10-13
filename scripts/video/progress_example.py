"""A saved forecast, later ledger, and statistics from the actual Progress code.

The in-memory ledger is entirely fictional. The early checkpoint is captured
before the later transactions are inserted; nothing refits its saved forecast.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from finfore.history import networth_history
from finfore.models import Account, AccountType, RecurringItem
from finfore.progress import (capture, check_path, significance_since, weekly_discretionary,
                             compare_periods, category_shifts, score_move,
                             N_PERMUTATIONS, N_BOOTSTRAP)
from finfore.recurrence import normalize_desc
from finfore.store import Store

OLD = date(2026, 4, 6)
NOW = date(2026, 6, 29)


@contextmanager
def clock(when):
    class ExampleDate(date):
        @classmethod
        def today(cls):
            return cls(when.year, when.month, when.day)
    with ExitStack() as stack:
        for module in ("progress", "insights", "history", "forecast"):
            stack.enter_context(patch(f"finfore.{module}.date", ExampleDate))
        yield


@dataclass
class ProgressExample:
    old: object
    now: object
    actual: pd.Series
    check: object
    significance: object
    before: np.ndarray
    after: np.ndarray
    permutations: np.ndarray
    bootstraps: np.ndarray
    resamples: list
    shifts: list
    move: object
    insufficient: object
    unchanged: object


def build_progress_example():
    store = Store(":memory:")
    try:
        aid = store.add_account(Account("Example checking", AccountType.CHECKING, 6000, OLD))
        patterns = [
            RecurringItem(f"{normalize_desc('Example payroll')}|+", "Example payroll", "semimonthly", 2500,
                          date(2026, 3, 28), [15, 28], kind="income"),
            RecurringItem(f"{normalize_desc('Example rent')}|-", "Example rent", "monthly", -2000,
                          date(2026, 4, 1), [1]),
        ]
        store.merge_recurring(patterns)
        before_costs = [190,310,230,450,170,280,360,210,530,260,200,340]
        after_costs = [130,150,100,180,120,160,90,140,170,110,130,150]
        rows = []
        first = OLD - timedelta(weeks=12)
        for ts in pd.date_range(first, NOW):
            day = ts.date()
            week = (day-first).days//7
            cost = (before_costs+after_costs)[min(week,23)]
            def add(description, amount, category):
                rows.append(dict(account_id=aid, date=day, description=description,
                                 amount=amount, category=category, import_hash=f"progress-{day}-{description}"))
            add("Example groceries", -(22 + (day.day % 5)), "Groceries")
            if day.weekday() == 1:
                add(f"Restaurant {chr(65+week)}", -cost, "Dining")
            if day.day in (15,28):
                add("Example payroll", 2500, "Income")
            if day.day == 1:
                add("Example rent", -2000, "Housing")
        ledger = pd.DataFrame(rows)
        store.add_transactions(ledger[ledger.date <= OLD])
        with clock(OLD):
            old = capture(store, "Before spending change", horizon_days=84, n_paths=1000)
        assert old.has_forecast, "Example checkpoint failed to store its forecast"
        saved = old.forecast_frame().copy()
        store.add_transactions(ledger[ledger.date > OLD])
        with clock(NOW):
            history = networth_history(store)
            actual = history["cash"].reindex(saved.index)
            # The updated balance is the same ledger-derived amount used by the
            # path check, rather than leaving the account anchored in April.
            account = store.accounts()[0]
            account.balance = float(actual.iloc[-1])
            account.balance_date = NOW
            store.update_account(account)
            now = capture(store, "Latest import", with_forecast=False)
            check = check_path(store, old)
            weekly = weekly_discretionary(store)
            before = weekly[weekly.index < pd.Timestamp(OLD)].to_numpy(dtype=float)
            after = weekly[weekly.index >= pd.Timestamp(OLD)].to_numpy(dtype=float)
            sig = significance_since(store, old)
            shifts = category_shifts(store, old)
        pd.testing.assert_frame_equal(old.forecast_frame(), saved)
        np.testing.assert_allclose(check.delta, actual.iloc[-1] - saved.p50.iloc[-1])
        np.testing.assert_allclose(check.inside_80, ((actual >= saved.p10) & (actual <= saved.p90)).mean())
        np.testing.assert_allclose(sig.diff, after.mean() - before.mean())
        # Reproduce the seeded test's draws for the animation. The statistics
        # displayed still come from significance_since(), not this trace.
        rng = np.random.default_rng(11)
        combined = np.r_[before, after]
        n = len(before)
        permutations = np.array([rng.permutation(combined) for _ in range(N_PERMUTATIONS)])
        diffs = permutations[:, n:].mean(axis=1) - permutations[:, :n].mean(axis=1)
        boot_values = []
        resamples = []
        for _ in range(N_BOOTSTRAP):
            b = rng.choice(before, n, replace=True)
            a = rng.choice(after, len(after), replace=True)
            boot_values.append(a.mean()-b.mean())
            if len(resamples) < 2:
                resamples.append((b.copy(), a.copy()))
        boots = np.array(boot_values)
        np.testing.assert_allclose(sig.p_value, (abs(diffs) >= abs(sig.diff)-1e-12).mean())
        np.testing.assert_allclose([sig.ci_low, sig.ci_high], np.percentile(boots, [2.5,97.5]))
        unchanged = compare_periods(before, before[::-1])
        return ProgressExample(old, now, actual, check, sig, before, after, diffs, boots, resamples,
                               shifts, score_move(old,now), compare_periods(before,after[:3]), unchanged)
    finally:
        store.close()


if __name__ == "__main__":
    e = build_progress_example()
    print({"weeks": (len(e.before),len(e.after)), "weekly_change": e.significance.diff,
           "interval": (e.significance.ci_low,e.significance.ci_high),
           "p": e.significance.p_value, "cash_gap": e.check.delta,
           "coverage": e.check.inside_80, "scores": (e.old.score,e.now.score)})
