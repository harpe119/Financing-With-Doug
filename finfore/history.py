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

from dataclasses import dataclass, replace, field
from datetime import date

import numpy as np
import pandas as pd

from .categorize import INCOME_CATEGORIES, personal_transactions
from .models import Account, AccountType
from .store import Store
from .cache import cached_read


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
                     index: pd.DatetimeIndex,
                     before: str | None = None) -> pd.Series:
    """Daily balance series via snapshot interpolation (loans/investments).

    Before the earliest dated balance the account has, the series reads **zero**
    by default, because that is what was true: a car loan taken out in March
    2025 was not a debt in 2023, and a brokerage account opened last year held
    nothing the year before. Back-filling the first known balance instead —
    which is what this did — drew a $28,000 loan stretching back across the
    whole chart and subtracted it from net worth for years in which it did not
    exist. The chart spans the union of every account's history, so any account
    whose records start later than the oldest one is affected, which in practice
    is most of them.

    Zero is only assumed where there is something to be earlier *than*. An
    account with no snapshots at all has no first recorded day: its balance is
    held flat across the chart, exactly as before. Doing otherwise would show a
    manually entered loan at zero for three years and then have it appear at
    full size on the day it was typed in — a debt materialising out of nowhere,
    which is a worse lie than the one being fixed.

    ``before`` overrides the account's saved preference: ``"zero"`` or
    ``"flat"``. It exists because the default is an assumption, not a
    measurement — a mortgage carried over from another lender, or a brokerage
    account transferred in with its holdings, genuinely did have a balance
    before its first row here, and the user is the only one who knows.
    """
    snaps = store.snapshots(account.id)
    pts = {pd.Timestamp(account.balance_date): float(account.balance)}
    for _, r in snaps.iterrows():
        pts[pd.Timestamp(r["date"])] = float(r["balance"])
    s = pd.Series(pts).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    full = s.reindex(index.union(s.index)).interpolate(method="time")
    full = full.ffill()

    if before is None:
        before = store.prehistory_mode(account.id)
    # len(snaps) rather than len(s): `s` always contains the account's own
    # current balance, so it is never empty and would make every account look
    # as though it had a first recorded day.
    if before == "zero" and len(snaps):
        full = full.where(full.index >= s.index[0], 0.0)
    else:
        full = full.bfill()
    return full.reindex(index)


@dataclass
class LedgerIssue:
    """A cash account whose ledger and entered balance don't agree."""
    account: str
    opening: float          # balance the ledger + entered balance imply at the start
    entered: float          # what the user said the balance is now
    net_flow: float         # sum of every imported flow
    since: date
    duplicates: int = 0     # rows that look like the same event posted twice
    duplicate_value: float = 0.0
    n_rows: int = 0
    inflow: float = 0.0
    outflow: float = 0.0
    kind: str = "cash"
    allow_overdraft: bool = False
    minimum: float = 0.0
    minimum_date: date | None = None
    snapshot_gaps: list = field(default_factory=list)

    @property
    def if_opened_at_zero(self) -> float:
        """What the balance would be today had the account started empty.

        The single most useful number when a balance looks wrong. Most accounts
        did start at zero at the top of their export, so if this figure looks
        like the real balance and the entered one doesn't, the entered one is
        what needs fixing — or the file belongs to a different account.
        """
        return self.net_flow

    @property
    def ok(self) -> bool:
        return not self.needs_review()

    def needs_review(self, tolerance=25.0):
        deposit = self.kind in (AccountType.CHECKING.label, AccountType.SAVINGS.label)
        return bool(self.snapshot_gaps or (deposit and not self.allow_overdraft and
                    (self.opening < -tolerance or self.minimum < -0.005)))

    @property
    def message(self) -> str:
        def money(value):
            return ('-' if value < 0 else '') + f'${abs(value):,.2f}'
        if self.snapshot_gaps:
            gap = self.snapshot_gaps[0]
            return (f"{self.account}: between {gap['start']} and {gap['end']}, the observed balances differ "
                    f"from the posted ledger by {money(gap['difference'])}. Check missing/duplicate rows, "
                    'posting dates and whether both observations are closing posted balances.')
        if self.minimum < -0.005:
            return (f'{self.account}: reconstructed closing balance {money(self.minimum)} on {self.minimum_date}. '
                    'This account is marked as unable to overdraw. Check posting dates, missing/duplicate rows '
                    'and the closing posted balance. This calculation does not establish a bank overdraft.')
        return (f'{self.account}: implied opening balance {money(self.opening)}. Check the export coverage, '
                'posting dates and closing posted balance before relying on the history.')


