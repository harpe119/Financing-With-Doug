"""A month, on one page, that can leave the app.

Everything else here is a live instrument panel: it assumes you are sitting in
front of it, and it assumes you are the person who set it up. This is the
opposite — a single self-contained page about one calendar month, written for
somebody who has never seen the program, and shaped so it can be printed or
sent to a partner.

Three decisions follow from that.

**Plain HTML with the styling inline.** No JavaScript, no external stylesheet,
no fonts to fetch. It opens in any browser on any machine, offline, in ten
years, and Ctrl-P turns it into a PDF without anything else being installed —
which is why this is not a QtPrintSupport dialog. One file, no dependencies,
nothing to explain.

**Light, not the app's dark theme.** This is made to be printed, and a dark
page prints as a solid black rectangle or, worse, as nothing at all.

**A finished calendar month, not a rolling window.** Every other figure in the
app is recency-weighted, which is right for judging habits and wrong for a
document with a month's name at the top: "August" has to mean August.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .alerts import BillChange, bill_changes
from .history import headline_stats
from .insights import bucket_of, monthly_aggregates, spending_score
from .store import Store
from .util import add_months, fmt_money, fmt_day


@dataclass
class MonthReport:
    """Everything the page shows, gathered before any of it is formatted."""
    month: date                       # the first of the month covered
    generated: date
    networth: float = 0.0
    cash: float = 0.0
    investments: float = 0.0
    debt: float = 0.0
    income: float = 0.0
    spending: float = 0.0
    n_transactions: int = 0
    categories: list[tuple[str, float, str]] = field(default_factory=list)
    grade: str = ""
    grade_line: str = ""
    grade_detail: str = ""
    changes: list[BillChange] = field(default_factory=list)
    outlook_head: str = ""
    outlook_detail: str = ""
    goals: list[tuple[str, float, str]] = field(default_factory=list)
    prev_income: float | None = None
    prev_spending: float | None = None

    @property
    def surplus(self) -> float:
        return self.income - self.spending

    @property
    def label(self) -> str:
        return self.month.strftime("%B %Y")


def last_complete_month(today: date | None = None) -> date:
    """The first of the most recently finished month.

    A month still in progress always looks like an unusually thrifty one, which
    is the single most misleading thing a report like this could say.
    """
    today = today or date.today()
    return add_months(date(today.year, today.month, 1), -1)


def build(store: Store, month: date | None = None,
          with_outlook: bool = True) -> MonthReport:
    """Gather one month's figures. Nothing here formats anything."""
    month = date(month.year, month.month, 1) if month else last_complete_month()
    rep = MonthReport(month=month, generated=date.today())

    st = headline_stats(store)
    rep.networth = float(st["networth"])
    rep.cash = float(st["cash"])
    rep.investments = float(st["investments"])
    rep.debt = float(st["debt"])

    tx = store.transactions()
    if not tx.empty:
        per = pd.to_datetime(tx["date"]).dt.to_period("M")
        this = tx[per == pd.Period(month, freq="M")]
        prev = tx[per == pd.Period(add_months(month, -1), freq="M")]
        rep.income, rep.spending, rep.categories = _month_figures(this)
        rep.n_transactions = int(len(this))
        if len(prev):
            p_in, p_out, _ = _month_figures(prev)
            rep.prev_income, rep.prev_spending = p_in, p_out

    agg = monthly_aggregates(tx)
    sc = spending_score(agg) if agg is not None else None
    if sc is not None:
        rep.grade = sc.letter
        rep.grade_line = sc.means_line
        rep.grade_detail = sc.headline

    try:
        rep.changes = bill_changes(store)
    except Exception:
        rep.changes = []

    if with_outlook and store.accounts():
        try:
            _outlook(store, rep)
        except Exception:
            pass          # a report without a forecast is still worth having
    return rep


