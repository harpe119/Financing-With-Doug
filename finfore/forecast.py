"""Forecast engine.

Layered projection over a daily grid t = 0..H (day 0 = today):

1. Deterministic recurring flows — every active detected pattern (income,
   bills) is replayed forward on its cadence at its median amount. Scenario
   category multipliers scale bills; the income multiplier scales income.

2. Loan amortization — monthly compounding at APR/12, payment applied on each
   loan's observed payment day. Scenario extra payment goes to the
   highest-APR unpaid loan (avalanche); optionally, a paid-off loan's base
   payment rolls into the next loan (snowball). Loan dynamics are
   deterministic, so payoff dates are exact per scenario.

3. Investments — geometric Brownian motion per account,
       V_{k+1} = V_k * exp[(mu - sigma^2/2) dt + sigma sqrt(dt) Z] + C_k,
   stepped at month boundaries; the deterministic path uses sigma = 0.
   Contributions are debited from cash on the 1st.

4. Discretionary behavior — everything not matched to a recurring pattern is
   the empirical "habit" signal. Complete historical weeks form a matrix
   W[week, category] of spend; Monte Carlo paths bootstrap whole weeks
   (preserving cross-category correlation within a week), scale by scenario
   multipliers, and spread the total evenly over the 7 days. Irregular
   income is bootstrapped the same way.

Net worth = cash + investments - loan balances, with percentile bands taken
across paths at each t.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .categorize import categorize
from .history import current_accounts, networth_history, weekly_category_matrix
from .forecast_history import ForecastHistory, SETTING as HISTORY_SETTING
from .cache import cached_read
from .models import Account, AccountType, Goal, RecurringItem, Scenario
from .recurrence import detect, occurrences, personal_recurring
from .store import Store
from .util import add_months, clamp_day, month_firsts_between

PCTS = (10, 25, 50, 75, 90)
_EPS = 0.01


@dataclass
class LoanOutcome:
    account: Account
    payment: float
    payoff: date | None          # None = not within horizon
    interest_paid: float


@dataclass
class GoalOutcome:
    goal: Goal
    already_met: bool
    det: date | None             # deterministic crossing
    early: date | None           # optimistic (P90 path band)
    late: date | None            # conservative (P10 path band)


@dataclass
class ForecastResult:
    dates: list[date]
    networth_det: np.ndarray
    cash_det: np.ndarray
    networth_pct: dict[int, np.ndarray]
    cash_pct: dict[int, np.ndarray]
    loans: list[LoanOutcome]
    goal_outcomes: list[GoalOutcome]
    warnings: list[str] = field(default_factory=list)
    monthly_recurring_net: float = 0.0
    # P(cash dips below a floor at any point in the horizon), keyed by floor $.
    # Computed from the raw paths inside run_forecast; storing the answer rather
    # than every path keeps the result object small.
    cash_breach: dict[str, float] = field(default_factory=dict)
    min_cash_pct: dict[int, float] = field(default_factory=dict)
    first_breach_day: int | None = None
    runway_months: float | None = None      # cash / essential spend per month
    as_of: date | None = None               # None = projected from today
    # How bad the dips actually are, as opposed to how often they happen.
    shortfall: "Shortfall | None" = None
    history_weeks: int = 0
    history_first: date | None = None
    history_last: date | None = None
    recurring_refitted: bool = False
    # Unscaled monthly inputs for explaining scenario sliders. They use the
    # same schedules and week weights as this run, before slider multipliers.
    monthly_income_reference: float = 0.0
    monthly_spending_reference: dict[str, float] = field(default_factory=dict)
    goal_plan_budget: float | None = None
    spending_plan_applied: bool = False
    scheduled: list["ScheduledFlow"] = field(default_factory=list)
    # Earmarks change what is available to spend, not the bank balance or net
    # worth. Keep them separate so every cash-risk measure still uses holdings.
    reserved_cash: np.ndarray | None = None

    @property
    def free_cash_det(self) -> np.ndarray:
        return self.cash_det - (self.reserved_cash if self.reserved_cash is not None else 0.0)

    @property
    def free_cash_pct(self) -> dict[int, np.ndarray]:
        reserved = self.reserved_cash if self.reserved_cash is not None else 0.0
        # The allocation schedule is deterministic, so translating each
        # percentile is equivalent to translating the underlying cash paths.
        return {p: values - reserved for p, values in self.cash_pct.items()}


def token_set(s: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in s.upper()).split()
            if len(t) > 3}


def goal_plan_enabled(store: Store) -> bool:
    """Default app outlook follows a configured Goals ladder; scripts opt in."""
    configured = bool(store.goals()) or store.get_setting('plan_surplus') is not None
    return bool(store.get_setting('forecast_goal_plan', configured))


def auto_link_loans(store: Store) -> int:
    """Best-effort linkage of detected loan payments to loan accounts."""
    loans = [a for a in store.accounts() if a.type == AccountType.LOAN]
    items = [it for it in store.recurring() if it.kind == "loan_payment"]
    linked = 0
    unlinked = [it for it in items if it.linked_account_id is None]
    for it in unlinked:
        matches = [l for l in loans if token_set(it.name) & token_set(l.name)]
        if len(matches) == 1:
            store.update_recurring(it.id, linked_account_id=matches[0].id)
            linked += 1
    unlinked = [it for it in store.recurring()
                if it.kind == "loan_payment" and it.linked_account_id is None]
    if len(loans) == 1 and len(unlinked) == 1:
        store.update_recurring(unlinked[0].id, linked_account_id=loans[0].id)
        linked += 1
    return linked


def _crossing(dates: list[date], series: np.ndarray, target: float) -> date | None:
    hit = np.nonzero(series >= target)[0]
    return dates[int(hit[0])] if hit.size else None


# Cash floors the projection is scored against. The starter buffer is the
# widely-taught "first $1,000 before anything else" milestone; the emergency
# target is months of essential spending and is supplied by the caller.
STARTER_BUFFER = 1000.0


# ===========================================================================
# Shortfall — is this running out of money, or is it a Tuesday?
# ===========================================================================
# "P(cash goes below zero at some point in three years)" is a bad headline on
# its own, and it produced a genuinely wrong one: $10 in the account, a $15 bill
# on Thursday and a new job paying $100k a month from now on scores a 100%
# chance of going negative, and the app said *high risk of running out of cash*
# about a person who is about to be extremely comfortable. The probability was
# right. The sentence was not.
#
# What separates the two cases is not how often the line is crossed but how far
# below it goes, for how long, and whether it comes back. A $5 dip for two days
# is a payment-timing problem — move the direct debit. A $5,000 dip that lasts a
# quarter is insolvency. Three measurements tell them apart:
#
#   depth     — how far under, against what a day of living actually costs
#   duration  — how many days the balance stays under
#   recovery  — whether it is still under at the end of the horizon
#
# A dip is *material* when it is worth more than a few days of essential
# spending. Below that it is a scheduling detail, and calling it a shortfall
# trains people to ignore the warning that matters.
MATERIAL_MONTHS = 0.1                   # ≈ three days of essentials
MATERIAL_MIN = 100.0                    # floor, for a ledger with no essentials
SUSTAINED_DAYS = 21                     # three weeks under is not a timing gap


@dataclass
class Shortfall:
    """A verdict on the dips, not just a count of them.

    ``level`` drives the wording and the colour, and is deliberately coarse:

    * ``ok``       — the paths hold up.
    * ``thin``     — never negative, but the buffer keeps getting eaten.
    * ``brief``    — it does go under, by trivial amounts, and comes straight
                     back. The case the old headline got wrong.
    * ``watch``    — real dips, or lasting ones, but not most of the time.
    * ``crunch``   — a genuine squeeze that genuinely clears. Deep enough to
                     bounce a payment, shallow in time, and followed by
                     recovery. Worth a date and a plan; not worth the word
                     "risk of running out".
    * ``high``     — deep *and* lasting, or still under at the end. The only
                     level that means what it sounds like.
    """
    prob_any: float = 0.0           # P(cash < 0 at any point)
    prob_material: float = 0.0      # P(dips deeper than `material`)
    prob_sustained: float = 0.0     # P(under for ≥ SUSTAINED_DAYS)
    prob_unrecovered: float = 0.0   # P(still under on the last day)
    median_depth: float = 0.0       # typical worst dip, among paths that dip
    median_days: float = 0.0        # typical days spent under, same paths
    low_day: int | None = None      # typical day index of the low point
    low_date: date | None = None    # …as a calendar date, when known
    end_cash_p50: float = 0.0
    end_cash_p10: float = 0.0
    material: float = MATERIAL_MIN
    level: str = "ok"
    headline: str = ""
    detail: str = ""

    @property
    def recovers(self) -> bool:
        """Does the money come back on its own?"""
        return self.level in ("ok", "thin", "brief", "crunch")

    @property
    def severe(self) -> bool:
        return self.level == "high"


def _money(v: float) -> str:
    """Local money formatting — forecast.py stays free of UI imports."""
    return f"${abs(v):,.0f}" if abs(v) >= 10 else f"${abs(v):,.2f}"


def assess_shortfall(cash_paths: np.ndarray, starter_breach: float = 0.0,
                     essential_monthly: float | None = None,
                     horizon_days: int | None = None,
                     dates: list[date] | None = None) -> Shortfall:
    """Classify the dips in a set of simulated cash paths.

    Separated from ``run_forecast`` so it can be tested against hand-built
    paths — the interesting cases (a $5 dip in front of a fortune, a squeeze
    that clears, a slow slide that never returns) are far easier to construct
    directly than to coax a demo ledger into producing.
    """
    material = max(MATERIAL_MIN,
                   float(essential_monthly or 0.0) * MATERIAL_MONTHS)
    paths = np.atleast_2d(np.asarray(cash_paths, dtype=float))
    if paths.size == 0:
        s = Shortfall(material=material)
        _narrate(s, horizon_days)
        return s
    below = paths < 0.0
    dips = below.any(axis=1)
    path_min = paths.min(axis=1)
    days_below = below.sum(axis=1)
    end_cash = paths[:, -1]

    s = Shortfall(
        prob_any=float(dips.mean()),
        prob_material=float((path_min < -material).mean()),
        prob_sustained=float((days_below >= SUSTAINED_DAYS).mean()),
        prob_unrecovered=float((end_cash < 0.0).mean()),
        material=material,
        end_cash_p50=float(np.percentile(end_cash, 50)),
        end_cash_p10=float(np.percentile(end_cash, 10)),
    )
    if dips.any():
        s.median_depth = float(np.median(-path_min[dips]))
        s.median_days = float(np.median(days_below[dips]))
        s.low_day = int(np.median(np.argmin(paths[dips], axis=1)))
        if dates and 0 <= s.low_day < len(dates):
            s.low_date = dates[s.low_day]

    # Depth and duration are separate axes and the verdict needs both. Depth
    # alone would call a fortnight of being $600 down the same thing as a slow
    # two-year slide into insolvency; duration alone would wave through a
    # single catastrophic month. "Running out of money" is the intersection —
    # deep *and* not coming back — and everything short of it gets a name that
    # says what is actually happening instead.
    lasting = s.prob_sustained >= 0.25 or s.prob_unrecovered >= 0.10
    deep = s.prob_material
    # Is the typical dip itself trivial? Frequency and severity are independent
    # and both directions of the confusion are wrong: an ever-present $5 dip is
    # not a crisis, and a rare 53-day $1,600 hole is not "brief" merely because
    # only one path in ten falls into it. The population thresholds above sort
    # on how *often*; this sorts on how *badly*, and nothing is called brief
    # unless it earns the word on its own terms.
    trivial = s.median_depth <= s.material and s.median_days < SUSTAINED_DAYS
    if s.prob_unrecovered >= 0.25 or (deep >= 0.15 and lasting):
        s.level = "high"
    elif deep >= 0.5:
        s.level = "crunch"          # it hurts, it is dated, and it ends
    elif deep >= 0.15 or lasting:
        s.level = "watch"
    elif s.prob_any >= 0.10:
        s.level = "brief" if trivial else "watch"
    elif starter_breach >= 0.35:
        s.level = "thin"
    else:
        s.level = "ok"

    _narrate(s, horizon_days)
    return s


def _narrate(s: Shortfall, horizon_days: int | None) -> None:
    """Plain-English headline and body for a verdict.

    Written here rather than in the page because the wording *is* the finding —
    the numbers are the same either way, and the whole bug this replaces was a
    sentence, not an arithmetic error.
    """
    yrs = (horizon_days or 0) / 365.0
    when = (f"in {yrs:.0f} years' time" if yrs >= 1.6 else
            "in a year's time" if yrs >= 0.9 else
            "by the end of this projection")
    amount = _money(s.end_cash_p50) + ("" if s.end_cash_p50 >= 0
                                       else " in the red")
    ends = f"The middle of the range is {amount} {when}."

    around = (f" around {s.low_date:%B %Y}" if s.low_date else "")
    days = (f"{s.median_days:.0f} day"
            f"{'s' if round(s.median_days) != 1 else ''}")

    if s.level == "high":
        s.headline = "High risk of running out of cash."
        bits = [f"{s.prob_material:.0%} of simulated runs dip more than "
                f"{_money(s.material)} below zero"]
        if s.prob_sustained >= 0.15:
            bits.append(f"{s.prob_sustained:.0%} stay under for three weeks or "
                        f"more")
        if s.prob_unrecovered >= 0.05:
            bits.append(f"{s.prob_unrecovered:.0%} are still under water on the "
                        f"last day — those ones do not recover on their own")
        s.detail = ("; ".join(bits) + ". " + ends + " This is the case worth "
                    "acting on: a lower bill, a delayed purchase or more income "
                    "all move it.")
    elif s.level == "crunch":
        # The squeeze that clears. Deep enough to bounce a payment and worth a
        # date on the calendar, but calling it "running out of cash" is how a
        # perfectly solvent person gets told they are in trouble.
        s.headline = "A tight patch, then it clears."
        s.detail = (f"Most runs go short{around} — typically about "
                    f"{_money(s.median_depth)} below zero for {days} — and then "
                    f"recover. Those weeks are a real problem in their own "
                    f"right, because payments bounce and cards decline, but "
                    f"they are not where this ends up: {ends[0].lower()}"
                    f"{ends[1:]} Bridging the gap is usually enough — delay one "
                    f"purchase, or move a bill to the other side of payday.")
    elif s.level == "watch":
        s.headline = "Worth watching — some runs go genuinely short."
        s.detail = (f"{s.prob_material:.0%} of runs dip more than "
                    f"{_money(s.material)} below zero, typically by "
                    f"{_money(s.median_depth)} for {days}{around}. Most come "
                    f"back. {ends}")
    elif s.level == "brief":
        s.headline = "A short dip, not a shortfall."
        s.detail = (f"Cash crosses zero in {s.prob_any:.0%} of runs, but only "
                    f"by about {_money(s.median_depth)} and for roughly "
                    f"{days}{around} before it recovers — less than a few days "
                    f"of essential spending. That is a timing problem, not "
                    f"running out of money: moving one payment usually settles "
                    f"it. {ends}")
    elif s.level == "thin":
        s.headline = "Cash gets thin, though it stays above zero."
        s.detail = (f"No run goes negative, but the buffer is repeatedly worn "
                    f"down. {ends}")
    else:
        s.headline = "Cash holds up across the simulations."
        s.detail = ends


# Student-t degrees of freedom for investment shocks. Real equity returns have
# fatter tails than a normal: crashes are more frequent than a Gaussian admits,
# so a normal-innovation GBM quietly understates how bad the bad paths get.
# nu = 5 is the usual compromise — clearly heavy-tailed, but with finite
# kurtosis so the variance rescaling below is well defined.
T_DOF = 5.0


# Behavioural weeks decay with the same half-life the habits score uses, so
# "recent" means one thing across the whole app — but only once there is reason
# to believe the recent weeks are describing someone different. See
# `_behaviour_weights` for why that condition matters.
BEHAVIOUR_HALF_LIFE_WEEKS = 13.0        # ~3 months, matching insights
CHANGE_TAIL_FRACTION = 1.0 / 3.0        # recent share tested against the rest
# Per side, and deliberately high. A difference that is real *in sample* need
# not persist: the balanced demo's 38-week backtest window shows a genuine
# shift at Cohen's d = 1.40 which is simply mean-reversion, and forecasting
# from it dropped coverage from 61% to 10%. Requiring roughly four months of
# sustained new behaviour, against at least as much baseline, is what separates
# a changed habit from a quiet quarter. It also means the mechanism cannot
# engage at all until there is about a year of history, which is the right
# trade: rewriting the model of someone's behaviour on thin data is worse than
# not doing it.
CHANGE_MIN_WEEKS = 16
CHANGE_ALPHA = 0.01
CHANGE_MIN_EFFECT = 0.8                 # Cohen's d — a *large* effect, not any
_CHANGE_PERMUTATIONS = 4000


def _recency_weights(n: int,
                     half_life: float = BEHAVIOUR_HALF_LIFE_WEEKS) -> np.ndarray:
    """Normalised sampling weights for `n` chronologically ordered weeks.

    The newest week has weight 1 and each older one halves every ``half_life``
    weeks, so a year-old week counts about a sixteenth of last week's. Passing
    a non-positive half-life returns uniform weights.
    """
    if n <= 0:
        return np.ones(0)
    if not half_life or half_life <= 0:
        return np.full(n, 1.0 / n)
    age = np.arange(n - 1, -1, -1, dtype=float)     # oldest first, newest = 0
    w = 0.5 ** (age / float(half_life))
    total = w.sum()
    return w / total if total > 0 else np.full(n, 1.0 / n)


def _behaviour_changed(obs_net: np.ndarray, seed: int = 0) -> bool:
    """Has the recent third of the sample stopped resembling the rest?

    A permutation test on weekly net flow: reshuffle which weeks belong to
    which period and ask how often chance alone produces a gap this large. One
    test on one pre-declared split, so there is no multiple-comparisons problem
    to answer for.

    Significance alone is not the bar. Acting on a *statistically* detectable
    but small shift is how a stable ledger ends up reweighted on noise — which
    it did: an early version fired on the balanced demo's 38-week window and
    dropped backtest coverage from 61% to 10%. Rewriting the model of someone's
    behaviour is a large intervention, so it needs a large effect (Cohen's
    d ≥ 0.8) at a strict alpha, on a decent sample, before it is worth doing.
    """
    n = len(obs_net)
    k = int(round(n * CHANGE_TAIL_FRACTION))
    if k < CHANGE_MIN_WEEKS or n - k < CHANGE_MIN_WEEKS:
        return False
    prior, recent = obs_net[:-k], obs_net[-k:]
    observed = abs(float(recent.mean() - prior.mean()))
    if observed <= 0:
        return False
    pooled_sd = float(np.sqrt(
        (np.var(prior, ddof=1) + np.var(recent, ddof=1)) / 2.0))
    if pooled_sd <= 0 or observed / pooled_sd < CHANGE_MIN_EFFECT:
        return False
    rng = np.random.default_rng(seed)
    pooled = np.concatenate([prior, recent])
    idx = np.argsort(rng.random((_CHANGE_PERMUTATIONS, n)), axis=1)
    shuffled = pooled[idx]
    diffs = shuffled[:, -k:].mean(axis=1) - shuffled[:, :-k].mean(axis=1)
    p = float((np.abs(diffs) >= observed - 1e-12).mean())
    return p < CHANGE_ALPHA


def _behaviour_weights(obs_net: np.ndarray, seed: int = 0) -> np.ndarray:
    """Sampling weights over historical weeks — uniform unless habits moved.

    Stationarity is the right default and reweighting unconditionally is not a
    free improvement: on a stable ledger it shrinks the effective sample,
    letting a couple of unusual recent weeks drag the whole projection, and
    measurably *worsened* backtest coverage on the demo data (61% → 10% inside
    the nominal 80% band). Applied only when the recent third of the sample is
    statistically distinguishable from the rest, it fixes the case it exists
    for — someone who halved their spending five months ago being forecast as
    the average of who they were and who they are — and leaves everyone else
    exactly as they were.
    """
    n = len(obs_net)
    if n < 4 or not _behaviour_changed(obs_net, seed=seed):
        return np.full(n, 1.0 / n) if n else np.ones(0)
    return _recency_weights(n)


def _shocks(rng, shape, dof: float = T_DOF) -> np.ndarray:
    """Standardised heavy-tailed innovations (unit variance).

    A Student-t with nu dof has variance nu/(nu-2); dividing by its square root
    keeps the calibrated volatility of each account intact while redistributing
    probability mass into the tails. Set dof <= 2 to fall back to normal.
    """
    if dof is None or dof <= 2:
        return rng.standard_normal(shape)
    return rng.standard_t(dof, size=shape) / np.sqrt(dof / (dof - 2.0))


@cached_read
def _prepared_history(store, today, history, recurring_override):
    """History and fitted schedules shared by every scenario in an analysis."""
    ledger = store.transactions()
    tx = history.transactions(ledger, today)
    saved = recurring_override if recurring_override is not None else store.recurring()
    saved = personal_recurring(ledger, saved, as_of=today)
    known_keys = {it.key for it in saved}
    items, warnings = history.patterns(tx, saved, today)
    return tx, items, known_keys, warnings


@cached_read
def _goal_payment_plan(store, required, horizon_months):
    from .goals import PLAN_HORIZON_MONTHS, build_plan, targets
    minimums = dict(required)
    items = [replace(t, required=minimums[t.key]) if t.key in minimums else t
             for t in targets(store)]
    return build_plan(store, target_overrides=items, surplus=store.get_setting('plan_surplus'),
                      horizon_months=max(PLAN_HORIZON_MONTHS, horizon_months))


def _reserved_cash(store, dates, goal_plan, spending_plan) -> np.ndarray:
    """Recorded cash earmarks plus intended future savings, without outflows.

    Unlocated savings are conservatively treated as cash claims. Located
    investment holdings stay outside cash. Balance milestones overlap household
    holdings and do not create a second earmark. Purchases already recorded are
    progress toward the target, not money still reserved.
    """
    from .funding import goal_buckets
    investments = {a.id for a in store.accounts() if a.type == AccountType.INVESTMENT}
    reserved = sum(amount for g in store.goals() if g.kind == 'savings'
                   for aid, amount in goal_buckets(store, g.id).items()
                   if aid not in investments)
    result = np.full(len(dates), reserved, dtype=float)
    if spending_plan is not None:
        # Match the daily treatment of budget amounts without a bill date.
        result += np.arange(len(dates)) * spending_plan.amounts['reserve'] / 30.44
    if goal_plan is not None:
        firsts = month_firsts_between(dates[0], dates[-1])
        for allocation in goal_plan.allocations:
            target = allocation.target
            if target.kind != 'goal' or target.goal_kind != 'savings':
                continue
            progress = goal_plan.progress[target.key]
            previous = target.progress
            for when, fraction in zip(firsts, progress):
                funded = target.amount * fraction
                result[(when - dates[0]).days:] += max(0.0, funded - previous)
                previous = funded
    return result


@cached_read
def run_forecast(store: Store, horizon_days: int, n_paths: int,
                 scenario: Scenario | None = None, seed: int = 7,
                 emergency_target: float | None = None,
                 essential_monthly: float | None = None,
                 as_of: date | None = None,
                 recurring_override: list[RecurringItem] | None = None,
                 dof: float = T_DOF,
                 history: ForecastHistory | None = None) -> ForecastResult:
    """Project cash and net worth.

    ``emergency_target`` is the months-of-essentials cash buffer to score paths
    against; pass None to skip that floor. ``essential_monthly`` enables the
    runway figure.

    ``as_of`` projects from a past date instead of today, using only data that
    existed then — the machinery behind the backtest. ``recurring_override``
    supplies patterns re-detected as of that date, so the projection doesn't
    quietly benefit from knowing how the patterns turned out.

    ``history`` overrides the database's saved history selection for this run.
    A short selection never falls back to older weeks.
    """
    scenario = scenario or Scenario()
    warnings: list[str] = []
    today = as_of or date.today()
    follow_goals = scenario.follow_goal_plan and as_of is None
    history = history or ForecastHistory.from_setting(store.get_setting(HISTORY_SETTING))
    tx, all_items, known_keys, history_warnings = _prepared_history(
        store, today, history, recurring_override)
    H = int(horizon_days)
    n = H + 1                                  # index 0 = today
    dates = [today + timedelta(days=i) for i in range(n)]
    end = dates[-1]
    scheduled: list[ScheduledFlow] = []

    def idx(d: date) -> int:
        return (d - today).days

    accounts = current_accounts(store) if as_of is None else store.accounts()
    hist = networth_history(store)
    if len(hist) == 0:
        cash0 = 0.0
    elif as_of is None:
        cash0 = float(hist["cash"].iloc[-1])
    else:
        # start from the balance actually held on that date, not today's
        ts = pd.Timestamp(as_of)
        cash0 = float(hist["cash"].asof(ts)) if ts >= hist.index[0] else \
            float(hist["cash"].iloc[0])

    warnings.extend(history_warnings)
    items = [it for it in all_items if it.active and it.kind != "ignore"]
    loan_accounts = {a.id: a for a in accounts if a.type == AccountType.LOAN}
    linked_payments: dict[int, list[RecurringItem]] = {}
    flow_items: list[RecurringItem] = []
    for it in items:
        if it.kind == "loan_payment" and it.linked_account_id in loan_accounts:
            if it.amount < 0:
                linked_payments.setdefault(it.linked_account_id, []).append(it)
        elif it.kind == "loan_payment" and loan_accounts:
            # An unlinked loan payment is almost certainly the same money the
            # amortization engine is already withdrawing for one of the loan
            # accounts. Replaying it here as well double-charged cash every
            # month. Drop it and tell the user to link it explicitly.
            warnings.append(
                f"“{it.name}” looks like a loan payment but isn't linked to a "
                f"loan account, so it was left out of the cash projection to "
                f"avoid counting it twice. Link it under Bills & payday → Detected bills & pay."
            )
        else:
            flow_items.append(it)

    # Establish unscaled monthly inputs before applying a saved budget. Budget
    # ratios scale both recurring bills and sampled weeks, preserving their
    # dates and historical variation. Never apply today's plan to a backtest.
    spending_plan = None
    if follow_goals and store.get_setting('spending_plan') is not None:
        from .spending_plan import calculate_plan
        spending_plan = calculate_plan(store)
    exclude = known_keys | {it.key for it in all_items}
    W, irr_inc = store.cached(('forecast_weeks', today, history, frozenset(exclude)),
        lambda: weekly_category_matrix(
            tx, exclude_keys=exclude,
            max_weeks=78 if history.mode == "auto" else None,
            start_date=history.start(today),
            end_date=today if history.mode != "auto" else None))
    wts = None
    if len(W) >= 4:
        original_mvec = np.array([scenario.category_multipliers.get(c, 1.0) for c in W.columns])
        obs = (irr_inc.values - W.values.sum(axis=1) if spending_plan else
               irr_inc.values * scenario.income_multiplier - W.values @ original_mvec)
        wts = _behaviour_weights(obs, seed=seed)
    monthly_income_reference = sum(
        it.amount * (30.44 / _cad_days(it)) for it in flow_items if it.kind == "income")
    monthly_spending_reference: dict[str, float] = {}
    for it in flow_items:
        if it.kind != "income" and it.amount < 0:
            cat = categorize(it.name, it.amount)
            monthly_spending_reference[cat] = (monthly_spending_reference.get(cat, 0.0)
                                             - it.amount * (30.44 / _cad_days(it)))
    if wts is not None:
        monthly_income_reference += float(np.dot(wts, irr_inc.values)) * (30.44 / 7)
        for cat, amount in zip(W.columns, wts @ W.values):
            monthly_spending_reference[cat] = (monthly_spending_reference.get(cat, 0.0)
                                             + float(amount) * (30.44 / 7))
    income_factor = scenario.income_multiplier
    category_factors = dict(scenario.category_multipliers)
    base_flow = np.zeros(n)
    if spending_plan is not None:
        from .spending_plan import forecast_targets
        planned = forecast_targets(spending_plan, monthly_spending_reference)
        # Without tracked loans, a budgeted debt payment is a cash outflow;
        # tracked loans are handled once by the amortization engine below.
        if not loan_accounts:
            planned['Debt payment'] = spending_plan.amounts['debt']
        else:
            planned['Debt payment'] = 0.0
        desired_income = spending_plan.amounts['income']
        if monthly_income_reference > 0:
            income_factor *= desired_income / monthly_income_reference
        else:
            base_flow[1:] += desired_income * income_factor / 30.44
        for cat in sorted(set(planned) | set(monthly_spending_reference)):
            target = planned.get(cat, 0.0)
            reference = monthly_spending_reference.get(cat, 0.0)
            adjustment = scenario.category_multipliers.get(cat, 1.0)
            if reference > 0:
                category_factors[cat] = adjustment * target / reference
            else:
                category_factors[cat] = 0.0
                base_flow[1:] -= target * adjustment / 30.44
        monthly_income_reference = desired_income
        monthly_spending_reference = planned
        if spending_plan.remaining(store.get_setting('plan_surplus') or 0) < -.005:
            warnings.append('The saved monthly Plan allocates more than its income. '
                            'Goal allocations are intentions, not evidence that the cash is available.')

    # ---- 1. deterministic recurring flows ------------------------------
    monthly_net = 0.0
    for it in flow_items:
        factor = income_factor if it.kind == 'income' else category_factors.get(
            categorize(it.name, it.amount), 1.0)
        amt = it.amount * factor
        monthly_net += amt * (30.44 / _cad_days(it))
        for d in occurrences(it, today, end):
            base_flow[idx(d)] += amt
            if abs(amt) > _EPS:
                source = 'loan' if it.kind == 'loan_payment' else 'recurring'
                scheduled.append(ScheduledFlow(it.name, d, amt, source, item_id=it.id))

    # ---- 2. loans -------------------------------------------------------
    loan_daily_total = np.zeros(n)
    loan_outcomes: list[LoanOutcome] = []
    sim_loans = []
    for a in sorted(loan_accounts.values(), key=lambda x: -x.apr):
        linked = linked_payments.get(a.id, [])
        if linked:
            pay = sum(abs(it.monthly_equivalent) for it in linked)
            li = linked[0]
            day = li.days_of_month[0] if li.days_of_month else li.last_date.day
        else:
            pay = a.monthly_payment
            day = a.payment_day
        b0 = abs(a.balance)
        if pay <= 0 and b0 > 0 and not follow_goals:
            r = a.apr / 12.0
            pay = b0 * r / (1 - (1 + r) ** -60) if r > 0 else b0 / 60.0
            warnings.append(
                f"{a.name}: no payment detected or configured; "
                f"assuming a 60-month amortizing payment of ${pay:,.0f}."
            )
        sim_loans.append({
            "acct": a, "bal": b0, "r": a.apr / 12.0, "base": pay, "day": int(day),
            "payoff": None, "interest": 0.0, "events": [],
        })

    goal_plan = None
    if follow_goals:
        # Keep this run's selected recurring-history inputs while applying the
        # saved allowance, order and allocation policy from Goals.
        required = tuple((f'loan:{l["acct"].id}', l['base']) for l in sim_loans)
        goal_plan = _goal_payment_plan(store, required, int(np.ceil(H / 28)) + 2)
        if spending_plan is not None and spending_plan.amounts['debt'] + .005 < sum(l['base'] for l in sim_loans):
            warnings.append('The Plan budgets less than the existing loan payments. '
                            'The forecast keeps those loan obligations; revise the budget on Plan.')
        for l in sim_loans:
            allocation = goal_plan.by_key(f'loan:{l["acct"].id}')
            if allocation is not None:
                l['base'] = allocation.target.required
            first = clamp_day(today.year, today.month, l['day'])
            if first <= today:
                next_month = add_months(today.replace(day=1), 1)
                first = clamp_day(next_month.year, next_month.month, l['day'])
            values = goal_plan.loan_payments.get(f'loan:{l["acct"].id}', [])
            for month, planned in enumerate(values):
                month_start = add_months(first.replace(day=1), month)
                when = clamp_day(month_start.year, month_start.month, l['day'])
                if when > end or l['bal'] <= _EPS:
                    break
                interest = l['bal'] * l['r']
                l['bal'] += interest
                l['interest'] += interest
                pay = min(max(0.0, planned), l['bal'])
                l['bal'] -= pay
                base_flow[idx(when)] -= pay
                if pay > _EPS:
                    scheduled.append(ScheduledFlow(l['acct'].name, when, -pay, 'loan'))
                l['events'].append((idx(when), l['bal']))
                if l['bal'] <= _EPS:
                    l['payoff'] = when
        warnings.extend(goal_plan.warnings)
        if scenario.extra_loan_payment:
            warnings.append('The Goals funding plan supplies extra loan payments; '
                            'the separate extra-loan control is not applied in this mode.')
    elif sim_loans:
        freed = 0.0
        y, m = today.year, today.month
        for _ in range(int(np.ceil(H / 28)) + 2):
            unpaid = [l for l in sim_loans if l["bal"] > _EPS]
            if not unpaid:
                break
            pool = scenario.extra_loan_payment + freed
            first_unpaid = unpaid[0]           # highest APR (list is sorted)
            for l in sim_loans:
                if l["bal"] <= _EPS:
                    continue
                pdate = clamp_day(y, m, l["day"])
                if pdate <= today:
                    continue
                interest = l["bal"] * l["r"]
                l["bal"] += interest
                l["interest"] += interest
                pay = l["base"] + (pool if l is first_unpaid else 0.0)
                pay = min(pay, l["bal"])
                l["bal"] -= pay
                if pdate <= end:
                    base_flow[idx(pdate)] -= pay
                    if pay > _EPS:
                        scheduled.append(ScheduledFlow(l['acct'].name, pdate, -pay, 'loan'))
                    l["events"].append((idx(pdate), l["bal"]))
                if l["bal"] <= _EPS and l["payoff"] is None:
                    l["payoff"] = pdate
                    if scenario.snowball:
                        freed += l["base"]
            nxt = add_months(date(y, m, 1), 1)
            y, m = nxt.year, nxt.month
    for l in sim_loans:
        arr = np.full(n, abs(l["acct"].balance))
        for i, b in sorted(l["events"]):
            arr[i:] = b
        loan_daily_total += arr
        loan_outcomes.append(LoanOutcome(
            account=l["acct"], payment=l["base"],
            payoff=l["payoff"], interest_paid=l["interest"],
        ))

    # ---- 2b. what-if events --------------------------------------------
    # Dated impulses, applied to cash on the day they land. Stored events and
    # scenario-only events are merged so the page can preview an event before
    # committing it.
    # Current saved hypotheticals were not known at an earlier backtest origin.
    events = (list(store.events(active_only=True)) if as_of is None else [])
    events += list(scenario.events or [])
    from .events import event_occurrences
    for ev in events:
        for when in event_occurrences(ev, today + timedelta(days=1), end):
            base_flow[idx(when)] += float(ev.amount)
            scheduled.append(ScheduledFlow(ev.label, when, float(ev.amount), 'event',
                                           event_id=getattr(ev, 'id', None)))

    # ---- 3. investments -------------------------------------------------
    inv_accounts = [a for a in accounts if a.type == AccountType.INVESTMENT]
    firsts = month_firsts_between(today, end)
    ev_idx = np.array([idx(d) for d in firsts], dtype=int)
    prev_dates = [today] + firsts[:-1]
    dt_years = np.array([(f - p).days for f, p in zip(firsts, prev_dates)]) / 365.25
    n_ev = len(firsts)

    rng = np.random.default_rng(seed)
    inv_det_daily = np.zeros(n)
    inv_paths_daily = np.zeros((n_paths, n))
    total_extra = scenario.extra_investing
    observed_contributions = sum(max(0.0, a.monthly_contribution) for a in inv_accounts)
    for k, a in enumerate(inv_accounts):
        planned_contrib = a.monthly_contribution
        if spending_plan is not None:
            share = (max(0.0, a.monthly_contribution) / observed_contributions
                     if observed_contributions else float(k == 0))
            planned_contrib = spending_plan.amounts['investing'] * share
        contrib = planned_contrib + (total_extra if k == 0 else 0.0)
        V0 = float(a.balance)
        mu, sig = a.annual_return, a.volatility
        # deterministic (sigma = 0)
        det_vals = np.empty(n_ev)
        v = V0
        for j in range(n_ev):
            v = v * np.exp(mu * dt_years[j]) + contrib
            det_vals[j] = v
        # Monte Carlo
        if n_ev:
            Z = _shocks(rng, (n_paths, n_ev), dof)
            growth = np.exp((mu - 0.5 * sig ** 2) * dt_years[None, :]
                            + sig * np.sqrt(dt_years)[None, :] * Z)
            mc = np.empty((n_paths, n_ev))
            v = np.full(n_paths, V0)
            for j in range(n_ev):
                v = v * growth[:, j] + contrib
                mc[:, j] = v
        else:
            mc = np.zeros((n_paths, 0))
        # debit contributions from cash
        for j in range(n_ev):
            base_flow[ev_idx[j]] -= contrib
            if abs(contrib) > _EPS:
                scheduled.append(ScheduledFlow(f'{a.name} contribution', firsts[j], -contrib, 'investment'))
        # step-expand month values onto the daily grid
        ptr = np.searchsorted(ev_idx, np.arange(n), side="right")  # 0..n_ev
        det_ext = np.concatenate(([V0], det_vals))
        inv_det_daily += det_ext[ptr]
        mc_ext = np.hstack([np.full((n_paths, 1), V0), mc])
        inv_paths_daily += mc_ext[:, ptr]
    if not inv_accounts and total_extra > 0:
        warnings.append("Extra investing is set but no investment account exists; "
                        "it was ignored.")
    if not inv_accounts and spending_plan is not None and spending_plan.amounts['investing'] > 0:
        warnings.append('The Plan includes investment contributions but no investment account '
                        'exists. Add one on Accounts; these contributions were not applied.')

    # ---- 4. discretionary behavior (bootstrap) --------------------------
    # Every *known* pattern is withheld from the behavioral sample, not just the
    # active ones. Excluding only active items meant deactivating a pattern (or
    # marking it 'ignore') quietly readmitted its history as variable spending,
    # so the control had almost no effect on the forecast.
    n_weeks = int(np.ceil(H / 7))
    if len(W) >= 4:
        cats = list(W.columns)
        mvec = np.array([category_factors.get(c, 1.0) for c in cats])
        Wv = W.values
        Iv = irr_inc.values * income_factor
        # Weeks are drawn by recency, not uniformly. Sampling all of history
        # with equal probability assumes habits are stationary, and when they
        # are not it projects an average of the person you were and the person
        # you are — someone who halved their spending five months ago was being
        # forecast at the midpoint of both, and told they would run out of money
        # while the Insights page (which *is* recency-weighted) correctly
        # reported a surplus. The two halves of the app now answer the question
        # the same way, with the same half-life.
        obs_net = Iv - Wv @ mvec
        pick = rng.choice(len(W), size=(n_paths, n_weeks), p=wts)
        net_w = Iv[pick] - Wv[pick] @ mvec                     # (paths, weeks)
        # Parameter uncertainty, not just sampling noise. Resampling weeks
        # independently makes week-to-week variation average out like sqrt(t),
        # so by a one-year horizon the band collapses to a few percent — it
        # implicitly asserts the historical mean weekly net is known exactly.
        # It isn't: it is estimated from the sample, with standard error
        # s/sqrt(n). Drawing one drift offset per path and holding it for the
        # whole path adds the term that grows *linearly* in t, which is what
        # actually dominates at long horizons. Backtesting without this showed
        # ~1% of days falling inside the nominal 80% band.
        #
        # With weighted draws the divisor is Kish's effective sample size,
        # 1/sum(w^2), not the raw week count: down-weighting old weeks means
        # fewer of them are really informing the mean, and using n would claim
        # a precision the sample no longer has.
        det_net_w = float(np.dot(wts, obs_net))
        n_eff = 1.0 / float(np.sum(wts ** 2))
        if n_eff > 1.0:
            var_w = float(np.dot(wts, (obs_net - det_net_w) ** 2))
            var_w *= n_eff / (n_eff - 1.0)              # small-sample correction
            se_mean = float(np.sqrt(max(var_w, 0.0) / n_eff))
        else:
            se_mean = 0.0
        net_w = net_w + rng.standard_normal((n_paths, 1)) * se_mean
        disc_paths = np.repeat(net_w / 7.0, 7, axis=1)[:, : n - 1]
        disc_det = np.full(n - 1, det_net_w / 7.0)
    else:
        disc_paths = np.zeros((n_paths, n - 1))
        disc_det = np.zeros(n - 1)
        if not tx.empty or history.mode != "auto":
            warnings.append("Under 4 complete weeks in the selected history — "
                            "variable day-to-day spending is not modeled. "
                            "Choose a longer period or import more recent data.")

    # ---- 5. assemble cash and net worth ---------------------------------
    cash_det = cash0 + np.concatenate(([0.0], np.cumsum(base_flow[1:] + disc_det)))
    cash_paths = cash0 + np.concatenate(
        [np.zeros((n_paths, 1)),
         np.cumsum(base_flow[None, 1:] + disc_paths, axis=1)], axis=1)

    networth_det = cash_det + inv_det_daily - loan_daily_total
    networth_paths = cash_paths + inv_paths_daily - loan_daily_total[None, :]

    # Partition each path matrix once for all requested percentiles.
    networth_pct = dict(zip(PCTS, np.percentile(networth_paths, PCTS, axis=0)))
    cash_pct = dict(zip(PCTS, np.percentile(cash_paths, PCTS, axis=0)))

    # --- shortfall risk -------------------------------------------------
    # A median line that stays positive can still hide a large fraction of
    # paths dipping negative on the way. The running minimum answers the
    # question people actually care about: "how likely am I to run out at any
    # point?" — not "where do I end up?".
    path_min = cash_paths.min(axis=1)
    min_cash_pct = {p: float(np.percentile(path_min, p)) for p in PCTS}
    floors = {"zero": 0.0, "starter": STARTER_BUFFER}
    if emergency_target and emergency_target > 0:
        floors["emergency"] = float(emergency_target)
    cash_breach = {k: float((path_min < v).mean()) for k, v in floors.items()}
    runway = None
    if essential_monthly and essential_monthly > 0:
        runway = max(0.0, cash0 / essential_monthly)
    shortfall = assess_shortfall(cash_paths,
                                 starter_breach=cash_breach.get("starter", 0.0),
                                 essential_monthly=essential_monthly,
                                 horizon_days=H, dates=dates)
    # Dated only when the dip is worth dating. "1 in 10 paths are underwater by
    # March" is alarming language for a balance that touches −$4 that week, and
    # the page quotes this line directly.
    below_zero = cash_paths < -shortfall.material
    first_breach_day = None
    if below_zero.any():
        # earliest day on which at least 10% of paths are materially underwater
        frac = below_zero.mean(axis=0)
        hits = np.nonzero(frac >= 0.10)[0]
        if len(hits):
            first_breach_day = int(hits[0])

    # ---- 6. goals -------------------------------------------------------
    goal_outcomes: list[GoalOutcome] = []
    for g in store.goals():
        # A purchase's earmarked savings are not a household balance threshold.
        # Loan priorities can change household cash, but an earmark is not a
        # household balance threshold. Its funding progress belongs on Goals.
        if g.kind == "savings":
            continue
        det_series, pct = ((cash_det, cash_pct) if g.kind == "cash"
                           else (networth_det, networth_pct))
        if det_series[0] >= g.amount:
            goal_outcomes.append(GoalOutcome(g, True, None, None, None))
            continue
        goal_outcomes.append(GoalOutcome(
            g, False,
            det=_crossing(dates, det_series, g.amount),
            early=_crossing(dates, pct[90], g.amount),
            late=_crossing(dates, pct[10], g.amount),
        ))

    return ForecastResult(
        dates=dates, networth_det=networth_det, cash_det=cash_det,
        networth_pct=networth_pct, cash_pct=cash_pct,
        loans=loan_outcomes, goal_outcomes=goal_outcomes,
        warnings=warnings, monthly_recurring_net=monthly_net,
        cash_breach=cash_breach, min_cash_pct=min_cash_pct,
        first_breach_day=first_breach_day, runway_months=runway, as_of=as_of,
        shortfall=shortfall,
        history_weeks=len(W),
        history_first=W.index[0].start_time.date() if len(W) else None,
        history_last=W.index[-1].end_time.date() if len(W) else None,
        recurring_refitted=history.refit_recurring,
        goal_plan_budget=goal_plan.surplus if goal_plan is not None else None,
        spending_plan_applied=spending_plan is not None,
        scheduled=sorted(scheduled, key=lambda flow: (flow.when, flow.amount)),
        monthly_income_reference=monthly_income_reference,
        monthly_spending_reference=monthly_spending_reference,
        # Past cutoffs must not inherit today's earmarks or saved budget.
        reserved_cash=(_reserved_cash(store, dates, goal_plan, spending_plan)
                       if as_of is None else np.zeros(n)),
    )


def _cad_days(it: RecurringItem) -> float:
    from .models import CADENCE_DAYS
    return CADENCE_DAYS[it.cadence]


# ===========================================================================
# What is actually causing the squeeze
# ===========================================================================
# The furthest back attribution will look. Beyond about six weeks a "cause" is
# no longer an event, it is a pattern of living.
MAX_CAUSE_WINDOW = 45


@dataclass
class ScheduledFlow:
    """One dated, known movement of money — a bill, a payment, a pay cheque.

    The projection already replays all of these to build its cash path; it just
    throws away which was which. Keeping them lets the page answer the question
    a dated warning immediately provokes — *caused by what?* — with names and
    dates instead of a shrug, and lets the answer be acted on: knowing that the
    low point is rent landing two days before payday is knowing the fix.
    """
    label: str
    when: date
    amount: float                    # signed; negative is money out
    source: str                      # recurring | loan | event | investment
    item_id: int | None = None
    event_id: int | None = None

    @property
    def is_out(self) -> bool:
        return self.amount < 0

    @property
    def movable(self) -> bool:
        """Can this be shifted by a few days without inventing anything?

        A bill or a subscription can usually be moved; a pay cheque cannot, and
        an investment contribution is already the user's own choice made
        elsewhere.
        """
        return self.source in ("recurring", "event") and self.is_out


def scheduled_flows(store: Store, start: date, end: date,
                    scenario: Scenario | None = None) -> list[ScheduledFlow]:
    """Every dated flow the projection knows about in a window, by date.

    Deterministic only — recurring patterns, loan payments, investment
    contributions and what-if event occurrences. Bootstrapped day-to-day spending is
    deliberately absent: it has no date and no name, so it cannot be pointed at
    or moved, and listing "about $60 of groceries" beside a $1,795 rent payment
    would suggest the two are equally addressable.
    """
    scenario = scenario or Scenario()
    if scenario.follow_goal_plan:
        # Use the engine's actual schedule, including budget scaling, planned
        # loan extras, payoff caps and investment contributions. Reconstructing
        # it separately from Accounts used to contradict the plotted cash dip.
        horizon = (end - date.today()).days
        if horizon <= 0:
            return []
        result = run_forecast(store, horizon, 1, scenario)
        return [flow for flow in result.scheduled if start <= flow.when <= end]
    accounts = store.accounts()
    loans = {a.id: a for a in accounts if a.type == AccountType.LOAN}
    out: list[ScheduledFlow] = []
    history = ForecastHistory.from_setting(store.get_setting(HISTORY_SETTING))
    ledger = store.transactions()
    patterns = personal_recurring(ledger, store.recurring())
    patterns, _ = history.patterns(ledger, patterns, date.today())

    # Only *active* patterns count as covering a loan, exactly as the
    # projection decides it. Deactivating a detected payment does not stop the
    # loan being amortised — the engine falls back to the account's own
    # monthly_payment — so treating the account as covered here dropped the
    # largest fixed outflow of the month out of the attribution list.
    linked = {it.linked_account_id for it in patterns
              if it.kind == "loan_payment" and it.active
              and it.linked_account_id is not None}
    for it in patterns:
        if not it.active or it.kind == "ignore":
            continue
        if it.kind == "loan_payment" and loans and it.linked_account_id not in loans:
            continue                 # dropped by the projection too, as a double-count
        if it.kind == "income":
            amt = it.amount * scenario.income_multiplier
        else:
            amt = it.amount * scenario.category_multipliers.get(
                categorize(it.name, it.amount), 1.0)
        src = "loan" if it.kind == "loan_payment" else "recurring"
        for d in occurrences(it, start - timedelta(days=1), end):
            out.append(ScheduledFlow(it.name, d, amt, src, item_id=it.id))

    # Loans with no detected payment: the projection amortises them anyway, so
    # they have to appear here or the largest outflow of the month is missing.
    for a in loans.values():
        if a.id in linked or a.monthly_payment <= 0:
            continue
        d = clamp_day(start.year, start.month, int(a.payment_day))
        while d <= end:
            if d >= start:
                out.append(ScheduledFlow(a.name, d, -abs(a.monthly_payment),
                                         "loan"))
            nxt = add_months(date(d.year, d.month, 1), 1)
            d = clamp_day(nxt.year, nxt.month, int(a.payment_day))

    for a in accounts:
        if a.type == AccountType.INVESTMENT and a.monthly_contribution > 0:
            for d in month_firsts_between(start, end):
                out.append(ScheduledFlow(f"{a.name} contribution", d,
                                         -abs(a.monthly_contribution),
                                         "investment"))

    from .events import event_occurrences
    for ev in list(store.events(active_only=True)) + list(scenario.events or []):
        for when in event_occurrences(ev, start, end):
            out.append(ScheduledFlow(ev.label, when, float(ev.amount),
                                     "event", event_id=getattr(ev, "id", None)))

    out.sort(key=lambda f: (f.when, f.amount))
    return out


def crunch_causes(store: Store, low_date: date, days_under: float = 7.0,
                  scenario: Scenario | None = None,
                  lead_days: int = 14) -> tuple[list[ScheduledFlow],
                                                ScheduledFlow | None]:
    """The dated outflows that dig the hole, and the next money in after it.

    Returns ``(outflows_largest_first, next_inflow)``. The window reaches back
    far enough to catch the run-up rather than only the day itself, because a
    low point is the *end* of a sequence of withdrawals, not a single event.

    It is also capped, hard. Attribution only means anything for a *bounded*
    dip: ask this about a ledger that has been underwater for three years and
    the honest answer is "every rent payment since 2026", which is true, is not
    a cause, and is not something anyone can move. A structural shortfall is a
    question for the sensitivity panel, not for this.
    """
    span = min(max(lead_days, int(round(days_under)) + 7), MAX_CAUSE_WINDOW)
    start = low_date - timedelta(days=span)
    end = low_date + timedelta(days=45)
    flows = scheduled_flows(store, start, end, scenario)
    causes = sorted((f for f in flows if f.is_out and f.when <= low_date),
                    key=lambda f: f.amount)
    nxt = next((f for f in flows if not f.is_out and f.when > low_date), None)
    return causes, nxt


# ===========================================================================
# Sensitivity — which lever actually matters
# ===========================================================================
@dataclass
class Sensitivity:
    """One lever's effect on the projection when nudged either way."""
    lever: str
    kind: str                 # 'category' | 'income' | 'loan' | 'invest'
    down_delta: float         # $ change in the metric when the lever falls
    up_delta: float           # $ change when it rises
    swing: float              # total span — the ranking key

    @property
    def worst(self) -> float:
        return min(self.down_delta, self.up_delta)

    @property
    def best(self) -> float:
        return max(self.down_delta, self.up_delta)


