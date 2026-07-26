"""Historical reconstruction and descriptive statistics.

Cash-like account balances are reconstructed from the transaction ledger by
anchoring at the user-supplied (balance, balance_date) pair:

    B(t) = B(t0) + F(t) - F(t0),   F(t) = cumulative sum of daily net flow

which is exact in both directions (t before or after the anchor), assuming
the anchor is an end-of-day balance. Loan and investment accounts carry no
transaction ledger; their history is piecewise-linear interpolation through
user snapshots, with the current (balance, balance_date) acting as the final
snapshot.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .categorize import INCOME_CATEGORIES
from .models import Account, AccountType
from .store import Store


def cash_anchor(store: Store, account: Account) -> tuple[pd.Timestamp, float]:
    """The (date, balance) pair a cash account's ledger is anchored to.

    The account's own (balance, balance_date) is the default. A snapshot dated
    later wins, because a snapshot is a directly observed balance and the whole
    point of adding one to a cash account is to correct drift after an import
    that didn't reach far enough back.
    """
    anchor_d = pd.Timestamp(account.balance_date)
    anchor_b = float(account.balance)
    try:
        snaps = store.snapshots(account.id)
    except Exception:
        return anchor_d, anchor_b
    if len(snaps):
        snaps = snaps.dropna(subset=["date"]).sort_values("date")
        if len(snaps):
            last_d = pd.Timestamp(snaps["date"].iloc[-1])
            if last_d >= anchor_d:
                return last_d, float(snaps["balance"].iloc[-1])
    return anchor_d, anchor_b


def known_from(tx: pd.DataFrame, account: Account,
               anchor_date: pd.Timestamp | None = None) -> pd.Timestamp:
    """Earliest date this account's balance can actually be reconstructed.

    Two things carry information: the anchor (a balance the user asserted on a
    date) and the transactions (the deltas either side of it). Before the
    earlier of those, nothing is known.
    """
    if anchor_date is None:
        anchor_date = pd.Timestamp(account.balance_date)
    sub = tx[tx["account_id"] == account.id] if not tx.empty else tx
    first_tx = sub["date"].min() if len(sub) and sub["date"].notna().any() else None
    if first_tx is None or pd.isna(first_tx):
        return pd.Timestamp(anchor_date).normalize()
    return min(pd.Timestamp(anchor_date), pd.Timestamp(first_tx)).normalize()


def cash_history(tx: pd.DataFrame, account: Account,
                 index: pd.DatetimeIndex,
                 anchor: tuple[pd.Timestamp, float] | None = None) -> pd.Series:
    """Daily end-of-day balance series for one cash-like account.

    Reconstruction runs back only as far as this account actually knows
    anything — the earlier of its anchor date and its first transaction. Before
    that the earliest reconstructed value is held flat.

    Extrapolating past it produced confidently wrong numbers, and the failure
    was invisible on a single-account ledger. The chart spans the *union* of
    every account's history, so a savings account imported six months ago sits
    on a three-year axis; ``F(t)`` is zero across the years before its first
    row, and B(t₀) − F(t₀) then walks today's balance back through inflows that
    had not happened yet. A $5,000 balance funded by $6,500 of recent transfers
    reported **−$1,500 three years ago** — an account that had no data then, and
    in all likelihood no existence.

    Flat is the right fill rather than dropping the account: an account with no
    transactions at all already holds its balance flat across the whole chart,
    so this is consistent, and excluding it instead would make net worth jump
    on the day its ledger happens to start — a windfall that never happened.
    """
    if anchor is None:
        anchor_date, anchor_bal = pd.Timestamp(account.balance_date), account.balance
    else:
        anchor_date, anchor_bal = anchor
    sub = tx[tx["account_id"] == account.id]
    flows = sub.groupby(sub["date"].dt.normalize())["amount"].sum()
    flows = flows.reindex(index, fill_value=0.0)
    F = flows.cumsum()
    if anchor_date < index[0]:
        f0 = 0.0
    elif anchor_date > index[-1]:
        f0 = float(F.iloc[-1])
    else:
        f0 = float(F.asof(anchor_date))
    series = anchor_bal + (F - f0)

    start = known_from(tx, account, anchor_date)
    if start > index[0]:
        # asof() rather than direct lookup: `start` may fall between grid points
        # when the index is coarser than daily.
        edge = series.asof(start)
        if pd.isna(edge):
            edge = float(series.iloc[0])
        series = series.where(series.index >= start, float(edge))
    return series


def snapshot_history(store: Store, account: Account,
                     index: pd.DatetimeIndex) -> pd.Series:
    """Daily balance series via snapshot interpolation (loans/investments)."""
    snaps = store.snapshots(account.id)
    pts = {pd.Timestamp(account.balance_date): float(account.balance)}
    for _, r in snaps.iterrows():
        pts[pd.Timestamp(r["date"])] = float(r["balance"])
    s = pd.Series(pts).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    full = s.reindex(index.union(s.index)).interpolate(method="time")
    full = full.ffill().bfill()
    return full.reindex(index)


def networth_history(store: Store) -> pd.DataFrame:
    """Daily frame with columns cash, investments, debt, networth over the
    span of the transaction ledger (falling back to the last 90 days)."""
    tx = store.transactions()
    accounts = store.accounts()
    today = pd.Timestamp(date.today())
    if tx.empty:
        start = today - pd.Timedelta(days=90)
    else:
        start = tx["date"].min().normalize()
    index = pd.date_range(start, today, freq="D")
    cash = pd.Series(0.0, index=index)
    invest = pd.Series(0.0, index=index)
    debt = pd.Series(0.0, index=index)
    for a in accounts:
        if a.type in (AccountType.CHECKING, AccountType.SAVINGS):
            cash = cash + cash_history(tx, a, index, anchor=cash_anchor(store, a))
        elif a.type == AccountType.CREDIT:
            # A card carrying a balance is debt, not negative cash — the same
            # split `headline_stats` has made since 0.6.1. This series was
            # still folding the whole card into cash, so the chart's "Net cash"
            # line sat below the "Net cash" stat card by the card balance, and
            # the debt line ignored cards entirely. Net worth came out right
            # either way, which is exactly why it went unnoticed: the error
            # cancelled in the total and showed only in the two components.
            line = cash_history(tx, a, index, anchor=cash_anchor(store, a))
            debt = debt + (-line).clip(lower=0.0)     # owed
            cash = cash + line.clip(lower=0.0)        # overpaid card is cash
        elif a.type == AccountType.INVESTMENT:
            invest = invest + snapshot_history(store, a, index)
        elif a.type == AccountType.LOAN:
            debt = debt + snapshot_history(store, a, index).abs()
    out = pd.DataFrame({"cash": cash, "investments": invest, "debt": debt})
    out["networth"] = out["cash"] + out["investments"] - out["debt"]
    return out


def spending_frame(tx: pd.DataFrame) -> pd.DataFrame:
    """Transactions relevant to income/spending stats (transfers removed)."""
    if tx.empty:
        return tx
    return tx[tx["category"] != "Transfer"].copy()


def monthly_cashflow(tx: pd.DataFrame, months: int = 13) -> pd.DataFrame:
    """Per-month income and spending totals (spending positive)."""
    df = spending_frame(tx)
    if df.empty:
        return pd.DataFrame(columns=["month", "income", "spending"])
    df["month"] = df["date"].dt.to_period("M")
    inc = df[df["amount"] > 0].groupby("month")["amount"].sum()
    spend = -df[df["amount"] < 0].groupby("month")["amount"].sum()
    out = pd.DataFrame({"income": inc, "spending": spend}).fillna(0.0)
    out = out.tail(months).reset_index()
    out["month_start"] = out["month"].dt.to_timestamp()
    return out


def category_spending(tx: pd.DataFrame, days: int = 90, top: int = 9) -> pd.Series:
    """Total spend by category over the trailing window, descending."""
    df = spending_frame(tx)
    if df.empty:
        return pd.Series(dtype=float)
    cutoff = df["date"].max() - pd.Timedelta(days=days)
    df = df[(df["date"] > cutoff) & (df["amount"] < 0)]
    s = (-df.groupby("category")["amount"].sum()).sort_values(ascending=False)
    return s.head(top)


def headline_stats(store: Store) -> dict:
    """Numbers for the dashboard stat cards."""
    accounts = store.accounts()
    tx = store.transactions()
    # A credit card carrying a balance is debt, not negative cash. Counting it
    # in both places broke the identity the stat cards are read as:
    #     networth == cash + investments - debt
    cash = 0.0
    debt = 0.0
    for a in accounts:
        if a.type in (AccountType.CHECKING, AccountType.SAVINGS):
            cash += a.balance
        elif a.type == AccountType.CREDIT:
            # negative = owed; a positive (overpaid) card is spendable cash
            debt += -a.balance if a.balance < 0 else 0.0
            cash += a.balance if a.balance > 0 else 0.0
        elif a.type == AccountType.LOAN:
            debt += abs(a.balance)
    invest = sum(a.balance for a in accounts if a.type == AccountType.INVESTMENT)
    networth = cash + invest - debt
    burn = savings_rate = None
    if not tx.empty:
        df = spending_frame(tx)
        cutoff = df["date"].max() - pd.Timedelta(days=90)
        recent = df[df["date"] > cutoff]
        spend = -recent.loc[recent["amount"] < 0, "amount"].sum()
        income = recent.loc[recent["amount"] > 0, "amount"].sum()
        span_months = max((recent["date"].max() - recent["date"].min()).days, 28) / 30.44
        burn = spend / span_months
        if income > 0:
            savings_rate = (income - spend) / income
    return {
        "networth": networth, "cash": cash, "investments": invest,
        "debt": debt, "burn": burn, "savings_rate": savings_rate,
    }


def weekly_category_matrix(tx: pd.DataFrame, exclude_keys: set[str],
                           max_weeks: int = 78) -> tuple[pd.DataFrame, pd.Series]:
    """Build the empirical weekly behavior sample used by the forecaster.

    Returns (W, irregular_income):
      W: rows = complete historical weeks, cols = spending categories,
         values = positive weekly spend, for transactions NOT matched to an
         active recurring pattern and not transfers.
      irregular_income: weekly totals of non-recurring inflows.
    """
    from .recurrence import normalize_desc  # local import to avoid cycle
    df = spending_frame(tx)
    if df.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    df["norm_key"] = [
        f"{normalize_desc(d)}|{'+' if a > 0 else '-'}"
        for d, a in zip(df["description"], df["amount"])
    ]
    df = df[~df["norm_key"].isin(exclude_keys)]
    if df.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    df["week"] = df["date"].dt.to_period("W-SUN")
    weeks = sorted(df["week"].unique())
    if len(weeks) > 2:
        weeks = weeks[1:-1]           # drop partial edge weeks
    df = df[df["week"].isin(weeks)]
    spend = df[df["amount"] < 0]
    W = spend.pivot_table(index="week", columns="category", values="amount",
                          aggfunc="sum").fillna(0.0).abs()
    W = W.reindex(weeks, fill_value=0.0)
    inc = df[df["amount"] > 0].groupby("week")["amount"].sum()
    inc = inc.reindex(weeks, fill_value=0.0)
    if len(W) > max_weeks:
        W = W.tail(max_weeks)
        inc = inc.tail(max_weeks)
    return W, inc
