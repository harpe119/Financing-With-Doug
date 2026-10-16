"""A reusable monthly budget, sharing its goal allowance with the goal ladder."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .insights import monthly_breakdown
from .models import AccountType

LABELS = {
    "income": "Take-home income",
    "essential": "Essentials, excluding debt",
    "debt": "Regular debt payments",
    "frivolous": "Wants",
    "giving": "Giving",
    "unknown": "Uncategorised spending",
    "reserve": "Irregular expenses reserve",
    "investing": "Investment contributions",
}


def money(value) -> float:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal('1000000000'):
            raise ValueError
        return float(number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError("Enter a finite amount between $0 and $1 billion.") from None


def validate_overrides(values: dict) -> dict[str, float]:
    if not isinstance(values, dict) or set(values) - set(LABELS):
        raise ValueError("The spending plan contains an unknown budget line.")
    return {key: money(value) for key, value in values.items()}


@dataclass
class SpendingPlan:
    detected: dict[str, float]
    amounts: dict[str, float]
    overrides: dict[str, float]
    required_debt: float
    details: object

    @property
    def expenses(self) -> float:
        return round(sum(v for k, v in self.amounts.items() if k != "income"), 2)

    @property
    def available(self) -> float:
        return round(self.amounts["income"] - self.expenses, 2)

    def remaining(self, goal_budget: float) -> float:
        return round(self.available - goal_budget, 2)


def calculate_plan(store, overrides: dict | None = None) -> SpendingPlan:
    from .goals import targets, LOAN
    details = monthly_breakdown(store.transactions())
    agg = details.aggregates if details else None
    required = round(sum(t.required for t in targets(store) if t.kind == LOAN), 2)
    essential = round(agg.essential, 2) if agg else 0.0
    debt = (float(details.groups['essential'].loc[
        details.groups['essential'].category == 'Debt payment', 'contribution'].sum())
        if details else 0.0)
    # Carve debt out of the same floored essentials group; never add a recorded
    # payment a second time. Refunds cannot make the other portion negative.
    debt = round(min(essential, max(0.0, debt)), 2)
    detected = dict(income=round(agg.income, 2) if agg else 0.0,
        essential=round(essential - debt, 2), debt=max(debt, required),
        frivolous=round(agg.frivolous, 2) if agg else 0.0,
        giving=round(agg.giving, 2) if agg else 0.0,
        unknown=round(agg.unknown, 2) if agg else 0.0,
        reserve=0.0, investing=round(sum(max(0.0, a.monthly_contribution)
            for a in store.accounts() if a.type == AccountType.INVESTMENT), 2))
    raw = store.get_setting('spending_plan', {}) if overrides is None else overrides
    values = validate_overrides(raw or {})
    return SpendingPlan(detected, {**detected, **values}, values, required, details)