def sensitivity(store: Store, base_scenario: Scenario, horizon_days: int,
                categories: list[str], delta: float = 0.20,
                metric: str = "networth", step_dollars: float = 200.0
                ) -> list[Sensitivity]:
    """Tornado analysis: perturb each lever independently, rank by swing.

    Uses only the deterministic path (n_paths=1), which is exact for this
    purpose — sensitivity is about the mean response to a lever, and Monte
    Carlo noise would swamp small differences without adding information.

    Sliders move by a relative ``delta``; the dollar levers (extra loan
    payment, extra investing) move by ``step_dollars``, since a percentage of
    zero is zero and those usually start at zero.
    """
    def end_value(sc: Scenario) -> float:
        r = run_forecast(store, horizon_days, 1, sc)
        arr = r.networth_det if metric == "networth" else r.cash_det
        return float(arr[-1])

    def clone(**over) -> Scenario:
        return Scenario(
            category_multipliers=dict(over.pop(
                "category_multipliers", base_scenario.category_multipliers)),
            income_multiplier=over.pop("income_multiplier",
                                       base_scenario.income_multiplier),
            extra_loan_payment=over.pop("extra_loan_payment",
                                        base_scenario.extra_loan_payment),
            extra_investing=over.pop("extra_investing",
                                     base_scenario.extra_investing),
            snowball=base_scenario.snowball,
            events=list(base_scenario.events or []),
            follow_goal_plan=base_scenario.follow_goal_plan,
        )

    baseline = end_value(clone())
    out: list[Sensitivity] = []

    for cat in categories:
        cur = base_scenario.category_multipliers.get(cat, 1.0)
        lo = dict(base_scenario.category_multipliers)
        hi = dict(base_scenario.category_multipliers)
        lo[cat] = max(0.0, cur * (1 - delta))
        hi[cat] = cur * (1 + delta)
        d_lo = end_value(clone(category_multipliers=lo)) - baseline
        d_hi = end_value(clone(category_multipliers=hi)) - baseline
        out.append(Sensitivity(f"{cat} spend", "category", d_lo, d_hi,
                               abs(d_hi - d_lo)))

    inc = base_scenario.income_multiplier
    d_lo = end_value(clone(income_multiplier=max(0.0, inc * (1 - delta)))) - baseline
    d_hi = end_value(clone(income_multiplier=inc * (1 + delta))) - baseline
    out.append(Sensitivity("Income", "income", d_lo, d_hi, abs(d_hi - d_lo)))

    if not base_scenario.follow_goal_plan and any(a.type == AccountType.LOAN for a in store.accounts()):
        base_pay = base_scenario.extra_loan_payment
        d_lo = end_value(clone(extra_loan_payment=max(
            0.0, base_pay - step_dollars))) - baseline
        d_hi = end_value(clone(
            extra_loan_payment=base_pay + step_dollars)) - baseline
        out.append(Sensitivity(f"Extra loan payment ±{fmt_step(step_dollars)}",
                               "loan", d_lo, d_hi, abs(d_hi - d_lo)))

    if any(a.type == AccountType.INVESTMENT for a in store.accounts()):
        base_inv = base_scenario.extra_investing
        d_lo = end_value(clone(extra_investing=max(
            0.0, base_inv - step_dollars))) - baseline
        d_hi = end_value(clone(
            extra_investing=base_inv + step_dollars)) - baseline
        out.append(Sensitivity(f"Extra investing ±{fmt_step(step_dollars)}",
                               "invest", d_lo, d_hi, abs(d_hi - d_lo)))

    out.sort(key=lambda s: -s.swing)
    return out


