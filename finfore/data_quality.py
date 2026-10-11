"""Read-only checks of the evidence behind a projection, not its accuracy.

No transactions in a month does not establish a missing statement. Likewise,
a recently entered balance does not establish that the ledger is complete.
These checks report those distinctions rather than grading a household.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .calendar import upcoming_schedule
from .history import cash_anchor
from .store import Store
from .util import fmt_money

BALANCE_REMINDER_DAYS = 30
HISTORY_REMINDER_DAYS = 90
CATEGORY_WINDOW_DAYS = 180
GAP_WINDOW_MONTHS = 12
SCHEDULE_WINDOW_DAYS = 30


@dataclass(frozen=True)
class QualityCheck:
    key: str
    title: str
    needs_review: bool
    detail: str
    page: str
    action: str


@dataclass(frozen=True)
class QualityReport:
    as_of: date
    checks: tuple[QualityCheck, ...]
    first_transaction: date | None
    last_transaction: date | None

    @property
    def concerns(self) -> tuple[QualityCheck, ...]:
        return tuple(c for c in self.checks if c.needs_review)

    @property
    def summary(self) -> str:
        if self.concerns:
            n = len(self.concerns)
            return f"{n} {'check needs' if n == 1 else 'checks need'} review"
        return "No gaps flagged by these checks"


def inspect_data_quality(store: Store, today: date | None = None) -> QualityReport:
    """Inspect recorded balances, cash ledgers, categories and calendar routing.

