"""Build fictional manual evidence from a temporary snapshot of the application.

All writes are confined to temporary databases and docs/manual/generated.
No GUI, normal database, network connection, or installation is opened.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
MANUAL = Path(__file__).resolve().parents[1]
REPO = MANUAL.parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tex(value):
    table = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
             '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
             '^': r'\textasciicircum{}'}
    return ''.join(table.get(c, c) for c in str(value))


def main():
    os.environ['TZ'] = 'America/New_York'
    if hasattr(time, 'tzset'):
        time.tzset()
    reference_date = datetime.now(ZoneInfo('America/New_York')).date()
    source_hashes = {}
    with tempfile.TemporaryDirectory(prefix='doug-manual-') as scratch_name:
        scratch = Path(scratch_name)
        frozen = scratch / 'source'
        for path in sorted((REPO / 'finfore').rglob('*.py')):
            relative = path.relative_to(REPO)
            data = path.read_bytes()
            source_hashes[str(relative)] = digest(data)
            destination = frozen / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        for relative in ['run.py', 'requirements.txt', 'docs/getting-started.md', 'docs/methodology.md']:
            source_hashes[relative] = digest((REPO / relative).read_bytes())
        sys.path.insert(0, str(frozen))
        import numpy as np
        import pandas as pd
        from finfore import __version__
        from finfore.forecast import run_forecast
        from finfore.funding import funding_overview
        from finfore.goals import build_plan
        from finfore.history import account_balance_history, headline_stats
        from finfore.insights import Aggregates, spending_score
        from finfore.models import Account, AccountType, Goal, RecurringItem, Scenario
        from finfore.spending_plan import calculate_plan
        from finfore.store import Store

        generated = scratch / 'generated'
        generated.mkdir()
        checks = []
        values = {}
        def check(label, condition):
            if not condition:
                raise AssertionError(label)
            checks.append(label)
        def table_file(name, headings, rows, spec):
            lines = [r'\par\noindent\begin{tabularx}{\textwidth}{@{}' + spec + r'@{}}',
                     r'\toprule', ' & '.join(headings) + r'\\\midrule']
            lines += [' & '.join(tex(v) for v in row) + r'\\' for row in rows]
            lines += [r'\bottomrule', r'\end{tabularx}\par']
            (generated / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')

        # An independent statement interval, followed by the actual history API.
        ledger = Store(scratch / 'ledger.db')
        anchor = date(reference_date.year, 1, 3)
        aid = ledger.add_account(Account('Example checking', AccountType.CHECKING, 1000, anchor))
        rows = [(date(anchor.year, 1, d), description, amount) for d, description, amount in
                [(1, 'Deposit', 500), (2, 'Purchase', -100), (3, 'Purchase', -50), (4, 'Purchase', -25)]]
        frame = pd.DataFrame([dict(date=d, description=s, amount=a, category='Income' if a > 0 else 'Shopping',
                                  account_id=aid, import_hash=f'anchor-{i}') for i, (d, s, a) in enumerate(rows)])
        ledger.add_transactions(frame)
        history = account_balance_history(ledger)[aid]
        expected = [1150, 1050, 1000, 975]
        actual = [float(history.loc[pd.Timestamp(d)]) for d, _, _ in rows]
        check('dated-anchor balances agree with independent statement arithmetic', actual == expected)
        table_file('balance_table.tex', ['Date', 'Posted flow', 'Calculated close'],
                   [(d.isoformat(), f'${a:+,.2f}', f'${b:,.2f}') for (d, _, a), b in zip(rows, actual)], 'Yrr')
        ledger.close()

        # Four holdings and four purposes: no double counting or invented location.
        goals = Store(scratch / 'goals.db')
        aids = [goals.add_account(Account(n, t, 1000, reference_date, annual_return=0, volatility=0))
                for n, t in [('Checking', AccountType.CHECKING), ('Savings', AccountType.SAVINGS),
                             ('Money market', AccountType.SAVINGS), ('CD', AccountType.INVESTMENT)]]
        gids = [goals.add_goal(Goal(n, a, 'savings')) for n, a in
                [('Baseline', 1000), ('Funded purchase', 1500), ('House', 10000), ('Unfunded purchase', 500)]]
        goals.set_plan_order([f'goal:{gid}' for gid in gids])
        for gi, ai, amount in [(0, 0, 1000), (1, 1, 1000), (1, 2, 500), (2, 2, 500), (2, 3, 1000)]:
            goals.record_goal_activity(gids[gi], 'contribute', amount, reference_date, aids[ai])
        overview = funding_overview(goals)
        check('located savings and account holdings each total $4,000',
              sum(g.saved for g in overview.goals) == overview.cash + overview.investments == 4000)
        check('four-account allocation leaves no unassigned holdings', overview.unassigned == 0)
        table_file('goal_table.tex', ['Goal', 'Target', 'Held', 'Still needed'],
                   [(g.name, f'${g.target:,.2f}', f'${g.saved:,.2f}', f'${g.remaining:,.2f}')
                    for g in overview.goals], 'Yrrr')
        goals.set_setting('plan_surplus', 350)
        goals.set_plan_policy('custom')
        goals.set_plan_allocations({f'goal:{gids[2]}': 100, f'goal:{gids[3]}': 100})
        plan = build_plan(goals, surplus=350)
        allocation = [plan.by_key(f'goal:{gids[i]}').monthly for i in (2, 3)]
        check('equal custom shares divide $350 into $175 and $175', allocation == [175, 175])
        before_saved = sum(g.saved_amount for g in goals.goals())
        goals.record_goal_activity(gids[2], 'purchase', 200, reference_date, aids[3])
        house = next(g for g in goals.goals() if g.id == gids[2])
        check('purchase reduces held money but retains progress', house.saved_amount == 1300 and house.spent_amount == 200)
        check('goal activity creates no ledger transaction', goals.transactions().empty)
        values.update(GoalHeldBefore=f'{before_saved:,.2f}', HouseHeldAfter=f'{house.saved_amount:,.2f}')
        goals.close()

        budget = Store(scratch / 'budget.db')
        original = dict(income=4000, essential=2500, debt=0, frivolous=700,
                        giving=100, unknown=0, reserve=100, investing=0)
        budget.save_spending_plan(original, 350)
        old = calculate_plan(budget)
        new_values = dict(original, frivolous=500)
        budget.save_spending_plan(new_values, 550)
        new = calculate_plan(budget)
        check('budget reduction increases room by $200 with no category double counting',
              old.available == 600 and new.available == 800 and old.remaining(350) == new.remaining(550) == 250)
        table_file('budget_table.tex', ['Monthly line', 'Before', 'After'],
                   [('Take-home income', '$4,000', '$4,000'), ('Needs, excluding debt', '$2,500', '$2,500'),
                    ('Regular debt', '$0', '$0'), ('Wants', '$700', '$500'), ('Giving', '$100', '$100'),
                    ('Uncategorized', '$0', '$0'), ('Irregular reserve', '$100', '$100'), ('Investing', '$0', '$0'),
                    ('Available before goals', '$600', '$800'), ('Goal allowance', '$350', '$550'),
                    ('Unassigned', '$250', '$250')], 'Yrr')
        budget.close()

        score = spending_score(Aggregates(income=4000, spending=3200, essential=2000,
            frivolous=1200, unknown=0, subscriptions=0, n_months=1, partial_current=False))
        independent_score = .6 * 5 + .4 * 8.5 - 2.5 * (1 - 5 / 10) * (1 - 8.5 / 10)
        check('documented score example agrees with independent anchor arithmetic',
              score is not None and abs(score.value - independent_score) < 1e-12
              and abs(independent_score - 6.2125) < 1e-12)

        # Synthetic history: fixed salary/rent plus seeded variable spending.
        simulated = Store(scratch / 'forecast.db')
        cash_id = simulated.add_account(Account('Fictional checking', AccountType.CHECKING, 8000, reference_date))
        rng = np.random.default_rng(41)
        first = (pd.Timestamp(reference_date).to_period('M') - 12).start_time.date()
        data = []
        def row(d, description, amount, category):
            if d < reference_date:
                data.append(dict(date=d, description=description, amount=amount, category=category,
                                 account_id=cash_id, import_hash=f'forecast-{len(data)}'))
        for month in pd.period_range(first, reference_date, freq='M'):
            row(month.start_time.date(), 'PAYROLL', 4000, 'Income')
            row(month.start_time.date() + timedelta(days=1), 'RENT', -2000, 'Housing')
            row(month.start_time.date() + timedelta(days=6), 'UTILITIES', -200, 'Utilities')
            row(month.start_time.date() + timedelta(days=14), 'INSURANCE', -100, 'Insurance')
            row(month.start_time.date() + timedelta(days=19), 'CHARITY', -100, 'Charitable donation')
        for day in pd.date_range(first, reference_date - timedelta(days=1), freq='7D'):
            row(day.date(), 'GROCERIES', -round(float(rng.uniform(30, 75)), 2), 'Groceries')
            row(day.date(), 'DINING', -round(float(rng.uniform(80, 160)), 2), 'Dining')
        simulated.add_transactions(pd.DataFrame(data))
        last_month = (pd.Timestamp(reference_date).to_period('M') - 1).start_time.date()
        simulated.merge_recurring([
            RecurringItem('PAYROLL|+', 'PAYROLL', 'monthly', 4000, last_month, days_of_month=[1], kind='income'),
            RecurringItem('RENT|-', 'RENT', 'monthly', -2000, last_month + timedelta(days=1), days_of_month=[2])])
        base = run_forecast(simulated, 365, 600, Scenario(), seed=7)
        check('forecast begins at the stated fictional balance and date',
              base.dates[0] == reference_date and base.cash_det[0] == 8000)
        check('forecast percentiles are ordered at every date',
              all(np.all(base.cash_pct[a] <= base.cash_pct[b]) for a, b in [(10, 25), (25, 50), (50, 75), (75, 90)]))
        simulated.save_spending_plan(new_values, 550)
        changed = run_forecast(simulated, 365, 600, Scenario(follow_goal_plan=True), seed=7)
        repeated = run_forecast(simulated, 365, 600, Scenario(follow_goal_plan=True), seed=7)
        check('repeated seeded plan run reproduces the saved percentile curves',
              np.array_equal(changed.cash_pct[50], repeated.cash_pct[50]))
        check('saved budget mode is actually applied', changed.spending_plan_applied)
        fields = np.column_stack([np.arange(366), *(base.cash_pct[p] for p in [10, 25, 50, 75, 90]),
                                  base.cash_det, changed.cash_pct[50]])
        np.savetxt(generated / 'forecast.dat', fields, fmt='%.8f', header='day p10 p25 p50 p75 p90 deterministic planned', comments='')
        values.update(BaseFinal=f'{base.cash_pct[50][-1]:,.2f}', PlanFinal=f'{changed.cash_pct[50][-1]:,.2f}',
                      ForecastWeeks=str(base.history_weeks), ForecastPaths='600')
        simulated.close()

        # A diagnostic comparison is retained even when it identifies a model gap.
        credit = Store(scratch / 'credit.db')
        credit.add_account(Account('Checking', AccountType.CHECKING, 1000, reference_date))
        credit.add_account(Account('Card', AccountType.CREDIT, -250, reference_date))
        stats = headline_stats(credit)
        result = run_forecast(credit, 31, 20, Scenario(), seed=7)
        actual_nw = float(result.networth_det[0])
        recorded_nw = float(stats['networth'])
        gap = round(actual_nw - recorded_nw, 2)
        qualification = dict(check='opening net worth with a $250 credit-card debt',
                             ledger_networth=recorded_nw, forecast_networth=actual_nw, difference=gap)
        if abs(gap) > .005:
            credit_text = (r'\begin{notebox}{Recorded credit-card qualification}' + '\n'
                + f'The retained probe uses checking of \\money{{1,000}} and card debt of \\money{{250}}. '
                + f'Ledger net worth is \\money{{{recorded_nw:,.2f}}}, while the inspected forecast begins at \\money{{{actual_nw:,.2f}}}. '
                + 'The difference is a recorded implementation limitation, not an additional asset. '
                + 'Compare the forecast opening values with Accounts when carrying card debt; do not interpret this offset as available wealth. '
                + 'The probe is retained for maintainers to review after model changes.\\end{notebox}\n')
        else:
            credit_text = ('The retained credit-card probe agrees at the opening point: checking of '
                r'\money{1,000} less card debt of \money{250} gives net worth of \money{750}. '
                'This opening check does not establish revolving-card interest or repayment accuracy.\n')
        (generated / 'credit_qualification.tex').write_text(credit_text, encoding='utf-8')
        credit.close()

        try:
            commit = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = 'unavailable'
        macros = dict(AppVersion=__version__, SourceCommit=commit, ExampleDate=reference_date.isoformat(), **values)
        (generated / 'version.tex').write_text(''.join(f'\\newcommand{{\\{k}}}{{{tex(v)}}}\n' for k, v in macros.items()), encoding='utf-8')
        (generated / 'results.json').write_text(json.dumps(dict(values=values, checks=checks,
            qualification=qualification), indent=2) + '\n', encoding='utf-8')
        files = {str(p.relative_to(generated)): digest(p.read_bytes()) for p in generated.rglob('*') if p.is_file()}
        provenance = dict(schema=1, generated_at=datetime.now(ZoneInfo('America/New_York')).isoformat(),
            example_date=reference_date.isoformat(), application_version=__version__, commit=commit,
            source_snapshot='Temporary copy of individually read working-tree Python files; hashes identify the exact bytes.',
            python=sys.version, dependencies={p: importlib.metadata.version(p) for p in ['numpy', 'pandas']},
            source_hashes=source_hashes, generator_sha256=digest(Path(__file__).read_bytes()),
            output_hashes=files, independent_checks=checks, diagnostic_qualifications=[qualification],
            data='Fictional temporary SQLite databases only; no user database or GUI opened.')
        (generated / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
        stage = MANUAL / '.generated-stage'
        backup = MANUAL / '.generated-backup'
        if stage.exists() or backup.exists():
            raise RuntimeError('A previous staged build needs inspection; refusing to overwrite it.')
        shutil.copytree(generated, stage)
        current = MANUAL / 'generated'
        if current.exists():
            current.rename(backup)
        try:
            stage.rename(current)
        except Exception:
            if backup.exists():
                backup.rename(current)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        print(f'Published {len(checks)} example checks and {len(provenance["diagnostic_qualifications"])} diagnostic comparison(s).')
        print(f'Recorded source: Doug {__version__}, commit {commit}, {len(source_hashes)} file hashes.')


if __name__ == '__main__':
    main()
