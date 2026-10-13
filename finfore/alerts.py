"""Things that changed without anybody deciding to change them.

Every other page in this app answers a question you asked. This one answers a
question you did not: *what moved while I was not looking?*

A recurring charge that quietly goes up is the highest-signal, lowest-effort
finding in personal finance. Nobody notices a subscription going from $9.99 to
$13.99 — it is four dollars, it arrives on a statement with forty other lines,
and by the time it is obvious it has been running for a year. The ledger knows
perfectly well that it happened; nothing was looking.

The hard part is not spotting a change, it is *not* spotting one. A bill that
varies by its nature — electricity in August, a phone bill with roaming on it,
groceries — will differ from last month every single time, and an alert that
fires on all of them is one nobody reads. So the test is not "is this month
different?" but "is this month different by more than this bill's own normal
variation?", which is what the MAD gate below measures. A subscription that has
been $9.99 eleven times running has a MAD of zero and a two-dollar rise is
enormous; a power bill that swings $60 either way has to move a long way before
anyone should be told about it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .models import CADENCE_DAYS, CADENCE_LABEL, RecurringItem
from .recurrence import normalize_desc
from .store import Store

# How many occurrences at the old level are needed before a new one means
# anything. Two points make a line and three make a habit; below that there is
# no "usual" to be different from.
MIN_BASELINE = 3
# Multiples of the baseline's own median absolute deviation. Three is the
# conventional robust-outlier bar and it is doing the real work here: it is what
# separates a price rise from a hot August.
NOISE_MADS = 3.0
# Floors, so a rounding difference on a large bill is not news. Both must be
# cleared: a change has to be worth a dollar in itself *and* worth a couple of
# coffees a year once its cadence is taken into account.
MIN_DOLLARS = 1.0
MIN_ANNUAL = 24.0
# How far back a change is still worth reporting. A rise found six months after
# it happened is history, not news — and it will have been absorbed into the
# habits score and the forecast by then anyway.
MAX_AGE_DAYS = 400


@dataclass
class BillChange:
    """One recurring charge that is not what it used to be.

    Amounts are in "money out is positive" terms for bills and "money in is
    positive" for income, because that is how the two are read aloud. ``delta``
    is always ``new - old`` in those terms, so its sign means *more* — which is
    good news for income and bad news for a bill, and is why ``worse`` exists
    rather than being inferred from the sign at each call site.
    """
    key: str
    name: str
    cadence: str
    category: str
    is_income: bool
    old_amount: float
    new_amount: float
    delta: float
    since: date                  # first occurrence at the new level
    n_before: int
    n_after: int
    baseline_mad: float          # the bill's own normal variation, per occurrence

    @property
    def pct(self) -> float:
        return self.delta / self.old_amount if self.old_amount else 0.0

    @property
    def per_month(self) -> float:
        return self.delta * (30.44 / CADENCE_DAYS.get(self.cadence, 30.44))

    @property
    def per_year(self) -> float:
        return self.per_month * 12.0

    @property
    def worse(self) -> bool:
        """Is this change in the direction that costs you money?"""
        return self.delta < 0 if self.is_income else self.delta > 0

    @property
    def direction(self) -> str:
        return "up" if self.delta > 0 else "down"

    @property
    def steady(self) -> bool:
        """Was the old level flat enough that the change is unmistakable?"""
        return self.baseline_mad <= 0.01 * max(abs(self.old_amount), 1.0)

    def headline(self) -> str:
        verb = "went up" if self.delta > 0 else "went down"
        return (f"{self.name} {verb} {_money(abs(self.delta))} "
                f"— {_money(self.old_amount)} → {_money(self.new_amount)}")

    def detail(self) -> str:
        cad = CADENCE_LABEL.get(self.cadence, self.cadence).lower()
        when = self.since.strftime("%B %Y")
        rate = (f"{_money(abs(self.per_year))} a year"
                if abs(self.per_year) >= 12 else
                f"{_money(abs(self.per_month))} a month")
        lead = ("costing you" if self.worse else "saving you")
        bits = [f"A {cad} charge, changed in {when}"
                if not self.is_income else
                f"{CADENCE_LABEL.get(self.cadence, self.cadence)} income, "
                f"changed in {when}",
                f"{lead} {rate}"]
        if self.n_after == 1:
            bits.append("seen once so far, so it could still be a one-off")
        else:
            bits.append(f"and it has stayed there {self.n_after} times")
        if not self.steady:
            bits.append(f"this one does normally vary by about "
                        f"{_money(self.baseline_mad)}, so the change is real "
                        f"but the exact figure will drift")
        return "; ".join(bits) + "."


def _money(v: float) -> str:
    return f"${abs(v):,.0f}" if abs(v) >= 100 else f"${abs(v):,.2f}"


def _occurrence_amounts(tx: pd.DataFrame, key: str) -> pd.Series:
    """Per-occurrence signed totals for one recurring key, oldest first.

    Rebuilt from the ledger the same way `recurrence.detect` builds it — same
    normalisation, same sign split, same same-day collapse — because the stored
    item keeps only the median. A pattern is its history; the median is a
    summary of it, and a summary cannot be asked whether it moved.
    """
    if tx.empty or "|" not in key:
        return pd.Series(dtype=float)
    norm, sign = key.rsplit("|", 1)
    df = tx
    if "category" in df.columns:
        from .categorize import personal_transactions
        df = personal_transactions(df)
    if df.empty:
        return pd.Series(dtype=float)
    want = df["amount"] > 0 if sign == "+" else df["amount"] < 0
    df = df[want]
    if df.empty:
        return pd.Series(dtype=float)
    df = df[df["description"].map(normalize_desc) == norm]
    if df.empty:
        return pd.Series(dtype=float)
    daily = df.groupby(pd.to_datetime(df["date"]).dt.date)["amount"].sum()
    return daily.sort_index().astype(float)


def _new_level(amounts: np.ndarray) -> int:
    """How many of the trailing occurrences share the newest amount.

    Walks back from the end while each earlier value is within a whisker of the
    latest one, so a bill that rose three months ago is reported as having risen
    three months ago rather than as three separate surprises.
    """
    latest = amounts[-1]
    tol = max(0.02 * abs(latest), 0.01)
    n = 1
    while n < len(amounts) and abs(amounts[-1 - n] - latest) <= tol:
        n += 1
    return n


def detect_change(item: RecurringItem, amounts: pd.Series,
                  today: date | None = None) -> BillChange | None:
    """Has this pattern's amount moved beyond its own normal variation?"""
    if len(amounts) < MIN_BASELINE + 1:
        return None
    vals = amounts.to_numpy(dtype=float)
    dates = list(amounts.index)
    n_after = _new_level(vals)
    baseline = vals[:-n_after]
    if len(baseline) < MIN_BASELINE:
        return None

    med = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - med)))
    latest = float(np.median(vals[-n_after:]))
    raw_delta = latest - med
    if abs(raw_delta) <= NOISE_MADS * mad:
        return None                       # inside this bill's own noise

    since = dates[-n_after]
    since = since if isinstance(since, date) else pd.Timestamp(since).date()
    if today and (today - since).days > MAX_AGE_DAYS:
        return None                       # history, not news

    is_income = item.kind == "income" or med > 0
    flip = 1.0 if is_income else -1.0     # bills read as money out, positive
    ch = BillChange(
        key=item.key, name=item.name, cadence=item.cadence,
        category="income" if is_income else item.kind,
        is_income=is_income,
        old_amount=flip * med, new_amount=flip * latest,
        delta=flip * raw_delta, since=since,
        n_before=len(baseline), n_after=n_after, baseline_mad=mad,
    )
    if abs(ch.delta) < MIN_DOLLARS or abs(ch.per_year) < MIN_ANNUAL:
        return None
    return ch


