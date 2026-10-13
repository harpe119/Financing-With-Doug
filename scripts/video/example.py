"""Fictional, seeded examples calculated by Doug's actual forecast engine.

No personal database is opened. Raw paths are captured at the engine's
percentile boundary for animation, without modifying application code.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from finfore.forecast import ForecastResult, _behaviour_weights, run_forecast, scheduled_flows
from finfore.history import networth_history, weekly_category_matrix
from finfore.models import Account, AccountType, OneOffEvent, RecurringItem, Scenario
from finfore.recurrence import occurrences
from finfore.store import Store

START = date(2026, 1, 1)
DAYS = 365
PATHS = 1000
SEED = 7


@dataclass
class Example:
    baseline: ForecastResult
    scenario: ForecastResult
    cash_paths: np.ndarray
    networth_paths: np.ndarray
    weeks: pd.DataFrame
    squeeze: ForecastResult
    squeeze_paths: np.ndarray
    week_picks: np.ndarray
    fixed_cash: np.ndarray
    history: pd.DataFrame
    schedule: list


def _forecast(store: Store, scenario: Scenario | None = None):
    # The first two daily, two-dimensional percentile inputs are net worth
    # and cash. Record each once, delegating all calculations to NumPy.
    captured = []
    percentile = np.percentile
    generator = np.random.default_rng
    sampled_weeks = []

    class RecordingGenerator:
        def __init__(self, *args, **kwargs):
            self.rng = generator(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.rng, name)

        def choice(self, *args, **kwargs):
            value = self.rng.choice(*args, **kwargs)
            if np.shape(value) == (PATHS, int(np.ceil(DAYS / 7))):
                sampled_weeks.append(value.copy())
            return value

    def record(a, *args, **kwargs):
        if (np.shape(a) == (PATHS, DAYS + 1) and args and args[0] == 10
                and kwargs.get("axis") == 0 and len(captured) < 2):
            captured.append(np.array(a, copy=True))
        return percentile(a, *args, **kwargs)

    with patch("finfore.forecast.np.percentile", side_effect=record), \
            patch("finfore.forecast.np.random.default_rng", RecordingGenerator):
        result = run_forecast(store, DAYS, PATHS, scenario, seed=SEED,
                              as_of=START, essential_monthly=1800)
    if len(captured) != 2:
        raise RuntimeError("The forecast path capture needs updating.")
    networth, cash = captured
    for p, values in result.cash_pct.items():
        np.testing.assert_allclose(percentile(cash, p, axis=0), values)
    for p, values in result.networth_pct.items():
        np.testing.assert_allclose(percentile(networth, p, axis=0), values)
    if len(sampled_weeks) != 1:
        raise RuntimeError("The forecast's weekly sampling capture needs updating.")
    return result, cash, networth, sampled_weeks[0]


def build_example() -> Example:
    store = Store(":memory:")
    try:
        cash_id = store.add_account(Account("Example checking", AccountType.CHECKING,
                                            6000, START))
        store.add_account(Account("Example investment", AccountType.INVESTMENT,
                                  10000, START, annual_return=0.06,
                                  volatility=0.15, monthly_contribution=150))
        loan_id = store.add_account(Account("Example loan", AccountType.LOAN, 8000, START,
                                            apr=0.06, monthly_payment=300, payment_day=20))
        patterns = [
            RecurringItem("PAYDAY|+", "Payday", "semimonthly", 2200,
                          date(2025, 12, 31), [15, 28], kind="income"),
            RecurringItem("RENT|-", "Rent", "monthly", -1300,
                          date(2025, 12, 1), [1]),
            RecurringItem("BILLS|-", "Bills", "monthly", -350,
                          date(2025, 12, 10), [10]),
            RecurringItem("LOAN|-", "Loan", "monthly", -300,
                          date(2025, 12, 20), [20], kind="loan_payment",
                          linked_account_id=loan_id),
        ]
        store.merge_recurring(patterns)
        rng = np.random.default_rng(19)
        rows = []
        for w in range(52):
            when = START - timedelta(days=364) + timedelta(weeks=w)
            # A common factor retains the within-week relationship between
            # categories. Occasional large weeks remain in the sample.
            factor = float(rng.lognormal(-0.1, 0.45))
            costs = (round(155 * factor + rng.uniform(0, 35)),
                     round(70 * factor + rng.uniform(0, 70)),
                     round(120 * factor + rng.uniform(0, 160)
                           + (450 if w % 11 == 0 else 0)))
            for label, cost in zip(("Groceries", "Dining", "Misc"), costs):
                rows.append(dict(account_id=cash_id, date=when,
                                 description=f"Example {label} {w}",
                                 amount=-cost, category=label,
                                 import_hash=f"video-{w}-{label}"))
        # Include the corresponding regular flows in the fictional ledger so
        # the historical chart looks like an actual account, rather than a
        # spending-only export. Known patterns stay outside the bootstrap.
        for pattern in patterns:
            historical_pattern = replace(pattern, last_date=pattern.last_date.replace(year=2024))
            for when in occurrences(historical_pattern, date(2024, 12, 31), START - timedelta(days=1)):
                rows.append(dict(account_id=cash_id, date=when,
                                 description=pattern.name, amount=pattern.amount,
                                 category="Income" if pattern.kind == "income" else "Bills",
                                 import_hash=f"schedule-{pattern.key}-{when}"))
        for month in range(1, 13):
            rows.append(dict(account_id=cash_id, date=date(2025, month, 1),
                             description="Investment transfer", amount=-150,
                             category="Transfer", import_hash=f"investment-{month}"))
        store.add_transactions(pd.DataFrame(rows))
        baseline, cash, networth, picks = _forecast(store)
        scenario, _, _, _ = _forecast(store, Scenario(category_multipliers={"Dining": 0.75}))
        weeks, irregular = weekly_category_matrix(store.transactions(),
                                                  exclude_keys={it.key for it in patterns})
        net_week = irregular.values - weeks.values.sum(axis=1)
        mean_week = float(np.dot(_behaviour_weights(net_week, seed=SEED), net_week))
        fixed_cash = baseline.cash_det - np.arange(DAYS + 1) * mean_week / 7
        history = networth_history(store).loc[:pd.Timestamp(START)].tail(121)
        schedule = scheduled_flows(store, START + timedelta(days=1),
                                   START + timedelta(days=DAYS))
        known = np.zeros(DAYS + 1)
        for flow in schedule:
            known[(flow.when - START).days] += flow.amount
        np.testing.assert_allclose(fixed_cash, cash[0, 0] + np.cumsum(known), atol=1e-8)
        # A separate, explicitly labeled example: a known one-time expense
        # and a smaller opening balance can produce a dip before recovery.
        account = store.account(cash_id)
        account.balance = 2000
        store.update_account(account)
        squeeze, squeeze_paths, _, _ = _forecast(store, Scenario(events=[
            OneOffEvent("Example repair", START + timedelta(days=80), -3400)]))
        return Example(baseline, scenario, cash, networth, weeks,
                       squeeze, squeeze_paths, picks, fixed_cash, history, schedule)
    finally:
        store.close()


if __name__ == "__main__":
    example = build_example()
    print(f"Verified {PATHS:,} engine paths over {DAYS} days.")
    print("Opening cash:", example.cash_paths[0, 0])
    print("Ending cash P10/P50/P90:",
          [round(example.baseline.cash_pct[p][-1]) for p in (10, 50, 90)])
    print("Scenario median improvement:",
          round(example.scenario.cash_pct[50][-1] - example.baseline.cash_pct[50][-1]))
    print("Squeeze paths below zero:", example.squeeze.cash_breach["zero"])
