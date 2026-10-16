"""Goal planning: one priority ladder covering both debts and savings goals.

The debt snowball is a well-known idea — pay minimums everywhere, throw every
spare dollar at one debt, and when it clears, roll its whole payment into the
next. What it normally stops short of is the rest of life: the ladder ends when
the last loan clears, and the freed money quietly disperses back into spending.

This module extends the same mechanism past the last loan. Loans and savings
goals sit in *one* ordered list. Spare income flows down it by priority, and
whenever anything completes, everything it was consuming — a goal's allocation,
or a loan's whole minimum payment — cascades to whatever is next. That is the
snowball the user actually wants: the car payment becomes the emergency fund,
which becomes the new computer.

Three allocation policies are offered because the honest answer depends on
temperament, not arithmetic:

``focus``    every spare dollar to the top incomplete target. Mathematically
             fastest to clear the ladder, and the classic snowball advice.
``weighted`` split by 1/rank, so lower goals still visibly move. Slower
             overall, but a goal that never moves for two years is a goal
             people abandon, and a plan abandoned returns nothing.
``custom``   explicit dollars per target, for when the user knows better than
             any rule here does.

Everything is deterministic. This is a plan, not a forecast: the Monte Carlo
fan on the Forecast page already answers "what might happen", and mixing the
two would make a schedule nobody can act on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math

from .history import headline_stats
from .insights import emergency_fund_target, monthly_aggregates
from .models import AccountType
from .util import add_months

# A loan is a target whose "amount" is what is owed and whose "progress" is what
# has been repaid. Keeping both kinds in one shape is what lets the cascade not
# care which sort of thing just completed.
GOAL, LOAN = "goal", "loan"

POLICIES = {
    "focus": "Focus — everything spare to the top target",
    "weighted": "Weighted — split by priority, lower goals still move",
    "custom": "Custom — the amounts you set below",
}

PLAN_HORIZON_MONTHS = 360        # 30 years; long enough that "never" is obvious
_EPS = 0.01


@dataclass
class Target:
    """One rung on the ladder: a loan to clear or a goal to fund."""
    key: str                      # 'goal:3' / 'loan:2' — stable across reorders
    kind: str                     # GOAL | LOAN
    name: str
    amount: float                 # goal target, or loan balance owed
    progress: float               # saved so far, or repaid so far
    required: float = 0.0         # loan minimum payment; 0 for goals
    apr: float = 0.0
    goal_kind: str = "cash"       # 'savings' | 'cash' | 'networth', goals only
    account_id: int | None = None
    goal_id: int | None = None
    payment_source: str = ""

    @property
    def remaining(self) -> float:
        return max(0.0, self.amount - self.progress)

    @property
    def complete(self) -> bool:
        return self.remaining <= _EPS

    @property
    def fraction(self) -> float:
        return 1.0 if self.amount <= 0 else min(1.0, self.progress / self.amount)


@dataclass
class Cascade:
    """A dated hand-off: what finished, how much it freed, where it went."""
    when: date
    source: str                   # name of the target that completed
    amount: float                 # $/mo released
    destination: str | None       # next target, or None if the ladder is done
    kind: str = GOAL              # what completed


@dataclass
class Allocation:
    target: Target
    monthly: float                # $/mo at the start of the plan
    share: float                  # fraction of the spare pool, at the start
    eta: date | None              # completion date, None = beyond the horizon
    months: int | None
    peak_monthly: float = 0.0     # most it ever receives, after cascades

    @property
    def name(self) -> str:
        return self.target.name

    @property
    def total_monthly(self) -> float:
        """Existing loan payment plus the allocated extra money."""
        return self.monthly + self.target.required


@dataclass
class Plan:
    surplus: float                       # spare $/mo the plan distributes
    policy: str
    allocations: list[Allocation]
    cascades: list[Cascade] = field(default_factory=list)
    dates: list[date] = field(default_factory=list)
    # fraction funded per target over time, keyed by target key — the series
    # the Goals chart draws
    progress: dict[str, list[float]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    horizon_months: int = PLAN_HORIZON_MONTHS
    custom: dict[str, float] = field(default_factory=dict)

    @property
    def all_done(self) -> date | None:
        etas = [a.eta for a in self.allocations]
        return max(etas) if etas and all(e is not None for e in etas) else None

    def by_key(self, key: str) -> Allocation | None:
        return next((a for a in self.allocations if a.target.key == key), None)


# --------------------------------------------------------------------------
# Building the ladder
# --------------------------------------------------------------------------
def surplus_monthly(store) -> float:
    """Spare income per month: what is left after everything already spent.

    A saved monthly spending plan supplies its income minus budgeted spending,
    reserves and investing. Without one, use the observed history below.

    Taken from recency-weighted completed-month income minus personal spending,
    floored at zero. Recorded loan payments are already inside ``spending`` and
    must not be subtracted twice. This is observed surplus: it does not prove
    that every future commitment was recorded or that cash is available today.
    """
    if store.get_setting("spending_plan", None) is not None:
        from .spending_plan import calculate_plan
        return max(0.0, calculate_plan(store).available)
    agg = monthly_aggregates(store.transactions())
    return max(0.0, float(agg.surplus)) if agg else 0.0


def _pot(store) -> tuple[float, float]:
    """(cash, net worth) available to count as progress against goals."""
    try:
        st = headline_stats(store)
        return float(st["cash"]), float(st["networth"])
    except Exception:
        return 0.0, 0.0


def targets(store) -> list[Target]:
    """The ladder, ordered by the user's stored priority.

    Goals share one pot of money, so a naive "progress = current cash" would
    report every goal under your balance as already met at the same time. They
    are filled in priority order instead: the emergency fund consumes the cash
    first, and a lower goal only shows progress once the ones above it are
    covered. That is what having a priority *means*.
    """
    cash, networth = _pot(store)
    from .recurrence import personal_recurring
    linked: dict[int, float] = {}
    for it in personal_recurring(store.transactions(), store.recurring()):
        if (it.active and it.kind == "loan_payment" and it.amount < 0
                and it.linked_account_id is not None):
            linked[it.linked_account_id] = (linked.get(it.linked_account_id, 0.0)
                                          + abs(it.monthly_equivalent))
    items: list[Target] = []
    for a in store.accounts():
        if a.type != AccountType.LOAN or abs(a.balance) <= _EPS:
            continue
        items.append(Target(key=f"loan:{a.id}", kind=LOAN, name=a.name,
                            amount=abs(a.balance), progress=0.0,
                            required=linked.get(a.id, max(0.0, a.monthly_payment)), apr=a.apr,
                            payment_source=("Linked recurring payments" if a.id in linked
                                            else "Accounts monthly payment"),
                            account_id=a.id))
    for g in store.goals():
        items.append(Target(key=f"goal:{g.id}", kind=GOAL, name=g.name,
                            amount=float(g.amount),
                            progress=(max(0.0, min(g.amount, g.saved_amount + g.spent_amount))
                                      if g.kind == "savings" else 0.0),
                            goal_kind=g.kind, goal_id=g.id))

    order = store.plan_order()
    rank = {k: i for i, k in enumerate(order)}
    # Unranked items (just added) go to the bottom rather than silently to the
    # top, where they would hijack the whole surplus before being noticed.
    items.sort(key=lambda t: (rank.get(t.key, len(rank) + 1), t.name))

    # Balance milestones measure household totals, including earmarked money.
    # Keep their legacy behavior; earmarks themselves are never auto-filled.
    remaining = {"cash": cash, "networth": networth}
    for t in items:
        if t.kind != GOAL or t.goal_kind == "savings":
            continue
        pool = remaining.get(t.goal_kind, 0.0)
        t.progress = max(0.0, min(t.amount, pool))
        remaining[t.goal_kind] = pool - t.progress
    return items


def suggested_shares(items: list[Target], policy: str) -> dict[str, float]:
    """Fraction of the spare pool each incomplete target should receive."""
    live = [t for t in items if not t.complete]
    if not live:
        return {}
    if policy == "weighted":
        # 1/rank: the top target gets half of a two-item ladder, two thirds of
        # what the second gets on a three-item one. Steep enough to be a real
        # priority, flat enough that the bottom rung still moves.
        w = {t.key: 1.0 / (i + 1) for i, t in enumerate(live)}
        total = sum(w.values())
        return {k: v / total for k, v in w.items()}
    return {live[0].key: 1.0}            # focus


# --------------------------------------------------------------------------
# The simulation
# --------------------------------------------------------------------------
def build_plan(store, policy: str | None = None,
               surplus: float | None = None,
               custom: dict[str, float] | None = None,
               horizon_months: int = PLAN_HORIZON_MONTHS,
               extra_monthly: float = 0.0) -> Plan:
    """Walk the ladder forward month by month, cascading as things complete.

    ``extra_monthly`` adds to the spare pool without persisting anything — it
    is how the page answers "what if I found another $200?" without the user
    having to commit to the answer first.
    """
    policy = policy or store.plan_policy()
    custom = custom if custom is not None else store.plan_allocations()
    items = targets(store)
    pool0 = (surplus_monthly(store) if surplus is None else float(surplus))
    pool0 = max(0.0, pool0 + float(extra_monthly))

    warnings: list[str] = []
    if not items:
        return Plan(surplus=pool0, policy=policy, allocations=[],
                    warnings=["No goals or loans yet — add one to build a plan."],
                    horizon_months=horizon_months)
    if pool0 <= 0:
        warnings.append(
            "No spare income is available for extra contributions. Existing loan "
            "payments continue; a payment freed after payoff can fund the next goal.")
    for t in items:
        if t.kind == LOAN and t.required <= 0:
            warnings.append(f"{t.name}: no existing payment found. Set the monthly "
                            "payment on Accounts, or link it in Bills & payday.")
        elif t.kind == LOAN and t.required <= t.amount * t.apr / 12:
            warnings.append(f"{t.name}: the existing payment does not cover monthly "
                            "interest; payoff requires extra money.")
    reserved = sum(g.saved_amount for g in store.goals() if g.kind == "savings")
    cash, _ = _pot(store)
    if reserved > max(0.0, cash) + _EPS:
        warnings.append("Goal savings recorded exceed current net cash. Review "
                        "Already saved amounts and account balances.")

    start = date.today().replace(day=1)
    state = {t.key: t for t in items}
    saved = {t.key: t.progress for t in items}
    eta: dict[str, date | None] = {t.key: None for t in items}
    peak: dict[str, float] = {t.key: 0.0 for t in items}
    first_month: dict[str, float] = {}
    cascades: list[Cascade] = []
    dates: list[date] = []
    progress: dict[str, list[float]] = {t.key: [] for t in items}

    # Loan minimums keep flowing whether or not there is any surplus; when a
    # loan clears, that payment is what makes the snowball big.
    freed = 0.0
    done: set[str] = {t.key for t in items if t.complete}
    # A target already met needs a date, or it falls through to the "not
    # reached within the horizon" branch and gets reported as unreachable —
    # the exact opposite of the truth. Callers distinguish the two by asking
    # the target whether it is complete, not by the ETA being None.
    for key in done:
        eta[key] = start

    for m in range(horizon_months):
        when = add_months(start, m)
        dates.append(when)
        pool = pool0 + freed
        live = [t for t in items if t.key not in done]

        # --- required loan payments (already committed, outside the pool) ---
        for t in live:
            if t.kind != LOAN:
                continue
            owed = t.amount - saved[t.key]
            owed += owed * (t.apr / 12.0)
            pay = min(t.required, owed)
            pool += t.required - pay
            saved[t.key] = t.amount - (owed - pay)

        # --- discretionary pool, by policy ---
        shares: dict[str, float] = {}
        # Money left after a target finishes is still available this month.
        # Re-split it among unfinished targets rather than losing it at every
        # hand-off (particularly visible for small purchase goals).
        while pool > _EPS:
            receiving = [t for t in live if t.amount - saved[t.key] > _EPS]
            proposed = _shares_for(receiving, policy, custom, pool)
            used = 0.0
            for key, amount in proposed.items():
                put = min(amount, max(0.0, state[key].amount - saved[key]))
                saved[key] += put
                shares[key] = shares.get(key, 0.0) + put
                used += put
            if used <= _EPS:
                break
            pool -= used
        for key, amount in shares.items():
            peak[key] = max(peak[key], amount)
            if m == 0:
                first_month[key] = amount

        # --- completions cascade ---
        for t in list(live):
            if saved[t.key] < t.amount - _EPS:
                continue
            done.add(t.key)
            released = t.required if t.kind == LOAN else shares.get(t.key, 0.0)
            if t.kind == LOAN:
                freed += t.required
            nxt = next((x for x in items if x.key not in done), None)
            cascades.append(Cascade(when=when, source=t.name, amount=released,
                                    destination=nxt.name if nxt else None,
                                    kind=t.kind))
            eta[t.key] = when

        for t in items:
            progress[t.key].append(
                1.0 if t.amount <= 0 else min(1.0, saved[t.key] / t.amount))
        if len(done) == len(items):
            break

    allocations = []
    for t in items:
        months = None
        if eta[t.key] is not None:
            months = (eta[t.key].year - start.year) * 12 + \
                     (eta[t.key].month - start.month)
        allocations.append(Allocation(
            target=t, monthly=first_month.get(t.key, 0.0),
            share=(first_month.get(t.key, 0.0) / pool0) if pool0 > 0 else 0.0,
            eta=eta[t.key], months=months, peak_monthly=peak[t.key]))

    if any(a.eta is None for a in allocations):
        warnings.append(
            f"Some targets are not reached within {horizon_months // 12} years "
            f"at this rate — raise the spare amount, lower a target, or move it "
            f"up the ladder.")
    return Plan(surplus=pool0, policy=policy, allocations=allocations,
                cascades=cascades, dates=dates, progress=progress,
                warnings=warnings, horizon_months=horizon_months, custom=dict(custom))


def _shares_for(live: list[Target], policy: str, custom: dict[str, float],
                pool: float) -> dict[str, float]:
    """Split ``pool`` across the live targets for one month."""
    if not live or pool <= 0:
        return {}
    if policy == "custom" and custom:
        want = {t.key: max(0.0, float(custom.get(t.key, 0.0))) for t in live}
        total = sum(want.values())
        if total <= 0:
            return {live[0].key: pool}
        # Custom amounts are a ratio, not a cap: if the user's numbers add up
        # to less than the pool, the remainder still has to go somewhere, and
        # leaving it unallocated would silently under-report every ETA.
        return {k: pool * v / total for k, v in want.items() if v > 0}
    if policy == "weighted":
        w = {t.key: 1.0 / (i + 1) for i, t in enumerate(live)}
        total = sum(w.values())
        return {k: pool * v / total for k, v in w.items()}
    return {live[0].key: pool}


# --------------------------------------------------------------------------
# "How do I get there faster?"
# --------------------------------------------------------------------------
@dataclass
class Speedup:
    """What an extra $X/month buys, in months off a specific target."""
    extra: float
    target: str
    months_saved: int
    new_eta: date | None
    old_eta: date | None


def speedups(store, plan: Plan, amounts=(50.0, 100.0, 250.0, 500.0),
             target_key: str | None = None) -> list[Speedup]:
    """Cost out several extra-savings levels against one live target.

    Defaults to the top incomplete rung; ``target_key`` costs any other. Under
    a focus split, extra money reaches a lower rung only after the ones above
    it clear — which is the honest answer to "can I have this by July?" and the
    reason the figures for a lower goal can look immovable.

    Deliberately phrased as time rather than dollars. "Save $100/mo more" is an
    instruction nobody follows; "the emergency fund arrives seven months
    sooner" is a reason to.
    """
    focus = None
    if target_key:
        focus = next((a for a in plan.allocations
                      if a.target.key == target_key and not a.target.complete),
                     None)
    if focus is None:
        focus = next((a for a in plan.allocations if not a.target.complete),
                     None)
    if focus is None:
        return []
    out = []
    for extra in amounts:
        alt = build_plan(store, policy=plan.policy, surplus=plan.surplus,
                         custom=plan.custom,
                         horizon_months=plan.horizon_months,
                         extra_monthly=extra)
        a2 = alt.by_key(focus.target.key)
        if a2 is None:
            continue
        if focus.months is None:
            saved = None if a2.months is None else plan.horizon_months - a2.months
        elif a2.months is None:
            saved = 0
        else:
            saved = focus.months - a2.months
        out.append(Speedup(extra=extra, target=focus.target.name,
                           months_saved=int(saved or 0), new_eta=a2.eta,
                           old_eta=focus.eta))
    return out


def emergency_goal_amount(store, months: int = 3) -> float:
    """Suggested size for an emergency-fund goal, from real essential spend."""
    return round(emergency_fund_target(store, months), -1)


# --------------------------------------------------------------------------
# "I want it by this date — what would I have to give up?"
# --------------------------------------------------------------------------
@dataclass
class Cut:
    """One thing that could be reduced or dropped, and what it frees."""
    name: str
    monthly: float                # $/mo recovered if dropped entirely
    bucket: str                   # 'frivolous' | 'unknown'
    source: str                   # 'subscription' | 'category'
    recurring: bool = False


@dataclass
class Deadline:
    """What reaching one target by a chosen date would take."""
    target: str
    by: date
    months: int
    needed_monthly: float         # total $/mo the target must receive
    shortfall: float              # extra $/mo beyond what it gets today
    cuts: list[Cut] = field(default_factory=list)      # the ones that suffice
    available: float = 0.0        # total discretionary $/mo that could be cut
    feasible: bool = True
    already_met: bool = False
    note: str = ""
    rank: int = 1                 # position on the ladder, 1 = top
    ahead: list[str] = field(default_factory=list)     # names above it
    advice: str = ""              # what to do about being below them


def _cut_candidates(store) -> list[Cut]:
    """Discretionary spending that could plausibly be reduced.

    Only wants. Essentials — rent, groceries, insurance, medical, fuel — and
    loan payments are deliberately excluded: a plan that reaches a goal by
    cutting those is not a plan, it is a description of a crisis, and offering
    it as advice would be worse than saying the date cannot be met.
    Uncategorised spending is offered separately and flagged, because some of
    it is certainly essential and the app simply does not know which.
    """
    from .categorize import categorize
    from .insights import SUBSCRIPTION_CAT, bucket_of, category_monthly

    cuts: list[Cut] = []
    seen_recurring = 0.0
    from .recurrence import personal_recurring
    for it in personal_recurring(store.transactions(), store.recurring()):
        if not it.active or it.amount >= 0 or it.kind in ("ignore", "loan_payment"):
            continue
        cat = categorize(it.name, it.amount)
        if bucket_of(cat) != "frivolous":
            continue
        amount = abs(it.monthly_equivalent)
        if amount < 1.0:
            continue
        cuts.append(Cut(name=it.name.title(), monthly=amount,
                        bucket="frivolous", source="subscription",
                        recurring=True))
        if cat == SUBSCRIPTION_CAT:
            seen_recurring += amount

    # Whole categories, minus whatever the named recurring items already cover,
    # so a $16 streaming charge is not counted once by name and again inside
    # "Subscriptions".
    per_cat = category_monthly(store.transactions())
    for cat, amount in sorted(per_cat.items(), key=lambda kv: -kv[1]):
        b = bucket_of(cat)
        if b not in ("frivolous", "unknown"):
            continue
        rest = amount - (seen_recurring if cat == SUBSCRIPTION_CAT else 0.0)
        if rest < 5.0:
            continue
        cuts.append(Cut(name=cat, monthly=rest, bucket=b, source="category"))
    cuts.sort(key=lambda c: -c.monthly)
    return cuts


def deadline_plan(store, plan: Plan, target_key: str, by: date) -> Deadline:
    """What it would take to finish one target by ``by``.

    Answers with named things to give up rather than a number, because "find
    another $240/month" is not an instruction anyone can act on and "drop the
    two streaming subscriptions and half the takeaway" is. If the wants on the
    table do not add up, it says so plainly instead of quietly proposing a cut
    into rent.
    """
    alloc = plan.by_key(target_key)
    if alloc is None:
        return Deadline(target="—", by=by, months=0, needed_monthly=0.0,
                        shortfall=0.0, note="That target is no longer on the ladder.")
    t = alloc.target
    # Where it sits, and what is in front of it. A goal below a loan is not
    # slow because it is underfunded, it is slow because it is waiting — and
    # the useful advice there is about priority, not about spending less.
    live = [a for a in plan.allocations if not a.target.complete]
    rank = next((i + 1 for i, a in enumerate(live)
                 if a.target.key == target_key), 1)
    ahead = [a.target.name for a in live[:max(0, rank - 1)]]

    today = date.today().replace(day=1)
    months = max(0, (by.year - today.year) * 12 + (by.month - today.month))
    if t.complete:
        return Deadline(target=t.name, by=by, months=months, needed_monthly=0.0,
                        shortfall=0.0, already_met=True,
                        note="Already met — nothing to find.")
    if months <= 0:
        return Deadline(
            target=t.name, by=by, months=0, needed_monthly=t.remaining,
            shortfall=t.remaining, feasible=False,
            note="That date is this month or already past. Pick a later one.")

    # This month's installment is included by build_plan. Quote an indicative
    # standalone pace, but determine extra household budget by replaying the
    # actual ladder, including interest, priorities and released payments.
    installments = months + 1
    r = t.apr / 12 if t.kind == LOAN else 0.0
    needed = (t.remaining * r / (1 - (1 + r) ** -installments)
              if r > 0 else t.remaining / installments)
    end_month = by.replace(day=1)
    if alloc.eta is not None and alloc.eta <= end_month:
        shortfall = 0.0
    else:
        def meets(extra: float) -> bool:
            alt = build_plan(store, policy=plan.policy, surplus=plan.surplus,
                             custom=plan.custom, extra_monthly=extra,
                             horizon_months=installments)
            a = alt.by_key(target_key)
            return a is not None and a.eta is not None and a.eta <= end_month

        low, high = 0.0, max(1.0, needed)
        # A custom zero share can make a target wait until others finish. The
        # same replay captures that instead of declaring it on track from a
        # standalone division of dollars by months.
        while high < 1e9 and not meets(high):
            high *= 2
        if high >= 1e9 and not meets(high):
            shortfall = high
        else:
            for _ in range(24):
                mid = (low + high) / 2
                if meets(mid):
                    high = mid
                else:
                    low = mid
            shortfall = math.ceil(high * 100) / 100

    cuts = _cut_candidates(store)
    available = sum(c.monthly for c in cuts)
    if shortfall <= 0.01:
        return Deadline(target=t.name, by=by, months=months,
                        needed_monthly=needed, shortfall=0.0, cuts=[],
                        available=available, rank=rank, ahead=ahead,
                        advice=(f"This is priority {rank}, behind {', '.join(ahead)}. "
                                "The plan includes those commitments before this goal."
                                if ahead else ""),
                        note="Already on track for that date at the current split.")

    chosen: list[Cut] = []
    running = 0.0
    for c in cuts:
        if running >= shortfall:
            break
        chosen.append(c)
        running += c.monthly
    feasible = running >= shortfall - 0.01
    note = ""
    if not feasible:
        note = (f"Not reachable by then. Every discretionary item adds up to "
                f"{available:,.0f}/mo, which is {shortfall - available:,.0f}/mo "
                f"short — and the rest of the budget is essentials and loan "
                f"payments, which this will not propose cutting. Push the date "
                f"out, lower the target, or raise income.")

    advice = ""
    if ahead:
        names = ", ".join(ahead[:3]) + ("…" if len(ahead) > 3 else "")
        if feasible:
            advice = (f"This is priority {rank}, behind {names}. The figures "
                      f"above are extra money on top of those — nothing here "
                      f"asks you to stop paying them.")
        else:
            advice = (f"This is priority {rank}, behind {names}. Moving it up "
                      f"the ladder would reach it sooner without finding any "
                      f"more money — but it delays whatever it overtakes by "
                      f"about as much, so it is worth being deliberate. "
                      f"Clearing a high-interest debt first is usually worth "
                      f"more than it costs in waiting.")
    return Deadline(target=t.name, by=by, months=months, needed_monthly=needed,
                    shortfall=shortfall, cuts=chosen, available=available,
                    feasible=feasible, note=note, rank=rank, ahead=ahead,
                    advice=advice)
