"""Spending analytics: composition, a benchmarked habits score, and tailored
reduction suggestions.

Three ideas hold this module together.

**One taxonomy.** Every category sorts into essential / frivolous / unknown.
Income and internal transfers are not spending at all. The mapping is kept as a
plain editable dict at the top of the file because it encodes a value judgement
(what is a "need") that different households draw differently.

**Recency weighting.** A hard "last 3 complete months" window throws away
usable history and puts a cliff at the boundary — a big purchase silently stops
counting the day it ages out. Instead every complete month is weighted by an
exponential decay, so last month dominates, six months ago still contributes,
and two years ago is noise. One constant (`HALF_LIFE_MONTHS`) controls it.

**Benchmarks, not invented numbers.** The score is anchored on published
budgeting guidance rather than taste: the 50/30/20 rule (50% needs, 30% wants,
20% savings, from Warren & Tyagi's *All Your Worth*, and the basis of most
"balanced budget" advice) gives a frivolous-to-essential target of 30/50 = 0.60,
and a savings-rate target of 20%. Those two numbers are the anchor points of
both sub-scores.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .categorize import INCOME_CATEGORIES, categorize
from .models import RecurringItem
from .util import fmt_money

# --- taxonomy ---------------------------------------------------------------
ESSENTIAL = {"Housing", "Utilities", "Groceries", "Insurance", "Fuel",
             "Transport", "Debt payment", "Health & fitness"}
# "Frivolous" in the user-facing sense: discretionary wants.
FRIVOLOUS = {"Dining", "Entertainment", "Shopping", "Travel", "Subscriptions"}
DISCRETIONARY = FRIVOLOUS            # backwards-compatible alias
# Giving gets its own bucket rather than being folded into wants. Calling a
# tithe or a monthly charity commitment "frivolous" is a value judgement most
# people would reject, and it would drag the habits grade down for behaviour
# nobody considers a spending problem. It is still real spending, so it counts
# toward the total and appears in the composition chart — it is simply kept out
# of the frivolous-to-essential ratio, and flagged separately if it is large
# while the household is in deficit.
GIVING = {"Charitable donation"}
# Must stay in lockstep with categorize.INCOME_CATEGORIES. categorize() assigns
# "Other income" to every inflow that matches no keyword rule.
INCOME = set(INCOME_CATEGORIES)
EXCLUDE = {"Transfer"}               # internal movement, not spending
SUBSCRIPTION_CAT = "Subscriptions"

# --- benchmarks -------------------------------------------------------------
# 50/30/20: needs 50%, wants 30%, savings 20%.
FESR_TARGET = 0.60          # 30 wants / 50 needs — the recommended ceiling
SAVINGS_TARGET = 0.20       # the recommended savings rate
FESR_CAP = 4.0              # beyond this the ratio carries no more information
# The same rule read as shares of income. FESR compares wants to *needs*, which
# is deliberate — grading someone against their income punishes a low earner for
# a circumstance rather than a habit — but it is scale-free, so wants and needs
# can inflate together and the ratio still reads as fine. Someone spending 69%
# of income on essentials and 39% on wants has an FESR of 0.57, comfortably
# "within guideline", while being 8% of income underwater. These two shares are
# what catch that, and they are what the verdict consults before blaming or
# absolving spending habits.
WANTS_INCOME_TARGET = 0.30      # the "30"
NEEDS_INCOME_TARGET = 0.50      # the "50"
HALF_LIFE_MONTHS = 3.0      # recency decay: 6mo ago counts 1/4, 24mo ago 1/256
LOOKBACK_MONTHS = 24        # complete months considered at all
HOUSING_NOTE_THRESHOLD = 0.35   # housing share of income that earns a note


def bucket_of(category: str) -> str:
    """essential | frivolous | unknown (income/transfer -> excluded)."""
    if category in ESSENTIAL:
        return "essential"
    if category in FRIVOLOUS:
        return "frivolous"
    if category in GIVING:
        return "giving"
    if category in INCOME or category in EXCLUDE:
        return "excluded"
    return "unknown"


# --- recency-weighted monthly aggregation -----------------------------------
def _month_weights(months: list[pd.Period],
                   half_life: float = HALF_LIFE_MONTHS) -> dict[pd.Period, float]:
    """Exponential decay by month age. The newest complete month has weight 1."""
    if not months:
        return {}
    newest = max(months)
    return {m: 0.5 ** ((newest - m).n / half_life) for m in months}


@dataclass
class Aggregates:
    income: float               # I: recency-weighted monthly income
    spending: float             # S: total monthly spending
    essential: float            # E
    frivolous: float            # F: wants
    unknown: float              # U: not yet categorised — bucket unknown
    subscriptions: float        # component of F
    n_months: int               # complete months contributing
    partial_current: bool       # was the current (partial) month excluded
    half_life: float = HALF_LIFE_MONTHS
    giving: float = 0.0         # G: charitable donations, own bucket

    # --- backwards-compatible aliases -------------------------------------
    @property
    def discretionary(self) -> float:
        return self.frivolous

    @property
    def incidental(self) -> float:
        return self.unknown

    @property
    def savings_rate(self) -> float | None:      # r = (I - S) / I
        return (self.income - self.spending) / self.income if self.income > 0 else None

    @property
    def surplus(self) -> float:                  # net monthly income, $
        return self.income - self.spending

    @property
    def net_income(self) -> float:
        return self.surplus

    @property
    def discretionary_burden(self) -> float | None:
        return self.frivolous / self.income if self.income > 0 else None

    @property
    def nonessential(self) -> float:
        return self.frivolous

    @property
    def fesr(self) -> float | None:
        """Frivolous-to-essential spending ratio, F / E.

        Uncategorised spending is deliberately *excluded* from both sides. It
        used to be counted as frivolous, which punished people for spending the
        app had simply failed to label — and some of it is genuinely essential.
        Treating it as unknown is the honest reading; `unknown_share` reports
        how much of the picture is still missing.
        """
        if self.essential <= 0:
            return None if self.frivolous <= 0 else FESR_CAP
        return min(self.frivolous / self.essential, FESR_CAP)

    @property
    def ratio(self) -> float | None:             # legacy name
        return self.fesr

    @property
    def unknown_share(self) -> float:
        """Fraction of total spending that is still uncategorised."""
        return self.unknown / self.spending if self.spending > 0 else 0.0

    @property
    def wants_share(self) -> float | None:
        """Wants as a fraction of income — the '30' of 50/30/20."""
        return self.frivolous / self.income if self.income > 0 else None

    @property
    def needs_share(self) -> float | None:
        """Essentials as a fraction of income — the '50' of 50/30/20."""
        return self.essential / self.income if self.income > 0 else None


def monthly_aggregates(tx: pd.DataFrame, window: int | None = None,
                       half_life: float = HALF_LIFE_MONTHS) -> Aggregates | None:
    """Recency-weighted monthly income and spending.

    Every *complete* calendar month in the lookback contributes, weighted by
    ``0.5 ** (months_ago / half_life)``. The current partial month is excluded
    so a mid-month reading isn't mistaken for a low-spending month. ``window``
    is accepted for backwards compatibility: passing an int caps the number of
    months considered.
    """
    if tx is None or tx.empty:
        return None
    df = tx.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return None
    df["ym"] = df["date"].dt.to_period("M")
    this_month = pd.Timestamp(date.today()).to_period("M")
    partial = bool((df["ym"] == this_month).any())
    complete = df[df["ym"] < this_month]
    if complete.empty:
        return None

    months = sorted(complete["ym"].unique())[-(window or LOOKBACK_MONTHS):]
    sel = complete[complete["ym"].isin(months)]
    w = _month_weights(list(months), half_life)
    wsum = sum(w.values())
    if wsum <= 0:
        return None

    def weighted_monthly(rows: pd.DataFrame, outflow: bool = True) -> float:
        """Weighted average of per-month totals, in $/month."""
        if rows.empty:
            return 0.0
        per_month = rows.groupby("ym")["amount"].sum()
        total = 0.0
        for m, v in per_month.items():
            total += w.get(m, 0.0) * (-float(v) if outflow else float(v))
        return max(0.0, total / wsum)

    income = weighted_monthly(sel[sel["category"].isin(INCOME) & (sel["amount"] > 0)],
                              outflow=False)
    essential = weighted_monthly(sel[sel["category"].isin(ESSENTIAL)])
    frivolous = weighted_monthly(sel[sel["category"].isin(FRIVOLOUS)])
    subs = weighted_monthly(sel[sel["category"] == SUBSCRIPTION_CAT])
    giving = weighted_monthly(sel[sel["category"].isin(GIVING)])
    known = ESSENTIAL | FRIVOLOUS | GIVING | INCOME | EXCLUDE
    unknown_rows = sel[~sel["category"].isin(known)]
    unknown = weighted_monthly(unknown_rows[unknown_rows["amount"] < 0])
    spending = essential + frivolous + giving + unknown

    return Aggregates(income=income, spending=spending, essential=essential,
                      frivolous=frivolous, unknown=unknown, subscriptions=subs,
                      n_months=len(months), partial_current=partial,
                      half_life=half_life, giving=giving)


def weight_shares(half_life: float = HALF_LIFE_MONTHS,
                  lookback: int = LOOKBACK_MONTHS) -> dict[int, float]:
    """Share of the score contributed by the most recent N months.

    Derived from the constants rather than written out, so the sentence the UI
    shows cannot drift away from the arithmetic it describes.
    """
    w = [0.5 ** (m / half_life) for m in range(lookback)]
    total = sum(w) or 1.0
    return {n: sum(w[:n]) / total for n in (3, 6, 12)}


def window_note(agg: "Aggregates | None" = None,
                half_life: float = HALF_LIFE_MONTHS,
                lookback: int = LOOKBACK_MONTHS) -> str:
    """One line saying how far back the score looks, and how unevenly.

    Quoting the lookback alone ("24 months") reliably reads as "two years,
    treated evenly", which is the opposite of what the decay does — the recent
    quarter is already half the answer. Both numbers, or neither.
    """
    sh = weight_shares(half_life, lookback)
    base = (f"Looks back {lookback} months, weighted toward recent ones "
            f"(half-life {half_life:.0f} months): the last 3 months are about "
            f"{sh[3] * 100:.0f}% of this score, the last 6 about "
            f"{sh[6] * 100:.0f}%, and anything over a year about "
            f"{(1 - sh[12]) * 100:.0f}%.")
    if agg is not None and getattr(agg, "n_months", 0):
        base += (f" You have {agg.n_months} complete month"
                 f"{'s' if agg.n_months != 1 else ''} of history"
                 + (" (this month is still in progress and is left out)."
                    if agg.partial_current else "."))
    return base


def essential_monthly(store) -> float:
    """Average monthly essential outflow, $/mo.

    Prefers the recency-weighted figure from the actual ledger, since that
    captures the whole cost of living — bills, groceries, insurance, medical,
    fuel, loan payments — not just the charges the recurrence detector happened
    to lock on to. Falls back to summing essential recurring items when there
    isn't enough transaction history to weight.
    """
    try:
        agg = monthly_aggregates(store.transactions())
    except Exception:
        agg = None
    if agg is not None and agg.essential > 0:
        return agg.essential
    total = 0.0
    for it in store.recurring():
        if not it.active or it.amount >= 0 or it.kind == "ignore":
            continue
        if bucket_of(categorize(it.name, it.amount)) == "essential":
            total += abs(it.monthly_equivalent)
    return total


def emergency_fund_target(store, months: int = 3) -> float:
    """Cash buffer covering `months` of essential spending.

    Three months is the common floor in mainstream guidance; six is the usual
    recommendation for variable or single income. Returns 0 when essential
    spending can't be established, so callers can skip drawing the line.
    """
    return max(0.0, essential_monthly(store) * months)


# --- composition breakdown --------------------------------------------------
def breakdown(agg: Aggregates) -> list[tuple[str, float, str]]:
    """Slices for the composition chart: (label, monthly $, hex color)."""
    friv_other = max(0.0, agg.frivolous - agg.subscriptions)
    slices = [
        ("Essential", agg.essential, "#5B9CE0"),
        ("Frivolous", friv_other, "#E0A83C"),
        ("Subscriptions", agg.subscriptions, "#9B7EDE"),
        ("Giving", agg.giving, "#4FB3A4"),
        ("Uncategorized", agg.unknown, "#8B98A9"),
    ]
    return [(lbl, val, col) for lbl, val, col in slices if val > 0.005]


# --- habits score -----------------------------------------------------------
# Two independent sub-scores, then a quadrant-aware combination.
#
#   H = habit  sub-score, from FESR = frivolous / essential   (what you control)
#   M = means  sub-score, from the savings rate r = (I-S)/I   (your situation)
#
# Both are piecewise-linear through published-guidance anchor points, which
# keeps them auditable — you can read a number off the table and check it.
#
#   FESR  0.00 -> 10    nothing frivolous at all
#         0.30 ->  8.5  wants are a third of needs
#         0.60 ->  5.0  the 50/30/20 ceiling — exactly "as recommended"
#         1.00 ->  3.0  as much on wants as on needs
#         2.00 ->  1.0  twice as much on wants as on needs
#
#   r    -0.50 ->  0    spending 1.5x what you earn
#        -0.20 ->  2.0
#         0.00 ->  4.0  breaking even
#         0.10 ->  6.5
#         0.20 ->  8.5  the 50/30/20 savings target
#         0.35 -> 10
#
# The four cases the score has to tell apart, and where this lands them:
#
#   high FESR + low net   -> both sub-scores low, and the interaction penalty
#                            bites hardest here: distress that is self-inflicted
#   high FESR + high net  -> average. Not in danger, but leaving money on the table
#   low FESR + low net    -> okay. A bad situation, not a bad habit
#   low FESR + high net   -> best. Saving about as well as the inputs allow
_FESR_ANCHORS = [(0.00, 10.0), (0.30, 8.5), (0.60, 5.0), (1.00, 3.0),
                 (2.00, 1.0), (FESR_CAP, 0.0)]
_MEANS_ANCHORS = [(-0.50, 0.0), (-0.20, 2.0), (0.00, 4.0), (0.10, 6.5),
                  (0.20, 8.5), (0.35, 10.0)]

W_HABIT = 0.60          # habits carry more weight than circumstances
W_MEANS = 0.40
DANGER_WEIGHT = 2.5     # extra penalty when habits AND means are both poor

_GRADE_BANDS = [(9.3, "A+"), (8.6, "A"), (8.0, "A−"), (7.3, "B+"), (6.6, "B"),
                (6.0, "B−"), (5.3, "C+"), (4.6, "C"), (4.0, "C−"), (3.0, "D+"),
                (2.0, "D"), (1.2, "D−"), (-1.0, "F")]


def _letter(score: float) -> str:
    for lo, g in _GRADE_BANDS:
        if score >= lo:
            return g
    return "F"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _interp(x: float, anchors: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation through (input, score) anchors."""
    xs = [a for a, _ in anchors]
    ys = [b for _, b in anchors]
    return float(np.interp(_clamp(x, xs[0], xs[-1]), xs, ys))


