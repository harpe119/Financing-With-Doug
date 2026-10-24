"""A reusable monthly budget, sharing its goal allowance with the goal ladder."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .insights import ESSENTIAL, FRIVOLOUS, bucket_of, monthly_breakdown
from .models import AccountType
from .cache import cached_read

LABELS = {
    "income": "Take-home income",
    "essential": "Needs, excluding debt payments",
    "debt": "Regular debt payments",
    "frivolous": "Wants",
    "giving": "Giving",
    "unknown": "Uncategorized spending",
    "reserve": "Irregular expenses reserve",
    "investing": "Investment contributions",
}
CATEGORY_LABELS = {f'category:{cat}': cat for cat in
                   sorted(ESSENTIAL - {'Debt payment'}) + sorted(FRIVOLOUS)}


def category_group(key):
    return bucket_of(CATEGORY_LABELS[key]) if key in CATEGORY_LABELS else None


def money(value) -> float:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal('1000000000'):
            raise ValueError
        return float(number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError("Enter a finite amount between $0 and $1 billion.") from None


def validate_overrides(values: dict) -> dict[str, float]:
    if not isinstance(values, dict) or set(values) - (set(LABELS) | set(CATEGORY_LABELS)):
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
        # Loan obligations are paid whatever the override says; budgeting less
        # would hand the same dollars to the goal allowance as well.
        return round(sum(max(self.amounts[k], self.required_debt) if k == "debt"
                         else self.amounts[k] for k in LABELS if k != "income"), 2)

    @property
    def available(self) -> float:
        return round(self.amounts["income"] - self.expenses, 2)

    def remaining(self, goal_budget: float) -> float:
        return round(self.available - goal_budget, 2)


@cached_read
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
    signed = {}
    if details:
        for group, rows in details.groups.items():
            if group != 'income':
                signed.update(rows.groupby('category')['contribution'].sum().to_dict())
    for key, cat in CATEGORY_LABELS.items():
        detected[key] = round(max(0.0, signed.get(cat, 0.0)), 2)
    amounts = {**detected, **values}
    for group in ('essential', 'frivolous'):
        if group not in values and any(category_group(k) == group for k in values):
            categories = [k for k in CATEGORY_LABELS if category_group(k) == group]
            amounts[group] = round(max(0.0, sum(
                values[k] if k in values else signed.get(CATEGORY_LABELS[k], 0.0)
                for k in categories)), 2)
    return SpendingPlan(detected, amounts, values, required, details)


def planned_line(plan, key, reference):
    """The forecast's monthly amount for one budget line.

    The plan and the forecast estimate the same habits differently (complete
    months versus bills and recent weeks), so replacing the forecast's figure
    with the plan's moved an unchanged line, and a cut could raise forecast
    spending. A line the plan leaves alone keeps the forecast's reference. An
    override with detected history moves it by the same dollars it moves the
    plan, matching the allowance that cut frees. Without history on either
    side, or at $0, the planned amount is taken as the whole budget.
    """
    if key not in plan.overrides:
        return reference
    amount, detected = plan.amounts[key], plan.detected[key]
    if amount <= 0:
        return 0.0
    if detected <= 0 or reference <= 0:
        return amount
    return max(0.0, reference + amount - detected)


def forecast_targets(plan, references):
    """Planned monthly spending for the forecast categories the plan changes.

    Explicit total overrides take precedence over category amounts. A changed
    total scales its group's forecast mix; a total without detected history is
    distributed using the plan's category mix (or the forecast mix). Categories
    left out of the result keep their forecast reference.
    """
    result = {}
    for group in ('essential', 'frivolous', 'giving', 'unknown'):
        cats = {cat for cat in references if cat != 'Debt payment' and bucket_of(cat) == group}
        if group not in plan.overrides:
            for key, cat in CATEGORY_LABELS.items():
                if category_group(key) == group and key in plan.overrides:
                    result[cat] = planned_line(plan, key, references.get(cat, 0.0))
            continue
        current = {cat: references[cat] for cat in sorted(cats) if references[cat] > 0}
        if plan.detected[group] > 0 or plan.amounts[group] <= 0:
            total = planned_line(plan, group, sum(current.values()))
            if current:
                scale = total / sum(current.values())
                result.update({cat: value * scale for cat, value in current.items()})
            elif total > 0:
                result[f'Planned {LABELS[group].lower()}'] = total
            continue
        cats.update(cat for key, cat in CATEGORY_LABELS.items() if category_group(key) == group)
        if group == 'giving':
            cats.add('Charitable donation')
        weights = {cat: plan.amounts.get(f'category:{cat}', references.get(cat, 0.0)) for cat in sorted(cats)}
        total = sum(weights.values())
        if total <= 0:
            weights = {cat: references.get(cat, 0.0) for cat in sorted(cats)
                       if f'category:{cat}' not in plan.overrides}
            total = sum(weights.values())
        if total <= 0:
            # A new category or a plan without usable forecast history needs
            # an explicit daily flow rather than a division by zero.
            label = next(iter(cats)) if len(cats) == 1 else f'Planned {LABELS[group].lower()}'
            result[label] = plan.amounts[group]
        else:
            result.update({cat: plan.amounts[group] * value / total for cat, value in weights.items()})
    return result
