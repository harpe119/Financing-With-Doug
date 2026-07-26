"""Demo datasets — five financial situations, each a complete worked example.

The original demo imports the two bundled CSVs, which is the only path that
exercises the import mapper end to end, so it stays. The rest are generated in
code: they need to describe *situations* (a deficit, a healthy surplus, a
turnaround, a collapse) rather than dialects, and parameterising a generator is
both smaller in the repo and easier to keep honest than four more static CSV
pairs that would drift out of date the moment the taxonomy changed.

Each profile ends by backfilling checkpoints from its own ledger, so the
Progress page has something to show the moment a demo is loaded rather than
after two months of waiting. Those checkpoints are built from data that existed
at each date — see ``progress.capture_as_of`` — because a demo that flattered
the model would misrepresent the one page whose job is to catch it out.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .models import Account, AccountType, Goal
from .pipeline import import_csv, refresh_derived
from .store import Store
from .util import add_months, clamp_day, resource_path

SAMPLE_DIR = resource_path("sample_data")


@dataclass(frozen=True)
class DemoProfile:
    key: str
    name: str
    blurb: str
    detail: str


PROFILES: list[DemoProfile] = [
    DemoProfile(
        "balanced", "Balanced — good mix of fun and saving",
        "Two CSV dialects, a car loan, a brokerage, three goals.",
        "An ordinary situation: a modest surplus most months, a car loan "
        "part-paid, and savings that grow slowly. Imported from the bundled "
        "CSV files, so it also demonstrates the import mapper."),
    DemoProfile(
        "struggling", "Struggling — spending past the edge",
        "Deficit every month, credit-card balance climbing, no buffer.",
        "Income does not cover spending, so the card carries the difference "
        "and grows. Useful for seeing what the habits score, the shortfall "
        "risk and the goal ladder say when the news is bad."),
    DemoProfile(
        "thriving", "Thriving — well ahead",
        "Large surplus, investments compounding, debts nearly gone.",
        "A high savings rate with modest wants. Shows the forecast fan when "
        "the trajectory is comfortable, and what an A grade looks like."),
    DemoProfile(
        "turnaround", "Turnaround — habits changed five months ago",
        "Was overspending; cut back sharply. Built for the Progress page.",
        "Discretionary spending drops by roughly half part-way through the "
        "history. The Progress page should detect that change as "
        "statistically significant, and the older projections should show "
        "cash running above what they predicted."),
    DemoProfile(
        "spiralling", "Spiralling — the wheels are coming off",
        "An F grade, three high-interest debts, and cash going negative fast.",
        "Spending runs at roughly 1.6× income, the savings account is gone and "
        "the shortfall is landing on a card at 27% APR. Every warning the app "
        "has is firing at once: an F on the habits score, near-certain "
        "shortfall on the forecast, a runway measured in days, and a goal "
        "ladder that cannot reach its targets this century. This is what the "
        "tool looks like when the news is as bad as it gets."),
]

PROFILE_KEYS = [p.key for p in PROFILES]
DEFAULT_PROFILE = "balanced"


def profile(key: str) -> DemoProfile:
    return next((p for p in PROFILES if p.key == key), PROFILES[0])


# ---------------------------------------------------------------------------
# Ledger generation
# ---------------------------------------------------------------------------
_GROCERS = ["KROGER #482", "TRADER JOE'S #77", "ALDI 5501", "SAFEWAY 1180"]
_DINING = ["CHIPOTLE 1123", "STARBUCKS #2214", "THAI ORCHID", "FIVE GUYS",
           "DOORDASH*BURGERWORKS", "PANERA BREAD #4402", "TACO BELL 3391"]
_SHOPPING = ["AMAZON.COM*8H2K1", "TARGET 00021", "BEST BUY 118", "REI 0042"]
_FUEL = ["SHELL OIL 5744", "EXXONMOBIL 9921", "WAWA 8113"]


def _rows_for(months: int, end: date, *, income: float, rent: float,
              groceries: float, dining: float, shopping: float,
              dining_after: float | None = None,
              shopping_after: float | None = None,
              change_months_ago: int = 0, seed: int = 7,
              account_id: int = 1,
              debt_payments: list[tuple[int, str, float]] | None = None
              ) -> pd.DataFrame:
    """A synthetic ledger. ``*_after`` take effect from the change date on.

    ``debt_payments`` are (day-of-month, description, amount) outflows written
    into the ledger rather than left implicit on the account. Real statements
    show them, the recurrence detector then finds them, and ``auto_link_loans``
    ties each to its account by name — so a profile carrying debt behaves the
    way an imported one would instead of quietly omitting the largest fixed
    cost in the picture.
    """
    rng = np.random.default_rng(seed)
    start = add_months(date(end.year, end.month, 1), -(months - 1))
    change = add_months(date(end.year, end.month, 1), -change_months_ago) \
        if change_months_ago else None
    rows: list[dict] = []
    h = 0

    def add(d: date, desc: str, amt: float, cat: str | None = None) -> None:
        nonlocal h
        if not (start <= d <= end) or abs(amt) < 0.01:
            return
        h += 1
        rows.append({"account_id": account_id, "date": pd.Timestamp(d),
                     "description": desc, "amount": round(float(amt), 2),
                     "import_hash": f"demo-{seed}-{h}"})

    # Biweekly payroll anchored to a Friday. `income` is the monthly figure, so
    # a cheque is a twenty-sixth of the year rather than half a month — which
    # is what makes two months a year land three cheques, exactly as real
    # fortnightly pay does.
    cheque = income * 12.0 / 26.0
    pay = start - timedelta(days=(start.weekday() - 4) % 7)
    while pay <= end:
        add(pay, "ACME AEROSPACE PAYROLL DIR DEP", cheque)
        pay += timedelta(days=14)

    for k in range(months + 1):
        m0 = add_months(start, k)
        y, m = m0.year, m0.month
        add(clamp_day(y, m, 1), "OAKWOOD PROPERTIES RENT PAYMENT", -rent)
        add(clamp_day(y, m, 12), "DOMINION ELECTRIC UTILITY",
            -(95 + rng.normal(0, 18)))
        add(clamp_day(y, m, 5), "XFINITY INTERNET 8842", -79.99)
        add(clamp_day(y, m, 20), "T-MOBILE WIRELESS PMT", -65.00)
        add(clamp_day(y, m, 8), "GEICO INSURANCE AUTOPAY", -138.40)
        add(clamp_day(y, m, 22), "NETFLIX.COM", -15.49)
        add(clamp_day(y, m, 24), "SPOTIFY USA", -11.99)
        add(clamp_day(y, m, 3), "PLANET FITNESS MEMBER", -24.99)
        for pay_day, desc, amt in (debt_payments or []):
            add(clamp_day(y, m, pay_day), desc, -abs(amt))

        d0 = date(y, m, 1)
        after = change is not None and d0 >= change
        dine_rate = (dining_after if after and dining_after is not None
                     else dining)
        shop_rate = (shopping_after if after and shopping_after is not None
                     else shopping)

        for w in range(4):
            base = clamp_day(y, m, 3 + w * 7)
            add(base, str(rng.choice(_GROCERS)),
                -abs(rng.normal(groceries / 4, groceries / 16)))
            add(base + timedelta(days=1), str(rng.choice(_FUEL)),
                -abs(rng.normal(52, 12)))
            for _ in range(3):
                add(base + timedelta(days=int(rng.integers(0, 6))),
                    str(rng.choice(_DINING)),
                    -abs(rng.normal(dine_rate / 12, dine_rate / 40)))
            if rng.random() < 0.55:
                add(base + timedelta(days=int(rng.integers(0, 6))),
                    str(rng.choice(_SHOPPING)),
                    -abs(rng.normal(shop_rate / 2.5, shop_rate / 8)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
def _load_balanced(store: Store) -> dict:
    today = date.today()
    meta = {}
    meta_path = SAMPLE_DIR / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    checking = Account("Everyday Checking", AccountType.CHECKING,
                       balance=meta.get("checking_end", 4850.00),
                       balance_date=today)
    savings = Account("High-Yield Savings", AccountType.SAVINGS,
                      balance=meta.get("savings_end", 12400.00),
                      balance_date=today)
    loan = Account("Capital Auto Loan", AccountType.LOAN, balance=14250.00,
                   balance_date=today, apr=0.064, monthly_payment=412.33,
                   payment_day=15)
    broker = Account("Brokerage", AccountType.INVESTMENT, balance=18300.00,
                     balance_date=today, annual_return=0.07, volatility=0.15,
                     monthly_contribution=400.0)
    for a in (checking, savings, loan, broker):
        store.add_account(a)
    for k, (lb, bb) in enumerate([(17650, 13900), (16820, 14800), (15980, 15600),
                                  (15120, 16900), (14690, 17550)]):
        d = today - timedelta(days=(5 - k) * 90)
        store.add_snapshot(loan.id, d, lb)
        store.add_snapshot(broker.id, d, bb)

    added_c, _ = import_csv(store, str(SAMPLE_DIR / "checking.csv"), checking.id)
    added_s, _ = import_csv(store, str(SAMPLE_DIR / "savings.csv"), savings.id)
    n_rec = refresh_derived(store, checkpoint=False)
    store.add_goal(Goal("Emergency fund", 25000, "cash"))
    store.add_goal(Goal("House down payment", 60000, "cash"))
    store.add_goal(Goal("Net worth: $100k", 100000, "networth"))
    return {"transactions": added_c + added_s, "recurring": n_rec}


def _load_generated(store: Store, *, months: int, income: float, rent: float,
                    groceries: float, dining: float, shopping: float,
                    checking: float, savings: float, seed: int,
                    card: float = 0.0, loan_bal: float = 0.0,
                    invest: float = 0.0, contribution: float = 0.0,
                    goals: list[tuple[str, float, str]] | None = None,
                    dining_after: float | None = None,
                    shopping_after: float | None = None,
                    change_months_ago: int = 0,
                    card_apr: float = 0.2299,
                    extra_loans: list[dict] | None = None,
                    debt_payments: list[tuple[int, str, float]] | None = None
                    ) -> dict:
    today = date.today()
    chk = Account("Everyday Checking", AccountType.CHECKING, balance=checking,
                  balance_date=today)
    store.add_account(chk)
    accounts = [chk]
    if savings:
        sv = Account("Savings", AccountType.SAVINGS, balance=savings,
                     balance_date=today)
        store.add_account(sv)
        accounts.append(sv)
    if card:
        cc = Account("Visa Rewards Card", AccountType.CREDIT, balance=-abs(card),
                     balance_date=today, apr=card_apr)
        store.add_account(cc)
    if loan_bal:
        ln = Account("Auto Loan", AccountType.LOAN, balance=loan_bal,
                     balance_date=today, apr=0.0649, monthly_payment=395.0,
                     payment_day=15)
        store.add_account(ln)
        for k in range(5):
            d = today - timedelta(days=(5 - k) * 90)
            store.add_snapshot(ln.id, d, loan_bal + (5 - k) * 1150)
    for spec in (extra_loans or []):
        ln = Account(spec["name"], AccountType.LOAN, balance=spec["balance"],
                     balance_date=today, apr=spec["apr"],
                     monthly_payment=spec["payment"],
                     payment_day=spec.get("day", 15))
        store.add_account(ln)
        # Snapshots that climb as you go back: a balance that has barely moved
        # despite months of payments is what a high-APR debt actually looks
        # like, and the history line should show it rather than a clean slope.
        for k in range(5):
            d = today - timedelta(days=(5 - k) * 90)
            store.add_snapshot(ln.id, d,
                               spec["balance"] + (5 - k) * spec.get("drift", 400))
    if invest:
        br = Account("Brokerage", AccountType.INVESTMENT, balance=invest,
                     balance_date=today, annual_return=0.07, volatility=0.15,
                     monthly_contribution=contribution)
        store.add_account(br)
        for k in range(5):
            d = today - timedelta(days=(5 - k) * 90)
            store.add_snapshot(br.id, d, max(0.0, invest - (5 - k) * 1900))

    # Anything an account commits to every month has to appear in the ledger,
    # or the demo contradicts itself: the forecast charges a contribution the
    # reconstructed history never shows leaving, and the backtest then scores
    # the model against a cash curve that never paid it. That is what made the
    # thriving profile backtest at 1% coverage — a data fault, read as the
    # model being hopeless.
    fixed = list(debt_payments or [])
    named = {d.upper() for _, d, _ in fixed}
    if loan_bal and not any("AUTO" in n for n in named):
        fixed.append((15, "AUTO LOAN PMT", 395.0))
    for spec in (extra_loans or []):
        tag = spec["name"].upper().split()[0]
        if not any(tag in n for n in named):
            fixed.append((spec.get("day", 15),
                          f"{spec['name'].upper()} PMT", spec["payment"]))
    if invest and contribution:
        # "TRANSFER" so the keyword rules file it as internal movement rather
        # than spending — money moved into a brokerage is saved, not spent.
        fixed.append((2, "VANGUARD BROKERAGE TRANSFER", contribution))

    rows = _rows_for(months, today, income=income, rent=rent,
                     groceries=groceries, dining=dining, shopping=shopping,
                     dining_after=dining_after, shopping_after=shopping_after,
                     change_months_ago=change_months_ago, seed=seed,
                     account_id=chk.id, debt_payments=fixed)
    from .learning import apply_chain, full_chain
    rows = apply_chain(rows, full_chain(store))
    added, _ = store.add_transactions(rows)
    n_rec = refresh_derived(store, checkpoint=False)
    for name, amount, kind in (goals or []):
        store.add_goal(Goal(name, amount, kind))
    return {"transactions": added, "recurring": n_rec}


# The habits score separates *how* you spend from *how much you have*, so a
# profile meant to look bad has to be bad on both axes. A low earner with
# modest wants grades a deliberate B — "squeezed, not careless" — which is the
# score working correctly and the wrong story for this demo. Struggling
# therefore runs a deficit *and* a wants-to-needs ratio above the 50/30/20
# ceiling of 0.60, which is the quadrant the score reserves for an F.
_GENERATED = {
    "struggling": dict(
        months=20, income=3400.0, rent=1250.0, groceries=450.0, dining=900.0,
        shopping=750.0, checking=310.0, savings=180.0, card=6800.0,
        loan_bal=11200.0, invest=0.0, contribution=0.0, seed=21,
        goals=[("Emergency fund", 9000, "cash"),
               ("Clear the card", 6800, "cash")]),
    "thriving": dict(
        months=20, income=7800.0, rent=1900.0, groceries=620.0, dining=310.0,
        shopping=260.0, checking=9400.0, savings=41000.0, card=0.0,
        loan_bal=3100.0, invest=86000.0, contribution=1400.0, seed=33,
        goals=[("Emergency fund", 24000, "cash"),
               ("House down payment", 90000, "cash"),
               ("Net worth: $250k", 250000, "networth")]),
    # Starts in the same F quadrant as `struggling` and climbs out of it, so
    # the Progress page has a real arc to show rather than good-to-slightly-
    # better. The change is five months back: far enough that two backfilled
    # checkpoints predate it, recent enough that the recency-weighted score has
    # not fully forgotten the old habits.
    "turnaround": dict(
        months=20, income=4200.0, rent=1550.0, groceries=580.0, dining=950.0,
        shopping=800.0, dining_after=320.0, shopping_after=220.0,
        change_months_ago=5, checking=3900.0, savings=6100.0, card=1450.0,
        loan_bal=8600.0, invest=7200.0, contribution=250.0, seed=44,
        goals=[("Emergency fund", 14000, "cash"),
               ("Clear the card", 1450, "cash"),
               ("Net worth: $50k", 50000, "networth")]),
    # The worst case the score can express. An F needs *both* sub-scores on the
    # floor — wants above needs (FESR near 1.0) and a savings rate at or past
    # −50% — because the interaction penalty is what drags the total under 1.2,
    # and it only bites when both are bad. The debts and the deficit are sized
    # to agree with each other: roughly −$2,000/month over the 20-month ledger
    # is about the $37k carried across the three accounts, so the story the
    # numbers tell is internally consistent rather than merely alarming.
    "spiralling": dict(
        months=20, income=3400.0, rent=1250.0, groceries=400.0, dining=1800.0,
        shopping=1350.0, checking=120.0, savings=0.0, card=14800.0,
        card_apr=0.2699, loan_bal=0.0, invest=0.0, contribution=0.0, seed=57,
        extra_loans=[
            dict(name="Sunrise Auto Finance", balance=12400.0, apr=0.159,
                 payment=340.0, day=15, drift=520),
            dict(name="Lakeside Personal Loan", balance=9200.0, apr=0.2899,
                 payment=265.0, day=5, drift=300),
        ],
        debt_payments=[(15, "SUNRISE AUTO FINANCE PMT", 340.0),
                       (5, "LAKESIDE PERSONAL LOAN PMT", 265.0),
                       (25, "VISA REWARDS CARD MIN PMT", 210.0)],
        # All three are out of reach at this burn rate, which is the point: the
        # ladder should say so plainly rather than quote a date. A goal of $0
        # net worth would have been the obvious "get back to level" target, but
        # a zero-amount goal reads as already met, so the starter buffer stands
        # in as the nearest rung — and even that is unreachable here.
        goals=[("$1,000 starter buffer", 1000, "cash"),
               ("Emergency fund", 8400, "cash"),
               ("Clear the credit card", 14800, "cash")]),
}


def load_demo(store: Store, key: str = DEFAULT_PROFILE,
              checkpoints: int = 4) -> dict:
    """Populate a store with one of the demo profiles.

    ``checkpoints`` backfills that many historical checkpoints so the Progress
    page is immediately useful. Each is built only from data that existed at
    its own date.
    """
    key = key if key in PROFILE_KEYS else DEFAULT_PROFILE
    if key == "balanced":
        info = _load_balanced(store)
    else:
        info = _load_generated(store, **_GENERATED[key])
    info["profile"] = key

    made = 0
    if checkpoints:
        try:
            from .progress import backfill, capture
            made = backfill(store, n=checkpoints, spacing_days=55,
                            n_paths=160, label="history")
            capture(store, label="today", n_paths=160)
            made += 1
        except Exception:
            made = 0
    info["checkpoints"] = made
    return info
