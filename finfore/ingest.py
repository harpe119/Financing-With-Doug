"""CSV ingestion.

Bank exports disagree on everything: delimiter, date format, single signed
amount vs. separate debit/credit columns, and sign convention. This module
sniffs what it can, lets the UI override the rest, and normalizes to a
canonical frame: [date (datetime64), description (str), amount (float, signed
inflow-positive)].

Deduplication: a stable hash of (account, date, description, amount,
occurrence-index-within-day) so re-importing an overlapping export is safe,
while two genuinely identical purchases on the same day are both kept.
"""
from __future__ import annotations

import csv
import hashlib
import math
import re
import warnings
from dataclasses import dataclass, field
from datetime import date as _date

import pandas as pd

SIGN_MODES = {
    "standard": "Signed amount (negative = spending)",
    "flip": "Signed amount (positive = spending) — flip",
    "debit_credit": "Separate debit / credit columns",
    "indicator": "Unsigned amount + credit/debit indicator column",
}

# Tokens accepted in an indicator column (normalized: stripped, lower-cased).
DEBIT_TOKENS = frozenset({"debit", "dr", "d", "dbit", "debit memo",
                          "withdrawal", "out", "-"})
CREDIT_TOKENS = frozenset({"credit", "cr", "c", "crdt", "credit memo",
                           "deposit", "in", "+"})

_POSTED_HDRS = ("posting date", "posted date", "post date", "date posted", "booking date")
_TRANSACTED_HDRS = ("transaction date", "trans date", "purchase date")
_DATE_HDRS = (*_POSTED_HDRS, "date", "value date", *_TRANSACTED_HDRS)
_REFERENCE_HDRS = ("transaction id", "transaction reference", "reference", "fitid")
_STATUS_HDRS = ("posting status", "transaction status", "status")
_DESC_HDRS = ("description", "payee", "merchant", "name", "details", "memo",
              "transaction description", "narrative")
_AMT_HDRS = ("amount", "transaction amount", "amount (usd)", "amt", "value")
_DEBIT_HDRS = ("debit", "withdrawal", "withdrawals", "money out", "amount debit",
               "outflow", "paid out")
_CREDIT_HDRS = ("credit", "deposit", "deposits", "money in", "amount credit",
                "inflow", "paid in")
_INDICATOR_HDRS = ("credit debit indicator", "debit credit indicator",
                   "credit debit", "credit/debit", "debit/credit", "cr/dr", "dr/cr",
                   "transaction indicator", "indicator")


@dataclass
class ImportConfig:
    path: str
    account_id: int
    delimiter: str = ","          # actual character
    dayfirst: bool = False
    sign_mode: str = "standard"
    col_date: str = ""
    col_desc: str = ""
    col_amount: str = ""
    col_debit: str = ""
    col_credit: str = ""
    col_indicator: str = ""
    extra: dict = field(default_factory=dict)
    col_transaction_date: str = ""
    col_reference: str = ""
    col_status: str = ""