def reconcile(store: Store, tolerance: float = 25.0) -> list[LedgerIssue]:
    """Flag conflicting observations or negative cash where overdrafts are disallowed.

    The reconstruction assumes a complete posted ledger and a closing posted
    anchor. A warning identifies inconsistent inputs, not a verified overdraft
    or permission to manufacture a balancing transaction. Negative credit-card
    debt and explicitly allowed overdrafts are not errors by sign alone.
    """
    return [i for i in reconcile_all(store) if i.needs_review(tolerance)]


def _ack_key(issue: LedgerIssue) -> str:
    return issue.account


def _ack_value(issue: LedgerIssue) -> float:
    """Stable discrepancy fingerprint, exactly representable by legacy numeric settings."""
    # Include the kind of inconsistency so a new middle-of-history dip or a
    # changed observation reopens the warning even if opening cash is unchanged.
    import hashlib
    import json
    content = json.dumps([round(issue.opening,2), issue.minimum, str(issue.minimum_date),
                          issue.snapshot_gaps, issue.allow_overdraft, issue.kind],sort_keys=True)
    return int(hashlib.sha256(content.encode()).hexdigest()[:12],16)


def unacknowledged(store: Store, tolerance: float = 25.0) -> list[LedgerIssue]:
    """Ledger problems the user has not already said they know about.

    What the dashboard nags with. `reconcile` remains the unfiltered truth and
    is what the Accounts → Check my balances table reports, because a dedicated
    page someone opened on purpose should never hide a finding — dismissing a
    banner is a statement about the banner, not about the arithmetic.
    """
    ack = store.ledger_acknowledgements()
    out = []
    for i in reconcile(store, tolerance):
        seen = ack.get(_ack_key(i))
        if seen is not None and abs(float(seen) - _ack_value(i)) < 1.0:
            continue
        out.append(i)
    return out


def acknowledge(store: Store, issues: list[LedgerIssue]) -> None:
    """Record that the user has read these, at the numbers they now hold."""
    ack = store.ledger_acknowledgements()
    ack.update({_ack_key(i): _ack_value(i) for i in issues})
    store.set_ledger_acknowledgements(ack)


@cached_read
def reconcile_all(store: Store) -> list[LedgerIssue]:
    """The same check for every cash account, whether or not it looks wrong.

    Reported in full because the useful comparison is *between* accounts: when
    three reconcile to a sensible opening and the fourth lands at −$1,469, the
    odd one out is obvious, and the argument that the reconstruction is broken
    dies immediately.
    """
    tx = store.transactions()
    out: list[LedgerIssue] = []
    for a in store.accounts():
        if not a.type.is_cashlike:
            continue
        sub = tx[tx["account_id"] == a.id] if not tx.empty else tx
        snaps = store.snapshots(a.id)
        if sub.empty and snaps.empty:
            continue
        net = float(sub["amount"].sum())
        start = known_from(tx, a, pd.Timestamp(a.balance_date))
        anchor_date, anchor_bal = cash_anchor(store, a)
        # The balance *before* the first row, not after it. Taking the first
        # point of the daily series instead includes that day's transactions,
        # which mislabels any account whose earliest row is large — a card
        # whose first line is a $1,219 purchase looks like it opened $1,219
        # overdrawn when it opened at zero and then bought something.
        prior = float(sub.loc[sub["date"] <= anchor_date, "amount"].sum())
        opening = anchor_bal - prior
        n_dup, dup_val = _double_postings(sub)
        grid = pd.date_range(min(start, pd.Timestamp(date.today())), date.today())
        line = cash_history(tx, a, grid, anchor=(anchor_date, anchor_bal))
        points = {pd.Timestamp(a.balance_date): float(a.balance)}
        for row in snaps.itertuples():
            points[pd.Timestamp(row.date)] = float(row.balance)
        points = sorted((d,b) for d,b in points.items() if d <= pd.Timestamp(date.today()))
        gaps = []
        for (d0,b0),(d1,b1) in zip(points,points[1:]):
            delta = float(sub.loc[(sub.date > d0) & (sub.date <= d1),'amount'].sum())
            difference = round((b1-b0)-delta,2)
            if abs(difference) >= .01:
                gaps.append(dict(start=d0.date().isoformat(),end=d1.date().isoformat(),difference=difference))
        out.append(LedgerIssue(
            account=a.name, opening=opening,
            entered=float(anchor_bal), net_flow=net, since=start.date(),
            duplicates=n_dup, duplicate_value=dup_val, n_rows=len(sub),
            inflow=float(sub.loc[sub["amount"] > 0, "amount"].sum()),
            outflow=float(-sub.loc[sub["amount"] < 0, "amount"].sum()),
            kind=a.type.label, allow_overdraft=a.allow_overdraft,
            minimum=round(float(line.min()),2), minimum_date=line.idxmin().date(),snapshot_gaps=gaps))
    return out