Dates are dates of evidence, not CSV import times. Future/invalid dates are
reported separately and cannot make a historical check look more current.
"""
    today = today or date.today()
    accounts = store.accounts()
    tx = store.transactions()
    valid = tx[tx["date"].notna() & (tx["date"] <= pd.Timestamp(today))]
    cash_ids = {a.id for a in accounts if a.type.is_cashlike}
    cash = valid[valid["account_id"].isin(cash_ids)]
    behavior = cash[(cash["category"] != "Transfer") & (cash["amount"] != 0)]
    first = cash["date"].min().date() if len(cash) else None
    last = cash["date"].max().date() if len(cash) else None
    checks: list[QualityCheck] = []

    def add(key, title, review, detail, page, action):
        checks.append(QualityCheck(key, title, bool(review), detail, page, action))

    balance_notes = []
    for account in accounts:
        anchor, _ = cash_anchor(store, account)
        age = (today - anchor.date()).days
        if age < 0:
            balance_notes.append(f"{account.name}: balance dated {anchor.date().isoformat()} is in the future.")
        elif age > BALANCE_REMINDER_DAYS:
            balance_notes.append(f"{account.name}: last recorded balance {anchor.date().isoformat()} ({age} days ago).")
    add("balances", "Recorded balances", not accounts or balance_notes,
        ("Add accounts and enter the balance and its date before using a projection."
         if not accounts else
         "\n".join(balance_notes) + "\nUpdate or confirm these balances against your statements."
         if balance_notes else "Each account has a recorded balance from the last 30 days.")
        + "\nThe 30-day cutoff is a reminder, not an accuracy threshold. Newer transactions can move cash forward from an older balance, but cannot confirm it.",
        "Accounts", "Check balances")

    ledger_notes = []
    for account in accounts:
        if not account.type.is_cashlike:
            continue
        rows = cash[cash["account_id"] == account.id]
        if rows.empty:
            ledger_notes.append(f"{account.name}: no dated transactions recorded through today.")
        else:
            latest = rows["date"].max().date()
            age = (today - latest).days
            if age > BALANCE_REMINDER_DAYS:
                ledger_notes.append(f"{account.name}: latest transaction {latest.isoformat()} ({age} days ago).")
    add("recent_transactions", "Recent transaction coverage", not cash_ids or ledger_notes,
        ("\n".join(ledger_notes) if ledger_notes else
         "No checking, savings or credit-card ledger is available." if not cash_ids else
         "Each cash or credit-card account has a transaction recorded in the last 30 days.")
        + "\nA quiet account may be complete. Compare the latest recorded date with the bank's export; this is not the date the CSV was imported.",
        "Transactions", "Review transactions")

    span = 0
    if not behavior.empty:
        span = (behavior["date"].max() - behavior["date"].min()).days + 1
    add("history_length", "Spending history", span < HISTORY_REMINDER_DAYS,
        ("No non-transfer cash transactions are recorded." if not span else
         f"Recorded non-transfer cash transactions span {span} days, from "
         f"{behavior['date'].min().date().isoformat()} to {behavior['date'].max().date().isoformat()}.")
        + "\nLess than 90 days is flagged as a short history. A longer span may show more variation, but does not establish completeness or capture every seasonal expense.",
        "Transactions", "Review history")

    gap_notes = []
    current_month = pd.Period(today, freq="M")
    for account in accounts:
        if not account.type.is_cashlike:
            continue
        rows = cash[cash["account_id"] == account.id]
        if rows.empty:
            continue
        observed = set(rows["date"].dt.to_period("M"))
        # Exclude the first recorded month and the current, unfinished month.
        start = max(min(observed) + 1, current_month - GAP_WINDOW_MONTHS)
        months = pd.period_range(start, current_month - 1, freq="M")
        gaps = [m.strftime("%b %Y") for m in months if m not in observed]
        if gaps:
            gap_notes.append(f"{account.name}: {', '.join(gaps)}.")
    add("quiet_months", "Months with no recorded activity", gap_notes,
        ("\n".join(gap_notes) if gap_notes else "No empty interior months were found in the available cash ledgers.")
        + "\nCheck up to the last 12 completed months after each account's first recorded month. These may be quiet months or missing history; the app cannot distinguish them. Transfers count as activity. Loan and investment statements are not expected to contain daily transactions.",
        "Transactions", "Check history")

    recent = behavior[behavior["date"] >= pd.Timestamp(today - timedelta(days=CATEGORY_WINDOW_DAYS - 1))]
    categories = recent["category"].fillna("").str.strip()
    unknown = recent[categories.isin(("", "Misc", "Other income"))]
    spent = -float(recent.loc[recent["amount"] < 0, "amount"].sum())
    unknown_spend = -float(unknown.loc[unknown["amount"] < 0, "amount"].sum())
    unknown_income = float(unknown.loc[unknown["amount"] > 0, "amount"].sum())
    share = f" ({unknown_spend / spent:.1%} of recorded spending)" if spent else ""
    add("categories", "Categories to review", len(unknown),
        (f"Nonzero cash transactions labeled Misc, Other income or without a category in the last 180 days: {len(unknown)}. "
         f"They include {fmt_money(unknown_spend)} of outflows{share} and {fmt_money(unknown_income)} of inflows."
         if len(unknown) else "No Misc, Other income or blank categories were found among nonzero cash transactions in the last 180 days.")
        + "\nThese entries are not omitted from the forecast. Review the labels before interpreting category-specific spending or scenarios; Misc and Other income can also be intentional. This check does not verify the other labels.",
        "Transactions", "Review categories")

    end = today + timedelta(days=SCHEDULE_WINDOW_DAYS)
    unassigned = [e for e in upcoming_schedule(store, end, today).entries
                  if e.amount < 0 and e.account_id is None]
    add("unassigned", "Scheduled outflows without an account", unassigned,
        (f"Scheduled outflows without a confirmed payment account: {len(unassigned)}, "
         f"totaling {fmt_money(-sum(e.amount for e in unassigned))} "
         f"between {(today + timedelta(days=1)).isoformat()} and {end.isoformat()}."
         if unassigned else "Every scheduled outflow in the next 30 days has a confirmed payment account, or none is scheduled.")
        + "\nUnassigned bills, loan payments, investment contributions and saved expenses still affect the combined forecast, but not the calendar's account-specific balances. The app does not invent a payment account. Review the Unassigned calendar filter and recurring patterns.",
        "Calendar", "Review calendar")

    invalid_count = int(tx["date"].isna().sum())
    future_count = int((tx["date"] > pd.Timestamp(today)).sum())
    add("dates", "Transaction dates", invalid_count or future_count,
        (f"Unreadable transaction dates: {invalid_count}. Transactions dated after today: {future_count}. "
         "They are excluded from the history checks above. Check the CSV date mapping; use one-off events for planned expenses."
         if invalid_count or future_count else "No unreadable or future transaction dates were found.")
        + "\nThis is a diagnostic check. It does not change any records or alter how the existing forecast handles them.",
        "Transactions", "Check dates")
    return QualityReport(today, tuple(checks), first, last)