def sniff_delimiter(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            sample = f.read(4096)
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:
        return ","


def read_raw(path: str, delimiter: str, nrows: int | None = None) -> pd.DataFrame:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', pd.errors.ParserWarning)
            return pd.read_csv(
                path, sep=delimiter, dtype=str, nrows=nrows, index_col=False,
                encoding="utf-8-sig", engine="python", keep_default_na=False,
                skip_blank_lines=True, on_bad_lines="error",
            )
    except (pd.errors.ParserError, pd.errors.ParserWarning) as exc:
        raise ValueError(f'The CSV has a malformed row. Export it again or correct the row before importing. {exc}') from exc


def _match(columns: list[str], candidates: tuple[str, ...]) -> str:
    low = {c.strip().lower(): c for c in columns}
    for cand in candidates:               # exact first
        if cand in low:
            return low[cand]
    for cand in candidates:               # then substring
        for lc, orig in low.items():
            if cand in lc:
                return orig
    return ""


def _sniff_indicator(columns: list[str],
                     sample: pd.DataFrame | None,
                     taken: set[str]) -> str:
    """Find a sign-indicator column by header name, else by inspecting values
    (a column whose distinct entries are all credit/debit-style tokens)."""
    by_name = _match(columns, _INDICATOR_HDRS)
    if by_name:
        return by_name
    if sample is None or sample.empty:
        return ""
    vocab = DEBIT_TOKENS | CREDIT_TOKENS
    for c in columns:
        if c in taken:
            continue
        vals = sample[c].astype(str).str.strip().str.lower()
        uniq = set(vals[vals != ""].unique())
        if 1 <= len(uniq) <= 3 and uniq <= vocab:
            return c
    return ""


def guess_mapping(columns: list[str],
                  sample: pd.DataFrame | None = None) -> dict[str, str]:
    g = {
        "date": _match(columns, _DATE_HDRS),
        "desc": _match(columns, _DESC_HDRS),
        "amount": _match(columns, _AMT_HDRS),
        "transaction_date": _match(columns, _TRANSACTED_HDRS),
        "posting_date": _match(columns, _POSTED_HDRS),
        "reference": _match(columns, _REFERENCE_HDRS),
        "status": _match(columns, _STATUS_HDRS),
    }
    g["indicator"] = _sniff_indicator(columns, sample,
                                      {g["date"], g["desc"], g["amount"]})
    # An indicator column must not be mistaken for a debit/credit amount
    # column ("Credit Debit Indicator" substring-matches both).
    rest = [c for c in columns if c != g["indicator"]]
    g["debit"] = _match(rest, _DEBIT_HDRS)
    g["credit"] = _match(rest, _CREDIT_HDRS)
    # 'balance' must never be mistaken for the amount, nor may a date column
    # ("Value Date" substring-matches "value")
    if g["amount"] and ("balance" in g["amount"].lower() or "date" in g["amount"].lower()):
        g["amount"] = ""
    return g


_num_re = re.compile(r"[^0-9.,]")
_lead_re = re.compile(r"^[^\d(+\-.,]+")       # currency code or symbol before a sign
_trail_re = re.compile(r"[^\d)\-.,]+$")
_numeric_date_re = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}\b")


def parse_dates(values: pd.Series, dayfirst: bool) -> pd.Series:
    """Parse a date column, refusing one read in the wrong day/month order.

    Mixed formats are allowed, but ``format="mixed"`` parses each cell alone,
    so 13/01 became 13 January while 05/02 stayed 2 May: the order was
    silently swapped row by row instead of reported.
    """
    parts = values.astype(str).str.extract(_numeric_date_re).dropna().astype(int)
    if len(parts):
        if not dayfirst and (parts[0] > 12).any():
            raise ValueError("Some dates only make sense day first (for example 31/12). "
                             "Select Day-first dates and try again.")
        if dayfirst and (parts[1] > 12).any():
            raise ValueError("Some dates only make sense month first (for example 12/31). "
                             "Clear Day-first dates and try again.")
    return pd.to_datetime(values, errors="coerce", dayfirst=dayfirst, format="mixed")
_NULLS = frozenset({"nan", "none", "null", "n/a", "na", "-", "--", "."})