def _month_figures(rows: pd.DataFrame) -> tuple[float, float,
                                                list[tuple[str, float, str]]]:
    """Income, spending and a ranked category breakdown for one month.

    Transfers are excluded on both sides: moving your own money between your
    own accounts is not income and not spending, and counting it as either
    would make a month with one savings sweep in it look like a disaster.
    """
    if rows.empty:
        return 0.0, 0.0, []
    rows = rows[rows["category"] != "Transfer"]
    if rows.empty:
        return 0.0, 0.0, []
    income = float(rows.loc[rows["amount"] > 0, "amount"].sum())
    out = rows[rows["amount"] < 0]
    spending = float(-out["amount"].sum())
    cats = (-out.groupby("category")["amount"].sum()).sort_values(
        ascending=False)
    top = [(str(c), float(v), bucket_of(str(c))) for c, v in cats.items()]
    return income, spending, top[:10]


def _outlook(store: Store, rep: MonthReport) -> None:
    from .forecast import goal_plan_enabled, run_forecast
    from .models import Scenario
    from .insights import essential_monthly
    ess = essential_monthly(store)
    res = run_forecast(store, 365, 200, Scenario(follow_goal_plan=goal_plan_enabled(store)),
                       emergency_target=max(0.0, ess * 3),
                       essential_monthly=ess)
    if res.shortfall is not None:
        rep.outlook_head = res.shortfall.headline
        rep.outlook_detail = res.shortfall.detail
    for out in res.goal_outcomes:
        if out.already_met:
            when = "already there"
        elif out.det is not None:
            when = f"on track for {out.det.strftime('%b %Y')}"
        else:
            when = "not within the next year at this rate"
        rep.goals.append((out.goal.name, out.goal.amount, when))
    for goal in store.goals():
        if goal.kind == "savings":
            rep.goals.append((goal.name, goal.amount,
                f"{fmt_money(goal.saved_amount)} set aside; contribution schedule on Goals"))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px 28px 48px; background: #fff; color: #1c2430;
       font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial,
                    sans-serif; line-height: 1.5; }
.wrap { max-width: 760px; margin: 0 auto; }
h1 { font-size: 27px; margin: 0 0 2px; letter-spacing: -0.4px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 1.1px;
     color: #6b7a8d; margin: 32px 0 10px; border-bottom: 1px solid #e2e7ee;
     padding-bottom: 6px; }
