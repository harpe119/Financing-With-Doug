"""Generate realistic synthetic bank exports (18 months, seeded).

Two deliberately different CSV dialects exercise the import mapper:
  checking.csv — Chase-style: Posting Date, Description, Amount, Type, Balance
  savings.csv  — dual-column: Date, Details, Money Out, Money In

A meta.json sidecar records the true ending balances so the demo loader can
seed accounts that are exactly consistent with the ledger.
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from finfore.util import add_months, clamp_day

RNG = np.random.default_rng(42)

CHECKING_START = 6300.00
SAVINGS_START = 2800.00

GROCERS = ["KROGER #482", "TRADER JOE'S #77", "ALDI 5501", "COSTCO WHSE #114"]
DINING = ["CHIPOTLE 1123", "STARBUCKS #2214", "THAI ORCHID RESTAURANT",
          "FIVE GUYS VA-081", "SUSHI DEN", "DOORDASH*BURGERWORKS",
          "LOCAL BREWERY TAPROOM", "PANERA BREAD #4402"]
FUEL = ["SHELL OIL 5744", "EXXONMOBIL 9921", "WAWA 8113"]
SHOPPING = ["AMAZON.COM*8H2K1", "TARGET 00021", "HOME DEPOT #4412",
            "MICRO CENTER #101"]


def month_iter(start: date, n_months: int):
    d = date(start.year, start.month, 1)
    for k in range(n_months):
        yield add_months(d, k)


def generate(out_dir: str | Path, end: date | None = None, months: int = 18) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    end = end or date.today()
    start = add_months(date(end.year, end.month, 1), -(months - 1))

    checking: list[tuple[date, str, float]] = []
    savings: list[tuple[date, str, float]] = []

    def add(rows, d, desc, amt):
        if start <= d <= end:
            rows.append((d, desc, round(float(amt), 2)))

    # biweekly payroll anchored on a Friday
    pay = start - timedelta(days=(start.weekday() - 4) % 7 + 14)
    while pay <= end:
        if pay >= start:
            add(checking, pay, "ACME AEROSPACE PAYROLL DIR DEP", 2650.00)
        pay += timedelta(days=14)

    for m0 in month_iter(start, months + 1):
        y, m = m0.year, m0.month
        add(checking, clamp_day(y, m, 1), "OAKWOOD PROPERTIES RENT PAYMENT", -1795.00)
        add(checking, clamp_day(y, m, 12), "DOMINION ELECTRIC UTILITY",
            -(105 + RNG.normal(0, 24)))
        add(checking, clamp_day(y, m, 5), "XFINITY INTERNET 8842", -79.99)
        add(checking, clamp_day(y, m, 20), "T-MOBILE WIRELESS PMT", -65.00)
        add(checking, clamp_day(y, m, 15), "CAPITAL AUTO FINANCE PMT", -412.33)
        add(checking, clamp_day(y, m, 8), "GEICO INSURANCE AUTOPAY", -138.40)
        add(checking, clamp_day(y, m, 22), "NETFLIX.COM", -15.49)
        add(checking, clamp_day(y, m, 3), "SPOTIFY USA", -11.99)
        add(checking, clamp_day(y, m, 1), "IRONWORKS GYM MEMBERSHIP", -45.00)
        # internal transfers: savings sweep (paired) + brokerage sweep (one leg)
        add(checking, clamp_day(y, m, 2), "ONLINE TRANSFER TO SAVINGS X8821", -500.00)
        add(savings, clamp_day(y, m, 2), "ONLINE TRANSFER FROM CHECKING", 500.00)
        add(checking, clamp_day(y, m, 6), "ONLINE TRANSFER TO BROKERAGE X4471", -400.00)
        add(savings, clamp_day(y, m, 27), "INTEREST PAYMENT",
            33 + 0.45 * (m0 - start).days / 30.44 + RNG.normal(0, 1.5))

    # variable habits (daily hazard rates ~ events per 30 days)
    d = start
    while d <= end:
        if RNG.random() < 7.5 / 30:
            add(checking, d, RNG.choice(GROCERS), -abs(RNG.normal(80, 36)) - 14)
        if RNG.random() < 14.0 / 30:
            add(checking, d, RNG.choice(DINING), -abs(RNG.normal(24, 14)) - 6)
        if RNG.random() < 4.2 / 30:
            add(checking, d, RNG.choice(FUEL), -abs(RNG.normal(46, 9)) - 8)
        if RNG.random() < 4.6 / 30:
            add(checking, d, RNG.choice(SHOPPING), -abs(RNG.normal(58, 48)) - 10)
        if RNG.random() < 1.0 / 30:
            add(checking, d, "CVS PHARMACY #0193", -abs(RNG.normal(28, 15)) - 5)
        if RNG.random() < 0.9 / 30:
            add(checking, d, "DELTA AIR 0062340112", -abs(RNG.normal(260, 95)) - 60)
        d += timedelta(days=1)

    checking.sort(key=lambda r: r[0])
    savings.sort(key=lambda r: r[0])

    # Chase-style with a running Balance column (mapper must ignore it)
    bal = CHECKING_START
    with open(out / "checking.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Posting Date", "Description", "Amount", "Type", "Balance"])
        for d, desc, amt in checking:
            bal += amt
            w.writerow([d.strftime("%m/%d/%Y"), desc, f"{amt:.2f}",
                        "CREDIT" if amt > 0 else "DEBIT", f"{bal:.2f}"])
    checking_end = round(bal, 2)

    sbal = SAVINGS_START
    with open(out / "savings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Details", "Money Out", "Money In"])
        for d, desc, amt in savings:
            sbal += amt
            w.writerow([
                d.strftime("%Y-%m-%d"), desc,
                f"{-amt:.2f}" if amt < 0 else "",
                f"{amt:.2f}" if amt > 0 else "",
            ])
    savings_end = round(sbal, 2)

    (out / "meta.json").write_text(json.dumps({
        "end_date": end.isoformat(),
        "checking_end": checking_end,
        "savings_end": savings_end,
    }, indent=2))
    print(f"wrote {len(checking)} checking rows (end {checking_end}), "
          f"{len(savings)} savings rows (end {savings_end}) -> {out}")


if __name__ == "__main__":
    import sys
    generate(sys.argv[1] if len(sys.argv) > 1 else "sample_data")
