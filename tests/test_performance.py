"""Correctness checks for reused calculations; timings live in the benchmark."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from finfore.cache import ReadCache
from finfore.forecast import run_forecast
from finfore.history import _double_postings, networth_history
from finfore.models import Account, AccountType, OneOffEvent, Scenario
from finfore.store import Store


class CalculationCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'fictional.db')
        self.aid = self.store.add_account(Account('Fictional checking', AccountType.CHECKING, 5000))
        self.store.add_transactions(pd.DataFrame([
            dict(account_id=self.aid, date=date.today() - timedelta(days=7*i),
                 description='Fictional groceries', amount=-40-i, category='Groceries', import_hash=f'row{i}')
            for i in range(1, 20)]))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_ledger_read_is_reused_and_callers_cannot_change_cached_data(self):
        queries = []
        self.store.con.set_trace_callback(queries.append)
        tx = self.store.transactions()
        tx.loc[0, 'amount'] = -99999
        tx['temporary'] = 1
        again = self.store.transactions(account_id=None)
        self.assertNotIn('temporary', again)
        self.assertNotEqual(again.amount.iloc[0], -99999)
        self.assertEqual(sum('FROM transactions t LEFT JOIN' in q for q in queries), 1)

    def test_direct_sql_commit_and_rollback_invalidate_results(self):
        original = self.store.transactions()
        tid = int(original.id.iloc[0])
        self.store.con.execute('UPDATE transactions SET amount=-123 WHERE id=?', (tid,))
        self.assertEqual(self.store.transactions().amount.iloc[0], -123)
        self.store.con.rollback()
        pd.testing.assert_frame_equal(self.store.transactions(), original)
        self.store.con.execute('UPDATE transactions SET amount=-456 WHERE id=?', (tid,))
        self.store.con.commit()
        self.assertEqual(self.store.transactions().amount.iloc[0], -456)

    def test_other_connection_commit_and_midnight_expire_calculations(self):
        self.store.transactions()
        with closing(sqlite3.connect(self.store.path)) as writer, writer:
            writer.execute('UPDATE transactions SET category=?', ('Dining',))
        self.assertTrue(self.store.transactions().category.eq('Dining').all())
        calls = []
        build = lambda: calls.append(1) or len(calls)
        self.assertEqual(self.store.cached('clock-test', build), 1)
        self.assertEqual(self.store.cached('clock-test', build), 1)
        with patch('finfore.store.date') as clock:
            clock.today.return_value = date.today() + timedelta(days=1)
            self.assertEqual(self.store.cached('clock-test', build), 2)

    def test_savepoint_rollback_never_reuses_uncommitted_reads(self):
        original = self.store.transactions()
        self.store.con.execute('SAVEPOINT temporary_edit')
        self.store.con.execute('UPDATE transactions SET amount=-777')
        self.assertTrue(self.store.transactions().amount.eq(-777).all())
        self.store.con.execute('ROLLBACK TO temporary_edit')
        pd.testing.assert_frame_equal(self.store.transactions(), original)
        self.store.con.execute('RELEASE temporary_edit')
        pd.testing.assert_frame_equal(self.store.transactions(), original)

    def test_undo_redo_and_separate_ledgers_do_not_share_results(self):
        before = networth_history(self.store)
        self.store.enable_undo()
        account = self.store.account(self.aid)
        account.balance += 250
        self.store.update_account(account)
        after = networth_history(self.store)
        self.assertAlmostEqual(after.cash.iloc[-1] - before.cash.iloc[-1], 250)
        self.store.undo.undo()
        pd.testing.assert_frame_equal(networth_history(self.store), before)
        self.store.undo.redo()
        pd.testing.assert_frame_equal(networth_history(self.store), after)
        other = Store(Path(self.tmp.name) / 'other.db')
        try:
            self.assertTrue(other.transactions().empty)
            self.assertEqual(networth_history(other).cash.iloc[-1], 0)
        finally:
            other.close()

    def test_forecast_keys_include_mutable_scenario_and_events(self):
        scenario = Scenario()
        baseline = run_forecast(self.store, 90, 80, scenario)
        # The returned arrays and nested dataclasses belong to the caller.
        saved = baseline.cash_pct[50].copy()
        baseline.cash_pct[50][:] = -99999
        np.testing.assert_array_equal(run_forecast(self.store, 90, 80, scenario).cash_pct[50], saved)
        scenario.events.append(OneOffEvent('Fictional purchase', date.today()+timedelta(days=1), -300))
        changed = run_forecast(self.store, 90, 80, scenario)
        np.testing.assert_allclose(changed.cash_det[1:] + 300,
                                   run_forecast(self.store, 90, 80).cash_det[1:])
        scenario.events.clear()
        eid = self.store.add_event(OneOffEvent('Fictional bill', date.today()+timedelta(days=2), -100))
        np.testing.assert_allclose(run_forecast(self.store, 90, 80).cash_pct[50][2:] + 100, saved[2:])
        self.store.delete_event(eid)
        np.testing.assert_array_equal(run_forecast(self.store, 90, 80).cash_pct[50], saved)

    def test_cache_capacity_failures_and_reads_that_write(self):
        cache = ReadCache(entries=2, budget=1024)
        stamp = [1]
        calls = []
        def get(key):
            return cache.get(stamp[0], key, lambda: calls.append(key) or [key], lambda: stamp[0])
        get('a'); get('b'); get('a'); get('c'); get('b')
        self.assertEqual(calls, ['a', 'b', 'c', 'b'])
        with self.assertRaises(ValueError):
            cache.get(1, 'failed', lambda: (_ for _ in ()).throw(ValueError()), lambda: 1)
        self.assertEqual(cache.get(1, 'failed', lambda: 'recovered', lambda: 1), 'recovered')
        def write():
            stamp[0] += 1
            return 'changed'
        cache.get(1, 'write', write, lambda: stamp[0])
        self.assertEqual(cache.get(2, 'write', lambda: 'fresh', lambda: 2), 'fresh')
        tiny = ReadCache(budget=1)
        count = []
        for _ in range(2):
            tiny.get(1, 'large', lambda: count.append(1) or [1,2,3], lambda: 1)
        self.assertEqual(len(count), 2)

    def test_duplicate_candidates_match_independent_grouped_reference(self):
        frame = pd.DataFrame(dict(date=pd.to_datetime(['2026-01-01']*4 + ['2026-01-02']*3),
            amount=[-10,-10,-10,20,30,30,30], description=['A','B','B','C','D','D','D']))
        self.assertEqual(_double_postings(frame), (2, -20.0))
        self.assertEqual(_double_postings(frame.iloc[:0]), (0, 0.0))

    def test_progress_resampling_preserves_seeded_reference_intervals(self):
        from finfore.progress import compare_periods
        cases = [
            (8, 8, .2316, -78.25480931459187, 16.380007631540607),
            (13, 17, .76195, -34.05349916596022, 47.69980694370531),
            (37, 280, .10315, -40.84768590637806, -.36322410444315556),
            (100, 20, .89845, -44.932393078896624, 46.60354689388055),
        ]
        # References from the original per-replicate implementation, including
        # unequal period lengths. Keep the statistical sample and seed fixed.
        for nb, na, p_value, low, high in cases:
            with self.subTest(before=nb, after=na):
                rng = np.random.default_rng(123)
                result = compare_periods(rng.lognormal(4.5, .6, nb),
                                         rng.lognormal(4.2, .8, na), seed=7)
                self.assertEqual(result.p_value, p_value)
                np.testing.assert_allclose([result.ci_low, result.ci_high], [low, high],
                                           rtol=1e-13, atol=1e-10)


def run_tests():
    return unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))


if __name__ == '__main__':
    unittest.main()
