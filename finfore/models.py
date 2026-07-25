"""Domain models for Financing with Doug.

All monetary amounts are signed floats in account currency:
inflows positive, outflows negative. Loan balances are stored as
positive "amount owed"; net worth subtracts them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class AccountType(str, Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT = "credit"        # balance stored <= 0 (amount owed is negative)
    LOAN = "loan"            # balance stored >= 0 (amount owed)
    INVESTMENT = "investment"

    @property
    def label(self) -> str:
        return {
            "checking": "Checking",
            "savings": "Savings",
            "credit": "Credit card",
            "loan": "Loan",
            "investment": "Investment",
        }[self.value]

    @property
    def is_cashlike(self) -> bool:
        """Accounts whose transaction flows directly move the net cash position."""
        return self in (AccountType.CHECKING, AccountType.SAVINGS, AccountType.CREDIT)


@dataclass
class Account:
    name: str
    type: AccountType
    balance: float = 0.0                 # balance as of balance_date
    balance_date: date = field(default_factory=date.today)
    apr: float = 0.0                     # annual rate, loans/credit (e.g. 0.064)
    annual_return: float = 0.07          # investments: expected drift mu
    volatility: float = 0.15             # investments: annualized sigma
    monthly_contribution: float = 0.0    # investments: planned monthly add
    monthly_payment: float = 0.0         # loans: fallback payment if none detected
    payment_day: int = 1                 # loans: day-of-month for payment
    id: int | None = None

    @property
    def signed_balance(self) -> float:
        """Contribution of this account to net worth."""
        if self.type == AccountType.LOAN:
            return -abs(self.balance)
        return self.balance


CADENCE_DAYS = {
    "weekly": 7.0,
    "biweekly": 14.0,
    "semimonthly": 15.22,
    "monthly": 30.44,
    "quarterly": 91.31,
    "annual": 365.25,
}

CADENCE_LABEL = {
    "weekly": "Weekly",
    "biweekly": "Every 2 weeks",
    "semimonthly": "Twice a month",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "annual": "Yearly",
}


@dataclass
class RecurringItem:
    key: str                  # stable identity: normalized description + sign
    name: str                 # display name
    cadence: str              # one of CADENCE_DAYS keys
    amount: float             # signed median amount per occurrence
    last_date: date
    days_of_month: list[int] = field(default_factory=list)  # monthly-family cadences
    confidence: float = 0.0
    amount_cv: float = 0.0    # robust coefficient of variation of |amount|
    n_seen: int = 0
    active: bool = True
    kind: str = "bill"        # 'income' | 'bill' | 'loan_payment' | 'ignore'
    linked_account_id: int | None = None   # loan account for loan_payment
    id: int | None = None

    @property
    def monthly_equivalent(self) -> float:
        return self.amount * (30.44 / CADENCE_DAYS[self.cadence])


@dataclass
class Goal:
    name: str
    amount: float
    kind: str = "cash"        # 'cash' (net cash position) | 'networth'
    id: int | None = None


@dataclass
class OneOffEvent:
    """A dated, one-time shock to cash.

    Trend projections answer "if nothing changes"; real life is punctuated by
    things that do change — a car, a medical bill, a bonus, a gap between jobs.
    Modelling them as dated impulses rather than smoothing them into the
    monthly average is what lets the chart answer "can I absorb this, and when
    is the worst moment?"
    """
    label: str
    when: date
    amount: float             # negative = expense, positive = windfall
    active: bool = True
    id: int | None = None

    @property
    def is_expense(self) -> bool:
        return self.amount < 0


@dataclass
class Scenario:
    """What-if knobs applied on top of observed behavior."""
    category_multipliers: dict[str, float] = field(default_factory=dict)  # 1.0 = as-is
    income_multiplier: float = 1.0
    extra_loan_payment: float = 0.0      # $/month toward highest-APR unpaid loan
    extra_investing: float = 0.0         # $/month additional contribution
    snowball: bool = True                # roll freed loan payments into next loan
    events: list = field(default_factory=list)   # list[OneOffEvent]

    def is_baseline(self) -> bool:
        return (
            self.income_multiplier == 1.0
            and self.extra_loan_payment == 0.0
            and self.extra_investing == 0.0
            and all(abs(v - 1.0) < 1e-9 for v in self.category_multipliers.values())
        )
