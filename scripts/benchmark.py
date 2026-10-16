"""Measure desktop work with fictional data and temporary preferences only.

Run from the repository: python scripts/benchmark.py --rounds 3 --json /tmp/times.json
Use QT_QPA_PLATFORM=offscreen on a machine without a desktop session.
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication

from finfore.demo import _rows_for
from finfore.forecast import run_forecast
from finfore.learning import apply_chain, full_chain
from finfore.models import Account, AccountType, Goal, Scenario
from finfore.pipeline import refresh_derived
from finfore.progress import capture
from finfore.store import Store
from finfore.ui import prefs, theme
from finfore.ui.main_window import MainWindow


def fixture(path, months, cash_accounts):
    store = Store(path)
    for k in range(cash_accounts):
        aid = store.add_account(Account(f'Fictional checking {k+1}', AccountType.CHECKING, 6000))
        frame = _rows_for(months, date.today(), income=4800, rent=1800,
                         groceries=450, dining=350, shopping=250, seed=7+k, account_id=aid)
        store.add_transactions(apply_chain(frame, full_chain(store)))
    store.add_account(Account('Fictional auto loan', AccountType.LOAN, 14000,
                             apr=.06, monthly_payment=350))
    store.add_account(Account('Fictional investment', AccountType.INVESTMENT, 45000,
                             monthly_contribution=300))
    store.add_goal(Goal('Fictional holiday', 4500, 'savings'))
    store.add_goal(Goal('Fictional reserve', 30000, 'cash'))
    refresh_derived(store, checkpoint=False)
    # Two ordered checkpoints exercise Progress rather than its empty state.
    cp = capture(store, with_forecast=False)
    cp.when = date.today() - timedelta(days=180)
    store.con.execute('DELETE FROM checkpoints')
    store.con.commit()
    store.add_checkpoint(cp)
    cp.when = date.today()
    store.add_checkpoint(cp)
    return store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--months', type=int, default=72)
    parser.add_argument('--cash-accounts', type=int, default=4)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--profile', type=Path, help='Optional cProfile output for all timed actions')
    args = parser.parse_args()
    if not 12 <= args.months <= 240 or not 1 <= args.cash_accounts <= 10 or not 1 <= args.rounds <= 10:
        parser.error('Use 12–240 months, 1–10 cash accounts and 1–10 rounds.')
    app = QApplication.instance() or QApplication([])
    if sys.platform == 'win32' and not QFontDatabase.families():
        for filename in ('segoeui.ttf', 'segoeuib.ttf', 'consola.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / filename))
    theme.apply(app)
    times = {}
    profile = cProfile.Profile() if args.profile else None

    def measure(name, action):
        if profile:
            profile.enable()
        start = time.perf_counter()
        action()
        app.processEvents()
        elapsed = time.perf_counter() - start
        if profile:
            profile.disable()
        times.setdefault(name, []).append(elapsed)
        print(f'{name}: {elapsed * 1000:.1f} ms', flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        settings = QSettings(str(Path(tmp) / 'prefs.ini'), QSettings.Format.IniFormat)
        with patch.object(prefs, 'app_settings', lambda: settings):
            store = fixture(Path(tmp) / 'fictional.db', args.months, args.cash_accounts)
            count = len(store.transactions())
            # Fixture construction must not warm the app's read cache.
            store.close()
            store = Store(Path(tmp) / 'fictional.db')
            window = []
            def launch():
                window.append(MainWindow(store))
                window[0].show()
            measure('startup', launch)
            win = window[0]
            pages = {
                'Dashboard': win.pages[0], 'Accounts': win.pages[1],
                'Transactions': win.pages[2], 'Recurring': win.pages[3],
                'Spending': win.pages[4], 'Goals': win.pages[5],
                'Projection': win.pages[6], 'Progress': win.pages[7],
                'Calendar': win.pages[8], 'Plan': win.pages[9],
            }
            def visit(name):
                # Select the requested sub-tab before navigation, so a link to
                # Progress does not also calculate Projection on the way in.
                page = pages[name]
                row = win._destinations[name][0]
                section = win.sections[row]
                if hasattr(section, 'setCurrentWidget'):
                    section.blockSignals(True)
                    section.setCurrentWidget(page)
                    section.blockSignals(False)
                previous = win.nav.currentRow()
                win.show_page(name)
                if previous == row:
                    win.refresh_current()
                if name == 'Projection' and (page._res is None or page._refresh_needed):
                    raise RuntimeError('The forecast failed; this is not a valid timing.')
                if name == 'Goals' and page.plan_note.text().startswith('Could not build a plan'):
                    raise RuntimeError(page.plan_note.text())
            for iteration in range(args.rounds):
                for name in ('Transactions', 'Accounts', 'Calendar', 'Recurring', 'Plan',
                             'Goals', 'Projection', 'Progress', 'Spending', 'Dashboard'):
                    measure(name, lambda n=name: visit(n))
                fp = pages['Projection']
                visit('Projection')
                for value in (105 + iteration * 10, 110 + iteration * 10):
                    def change(v=value):
                        fp.income_slider.slider.setValue(v)
                        fp.run()
                        if fp._res is None or fp._refresh_needed:
                            raise RuntimeError('The forecast change failed; this is not a valid timing.')
                    measure('forecast_change', change)
                measure('forecast_5year', lambda: run_forecast(store, 1826, 400,
                                                              Scenario(income_multiplier=1.05)))
            win.close()
            store.close()
    result = dict(fictional=True, transactions=count, months=args.months,
                  cash_accounts=args.cash_accounts, rounds=args.rounds,
                  platform=platform.platform(), python=platform.python_version(),
                  qt_platform=app.platformName(), seconds=times,
                  median_seconds={k: statistics.median(v) for k, v in times.items()})
    print(json.dumps(result['median_seconds'], indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    if profile:
        profile.dump_stats(str(args.profile))


if __name__ == '__main__':
    main()