def parse_amount(s) -> float:
    """Parse a bank-export amount cell into a signed float.

    Handles currency symbols and thousands separators ('$1,234.56' -> 1234.56),
    parenthesized negatives ('(45.00)' -> -45.0), the trailing-minus convention
    ('45.00-' -> -45.0), and comma-decimal locales ('1.234,56' -> 1234.56).
    Anything unparseable becomes nan rather than raising, so one malformed row
    is dropped instead of aborting the whole import.
    """
    if s is None:
        return float("nan")
    t = str(s).strip()
    if t == "" or t.lower() in _NULLS:
        return float("nan")

    # A currency code or symbol around the number must not hide its sign:
    # '$-45.00', '$(45.00)' and 'USD -45.00' are all negative.
    t = _trail_re.sub("", _lead_re.sub("", t)).strip()
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1].strip()
    if t.endswith("-"):                    # trailing-minus convention
        neg, t = True, t[:-1].strip()
    if t.startswith("-"):
        neg, t = True, t[1:].strip()
    elif t.startswith("+"):
        t = t[1:].strip()

    t = _num_re.sub("", t)                 # drop symbols, spaces, letters
    if not t or t in _NULLS:
        return float("nan")

    # Decide which separator is decimal. If both appear, the rightmost one is;
    # if only one appears and it is followed by exactly three digits, it is a
    # thousands separator (bank amounts otherwise carry 1-2 decimal places).
    dot, com = t.rfind("."), t.rfind(",")
    if dot >= 0 and com >= 0:
        dec_sep = "." if dot > com else ","      # rightmost wins
        grp_sep = "," if dec_sep == "." else "."
        t = t.replace(grp_sep, "").replace(dec_sep, ".")
    elif dot >= 0 or com >= 0:
        sep = "." if dot >= 0 else ","
        parts = t.split(sep)
        # Genuine grouping looks like 1|234|567: a 1-3 digit head and every
        # later group exactly 3 digits.
        if len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            t = t.replace(sep, "")         # thousands grouping
        elif len(parts) == 2:
            t = t.replace(sep, ".")        # decimal mark
        else:
            return float("nan")            # malformed, e.g. '12.3.4'

    if t.count(".") > 1 or not any(ch.isdigit() for ch in t):
        return float("nan")
    try:
        v = float(t)
    except ValueError:
        return float("nan")
    return -v if neg else v


