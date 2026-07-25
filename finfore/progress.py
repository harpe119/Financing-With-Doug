"""Progress over time: did the last projection hold, and is anything better?

Every other page in this app answers a question about *now*. This one answers
questions about the gap between two nows — which is the only way to find out
whether the model was any good and whether behaviour actually changed.

Three things are recorded at each import, and none of them can be recovered
after the fact:

* **the score and its components**, because they are recency-weighted and
  therefore un-recomputable for a past date once newer months exist;
* **the forecast fan**, because a projection is only a claim if you write it
  down before the outcome is known — refitting later with today's data is how
  models flatter themselves;
* **the ledger's extent**, so a later comparison knows which weeks were new.

The statistics deliberately avoid scipy: it is in the PyInstaller exclude list
because pandas drags it in and it doubles the bundle. Everything below runs on
numpy, which is not a compromise — a permutation test needs no distributional
assumption at all, where a t-test would need one this data does not satisfy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .history import networth_history, spending_frame, weekly_category_matrix
from .insights import bucket_of, monthly_aggregates, spending_score
from .store import Store

# Weekly spend is the unit of analysis. Days are too noisy and too
# autocorrelated (nobody buys groceries daily); months give four data points a
# year, which no test can do anything with. Weeks are also what the forecaster
# already bootstraps, so the two halves of the app agree about what a
# behavioural sample is.
MIN_WEEKS = 4                 # below this, say "not enough data", never "no change"
N_PERMUTATIONS = 20_000
N_BOOTSTRAP = 4_000
ALPHA = 0.05
_RNG_SEED = 11


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------
@dataclass
class Checkpoint:
    when: date
    label: str = ""
    n_transactions: int = 0
    latest_tx: date | None = None
    networth: float = 0.0
    cash: float = 0.0
    investments: float = 0.0
    debt: float = 0.0
    score: float | None = None
    grade: str = ""
    fesr: float | None = None
    savings_rate: float | None = None
    income: float = 0.0
    spending: float = 0.0
    essential: float = 0.0
    frivolous: float = 0.0
    unknown: float = 0.0
    # The projection as it stood, so it can be scored against reality later.
    forecast: dict = field(default_factory=dict)
    id: int | None = None

    @property
    def has_forecast(self) -> bool:
        return bool(self.forecast.get("dates"))

    def forecast_frame(self) -> pd.DataFrame | None:
        """The stored cash fan as a frame indexed by date."""
        f = self.forecast
        if not f.get("dates"):
            return None
        idx = pd.to_datetime(f["dates"])
        cols = {f"p{p}": f.get(f"p{p}") for p in (10, 25, 50, 75, 90)}
        cols = {k: v for k, v in cols.items() if v}
        if not cols:
            return None
        return pd.DataFrame(cols, index=idx)


def capture(store: Store, label: str = "", horizon_days: int = 365,
            n_paths: int = 300, with_forecast: bool = True) -> Checkpoint:
    """Record the current state, including the projection being made *now*.

    Called automatically after each import. The forecast is the expensive part
    and the whole point: storing what the model claimed, before the weeks it
    claimed it about have happened, is what makes the later comparison honest.
    """
    from .forecast import run_forecast          # local: avoids an import cycle
    from .insights import essential_monthly

    tx = store.transactions()
    agg = monthly_aggregates(tx)
    sc = spending_score(agg) if agg is not None else None
    from .history import headline_stats
    st = headline_stats(store)

    cp = Checkpoint(
        when=date.today(),
        label=label,
        n_transactions=int(len(tx)),
        latest_tx=(tx["date"].max().date()
                   if not tx.empty and tx["date"].notna().any() else None),
        networth=float(st["networth"]), cash=float(st["cash"]),
        investments=float(st["investments"]), debt=float(st["debt"]),
        score=(float(sc.value) if sc else None),
        grade=(sc.letter if sc else ""),
        fesr=(float(sc.fesr) if sc else None),
        savings_rate=(float(agg.savings_rate)
                      if agg is not None and agg.savings_rate is not None else None),
        income=(float(agg.income) if agg else 0.0),
        spending=(float(agg.spending) if agg else 0.0),
        essential=(float(agg.essential) if agg else 0.0),
        frivolous=(float(agg.frivolous) if agg else 0.0),
        unknown=(float(agg.unknown) if agg else 0.0),
    )

    if with_forecast and store.accounts():
        try:
            ess = essential_monthly(store)
            res = run_forecast(store, horizon_days, n_paths,
                               emergency_target=max(0.0, ess * 3),
                               essential_monthly=ess)
            cp.forecast = {
                "dates": [d.isoformat() for d in res.dates],
                **{f"p{p}": [float(v) for v in res.cash_pct[p]]
                   for p in (10, 25, 50, 75, 90)},
                "horizon_days": horizon_days,
            }
        except Exception:
            # A projection that cannot be built must not block the checkpoint:
            # the score history is worth keeping on its own.
            cp.forecast = {}
    store.add_checkpoint(cp)
    return cp


def capture_as_of(store: Store, when: date, label: str = "",
                  horizon_days: int = 365, n_paths: int = 200,
                  with_forecast: bool = True) -> Checkpoint | None:
    """Record the checkpoint that *would* have been taken on a past date.

    Every figure is computed from data that existed then, by the same three
    exclusions the backtest makes: recurring patterns are re-detected from
    transactions on or before the date rather than reusing today's (which were
    fitted knowing how the period turned out), the behavioural bootstrap is
    truncated the same way, and starting cash comes from the reconstructed
    history rather than today's balance. The score is recomputed from the
    truncated ledger, where the recency weighting anchors on the newest month
    that existed rather than on this one.

    That care matters because a backfilled checkpoint is indistinguishable from
    a real one once stored. A retroactive "projection" built with hindsight
    would make the Progress page report the model as far better than it is,
    which is the one thing that page exists to prevent.
    """
    from .forecast import run_forecast
    from .insights import essential_monthly
    from .recurrence import detect

    ts = pd.Timestamp(when)
    tx = store.transactions()
    past = tx[tx["date"] <= ts] if not tx.empty else tx
    if len(past) < 20:
        return None

    agg = monthly_aggregates(past)
    sc = spending_score(agg) if agg is not None else None

    hist = networth_history(store)
    row = None
    if not hist.empty:
        upto = hist[hist.index <= ts]
        if not upto.empty:
            row = upto.iloc[-1]

    cp = Checkpoint(
        when=when, label=label, n_transactions=int(len(past)),
        latest_tx=(past["date"].max().date()
                   if not past.empty and past["date"].notna().any() else None),
        networth=float(row["networth"]) if row is not None else 0.0,
        cash=float(row["cash"]) if row is not None else 0.0,
        investments=float(row["investments"]) if row is not None else 0.0,
        debt=float(row["debt"]) if row is not None else 0.0,
        score=(float(sc.value) if sc else None),
        grade=(sc.letter if sc else ""),
        fesr=(float(sc.fesr) if sc else None),
        savings_rate=(float(agg.savings_rate)
                      if agg is not None and agg.savings_rate is not None else None),
        income=(float(agg.income) if agg else 0.0),
        spending=(float(agg.spending) if agg else 0.0),
        essential=(float(agg.essential) if agg else 0.0),
        frivolous=(float(agg.frivolous) if agg else 0.0),
        unknown=(float(agg.unknown) if agg else 0.0),
    )

    if with_forecast and store.accounts():
        try:
            items = detect(past)
            ess = essential_monthly(store)
            res = run_forecast(store, horizon_days, n_paths, as_of=when,
                               recurring_override=items,
                               emergency_target=max(0.0, ess * 3),
                               essential_monthly=ess)
            cp.forecast = {
                "dates": [d.isoformat() for d in res.dates],
                **{f"p{p}": [float(v) for v in res.cash_pct[p]]
                   for p in (10, 25, 50, 75, 90)},
                "horizon_days": horizon_days,
            }
        except Exception:
            cp.forecast = {}
    store.add_checkpoint(cp)
    return cp


def backfill(store: Store, n: int = 4, spacing_days: int = 60,
             n_paths: int = 200, label: str = "backfilled") -> int:
    """Reconstruct checkpoints a ledger should already have had.

    Someone importing a couple of years of history on their first run has all
    the data needed for the Progress page and none of the checkpoints, and
    would otherwise wait months for it to say anything. Each reconstructed
    point is built only from data that existed at its own date, so the
    calibration figures it later produces stay honest.

    Existing checkpoints are never duplicated: a date already covered is
    skipped rather than written twice.
    """
    tx = store.transactions()
    if tx.empty or tx["date"].notna().sum() < 30:
        return 0
    first = tx["date"].min().date()
    today = date.today()
    have = {c.when for c in store.checkpoints()}
    made = 0
    for k in range(n, 0, -1):
        when = today - timedelta(days=spacing_days * k)
        if when <= first + timedelta(days=45) or when in have:
            continue          # too little history behind it to mean anything
        if capture_as_of(store, when, label=label, n_paths=n_paths) is not None:
            made += 1
            have.add(when)
    return made


def should_capture(store: Store, min_days: int = 3,
                   min_new_tx: int = 5) -> bool:
    """Is a new checkpoint worth taking?

    Re-importing the same export twice in an afternoon should not create two
    checkpoints — the comparison page would fill with zero-length intervals
    that no test can say anything about. A checkpoint needs either a few days
    of separation or a real batch of new rows behind it.
    """
    prev = store.checkpoints()
    if not prev:
        return True
    last = prev[-1]
    n_now = len(store.transactions())
    if (date.today() - last.when).days >= min_days:
        return True
    return (n_now - last.n_transactions) >= min_new_tx


# ---------------------------------------------------------------------------
# Did the projection hold?
# ---------------------------------------------------------------------------
@dataclass
class PathCheck:
    """How reality compared with a projection made at a past date."""
    made_on: date
    days_elapsed: int
    n_points: int = 0
    inside_80: float = 0.0          # fraction of days within P10–P90
    inside_50: float = 0.0
    actual_now: float = 0.0
    predicted_now: float = 0.0      # the P50 for today
    band_low: float = 0.0           # P10 today
    band_high: float = 0.0          # P90 today
    percentile_now: float = 50.0    # where today's cash sits in that fan
    status: str = "on-path"         # 'ahead' | 'on-path' | 'behind' | 'broken'
    headline: str = ""

    @property
    def delta(self) -> float:
        return self.actual_now - self.predicted_now


def check_path(store: Store, cp: Checkpoint) -> PathCheck | None:
    """Score a stored projection against what actually happened since.

    Only cash is scored, for the same reason the backtest only scores cash: it
    is exactly reconstructable from the ledger, where loan and investment
    balances depend on snapshots that may never have been taken.
    """
    fan = cp.forecast_frame()
    if fan is None:
        return None
    hist = networth_history(store)
    if hist.empty:
        return None
    actual = hist["cash"]
    lo, hi = max(fan.index.min(), actual.index.min()), min(fan.index.max(),
                                                           actual.index.max())
    if lo >= hi:
        return None
    idx = fan.index[(fan.index >= lo) & (fan.index <= hi)]
    idx = idx[idx.isin(actual.index)]
    if len(idx) < 2:
        return None

    a = actual.reindex(idx).to_numpy(dtype=float)
    p10 = fan["p10"].reindex(idx).to_numpy(dtype=float)
    p25 = fan["p25"].reindex(idx).to_numpy(dtype=float)
    p50 = fan["p50"].reindex(idx).to_numpy(dtype=float)
    p75 = fan["p75"].reindex(idx).to_numpy(dtype=float)
    p90 = fan["p90"].reindex(idx).to_numpy(dtype=float)

    chk = PathCheck(
        made_on=cp.when,
        days_elapsed=int((idx[-1] - idx[0]).days),
        n_points=len(idx),
        inside_80=float(((a >= p10) & (a <= p90)).mean()),
        inside_50=float(((a >= p25) & (a <= p75)).mean()),
        actual_now=float(a[-1]), predicted_now=float(p50[-1]),
        band_low=float(p10[-1]), band_high=float(p90[-1]),
    )
    chk.percentile_now = _percentile_in_fan(
        a[-1], p10[-1], p25[-1], p50[-1], p75[-1], p90[-1])

    if a[-1] > p90[-1]:
        chk.status = "broken"
        chk.headline = ("Above your own projection entirely — cash is higher "
                        "than the optimistic edge of what that forecast "
                        "allowed for. Something changed for the better.")
    elif a[-1] < p10[-1]:
        chk.status = "broken"
        chk.headline = ("Below the pessimistic edge of that projection. The "
                        "forecast did not anticipate this, so it is worth "
                        "asking what changed rather than trusting the next one "
                        "blindly.")
    elif a[-1] > p75[-1]:
        chk.status = "ahead"
        chk.headline = "Ahead of the projection — in the top quarter of paths."
    elif a[-1] < p25[-1]:
        chk.status = "behind"
        chk.headline = "Behind the projection — in the bottom quarter of paths."
    else:
        chk.status = "on-path"
        chk.headline = "Tracking the middle of the projection."
    return chk


def _percentile_in_fan(x, p10, p25, p50, p75, p90) -> float:
    """Interpolate where a value sits within the stored percentile curve."""
    xs = [p10, p25, p50, p75, p90]
    ys = [10.0, 25.0, 50.0, 75.0, 90.0]
    if x <= xs[0]:
        return 10.0 if x == xs[0] else max(0.0, 10.0 - 5.0)
    if x >= xs[-1]:
        return 90.0 if x == xs[-1] else min(100.0, 95.0)
    return float(np.interp(x, xs, ys))


# ---------------------------------------------------------------------------
# Has behaviour actually changed?
# ---------------------------------------------------------------------------
@dataclass
class Significance:
    """A two-sample comparison of weekly discretionary spending."""
    n_before: int = 0
    n_after: int = 0
    mean_before: float = 0.0
    mean_after: float = 0.0
    diff: float = 0.0               # after - before, $/week (negative = spending less)
    ci_low: float = 0.0             # 95% bootstrap CI on the difference
    ci_high: float = 0.0
    p_value: float = 1.0
    significant: bool = False
    effect: float = 0.0             # standardised difference (Cohen's d)
    direction: str = "unchanged"    # 'improved' | 'worsened' | 'unchanged'
    enough_data: bool = False
    detectable: float = 0.0         # smallest difference this sample could resolve
    note: str = ""

    @property
    def monthly_diff(self) -> float:
        return self.diff * 52.0 / 12.0


def weekly_discretionary(store: Store) -> pd.Series:
    """Weekly totals of non-recurring, non-essential spending.

    Recurring patterns are excluded because a rent rise is not a change of
    habit, and their fixed monthly timing would put a 4-week cycle into a
    weekly series and violate the independence every test below assumes.
    What is left is the part of spending that is actually a choice each week.
    """
    tx = store.transactions()
    if tx.empty:
        return pd.Series(dtype=float)
    known = {it.key for it in store.recurring()}
    W, _inc = weekly_category_matrix(tx, exclude_keys=known, max_weeks=520)
    if W.empty:
        return pd.Series(dtype=float)
    cols = [c for c in W.columns if bucket_of(str(c)) in ("frivolous", "unknown")]
    if not cols:
        return pd.Series(dtype=float)
    s = W[cols].sum(axis=1)
    s.index = [p.start_time if hasattr(p, "start_time") else p for p in s.index]
    return s.sort_index()


def compare_periods(before: np.ndarray, after: np.ndarray,
                    seed: int = _RNG_SEED) -> Significance:
    """Permutation test + bootstrap CI on the change in weekly spending.

    A permutation test is used rather than Welch's t because it assumes only
    exchangeability under the null — no normality, no equal variance. Weekly
    discretionary spend is right-skewed and occasionally has a very large week
    in it, which is exactly the shape that makes a t-test's p-value a work of
    fiction at n = 8. The null distribution here is built from the data itself
    by reshuffling which weeks belong to which period.

    The confidence interval on the dollar difference is the number that
    actually answers the user's question. A p-value says only "distinguishable
    from noise"; the interval says how much, and how sure.
    """
    out = Significance(n_before=len(before), n_after=len(after))
    if len(before) < MIN_WEEKS or len(after) < MIN_WEEKS:
        out.note = (f"Needs at least {MIN_WEEKS} complete weeks on each side; "
                    f"there are {len(before)} and {len(after)}. Import again "
                    f"after a few more weeks of activity.")
        return out

    out.enough_data = True
    out.mean_before = float(np.mean(before))
    out.mean_after = float(np.mean(after))
    out.diff = out.mean_after - out.mean_before

    pooled_sd = np.sqrt((np.var(before, ddof=1) + np.var(after, ddof=1)) / 2.0)
    out.effect = float(out.diff / pooled_sd) if pooled_sd > 0 else 0.0

    rng = np.random.default_rng(seed)
    combined = np.concatenate([before, after])
    n_b = len(before)
    observed = abs(out.diff)
    # Vectorised permutation: shuffle the labels, not the values.
    perms = np.array([rng.permutation(combined) for _ in range(N_PERMUTATIONS)])
    diffs = perms[:, n_b:].mean(axis=1) - perms[:, :n_b].mean(axis=1)
    out.p_value = float((np.abs(diffs) >= observed - 1e-12).mean())
    out.significant = out.p_value < ALPHA
    # The smallest difference this sample size could have called significant —
    # the honest answer to "so there was no change, right?" when p is large.
    out.detectable = float(np.percentile(np.abs(diffs), 95))

    boot = np.empty(N_BOOTSTRAP)
    for i in range(N_BOOTSTRAP):
        b = rng.choice(before, size=n_b, replace=True)
        a = rng.choice(after, size=len(after), replace=True)
        boot[i] = a.mean() - b.mean()
    out.ci_low, out.ci_high = (float(np.percentile(boot, 2.5)),
                               float(np.percentile(boot, 97.5)))

    if out.significant:
        out.direction = "improved" if out.diff < 0 else "worsened"
    else:
        out.direction = "unchanged"

    if out.significant and out.diff < 0:
        out.note = (f"Discretionary spending is down about "
                    f"${abs(out.diff):,.0f}/week — around "
                    f"${abs(out.monthly_diff):,.0f}/month. Larger than this "
                    f"sample's noise, so it is a real change in behaviour, not "
                    f"a quiet fortnight.")
    elif out.significant:
        out.note = (f"Discretionary spending is up about ${out.diff:,.0f}/week "
                    f"(${out.monthly_diff:,.0f}/month), by more than the "
                    f"week-to-week variation explains.")
    else:
        out.note = (f"The change (${out.diff:,.0f}/week) is inside the noise of "
                    f"this many weeks. That is not evidence nothing happened — "
                    f"with {out.n_before} and {out.n_after} weeks, only a shift "
                    f"bigger than about ${out.detectable:,.0f}/week could have "
                    f"been told apart from chance at all.")
    return out


def significance_since(store: Store, cp: Checkpoint,
                       seed: int = _RNG_SEED) -> Significance:
    """Compare weekly spending since a checkpoint against everything before it."""
    s = weekly_discretionary(store)
    if s.empty:
        return Significance(note="No discretionary spending recorded yet.")
    cut = pd.Timestamp(cp.when)
    before = s[s.index < cut].to_numpy(dtype=float)
    after = s[s.index >= cut].to_numpy(dtype=float)
    # A long "before" adds little and drags in habits from years ago; cap it at
    # roughly a year so the comparison is recent-versus-recent.
    if len(before) > 52:
        before = before[-52:]
    return compare_periods(before, after, seed=seed)


# ---------------------------------------------------------------------------
# Category-level movement
# ---------------------------------------------------------------------------
@dataclass
class CategoryShift:
    category: str
    before: float                   # $/week
    after: float
    diff: float
    bucket: str


def category_shifts(store: Store, cp: Checkpoint,
                    top: int = 6) -> list[CategoryShift]:
    """Which categories moved most since a checkpoint, biggest change first.

    No per-category p-values. Twenty categories tested at 5% yields one false
    positive per run on average, and presenting that as "significant" beside a
    real finding would undermine the one number on this page that is defensible.
    These are descriptive: they say where the overall change came from.
    """
    tx = store.transactions()
    if tx.empty:
        return []
    known = {it.key for it in store.recurring()}
    W, _ = weekly_category_matrix(tx, exclude_keys=known, max_weeks=520)
    if W.empty:
        return []
    idx = [p.start_time if hasattr(p, "start_time") else p for p in W.index]
    W = W.copy()
    W.index = pd.DatetimeIndex(idx)
    cut = pd.Timestamp(cp.when)
    b, a = W[W.index < cut], W[W.index >= cut]
    if b.empty or a.empty:
        return []
    out = []
    for c in W.columns:
        out.append(CategoryShift(
            category=str(c), before=float(b[c].mean()), after=float(a[c].mean()),
            diff=float(a[c].mean() - b[c].mean()),
            bucket=bucket_of(str(c))))
    out.sort(key=lambda x: -abs(x.diff))
    return out[:top]


# ---------------------------------------------------------------------------
# Score movement
# ---------------------------------------------------------------------------
@dataclass
class ScoreMove:
    from_score: float | None
    to_score: float | None
    from_grade: str
    to_grade: str
    delta: float
    improved: bool
    components: list[tuple[str, float, float]] = field(default_factory=list)
    note: str = ""


def score_move(a: Checkpoint, b: Checkpoint) -> ScoreMove:
    """Change in the habits score between two checkpoints."""
    m = ScoreMove(from_score=a.score, to_score=b.score,
                  from_grade=a.grade, to_grade=b.grade,
                  delta=0.0, improved=False)
    if a.score is None or b.score is None:
        m.note = ("One of these checkpoints predates having enough categorised "
                  "history to score.")
        return m
    m.delta = b.score - a.score
    m.improved = m.delta > 0.05
    m.components = [
        ("Savings rate", a.savings_rate or 0.0, b.savings_rate or 0.0),
        ("Wants vs needs (FESR)", a.fesr or 0.0, b.fesr or 0.0),
        ("Income $/mo", a.income, b.income),
        ("Spending $/mo", a.spending, b.spending),
    ]
    if abs(m.delta) < 0.05:
        m.note = f"Effectively unchanged at {b.score:.1f}/10."
    elif m.improved:
        m.note = (f"Up {m.delta:.1f} points, {a.grade} → {b.grade}."
                  if a.grade != b.grade else f"Up {m.delta:.1f} points.")
    else:
        m.note = (f"Down {abs(m.delta):.1f} points, {a.grade} → {b.grade}."
                  if a.grade != b.grade else f"Down {abs(m.delta):.1f} points.")
    return m
