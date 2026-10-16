"""Recurring-transaction detection.

Pipeline:
1. Normalize descriptions (strip store numbers, POS noise, digits) so
   "ACME PAYROLL 8842 DD" and "ACME PAYROLL 8843 DD" collapse to one key.
2. Group by (normalized key, sign). For groups with >= MIN_OCCURRENCES,
   examine inter-arrival intervals with robust statistics (median / MAD)
   and classify against known cadences with per-cadence tolerances.
3. Semimonthly (e.g. paid on the 1st and 15th) is disambiguated from
   biweekly by checking whether occurrences concentrate on exactly two
   days of the month.
4. Score confidence from occurrence count, interval regularity, and
   amount stability; keep suggestions above a floor.

Future occurrences are generated from the *last observed date* forward,
stepping either by a fixed day interval (weekly/biweekly) or through the
calendar (monthly family), clamping day-of-month to month length.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from .categorize import categorize
from .models import RecurringItem
from .util import add_months, clamp_day

MIN_OCCURRENCES = 3
CONFIDENCE_FLOOR = 0.45

_NOISE = {
    "POS", "DEBIT", "CREDIT", "CARD", "PURCHASE", "ACH", "ONLINE", "WEB",
    "PMT", "AUTOPAY", "RECURRING", "PAYMENT", "PYMT", "EPAY", "BILLPAY",
    "WITHDRAWAL", "DEPOSIT", "CO", "INC", "LLC", "CORP", "DES", "ID",
    "PPD", "CCD", "REF", "TRN", "TYPE", "CKCD", "VISA", "MC",
    "DIR", "DEP", "COM", "USA", "WWW", "TO", "FROM", "PAYMENT", "PMTS",
}
_token_re = re.compile(r"[A-Z&']+")


@lru_cache(maxsize=8192)
def normalize_desc(s: str) -> str:
    toks = _token_re.findall((s or "").upper())
    toks = [t for t in toks if t not in _NOISE and len(t) > 1]
    return " ".join(toks[:4]) if toks else (s or "").upper().strip()[:24]


# cadence -> (nominal days, interval tolerance in days)
_CADENCES = [
    ("weekly", 7.0, 2.0),
    ("biweekly", 14.0, 3.5),
    ("monthly", 30.44, 6.0),
    ("quarterly", 91.31, 12.0),
    ("annual", 365.25, 25.0),
]


def _dom_clusters(days_of_month: np.ndarray, tol: int = 1) -> list[int]:
    """Cluster day-of-month values within +/-tol; return representative days."""
    vals = sorted(set(int(d) for d in days_of_month))
    clusters: list[list[int]] = []
    for v in vals:
        if clusters and v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    # Month-end wraparound: 31 and 1 are the same slot. The median has to be
    # taken in a shifted frame — a plain median of [1, 2, 30, 31] is 16, which
    # would anchor a month-end bill to the middle of the month.
    wrapped: int | None = None
    if len(clusters) > 1 and clusters[0][0] <= tol and clusters[-1][-1] >= 31 - tol:
        low = clusters.pop(0)
        high = clusters.pop()
        rep = int(round(float(np.median(high + [d + 31 for d in low]))))
        wrapped = rep - 31 if rep > 31 else rep
    reps = [int(round(float(np.median(c)))) for c in clusters]
    if wrapped is not None:
        reps.append(wrapped)
    return sorted(reps)


def detect(tx: pd.DataFrame) -> list[RecurringItem]:
    """tx: frame with columns date, description, amount, category (optional).
    Transfers and reimbursable business rows are excluded. Paycheck amounts
    exclude any separately marked business reimbursement."""
    if tx.empty:
        return []
    from .categorize import personal_transactions
    df = personal_transactions(tx)
    df["date"] = pd.to_datetime(df["date"])
    df["norm"] = df["description"].map(normalize_desc)
    df["sign"] = np.where(df["amount"] > 0, "+", "-")

    items: list[RecurringItem] = []
    for (norm, sign), g in df.groupby(["norm", "sign"]):
        g = g.sort_values("date")
        # collapse same-day repeats to one event for interval analysis
        daily = g.groupby(g["date"].dt.date)["amount"].sum()
        n = len(daily)
        if n < MIN_OCCURRENCES:
            continue
        dts = np.array([pd.Timestamp(d) for d in daily.index])
        intervals = np.diff(dts).astype("timedelta64[D]").astype(float)
        med_iv = float(np.median(intervals))
        mad_iv = float(np.median(np.abs(intervals - med_iv)))
        amts = daily.values.astype(float)
        med_amt = float(np.median(amts))
        mad_amt = float(np.median(np.abs(amts - med_amt)))
        cv = mad_amt / max(abs(med_amt), 1e-9)

        doms = np.array([d.day for d in daily.index])
        cadence = None
        days_of_month: list[int] = []

        # semimonthly: median interval near a half-month AND exactly two
        # day-of-month clusters (e.g. {1, 15})
        if 12.0 <= med_iv <= 19.0:
            reps = _dom_clusters(doms)
            if len(reps) == 2 and n >= 4:
                cadence = "semimonthly"
                days_of_month = sorted(reps)

        if cadence is None:
            for name, nominal, tol in _CADENCES:
                if abs(med_iv - nominal) <= tol and mad_iv <= tol:
                    cadence = name
                    break
        if cadence is None:
            continue
        if cadence in ("monthly", "quarterly", "annual"):
            reps = _dom_clusters(doms, tol=2)
            days_of_month = [reps[0]] if len(reps) == 1 else [int(np.median(doms))]

        tol_used = dict((c[0], c[2]) for c in _CADENCES).get(cadence, 3.0)
        c_count = min(n, 8) / 8.0
        c_interval = max(0.0, 1.0 - mad_iv / max(tol_used, 1e-9))
        c_amount = max(0.0, 1.0 - min(cv / 0.5, 1.0))
        confidence = round(0.30 * c_count + 0.40 * c_interval + 0.30 * c_amount, 3)
        if confidence < CONFIDENCE_FLOOR:
            continue

        raw_name = g["description"].iloc[-1]
        # The ledger already contains manual/learned categories. Do not undo a
        # user's decision by reapplying the raw keyword rule to the same bill.
        cat = g["category"].iloc[-1] if "category" in g else ""
        if not cat or cat in {"Misc", "Other income"}:
            cat = categorize(raw_name, med_amt)
        kind = "income" if med_amt > 0 else ("loan_payment" if cat == "Debt payment" else "bill")
        items.append(RecurringItem(
            key=f"{norm}|{sign}",
            name=norm.title() if norm else raw_name[:32],
            cadence=cadence,
            amount=round(med_amt, 2),
            last_date=daily.index[-1],
            days_of_month=days_of_month,
            confidence=confidence,
            amount_cv=round(cv, 3),
            n_seen=n,
            kind=kind,
        ))
    items.sort(key=lambda it: -abs(it.monthly_equivalent))
    return items


def personal_recurring(tx: pd.DataFrame, items: list[RecurringItem],
                       as_of: date | None = None) -> list[RecurringItem]:
    """Correct existing schedules affected by a business label or paycheck split.

    Detection can predate a user's correction. Refit affected keys using only
    personal transactions; retain user-owned switches, names and loan links.
    An absent personal pattern stays as an inactive key so it cannot fall back
    into random weekly sampling. Saved schedules are not edited.
    """
    from dataclasses import replace
    from .categorize import BUSINESS_CATEGORY
    if tx.empty:
        return items
    past = tx[tx["date"] <= pd.Timestamp(as_of or date.today())]
    business = past["category"].eq(BUSINESS_CATEGORY)
    if "business_reimbursement" in past:
        business = business | past["business_reimbursement"].gt(0)
    affected = {f"{normalize_desc(d)}|{'+' if a > 0 else '-'}"
                for d, a in zip(past.loc[business, "description"], past.loc[business, "amount"])}
    if not affected:
        return items
    fitted = {it.key: it for it in detect(past)}
    out = []
    for it in items:
        if it.key not in affected:
            out.append(it)
        elif it.key in fitted:
            out.append(replace(fitted[it.key], id=it.id, name=it.name,
                               active=it.active, kind=it.kind,
                               linked_account_id=it.linked_account_id))
        else:
            out.append(replace(it, active=False))
    return out


def occurrences(item: RecurringItem, start: date, end: date) -> list[date]:
    """Projected occurrence dates in (start, end], anchored at last_date."""
    out: list[date] = []
    last = item.last_date
    if item.cadence in ("weekly", "biweekly"):
        step = timedelta(days=7 if item.cadence == "weekly" else 14)
        d = last + step
        while d <= end:
            if d > start:
                out.append(d)
            d += step
    elif item.cadence == "semimonthly":
        d1, d2 = (item.days_of_month + [1, 15])[:2]
        y, m = last.year, last.month
        for _ in range(0, 12 * 30):
            for dd in sorted((d1, d2)):
                cand = clamp_day(y, m, dd)
                if cand > last and start < cand <= end:
                    out.append(cand)
            nxt = add_months(date(y, m, 1), 1)
            y, m = nxt.year, nxt.month
            if date(y, m, 1) > end:
                break
    else:
        step_months = {"monthly": 1, "quarterly": 3, "annual": 12}[item.cadence]
        dom = item.days_of_month[0] if item.days_of_month else last.day
        d = last
        while True:
            nxt = add_months(d, step_months)
            d = clamp_day(nxt.year, nxt.month, dom)
            if d > end:
                break
            if d > start:
                out.append(d)
    return out


def next_occurrence(item: RecurringItem, today: date) -> date | None:
    occ = occurrences(item, today, add_months(today, 14))
    return occ[0] if occ else None
