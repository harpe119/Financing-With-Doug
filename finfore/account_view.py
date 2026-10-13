"""What each account row on the Accounts page says, without any Qt in it.

The Accounts page used to end every row at a date. The facts that decide whether
a balance can be trusted — how old it is, whether any transactions have been
imported, whether the imported history agrees with it — were all already
computed elsewhere (the dashboard's freshness chip, the Forecast's data-quality
card, Check my balances), but the page where you *fix* those things showed none
of them. This module turns them into one plain sentence per account, with the
one thing worth doing about it.

Kept free of Qt so every wording and every threshold can be tested directly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .data_quality import BALANCE_REMINDER_DAYS
from .history import cash_anchor, reconcile_all
from .models import Account, AccountType
from .util import fmt_rate


def money_exact(x: float, signed: bool = False) -> str:
    """$12,415.57 with the sign in front of the dollar sign.

    The Accounts page is where balances are checked against a statement, so it
    shows cents; rounding to whole dollars would hide exactly the small
    discrepancies the page exists to catch.
    """
    sign = "-" if x < 0 and round(abs(x), 2) else ("+" if signed and x > 0 else "")
    return f"{sign}${abs(x):,.2f}"


# ---------------------------------------------------------------------------
# Which side of net worth an account sits on
# ---------------------------------------------------------------------------
def owed_amount(a: Account) -> float | None:
    """What the account owes, as a positive figure, or None if it is an asset.

    Mirrors ``history.headline_stats`` so the subtotals on the page tie to the
    net worth in the header: a loan is always debt, a credit card is debt while
    its stored balance is negative and cash once it is in credit.
    """
    if a.type == AccountType.LOAN:
        return abs(a.balance)
    if a.type == AccountType.CREDIT and a.balance < 0:
        return -a.balance
    return None


def held_amount(a: Account) -> float:
    """What the account holds (an asset side figure); only valid when not owed."""
    return a.balance


def split_accounts(accounts: list[Account]):
    """(accounts you hold money in, accounts you owe on), each in the order given."""
    have, owe = [], []
    for a in accounts:
        (owe if owed_amount(a) is not None else have).append(a)
    return have, owe


# ---------------------------------------------------------------------------
# Loan payoff, in words
# ---------------------------------------------------------------------------
def payoff_months(balance: float, apr: float, payment: float) -> int | None:
    """Whole months to pay a loan off at a fixed payment, or None if it never is.

    Standard amortisation at APR/12 a month. None means the payment does not
    even cover the interest, which is worth saying out loud rather than
    quoting an absurd date.
    """
    balance = abs(balance)
    if balance <= 0:
        return 0
    if payment <= 0:
        return None
    r = apr / 12.0
    if r <= 0:
        return math.ceil(balance / payment)
    if payment <= balance * r:
        return None
    return math.ceil(-math.log(1.0 - r * balance / payment) / math.log(1.0 + r))


def duration_text(months: int) -> str:
    """'7 months', '18 months', '3 years': rounded the way a person would say it."""
    if months < 24:
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{round(months / 12)} years"


def terms_text(a: Account) -> str:
    """One plain line about how this account behaves going forward."""
    parts: list[str] = []
    if a.type == AccountType.LOAN:
        if a.apr:
            parts.append(f"{fmt_rate(a.apr)} APR")
        if a.monthly_payment:
            parts.append(f"${a.monthly_payment:,.0f}/mo on day {a.payment_day}")
            n = payoff_months(a.balance, a.apr, a.monthly_payment)
            if n is None:
                parts.append("payment doesn’t cover the interest")
            else:
                parts.append(f"paid off in about {duration_text(n)}")
    elif a.type == AccountType.INVESTMENT:
        parts.append(f"assumes {fmt_rate(a.annual_return)} a year, "
                     f"swinging ±{fmt_rate(a.volatility)}")
        if a.monthly_contribution:
            parts.append(f"adds ${a.monthly_contribution:,.0f}/mo")
    elif a.type == AccountType.CREDIT and a.apr:
        parts.append(f"{fmt_rate(a.apr)} APR")
    if not parts:
        return "—"
    text = " · ".join(parts)
    return text[0].upper() + text[1:]


# ---------------------------------------------------------------------------
# Freshness, in words
# ---------------------------------------------------------------------------
def ago_text(days: int) -> str:
    if days < 0:
        return "In the future"
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 60:
        return f"{days} days ago"
    if days < 365:
        return f"{round(days / 30)} months ago"
    return f"{round(days / 365)} year{'s' if round(days / 365) != 1 else ''} ago"


@dataclass(frozen=True)
class AccountStatus:
    """What to tell the person about one account, and the one fix worth offering.

    ``level`` is ``ok`` / ``info`` / ``caution``. Caution is amber and never red:
    the data is suspect, nothing is broken, and the person is not in trouble.
    ``fix`` is ``balance`` (update the balance), ``import`` (bring in
    transactions), ``check`` (open Check my balances) or None.
    """
    text: str
    level: str = "ok"
    fix: str | None = None
    anchor: date | None = None
    age_days: int = 0

    @property
    def needs_a_look(self) -> bool:
        return self.level == "caution"


def account_statuses(store, today: date | None = None) -> dict[int, AccountStatus]:
    """Status for every account, keyed by account id.

    Computed in one pass because the inputs are shared: the transactions are
    read once and the ledger reconciliation once, however many accounts there
    are. The thresholds are the same ones the dashboard and the Forecast's
    data-quality card use, so the three places can never disagree about what
    "old" means.
    """
    today = today or date.today()
    accounts = store.accounts()
    if not accounts:
        return {}
    tx = store.transactions()
    if len(tx):
        tx = tx[tx["date"].notna()]
    try:
        bad_names = {i.account for i in reconcile_all(store) if not i.ok}
    except Exception:
        bad_names = set()
    out: dict[int, AccountStatus] = {}
    for a in accounts:
        anchor, _ = cash_anchor(store, a)
        age = (pd.Timestamp(today) - pd.Timestamp(anchor).normalize()).days
        reasons: list[str] = []
        fix: str | None = None
        level = "ok"
        if a.type.is_cashlike:
            rows = tx[tx["account_id"] == a.id] if len(tx) else tx
            if a.name in bad_names:
                reasons.append("Doesn’t add up")
                level, fix = "caution", "check"
            elif rows is None or len(rows) == 0:
                reasons.append("No transactions imported yet")
                level, fix = "info", "import"
            else:
                lag = (pd.Timestamp(today) - rows["date"].max().normalize()).days
                if lag > BALANCE_REMINDER_DAYS:
                    reasons.append(f"Last transaction {lag} days ago")
                    level, fix = "caution", "import"
        if age < 0:
            reasons.append("Balance is dated in the future")
            level, fix = "caution", fix or "balance"
        elif age > BALANCE_REMINDER_DAYS:
            reasons.append(f"Balance {age} days old")
            level, fix = "caution", fix or "balance"
        text = " · ".join(reasons) if reasons else "Up to date"
        out[a.id] = AccountStatus(text, level, fix, pd.Timestamp(anchor).date(), age)
    return out


def summary_line(accounts: list[Account], statuses: dict[int, AccountStatus]) -> str:
    """The sentence under the page title."""
    n = len(accounts)
    if n == 0:
        return "No accounts yet."
    look = sum(1 for s in statuses.values() if s.needs_a_look)
    waiting = sum(1 for s in statuses.values() if s.level == "info")
    noun = "account" if n == 1 else "accounts"
    if look:
        return f"{n} {noun}. {look} {'needs' if look == 1 else 'need'} a look."
    if waiting:
        return (f"{n} {noun}. {waiting} still "
                f"{'needs its' if waiting == 1 else 'need their'} "
                f"transactions imported.")
    return (f"{n} {noun}. Every balance is recent." if n > 1
            else f"{n} {noun}. Its balance is recent.")