def bill_changes(store: Store, today: date | None = None,
                 include_income: bool = True) -> list[BillChange]:
    """Every recurring pattern whose amount has moved, worst news first.

    Only *active*, non-ignored patterns are examined: deactivating a pattern is
    the user saying "stop telling me about this", and that has to mean here too.
    """
    today = today or date.today()
    tx = store.transactions()
    if tx.empty:
        return []
    out: list[BillChange] = []
    for it in store.recurring():
        if not it.active or it.kind == "ignore":
            continue
        if it.kind == "income" and not include_income:
            continue
        try:
            ch = detect_change(it, _occurrence_amounts(tx, it.key), today)
        except Exception:
            ch = None                     # one odd pattern must not kill the rest
        if ch is not None:
            out.append(ch)
    # Costs first, largest annual impact at the top; savings and pay rises after.
    out.sort(key=lambda c: (not c.worse, -abs(c.per_year)))
    return out


def annual_total(changes: list[BillChange]) -> float:
    """Net yearly effect of everything found — positive means it costs more."""
    return sum(abs(c.per_year) if c.worse else -abs(c.per_year)
               for c in changes)


def summarise(changes: list[BillChange]) -> str:
    """One line for the dashboard strip."""
    if not changes:
        return ""
    worse = [c for c in changes if c.worse]
    better = [c for c in changes if not c.worse]
    n = len(changes)
    parts = [f"{n} recurring {'charge' if n == 1 else 'charges'} changed "
             f"without being asked to"]
    if worse:
        cost = sum(abs(c.per_year) for c in worse)
        parts.append(f"{len(worse)} costing you {_money(cost)} a year more")
    if better:
        save = sum(abs(c.per_year) for c in better)
        parts.append(f"{len(better)} going your way by {_money(save)} a year")
    return " · ".join(parts) + "."