def _double_postings(sub: pd.DataFrame) -> tuple[int, float]:
    """Rows that look like one event posted twice under different wording.

    Same account, same day, same amount, but a different description — which
    is exactly what the import hash cannot collapse, because the description is
    part of it. Two genuine identical purchases on one day look the same from
    here, so this counts *candidates* and is only ever used to explain a
    discrepancy that has already been detected some other way.
    """
    if sub.empty:
        return 0, 0.0
    groups = sub.groupby([sub["date"].dt.normalize(), "amount"])["description"].agg(
        rows='size', descriptions='nunique')
    candidates = groups[(groups.rows > 1) & (groups.descriptions > 1)]
    extra = candidates.rows - 1
    return int(extra.sum()), float((extra * candidates.index.get_level_values('amount')).sum())


def transaction_balances(store: Store, tx: pd.DataFrame | None = None) -> pd.DataFrame:
    """Reconstructed cash-account balance immediately before/after every row.

    Compute from the complete, unfiltered *bank* ledger. The entered balance
    (or a newer cash snapshot) is an end-of-day anchor. Rows on the same day
    use import ID order, which is not necessarily the bank's posting order.
    """
    tx = store.transactions() if tx is None else tx
    columns = ["before", "after", "anchor_date", "anchor_balance", 'day_close', 'unordered']
    records = {}
    for account in store.accounts():
        if not account.type.is_cashlike or tx.empty:
            continue
        rows = tx[tx.account_id == account.id].dropna(subset=["date"]).copy()
        if rows.empty:
            continue
        rows["day"] = pd.to_datetime(rows.date).dt.normalize()
        rows = rows.sort_values(["day", "id"], kind="stable")
        anchor_date, anchor_balance = cash_anchor(store, account)
        cumulative = rows.amount.cumsum()
        flow_at_anchor = float(rows.loc[rows.day <= anchor_date, "amount"].sum())
        after = (anchor_balance + cumulative - flow_at_anchor).round(2)
        closing = pd.Series(after.to_numpy(),index=rows.index).groupby(rows.day).last()
        counts = rows.day.value_counts()
        for row, value in zip(rows.itertuples(index=False), after):
            records[int(row.id)] = [round(float(value - row.amount), 2), float(value),
                                    anchor_date.date(), float(anchor_balance), float(closing.loc[row.day]),
                                    bool(counts.loc[row.day] > 1)]
    result = pd.DataFrame.from_dict(records, orient="index", columns=columns)
    result.index.name = "id"
    return result