def parse_transactions(cfg: ImportConfig) -> pd.DataFrame:
    """Load + normalize a CSV per config. Raises ValueError with a readable
    message on unusable mappings."""
    try:
        raw = read_raw(cfg.path, cfg.delimiter)
    except pd.errors.ParserError as exc:
        raise ValueError(f"The CSV has a malformed row. Export it again or correct the row before importing. {exc}") from exc
    if raw.empty:
        raise ValueError("File contains no data rows.")
    for col, label in ((cfg.col_date, "date"), (cfg.col_desc, "description")):
        if not col or col not in raw.columns:
            raise ValueError(f"Select which column holds the {label}.")

    dates = parse_dates(raw[cfg.col_date], cfg.dayfirst)
    guessed = guess_mapping(list(raw.columns), raw.head(50))
    def text_column(name):
        return raw[name].astype(str).str.strip() if name in raw else pd.Series('', index=raw.index)
    transaction_col = cfg.col_transaction_date or guessed['transaction_date']
    transaction_dates = parse_dates(text_column(transaction_col), cfg.dayfirst)
    status = text_column(cfg.col_status or guessed['status']).str.casefold()
    pending = status.isin({'pending', 'authorized', 'authorised', 'authorization', 'unposted', 'processing'})
    # A blank posting date is unposted, not a reason to silently use purchase
    # date for the bank ledger. Invalid nonblank dates remain validation errors.
    if cfg.col_date == guessed['posting_date']:
        pending |= text_column(cfg.col_date).eq('') & transaction_dates.notna()
    excluded = status.isin({'cancelled', 'canceled', 'void', 'voided', 'declined', 'rejected'})

    if cfg.sign_mode == "debit_credit":
        if not cfg.col_debit and not cfg.col_credit:
            raise ValueError("Select the debit and/or credit columns.")
        for col in (cfg.col_debit, cfg.col_credit):
            if col and col not in raw:
                raise ValueError(f"The saved amount column {col!r} is missing. Choose the columns again.")
        deb = raw[cfg.col_debit].map(parse_amount) if cfg.col_debit in raw.columns else 0.0
        cre = raw[cfg.col_credit].map(parse_amount) if cfg.col_credit in raw.columns else 0.0
        bad_amount = pd.Series(False, index=raw.index)
        for col in (cfg.col_debit, cfg.col_credit):
            if col:
                bad_amount |= text_column(col).ne('') & raw[col].map(parse_amount).isna()
        deb = pd.Series(deb, index=raw.index).fillna(0.0).abs()
        cre = pd.Series(cre, index=raw.index).fillna(0.0).abs()
        amounts = cre - deb
        amounts = amounts.mask(bad_amount)
    elif cfg.sign_mode == "indicator":
        if not cfg.col_amount or cfg.col_amount not in raw.columns:
            raise ValueError("Select which column holds the amount.")
        if not cfg.col_indicator or cfg.col_indicator not in raw.columns:
            raise ValueError("Select the credit/debit indicator column.")
        amounts = raw[cfg.col_amount].map(parse_amount)
        tok = raw[cfg.col_indicator].astype(str).str.strip().str.lower()
        amounts = amounts.where(~tok.isin(DEBIT_TOKENS), -amounts.abs())
        amounts = amounts.where(~tok.isin(CREDIT_TOKENS), amounts.abs())
        # rows with an unrecognized/blank indicator keep their parsed sign
    else:
        if not cfg.col_amount or cfg.col_amount not in raw.columns:
            raise ValueError("Select which column holds the amount.")
        amounts = raw[cfg.col_amount].map(parse_amount)
        if cfg.sign_mode == "flip":
            amounts = -amounts

    invalid = (~pending & ~excluded & dates.isna()) | (~excluded & ~amounts.map(math.isfinite))
    if invalid.any():
        rows = ', '.join(str(int(i) + 2) for i in raw.index[invalid][:12])
        raise ValueError(f"Invalid date or amount on CSV row(s) {rows}. Nothing was imported. "
                         "Correct these rows or check the column mapping and date format.")
    df = pd.DataFrame({
        "date": dates,
        "description": raw[cfg.col_desc].astype(str).str.strip(),
        "amount": amounts,
        "transaction_date": transaction_dates,
        "posting_date": dates if cfg.col_date == guessed['posting_date'] else pd.NaT,
        "source_reference": text_column(cfg.col_reference or guessed['reference']),
        "date_basis": 'posted' if cfg.col_date == guessed['posting_date'] else 'selected',
        "source_row": raw.index + 2,
    })
    n0 = len(df)
    pending_rows = df.loc[pending & ~excluded].copy()
    pending_rows['date'] = pending_rows.transaction_date.fillna(pending_rows.date)
    pending_records = [dict(date=None if pd.isna(r.date) else r.date.date().isoformat(),
                            description=r.description, amount=float(r.amount), reference=r.source_reference)
                       for r in pending_rows.itertuples()]
    zero = int((df.amount.eq(0) & ~pending & ~excluded).sum())
    df = df.loc[~pending & ~excluded & df.amount.ne(0)].copy()
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    if df.empty and not pending_records and not excluded.any() and not zero:
        raise ValueError(
            f"Parsed 0 usable rows out of {n0}. Check the column mapping, "
            "delimiter, and date format (try the day-first option)."
        )
    df["account_id"] = cfg.account_id
    df["import_hash"] = make_hashes(df)
    df.attrs['import_report'] = dict(source_rows=n0, pending=pending_records,
                                    excluded=int(excluded.sum()), zero=zero)
    return df


_BALANCE_HDRS = ("balance", "value", "market value", "account value",
                 "current value", "ending balance", "amount", "total",
                 "principal", "closing balance")


def guess_snapshot_mapping(columns: list[str]) -> dict[str, str]:
    """Best-effort (date, balance) column guess for a balance-history CSV."""
    return {
        "date": _match(columns, _DATE_HDRS),
        "balance": _match(columns, _BALANCE_HDRS),
    }


