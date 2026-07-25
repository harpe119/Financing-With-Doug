"""One-click demo: seed accounts, import the bundled sample CSVs, categorize,
pair transfers, detect recurring patterns, link the loan, add goals."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .models import Account, AccountType, Goal
from .pipeline import import_csv, refresh_derived
from .store import Store
from .util import resource_path

SAMPLE_DIR = resource_path("sample_data")


def load_demo(store: Store) -> dict:
    """Populate a store with the demo dataset."""
    today = date.today()
    meta = {}
    meta_path = SAMPLE_DIR / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    checking = Account("Everyday Checking", AccountType.CHECKING,
                       balance=meta.get("checking_end", 4850.00), balance_date=today)
    savings = Account("High-Yield Savings", AccountType.SAVINGS,
                      balance=meta.get("savings_end", 12400.00), balance_date=today)
    loan = Account("Capital Auto Loan", AccountType.LOAN, balance=14250.00,
                   balance_date=today, apr=0.064, monthly_payment=412.33,
                   payment_day=15)
    broker = Account("Brokerage", AccountType.INVESTMENT, balance=18300.00,
                     balance_date=today, annual_return=0.07, volatility=0.15,
                     monthly_contribution=400.0)
    for a in (checking, savings, loan, broker):
        store.add_account(a)

    # historical snapshots so loan/investment history is non-flat
    for k, (lb, bb) in enumerate([(17650, 13900), (16820, 14800), (15980, 15600),
                                  (15120, 16900), (14690, 17550)]):
        d = today - timedelta(days=(5 - k) * 90)
        store.add_snapshot(loan.id, d, lb)
        store.add_snapshot(broker.id, d, bb)

    added_c, _ = import_csv(store, str(SAMPLE_DIR / "checking.csv"), checking.id)
    added_s, _ = import_csv(store, str(SAMPLE_DIR / "savings.csv"), savings.id)
    n_rec = refresh_derived(store)

    store.add_goal(Goal("Emergency fund", 25000, "cash"))
    store.add_goal(Goal("House down payment", 60000, "cash"))
    store.add_goal(Goal("Net worth: $100k", 100000, "networth"))
    return {"transactions": added_c + added_s, "recurring": n_rec}