def habit_term(fesr: float) -> float:
    """Habit sub-score in [0, 10] from the frivolous-to-essential ratio."""
    return _interp(fesr, _FESR_ANCHORS)


def means_term(savings_rate: float) -> float:
    """Means sub-score in [0, 10] from the savings rate."""
    return _interp(savings_rate, _MEANS_ANCHORS)


def net_income_term(savings_rate: float) -> float:      # legacy shim
    return means_term(savings_rate) - 5.0


def combine(habit: float, means: float) -> tuple[float, float]:
    """Blend the two sub-scores. Returns (score, danger_penalty).

    A plain weighted mean gets three of the four quadrants right but is too
    kind to the fourth: high frivolous spending *and* a deficit is not the
    average of two mediocre situations, it is the one case where the shortfall
    is being caused by the spending. The penalty term is a product, so it is
    negligible unless both sub-scores are poor, and largest when both are worst.
    """
    penalty = DANGER_WEIGHT * (1.0 - habit / 10.0) * (1.0 - means / 10.0)
    return _clamp(W_HABIT * habit + W_MEANS * means - penalty, 0.0, 10.0), penalty


@dataclass
class Score:
    value: float                # 0..10
    letter: str
    habit_component: float      # H, 0..10, from FESR
    means_component: float      # M, 0..10, from the savings rate
    danger_penalty: float       # interaction term subtracted
    savings_rate: float         # r
    fesr: float                 # frivolous / essential
    frivolous: float            # $/mo
    essential: float            # $/mo
    net_income: float           # $/mo
    quadrant: str               # which of the four cases this is
    headline: str               # one-line verdict for that quadrant
    means_line: str             # plain-language circumstance descriptor
    detail: str                 # benchmark comparison
    unknown_share: float = 0.0  # fraction of spending still uncategorised
    provisional: bool = False   # too much unknown to trust the ratio

    # legacy aliases
    @property
    def ratio(self) -> float:
        return self.fesr

    @property
    def net_adjustment(self) -> float:
        return self.means_component - 5.0

    @property
    def savings_adjustment(self) -> float:
        return self.net_adjustment

    @property
    def nonessential(self) -> float:
        return self.frivolous

    @property
    def uncategorised_share(self) -> float:
        return self.unknown_share


