"""Shared helpers: money formatting, date arithmetic."""
from __future__ import annotations

import calendar
import sys
from datetime import date, timedelta
from pathlib import Path


def fmt_money(x: float, signed: bool = False) -> str:
    """Format a float as $1,234 (no cents above $100, cents below)."""
    if x is None:
        return "—"
    sign = "-" if x < 0 else ("+" if signed and x > 0 else "")
    ax = abs(x)
    if ax >= 100:
        return f"{sign}${ax:,.0f}"
    return f"{sign}${ax:,.2f}"


def fmt_rate(rate: float, decimals: int = 4) -> str:
    """Format a stored annual rate (0.0649) as a percentage (6.49%).

    Trailing zeros are trimmed, so a rate entered to four places shows all four
    and a round one still reads as ``7%`` rather than ``7.0000%``. Displaying
    what the user typed is the point: rounding 6.4900 to 6.49 on the way back
    out makes an accurate entry look like it was thrown away.
    """
    if rate is None:
        return "—"
    s = f"{rate * 100:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{s or '0'}%"


def resource_path(*parts: str) -> Path:
    """Locate a bundled data file, in development or inside a frozen build.

    PyInstaller unpacks bundled data to a temporary directory and points
    ``sys._MEIPASS`` at it; ``__file__`` then refers to a location that has no
    data files beside it. Resolving through here means the same call works from
    a source checkout and from a packaged executable, which is the single most
    common thing to get wrong when freezing a Qt app.
    """
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root.joinpath(*parts)


def fmt_date(value) -> str:
    """Format a ledger date defensively.

    A stored date that will not parse comes back as NaT, and NaT.strftime()
    raises. In the UI that means raising inside a Qt slot, which PyQt turns
    into a hard abort of the whole application — so an unreadable date degrades
    to a placeholder instead of taking the page down.
    """
    import pandas as pd
    if value is None or pd.isna(value):
        return "—"
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "—"


def add_months(d: date, months: int) -> date:
    """Add calendar months to a date, clamping the day to month length."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def clamp_day(year: int, month: int, day: int) -> date:
    """Return a date in (year, month) with the day clamped to month length."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def daterange_days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def month_firsts_between(start: date, end: date) -> list[date]:
    """All first-of-month dates in (start, end]."""
    out = []
    d = date(start.year, start.month, 1)
    if d <= start:
        d = add_months(d, 1)
    while d <= end:
        out.append(d)
        d = add_months(d, 1)
    return out
