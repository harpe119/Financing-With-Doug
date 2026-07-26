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

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .categorize import categorize
from .history import networth_history, weekly_category_matrix
from .models import Account, AccountType, Goal, RecurringItem, Scenario
from .recurrence import detect, occurrences
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


def token_set(s: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in s.upper()).split()
            if len(t) > 3}


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


def run_forecast(store: Store, horizon_days: int, n_paths: int,
                 scenario: Scenario | None = None, seed: int = 7,
                 emergency_target: float | None = None,
                 essential_monthly: float | None = None,
                 as_of: date | None = None,
                 recurring_override: list[RecurringItem] | None = None,
                 dof: float = T_DOF) -> ForecastResult:
    """Project cash and net worth.

    ``emergency_target`` is the months-of-essentials cash buffer to score paths
    against; pass None to skip that floor. ``essential_monthly`` enables the
    runway figure.

    ``as_of`` projects from a past date instead of today, using only data that
    existed then — the machinery behind the backtest. ``recurring_override``
    supplies patterns re-detected as of that date, so the projection doesn't
    quietly benefit from knowing how the patterns turned out.
    """
    scenario = scenario or Scenario()
    warnings: list[str] = []
    today = as_of or date.today()
    H = int(horizon_days)
    n = H + 1                                  # index 0 = today
    dates = [today + timedelta(days=i) for i in range(n)]
    end = dates[-1]

    def idx(d: date) -> int:
        return (d - today).days

    accounts = store.accounts()
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

    all_items = (recurring_override if recurring_override is not None
                 else store.recurring())
    items = [it for it in all_items if it.active and it.kind != "ignore"]
    loan_accounts = {a.id: a for a in accounts if a.type == AccountType.LOAN}
    linked_payments: dict[int, RecurringItem] = {}
    flow_items: list[RecurringItem] = []
    for it in items:
        if it.kind == "loan_payment" and it.linked_account_id in loan_accounts:
            linked_payments[it.linked_account_id] = it
        elif it.kind == "loan_payment" and loan_accounts:
            # An unlinked loan payment is almost certainly the same money the
            # amortization engine is already withdrawing for one of the loan
            # accounts. Replaying it here as well double-charged cash every
            # month. Drop it and tell the user to link it explicitly.
            warnings.append(
                f"“{it.name}” looks like a loan payment but isn't linked to a "
                f"loan account, so it was left out of the cash projection to "
                f"avoid counting it twice. Link it on the Recurring page."
            )
        else:
            flow_items.append(it)

    # ---- 1. deterministic recurring flows ------------------------------
    base_flow = np.zeros(n)
    for it in flow_items:
        if it.kind == "income":
            amt = it.amount * scenario.income_multiplier
        else:
            cat = categorize(it.name, it.amount)
            amt = it.amount * scenario.category_multipliers.get(cat, 1.0)
        for d in occurrences(it, today, end):
            base_flow[idx(d)] += amt
    monthly_net = sum(
        (it.amount * scenario.income_multiplier if it.kind == "income"
         else it.amount * scenario.category_multipliers.get(
             categorize(it.name, it.amount), 1.0)) * (30.44 / _cad_days(it))
        for it in flow_items
    )

    # ---- 2. loans -------------------------------------------------------
    loan_daily_total = np.zeros(n)
    loan_outcomes: list[LoanOutcome] = []
    sim_loans = []
    for a in sorted(loan_accounts.values(), key=lambda x: -x.apr):
        li = linked_payments.get(a.id)
        if li is not None:
            pay = abs(li.monthly_equivalent)
            day = li.days_of_month[0] if li.days_of_month else li.last_date.day
        else:
            pay = a.monthly_payment
            day = a.payment_day
        b0 = abs(a.balance)
        if pay <= 0 and b0 > 0:
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

    if sim_loans:
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

    # ---- 2b. one-off events --------------------------------------------
    # Dated impulses, applied to cash on the day they land. Stored events and
    # scenario-only events are merged so the page can preview an event before
    # committing it.
    events = list(store.events(active_only=True)) + list(scenario.events or [])
    applied_events = []
    for ev in events:
        if not getattr(ev, "active", True):
            continue
        if today < ev.when <= end:
            base_flow[idx(ev.when)] += float(ev.amount)
            applied_events.append(ev)

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
    for k, a in enumerate(inv_accounts):
        contrib = a.monthly_contribution + (total_extra if k == 0 else 0.0)
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
        # step-expand month values onto the daily grid
        ptr = np.searchsorted(ev_idx, np.arange(n), side="right")  # 0..n_ev
        det_ext = np.concatenate(([V0], det_vals))
        inv_det_daily += det_ext[ptr]
        mc_ext = np.hstack([np.full((n_paths, 1), V0), mc])
        inv_paths_daily += mc_ext[:, ptr]
    if not inv_accounts and total_extra > 0:
        warnings.append("Extra investing is set but no investment account exists; "
                        "it was ignored.")

    # ---- 4. discretionary behavior (bootstrap) --------------------------
    tx = store.transactions()
    if as_of is not None:
        tx = tx[tx["date"] <= pd.Timestamp(as_of)]
    # Every *known* pattern is withheld from the behavioral sample, not just the
    # active ones. Excluding only active items meant deactivating a pattern (or
    # marking it 'ignore') quietly readmitted its history as variable spending,
    # so the control had almost no effect on the forecast.
    exclude = {it.key for it in all_items}
    W, irr_inc = weekly_category_matrix(tx, exclude_keys=exclude)
    n_weeks = int(np.ceil(H / 7))
    if len(W) >= 4:
        cats = list(W.columns)
        mvec = np.array([scenario.category_multipliers.get(c, 1.0) for c in cats])
        Wv = W.values
        Iv = irr_inc.values * scenario.income_multiplier
        # Weeks are drawn by recency, not uniformly. Sampling all of history
        # with equal probability assumes habits are stationary, and when they
        # are not it projects an average of the person you were and the person
        # you are — someone who halved their spending five months ago was being
        # forecast at the midpoint of both, and told they would run out of money
        # while the Insights page (which *is* recency-weighted) correctly
        # reported a surplus. The two halves of the app now answer the question
        # the same way, with the same half-life.
        obs_net = Iv - Wv @ mvec
        wts = _behaviour_weights(obs_net, seed=seed)
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
        if not tx.empty:
            warnings.append("Under 4 complete weeks of history — variable "
                            "day-to-day spending is not modeled yet.")

    # ---- 5. assemble cash and net worth ---------------------------------
    cash_det = cash0 + np.concatenate(([0.0], np.cumsum(base_flow[1:] + disc_det)))
    cash_paths = cash0 + np.concatenate(
        [np.zeros((n_paths, 1)),
         np.cumsum(base_flow[None, 1:] + disc_paths, axis=1)], axis=1)

    networth_det = cash_det + inv_det_daily - loan_daily_total
    networth_paths = cash_paths + inv_paths_daily - loan_daily_total[None, :]

    networth_pct = {p: np.percentile(networth_paths, p, axis=0) for p in PCTS}
    cash_pct = {p: np.percentile(cash_paths, p, axis=0) for p in PCTS}

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
    below_zero = cash_paths < 0.0
    first_breach_day = None
    if below_zero.any():
        # earliest day on which at least 10% of paths are underwater
        frac = below_zero.mean(axis=0)
        hits = np.nonzero(frac >= 0.10)[0]
        if len(hits):
            first_breach_day = int(hits[0])

    # ---- 6. goals -------------------------------------------------------
    goal_outcomes: list[GoalOutcome] = []
    for g in store.goals():
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
    )


def _cad_days(it: RecurringItem) -> float:
    from .models import CADENCE_DAYS
    return CADENCE_DAYS[it.cadence]


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

    if any(a.type == AccountType.LOAN for a in store.accounts()):
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
    if len(past) < 30:
        return None
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