_QUADRANTS = {
    ("high", "low"): (
        "overspending",
        "Your shortfall is being driven by discretionary spending."),
    ("high", "high"): (
        "comfortable but loose",
        "You're not at risk, but you're leaving real money on the table."),
    ("low", "low"): (
        "squeezed, not careless",
        "Money is tight, but it isn't your spending habits causing it."),
    ("low", "high"): (
        "disciplined and saving",
        "You're saving about as well as your income allows."),
}

# The (low FESR, low means) verdict absolves spending outright, and that is only
# true when spending really is modest against income. A wants:needs ratio inside
# the guideline says the *shape* of spending is sensible; it says nothing about
# the *scale*, because both sides can be inflated together. When wants are over
# the 30%-of-income guideline as well, the shortfall is at least partly the
# spending, and telling someone otherwise is bad advice dressed as reassurance.
_SQUEEZED_BUT_SPENDING = (
    "squeezed, and wants are over guideline too",
    "Your wants are modest next to your bills, but both are large against your "
    "income — the shortfall is partly the spending.")
_SQUEEZED_HIGH_NEEDS = (
    "squeezed by fixed costs",
    "Your essentials alone eat more of your income than the guideline allows. "
    "Spending discipline isn't the problem here; the fixed costs are.")


def _means_descriptor(r: float, surplus: float) -> str:
    s = round(surplus)
    if r < -0.005:
        return f"Spending more than you earn — about {fmt_money(-s)}/mo short"
    if r < 0.05:
        return "Breaking even — little or no cushion"
    if r < 0.15:
        return f"Saving modestly — about {fmt_money(s)}/mo"
    return f"Saving comfortably — about {fmt_money(s)}/mo"


