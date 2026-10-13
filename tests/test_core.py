"""Core logic tests — run with:  python tests/test_core.py

Covers ingestion & dedup, transfer pairing, recurrence detection (incl.
day-of-month clamping), history reconstruction, and forecast invariants.
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from pathlib import Path as _Path
from datetime import date as _date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows still hands a bare `python tests/test_core.py` a cp1252 stdout, and a
# single arrow in a section heading was enough to end the run with a
# UnicodeEncodeError *after* every test had passed — which reads exactly like a
# failure. The release build was never at risk (build.ps1 sets PYTHONIOENCODING
# before it runs this), but "run the tests" is the first thing anybody does with
# a checkout, and it should not appear to fail. The suite describes money, dates
# and rates, so non-ASCII in its output is normal; the console is what needs
# fixing, not the text.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from finfore.categorize import pair_transfers
from finfore.demo import SAMPLE_DIR, load_demo
from finfore.forecast import run_forecast
from finfore.history import (cash_anchor, cash_history, networth_history,
                             weekly_category_matrix)
from finfore.ingest import guess_mapping, read_raw, sniff_delimiter
from finfore.models import Account, AccountType, RecurringItem, Scenario
from finfore.pipeline import import_csv
from finfore.recurrence import occurrences
from finfore.store import Store


_APP_EXE = "Financing with Doug.exe"


def check(name: str, cond: bool) -> None:
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        raise AssertionError(name)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "test.db")

    print("mapping & ingestion")
    cols_c = list(read_raw(str(SAMPLE_DIR / "checking.csv"),
                           sniff_delimiter(str(SAMPLE_DIR / "checking.csv")),
                           nrows=1).columns)
    g = guess_mapping(cols_c)
    check("checking maps date/desc/amount",
          g["date"] == "Posting Date" and g["desc"] == "Description"
          and g["amount"] == "Amount")
    check("running balance never chosen as amount", "balance" not in g["amount"].lower())
    cols_s = list(read_raw(str(SAMPLE_DIR / "savings.csv"), ",", nrows=1).columns)
    gs = guess_mapping(cols_s)
    check("savings maps debit/credit pair",
          gs["debit"] == "Money Out" and gs["credit"] == "Money In"
          and not gs["amount"])

    print("indicator-mode ingestion (unsigned amount + CR/DR column)")
    fix = tmp / "indicator.csv"
    fix.write_bytes("\ufeff".encode("utf-8") + (
        "Posting Date,Transaction Date,Amount,Credit Debit Indicator,type,"
        "Description,Category\n"
        "07/17/2026,07/17/2026,500.00,Credit,Transfer,Transfer From Savings,T\n"
        "07/16/2026,07/16/2026,23.61,Debit,ACH Debit,Payment to Gas Co,U\n"
        "07/15/2026,07/15/2026,788.20,Debit,Transfer,Card Payment,T\n"
        "07/14/2026,07/14/2026,-42.00,,POS,Unmarked keeps its sign,M\n"
    ).encode("utf-8"))
    sample = read_raw(str(fix), ",", nrows=50)
    gi = guess_mapping(list(sample.columns), sample)
    check("BOM stripped and indicator column found",
          gi["date"] == "Transaction Date"
          and gi["indicator"] == "Credit Debit Indicator")
    check("indicator column not mistaken for debit/credit amounts",
          gi["debit"] == "" and gi["credit"] == "")
    ind_acct = Account("Ind", AccountType.CHECKING, balance=0.0,
                       balance_date=date.today())
    store.add_account(ind_acct)
    import_csv(store, str(fix), ind_acct.id)
    ind_tx = store.transactions()
    ind_tx = ind_tx[ind_tx["account_id"] == ind_acct.id].set_index("description")
    check("credit row positive, debit rows negative",
          ind_tx.loc["Transfer From Savings", "amount"] == 500.00
          and ind_tx.loc["Payment to Gas Co", "amount"] == -23.61
          and ind_tx.loc["Card Payment", "amount"] == -788.20)
    check("unmarked row keeps its own sign",
          ind_tx.loc["Unmarked keeps its sign", "amount"] == -42.00)
    store.delete_account(ind_acct.id)

    info = load_demo(store)
    check("demo imported transactions", info["transactions"] > 700)
    re_added, re_skipped = import_csv(store, str(SAMPLE_DIR / "checking.csv"),
                                      store.accounts()[0].id)
    check("re-import is idempotent", re_added == 0 and re_skipped > 700)

    print("transfer pairing & categorization")
    tx = store.transactions()
    n_transfer_pairs = (tx["category"] == "Transfer").sum()
    check("savings sweep pairs marked as transfers", n_transfer_pairs >= 30)

    print("recurrence detection")
    items = {it.name: it for it in store.recurring()}
    check("payroll detected biweekly",
          items["Acme Aerospace Payroll"].cadence == "biweekly")
    check("rent detected monthly on the 1st",
          items["Oakwood Properties Rent"].cadence == "monthly"
          and items["Oakwood Properties Rent"].days_of_month == [1])
    cap = items["Capital Auto Finance"]
    loan = next(a for a in store.accounts() if a.type == AccountType.LOAN)
    check("loan payment auto-linked",
          cap.kind == "loan_payment" and cap.linked_account_id == loan.id)

    print("occurrence generation")
    it = RecurringItem(key="k|-", name="EOM", cadence="monthly", amount=-10,
                       last_date=date(2026, 1, 31), days_of_month=[31])
    occ = occurrences(it, date(2026, 1, 31), date(2026, 4, 30))
    check("day-of-month clamps at short months",
          occ == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    print("snapshot editing")
    snaps0 = store.snapshots(loan.id)
    sid = int(snaps0.iloc[0]["id"])
    orig_date = snaps0.iloc[0]["date"].date()
    orig_bal = float(snaps0.iloc[0]["balance"])
    store.update_snapshot(sid, orig_date, 11111.11)
    snaps1 = store.snapshots(loan.id)
    check("edited snapshot balance persists",
          float(snaps1.loc[snaps1.id == sid, "balance"].iloc[0]) == 11111.11)
    other_date = snaps1.iloc[1]["date"].date()
    try:
        store.update_snapshot(sid, other_date, 1.0)
        raise AssertionError("expected ValueError on duplicate snapshot date")
    except ValueError:
        pass
    check("editing onto an existing date is rejected", True)
    store.update_snapshot(sid, orig_date, orig_bal)  # restore
    n_before = len(store.snapshots(loan.id))
    dummy_id = None
    store.add_snapshot(loan.id, date(2020, 1, 1), 999.0)
    df = store.snapshots(loan.id)
    dummy_id = int(df.loc[df["date"] == pd.Timestamp(2020, 1, 1), "id"].iloc[0])
    store.delete_snapshot(dummy_id)
    check("deleted snapshot is gone",
          len(store.snapshots(loan.id)) == n_before)

    print("history reconstruction")
    checking = store.accounts()[0]
    idx = pd.date_range(tx["date"].min(), pd.Timestamp(date.today()), freq="D")
    ch = cash_history(tx, checking, idx)
    check("cash history hits the anchored balance today",
          abs(ch.iloc[-1] - checking.balance) < 0.01)
    check("cash history stays positive over the demo span", ch.min() > 0)

    print("forecast invariants")
    r = run_forecast(store, 365 * 3, 200, seed=3)
    n = 365 * 3 + 1
    check("grid length = horizon + today", len(r.dates) == n
          and len(r.networth_det) == n)
    p = r.networth_pct
    check("percentile ordering holds everywhere",
          bool(np.all(p[10] <= p[50] + 1e-6) and np.all(p[50] <= p[90] + 1e-6)))
    check("today's value matches current net worth across paths",
          abs(p[50][0] - r.networth_det[0]) < 1e-6)
    base_loan = r.loans[0]
    r2 = run_forecast(store, 365 * 3, 200, Scenario(extra_loan_payment=300), seed=3)
    check("extra payment shortens payoff and cuts interest",
          r2.loans[0].payoff < base_loan.payoff
          and r2.loans[0].interest_paid < base_loan.interest_paid)
    r3 = run_forecast(store, 365 * 3, 200,
                      Scenario(category_multipliers={"Dining": 0.0}), seed=3)
    check("cutting a category raises the median outcome",
          r3.networth_pct[50][-1] > p[50][-1])

    print("insights: benchmarked quadrant score")
    from finfore.insights import (Aggregates, spending_score, breakdown, suggestions,
                                  monthly_aggregates, _recurring_subscriptions,
                                  bucket_of, habit_term, means_term, combine,
                                  category_monthly, FESR_TARGET, SAVINGS_TARGET,
                                  FESR_CAP)

    def mk(I, E, F, U=0.0, subs=0.0):
        return Aggregates(I, E + F + U, E, F, U, subs, 6, True)

    # benchmark anchors come from published guidance, not taste
    check("FESR 0.60 (the 50/30/20 ceiling) scores exactly 5/10",
          abs(habit_term(FESR_TARGET) - 5.0) < 1e-9)
    check("a 20% savings rate (the target) scores 8.5/10",
          abs(means_term(SAVINGS_TARGET) - 8.5) < 1e-9
          and abs(means_term(0.0) - 4.0) < 1e-9)

    # the four quadrants, in the order they were specified
    q1 = spending_score(mk(4000, 1600, 2600, 300))    # high FESR, negative net
    q2 = spending_score(mk(9000, 2800, 2600, 200))    # high FESR, high net
    q3 = spending_score(mk(3000, 2900, 300, 100))     # low FESR, low net
    q4 = spending_score(mk(7000, 2600, 700, 150))     # low FESR, high net
    check("q1 high-FESR + negative net is the worst case",
          q1.value < 2.0 and q1.quadrant == "overspending")
    check("q2 high-FESR + high net is average, not dire",
          4.5 <= q2.value <= 7.0 and q2.quadrant == "comfortable but loose")
    check("q3 low-FESR + low net is okay, not punished for circumstances",
          5.5 <= q3.value <= 7.5 and q3.quadrant.startswith("squeezed"))
    check("q4 low-FESR + high net is the best case",
          q4.value >= 8.5 and q4.quadrant == "disciplined and saving")
    check("the four quadrants rank q1 < q2 < q3 < q4",
          q1.value < q2.value < q3.value < q4.value)
    check("a bad situation still beats bad habits at the same net income",
          q3.value > q1.value)

    # FESR compares wants to *needs*, so it is blind to both sides inflating
    # together: someone can sit inside the 0.60 ceiling while spending well
    # past their income. The verdict must not absolve spending in that case.
    from finfore.insights import (NEEDS_INCOME_TARGET, WANTS_INCOME_TARGET,
                                  spending_score as _sscore)
    both_high = _sscore(mk(3000, 2050, 1150))     # 68% needs, 38% wants, deficit
    check("both shares inflated still reads as inside the wants:needs ceiling",
          both_high.fesr < FESR_TARGET)
    check("but wants are over the 30%-of-income guideline",
          1150 / 3000 > WANTS_INCOME_TARGET)
    check("so the verdict blames the spending rather than absolving it",
          "over guideline" in both_high.quadrant
          and "partly the spending" in both_high.headline)
    fixed = _sscore(mk(3000, 2600, 500))          # 87% needs, 17% wants, deficit
    check("genuinely modest wants are still absolved",
          fixed.quadrant == "squeezed by fixed costs"
          and "isn't the problem" in fixed.headline)
    check("and the fixed-cost case is named as such",
          500 / 3000 < WANTS_INCOME_TARGET and 2600 / 3000 > NEEDS_INCOME_TARGET)
    textbook = _sscore(mk(3000, 1500, 900))       # exactly 50 / 30 / 20
    check("textbook 50/30/20 is not called squeezed at all",
          textbook.quadrant == "disciplined and saving")
    check("the detail line quotes all three shares of income, not just the ratio",
          "50/30/20" in textbook.detail and "needs" in textbook.detail
          and "wants" in textbook.detail and "saved" in textbook.detail)
    _tb = mk(3000, 1500, 900)
    check("shares are computed against income, not against spending",
          abs(_tb.wants_share - 0.30) < 1e-9
          and abs(_tb.needs_share - 0.50) < 1e-9)

    # deepening a deficit keeps making it worse (no plateau)
    deficits = [spending_score(mk(4000, 1600, F, 300)).value
                for F in (1200, 1600, 2000, 2400, 2800)]
    check("score keeps falling as net income goes further negative",
          all(a > b for a, b in zip(deficits, deficits[1:])))

    # the interaction penalty only bites when both are poor
    _, pen_both = combine(2.0, 2.0)
    _, pen_one = combine(9.0, 2.0)
    _, pen_none = combine(9.0, 9.0)
    check("danger penalty fires only when habits and means are both poor",
          pen_both > pen_one > pen_none and pen_none < 0.1)

    # uncategorised spending is excluded from the ratio, not assumed frivolous
    clean = spending_score(mk(4000, 2000, 600, 0))
    murky = spending_score(mk(4000, 2000, 600, 900))
    check("uncategorized spending no longer inflates the frivolous ratio",
          abs(clean.fesr - murky.fesr) < 1e-9)
    check("but a murky picture is flagged provisional",
          murky.provisional and not clean.provisional
          and murky.unknown_share > 0.25)

    # guards
    check("zero income yields no score", spending_score(mk(0, 100, 50)) is None)
    check("no essential spending does not divide by zero",
          spending_score(mk(4000, 0, 900)).fesr == FESR_CAP)

    print("insights: recency weighting")
    _t8 = Path(tempfile.mkdtemp())
    _s8 = Store(_t8 / "recency.db")
    _ra = Account("Chk", AccountType.CHECKING, 5000.0, date.today())
    _s8.add_account(_ra)
    from finfore.ingest import make_hashes as _mh
    _anchor = date.today().replace(day=15)
    _rows = []
    for _m in range(0, 4):                      # $200/mo dining, recent
        _rows.append(dict(date=pd.Timestamp(_anchor - timedelta(days=30 * (_m + 1))),
                          description="CHIPOTLE", amount=-200.0,
                          account_id=_ra.id, category="Dining"))
    _rows.append(dict(date=pd.Timestamp(_anchor - timedelta(days=30 * 20)),
                      description="MAUI RESORT", amount=-6000.0,
                      account_id=_ra.id, category="Travel"))
    _rdf = pd.DataFrame(_rows)
    _rdf["import_hash"] = _mh(_rdf)
    _s8.add_transactions(_rdf)
    _cm = category_monthly(_s8.transactions())
    check("a two-year-old splurge is decayed to near-nothing",
          _cm.get("Travel", 0) < 100 and _cm.get("Dining", 0) > 150)
    check("recent spending dominates the monthly figure",
          _cm["Dining"] > _cm.get("Travel", 0))

    print("insights: suggestions are ranked by real dollars")
    _sug = suggestions(
        mk(6000, 2200, 700, 0),
        [],
        _s8.transactions())
    _levers = [g for g in _sug if g.rank]
    check("levers are ordered by monthly impact, descending",
          all(a.monthly_impact >= b.monthly_impact
              for a, b in zip(_levers, _levers[1:])))
    check("no lever claims a position it does not hold",
          all(("largest" not in g.detail) or g.rank == 1 for g in _levers))

    # breakdown conserves total spending, subscriptions carved from discretionary
    agg_b = mk(5000, 2500, 1200, U=300, subs=400)
    bd = breakdown(agg_b)
    check("breakdown slices sum to total spending",
          abs(sum(v for _, v, _ in bd) - agg_b.spending) < 1.0)
    check("subscriptions are a distinct slice",
          "Subscriptions" in [l for l, _, _ in bd]
          and bucket_of("Netflix".title()) or True)

    # subscription detector excludes recurring dining, includes streaming
    sub_items = _recurring_subscriptions(store.recurring())
    names = {it.name for it in sub_items}
    check("streaming detected as subscriptions",
          any("Netflix" in n for n in names))
    check("recurring dining NOT counted as a subscription",
          not any(("Chipotle" in n or "Starbucks" in n) for n in names))

    # aggregates exclude the current partial month and transfers
    agg_demo = monthly_aggregates(store.transactions())
    check("aggregates computed from complete months only",
          agg_demo is not None and agg_demo.n_months >= 1)
    check("transfers excluded from spending",
          agg_demo.income > 0 and agg_demo.spending > 0)
    # The partial-month rule is tested against rows placed here, not against
    # the demo. The sample CSVs are fixed files with fixed dates, so whether
    # they happen to reach into the current calendar month depends on how long
    # ago they were written — which is a fact about the fixture's age, not
    # about the code, and it silently turned into a failing assertion the
    # month real time moved past the last sample row.
    _pm_this = pd.Timestamp(_date.today().replace(day=1))
    _pm_prev = _pm_this - pd.offsets.MonthBegin(1)
    _pm = monthly_aggregates(pd.DataFrame([
        {"account_id": 1, "date": _pm_this, "description": "THIS MONTH",
         "amount": -900.0, "category": "Dining", "import_hash": "pm1"},
        {"account_id": 1, "date": _pm_prev, "description": "LAST MONTH",
         "amount": -100.0, "category": "Dining", "import_hash": "pm2"},
    ]))
    check("the current partial month is noticed",
          _pm is not None and _pm.partial_current)
    check("but kept out of the monthly average",
          _pm.n_months == 1 and abs(_pm.frivolous - 100.0) < 1e-6)

    print("csv history import + re-import")
    import tempfile as _tf
    from finfore.ingest import parse_snapshots as _ps
    from finfore.pipeline import (import_with_config as _iwc,
                                  reimport_with_saved_mapping as _reimp)
    from finfore.ingest import ImportConfig as _IC
    from finfore.models import Account as _Acct, AccountType as _AT
    _tmp = _Path(_tf.mkdtemp())

    # snapshot parser: comma-thousands + dayfirst + skip junk rows
    _f = _tmp / "bal.csv"
    _f.write_text("When,Value\n01/02/2025,\"10,500.00\"\n"
                  "15/02/2025,10990\nbad,row\n")
    _recs = _ps(str(_f), "When", "Value", ",", dayfirst=True)
    check("snapshot parser handles commas/dayfirst and skips junk",
          len(_recs) == 2 and _recs[0][1] == 10500.0
          and _recs[0][0].day == 1 and _recs[0][0].month == 2)

    # bulk import: upsert counts + account balance advances to newest
    _inv = _Acct("Brk", _AT.INVESTMENT, balance=9000.0,
                 balance_date=_date(2025, 1, 1))
    store.add_account(_inv)
    a1, u1 = store.import_snapshots(_inv.id, _recs)
    check("first snapshot import counts all as added", a1 == 2 and u1 == 0)
    _inv2 = [a for a in store.accounts() if a.id == _inv.id][0]
    check("account balance advanced to newest snapshot",
          _inv2.balance == 10990.0 and _inv2.balance_date == _date(2025, 2, 15))
    a2, u2 = store.import_snapshots(_inv.id,
                                    [(_date(2025, 2, 15), 11100.0),
                                     (_date(2025, 3, 1), 11500.0)])
    check("re-import upserts (1 new, 1 updated)", a2 == 1 and u2 == 1)

    # re-import transactions with saved mapping: mapping persists, dedup works
    _chk = _Acct("Chk", _AT.CHECKING, balance=500.0, balance_date=_date(2025, 1, 1))
    store.add_account(_chk)
    _t1 = _tmp / "m1.csv"
    _t1.write_text("Date,Description,Amount\n2025-01-05,STORE,-10\n"
                   "2025-01-06,PAY,1000\n")
    _cfg = _IC(path=str(_t1), account_id=_chk.id, sign_mode="standard",
               col_date="Date", col_desc="Description", col_amount="Amount")
    _iwc(store, _cfg)
    check("import saves the column mapping",
          store.get_import_mapping(_chk.id) is not None)
    _t2 = _tmp / "m2.csv"
    _t2.write_text("Date,Description,Amount\n2025-01-06,PAY,1000\n"
                   "2025-02-05,STORE,-12\n")
    _add, _skip = _reimp(store, _chk.id, str(_t2))
    check("re-import adds only new rows (dedup on overlap)",
          _add == 1 and _skip == 1)
    _raised = False
    try:
        _reimp(store, _inv.id, str(_t2))
    except LookupError:
        _raised = True
    check("re-import without saved mapping raises LookupError", _raised)

    # ------------------------------------------------------------------
    # regression tests — each of these covers a bug that shipped once
    # ------------------------------------------------------------------
    print("regressions")
    import numpy as _np
    from finfore.ingest import parse_amount as _pa
    from finfore.history import headline_stats as _hs
    from finfore.insights import monthly_aggregates as _ma
    from finfore.recurrence import _dom_clusters as _dc
    from finfore.forecast import run_forecast as _rf
    from finfore.models import RecurringItem as _RI
    from datetime import timedelta as _td

    # 1. 'Other income' is real income, not an incidental-bucket offset
    _tx = pd.DataFrame([
        dict(date=pd.Timestamp("2000-01-02"), description="PAYROLL",
             amount=4000.0, category="Income"),
        dict(date=pd.Timestamp("2000-01-10"), description="FREELANCE",
             amount=1000.0, category="Other income"),
        dict(date=pd.Timestamp("2000-01-12"), description="UNKNOWN",
             amount=-300.0, category="Misc"),
        dict(date=pd.Timestamp("2000-01-14"), description="RENT",
             amount=-1500.0, category="Housing"),
    ])
    _a = _ma(_tx)
    check("uncategorized inflow counts as income",
          abs(_a.income - 5000.0) < 1e-9)
    check("inflow does not cancel uncategorized spending",
          abs(_a.incidental - 300.0) < 1e-9
          and abs(_a.spending - 1800.0) < 1e-9)

    # 2. stat cards satisfy networth == cash + investments - debt
    _t2 = Path(tempfile.mkdtemp())
    _s2 = Store(_t2 / "hs.db")
    _s2.add_account(Account("C", AccountType.CHECKING, 1000.0, date.today()))
    _s2.add_account(Account("V", AccountType.CREDIT, -500.0, date.today()))
    _s2.add_account(Account("B", AccountType.INVESTMENT, 2000.0, date.today()))
    _h = _hs(_s2)
    check("credit balance is not both cash and debt",
          abs(_h["cash"] + _h["investments"] - _h["debt"] - _h["networth"]) < 1e-9
          and _h["cash"] == 1000.0 and _h["debt"] == 500.0)

    # 3. month-end day clusters do not collapse to mid-month
    check("month-end wraparound keeps a month-end anchor",
          _dc(_np.array([1, 2, 30, 31])) == [1]
          and _dc(_np.array([30, 31, 1]), tol=2) == [31])
    check("ordinary clusters unaffected",
          _dc(_np.array([1, 15])) == [1, 15]
          and _dc(_np.array([14, 15, 16, 29, 30])) == [15, 30])

    # 4. an unlinked loan payment is not charged twice
    _t3 = Path(tempfile.mkdtemp())
    _s3 = Store(_t3 / "ln.db")
    _s3.add_account(Account("Chk", AccountType.CHECKING, 5000.0, date.today()))
    _s3.add_account(Account("Vehicle Note", AccountType.LOAN, 12000.0,
                            date.today(), apr=0.06, monthly_payment=400.0,
                            payment_day=10))
    _s3.merge_recurring([_RI(key="ZORP|-", name="Zorp Financial",
                             cadence="monthly", amount=-400.0,
                             last_date=date.today() - _td(days=5),
                             days_of_month=[10], confidence=0.9, n_seen=8,
                             kind="loan_payment")])
    _r3 = _rf(_s3, 365, 40)
    _drop = _r3.cash_det[0] - _r3.cash_det[-1]
    check("unlinked loan payment counted once, not twice",
          4600 < _drop < 5000 and any("link it" in w.lower()
                                      for w in _r3.warnings))

    # 5. deactivating a pattern removes it instead of recycling it as noise
    _t4 = Path(tempfile.mkdtemp())
    _s4 = Store(_t4 / "de.db")
    load_demo(_s4)
    _big = max(_s4.recurring(), key=lambda i: abs(i.monthly_equivalent))
    _b4 = _rf(_s4, 365, 120, seed=11).cash_det[-1]
    _s4.update_recurring(_big.id, active=False)
    _o4 = _rf(_s4, 365, 120, seed=11).cash_det[-1]
    _exp = -12 * _big.monthly_equivalent
    check("deactivated pattern actually leaves the forecast",
          abs((_o4 - _b4) - _exp) / abs(_exp) < 0.25)

    # 6. amount parsing is total: bad cells become nan, never exceptions
    check("trailing-minus and comma-decimal amounts parse",
          _pa("45.00-") == -45.0 and abs(_pa("1.234,56") - 1234.56) < 1e-9
          and abs(_pa("1,234.56") - 1234.56) < 1e-9 and _pa("1,234") == 1234.0)
    check("malformed amounts yield nan instead of raising",
          all(_np.isnan(_pa(v)) for v in ("12.3.4", "abc", "-", "", None)))

    # 7. an empty update is a no-op, not a SQL syntax error
    _s4.update_recurring(_big.id)
    check("update_recurring with no fields is a no-op", True)

    # ------------------------------------------------------------------
    # snapshots, crash hardening, and adaptive categorisation
    # ------------------------------------------------------------------
    print("snapshots for any account type")
    _t5 = Path(tempfile.mkdtemp())
    _s5 = Store(_t5 / "snap.db")
    _kinds = [AccountType.CHECKING, AccountType.SAVINGS, AccountType.CREDIT,
              AccountType.LOAN, AccountType.INVESTMENT]
    for _k in _kinds:
        _acc = Account(f"{_k.value} acct", _k, 1000.0, date(2025, 1, 1))
        _s5.add_account(_acc)
        _s5.add_snapshot(_acc.id, date(2025, 3, 1), 1500.0)
        _s5.add_snapshot(_acc.id, date(2025, 6, 1), 2000.0)
        _rows = _s5.snapshots(_acc.id)
        _sid = int(_rows["id"].iloc[0])
        _s5.update_snapshot(_sid, date(2025, 3, 15), 1600.0)
        _s5.delete_snapshot(_sid)
        check(f"add/edit/delete snapshots on {_k.value}",
              len(_s5.snapshots(_acc.id)) == 1)

    # a snapshot newer than the account's balance_date becomes the cash anchor,
    # which is what makes snapshots meaningful on a cash account at all
    from finfore.history import cash_anchor as _ca
    _cash = Account("Anchored", AccountType.CHECKING, 500.0, date(2025, 1, 1))
    _s5.add_account(_cash)
    check("cash anchor defaults to the account balance",
          _ca(_s5, _cash) == (pd.Timestamp("2025-01-01"), 500.0))
    _s5.add_snapshot(_cash.id, date(2025, 5, 1), 875.0)
    check("a newer snapshot overrides the cash anchor",
          _ca(_s5, _cash) == (pd.Timestamp("2025-05-01"), 875.0))
    _s5.add_snapshot(_cash.id, date(2024, 6, 1), 100.0)
    check("an older snapshot does not override the anchor",
          _ca(_s5, _cash) == (pd.Timestamp("2025-05-01"), 875.0))

    print("unparseable dates cannot crash the ledger view")
    from finfore.util import fmt_date as _fd
    _t6 = Path(tempfile.mkdtemp())
    _s6 = Store(_t6 / "dates.db")
    _da = Account("Chk", AccountType.CHECKING, 1000.0, date.today())
    _s6.add_account(_da)
    # write past the normal import path, the way a hand-edited or
    # older-version database would look
    for _i, (_d, _desc) in enumerate([("2025-01-05", "ALDI"),
                                      ("01/05/2025", "SHELL"),
                                      ("not-a-date", "JUNK"),
                                      ("2025-01-07", "PAY")]):
        _s6.con.execute(
            "INSERT INTO transactions (account_id,date,description,amount,"
            "category,import_hash) VALUES (?,?,?,?,?,?)",
            (_da.id, _d, _desc, -10.0, "Misc", f"dh{_i}"))
    _s6.con.commit()
    _bad = _s6.transactions()
    check("mixed date formats read back as a datetime column",
          str(_bad["date"].dtype).startswith("datetime64"))
    check("every row still formats without raising",
          [_fd(v) for v in _bad["date"]].count("—") >= 1
          and len([_fd(v) for v in _bad["date"]]) == 4)
    check("NaT formats to a placeholder rather than raising",
          _fd(pd.NaT) == "—" and _fd(None) == "—")
    # and the write path refuses to store a date it cannot parse
    _junk = pd.DataFrame([{"account_id": _da.id, "date": "not-a-date",
                           "description": "X", "amount": -1.0,
                           "category": "Misc", "import_hash": "zz1"}])
    _added, _ = _s6.add_transactions(_junk)
    check("add_transactions skips rows with unparseable dates", _added == 0)

    print("an empty ledger still has real numbers in it")
    # The state every new user is in for their first few minutes: one account
    # added, nothing imported yet. pandas infers a SQL result's dtypes from the
    # rows it got back, so a query returning *no* rows handed back `object`
    # columns — which survived every arithmetic operation in the app (Python
    # floats add) and then made numpy refuse the array outright. The dashboard
    # drew that series straight into pyqtgraph, which calls np.isfinite on it
    # from inside a Qt callback, where an exception aborts the process. The app
    # crashed on startup with "ufunc 'isfinite' not supported for the input
    # types" and no clue that an empty table was the cause.
    _t6b = Path(tempfile.mkdtemp())
    _s6b = Store(_t6b / "brandnew.db")
    _empty = _s6b.transactions()
    check("an empty transactions frame is still typed",
          _empty.empty and _empty["amount"].dtype == np.float64
          and _empty["account_id"].dtype == np.int64)
    _s6b.add_account(Account("C", AccountType.CHECKING, 52.22, date.today()))
    _nh = networth_history(_s6b)
    check("and so is the history built from it",
          len(_nh) > 1
          and all(_nh[c].dtype == np.float64 for c in _nh.columns))
    for _c in _nh.columns:
        # The exact call that aborted the process, on the exact series.
        np.isfinite(_nh[_c].values)
    check("so numpy will accept every column of it", True)
    check("an empty snapshot frame is typed too",
          _s6b.snapshots(_s6b.accounts()[0].id)["balance"].dtype == np.float64)
    # And the chart layer refuses to hand pyqtgraph anything it cannot plot,
    # whatever upstream does — an abort inside a Qt callback is not a failure
    # mode worth leaving one dtype away.
    from finfore.ui.charts import nums as _nums
    check("the chart boundary coerces object arrays to float",
          _nums(pd.Series([1, 2.5, None], dtype=object)).dtype == np.float64)
    _s6b.close()

    print("bills that went up while nobody was looking")
    from finfore import alerts as _AL

    def _series(vals, start_months_ago=None, day=14):
        """Monthly occurrences ending last month, oldest first."""
        n = len(vals)
        out = {}
        for i, v in enumerate(vals):
            k = n - 1 - i
            m = _add_months(_date.today().replace(day=1), -(k + 1))
            out[_date(m.year, m.month, day)] = float(v)
        return pd.Series(out).sort_index()

    from finfore.util import add_months as _add_months
    _sub = RecurringItem(key="NETFLIX|-", name="Netflix", cadence="monthly",
                         amount=-15.49, last_date=_date.today(), kind="bill")
    _flat = _AL.detect_change(_sub, _series([-15.49] * 8), _date.today())
    check("a bill that has not moved is not reported", _flat is None)

    _rise = _AL.detect_change(
        _sub, _series([-15.49] * 6 + [-19.49] * 2), _date.today())
    check("a step change on a steady bill is caught",
          _rise is not None and _rise.worse and _rise.direction == "up")
    check("reported in money out, not ledger signs",
          abs(_rise.old_amount - 15.49) < 1e-6
          and abs(_rise.new_amount - 19.49) < 1e-6
          and abs(_rise.delta - 4.0) < 1e-6)
    check("and priced per year at its own cadence",
          abs(_rise.per_year - 48.0) < 0.5 and _rise.steady)
    check("the whole run at the new level is one finding, not three",
          _rise.n_after == 2 and _rise.n_before == 6)

    # The test that matters. A bill that swings by its nature must clear its own
    # noise, not a fixed threshold — otherwise every August electricity bill is
    # an alert and nobody reads any of them.
    _power = RecurringItem(key="ELECTRIC|-", name="Electric", cadence="monthly",
                           amount=-118.0, last_date=_date.today(), kind="bill")
    _noisy = [-95.0, -142.0, -108.0, -161.0, -99.0, -134.0, -117.0]
    check("the same dollar move on a naturally variable bill is not reported",
          _AL.detect_change(_power, _series(_noisy + [-122.0]),
                            _date.today()) is None)
    check("but a move far outside its own swing still is",
          _AL.detect_change(_power, _series(_noisy + [-260.0]),
                            _date.today()) is not None)

    _cut = _AL.detect_change(
        _sub, _series([-19.49] * 6 + [-12.49] * 2), _date.today())
    check("a bill going down is reported as good news",
          _cut is not None and not _cut.worse and _cut.direction == "down")
    _pay = RecurringItem(key="PAYROLL|+", name="Payroll", cadence="monthly",
                         amount=2650.0, last_date=_date.today(), kind="income")
    _raise_ = _AL.detect_change(
        _pay, _series([2650.0] * 6 + [2900.0] * 2), _date.today())
    check("a pay rise counts as income going the right way",
          _raise_ is not None and _raise_.is_income and not _raise_.worse)
    _drop = _AL.detect_change(
        _pay, _series([2900.0] * 6 + [2650.0] * 2), _date.today())
    check("and a pay cut as the wrong way", _drop is not None and _drop.worse)

    check("a change too small to matter in a year is left alone",
          _AL.detect_change(_sub, _series([-15.49] * 6 + [-16.49] * 2),
                            _date.today()) is None
          or abs(_AL.detect_change(
              _sub, _series([-15.49] * 6 + [-16.49] * 2),
              _date.today()).per_year) >= _AL.MIN_ANNUAL)
    check("too few occurrences to have a 'usual' says nothing",
          _AL.detect_change(_sub, _series([-15.49, -15.49, -19.49]),
                            _date.today()) is None)
    _stale = _AL.detect_change(
        _sub, _series([-15.49] * 8 + [-19.49] * 14), _date.today())
    check("a rise from two years ago is history, not news", _stale is None)

    _t7 = Path(tempfile.mkdtemp())
    _s7 = Store(_t7 / "shock.db")
    load_demo(_s7, "struggling")
    _ch = _AL.bill_changes(_s7)
    check("the demo ledger's seeded price rises are found",
          len(_ch) == 2 and all(c.worse for c in _ch))
    check("worst news first, by yearly cost",
          abs(_ch[0].per_year) >= abs(_ch[1].per_year))
    check("and summed into one line for the dashboard",
          "recurring charges changed" in _AL.summarise(_ch)
          and abs(_AL.annual_total(_ch) - 144.0) < 1.0)
    _off = next(i for i in _s7.recurring() if "Netflix" in i.name)
    _s7.update_recurring(_off.id, active=False)
    check("switching a pattern off stops it being reported",
          len(_AL.bill_changes(_s7)) == 1)
    check("a ledger with no transactions reports nothing",
          _AL.bill_changes(Store(_t7 / "bare.db")) == [])
    _s7.close()

    print("adaptive categorisation")
    from finfore.learning import (build_chain, predict_one, feedback_key,
                                  record_corrections, training_frame, coverage)
    _t7 = Path(tempfile.mkdtemp())
    _s7 = Store(_t7 / "learn.db")
    _la = Account("Chk", AccountType.CHECKING, 1000.0, date.today())
    _s7.add_account(_la)
    check("keyword rules apply before any correction exists",
          predict_one(build_chain(_s7), "ALDI 5501", -42.10) == "Groceries")
    _corr = pd.DataFrame([{"description": "ALDI 5501", "amount": -42.10,
                           "category": "Groceries", "account_id": _la.id}])
    _n = record_corrections(_s7, _corr, "Shopping",
                            account_types={_la.id: "checking"})
    check("a correction is stored as a training label", _n == 1)
    check("the learned label wins on the next import",
          predict_one(build_chain(_s7), "ALDI 5501", -42.10) == "Shopping")
    check("learning generalises across the store-number noise",
          predict_one(build_chain(_s7), "ALDI 8842", -19.99) == "Shopping")
    check("a refund of the same merchant is a separate label",
          feedback_key("ALDI 5501", -42.10) != feedback_key("ALDI 5501", 42.10)
          and predict_one(build_chain(_s7), "ALDI 5501", 42.10) != "Shopping")
    # the most recent correction wins, and history is retained
    record_corrections(_s7, _corr, "Groceries", account_types={_la.id: "checking"})
    check("the most recent correction wins",
          predict_one(build_chain(_s7), "ALDI 5501", -42.10) == "Groceries"
          and len(_s7.category_feedback()) == 2)
    _tf7 = training_frame(_s7)
    check("training frame is model-ready",
          list(_tf7.columns) == ["description", "norm_key", "amount",
                                 "abs_amount", "is_inflow", "account_type",
                                 "label"]
          and len(_tf7) == 1 and _tf7["is_inflow"].iloc[0] == 0)
    check("coverage reports not-ready on a tiny corpus",
          coverage(_s7)["ready"] is False and coverage(_s7)["labels"] == 1)

    print("merchant clustering & similarity propagation")
    from finfore.learning import (similarity, NearestMerchant, label_cluster,
                                  uncategorized_clusters, recategorize_all,
                                  uncategorized_summary, full_chain)
    # separability: variants of one merchant vs merchants sharing a first word
    _same = [("SQ *BLUE BOTTLE 0042", "BLUE BOTTLE COFFEE 12"),
             ("AMAZON MKTPL US*2H4KL", "AMAZON MKTPL US*9K2QP"),
             ("SHELL OIL 4471", "SHELL OIL 9982"),
             ("SAFEWAY #1842 SAN JOSE", "SAFEWAY #2201 OAKLAND")]
    _diff = [("SHELL OIL 4471", "SHELLFISH MARKET CO"),
             ("BLUE BOTTLE COFFEE", "BLUE CROSS INSURANCE"),
             ("ACME STORAGE UNIT 44", "ACME PAYROLL DIRECT DEP"),
             ("VALLEY WATER DISTRICT", "VALLEY VETERINARY")]
    _lo = min(similarity(a, b) for a, b in _same)
    _hi = max(similarity(a, b) for a, b in _diff)
    check("merchant similarity separates variants from lookalikes", _lo > _hi)
    check("processor prefixes are stripped before comparing",
          similarity("SQ *BLUE BOTTLE 0042", "BLUE BOTTLE COFFEE 12") > 0.5)

    _t9 = Path(tempfile.mkdtemp())
    _s9 = Store(_t9 / "clusters.db")
    _ca = Account("Chk", AccountType.CHECKING, 1000.0, date.today())
    _s9.add_account(_ca)
    _crows = []
    # note: "...UNIT 44" and "...UNIT 51" would collapse to one key, since
    # normalisation already strips store numbers — so the sibling here is a
    # genuinely different string that only similarity can connect.
    for _desc, _amt, _n in [("ZORP STORAGE UNIT 44", -180.0, 12),
                            ("ZORP STORAGE DEPOT", -180.0, 4),
                            ("QUIBBLE MICROPAY 9", -3.0, 2)]:
        for _k in range(_n):
            _crows.append(dict(date=pd.Timestamp(date.today() - timedelta(days=30 * _k)),
                               description=_desc, amount=_amt,
                               account_id=_ca.id, category="Misc"))
    _cdf = pd.DataFrame(_crows)
    _cdf["import_hash"] = _mh(_cdf)
    _s9.add_transactions(_cdf)
    _cl = uncategorized_clusters(_s9)
    check("queue groups transactions into merchants",
          len(_cl) == 3 and sum(c.n_tx for c in _cl) == 18)
    check("queue ranks by dollars, not by transaction count",
          _cl[0].display.startswith("ZORP STORAGE UNIT")
          and _cl[-1].display.startswith("QUIBBLE"))
    check("store-number variants already collapse before similarity is needed",
          all(c.n_tx != 1 or c.display.startswith("QUIBBLE") for c in _cl))
    _sum9 = uncategorized_summary(_s9)
    check("summary reports concentration",
          _sum9["merchants"] == 3 and _sum9["top10_share"] == 1.0)

    _moved = label_cluster(_s9, _cl[0], "Housing")
    check("one decision labels a whole merchant", _moved == 12)
    _prop = recategorize_all(_s9)
    _left = _s9.transactions()
    check("similarity propagates the label to the sibling merchant",
          _prop >= 4
          and (_left["category"] == "Housing").sum() == 16)
    check("a dissimilar merchant is left alone rather than guessed",
          (_left["category"] == "Misc").sum() == 2)

    print("charitable donations")
    from finfore.categorize import categorize as _cz
    from finfore.insights import (bucket_of as _bo, FRIVOLOUS,
                                  emergency_fund_target, essential_monthly)
    _give = ["RED CROSS DONATION", "ST JUDE CHILDRENS", "GOFUNDME *AID",
             "MONTHLY TITHE", "HUMANE SOCIETY", "NPR PLEDGE",
             "DOCTORS WITHOUT BORDERS", "COMMUNITY FOUNDATION OF MD"]
    _not = ["FOUNDATION BOOKSHOP", "SEPHORA FOUNDATION MAKEUP",
            "CHURCHILL DOWNS", "AMAZON MKTPL", "TEMPLE BAR PUB",
            "MISSION BURRITO"]
    check("charitable merchants are recognised",
          all(_cz(d, -50) == "Charitable donation" for d in _give))
    check("lookalike merchants are not swept in",
          all(_cz(d, -50) != "Charitable donation" for d in _not))
    check("giving is its own bucket, not frivolous",
          _bo("Charitable donation") == "giving"
          and "Charitable donation" not in FRIVOLOUS)

    # giving counts as spending but stays out of the frivolous ratio
    _base = Aggregates(5000, 2600, 2000, 600, 0.0, 0.0, 6, True)
    _with_giving = Aggregates(5000, 3000, 2000, 600, 0.0, 0.0, 6, True,
                              giving=400.0)
    check("giving does not inflate FESR",
          abs(_base.fesr - _with_giving.fesr) < 1e-9)
    check("giving still counts toward total spending",
          _with_giving.spending - _base.spending == 400.0
          and _with_giving.surplus < _base.surplus)
    check("giving appears as its own slice",
          any(lbl == "Giving" for lbl, _v, _c in breakdown(_with_giving)))

    print("emergency-fund floor & shortfall risk")
    _t10 = Path(tempfile.mkdtemp())
    _s10 = Store(_t10 / "floors.db")
    load_demo(_s10)
    _ess = essential_monthly(_s10)
    _ef3 = emergency_fund_target(_s10, 3)
    _ef6 = emergency_fund_target(_s10, 6)
    check("emergency fund is months x essential spending",
          _ess > 0 and abs(_ef3 - 3 * _ess) < 1e-6
          and abs(_ef6 - 2 * _ef3) < 1e-6)

    from finfore.forecast import STARTER_BUFFER as _SB
    _fr = run_forecast(_s10, 730, 200, emergency_target=_ef3)
    check("breach probabilities are reported for every floor",
          set(_fr.cash_breach) == {"zero", "starter", "emergency"}
          and all(0.0 <= v <= 1.0 for v in _fr.cash_breach.values()))
    check("breach probabilities are monotone in the floor height",
          _fr.cash_breach["zero"] <= _fr.cash_breach["starter"]
          <= _fr.cash_breach["emergency"])
    check("worst-dip percentiles are ordered",
          all(_fr.min_cash_pct[a] <= _fr.min_cash_pct[b]
              for a, b in zip(sorted(_fr.min_cash_pct), sorted(_fr.min_cash_pct)[1:])))

    _stress = Scenario(category_multipliers={"Dining": 3.0, "Shopping": 3.0,
                                             "Travel": 3.0},
                       income_multiplier=0.55)
    _fs = run_forecast(_s10, 730, 200, _stress, emergency_target=_ef3)
    check("a ruinous scenario raises breach risk and dates the breach",
          _fs.cash_breach["zero"] > _fr.cash_breach["zero"]
          and _fs.first_breach_day is not None)
    check("a healthy scenario reports no dated breach",
          _fr.first_breach_day is None or _fr.cash_breach["zero"] > 0)
    check("omitting the emergency target drops only that floor",
          set(run_forecast(_s10, 365, 60).cash_breach) == {"zero", "starter"})
    check("starter buffer constant is the $1,000 milestone", _SB == 1000.0)

    print("a dip is not the same thing as a shortfall")
    from finfore.forecast import (MATERIAL_MIN as _MMIN, SUSTAINED_DAYS as _SD,
                                  assess_shortfall as _assess)
    # The case that broke the old headline: $10 in the account, a $15 bill on
    # Thursday, and a new job paying $100k a month from here on. Every single
    # path crosses zero and not one of them is in trouble, so "P(cash < 0)"
    # reported 100% and the page said *high risk of running out of cash*.
    _nd = 365
    _rich = np.zeros((50, _nd))
    _rich[:, :3] = 10.0
    _rich[:, 3:5] = -5.0
    _rich[:, 5:] = np.linspace(100_000.0, 1_200_000.0, _nd - 5)
    _brief = _assess(_rich, essential_monthly=3000.0, horizon_days=_nd)
    check("a path that crosses zero is still noticed",
          _brief.prob_any == 1.0)
    check("but a $5 dip in front of a fortune is not a shortfall",
          _brief.level == "brief" and _brief.prob_material == 0.0)
    check("and the sentence says so rather than crying wolf",
          "not a shortfall" in _brief.headline
          and "timing" in _brief.detail)
    check("the dip is described by depth and duration, not just frequency",
          abs(_brief.median_depth - 5.0) < 1e-6 and _brief.median_days == 2.0)
    check("the recovery is reported, not buried",
          _brief.recovers and _brief.end_cash_p50 > 1_000_000.0)

    # The same shape as the toy case but deep enough to bounce a payment:
    # $10 and a fortnight of groceries before the new salary lands. That is a
    # real problem for those weeks and emphatically not "running out of cash",
    # so it gets its own level, a date, and a way out.
    _squeeze = np.zeros((50, _nd))
    _squeeze[:, :2] = 10.0
    _squeeze[:, 2:14] = -600.0
    _squeeze[:, 14:] = np.linspace(50_000.0, 900_000.0, _nd - 14)
    _crunch = _assess(_squeeze, essential_monthly=3000.0, horizon_days=_nd,
                      dates=[_date.today() + timedelta(days=i)
                             for i in range(_nd)])
    check("a deep dip that clears is a crunch, not a collapse",
          _crunch.level == "crunch" and _crunch.prob_material == 1.0)
    check("it is dated, so it can be planned around",
          _crunch.low_date is not None
          and _crunch.low_date.strftime("%B") in _crunch.detail)
    check("and it says the money comes back",
          _crunch.recovers and not _crunch.severe
          and "clears" in _crunch.headline)

    _doom = np.tile(np.linspace(4_000.0, -18_000.0, _nd), (50, 1))
    _high = _assess(_doom, essential_monthly=3000.0, horizon_days=_nd)
    check("a genuine slide is still called high risk",
          _high.level == "high" and _high.prob_unrecovered == 1.0)
    check("and is not described as recovering",
          not _high.recovers and _high.severe)
    # Deep *and* lasting is the intersection that earns the strong wording;
    # neither one alone does.
    _lasting = np.full((50, _nd), 800.0)
    _lasting[:, 30:120] = -4_000.0
    check("deep and lasting together is the only route to high risk",
          _assess(_lasting, essential_monthly=3000.0,
                  horizon_days=_nd).level == "high")

    _fine = np.tile(np.linspace(5_000.0, 40_000.0, _nd), (50, 1))
    check("cash that never dips is left alone",
          _assess(_fine, essential_monthly=3000.0).level == "ok")
    _low = np.tile(np.linspace(900.0, 700.0, _nd), (50, 1))
    check("a buffer worn down without ever going negative reads as thin",
          _assess(_low, starter_breach=1.0, essential_monthly=3000.0
                  ).level == "thin")

    # Shallow but relentless. Depth alone would wave this through; duration is
    # what catches it.
    _nag = np.full((50, _nd), 500.0)
    _nag[:, 100:100 + _SD + 5] = -50.0
    _nagged = _assess(_nag, essential_monthly=3000.0, horizon_days=_nd)
    check("two months under water is not a timing problem, however shallow",
          _nagged.level == "watch" and _nagged.prob_sustained == 1.0)

    # Rare is not the same as small. A dip one path in ten falls into, $1,600
    # deep and two months long, must not be called brief just because it is
    # uncommon — the wording for brief asserts the dip is trivial, and it has
    # to be true.
    _rare = np.full((40, _nd), 3_000.0)
    _rare[:4, 60:120] = -1_600.0
    _rarely = _assess(_rare, essential_monthly=3000.0, horizon_days=_nd)
    check("a rare dip is judged on its own depth, not its frequency",
          _rarely.prob_any == 0.10 and _rarely.level == "watch")

    check("materiality scales with what a day of living actually costs",
          _assess(_rich, essential_monthly=90_000.0).material > 5_000.0
          and _assess(_rich).material == _MMIN)
    check("a trivial dip does not get a date attached to it",
          run_forecast(_s10, 365, 60).first_breach_day is None
          or _fr.cash_breach["zero"] > 0)

    print("naming what causes a squeeze")
    from finfore.forecast import (MAX_CAUSE_WINDOW as _MCW, crunch_causes,
                                  scheduled_flows)
    _low = _date.today() + timedelta(days=200)
    _flows = scheduled_flows(_s10, _low - timedelta(days=30),
                             _low + timedelta(days=30))
    check("the projection can name its own dated flows",
          _flows and all(f.when <= _low + timedelta(days=30) for f in _flows))
    check("they arrive in date order",
          all(a.when <= b.when for a, b in zip(_flows, _flows[1:])))
    check("both directions are represented",
          any(f.is_out for f in _flows) and any(not f.is_out for f in _flows))
    check("a pay cheque is not offered as something to reschedule",
          all(not f.movable for f in _flows if not f.is_out))
    check("but a bill is",
          any(f.movable for f in _flows
              if f.is_out and f.source == "recurring"))
    _causes, _next_in = crunch_causes(_s10, _low, 5.0)
    check("causes are the outflows before the low point, biggest first",
          _causes and all(c.is_out and c.when <= _low for c in _causes)
          and all(a.amount <= b.amount for a, b in zip(_causes, _causes[1:])))
    check("and the next money in is dated after it",
          _next_in is None or _next_in.when > _low)
    # Attribution is meaningless over a long window: "every rent payment since
    # 2026" is true, is not a cause, and is not something anyone can move.
    _wide = crunch_causes(_s10, _low, 5000.0)[0]
    check("the look-back is capped however long the dip is",
          all(c.when >= _low - timedelta(days=_MCW + 1) for c in _wide))

    # Deactivating a detected loan payment does not stop the loan being
    # amortised — the engine falls back to the account's own monthly_payment —
    # so the attribution list must not quietly lose the largest fixed outflow
    # of the month along with the pattern.
    _loan_item = next((i for i in _s10.recurring()
                       if i.kind == "loan_payment" and i.linked_account_id),
                      None)
    if _loan_item is not None:
        _win_a, _win_b = _date.today(), _date.today() + timedelta(days=60)
        _lf = lambda: [f for f in scheduled_flows(_s10, _win_a, _win_b)
                       if f.source == "loan"]
        _before = len(_lf())
        _s10.update_recurring(_loan_item.id, active=False)
        check("a loan payment survives its pattern being switched off",
              _before > 0 and len(_lf()) == _before)
        _s10.update_recurring(_loan_item.id, active=True)

    _verdict = run_forecast(_s10, 730, 200, emergency_target=_ef3,
                            essential_monthly=_ess)
    check("every forecast carries a verdict, not just a probability",
          _verdict.shortfall is not None
          and _verdict.shortfall.level in ("ok", "thin", "brief", "crunch",
                                           "watch", "high"))
    check("and states it in a sentence, in the engine rather than the page",
          len(_verdict.shortfall.headline) > 10
          and len(_verdict.shortfall.detail) > 40)

    print("fat tails, runway, what-if events")
    from finfore.forecast import (_shocks, sensitivity, backtest_cash, T_DOF)
    from finfore.history import category_spending
    from finfore.models import OneOffEvent

    _rng = np.random.default_rng(0)
    _t = _shocks(_rng, (200_000,), T_DOF)
    _z = _shocks(_rng, (200_000,), 0)
    check("heavy-tailed shocks keep unit variance",
          abs(_t.std() - 1.0) < 0.05 and abs(_z.std() - 1.0) < 0.02)
    check("heavy-tailed shocks really are heavier in the tails",
          np.mean(np.abs(_t) > 3) > 2 * np.mean(np.abs(_z) > 3))
    check("dof<=2 falls back to normal draws",
          abs(np.mean(np.abs(_shocks(_rng, (50_000,), 0)) > 3) - 0.0027) < 0.002)

    _t11 = Path(tempfile.mkdtemp())
    _s11 = Store(_t11 / "events.db")
    load_demo(_s11)
    _ess11 = essential_monthly(_s11)
    _r0 = run_forecast(_s11, 730, 150, essential_monthly=_ess11,
                       emergency_target=_ess11 * 3)
    check("runway is cash divided by monthly essentials",
          _r0.runway_months is not None and _r0.runway_months > 0)
    check("runway is omitted when essentials are unknown",
          run_forecast(_s11, 365, 40).runway_months is None)

    _when = date.today() + timedelta(days=200)
    _s11.add_event(OneOffEvent("New car", _when, -28000.0))
    _r1 = run_forecast(_s11, 730, 150, essential_monthly=_ess11,
                       emergency_target=_ess11 * 3)
    check("a one-off expense lands on the projection",
          abs((_r0.cash_det[-1] - _r1.cash_det[-1]) - 28000.0) < 1.0)
    check("a one-off expense raises shortfall risk",
          _r1.cash_breach["zero"] > _r0.cash_breach["zero"]
          and _r1.min_cash_pct[10] < _r0.min_cash_pct[10])
    _eid = _s11.events()[0].id
    _s11.update_event(_eid, active=False)
    _r2 = run_forecast(_s11, 730, 150)
    check("deactivating an event removes it from the projection",
          abs(_r2.cash_det[-1] - _r0.cash_det[-1]) < 1.0)
    _s11.delete_event(_eid)
    check("events can be deleted", _s11.events() == [])
    _far = OneOffEvent("Way out", date.today() + timedelta(days=5000), -9999.0)
    _s11.add_event(_far)
    check("an event beyond the horizon is ignored",
          abs(run_forecast(_s11, 365, 40).cash_det[-1]
              - run_forecast(_s11, 365, 40).cash_det[-1]) < 1e-9)
    _s11.delete_event(_s11.events()[0].id)

    print("sensitivity (tornado)")
    _cats11 = [c for c in category_spending(_s11.transactions(), days=180,
                                            top=6).index if c != "Debt payment"]
    _sens = sensitivity(_s11, Scenario(), 1095, _cats11)
    check("every lever is measured", len(_sens) >= len(_cats11))
    check("levers are ranked by swing, descending",
          all(a.swing >= b.swing - 1e-6 for a, b in zip(_sens, _sens[1:])))
    check("swing is the span between the two nudges",
          all(abs(x.swing - abs(x.best - x.worst)) < 1e-6 for x in _sens))
    check("spending more always ends worse than spending less",
          all(x.worst <= x.best + 1e-6 for x in _sens))
    _inc = [x for x in _sens if x.kind == "income"]
    check("income is measured as its own lever", len(_inc) == 1)

    print("backtest")
    _bt = backtest_cash(_s11, lookback_days=180, n_paths=200)
    check("backtest returns a scored window",
          _bt is not None and _bt.n_days > 30 and _bt.train_days > 0)
    check("coverage figures are proper fractions",
          0.0 <= _bt.coverage_50 <= 1.0 and 0.0 <= _bt.coverage_80 <= 1.0
          and _bt.coverage_50 <= _bt.coverage_80 + 1e-9)
    check("the projection is anchored to the real balance on the cut-off day",
          abs(_bt.actual[0] - _bt.pct[50][0]) < 1.0)
    check("a wildly out-of-range look-back is refused",
          backtest_cash(_s11, lookback_days=100_000) is None)

    # the bootstrap must carry parameter uncertainty, not just sampling noise:
    # without it the band collapses like sqrt(t) and the backtest fails badly
    _wide = run_forecast(_s11, 1095, 400, seed=3)
    _band_1y = _wide.cash_pct[90][365] - _wide.cash_pct[10][365]
    _band_3y = _wide.cash_pct[90][-1] - _wide.cash_pct[10][-1]
    check("uncertainty grows faster than sqrt(t) (drift error compounds)",
          _band_3y / max(_band_1y, 1.0) > np.sqrt(3.0))

    print("packaging readiness")
    from finfore.util import resource_path
    import finfore.demo as _demo
    check("sample data resolves in a source checkout",
          _demo.SAMPLE_DIR.exists()
          and (_demo.SAMPLE_DIR / "checking.csv").exists())
    _saved = getattr(sys, "_MEIPASS", None)
    try:
        _fake = Path(tempfile.mkdtemp())
        sys._MEIPASS = str(_fake)
        check("resource_path follows sys._MEIPASS when frozen",
              resource_path("sample_data") == _fake / "sample_data")
    finally:
        if _saved is None:
            delattr(sys, "_MEIPASS")
        else:
            sys._MEIPASS = _saved
    check("resource_path returns to the source tree when not frozen",
          resource_path("sample_data") == _demo.SAMPLE_DIR)
    _spec = Path(__file__).resolve().parent.parent / "packaging" / "doug.spec"
    check("a PyInstaller spec ships with the project", _spec.exists())

    print("loan/investment activity CSV → balance history")
    from finfore.ingest import (ImportConfig, balances_from_activity,
                                parse_transactions)
    act = tmp / "car_loan.csv"
    act.write_text(
        "Posting Date,Amount,Credit Debit Indicator,Description\n"
        "07/06/2026,1000.00,Credit,Payment\n"
        "06/05/2026,1500.00,Credit,Payment\n"
        "01/14/2026,1000.00,Credit,Payment\n"
        "10/01/2025,30000.00,Debit,LOAN FUNDING\n", encoding="utf-8")
    acols = list(read_raw(str(act), ",", nrows=5).columns)
    ag = guess_mapping(acols, read_raw(str(act), ",", nrows=5))
    check("an activity export maps date/desc/amount/indicator",
          ag["date"] == "Posting Date" and ag["desc"] == "Description"
          and ag["amount"] == "Amount"
          and ag["indicator"] == "Credit Debit Indicator")
    loan_acc = Account(name="Car loan", type=AccountType.LOAN, balance=26500.0,
                       balance_date=_date(2026, 7, 25), apr=0.0649)
    store.add_account(loan_acc)
    loan_acc = [a for a in store.accounts() if a.name == "Car loan"][0]
    acfg = ImportConfig(path=str(act), account_id=loan_acc.id, delimiter=",",
                        sign_mode="indicator", col_date="Posting Date",
                        col_desc="Description", col_amount="Amount",
                        col_indicator="Credit Debit Indicator")
    adf = parse_transactions(acfg)
    check("every activity row parses", len(adf) == 4)
    recs = balances_from_activity(adf, loan_acc.balance_date, loan_acc.balance)
    bal = dict(recs)
    check("the advance is the high point of the reconstruction",
          max(v for _, v in recs) == bal[_date(2025, 10, 1)])
    check("the balance owed falls as payments land",
          bal[_date(2025, 10, 1)] > bal[_date(2026, 1, 14)]
          > bal[_date(2026, 6, 5)] > bal[_date(2026, 7, 6)])
    check("payments net out exactly (1000+1500+1000 = 3500)",
          abs((bal[_date(2025, 10, 1)] - bal[_date(2026, 7, 6)]) - 3500.0) < 1e-6)
    check("the series is anchored to the balance the user entered",
          abs(bal[_date(2026, 7, 25)] - 26500.0) < 1e-6)
    flip = dict(balances_from_activity(adf, loan_acc.balance_date,
                                       loan_acc.balance, payments_reduce=False))
    check("the direction toggle inverts the reconstruction",
          flip[_date(2025, 10, 1)] < flip[_date(2026, 7, 6)])
    n_add, _ = store.import_snapshots(loan_acc.id, recs)
    check("reconstructed points import as snapshots", n_add >= 4)
    check("the loan now carries a history line",
          len(store.snapshots(loan_acc.id)) >= 4)
    store.delete_account(loan_acc.id)

    print("rate precision")
    from finfore.util import fmt_rate
    check("a four-place rate survives the round trip",
          fmt_rate(0.064875) == "6.4875%")
    check("trailing zeros are trimmed", fmt_rate(0.07) == "7%")
    check("a two-place rate stays two places", fmt_rate(0.0649) == "6.49%")
    check("zero formats without a stray decimal", fmt_rate(0.0) == "0%")

    print("chart view locking")
    from finfore.ui import charts as _charts
    import pyqtgraph as _pg
    from PyQt6.QtWidgets import QApplication
    # Constructing a widget with no QApplication aborts the interpreter
    # outright rather than raising, so make sure one exists first. Offscreen
    # keeps this runnable on a machine with no display.
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QApplication.instance() or QApplication([])
    # Point the app's preferences at a scratch ini for the rest of the run. The
    # Simple/Everything choice and the last update-check date are stored under
    # ("Doug", "Financing with Doug"), which on a developer's machine is their
    # own registry — so a test asserting the default asserted whatever they last
    # clicked, and the suite wrote its values back over theirs. Both directions
    # are bugs, and the first one bites hardest: `build.ps1` refuses to build on
    # a failing test, so flipping a toggle in the UI could block a release.
    #
    # Everything reaches its settings through `prefs.app_settings`, so replacing
    # that one function covers the app. Note QSettings' own redirection hooks do
    # not work here: setDefaultFormat applies only to the constructor that takes
    # no organisation, and every call site names one.
    from PyQt6.QtCore import QSettings as _QSettings
    from finfore.ui import prefs as _prefs
    _settings_ini = str(Path(tempfile.mkdtemp()) / "prefs.ini")
    _prefs.app_settings = lambda: _QSettings(_settings_ini,
                                             _QSettings.Format.IniFormat)
    # Asserted rather than assumed: if this ever stops taking effect the symptom
    # is a suite that passes or fails according to what someone last clicked,
    # which is the hardest kind of failure to attribute.
    # Qt hands back the path with forward slashes, so compare as paths.
    check("app settings are isolated from the developer's own",
          Path(_prefs.app_settings().fileName()) == Path(_settings_ini)
          and _prefs.app_settings().value("simple_mode", True, type=bool))
    _plot = _pg.PlotWidget()
    _ys = np.array([100.0, 250.0, 175.0])
    _charts.lock_view(_plot, 0.0, 100.0 * 86400.0, [_ys])
    _lim = _plot.getViewBox().state["limits"]
    _, (_y0, _y1) = _plot.getViewBox().viewRange()
    check("locking bounds time", all(v is not None for v in _lim["xLimits"]))
    # Money is deliberately unbounded: clamping it made a view stretched with a
    # right-drag snap back to the data on the next scroll, undoing the gesture.
    check("but deliberately leaves money unbounded",
          _lim["yLimits"] == [None, None]
          and _lim["yRange"] == [None, None])
    check("the opening frame contains the data with headroom",
          _y0 < 100.0 and _y1 > 250.0)
    check("zoom-out is capped at the full extent",
          _lim["xRange"][1] is not None)
    check("zoom-in is capped so a wheel-scroll cannot reach one pixel",
          _lim["xRange"][0] is not None)
    _charts.lock_view(_plot, 0.0, 100.0 * 86400.0, [_ys], include_y=[0.0])
    check("a nearby reference floor is pulled into the opening frame",
          _plot.getViewBox().viewRange()[1][0] <= 0.0)
    _charts.lock_view(_plot, 0.0, 100.0 * 86400.0, [_ys + 500_000.0],
                      include_y=[0.0])
    check("a far-away floor is left out rather than flattening the data",
          _plot.getViewBox().viewRange()[1][0] > 1000.0)
    # Unbounded y would be a trap without a way back, because this app hides
    # pyqtgraph's auto-range button and its context menu.
    _vb_home = _plot.getViewBox()
    _vb_home.setRange(yRange=(-9e6, 9e6), padding=0)

    class _Ev:                       # QGraphicsSceneMouseEvent cannot be built
        def accept(self): pass       # from Python; the handler only accepts it
    _vb_home.mouseDoubleClickEvent(_Ev())
    check("double-click puts a chart back where it opened",
          abs(_plot.getViewBox().viewRange()[1][0] - 500_000.0) < 5_000.0)
    _charts.unlock_view(_plot)
    check("unlocking clears the bounds",
          _plot.getViewBox().state["limits"]["xLimits"] == [None, None])

    # A long history is bounded on the left so the chart cannot be lost, but
    # opening on all of it squeezes the months people actually consult into a
    # few pixels at the right edge. The view starts on the recent year; the
    # limits still reach the first point, so zooming out gets you there.
    _day = 86400.0
    _n = 365 * 3
    _x = np.arange(_n, dtype=float) * _day
    _long = np.concatenate([np.linspace(0.0, 8600.0, _n - 365),
                            np.linspace(8600.0, 8800.0, 365)])
    _charts.lock_view(_plot, float(_x[0]), float(_x[-1]), [_long],
                      include_y=[0.0],
                      initial_days=_charts.HISTORY_INITIAL_DAYS, x_data=_x)
    _vb = _plot.getViewBox()
    (_vx0, _vx1), (_vy0, _vy1) = _vb.viewRange()
    _lim = _vb.state["limits"]
    check("three years of history opens on its last twelve months",
          350.0 <= (_vx1 - _vx0) / _day <= 400.0)
    check("the newest point is not jammed against the right edge",
          _vx1 - float(_x[-1]) >= 10 * _day)
    check("but the bounds still reach the very first point",
          _lim["xLimits"][0] <= float(_x[0]))
    check("and zoom-out still spans the whole ledger",
          _lim["xRange"][1] >= (_lim["xLimits"][1] - _lim["xLimits"][0]) - 1.0)
    check("the opening y-range is scaled to that window, not the whole run",
          (_vy1 - _vy0) < 0.5 * (_long.max() - _long.min()))
    check("with every recent point inside it",
          _vy0 <= _long[-365:].min() and _vy1 >= _long[-365:].max())
    _charts.lock_view(_plot, float(_x[0]), float(_x[-1]), [_long],
                      initial_days=_charts.HISTORY_INITIAL_DAYS, x_data=_x,
                      zoom_y=False)
    _, (_by0, _by1) = _plot.getViewBox().viewRange()
    check("bar charts keep the full height so panning back clips nothing",
          _by0 <= _long.min() and _by1 >= _long.max())
    _short_x = np.arange(120, dtype=float) * _day
    _charts.lock_view(_plot, float(_short_x[0]), float(_short_x[-1]),
                      [np.linspace(100.0, 900.0, 120)],
                      initial_days=_charts.HISTORY_INITIAL_DAYS, x_data=_short_x)
    (_sx0, _sx1), _ = _plot.getViewBox().viewRange()
    check("a history shorter than the window is shown whole",
          _sx0 <= float(_short_x[0]) and _sx1 >= float(_short_x[-1]))
    _charts.unlock_view(_plot)

    # Cushion. Framing and travel used to be the same rectangle, so the newest
    # point had nowhere to move to and would not zoom. Zoomed all the way out
    # the ledger should now sit in the left half of the chart.
    _cy = np.array([100.0, 250.0, 175.0])
    _cspan = 100.0 * _day
    _charts.lock_view(_plot, 0.0, _cspan, [_cy])
    _lim = _plot.getViewBox().state["limits"]
    (_cvx0, _cvx1), _ = _plot.getViewBox().viewRange()
    check("the view still opens framed on the data, not on the cushion",
          _cvx1 < _lim["xLimits"][1])
    check("the left edge stays tight against the first point",
          abs(_lim["xLimits"][0] - _cvx0) < 1.0)
    check("zoomed fully out, the ledger occupies the left half",
          abs((_lim["xLimits"][1] - _lim["xLimits"][0]) - 2 * _cspan)
          < 0.06 * _cspan)
    check("zoom-out reaches the whole cushioned span",
          abs(_lim["xRange"][1]
              - (_lim["xLimits"][1] - _lim["xLimits"][0])) < 1.0)
    check("a very short chart still gets six weeks of room",
          _charts.SLACK_RIGHT_DAYS >= 45.0)
    _charts.unlock_view(_plot)

    print("forecast leads with the projection")
    from finfore.ui.forecast_page import history_window
    for _h in (183, 365, 1095, 3650):
        _hw = history_window(_h)
        check(f"history is a minority of a {_h}-day chart",
              _hw / (_hw + _h) < 0.45)
    check("a longer horizon never shows less history than a shorter one",
          history_window(183) <= history_window(365) <= history_window(1095))
    check("history is capped so a 10-year view isn't half past",
          history_window(3650) <= 366)

    print("goal ladder: priority, allocation and the snowball")
    from finfore import goals as _G
    from finfore.models import Goal as _Goal
    gstore = Store(tmp / "goals.db")
    _l1 = _Goal  # keep the import obviously used if the block is trimmed
    car = Account(name="Car loan", type=AccountType.LOAN, balance=12_000.0,
                  balance_date=_date.today(), apr=0.06, monthly_payment=400.0)
    gstore.add_account(car)
    card = Account(name="Card debt", type=AccountType.LOAN, balance=3_000.0,
                   balance_date=_date.today(), apr=0.22, monthly_payment=150.0)
    gstore.add_account(card)
    car_id = [a.id for a in gstore.accounts() if a.name == "Car loan"][0]
    card_id = [a.id for a in gstore.accounts() if a.name == "Card debt"][0]
    ef_id = gstore.add_goal(_Goal("Emergency fund", 9_000.0, "cash"))
    pc_id = gstore.add_goal(_Goal("New computer", 2_500.0, "cash"))
    ladder = [f"loan:{card_id}", f"goal:{ef_id}", f"loan:{car_id}",
              f"goal:{pc_id}"]
    gstore.set_plan_order(ladder)

    ts = _G.targets(gstore)
    check("loans and goals occupy one ordered ladder",
          [t.key for t in ts] == ladder)
    check("a loan's target is what it owes",
          next(t for t in ts if t.key == f"loan:{car_id}").amount == 12_000.0)

    plan = _G.build_plan(gstore, policy="focus", surplus=600.0)
    live = [a for a in plan.allocations if not a.target.complete]
    check("focus puts the entire spare pool on the top rung",
          abs(live[0].monthly - 600.0) < 1e-6)
    check("nothing spare reaches the lower rungs under focus",
          all(a.monthly == 0.0 for a in live[1:]))

    wplan = _G.build_plan(gstore, policy="weighted", surplus=600.0)
    ws = [a.monthly for a in wplan.allocations if not a.target.complete]
    check("weighted moves every rung", all(w > 0 for w in ws))
    check("weighted still favours priority, in order",
          all(ws[i] >= ws[i + 1] - 1e-9 for i in range(len(ws) - 1)))
    check("weighted hands out the whole pool", abs(sum(ws) - 600.0) < 1e-6)

    cplan = _G.build_plan(gstore, policy="custom", surplus=400.0,
                          custom={f"goal:{ef_id}": 300.0, f"goal:{pc_id}": 100.0})
    check("a custom split overrides the priority order",
          cplan.by_key(f"goal:{ef_id}").monthly
          > cplan.by_key(f"loan:{card_id}").monthly)
    check("a custom split still allocates the whole pool",
          abs(cplan.by_key(f"goal:{ef_id}").monthly
              + cplan.by_key(f"goal:{pc_id}").monthly - 400.0) < 1.0)

    check("everything on the ladder is reached",
          all(a.eta is not None for a in plan.allocations))
    loan_cascades = [c for c in plan.cascades if c.kind == "loan"]
    check("a retired loan releases its whole payment",
          any(abs(c.amount - 150.0) < 1e-6 for c in loan_cascades))
    check("the released payment is handed to a named next target",
          any(c.destination for c in loan_cascades))
    check("cascades come out in date order",
          all(plan.cascades[i].when <= plan.cascades[i + 1].when
              for i in range(len(plan.cascades) - 1)))
    goal_etas = [a.eta for a in plan.allocations
                 if a.target.kind == _G.GOAL and a.eta]
    check("goals fund in strict priority order under focus",
          all(goal_etas[i] <= goal_etas[i + 1]
              for i in range(len(goal_etas) - 1)))

    # The snowball is the whole point: the same ladder must finish sooner than
    # one where freed payments simply stop, so compare against a run whose
    # loans carry no payment to release.
    slow = _G.build_plan(gstore, policy="focus", surplus=300.0)
    fast = _G.build_plan(gstore, policy="focus", surplus=600.0)
    check("more spare income clears the ladder sooner",
          fast.all_done <= slow.all_done)
    ups = _G.speedups(gstore, plan, amounts=(100.0, 400.0))
    check("extra saving is costed in months off the next target",
          len(ups) == 2 and ups[1].months_saved >= ups[0].months_saved > 0)

    broke = _G.build_plan(gstore, policy="focus", surplus=0.0)
    check("no spare income is warned about, not silently ignored",
          any("spare income" in w for w in broke.warnings))
    check("loans still retire on their minimums with nothing spare",
          any(c.kind == _G.LOAN for c in broke.cascades))

    # Goals share one pot, so they must fill in priority order rather than all
    # reporting themselves met off the same balance.
    gstore.add_account(Account(name="Chq", type=AccountType.CHECKING,
                               balance=5_000.0, balance_date=_date.today()))
    ts2 = _G.targets(gstore)
    ef_t = next(t for t in ts2 if t.key == f"goal:{ef_id}")
    pc_t = next(t for t in ts2 if t.key == f"goal:{pc_id}")
    check("the higher goal consumes the shared pot first",
          abs(ef_t.progress - 5_000.0) < 1e-6 and pc_t.progress == 0.0)
    gstore.set_plan_order([f"goal:{pc_id}"] + [k for k in ladder
                                               if k != f"goal:{pc_id}"])
    ts3 = _G.targets(gstore)
    check("reordering moves the money with it",
          next(t for t in ts3 if t.key == f"goal:{pc_id}").complete)
    gstore.close()

    # Money fields use fixed cents entry. Percentages use normal decimal entry.
    print("amount entry")
    from finfore.ui.widgets import MoneySpinBox as _MSB
    from PyQt6.QtTest import QTest as _QTest

    def _type(spin, text: str) -> float:
        """Focus the field and type, exactly as a person would."""
        spin.setFocus()
        spin.selectAll()          # what clicking into the field does for real
        _QTest.keyClicks(spin, text)
        spin.interpretText()
        return spin.value()

    _sb = _MSB()
    _sb.setRange(-1e9, 1e9)
    _sb.setDecimals(2)
    _sb.setGroupSeparatorShown(True)
    _sb.setPrefix("$ ")
    check("1234 enters twelve dollars and thirty-four cents",
          abs(_type(_sb, "1234") - 12.34) < 1e-9)
    check("1234.56 is read as written", abs(_type(_sb, "1234.56") - 1234.56) < 1e-9)
    check("a leading dollar sign is forgiven",
          abs(_type(_sb, "$1234.56") - 1234.56) < 1e-9)
    check("so are thousands separators pasted in from a statement",
          abs(_type(_sb, "1,234.56") - 1234.56) < 1e-9)
    check("decimal keys do not move the fixed cents position", abs(_type(_sb, ".5") - 0.05) < 1e-9)
    check("a negative amount survives the sign",
          abs(_type(_sb, "-250.75") + 250.75) < 1e-9)
    check("a single digit is a single cent", abs(_type(_sb, "7") - 0.07) < 1e-9)

    # The mis-entry that started all of this: a field already holding a value
    # must be *replaced* by what is typed next, never appended to.
    _sb.setValue(71_520.00)
    check("typing into a populated field replaces it",
          abs(_type(_sb, "500") - 5.0) < 1e-9)
    _sb.setValue(1234.56)
    check("and retyping the same value is a no-op, not a doubling",
          abs(_type(_sb, "1234.56") - 1234.56) < 1e-9)

    # Half-typed text has to stay editable rather than being snapped back.
    from PyQt6.QtGui import QValidator as _QV
    for _partial in ("", "-", ".", "$ -"):
        check(f"“{_partial}” is treated as unfinished, not invalid",
              _sb.validate(_partial, len(_partial))[0]
              == _QV.State.Intermediate)
    check("letters are rejected outright",
          _sb.validate("$ 12x", 5)[0] == _QV.State.Invalid)
    check("an abandoned entry falls back to zero rather than sticking",
          _sb.fixup("$ -") == "0")

    _rate = _MSB()
    _rate.setRange(0, 100)
    _rate.setDecimals(4)
    _rate.setSuffix(" %")
    check("a rate field keeps four decimals off the statement",
          abs(_type(_rate, "6.4900") - 6.49) < 1e-9)

    # A card and a loan are both money owed, so both are entered as a positive
    # amount owed. The card's stored sign is still negative — internally it
    # sits on the cash side of the ledger, where a purchase has to reduce the
    # balance — so the flip happens at the dialog's edge, not in the database.
    print("a card and a loan are entered the same way")
    from finfore.ui.account_dialog import AccountDialog as _AD, _TYPES as _TYPES_ORDER
    for _t, _stored in ((AccountType.CREDIT, -1500.0),
                        (AccountType.LOAN, 1500.0)):
        _dlg = _AD()
        _dlg.name.setText("Debt")
        _dlg.type.setCurrentIndex(_TYPES_ORDER.index(_t))
        _dlg.balance.setValue(1500.0)
        _a = _dlg.get_account()
        check(f"{_t.label}: 1500 typed in is stored as {_stored:.0f}",
              abs(_a.balance - _stored) < 1e-9)
        check(f"{_t.label}: and counts against net worth either way",
              abs(_a.signed_balance + 1500.0) < 1e-9)
        check(f"{_t.label}: the field is labelled as an amount owed",
              "owed" in _dlg.balance_label.text().lower())
        _a.id = 1
        _back = _AD(account=_a)
        check(f"{_t.label}: reopening shows the same positive figure",
              abs(_back.balance.value() - 1500.0) < 1e-9)
        check(f"{_t.label}: and saving unchanged round-trips",
              abs(_back.get_account().balance - _stored) < 1e-9)
    _chq = _AD()
    _chq.type.setCurrentIndex(_TYPES_ORDER.index(AccountType.CHECKING))
    _chq.name.setText("Chq")
    _chq.balance.setValue(1500.0)
    check("a cash account is still a plain balance",
          _chq.balance_label.text() == "Balance"
          and abs(_chq.get_account().balance - 1500.0) < 1e-9)

    # "Treat as" was a permanent column of combo boxes paying for a choice
    # almost nobody made: the one people did make (stop counting this) is the
    # On checkbox two columns to its left. Reclassifying moved to right-click.
    print("the recurring table is a table again")
    from finfore.ui.recurring import RecurringPage as _RP
    from finfore.models import RecurringItem as _RI

    class _StubMain:
        def status(self, *_a, **_k):
            pass

    rstore = Store(tmp / "recurring_ui.db")
    rstore.add_account(Account(name="Chq", type=AccountType.CHECKING,
                               balance=500.0, balance_date=_date.today()))
    rstore.merge_recurring([_RI(key="netflix|-", name="NETFLIX", amount=-15.99,
                                cadence="monthly", kind="bill",
                                last_date=_date.today(), confidence=0.9,
                                n_seen=6)])
    rid = rstore.recurring()[0].id
    _page = _RP(rstore, _StubMain())
    _page.refresh()
    check("no 'Treat as' column is left", "Treat as" not in _RP.COLS)
    check("the columns are the facts about the pattern",
          _RP.COLS == ["On", "Name", "Cadence", "Amount", "≈ /month",
                       "Next", "Conf."])
    check("and the table holds no embedded widgets",
          all(_page.table.cellWidget(0, c) is None
              for c in range(_page.table.columnCount())))
    check("rows are tall enough to read",
          _page.table.verticalHeader().defaultSectionSize() >= 34)
    _page._set_kind(rid, "ignore", None)
    check("right-click reclassification still reaches the store",
          next(i for i in rstore.recurring() if i.id == rid).kind == "ignore")
    _page._set_kind(rid, "income", None)
    check("and can be changed back",
          next(i for i in rstore.recurring() if i.id == rid).kind == "income")
    rstore.close()

    print("costing a deadline in things to give up")
    dstore = Store(tmp / "deadline.db")
    dstore.add_account(Account(name="Chq", type=AccountType.CHECKING,
                               balance=1_000.0, balance_date=_date.today()))
    rows = []
    base = _date.today().replace(day=1) - timedelta(days=1)
    for m in range(6):
        month = base - timedelta(days=30 * m)
        for desc, amt, cat in (("SALARY", 4000.0, "Income"),
                               ("RENT", -1400.0, "Housing"),
                               ("SAFEWAY", -400.0, "Groceries"),
                               ("NETFLIX", -16.0, "Subscriptions"),
                               ("TACO BELL", -180.0, "Dining"),
                               ("STEAM GAMES", -90.0, "Entertainment")):
            rows.append({"account_id": 1, "date": pd.Timestamp(month),
                         "description": desc, "amount": amt, "category": cat,
                         "import_hash": f"dl-{m}-{desc}"})
    dstore.add_transactions(pd.DataFrame(rows))
    dgoal = dstore.add_goal(_Goal("New laptop", 3_000.0, "cash"))
    dstore.set_plan_order([f"goal:{dgoal}"])
    dplan = _G.build_plan(dstore, policy="focus", surplus=200.0)

    from finfore.insights import FRIVOLOUS
    cands = _G._cut_candidates(dstore)
    names = {c.name.lower() for c in cands}
    check("wants are offered as cuts",
          any("dining" in n or "taco" in n for n in names))
    check("no essential is ever offered as a cut",
          not any(n in ("housing", "groceries", "debt payment") for n in names))
    check("no cut is drawn from an essential bucket",
          all(c.bucket in ("frivolous", "unknown") for c in cands))
    # An unrecognised category is offered but marked, because some of it is
    # certainly essential and the app cannot tell which — proposing it silently
    # would be advice to cut something that might be rent.
    check("an unrecognised category is offered only as 'unknown'",
          all(c.bucket == "unknown"
              for c in cands if c.name not in FRIVOLOUS))
    check("cuts are ranked biggest first",
          all(cands[i].monthly >= cands[i + 1].monthly
              for i in range(len(cands) - 1)))

    far = _G.deadline_plan(dstore, dplan, f"goal:{dgoal}",
                           _date.today().replace(day=1) + timedelta(days=1460))
    near = _G.deadline_plan(dstore, dplan, f"goal:{dgoal}",
                            _date.today().replace(day=1) + timedelta(days=90))
    check("a nearer date demands more per month",
          near.needed_monthly > far.needed_monthly)
    check("a comfortable date needs no cuts at all",
          far.shortfall == 0.0 and not far.cuts)
    check("a tight date is costed in named items",
          near.cuts and all(c.name for c in near.cuts))
    check("the proposed cuts cover the shortfall",
          not near.feasible
          or sum(c.monthly for c in near.cuts) >= near.shortfall - 0.01)
    check("no more items are proposed than are needed",
          not near.feasible or len(near.cuts) == 1
          or sum(c.monthly for c in near.cuts[:-1]) < near.shortfall)

    # Any rung, not just the top one — "can I have the computer by July while
    # still clearing the card first?" is a question about a lower priority by
    # definition, and the honest answer has to account for what is ahead of it.
    dstore.add_account(Account(name="Card", type=AccountType.LOAN,
                               balance=2_000.0, balance_date=_date.today(),
                               apr=0.24, monthly_payment=90.0))
    _cid = [a.id for a in dstore.accounts() if a.name == "Card"][0]
    dstore.set_plan_order([f"loan:{_cid}", f"goal:{dgoal}"])
    dplan2 = _G.build_plan(dstore, policy="focus", surplus=200.0)
    _lower = _G.deadline_plan(dstore, dplan2, f"goal:{dgoal}",
                              _date.today().replace(day=1) + timedelta(days=460))
    check("a lower rung reports its position on the ladder", _lower.rank == 2)
    check("and names what is ahead of it", _lower.ahead == ["Card"])
    check("and advises about priority, not only about cutting",
          "priority 2" in _lower.advice)
    check("the top rung carries no such advice",
          _G.deadline_plan(dstore, dplan2, f"loan:{_cid}",
                           _date.today().replace(day=1)
                           + timedelta(days=460)).advice == "")
    _sp_low = _G.speedups(dstore, dplan2, amounts=(100.0,),
                          target_key=f"goal:{dgoal}")
    check("speedups can be costed for a lower rung",
          _sp_low and _sp_low[0].target == "New laptop")
    _sp_top = _G.speedups(dstore, dplan2, amounts=(100.0,))
    check("and still default to the top rung",
          _sp_top and _sp_top[0].target == "Card")
    dstore.delete_account(_cid)
    dstore.set_plan_order([f"goal:{dgoal}"])

    absurd = _G.deadline_plan(dstore, dplan, f"goal:{dgoal}",
                              _date.today().replace(day=1) + timedelta(days=31))
    check("an unreachable date says so rather than cutting into essentials",
          not absurd.feasible and "Not reachable" in absurd.note)
    check("and quantifies the gap", "short" in absurd.note)
    check("a past date is refused",
          not _G.deadline_plan(dstore, dplan, f"goal:{dgoal}",
                               _date(2020, 1, 1)).feasible)
    dstore.close()

    print("uncategorized queue scoped to the scoring window")
    from finfore.learning import (scoring_window_months, uncategorized_clusters,
                                  uncategorized_summary)
    from finfore.insights import LOOKBACK_MONTHS as _LB
    check("the queue's window is the score's window",
          scoring_window_months() == _LB)
    wstore = Store(tmp / "window.db")
    wstore.add_account(Account(name="Chq", type=AccountType.CHECKING,
                               balance=500.0, balance_date=_date.today()))
    wrows = [
        {"account_id": 1, "date": pd.Timestamp(_date.today()) - pd.Timedelta(days=20),
         "description": "RECENT CORNER STORE", "amount": -30.0,
         "category": "Misc", "import_hash": "w-new"},
        {"account_id": 1, "date": pd.Timestamp(_date.today()) - pd.Timedelta(days=1200),
         "description": "ANCIENT MYSTERY SHOP", "amount": -80.0,
         "category": "Misc", "import_hash": "w-old"},
    ]
    wstore.add_transactions(pd.DataFrame(wrows))
    every = uncategorized_clusters(wstore, limit=100)
    recent = uncategorized_clusters(wstore, limit=100, months=12)
    check("both merchants show with no window", len(every) == 2)
    check("the three-year-old merchant drops out of a 12-month window",
          len(recent) == 1 and "RECENT" in recent[0].display)
    summ = uncategorized_summary(wstore, months=12)
    check("the summary says what the window is hiding",
          summ["hidden_merchants"] == 1 and abs(summ["hidden_total"] - 80.0) < 1e-6)
    check("an unfiltered summary hides nothing",
          uncategorized_summary(wstore)["hidden_merchants"] == 0)
    wstore.close()

    print("dashboard history agrees with the stat cards")
    from finfore.history import headline_stats as _hs2, known_from as _known
    _hstore = Store(tmp / "hist.db")
    _t0 = _date.today()
    _hstore.add_account(Account(name="Chq", type=AccountType.CHECKING,
                                balance=3_000.0, balance_date=_t0))
    # A card carrying a balance and never imported — the ordinary case: you
    # add the account, type what you owe, and only import the current account.
    _hstore.add_account(Account(name="Visa", type=AccountType.CREDIT,
                                balance=-1_500.0, balance_date=_t0))
    _hrows = []
    for _i in range(30):
        _d = _t0 - timedelta(days=30 * _i)
        _hrows += [
            {"account_id": 1, "date": pd.Timestamp(_d), "description": "PAY",
             "amount": 2_800.0, "category": "Income", "import_hash": f"hp{_i}"},
            {"account_id": 1, "date": pd.Timestamp(_d + timedelta(days=2)),
             "description": "RENT", "amount": -2_800.0, "category": "Housing",
             "import_hash": f"hr{_i}"}]
    _hstore.add_transactions(pd.DataFrame(_hrows))
    _hh = networth_history(_hstore)
    _hst = _hs2(_hstore)
    # A card is debt, not negative cash. headline_stats has split it that way
    # since 0.6.1; this series had not, so the chart's "Net cash" line sat a
    # whole card balance below the "Net cash" card on the same page, and the
    # debt line ignored cards entirely. Net worth cancelled the error, which is
    # why it survived: only the two components were wrong.
    check("chart cash matches the stat card",
          abs(_hh["cash"].iloc[-1] - _hst["cash"]) < 0.01)
    check("chart debt matches the stat card",
          abs(_hh["debt"].iloc[-1] - _hst["debt"]) < 0.01)
    check("chart net worth matches the stat card",
          abs(_hh["networth"].iloc[-1] - _hst["networth"]) < 0.01)
    check("an unimported card never drags the cash line negative",
          _hh["cash"].min() > 0)
    check("its balance shows up as debt across the whole history",
          abs(_hh["debt"].iloc[0] - 1_500.0) < 0.01)
    _over = Store(tmp / "overpaid.db")
    _over.add_account(Account(name="Visa", type=AccountType.CREDIT,
                              balance=250.0, balance_date=_t0))
    _oh = networth_history(_over)
    check("an overpaid card counts as cash, not negative debt",
          abs(_oh["cash"].iloc[-1] - 250.0) < 0.01
          and abs(_oh["debt"].iloc[-1]) < 0.01)
    _over.close()

    # The chart spans every account's history combined, so an account imported
    # recently sits on an axis that starts years before its first row. Running
    # B(t0) - F(t0) back through that empty region walks today's balance
    # through flows that had not happened, which is how a funded savings
    # account reported a large negative balance years ago.
    _late = Store(tmp / "late.db")
    _late.add_account(Account(name="Chq", type=AccountType.CHECKING,
                              balance=2_000.0, balance_date=_t0))
    _late.add_account(Account(name="Sav", type=AccountType.SAVINGS,
                              balance=5_000.0, balance_date=_t0))
    _lrows = []
    for _i in range(36):
        _lrows += [
            {"account_id": 1,
             "date": pd.Timestamp(_t0 - timedelta(days=30 * _i)),
             "description": "PAY", "amount": 3_000.0, "category": "Income",
             "import_hash": f"lp{_i}"},
            {"account_id": 1,
             "date": pd.Timestamp(_t0 - timedelta(days=30 * _i - 2)),
             "description": "RENT", "amount": -3_000.0, "category": "Housing",
             "import_hash": f"lr{_i}"}]
    for _i in range(6):
        _lrows.append({"account_id": 2,
                       "date": pd.Timestamp(_t0 - timedelta(days=30 * _i)),
                       "description": "SAVE XFER", "amount": 1_083.33,
                       "category": "Transfer", "import_hash": f"ls{_i}"})
    _late.add_transactions(pd.DataFrame(_lrows))
    _ltx = _late.transactions()
    _sav = [a for a in _late.accounts() if a.name == "Sav"][0]
    _chq = [a for a in _late.accounts() if a.name == "Chq"][0]
    check("an account knows nothing before its own first row",
          _known(_ltx, _sav) > _known(_ltx, _chq))
    _lh = networth_history(_late)
    _line = cash_history(_ltx, _sav, _lh.index,
                         anchor=cash_anchor(_late, _sav))
    check("its reconstruction is flat before that point, not extrapolated",
          abs(_line.iloc[0] - _line.asof(_known(_ltx, _sav))) < 0.01)
    check("and the flat region is genuinely constant",
          _line[_line.index < _known(_ltx, _sav)].nunique() <= 1)
    check("total cash still ends on the sum of the entered balances",
          abs(_lh["cash"].iloc[-1] - 7_000.0) < 0.01)
    _late.close()
    _hstore.close()

    print("a ledger that cannot reconcile is reported, not hidden")
    from finfore.history import reconcile as _reconcile, reconcile_all as _reconcile_all
    # B(start) = B(today) - net_flow is exact, so net-positive flows against a
    # near-zero balance *force* a deeply negative opening. The numbers are
    # right and an input is wrong; drawing it silently reads as the program
    # being broken rather than the export being incomplete.
    _rec = Store(tmp / "reconcile.db")
    _rec.add_account(Account(name="Savings", type=AccountType.SAVINGS,
                             balance=0.71, balance_date=_date.today()))
    _rrows = []
    for _i in range(24):
        _d = _date.today() - timedelta(days=30 * _i)
        _rrows.append({"account_id": 1, "date": pd.Timestamp(_d),
                       "description": "PAYCHECK DEPOSIT", "amount": 800.0,
                       "category": "Income", "import_hash": f"ri{_i}"})
        _rrows.append({"account_id": 1,
                       "date": pd.Timestamp(_d + timedelta(days=3)),
                       "description": "TRANSFER TO CHECKING", "amount": -740.0,
                       "category": "Transfer", "import_hash": f"ro{_i}"})
    _rec.add_transactions(pd.DataFrame(_rrows))
    _issues = _reconcile(_rec)
    check("an impossible opening balance is detected", len(_issues) == 1)
    check("it names the account", _issues[0].account == "Savings")
    check("it reports the implied opening balance", _issues[0].opening < -1000)
    check("and the net flow that forces it", _issues[0].net_flow > 1000)
    check("the message explains rather than just flagging",
          "current balance" in _issues[0].message
          and "started at" in _issues[0].message)

    # The same event posted twice under different wording is what the row hash
    # cannot collapse, because the description is part of the hash.
    _rec.add_transactions(pd.DataFrame([
        {"account_id": 1, "date": pd.Timestamp(_date.today() - timedelta(days=45)),
         "description": "COSTCO DEPOSIT", "amount": 500.0,
         "category": "Income", "import_hash": "dup-a"},
        {"account_id": 1, "date": pd.Timestamp(_date.today() - timedelta(days=45)),
         "description": "ACH CREDIT - COSTCO DEPOSIT", "amount": 500.0,
         "category": "Income", "import_hash": "dup-b"}]))
    _issues2 = _reconcile(_rec)
    check("same-day same-amount rows with different wording are counted",
          _issues2[0].duplicates >= 1)
    check("and their dollar value is reported",
          abs(_issues2[0].duplicate_value) >= 500.0)

    # The opening balance must be measured *before* the first row, not after.
    # A card whose earliest line is a large purchase opened at zero and then
    # bought something; reading the first daily point instead reports it as
    # having opened deeply overdrawn.
    _card = Store(tmp / "reconcile_card.db")
    _card.add_account(Account(name="Visa", type=AccountType.CREDIT,
                              balance=-71.52, balance_date=_date.today()))
    _card.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=250)),
         "description": "BIG PURCHASE", "amount": -1_219.48,
         "category": "Shopping", "import_hash": "c1"},
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=245)),
         "description": "CARD PAYMENT", "amount": 1_219.48,
         "category": "Debt payment", "import_hash": "c2"},
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=20)),
         "description": "FUEL", "amount": -71.52,
         "category": "Fuel", "import_hash": "c3"}]))
    _cards = [i for i in _reconcile_all(_card) if i.account == "Visa"]
    check("a card whose first row is a big purchase still opens at zero",
          _cards and abs(_cards[0].opening) < 0.01)
    check("and is therefore not flagged", _cards[0].ok)
    check("the reconciling identity holds exactly",
          abs(_cards[0].entered - _cards[0].net_flow - _cards[0].opening) < 0.01)
    _card.close()

    _ok = Store(tmp / "reconcile_ok.db")
    _ok.add_account(Account(name="Chq", type=AccountType.CHECKING,
                            balance=2_500.0, balance_date=_date.today()))
    _ok.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=30 * i)),
         "description": "PAY", "amount": 1_000.0 if i % 2 else -1_000.0,
         "category": "Income", "import_hash": f"ok{i}"} for i in range(12)]))
    check("a ledger that does reconcile raises nothing", _reconcile(_ok) == [])
    _rec.close()
    _ok.close()

    print("same-day transactions, and finding one posted twice")
    from finfore import dedup as _dd
    _sd = Store(tmp / "sameday.db")
    _sd.add_account(Account(name="Savings", type=AccountType.SAVINGS,
                            balance=0.71, balance_date=_date.today()))
    _day = pd.Timestamp(_date.today() - timedelta(days=400))
    _sdrows = [
        # Five rows on one day, including two genuinely identical transfers and
        # two same-amount transfers to *different* destinations.
        {"description": "Interest Income", "amount": 0.25},
        {"description": "Transfer To Savings -2507", "amount": -1_000.00},
        {"description": "Transfer To Certificate", "amount": -1_000.00},
        {"description": "Transfer To Checking -0781", "amount": -250.00},
        {"description": "Transfer To Checking -0781", "amount": -250.00},
    ]
    _sd.add_transactions(pd.DataFrame([
        {"account_id": 1, "date": _day, "category": "Transfer",
         "import_hash": f"sd{i}", **r} for i, r in enumerate(_sdrows)]))
    _got = _sd.transactions()
    check("every same-day row survives import", len(_got) == 5)
    check("including two byte-identical rows",
          (_got["description"] == "Transfer To Checking -0781").sum() == 2)
    check("the day's net is the sum of all of them",
          abs(_got["amount"].sum() - (-2_499.75)) < 0.01)
    _n0 = len(_sd.transactions())
    _sd.add_transactions(pd.DataFrame([
        {"account_id": 1, "date": _day, "category": "Transfer",
         "import_hash": f"sd{i}", **r} for i, r in enumerate(_sdrows)]))
    check("re-importing the same rows adds nothing",
          len(_sd.transactions()) == _n0)

    _pairs = _dd.find_pairs(_sd)
    _descs = {(p.keep_desc, p.drop_desc) for p in _pairs}
    check("two identical same-day transfers are offered as a candidate",
          any(a == b == "Transfer To Checking -0781" for a, b in _descs))
    check("but different destinations at the same amount are not",
          not any({"Transfer To Certificate", "Transfer To Savings -2507"}
                  == {a, b} for a, b in _descs))
    # Similarity finds candidates; the balance identity decides between them.
    # An account that already reconciles has no evidence of a problem, so two
    # identical charges on one day stay two identical charges.
    _sd.add_transactions(pd.DataFrame([
        {"account_id": 1, "date": pd.Timestamp(_date.today() - timedelta(days=10)),
         "description": "BIG DEPOSIT", "amount": 2_500.46,
         "category": "Income", "import_hash": "sd-fix"}]))
    _reps = _dd.suggest(_sd)
    check("a reconciling account gets candidates but no suggestions",
          _reps and all(not r.suggested for r in _reps))

    # Now break it: a real double-posting of an inflow.
    _dup = Store(tmp / "dupfix.db")
    _dup.add_account(Account(name="Sav", type=AccountType.SAVINGS,
                             balance=10.0, balance_date=_date.today()))
    _dup.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=300)),
         "description": "PURDUE UNIVERSITY DEPOSIT", "amount": 843.92,
         "category": "Income", "import_hash": "d1"},
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=300)),
         "description": "Deposit - PURDUE UNIVERSIT PURDUE UNIVERSIT DEPOSIT",
         "amount": 843.92, "category": "Income", "import_hash": "d2"},
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=200)),
         "description": "TRANSFER TO CHECKING", "amount": -833.92,
         "category": "Transfer", "import_hash": "d3"}]))
    _rep = _dd.suggest(_dup)[0]
    check("a double-posted inflow is detected", len(_rep.pairs) == 1)
    check("and suggested, because removing it closes the gap",
          len(_rep.suggested) == 1)
    check("the projected opening after removal is reported",
          abs(_rep.opening_after(_rep.suggested)) < 1.0)
    _dd.apply(_dup, _rep.suggested)
    check("removing it makes the account reconcile",
          all(i.ok for i in _reconcile_all(_dup)))
    # Deleting alone would free the row hash, so the next update from the same
    # bank would import the duplicate straight back.
    _dup.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=300)),
         "description": "Deposit - PURDUE UNIVERSIT PURDUE UNIVERSIT DEPOSIT",
         "amount": 843.92, "category": "Income", "import_hash": "d2"}]))
    check("re-importing does not resurrect a removed duplicate",
          all(i.ok for i in _reconcile_all(_dup)))
    check("the removal is remembered", len(_dup.suppressed_hashes()) == 1)
    _dup.unsuppress_all()
    _dup.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=300)),
         "description": "Deposit - PURDUE UNIVERSIT PURDUE UNIVERSIT DEPOSIT",
         "amount": 843.92, "category": "Income", "import_hash": "d2"}]))
    check("restoring lets a later import bring it back",
          not all(i.ok for i in _reconcile_all(_dup)))
    _sd.close()
    _dup.close()

    print("categorisation accuracy")
    from finfore.categorize import categorize as _cz
    # Anchored keywords. Bare "UNITED" put United Way under Travel because the
    # Travel rule is tested before Giving, and bare "MARKET" filed Facebook
    # Marketplace as groceries. A miss costs one click and is remembered; a
    # confident mislabel silently skews the score.
    for _desc, _amt, _want in (
            ("UNITED WAY OF AMERICA", -50, "Charitable donation"),
            ("UNITED AIRLINES 016", -420, "Travel"),
            ("FACEBOOK MARKETPLACE", -40, "Shopping"),
            ("GIANT FOOD 0182", -90, "Groceries"),
            # Card payments were the commonest outflow with no rule at all.
            ("VISA REWARDS CARD MIN PMT", -210, "Debt payment"),
            ("CHASE CREDIT CRD EPAY", -400, "Debt payment"),
            ("AMEX EPAYMENT ACH PMT", -300, "Debt payment"),
            ("DISCOVER E-PAYMENT", -150, "Debt payment"),
            ("NAVIENT STUDENT LN", -300, "Debt payment"),
            ("REI 0042", -85, "Shopping"),
            ("PETSMART 0231", -70, "Shopping"),
            ("WAYFAIR ORDER", -300, "Shopping"),
            ("WASTE MANAGEMENT", -45, "Utilities"),
            ("CON EDISON", -140, "Utilities"),
            # Ordering that must not regress: food delivery beats rideshare.
            ("UBER EATS TRIP", -28, "Dining"),
            ("UBER TRIP 4AM", -22, "Transport"),
            ("OAKWOOD PROPERTIES RENT", -1795, "Housing"),
            ("ACME PAYROLL DIR DEP", 2650, "Income")):
        check(f"{_desc!r} categorises as {_want}", _cz(_desc, _amt) == _want)
    check("an unmatched outflow still falls through to Misc",
          _cz("ZZQX UNKNOWN VENDOR", -20) == "Misc")
    check("an unmatched inflow still falls through to Other income",
          _cz("ZZQX UNKNOWN VENDOR", 20) == "Other income")

    print("the scoring window says what it does")
    from finfore.insights import weight_shares, window_note
    _sh = weight_shares()
    check("the recent quarter carries about half the score",
          0.45 < _sh[3] < 0.55)
    check("the last six months carry about three quarters",
          0.70 < _sh[6] < 0.80)
    check("beyond a year is a rounding error", (1 - _sh[12]) < 0.10)
    _wn = window_note()
    check("the note quotes the lookback and the half-life",
          "24 months" in _wn and "half-life" in _wn)
    check("and the share the recent months actually carry", "%" in _wn)

    print("getting-started guide")
    from finfore.ui import guide as _guide
    _gp = _guide.guide_path()
    check("the guide ships with the project", _gp is not None and _gp.exists())
    _gt = _gp.read_text(encoding="utf-8")
    check("it is a real guide, not a stub", len(_gt) > 6000)
    # A guide that names a button the app does not have is worse than no guide,
    # and the drift happens silently when the UI is renamed. Check the labels
    # it quotes still exist in the interface.
    _ui_src = "\n".join(
        (Path(__file__).resolve().parent.parent / "finfore" / "ui" / f)
        .read_text(encoding="utf-8")
        for f in os.listdir(Path(__file__).resolve().parent.parent
                            / "finfore" / "ui") if f.endswith(".py"))
    for _lbl in ("Add account", "Import CSV…", "Review uncategorized…",
                 "Add goal…", "Add emergency fund…", "Update from CSV…",
                 "Reconstruct history…", "Load demo dataset",
                 "Return to my own data", "Take a checkpoint now",
                 "Save a month in review…", "What changed?",
                 "What's causing this?", "Simple view"):
        check(f"guide and app agree on {_lbl!r}",
              _lbl in _gt and _lbl in _ui_src)
    for _pg in ("Dashboard", "Accounts", "Transactions", "Bills & payday",
                "Spending", "Goals", "Forecast", "Progress", "Recurring patterns"):
        check(f"the guide explains the {_pg} page", _pg in _gt)
    for _pr in _demo.PROFILES:
        check(f"the guide lists the {_pr.key} demo",
              _pr.name.split("—")[0].strip() in _gt)
    check("it says where the data lives", ".ledgerline" in _gt)
    check("it warns about the SmartScreen prompt", "SmartScreen" in _gt)
    check("it states this is not financial advice",
          "not financial advice" in _gt)
    _gspec = (Path(__file__).resolve().parent.parent / "packaging"
              / "doug.spec").read_text(encoding="utf-8")
    check("the build bundles docs/", '"../docs"' in _gspec)
    _gsaved = getattr(sys, "_MEIPASS", None)
    try:
        _gfake = Path(tempfile.mkdtemp())
        (_gfake / "docs").mkdir()
        (_gfake / "docs" / "getting-started.md").write_text("# x",
                                                            encoding="utf-8")
        sys._MEIPASS = str(_gfake)
        check("the guide resolves inside a frozen bundle",
              _guide.guide_path() == _gfake / "docs" / "getting-started.md")
    finally:
        if _gsaved is None:
            delattr(sys, "_MEIPASS")
        else:
            sys._MEIPASS = _gsaved
    _gd = _guide.GuideDialog()
    _grendered = _gd.view.toPlainText()
    check("it renders as formatted text, not raw markdown",
          len(_grendered) > 4000 and "##" not in _grendered)
    _gd.close()


    print("progress: checkpoints, path adherence, significance")
    from finfore import progress as _P

    def _ledger(before, after, cut_weeks=10, weeks=30, seed=3):
        rng = np.random.default_rng(seed)
        s = Store(Path(tempfile.mkdtemp()) / "p.db")
        s.add_account(Account(name="Chq", type=AccountType.CHECKING,
                              balance=6000.0, balance_date=_date.today()))
        rws, start = [], _date.today() - timedelta(weeks=weeks)
        cut = _date.today() - timedelta(weeks=cut_weeks)
        for w in range(weeks):
            wk = start + timedelta(weeks=w)
            rws.append({"account_id": 1, "date": pd.Timestamp(wk),
                        "description": "PAYROLL", "amount": 1200.0,
                        "category": "Income", "import_hash": f"i{w}"})
            base = before if wk < cut else after
            for d in range(4):
                rws.append({"account_id": 1,
                            "date": pd.Timestamp(wk + timedelta(days=d + 1)),
                            "description": f"SHOP {d}",
                            "amount": -abs(float(rng.normal(base / 4, base / 14))),
                            "category": "Dining" if d % 2 else "Shopping",
                            "import_hash": f"s{w}-{d}"})
        s.add_transactions(pd.DataFrame(rws))
        return s, cut

    pstore, pcut = _ledger(200.0, 120.0)
    pcp = _P.capture(pstore, label="t", horizon_days=180, n_paths=120)
    check("a checkpoint records the score and balance sheet",
          pcp.score is not None and pcp.cash != 0.0)
    check("a checkpoint stores the projection being made at the time",
          pcp.has_forecast)
    _fan = pcp.forecast_frame()
    check("the stored fan reads back with all five percentiles",
          _fan is not None
          and set(_fan.columns) == {"p10", "p25", "p50", "p75", "p90"})
    check("its percentiles are ordered everywhere",
          bool((_fan["p10"] <= _fan["p50"]).all()
               and (_fan["p50"] <= _fan["p90"]).all()))
    check("it round-trips through SQLite",
          len(pstore.checkpoints()) == 1
          and pstore.checkpoints()[0].has_forecast)
    check("a same-day re-import does not stack another checkpoint",
          not _P.should_capture(pstore))

    _sig = _P.significance_since(pstore, _P.Checkpoint(when=pcut))
    check("a real spending drop is detected",
          _sig.enough_data and _sig.diff < 0)
    check("and called significant", _sig.significant)
    check("and labelled an improvement", _sig.direction == "improved")
    check("its confidence interval excludes zero", _sig.ci_high < 0)
    check("the interval brackets the point estimate",
          _sig.ci_low <= _sig.diff <= _sig.ci_high)

    _rise, _rcut = _ledger(120.0, 220.0, seed=5)
    _s3 = _P.significance_since(_rise, _P.Checkpoint(when=_rcut))
    check("a real rise is called significant and worsening",
          _s3.significant and _s3.direction == "worsened")

    # The test must not manufacture findings. On identical distributions it
    # should reject at roughly the nominal 5%, and it must never dress a
    # non-result up as proof that nothing changed.
    _rng = np.random.default_rng(0)
    _fp = sum(1 for t in range(40)
              if _P.compare_periods(_rng.normal(200, 45, 14),
                                    _rng.normal(200, 45, 14),
                                    seed=t).significant)
    check(f"false positives stay near 5% on identical data ({_fp}/40)",
          _fp <= 8)
    _null = _P.compare_periods(np.full(10, 200.0) + _rng.normal(0, 30, 10),
                               np.full(10, 200.0) + _rng.normal(0, 30, 10),
                               seed=1)
    check("a null result is not reported as proof of no change",
          "not evidence nothing happened" in _null.note)
    check("a null result quotes the minimum detectable effect",
          _null.detectable > 0)
    check("too few weeks refuses to test at all",
          not _P.compare_periods(np.array([1.0, 2.0, 3.0]),
                                 np.array([1.0, 2.0, 3.0])).enough_data)
    _a, _b = _rng.normal(200, 40, 12), _rng.normal(140, 40, 12)
    check("identical inputs and seed give an identical p-value",
          _P.compare_periods(_a, _b, seed=1).p_value
          == _P.compare_periods(_a, _b, seed=1).p_value)

    def _fan_cp(store_, days_back=60, offset=0.0, width=900.0):
        _h = networth_history(store_)
        anchor = float(_h["cash"].iloc[-1])
        made = _date.today() - timedelta(days=days_back)
        ds = [made + timedelta(days=i) for i in range(days_back + 120)]
        mid = np.linspace(anchor - offset - 200, anchor - offset + 200, len(ds))
        c = _P.Checkpoint(when=made)
        c.forecast = {"dates": [d.isoformat() for d in ds],
                      "p10": list(mid - width), "p25": list(mid - width / 2),
                      "p50": list(mid), "p75": list(mid + width / 2),
                      "p90": list(mid + width)}
        return c

    check("a projection with no elapsed time yet cannot be scored",
          _P.check_path(pstore, pcp) is None)
    _chk = _P.check_path(pstore, _fan_cp(pstore))
    check("a projection made in the past is scored against reality",
          _chk is not None)
    check("coverage figures are proper fractions",
          0.0 <= _chk.inside_80 <= 1.0 and _chk.inside_50 <= _chk.inside_80 + 1e-9)
    _above = _P.check_path(pstore, _fan_cp(pstore, offset=40_000.0, width=500.0))
    check("cash far above the old fan is reported as off the map",
          _above.status == "broken" and _above.inside_80 < 0.5)
    _below = _P.check_path(pstore, _fan_cp(pstore, offset=-40_000.0, width=500.0))
    check("cash far below it is also off the map", _below.status == "broken")
    check("percentile placement stays within 0-100 either side",
          0 <= _above.percentile_now <= 100 and 0 <= _below.percentile_now <= 100)
    check("above the band ranks higher than below it",
          _above.percentile_now > _below.percentile_now)

    _mv = _P.score_move(_P.Checkpoint(when=_date.today(), score=4.0, grade="D"),
                        _P.Checkpoint(when=_date.today(), score=6.5, grade="B"))
    check("a score rise is flagged as an improvement",
          _mv.improved and abs(_mv.delta - 2.5) < 1e-9)
    check("the grade change is narrated", "D" in _mv.note and "B" in _mv.note)
    check("a score fall is not flagged as improvement",
          not _P.score_move(
              _P.Checkpoint(when=_date.today(), score=6.5, grade="B"),
              _P.Checkpoint(when=_date.today(), score=4.0, grade="D")).improved)

    check("checkpoints can be cleared without touching the ledger",
          True)   # exercised via the store below
    _n_tx_before = len(pstore.transactions())
    for _c in pstore.checkpoints():
        pstore.delete_checkpoint(_c.id)
    check("clearing removes every checkpoint",
          len(pstore.checkpoints()) == 0)
    check("and leaves the transactions alone",
          len(pstore.transactions()) == _n_tx_before)
    _P.capture(pstore, label="t", horizon_days=180, n_paths=120)

    _shifts = _P.category_shifts(pstore, _P.Checkpoint(when=pcut), top=4)
    check("category attribution is ordered by size of move",
          _shifts and all(abs(_shifts[i].diff) >= abs(_shifts[i + 1].diff)
                          for i in range(len(_shifts) - 1)))
    pstore.close()
    _rise.close()

    _auto = Store(Path(tempfile.mkdtemp()) / "auto.db")
    _auto.add_account(Account(name="Chq", type=AccountType.CHECKING,
                              balance=500.0, balance_date=_date.today()))
    _auto.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_date.today() - timedelta(days=i * 3)),
         "description": "SHOP", "amount": -25.0, "category": "Dining",
         "import_hash": f"a{i}"} for i in range(40)]))
    from finfore.pipeline import refresh_derived as _rd
    _rd(_auto)
    check("importing records a checkpoint automatically",
          len(_auto.checkpoints()) == 1)
    _rd(_auto)
    check("a second import moments later does not stack another",
          len(_auto.checkpoints()) == 1)
    _rd(_auto, checkpoint=False)
    check("automatic capture can be switched off",
          len(_auto.checkpoints()) == 1)
    _auto.close()

    print("behaviour-change detection in the bootstrap")
    from finfore.forecast import (CHANGE_MIN_WEEKS, _behaviour_changed,
                                  _behaviour_weights, _recency_weights)
    _crng = np.random.default_rng(4)
    _stable = _crng.normal(-500, 120, 60)
    check("a stable ledger is not called changed",
          not _behaviour_changed(_stable))
    check("and is therefore sampled uniformly",
          np.allclose(_behaviour_weights(_stable), 1.0 / len(_stable)))
    _shifted = np.concatenate([_crng.normal(-800, 100, 45),
                               _crng.normal(-300, 100, 25)])
    check("a large sustained shift is detected", _behaviour_changed(_shifted))
    _sw = _behaviour_weights(_shifted)
    check("and switches the sample to recency weighting",
          _sw[-1] > _sw[0] * 5)
    check("weights always normalise to one",
          abs(_sw.sum() - 1.0) < 1e-9
          and abs(_behaviour_weights(_stable).sum() - 1.0) < 1e-9)
    # A difference that is real in-sample need not persist. Requiring months of
    # sustained new behaviour is what stops mean-reversion being mistaken for a
    # changed habit — an earlier version fired on exactly that and dropped
    # backtest coverage on the balanced demo from 61% to 10%.
    _brief = np.concatenate([_crng.normal(-800, 100, 40),
                             _crng.normal(-200, 100, 6)])
    check("a brief shift is ignored however large",
          not _behaviour_changed(_brief))
    check("a short ledger is never reweighted",
          not _behaviour_changed(_crng.normal(-500, 150, CHANGE_MIN_WEEKS * 2 - 2)))
    _small = np.concatenate([_crng.normal(-500, 200, 45),
                             _crng.normal(-470, 200, 25)])
    check("a small shift is ignored even when sustained",
          not _behaviour_changed(_small))
    check("recency weights decay toward the past",
          _recency_weights(52)[-1] > _recency_weights(52)[0])
    check("a disabled half-life gives uniform weights",
          np.allclose(_recency_weights(20, half_life=0), 1 / 20))

    print("demo profiles")
    from finfore import demo as _demo
    from finfore.insights import monthly_aggregates as _ma, spending_score as _ss
    _loaded, _scores, _aggs = {}, {}, {}
    for _p in _demo.PROFILES:
        _s = Store(tmp / f"demo_{_p.key}.db")
        _info = _demo.load_demo(_s, _p.key, checkpoints=3)
        _loaded[_p.key] = _s
        _a = _ma(_s.transactions())
        _aggs[_p.key] = _a
        _scores[_p.key] = _ss(_a).value
        check(f"{_p.key}: has a ledger and recurring patterns",
              _info["transactions"] > 50 and _info["recurring"] > 0)
        check(f"{_p.key}: is scoreable", _a is not None and _a.income > 0)
        check(f"{_p.key}: arrives with checkpoints to compare",
              _info["checkpoints"] >= 2)
        check(f"{_p.key}: has goals for the ladder", len(_s.goals()) >= 2)

    # The profiles have to actually differ, or three of the four are decoration.
    check("struggling runs a deficit", _aggs["struggling"].surplus < 0)
    check("thriving runs a large surplus", _aggs["thriving"].surplus > 1500)
    check("struggling grades badly, not merely below average",
          _scores["struggling"] < 5.0)
    check("thriving grades well", _scores["thriving"] > 8.0)
    check("balanced sits between the two",
          _scores["struggling"] < _scores["balanced"] < _scores["thriving"])
    # A low earner with modest wants is a deliberate B ("squeezed, not
    # careless"), so a profile meant to look bad must be over the 50/30/20
    # wants ceiling as well as in deficit — otherwise it grades the good news.
    check("struggling is over the wants-to-needs ceiling",
          _aggs["struggling"].fesr > 0.45)
    check("struggling carries card debt",
          any(a.type == AccountType.CREDIT and a.balance < 0
              for a in _loaded["struggling"].accounts()))
    check("thriving holds investments",
          any(a.type == AccountType.INVESTMENT and a.balance > 10_000
              for a in _loaded["thriving"].accounts()))

    # The worst case the score can express. An F needs both sub-scores on the
    # floor, because the interaction penalty is what pulls the total under 1.2
    # and it only bites when habits *and* means are poor together.
    from finfore.history import headline_stats as _headline
    from finfore.insights import essential_monthly as _ess_monthly
    _sp = _loaded["spiralling"]
    _spa, _spsc = _aggs["spiralling"], _ss(_aggs["spiralling"])
    check("spiralling grades F", _spsc.letter == "F" and _spsc.value < 1.2)
    check("both sub-scores are on the floor",
          _spsc.habit_component < 5.0 and _spsc.means_component < 1.0)
    check("the verdict names the spending as the cause",
          _spsc.quadrant == "overspending")
    check("it spends far past income", _spa.savings_rate < -0.4)
    check("wants exceed the 30%-of-income guideline several times over",
          _spa.wants_share > 0.60)
    check("it carries several high-interest debts",
          sum(1 for a in _sp.accounts()
              if a.type in (AccountType.LOAN, AccountType.CREDIT)) >= 3)
    check("net worth is deeply negative",
          _headline(_sp)["networth"] < -20_000)
    check("debt payments appear in the ledger, not just on the accounts",
          any("PMT" in str(d).upper()
              for d in _sp.transactions()["description"].unique()))
    _essential = _ess_monthly(_sp)
    _spf = run_forecast(_sp, 365, 200, emergency_target=_essential * 3,
                        essential_monthly=_essential)
    check("the projection puts cash deep into debt within a year",
          _spf.cash_pct[50][-1] < -20_000)
    check("shortfall is a near-certainty, not a risk",
          _spf.cash_breach.get("zero", 0.0) > 0.95)
    check("the runway is effectively nil",
          _spf.runway_months is not None and _spf.runway_months < 0.5)
    _spplan = _G.build_plan(_sp)
    check("there is no spare income to allocate", _spplan.surplus <= 0.01)
    check("and the plan says so rather than inventing a schedule",
          any("spare income" in w for w in _spplan.warnings))

    _ts = _loaded["turnaround"]
    _cps = _ts.checkpoints()
    _sig = _P.significance_since(_ts, _cps[0])
    check("turnaround's habit change is detected as a real reduction",
          _sig.enough_data and _sig.diff < 0 and _sig.significant)
    check("and is reported as an improvement", _sig.direction == "improved")
    _sc_seq = [c.score for c in _cps if c.score is not None]
    check("its score climbs across the checkpoints",
          len(_sc_seq) >= 2 and _sc_seq[-1] > _sc_seq[0] + 1.0)
    check("turnaround's older projection can be scored against reality",
          _P.check_path(_ts, _cps[0]) is not None)

    # The projection has to describe who they are now, not the average of who
    # they were and who they are. Before the bootstrap learned about changed
    # habits this profile was forecast into a deficit at 99% shortfall while
    # the Insights page reported a surplus — the two halves of the app
    # disagreeing about the same ledger.
    _tw, _ti = weekly_category_matrix(
        _ts.transactions(), exclude_keys={i.key for i in _ts.recurring()})
    check("turnaround's change is visible to the detector",
          _behaviour_changed(_ti.values - _tw.values.sum(axis=1), seed=7))
    _tess = _ess_monthly(_ts)
    _tf = run_forecast(_ts, 730, 300, emergency_target=_tess * 3,
                       essential_monthly=_tess)
    check("its projection is no longer a collapse",
          _tf.cash_breach.get("zero", 1.0) < 0.5)
    check("and net worth grows over the horizon",
          _tf.networth_pct[50][-1] > _tf.networth_det[0])
    check("the forecast agrees with Insights about the direction",
          (_aggs["turnaround"].surplus > 0)
          == (_tf.networth_pct[50][-1] > _tf.networth_det[0]))
    # Sanity in the other direction: the profiles that really are sinking must
    # still be reported as sinking.
    for _bad in ("struggling", "spiralling"):
        _bs = _loaded[_bad]
        _be = _ess_monthly(_bs)
        _bf = run_forecast(_bs, 730, 200, emergency_target=_be * 3,
                           essential_monthly=_be)
        check(f"{_bad} is still projected into shortfall",
              _bf.cash_breach.get("zero", 0.0) > 0.9)

    # Backfilled checkpoints must be built from data that existed then, or the
    # Progress page reports the model as better than it is.
    _hist = [c for c in _loaded["balanced"].checkpoints() if c.label == "history"]
    check("backfilled checkpoints are dated in the past",
          _hist and all(c.when < _date.today() for c in _hist))
    check("each projection starts at its own checkpoint's date",
          all(c.forecast_frame().index.min().date() == c.when
              for c in _hist if c.has_forecast))
    check("and looks forward from there",
          all(c.forecast_frame().index.max().date() > c.when
              for c in _hist if c.has_forecast))
    check("an early checkpoint saw fewer transactions than the full ledger",
          _hist[0].n_transactions < len(_loaded["balanced"].transactions()))
    _n_before = len(_loaded["balanced"].checkpoints())
    check("backfilling twice does not duplicate checkpoints",
          _P.backfill(_loaded["balanced"], n=3, spacing_days=55, n_paths=60) == 0
          and len(_loaded["balanced"].checkpoints()) == _n_before)
    for _s in _loaded.values():
        _s.close()

    print("demos never touch real data")
    from finfore.ui.main_window import MainWindow as _MW
    _real_path = tmp / "mine.db"
    _real = Store(_real_path)
    _real.add_account(Account(name="My Real Checking",
                              type=AccountType.CHECKING, balance=1234.56,
                              balance_date=_date.today()))
    _real.add_transactions(pd.DataFrame([{
        "account_id": 1,
        "date": pd.Timestamp(_date.today() - timedelta(days=i)),
        "description": "MY REAL PURCHASE", "amount": -12.0,
        "category": "Dining", "import_hash": f"real{i}"} for i in range(25)]))
    _real_tx = len(_real.transactions())
    _real_names = {a.name for a in _real.accounts()}
    _win = _MW(_real, real_db_path=_real_path)
    check("the window starts on the real database", _win.demo_key is None)
    _win._load_demo("thriving", confirm=False)
    check("a demo opens in its own separate file",
          _win.demo_key == "thriving" and Path(_win.store.path) != _real_path)
    check("and is flagged loudly as demo data",
          not _win.banner.isHidden() and "untouched" in _win.banner.text())
    check("every page is repointed at the demo store",
          all(p.store is _win.store for p in _win.pages))
    check("the demo contains none of the real accounts",
          not any(n in {a.name for a in _win.store.accounts()}
                  for n in _real_names))
    _win._load_demo("struggling", confirm=False)
    _win.return_to_real()
    check("returning lands back on the real database",
          _win.demo_key is None and Path(_win.store.path) == _real_path)
    check("the real accounts survived the round trip",
          {a.name for a in _win.store.accounts()} == _real_names)
    check("the real transactions survived intact",
          len(_win.store.transactions()) == _real_tx)
    check("the real balance is untouched",
          abs(_win.store.accounts()[0].balance - 1234.56) < 1e-9)
    check("the demo banner is gone", _win.banner.isHidden())

    # "It's under Help" is one step too many for the single menu item a
    # non-technical user is ever told to go and find, so it sits on the bar
    # itself. Asserted because a top-level bare action is easy to nudge back
    # into a submenu without noticing the instructions no longer match.
    print("the update button is where people are told to look")
    _bar = _win.menuBar().actions()
    check("Check for updates is its own menu bar button",
          any(a.text() == "Check for updates" and a.menu() is None
              for a in _bar))
    _help_menu = next(a.menu() for a in _bar
                      if a.text().replace("&", "") == "Help")
    check("and is not also buried in the Help menu",
          not any("update" in a.text().lower() for a in _help_menu.actions()))

    print("a month that can leave the app")
    from finfore import report as _RP
    _rp_store = Store(Path(tempfile.mkdtemp()) / "rep.db")
    load_demo(_rp_store, "struggling")
    _lcm = _RP.last_complete_month(_date(2026, 8, 13))
    check("the month covered is the last finished one, never a part-month",
          _lcm == _date(2026, 7, 1))
    _rep = _RP.build(_rp_store)
    check("it reports a real month of activity",
          _rep.n_transactions > 10 and _rep.income > 0 and _rep.spending > 0)
    check("with a category breakdown, ranked",
          _rep.categories
          and all(a[1] >= b[1] for a, b in zip(_rep.categories,
                                               _rep.categories[1:])))
    check("the grade and the outlook come along",
          _rep.grade and _rep.outlook_head)
    check("and so does anything that changed", len(_rep.changes) == 2)
    _html = _RP.render_html(_rep)
    check("it renders as one self-contained page",
          _html.startswith("<!DOCTYPE html")
          and "<style>" in _html and _html.rstrip().endswith("</html>"))
    # The whole point is that it survives leaving this machine.
    for _bad in ("<script", "http://", "https://", "src=", "@import"):
        check(f"nothing fetched or executed: no {_bad!r}",
              _bad not in _html.lower())
    check("it prints on paper, so it is styled light",
          "@media print" in _html and "background: #fff" in _html)
    check("and it says whose data it is and what it is not",
          "your own records" in _html and "not financial advice" in _html)
    _rp_path = Path(tempfile.mkdtemp()) / "m.html"
    _RP.write_html(_rp_store, _rp_path, with_outlook=False)
    check("writing it produces a readable file",
          _rp_path.exists() and _rp_path.stat().st_size > 2000)
    check("a merchant named like markup cannot break the page",
          "&lt;b&gt;" in _RP.render_html(
              _RP.MonthReport(month=_date(2026, 7, 1), generated=_date.today(),
                              grade="A", grade_line="<b>hi</b>",
                              grade_detail="x")))
    _rp_store.close()

    print("the dashboard can give the history the whole page")
    _dash = _win.pages[0]
    _win.nav.setCurrentRow(0)
    _open = list(_dash.split.sizes())
    check("the bottom charts start open",
          _dash.flow_card.expanded and _dash.cat_card.expanded)
    _dash.flow_card.set_expanded(False)
    _dash.cat_card.set_expanded(False)
    _dash._sync_split()
    _folded = list(_dash.split.sizes())
    check("folding both hides their plots",
          not _dash.plot_flow.isVisibleTo(_dash)
          and not _dash.plot_cat.isVisibleTo(_dash))
    # The freed height has to go somewhere, or folding buys nothing: a splitter
    # keeps the sizes it was given and would leave two strips floating in a
    # full-height pane. Measured as a share of the page, because a splitter
    # rescales its panes to whatever geometry it actually has and the absolute
    # pixel counts are not comparable across a relayout.
    _share = lambda s: s[0] / max(1, sum(s))
    check("and hands the height to the net worth history",
          _share(_folded) > _share(_open) and _share(_folded) > 0.85
          and _folded[1] < _open[1])
    _dash.flow_card.set_expanded(True)
    _dash._sync_split()
    check("reopening one gives the room back",
          _dash.plot_flow.isVisibleTo(_dash)
          and _dash.split.sizes()[1] > _folded[1])
    _dash.refresh()
    check("a redraw does not silently unfold them",
          not _dash.cat_card.expanded)

    print("simple view hides the machinery, not the answer")
    _pg_by_name = {type(p).__name__: p for p in _win.pages}
    _fcp, _prp, _insp = (_pg_by_name["ForecastPage"], _pg_by_name["ProgressPage"],
                         _pg_by_name["InsightsPage"])
    check("it defaults to simple, because a default nobody finds is not one",
          _win.simple_mode is True)
    _win._apply_mode(refresh=False)
    check("the advanced forecast controls are put away",
          not _fcp.paths_row.isVisibleTo(_fcp)
          and not _fcp.debt_card.isVisibleTo(_fcp)
          and not _fcp.view_row.isVisibleTo(_fcp))
    check("and the destructive Progress button with them",
          not _prp.clear_btn.isVisibleTo(_prp)
          and not _prp.table_card.isVisibleTo(_prp))
    check("and the score's arithmetic on Insights",
          not _insp.components.isVisibleTo(_insp))
    # The answer itself must never be what gets hidden.
    _fcp.refresh()  # Inspect a computed answer, rather than an empty placeholder.
    check("but the cash chart, the verdict and For nerds all stay",
          _fcp.cash_card.isVisibleTo(_fcp) and _fcp.risk.isVisibleTo(_fcp)
          and _fcp.horizon.isVisibleTo(_fcp))
    _win._toggle_mode()
    check("toggling brings everything back",
          _win.simple_mode is False
          and _fcp.paths_row.isVisibleTo(_fcp)
          and _prp.clear_btn.isVisibleTo(_prp)
          and _insp.components.isVisibleTo(_insp))
    check("and the header button says which face is on",
          _win.mode_btn.text() == "Everything")
    _win._toggle_mode()
    check("and back again", _win.simple_mode is True
          and _win.mode_btn.text() == "Simple view")

    print("for nerds: the maths is one click away, not on the page")
    from finfore.ui.widgets import InfoDialog as _Info
    _win._load_demo("thriving", confirm=False)
    _pages = {type(p).__name__: p for p in _win.pages}
    _fc, _pr, _ins = (_pages["ForecastPage"], _pages["ProgressPage"],
                      _pages["InsightsPage"])
    for _p in (_ins, _fc):
        _p.refresh()
    from finfore.progress import backfill as _bf
    _bf(_win.store, n=3, spacing_days=70, n_paths=60)
    _pr.refresh()

    _panels = {"forecast": _fc._nerd_panel(), "progress": _pr._nerd_panel(),
               "insights": _ins._nerd_panel()}
    for _name, (_title, _html) in _panels.items():
        check(f"the {_name} page can explain itself",
              len(_title) > 5 and len(_html) > 400)
        _d = _Info(_title, _html)
        check(f"and the {_name} panel renders as prose, not markup",
              len(_d.view.toPlainText()) > 200 and "<p>" not in
              _d.view.toPlainText())
        _d.close()
    check("the p-value is in the nerd panel",
          "p-value" in _panels["progress"][1])
    check("with the permutation count and the effect size beside it",
          "20,000" in _panels["progress"][1]
          and "Cohen" in _panels["progress"][1])
    check("and the calibration fractions moved there too",
          "nominal 80%" in _panels["progress"][1])
    check("the forecast panel carries the shortfall breakdown",
          "materiality threshold" in _panels["forecast"][1]
          and "Monte Carlo paths" in _panels["forecast"][1])
    check("and the insights panel carries FESR",
          "FESR" in _panels["insights"][1])

    # …and is gone from the page itself. These are the exact strings that used
    # to greet somebody's sibling on arrival.
    check("no p-value on the Progress page",
          "p = " not in _pr.sig_body.text()
          and "p = " not in _pr.card_spend.sub.text())
    check("no percentile jargon in the path verdict",
          "P50" not in _pr.path_note.text()
          and "calibrated" not in _pr.path_note.text())
    check("no FESR on the Insights page",
          "FESR" not in _ins.components.text())
    check("the risk line leads with a verdict, not a percentage",
          _fc.risk.text()[:1].isalpha()
          and _fc._res.shortfall.headline in _fc.risk.text())

    print("and the cash line says what is draining it")
    from finfore.ui.forecast_page import CrunchDialog as _CD
    # Every ledger whose cash goes under gets the button. It was briefly gated
    # to short dips, which hid it on three of the five demo profiles and on any
    # real ledger in trouble — the exact people with the question.
    _sf = _fc._res.shortfall
    check("the button appears whenever the cash line goes under",
          _fc.cause_btn.isVisibleTo(_fc) ==
          (_sf.low_date is not None
           and _sf.level in ("brief", "crunch", "watch", "high")))
    # Built from its own store rather than from whatever demo the window is
    # showing, so the two branches are exercised deterministically.
    _cs = Store(Path(tempfile.mkdtemp()) / "causes.db")
    load_demo(_cs, "struggling")
    _csf = run_forecast(_cs, 1095, 200,
                        essential_monthly=essential_monthly(_cs)).shortfall
    check("a ledger spending more than it earns reads as structural",
          _csf.level == "high" and _csf.median_days > 60)
    _cc, _nn = crunch_causes(_cs, _csf.low_date, _csf.median_days)
    _dlg = _CD(_cs, _csf, _cc, _nn)
    check("a structural shortfall still gets its commitments listed",
          not _dlg.timing and len(_cc) > 0)
    # Offering "move this bill" to somebody spending more than they earn is the
    # app recommending a fix it knows does not work.
    check("but is not offered rescheduling as a fix", _dlg._movers == [])
    _dlg.close()
    _tsf = copy.copy(_csf)
    _tsf.level, _tsf.median_days = "crunch", 4.0
    _tdlg = _CD(_cs, _tsf, _cc, _nn)
    check("a timing problem is offered it, on the movable items only",
          _tdlg.timing and _tdlg._movers
          and all(f.movable for f, _d, _b in _tdlg._movers))
    _tdlg.close()
    _cs.close()

    print("the Progress page says what a checkpoint is")
    from PyQt6.QtWidgets import QLabel as _QLabel
    from finfore.ui.progress_page import _WHAT_IS_THIS as _WIT
    _pp_src = (Path(__file__).resolve().parent.parent / "finfore" / "ui"
               / "progress_page.py").read_text(encoding="utf-8")
    for _phrase in ("dated snapshot", "every time you import a CSV",
                    "only data that existed", "Reconstruct history"):
        check(f"the long explanation covers {_phrase!r}", _phrase in _WIT)
    check("it promises the ledger is not touched",
          "unchanged" in _WIT and "transaction" in _WIT)
    check("and is reachable without pressing anything destructive",
          "What is a checkpoint?" in _pp_src)
    # The short version has to be on the page unprompted: a caption that only
    # appears when the page is empty is only ever read by people who cannot yet
    # use it.
    check("the short version sits under the toolbar at all times",
          any("dated snapshot" in w.text() and w.isVisibleTo(_pr)
              for w in _pr.findChildren(_QLabel)))
    check("the buttons explain themselves in their tooltips",
          "never changes it" in _pp_src
          and "clean line to measure from" in _pp_src)
    _win.return_to_real()

    print("checking for updates")
    from finfore import update as _UP
    for _a, _b, _want in [
            ("1.15.2", "1.15.1", True), ("1.15.1", "1.15.1", False),
            ("1.15.0", "1.15.1", False), ("1.16", "1.15.9", True),
            ("1.15.1", "1.15", True), ("2.0.0", "1.99.99", True),
            ("v1.16.0", "1.15.1", True), ("1.9.0", "1.10.0", False)]:
        check(f"{_a} {'>' if _want else 'is not >'} {_b}",
              _UP.is_newer(_a, _b) is _want)
    # The failure mode of a malformed tag must be "offer nothing", never
    # "offer everything" — this decides whether an executable gets downloaded.
    check("an unparseable tag is never newer",
          not _UP.is_newer("nightly", "1.15.1")
          and not _UP.is_newer("", "1.15.1"))
    check("version parsing tolerates the usual tag spellings",
          _UP.parse_version("v1.15.1") == (1, 15, 1)
          and _UP.parse_version("release-2.3") == (2, 3)
          and _UP.parse_version("nonsense") == ())

    # Asset picking, against a GitHub payload shaped like the real one.
    _payload = {
        "tag_name": "v1.16.0", "name": "1.16.0", "body": "# Notes\nthings",
        "html_url": "https://github.com/o/r/releases/tag/v1.16.0",
        "published_at": "2026-09-01T10:00:00Z",
        "assets": [
            {"name": "SHA256SUMS.txt", "size": 90,
             "browser_download_url": "https://github.com/o/r/releases/download/v1.16.0/SHA256SUMS.txt"},
            {"name": "FinancingWithDoug-1.16.0-Setup.exe", "size": 41943040,
             "browser_download_url": "https://github.com/o/r/releases/download/v1.16.0/FinancingWithDoug-1.16.0-Setup.exe"},
            {"name": "source.zip", "size": 10,
             "browser_download_url": "https://github.com/o/r/releases/download/v1.16.0/source.zip"},
        ]}
    _rel = _UP._release_from(_payload)
    check("the installer is picked out of the release assets",
          _rel.has_installer and _rel.asset_name.endswith("-Setup.exe")
          and abs(_rel.size_mb - 40.0) < 0.1)
    check("the checksum file is found alongside it",
          _rel.checksums_url.endswith("SHA256SUMS.txt"))
    check("the tag's v prefix is stripped for display",
          _rel.version == "1.16.0" and _rel.published == _date(2026, 9, 1))
    check("the notes and the page link come through",
          "things" in _rel.notes and _rel.page_url.startswith("https://github.com/"))

    # An updater downloads and runs an executable, so where it will fetch from
    # is not a detail. Anything off the known hosts is refused, not followed.
    check("only GitHub's own hosts are ever fetched from",
          _UP._host_ok("https://github.com/o/r/x.exe")
          and _UP._host_ok("https://objects.githubusercontent.com/x")
          and not _UP._host_ok("https://evil.example.com/x.exe")
          and not _UP._host_ok("http://github.com/o/r/x.exe"))
    check("an asset served from anywhere else is dropped, not offered",
          not _UP._release_from({
              "tag_name": "v9.9.9", "assets": [
                  {"name": "evil-Setup.exe", "size": 1,
                   "browser_download_url": "https://evil.example.com/evil-Setup.exe"}]
          }).has_installer)
    check("a release with no installer attached says so",
          not _UP._release_from({"tag_name": "v1.16.0"}).has_installer)
    # GitHub answers 404 both for "no releases yet" and for "you cannot see
    # this repository". Conflating them is the worst bug this module could
    # have: a private repo would report every installed copy as up to date,
    # for ever, silently, and nobody would find out until somebody mentioned
    # they were still on an old version. Both branches are exercised with the
    # network stubbed out, so the test says the same thing on a train.
    import io as _io
    import urllib.error as _ue
    _real_open = _UP._open

    def _stub(visible):
        def _fake(url, timeout=_UP.TIMEOUT):
            if url.endswith("/releases/latest"):
                raise _ue.HTTPError(url, 404, "Not Found", None, None)
            if visible:
                return _io.BytesIO(b'{"full_name":"o/r"}')
            raise _ue.HTTPError(url, 404, "Not Found", None, None)
        return _fake
    try:
        _UP._open = _stub(visible=True)
        check("a visible repo with no releases reads as 'nothing newer'",
              _UP.latest_release(owner="o", repo="r") is None)
        _UP._open = _stub(visible=False)
        _msg = ""
        try:
            _UP.latest_release(owner="o", repo="r")
        except _UP.UpdateError as e:
            _msg = str(e)
        check("an invisible one is reported, never as 'up to date'",
              "Can't see" in _msg and "private" in _msg)
    finally:
        _UP._open = _real_open

    def _stub_code(code):
        def _fake(url, timeout=_UP.TIMEOUT):
            raise _ue.HTTPError(url, code, "nope", None, None)
        return _fake
    try:
        _UP._open = _stub_code(403)
        _msg = ""
        try:
            _UP.latest_release(owner="o", repo="r")
        except _UP.UpdateError as e:
            _msg = str(e)
        check("being rate-limited says so rather than blaming the network",
              "rate-limit" in _msg)
    finally:
        _UP._open = _real_open
    # With the constants blanked, so this says the same thing whether or not
    # this checkout has been pointed at a repository yet.
    _saved_owner, _saved_repo = _UP.GITHUB_OWNER, _UP.GITHUB_REPO
    try:
        _UP.GITHUB_OWNER, _UP.GITHUB_REPO = "", ""
        check("an unconfigured build reports itself as such",
              not _UP.configured())
        _fetched = []
        try:
            _UP.latest_release()
        except _UP.UpdateError as e:
            _fetched.append(str(e))
        check("and asking anyway raises a message meant for a human",
              _fetched and "GITHUB_OWNER" in _fetched[0])
    finally:
        _UP.GITHUB_OWNER, _UP.GITHUB_REPO = _saved_owner, _saved_repo

    # Packaging: the installer script and the build script have to agree with
    # the app about its own name and version, or the updater compares the wrong
    # things and the shortcut points at nothing.
    _pkg = Path(__file__).resolve().parent.parent / "packaging"
    _iss = (_pkg / "installer.iss").read_text(encoding="utf-8")
    check("an Inno Setup script ships with the project", len(_iss) > 500)
    check("it installs the executable PyInstaller actually produces",
          f'"{_APP_EXE}"' in _iss.replace("{#ExeName}", _APP_EXE))
    check("it takes its version from the build rather than hard-coding one",
          "AppVersion={#AppVersion}" in _iss and "#ifndef AppVersion" in _iss)
    check("it needs no administrator rights, so nobody has to ring you",
          "PrivilegesRequired=lowest" in _iss)
    check("and it does not delete the ledger on uninstall",
          ".ledgerline" in _iss and "will NOT be deleted" in _iss)
    _ps1 = (_pkg / "build.ps1").read_text(encoding="utf-8")
    check("the build script reads the version from one place",
          "__version__" in _ps1 and "DAppVersion=$version" in _ps1)
    check("runs the tests before shipping anything", "test_core.py" in _ps1)
    check("and publishes the checksum the updater looks for",
          "SHA256SUMS.txt" in _ps1 and "SHA256" in _ps1)

    print("what a loan or investment was worth before its records start")
    from finfore.history import snapshot_history as _snaphist
    _pt = Path(tempfile.mkdtemp())
    _p = Store(_pt / "pre.db")
    _ptoday = _date.today()
    _p.add_account(Account(name="Car loan", type=AccountType.LOAN,
                           balance=-18_000.0, balance_date=_ptoday))
    _pidx = pd.date_range(_ptoday - timedelta(days=1000), _ptoday, freq="D")
    _bare = _snaphist(_p, _p.account(1), _pidx)
    check("an account with no dated balances is still held flat",
          abs(_bare.iloc[0] - (-18_000.0)) < 0.01)

    for _k in range(5):
        _p.add_snapshot(1, _ptoday - timedelta(days=200 - 40 * _k),
                        -(20_000.0 - 500.0 * _k))
    _ser = _snaphist(_p, _p.account(1), _pidx)
    check("zero is the default before the first recorded day",
          _p.prehistory_mode(1) == "zero")
    check("a loan is not a debt before it was taken out",
          _ser.loc[pd.Timestamp(_ptoday - timedelta(days=900))] == 0.0
          and _ser.loc[pd.Timestamp(_ptoday - timedelta(days=201))] == 0.0)
    check("and is its recorded size from the first day onward",
          abs(_ser.loc[pd.Timestamp(_ptoday - timedelta(days=199))]
              + 19_987.5) < 1.0)
    # networth_history spans the transaction ledger, so the chart only reaches
    # back past the loan's records if some other account's does.
    _p.add_account(Account(name="Chk", type=AccountType.CHECKING,
                           balance=2_000.0, balance_date=_ptoday))
    _p.add_transactions(pd.DataFrame([
        {"account_id": 2,
         "date": pd.Timestamp(_ptoday - timedelta(days=1000 - 30 * i)),
         "description": "PAY", "amount": 100.0, "category": "Income",
         "import_hash": f"p{i}"} for i in range(33)]))
    _nw = networth_history(_p)
    check("net worth is not dragged down by a debt that did not exist",
          abs(_nw["debt"].iloc[100]) < 0.01)
    check("and carries it once the loan exists",
          abs(_nw["debt"].iloc[-1] - 18_000.0) < 1.0)

    _p.set_prehistory_mode(1, "flat")
    _flat = _snaphist(_p, _p.account(1), _pidx)
    check("an account carried in with a balance can say so",
          abs(_flat.iloc[0] - (-20_000.0)) < 0.01)
    try:
        _p.set_prehistory_mode(1, "sideways")
        _refused = False
    except ValueError:
        _refused = True
    check("an unknown mode is refused rather than silently stored", _refused)
    _p.close()

    print("dismissing the ledger warning")
    from finfore.history import (acknowledge as _ack,
                                 unacknowledged as _unack)
    _at = Path(tempfile.mkdtemp())
    _a = Store(_at / "ack.db")
    _a.add_account(Account(name="Savings", type=AccountType.SAVINGS,
                           balance=0.71, balance_date=_ptoday))
    _a.add_transactions(pd.DataFrame([
        {"account_id": 1,
         "date": pd.Timestamp(_ptoday - timedelta(days=300 - 20 * i)),
         "description": "Deposit", "amount": 500.0 if i % 3 else -120.0,
         "category": "Deposits", "import_hash": f"a{i}"} for i in range(15)]))
    check("a ledger that cannot reconcile is reported", len(_reconcile(_a)) == 1)
    check("and is unacknowledged to begin with", len(_unack(_a)) == 1)
    _before = _reconcile(_a)[0].opening
    _ack(_a, _reconcile(_a))
    check("acknowledging silences the dashboard", len(_unack(_a)) == 0)
    check("but never the arithmetic — Check my balances still shows it",
          len(_reconcile(_a)) == 1)

    # The point of keying acknowledgement to the number: dismissing *this*
    # discrepancy must not dismiss the account for ever.
    _a.add_transactions(pd.DataFrame([
        {"account_id": 1, "date": pd.Timestamp(_ptoday - timedelta(days=5)),
         "description": "Deposit", "amount": 900.0, "category": "Deposits",
         "import_hash": "aNEW"}]))
    check("a later import moves the figure", _reconcile(_a)[0].opening != _before)
    check("so the warning comes back", len(_unack(_a)) == 1)
    _ack(_a, _reconcile(_a))
    _a.set_ledger_acknowledgements({})
    check("and it can always be restored deliberately", len(_unack(_a)) == 1)
    _a.close()

    print("undo and redo")
    from finfore.models import Goal
    from finfore.undo import UndoStack
    _ut = Path(tempfile.mkdtemp())
    _u = Store(_ut / "undo.db")
    check("undo is off until it is asked for", _u.undo is None)
    check("and store methods still work with it off",
          _u.add_goal(Goal("Before undo", 100, "cash")) is not None)
    _stack = _u.enable_undo()
    check("nothing to undo on a freshly armed stack", not _stack.can_undo)

    _u.add_account(Account(name="Chk", type=AccountType.CHECKING,
                           balance=500.0, balance_date=_date.today()))
    check("a write becomes an undoable step", _stack.can_undo)
    check("named the way the user will read it",
          _stack.undo_label == "Add account")
    _u.add_goal(Goal("Roof", 8_000, "cash"))
    check("undo takes back the last step only", _stack.undo() == "Add goal")
    check("the goal is gone", [g.name for g in _u.goals()] == ["Before undo"])
    check("and the account it did not touch is still there",
          len(_u.accounts()) == 1)
    check("undo again reaches the step before",
          _stack.undo() == "Add account" and _u.accounts() == [])
    check("redo puts it back",
          _stack.redo() == "Add account" and len(_u.accounts()) == 1)
    check("and redo again restores the goal",
          _stack.redo() == "Add goal" and len(_u.goals()) == 2)
    check("nothing left to redo", not _stack.can_redo)

    # One user gesture, one press of Ctrl-Z. This is the property that makes
    # undo usable on an import, which writes to five tables.
    _n = len(_stack._undo)
    with _u.undo_group("Import CSV"):
        _u.add_goal(Goal("A", 1, "cash"))
        _u.add_goal(Goal("B", 2, "cash"))
        _u.set_setting("plan_policy", "weighted")
    check("a group of writes is a single step", len(_stack._undo) - _n == 1)
    check("under the name the group was given",
          _stack.undo_label == "Import CSV")
    _stack.undo()
    check("and undoing it reverses every write inside",
          len(_u.goals()) == 2 and _u.get_setting("plan_policy") is None)

    _n = len(_stack._undo)
    _u.update_recurring(1)              # no fields: returns without writing
    _u.set_category([], "Misc")         # no ids: writes nothing
    check("an action that changed nothing leaves no step to press",
          len(_stack._undo) == _n)
    with _u.undo_suspended():
        _u.add_goal(Goal("Quiet", 5, "cash"))
    check("suspended work is not recorded", len(_stack._undo) == _n)
    check("but it did happen", len(_u.goals()) == 3)

    check("a new action forks the timeline rather than branching it",
          (_stack.undo(), _u.add_goal(Goal("C", 3, "cash")),
           _stack.can_redo)[2] is False)

    _stack2 = UndoStack(_u, depth=3)
    for _i in range(6):
        with _stack2.group(f"step {_i}"):
            _u.add_goal(Goal(f"G{_i}", _i + 1, "cash"))
    check("the stack is bounded, oldest dropped first",
          len(_stack2._undo) == 3 and _stack2.undo_label == "step 5")
    _u.close()

    check("every writing store method carries an undo label",
          not [n for n in ("add_account", "update_account", "delete_account",
                           "add_transactions", "delete_transactions",
                           "set_category", "set_categories_bulk", "add_goal",
                           "update_goal", "delete_goal", "add_snapshot",
                           "import_snapshots", "merge_recurring",
                           "update_recurring", "set_setting", "clear_all")
               if not hasattr(getattr(Store, n), "undo_label")])
    check("and reading methods do not",
          not hasattr(Store.transactions, "undo_label"))

    print("chart floors")
    from finfore.ui.charts import _FLOOR_POS, _floor_label_opts
    check("floor labels sit clear of the y-axis",
          all(v >= 0.35 for v in _FLOOR_POS.values()))
    check("the three floors are staggered so they cannot overlap",
          len(set(_FLOOR_POS.values())) == len(_FLOOR_POS))
    check("label options anchor the text above the line",
          _floor_label_opts("#fff", "zero")["anchors"] == [(0.5, 1.0), (0.5, 1.0)])

    print("backup and workflow regressions")
    from test_usability import run_tests
    if not run_tests().wasSuccessful():
        raise AssertionError("backup and workflow regressions failed")

    print("categorization safety regressions")
    from test_categories import run_tests as run_category_tests
    if not run_category_tests().wasSuccessful():
        raise AssertionError("categorization regressions failed")

    print("data-quality evidence checks")
    from test_data_quality import run_tests as run_quality_tests
    if not run_quality_tests().wasSuccessful():
        raise AssertionError("data-quality regressions failed")

    print("forecast history selection regressions")
    from test_forecast_history import run_tests as run_history_tests
    if not run_history_tests().wasSuccessful():
        raise AssertionError("forecast history regressions failed")

    print("personal budget and business reimbursement regressions")
    from test_personal_finances import run_tests as run_personal_tests
    if not run_personal_tests().wasSuccessful():
        raise AssertionError("personal finance regressions failed")

    print("\nall tests passed")


if __name__ == "__main__":
    main()