@cached_read
def account_balance_history(store: Store, account_ids=None) -> pd.DataFrame:
    """Signed balance per selected account: assets positive, money owed negative.

    Cash uses bank transactions and a dated balance. Investments/loans use
    dated snapshots with the same interpolation and prehistory as the overview.
    """
    accounts = [a for a in store.accounts() if account_ids is None or a.id in account_ids]
    tx = store.transactions()
    ids = {a.id for a in accounts}
    tx = tx[tx.account_id.isin(ids)]
    today = pd.Timestamp(date.today())
    starts = [pd.Timestamp(a.balance_date) for a in accounts]
    if not tx.empty:
        starts.append(tx.date.min().normalize())
    for account in accounts:
        snapshots = store.snapshots(account.id)
        if not snapshots.empty:
            starts.append(snapshots.date.min().normalize())
    start = min([today, *starts]) if starts else today - pd.Timedelta(days=90)
    index = pd.date_range(start, today, freq="D")
    balances = {}
    for account in accounts:
        if account.type.is_cashlike:
            line = cash_history(tx, account, index, anchor=cash_anchor(store, account))
        else:
            line = snapshot_history(store, account, index)
            if account.type == AccountType.LOAN:
                line = -line.abs()
        balances[account.id] = line
    return pd.DataFrame(balances, index=index)


@cached_read
def current_accounts(store: Store) -> list[Account]:
    """Accounts valued through today using the same history as the charts.

    These are calculation inputs, not new bank observations. Keep the stored
    dated anchors intact; later transactions and snapshots determine the
    current value without moving those anchors or counting flows twice.
    """
    balances = account_balance_history(store)
    result = []
    for account in store.accounts():
        balance = float(balances[account.id].iloc[-1])
        if account.type == AccountType.LOAN:
            balance = abs(balance)
        result.append(replace(account, balance=balance, balance_date=date.today()))
    return result


@cached_read
def networth_history(store: Store, account_ids=None) -> pd.DataFrame:
    """Daily frame with columns cash, investments, debt, networth over the
    span of the transaction ledger (falling back to the last 90 days)."""
    tx = store.transactions()
    accounts = [a for a in store.accounts() if account_ids is None or a.id in account_ids]
    if account_ids is not None:
        tx = tx[tx.account_id.isin({a.id for a in accounts})]
    today = pd.Timestamp(date.today())
    if tx.empty:
        start = today - pd.Timedelta(days=90)
    else:
        start = tx["date"].min().normalize()
    if account_ids is not None:
        for account in accounts:
            start = min(start, pd.Timestamp(account.balance_date))
            snapshots = store.snapshots(account.id)
            if not snapshots.empty:
                start = min(start, snapshots.date.min().normalize())
    index = pd.date_range(min(start, today), today, freq="D")
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
    """Personal income/spending, excluding transfers and reimbursable business."""
    return personal_transactions(tx)


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


@cached_read
def headline_stats(store: Store) -> dict:
    """Numbers for the dashboard stat cards."""
    accounts = current_accounts(store)
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
                           max_weeks: int | None = 78,
                           start_date: date | None = None,
                           end_date: date | None = None) -> tuple[pd.DataFrame, pd.Series]:
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
    bounded = start_date is not None or end_date is not None
    if bounded:
        first = max(df["date"].min().date(), start_date or df["date"].min().date())
        last = min(df["date"].max().date(), end_date or df["date"].max().date())
        if first > last:
            return pd.DataFrame(), pd.Series(dtype=float)
        # Include quiet weeks, but never a partial week spanning the cut-off.
        weeks = [w for w in pd.period_range(first, last, freq="W-SUN")
                 if w.start_time.date() >= first and w.end_time.date() <= last]
        df = df[(df["date"] >= pd.Timestamp(first)) &
                (df["date"] <= pd.Timestamp(last))]
    df["norm_key"] = [
        f"{normalize_desc(d)}|{'+' if a > 0 else '-'}"
        for d, a in zip(df["description"], df["amount"])
    ]
    df = df[~df["norm_key"].isin(exclude_keys)]
    if df.empty and not bounded:
        return pd.DataFrame(), pd.Series(dtype=float)
    df["week"] = df["date"].dt.to_period("W-SUN")
    if not bounded:
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
    if max_weeks is not None and len(W) > max_weeks:
        W = W.tail(max_weeks)
        inc = inc.tail(max_weeks)
    return W, inc