def spending_score(agg: Aggregates) -> Score | None:
    """Benchmarked habits score in [0, 10].

        score = 0.6*H(FESR) + 0.4*M(r) - danger(H, M)

    H is anchored so that FESR = 0.60 — the 50/30/20 ceiling — scores exactly
    5/10, and M so that a 20% savings rate scores 8.5/10. The grade therefore
    reads against published guidance rather than against an invented curve.
    """
    r = agg.savings_rate
    fesr = agg.fesr
    if r is None or fesr is None:
        return None
    habit = habit_term(fesr)
    means = means_term(r)
    value, penalty = combine(habit, means)

    fesr_side = "high" if fesr > FESR_TARGET else "low"
    means_side = "low" if r < 0.05 else "high"
    quadrant, headline = _QUADRANTS[(fesr_side, means_side)]

    # Refine the one verdict that makes a causal claim. "Not your spending
    # habits" is only honest if spending is actually modest against income.
    wants_sh, needs_sh = agg.wants_share, agg.needs_share
    if (fesr_side, means_side) == ("low", "low") and wants_sh is not None:
        if wants_sh > WANTS_INCOME_TARGET:
            quadrant, headline = _SQUEEZED_BUT_SPENDING
        elif needs_sh is not None and needs_sh > NEEDS_INCOME_TARGET:
            quadrant, headline = _SQUEEZED_HIGH_NEEDS

    pct_of_target = fesr / FESR_TARGET * 100 if FESR_TARGET else 0.0
    # Report the rule as the rule is actually written — three shares of income —
    # then the ratio the habit sub-score is read from. Quoting only the ratio
    # let someone 8% of income underwater read "within the 50/30/20 guideline"
    # and conclude they were following it.
    if wants_sh is not None and needs_sh is not None:
        shares = (
            f"Against 50/30/20, your income splits "
            f"{needs_sh * 100:.0f}% needs / {wants_sh * 100:.0f}% wants / "
            f"{r * 100:.0f}% saved — the guideline is 50 / 30 / 20. ")
    else:
        shares = ""
    detail = (
        shares +
        f"You spend {fmt_money(agg.frivolous)}/mo on wants against "
        f"{fmt_money(agg.essential)}/mo of essentials — a ratio of {fesr:.2f}, "
        f"{'above' if fesr > FESR_TARGET else 'within'} the wants-to-needs "
        f"ceiling of {FESR_TARGET:.2f} ({pct_of_target:.0f}% of it), which is "
        f"what the habits half of the score reads. Net income is "
        f"{fmt_money(agg.surplus, signed=True)}/mo, a "
        f"{'savings' if r >= 0 else 'deficit'} rate of {abs(r) * 100:.0f}% "
        f"against the 20% target.")

    provisional = agg.unknown_share > 0.25
    if provisional:
        detail += (f" {agg.unknown_share * 100:.0f}% of your spending is still "
                   f"uncategorized and is left out of the ratio, so this grade "
                   f"is provisional.")

    return Score(value=value, letter=_letter(value), habit_component=habit,
                 means_component=means, danger_penalty=penalty, savings_rate=r,
                 fesr=fesr, frivolous=agg.frivolous, essential=agg.essential,
                 net_income=agg.surplus, quadrant=quadrant, headline=headline,
                 means_line=_means_descriptor(r, agg.surplus), detail=detail,
                 unknown_share=agg.unknown_share, provisional=provisional)