def parse_snapshots(path: str, col_date: str, col_balance: str,
                    delimiter: str = ",", dayfirst: bool = False
                    ) -> list[tuple[_date, float]]:
    """Parse a balance-history CSV into sorted, de-duplicated (date, balance)
    records. Later rows win on a duplicate date."""
    raw = read_raw(path, delimiter)
    if col_date not in raw.columns or col_balance not in raw.columns:
        raise ValueError("Pick both a date column and a balance column.")
    dates = parse_dates(raw[col_date], dayfirst)
    bals = raw[col_balance].map(parse_amount)
    invalid = dates.isna() | ~bals.map(math.isfinite)
    if invalid.any():
        rows = ', '.join(str(int(i)+2) for i in raw.index[invalid][:12])
        raise ValueError(f'Invalid date or balance on CSV row(s) {rows}. Nothing was imported. Correct the rows or column choices.')
    out: dict[_date, float] = {}
    for d, b in zip(dates, bals):
        if pd.isna(d) or pd.isna(b):
            continue
        out[d.date()] = float(b)
    if not out:
        raise ValueError("No valid date/balance rows were found. Check the "
                         "column choices and date format.")
    return sorted(out.items())


# --- activity CSV → balance history -----------------------------------------
# Lenders and brokerages mostly do not export "what you owed on each date".
# They export what a bank exports: dated activity. A car-loan statement is a
# funding row and a column of payments; a brokerage statement is contributions
# and withdrawals. Those files carry a balance history — it just has to be
# integrated out of the flows rather than read off a column.

def balances_from_activity(df: pd.DataFrame, anchor_date: _date,
                           anchor_balance: float, payments_reduce: bool = True
                           ) -> list[tuple[_date, float]]:
    """Reconstruct dated balances from an activity ledger.

    ``df`` is the canonical frame from :func:`parse_transactions` — inflow
    positive, so a loan payment (a credit to the lender) is positive and the
    original advance is negative. The balance owed therefore moves *against*
    the cumulative flow, which is what ``payments_reduce`` encodes; flip it for
    a file that already signs amounts the way the balance moves.

    The series is pinned to (``anchor_date``, ``anchor_balance``) — the
    account's own current balance — exactly as ``history.cash_history`` pins a
    cash ledger, so the newest point is a figure the user vouched for and the
    older points are walked back from it:

        B(t) = B(t0) ∓ (F(t) − F(t0))

    This is principal-only. Interest that accrues without appearing as a row in
    the file is not reconstructed, so balances far behind the anchor read a
    little high on an interest-bearing loan. It is the shape of the history,
    not a statement of record.
    """
    if df.empty:
        raise ValueError("No usable rows to build a balance history from.")
    daily = df.groupby(df["date"].dt.normalize())["amount"].sum().sort_index()
    F = daily.cumsum()
    a = pd.Timestamp(anchor_date)
    if a < F.index[0]:
        f0 = 0.0
    elif a >= F.index[-1]:
        f0 = float(F.iloc[-1])
    else:
        f0 = float(F.asof(a))
    sign = -1.0 if payments_reduce else 1.0
    series = float(anchor_balance) + sign * (F - f0)
    out = [(pd.Timestamp(d).date(), float(v)) for d, v in series.items()]
    if a.date() not in {d for d, _ in out}:
        out.append((a.date(), float(anchor_balance)))
    return sorted(out)


def make_hashes(df: pd.DataFrame) -> list[str]:
    """Stable per-row hash. The occurrence index within
    (account, date, description, amount) keeps legitimate duplicates distinct
    while making file re-imports idempotent."""
    occ = df.groupby(
        [df["account_id"], pd.to_datetime(df["date"]).dt.normalize(),
         df["description"], df["amount"].round(2)]
    ).cumcount()
    out = []
    for (aid, d, desc, amt), k in zip(
        zip(df["account_id"], df["date"], df["description"], df["amount"].round(2)),
        occ,
    ):
        s = f"{aid}|{pd.Timestamp(d).date().isoformat()}|{desc}|{amt:.2f}|{k}"
        out.append(hashlib.sha1(s.encode()).hexdigest()[:20])
    return out