def fmt_step(v: float) -> str:
    return f"${v:,.0f}/mo"


# ===========================================================================
# Backtest — does the fan deserve to be believed?
# ===========================================================================
@dataclass
class BacktestResult:
    as_of: date
    dates: list[date]
    actual: np.ndarray            # what cash really did
    pct: dict[int, np.ndarray]    # what the model said it would do
    coverage_80: float            # share of days inside P10-P90
    coverage_50: float            # share inside P25-P75
    median_abs_error: float       # |actual - P50|, median over days
    end_error: float              # actual - P50 on the final day
    bias: float                   # mean(actual - P50); >0 = model too pessimistic
    n_days: int
    train_days: int = 0           # history the refit actually had to learn from
    thin: bool = False            # too little training data to judge the model
    note: str = ""


def backtest_cash(store: Store, lookback_days: int = 365,
                  horizon_days: int | None = None, n_paths: int = 400,
                  seed: int = 7) -> BacktestResult | None:
    """Refit the projection at a past date and score it against what happened.

    This is the only check in the app that says whether the fan should be
    believed at all. Everything else reports what the model thinks; this
    reports how often the model has been right.

    Guarding against lookahead is the whole difficulty. Three leaks are closed
    explicitly: recurring patterns are re-detected from transactions on or
    before the as-of date rather than reusing today's (which were fitted with
    knowledge of the very period being predicted), the behavioural bootstrap is
    truncated the same way, and the starting cash balance is read from the
    reconstructed history at that date instead of today's balance.

    A leak that is *not* closed: loan and investment balances are current-day,
    because the app doesn't retain a full balance history for them. That
    inflates accuracy for net worth, which is why only cash is scored here —
    cash is exactly reconstructable from the transaction ledger.
    """
    hist = networth_history(store)
    if len(hist) < 60:
        return None
    as_of = date.today() - timedelta(days=int(lookback_days))
    first = hist.index[0].date()
    train_days = (as_of - first).days
    if train_days < 45:
        return None                        # nothing meaningful to refit on

    tx = store.transactions()
    past = tx[tx["date"] <= pd.Timestamp(as_of)]
    history = ForecastHistory.from_setting(store.get_setting(HISTORY_SETTING))
    selected = history.transactions(past, as_of)
    if history.start(as_of):
        train_days = min(train_days, (as_of - history.start(as_of)).days)
    if train_days < 45:
        return None
    if len(selected) < 30:
        return None
    # Learn known keys from the full past. run_forecast applies an optional
    # refit to the selected period, while still withholding absent known
    # schedules from its random sample. No future transactions enter either.
    items = detect(past)

    H = int(horizon_days or lookback_days)
    res = run_forecast(store, H, n_paths, as_of=as_of,
                       recurring_override=items, seed=seed)

    # align the projection to the days we can actually score
    actual_series = hist["cash"]
    dates, actual, keep = [], [], []
    for i, d in enumerate(res.dates):
        ts = pd.Timestamp(d)
        if ts < actual_series.index[0] or ts > actual_series.index[-1]:
            continue
        dates.append(d)
        actual.append(float(actual_series.asof(ts)))
        keep.append(i)
    if len(keep) < 30:
        return None
    keep = np.array(keep)
    a = np.array(actual)
    pct = {p: res.cash_pct[p][keep] for p in res.cash_pct}

    inside80 = float(np.mean((a >= pct[10]) & (a <= pct[90])))
    inside50 = float(np.mean((a >= pct[25]) & (a <= pct[75])))
    err = a - pct[50]
    # A refit with only a few weeks behind it will score badly no matter how
    # good the model is, so say that rather than blaming the model.
    thin = train_days < 120
    if thin:
        note = (f"Only {train_days // 7} weeks of history existed before this "
                f"cut-off, so this says more about the data than the model. "
                f"Try a shorter look-back.")
    elif inside80 >= 0.75:
        note = "Well calibrated — reality stayed inside the 80% band."
    elif inside80 >= 0.5:
        note = "Roughly calibrated — the band was too narrow at times."
    else:
        note = ("Poorly calibrated over this window — treat the fan as a rough "
                "sketch, not a forecast.")
    return BacktestResult(
        as_of=as_of, dates=dates, actual=a, pct=pct,
        coverage_80=inside80, coverage_50=inside50,
        median_abs_error=float(np.median(np.abs(err))),
        end_error=float(err[-1]), bias=float(np.mean(err)),
        n_days=len(a), train_days=train_days, thin=thin, note=note)