.sub { color: #6b7a8d; font-size: 13px; margin: 0 0 4px; }
.grid { display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0 0; }
.tile { flex: 1 1 150px; border: 1px solid #e2e7ee; border-radius: 9px;
        padding: 11px 13px; }
.tile .k { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px;
           color: #6b7a8d; }
.tile .v { font-size: 21px; font-weight: 600; margin-top: 2px; }
.pos { color: #12805f; } .neg { color: #c0453a; }
.row { display: flex; align-items: center; gap: 10px; margin: 5px 0; }
.row .nm { width: 168px; flex: none; font-size: 13px; }
.row .amt { width: 84px; flex: none; text-align: right; font-size: 13px;
            font-variant-numeric: tabular-nums; }
.bar { flex: 1; height: 9px; background: #eef1f6; border-radius: 5px;
       overflow: hidden; }
.bar span { display: block; height: 100%; border-radius: 5px; }
.need { background: #5b7fd0; } .want { background: #e0952c; }
.give { background: #8f7ede; } .other { background: #9aa7b8; }
.note { background: #f5f7fa; border: 1px solid #e2e7ee; border-radius: 9px;
        padding: 13px 15px; margin: 12px 0; }
.note.warn { background: #fdf5ea; border-color: #e8d3ae; }
.note h3 { margin: 0 0 5px; font-size: 15px; }
.note p { margin: 0; font-size: 14px; }
ul { margin: 8px 0 0; padding-left: 20px; font-size: 14px; }
li { margin: 4px 0; }
.foot { margin-top: 38px; padding-top: 12px; border-top: 1px solid #e2e7ee;
        color: #8493a5; font-size: 12px; }
@media print { body { padding: 0; } h2 { page-break-after: avoid; }
               .note, .tile { break-inside: avoid; } }
"""

_BUCKET_CLASS = {"essential": "need", "frivolous": "want", "giving": "give"}


def _e(s) -> str:
    return html.escape(str(s))


def _tile(key: str, value: str, cls: str = "") -> str:
    return (f'<div class="tile"><div class="k">{_e(key)}</div>'
            f'<div class="v {cls}">{_e(value)}</div></div>')


def _delta(now: float, before: float | None, lower_is_better: bool) -> str:
    if before is None or abs(before) < 0.01:
        return ""
    d = now - before
    if abs(d) < max(1.0, 0.005 * abs(before)):
        return " · about the same as last month"
    better = (d < 0) if lower_is_better else (d > 0)
    return (f' · <span class="{"pos" if better else "neg"}">'
            f'{fmt_money(abs(d))} {"less" if d < 0 else "more"} than last '
            f'month</span>')


def render_html(rep: MonthReport) -> str:
    """The whole report as one self-contained page."""
    saved_cls = "pos" if rep.surplus >= 0 else "neg"
    out = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Month in review — {_e(rep.label)}</title>",
        f"<style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{_e(rep.label)}</h1>",
        f"<p class='sub'>A month in review · prepared "
        f"{fmt_day(rep.generated)} from your own records</p>",

        "<h2>Where you stand today</h2><div class='grid'>",
        _tile("Net worth", fmt_money(rep.networth),
              "pos" if rep.networth >= 0 else "neg"),
        _tile("Cash", fmt_money(rep.cash)),
        _tile("Investments", fmt_money(rep.investments)),
        _tile("Debt", fmt_money(rep.debt), "neg" if rep.debt > 0 else ""),
        "</div>",

        f"<h2>What happened in {_e(rep.month.strftime('%B'))}</h2>",
        "<div class='grid'>",
        _tile("Money in", fmt_money(rep.income)),
        _tile("Money out", fmt_money(rep.spending)),
        _tile("Left over", fmt_money(rep.surplus, signed=True), saved_cls),
        "</div>",
        f"<p class='sub' style='margin-top:10px'>"
        f"{rep.n_transactions} transactions"
        f"{_delta(rep.spending, rep.prev_spending, True)}.</p>",
    ]

    if rep.categories:
        out.append("<h2>Where it went</h2>")
        top = max(v for _n, v, _b in rep.categories)
        for name, val, bucket in rep.categories:
            pct = max(2.0, 100.0 * val / top) if top else 0.0
            cls = _BUCKET_CLASS.get(bucket, "other")
            out.append(
                f"<div class='row'><div class='nm'>{_e(name)}</div>"
                f"<div class='bar'><span class='{cls}' "
                f"style='width:{pct:.1f}%'></span></div>"
                f"<div class='amt'>{_e(fmt_money(val))}</div></div>")
        out.append("<p class='sub' style='margin-top:9px'>Blue is what you "
                   "have to spend, amber is what you choose to.</p>")

    if rep.grade:
        out.append(
            f"<h2>Habits</h2><div class='note'><h3>Grade {_e(rep.grade)} — "
            f"{_e(rep.grade_line)}</h3><p>{_e(rep.grade_detail)}</p></div>")

    if rep.changes:
        out.append("<h2>What changed without being asked</h2>")
        for c in rep.changes:
            cls = "warn" if c.worse else ""
            out.append(f"<div class='note {cls}'><h3>{_e(c.headline())}</h3>"
                       f"<p>{_e(c.detail())}</p></div>")

    if rep.outlook_head:
        out.append(
            f"<h2>The year ahead</h2><div class='note'>"
            f"<h3>{_e(rep.outlook_head)}</h3>"
            f"<p>{_e(rep.outlook_detail)}</p></div>")

    if rep.goals:
        out.append("<h2>Goals</h2><ul>")
        for name, amount, when in rep.goals:
            out.append(f"<li><b>{_e(name)}</b> — {_e(fmt_money(amount))}, "
                       f"{_e(when)}</li>")
        out.append("</ul>")

    out.append(
        "<p class='foot'>Produced by Financing with Doug from records held "
        "only on this computer. Figures beyond today are statistical "
        "estimates from your own history, not financial advice.</p>"
        "</div></body></html>")
    return "".join(out)


def write_html(store: Store, path, month: date | None = None,
               with_outlook: bool = True) -> MonthReport:
    """Build and save the report; returns what was in it."""
    rep = build(store, month, with_outlook=with_outlook)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html(rep))
    return rep