# --- tailored suggestions ---------------------------------------------------
@dataclass
class Suggestion:
    title: str
    detail: str
    monthly_impact: float = 0.0       # estimated $/mo freed if acted on
    kind: str = "trim"                # trim|subscriptions|categorize|info|praise
    rank: int = 0                     # 1 = biggest dollar lever


# Subscription-like categories only. The broad frivolous bucket would sweep in
# recurring dining (a coffee run every few days looks recurring), which is a
# habit, not a subscription.
_SUBSCRIPTION_CATS = {"Subscriptions", "Entertainment"}


def _recurring_subscriptions(recurring: list[RecurringItem]) -> list[RecurringItem]:
    """Active recurring charges that are genuinely subscription-like."""
    out = []
    for it in recurring:
        if not it.active or it.kind not in ("bill", "ignore") or it.amount >= 0:
            continue
        if it.cadence == "weekly":               # too frequent to be a subscription
            continue
        if categorize(it.name, it.amount) in _SUBSCRIPTION_CATS:
            out.append(it)
    return sorted(out, key=lambda x: x.monthly_equivalent)   # most negative first


def category_monthly(tx: pd.DataFrame, window: int | None = None,
                     half_life: float = HALF_LIFE_MONTHS) -> dict[str, float]:
    """Recency-weighted average monthly spend per category, $/month.

    Same decay as `monthly_aggregates`, so a category's figure here is directly
    comparable to the totals on the score panel. A splurge two years ago barely
    registers; last month's dominates.
    """
    if tx is None or tx.empty:
        return {}
    df = tx.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return {}
    df["ym"] = df["date"].dt.to_period("M")
    this_month = pd.Timestamp(date.today()).to_period("M")
    complete = df[df["ym"] < this_month]
    if complete.empty:
        return {}
    months = sorted(complete["ym"].unique())[-(window or LOOKBACK_MONTHS):]
    sel = complete[complete["ym"].isin(months)]
    w = _month_weights(list(months), half_life)
    wsum = sum(w.values())
    if wsum <= 0:
        return {}
    out: dict[str, float] = {}
    for cat, grp in sel.groupby("category"):
        if cat in INCOME or cat in EXCLUDE:
            continue
        per_month = grp[grp["amount"] < 0].groupby("ym")["amount"].sum()
        total = sum(w.get(m, 0.0) * -float(v) for m, v in per_month.items())
        spend = total / wsum
        if spend > 0.005:
            out[cat] = spend
    return out


