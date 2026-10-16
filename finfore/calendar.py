"""Upcoming scheduled items and account attribution from the imported ledger.

These totals exclude variable spending and unassigned items. They are a
schedule of known commitments, rather than another probabilistic forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .forecast import scheduled_flows
from .categorize import PERSONAL_EXCLUSIONS
from .history import cash_anchor, cash_history
from .recurrence import normalize_desc
from .store import Store
from .cache import cached_read


@dataclass(frozen=True)
class CalendarEntry:
    when: date
    label: str
    amount: float
    source: str
    account_id: int | None
    balance_after: float | None


@dataclass(frozen=True)
class Schedule:
    entries: tuple[CalendarEntry, ...]
    opening_balances: dict[int, float]
    closing_balances: dict[int, float]


@cached_read
def upcoming_schedule(store: Store, end: date, today: date | None = None) -> Schedule:
    """Replay scheduled items after today's entered/reconstructed balances.

    An account is assigned only when every matching, non-transfer transaction
    belongs to the same account. Loans, contributions, and one-off events
    without that evidence stay unassigned; no payment account is invented.
    """
    today = today or date.today()
    tx = store.transactions()
    accounts = {a.id: a for a in store.accounts() if a.type.is_cashlike}
    balances = {}
    for aid, account in accounts.items():
        anchor = cash_anchor(store, account)
        index = pd.date_range(min(anchor[0], pd.Timestamp(today)),
                              max(anchor[0], pd.Timestamp(today)), freq="D")
        balances[aid] = float(cash_history(tx, account, index, anchor=anchor).loc[pd.Timestamp(today)])
    opening = dict(balances)
    pattern_accounts: dict[str, set[int]] = {}
    for row in tx.itertuples(index=False):
        if (pd.isna(row.date) or row.date.date() > today or
                row.category in PERSONAL_EXCLUSIONS):
            continue
        key = f"{normalize_desc(row.description)}|{'+' if row.amount > 0 else '-'}"
        pattern_accounts.setdefault(key, set()).add(int(row.account_id))
    assignments = {}
    for item in store.recurring():
        candidates = pattern_accounts.get(item.key, set())
        if len(candidates) == 1:
            aid = next(iter(candidates))
            if aid in accounts:
                assignments[item.id] = aid
    entries = []
    if end > today:
        for flow in scheduled_flows(store, today + timedelta(days=1), end):
            aid = assignments.get(flow.item_id)
            balance = None
            if aid is not None:
                balances[aid] += flow.amount
                balance = balances[aid]
            entries.append(CalendarEntry(flow.when, flow.label, flow.amount,
                                         flow.source, aid, balance))
    return Schedule(tuple(entries), opening, balances)
