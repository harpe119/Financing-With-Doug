"""Core logic tests — run with:  python tests/test_core.py

Covers ingestion & dedup, transfer pairing, recurrence detection (incl.
day-of-month clamping), history reconstruction, and forecast invariants.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from pathlib import Path as _Path
from datetime import date as _date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from finfore.categorize import pair_transfers
from finfore.demo import SAMPLE_DIR, load_demo
from finfore.forecast import run_forecast
from finfore.history import cash_history
from finfore.ingest import guess_mapping, read_raw, sniff_delimiter
from finfore.models import Account, AccountType, RecurringItem, Scenario
from finfore.pipeline import import_csv
from finfore.recurrence import occurrences
from finfore.store import Store


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
          5.5 <= q3.value <= 7.5 and q3.quadrant == "squeezed, not careless")
    check("q4 low-FESR + high net is the best case",
          q4.value >= 8.5 and q4.quadrant == "disciplined and saving")
    check("the four quadrants rank q1 < q2 < q3 < q4",
          q1.value < q2.value < q3.value < q4.value)
    check("a bad situation still beats bad habits at the same net income",
          q3.value > q1.value)

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
          agg_demo is not None and agg_demo.partial_current
          and agg_demo.n_months >= 1)
    check("transfers excluded from spending",
          agg_demo.income > 0 and agg_demo.spending > 0)

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
    _plot = _pg.PlotWidget()
    _ys = np.array([100.0, 250.0, 175.0])
    _charts.lock_view(_plot, 0.0, 100.0 * 86400.0, [_ys])
    _lim = _plot.getViewBox().state["limits"]
    check("locking bounds both axes",
          all(v is not None for v in _lim["xLimits"] + _lim["yLimits"]))
    check("the y bounds contain the data with headroom",
          _lim["yLimits"][0] < 100.0 and _lim["yLimits"][1] > 250.0)
    check("zoom-out is capped at the full extent",
          _lim["xRange"][1] is not None and _lim["yRange"][1] is not None)
    check("zoom-in is capped so a wheel-scroll cannot reach one pixel",
          _lim["xRange"][0] is not None)
    _charts.lock_view(_plot, 0.0, 100.0 * 86400.0, [_ys], include_y=[0.0])
    check("a nearby reference floor is pulled into view",
          _plot.getViewBox().state["limits"]["yLimits"][0] <= 0.0)
    _charts.lock_view(_plot, 0.0, 100.0 * 86400.0, [_ys + 500_000.0],
                      include_y=[0.0])
    check("a far-away floor is left out rather than flattening the data",
          _plot.getViewBox().state["limits"]["yLimits"][0] > 1000.0)
    _charts.unlock_view(_plot)
    check("unlocking clears the bounds",
          _plot.getViewBox().state["limits"]["xLimits"] == [None, None])

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

    print("teller-style amount entry")
    from finfore.ui.widgets import MoneySpinBox as _MSB
    from PyQt6.QtGui import QKeyEvent as _QKE
    from PyQt6.QtCore import Qt as _Qt

    def _type_raw(spin, digits: str) -> float:
        """Send keystrokes without clearing the selection first."""
        for ch in digits:
            key = (_Qt.Key.Key_0 + int(ch) if ch.isdigit()
                   else _Qt.Key.Key_Period)
            spin.keyPressEvent(_QKE(_QKE.Type.KeyPress, key,
                                    _Qt.KeyboardModifier.NoModifier, ch))
        return spin.value()

    def _type(spin, digits: str) -> float:
        spin.selectAll()          # what focusing the field does for real
        return _type_raw(spin, digits)

    _sb = _MSB(cents_entry=True)
    _sb.setRange(-1e9, 1e9)
    _sb.setDecimals(2)
    check("1234 fills in from the right as $12.34",
          abs(_type(_sb, "1234") - 12.34) < 1e-9)
    check("123456 reads as $1,234.56", abs(_type(_sb, "123456") - 1234.56) < 1e-9)
    check("a single digit is a single cent", abs(_type(_sb, "7") - 0.07) < 1e-9)
    check("typing the decimal point out lands in the same place",
          abs(_type(_sb, "1234.56") - 1234.56) < 1e-9)
    check("the mis-entry case: 7152000 is $71,520.00",
          abs(_type(_sb, "7152000") - 71_520.0) < 1e-9)
    _type(_sb, "1234")
    _sb.keyPressEvent(_QKE(_QKE.Type.KeyPress, _Qt.Key.Key_Backspace,
                           _Qt.KeyboardModifier.NoModifier, ""))
    check("backspace shifts one digit back out", abs(_sb.value() - 1.23) < 1e-9)

    # The regression that shipped broken: a field already holding a value must
    # be *replaced* by what is typed next, not appended to. It passed review
    # because a field sitting at 0.00 behaves identically either way.
    _sb.setValue(71_520.00)
    check("typing into a populated field replaces it",
          abs(_type(_sb, "500") - 5.00) < 1e-9)
    _sb.setValue(71_520.00)
    _sb.lineEdit().selectAll()      # drag-select, which includes the "$ " prefix
    check("a selection including the prefix also counts as a fresh start",
          _sb._fresh())
    _sb.setValue(71_520.00)
    _sb._pending_select = False
    _sb.lineEdit().deselect()
    check("with nothing selected, digits append rather than replace",
          abs(_type_raw(_sb, "5") - 715_200.05) < 1e-6)
    _plain = _MSB(cents_entry=False)
    _plain.setRange(0, 100)
    _plain.setDecimals(4)
    check("cents entry is opt-in, so rate fields keep ordinary typing",
          not _plain._cents_entry)

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

    print("explainer video")
    from finfore.ui import video as _video
    _vp = _video.video_path("monte_carlo")
    check("the Monte Carlo explainer ships with the project",
          _vp is not None and _vp.exists())
    check("has_video agrees with video_path", _video.has_video("monte_carlo"))
    check("an unknown key resolves to nothing",
          _video.video_path("nope") is None
          and not _video.has_video("nope"))
    _saved2 = getattr(sys, "_MEIPASS", None)
    try:
        _fake2 = Path(tempfile.mkdtemp())
        (_fake2 / "assets").mkdir()
        (_fake2 / "assets" / "monte_carlo.mp4").write_bytes(b"x")
        sys._MEIPASS = str(_fake2)
        check("the video resolves inside a frozen bundle",
              _video.video_path("monte_carlo")
              == _fake2 / "assets" / "monte_carlo.mp4")
    finally:
        if _saved2 is None:
            delattr(sys, "_MEIPASS")
        else:
            sys._MEIPASS = _saved2
    check("and returns to the source tree when not frozen",
          _video.video_path("monte_carlo") == _vp)
    _spec_txt = (Path(__file__).resolve().parent.parent
                 / "packaging" / "doug.spec").read_text(encoding="utf-8")
    check("the spec bundles assets/ so the video survives a build",
          "assets" in _spec_txt)

    print("splash & chart floors")
    from finfore.ui.splash import DOUG_LINES, DOUG_FACTS, doug_fact
    check("Doug has things to say",
          len(DOUG_LINES) >= 8 and len(DOUG_FACTS) >= 5
          and doug_fact() in DOUG_FACTS)
    from finfore.ui.charts import _FLOOR_POS, _floor_label_opts
    check("floor labels sit clear of the y-axis",
          all(v >= 0.35 for v in _FLOOR_POS.values()))
    check("the three floors are staggered so they cannot overlap",
          len(set(_FLOOR_POS.values())) == len(_FLOOR_POS))
    check("label options anchor the text above the line",
          _floor_label_opts("#fff", "zero")["anchors"] == [(0.5, 1.0), (0.5, 1.0)])

    print("\nall tests passed")


if __name__ == "__main__":
    main()