_category_monthly = category_monthly          # legacy private alias

TRIM_FRACTION = 0.25          # the partial cut suggested on a flexible category


def suggestions(agg: Aggregates, recurring: list[RecurringItem],
                tx: pd.DataFrame, window: int | None = None) -> list[Suggestion]:
    """Actionable suggestions ranked by real dollars.

    Every lever is costed first and ordered afterwards, so the prose can state a
    lever's true rank. The previous version generated the subscriptions item
    separately and then described the top category as "your largest after
    subscriptions" — which read as nonsense when subscriptions were $35/mo and
    the category was $500/mo. Nothing is described by position here unless it
    actually holds that position.
    """
    levers: list[Suggestion] = []
    notes: list[Suggestion] = []
    r = agg.savings_rate or 0.0
    cat_month = category_monthly(tx, window)

    # --- lever: subscriptions --------------------------------------------
    subs = _recurring_subscriptions(recurring)
    if subs:
        total = sum(abs(s.monthly_equivalent) for s in subs)
        listing = ", ".join(f"{s.name} ({fmt_money(abs(s.monthly_equivalent))}/mo)"
                            for s in subs[:6])
        levers.append(Suggestion(
            title=f"{len(subs)} subscription{'s' if len(subs) != 1 else ''} "
                  f"= {fmt_money(total)}/mo ({fmt_money(total * 12)}/yr)",
            detail=f"{listing}. Cancelling the ones you use least frees cash "
                   f"without changing daily life.",
            monthly_impact=total, kind="subscriptions"))

    # --- levers: flexible categories -------------------------------------
    flex = [(c, v) for c, v in cat_month.items()
            if bucket_of(c) == "frivolous" and c != SUBSCRIPTION_CAT and v > 0]
    flex.sort(key=lambda kv: -kv[1])
    for cat, val in flex[:3]:
        levers.append(Suggestion(
            title=f"{cat}: {fmt_money(val)}/mo",
            detail=f"Trimming this by {int(TRIM_FRACTION * 100)}% frees about "
                   f"{fmt_money(val * TRIM_FRACTION)}/mo — try it as a scenario "
                   f"on the Forecast page to see the long-run effect.",
            monthly_impact=val * TRIM_FRACTION, kind="trim"))

    # --- lever: uncategorised spending -----------------------------------
    if agg.unknown > max(25.0, 0.05 * agg.spending):
        share = agg.unknown_share
        levers.append(Suggestion(
            title=f"Uncategorized spending: {fmt_money(agg.unknown)}/mo",
            detail=(f"{share * 100:.0f}% of your spending isn't labelled, so it "
                    f"can't be judged a need or a want and is left out of your "
                    f"ratio entirely. Categorize it on the Transactions page — "
                    f"Review uncategorized does the highest-value ones first — "
                    f"and the grade stops being provisional."),
            monthly_impact=agg.unknown, kind="categorize"))

    # Rank every lever by actual dollars, then describe by true position.
    levers.sort(key=lambda s: -s.monthly_impact)
    for i, s in enumerate(levers, start=1):
        s.rank = i
    if levers:
        top = levers[0]
        if top.kind == "trim":
            top.detail = "Your largest flexible expense. " + top.detail
        elif top.kind == "subscriptions":
            top.detail = "Your largest single lever. " + top.detail
    for s in levers[1:]:
        if s.kind == "trim":
            s.detail = f"#{s.rank} by monthly cost. " + s.detail

    # --- informational notes (never ranked as levers) ---------------------
    if agg.income > 0:
        housing = cat_month.get("Housing", 0.0)
        if housing > HOUSING_NOTE_THRESHOLD * agg.income:
            notes.append(Suggestion(
                title=f"Housing is {housing / agg.income * 100:.0f}% of income",
                detail="Above the ~30% guideline, housing is a heavy fixed load. "
                       "It's hard to change quickly, but worth knowing it's the "
                       "structural driver — not day-to-day spending.",
                kind="info"))

    if r >= SAVINGS_TARGET * 0.75 and (agg.fesr if agg.fesr is not None else 99) <= FESR_TARGET:
        notes.append(Suggestion(
            title="You're living within your means",
            detail=f"You save about {r * 100:.0f}% of income against a 20% "
                   f"target, and keep wants at {agg.fesr:.2f}x essentials "
                   f"against a 0.60 guideline. Anything beyond this is "
                   f"optimization, not necessity.",
            kind="praise"))

    return levers + notes
