"""Independent schedule dates, cash impacts, migration and event editing."""
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from finfore.events import event_occurrences
from finfore.forecast import run_forecast, scheduled_flows
from finfore.calendar import upcoming_schedule
from finfore.models import Account, AccountType, OneOffEvent, Scenario
from finfore.store import Store


class EventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "events.db")
        self.today = date.today()
        self.store.add_account(Account("Checking", AccountType.CHECKING, 10000, self.today))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_month_end_is_anchored_and_does_not_drift(self):
        e = OneOffEvent("Purchase", date(2028, 1, 31), -125.75, repeat="monthly")
        self.assertEqual(event_occurrences(e, date(2028, 1, 1), date(2028, 5, 31)),
            [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31),
             date(2028, 4, 30), date(2028, 5, 31)])
        self.assertEqual(event_occurrences(e, date(2028, 2, 1), date(2028, 3, 30)),
                         [date(2028, 2, 29)])

    def test_yearly_leap_day_returns_in_the_next_leap_year(self):
        e = OneOffEvent("Annual fee", date(2024, 2, 29), -100, repeat="annual")
        self.assertEqual(event_occurrences(e, date(2025, 1, 1), date(2028, 12, 31)),
            [date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)])

    def test_quarter_and_half_year_use_calendar_months(self):
        e = OneOffEvent("Course", date(2026, 1, 31), -250, repeat="quarterly")
        self.assertEqual(event_occurrences(e, date(2026, 1, 1), date(2026, 12, 31)),
            [date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31), date(2026, 10, 31)])
        e.repeat = "semiannual"
        self.assertEqual(event_occurrences(e, date(2026, 1, 1), date(2027, 1, 31)),
            [date(2026, 1, 31), date(2026, 7, 31), date(2027, 1, 31)])

    def test_day_intervals_old_starts_and_inclusive_boundaries(self):
        for repeat, step in [("daily", 1), ("weekly", 7), ("biweekly", 14)]:
            with self.subTest(repeat=repeat):
                e = OneOffEvent("Purchase", date(2000, 1, 1), -10, repeat=repeat)
                first = e.when + timedelta(days=step*1400)
                self.assertEqual(event_occurrences(e, first, first+timedelta(days=step*2)),
                    [first, first+timedelta(days=step), first+timedelta(days=step*2)])
                self.assertEqual(event_occurrences(e, first+timedelta(days=1), first), [])

    def test_optional_end_includes_that_date_and_ignores_non_occurrence_dates(self):
        e = OneOffEvent("Purchase", date(2026, 1, 1), -10, repeat="weekly", until=date(2026, 1, 15))
        expected = [date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 15)]
        self.assertEqual(event_occurrences(e, e.when, date(2026, 2, 1)), expected)
        e.until = date(2026, 1, 14)
        self.assertEqual(event_occurrences(e, e.when, date(2026, 2, 1)), expected[:2])
        e.active = False
        self.assertEqual(event_occurrences(e, e.when, date(2026, 2, 1)), [])

    def test_once_is_not_repeated_and_disabled_scenario_events_stay_out(self):
        when = self.today + timedelta(days=3)
        e = OneOffEvent("Repair", when, -99.95)
        self.assertEqual(event_occurrences(e, self.today, when+timedelta(days=100)), [when])
        self.assertEqual(event_occurrences(e, when+timedelta(days=1), when+timedelta(days=100)), [])
        e.active = False
        self.assertEqual(scheduled_flows(self.store, self.today, when,
                                        Scenario(events=[e])), [])

    def test_all_cash_percentiles_and_net_worth_include_every_purchase(self):
        start = self.today + timedelta(days=1)
        end = start + timedelta(days=21)
        e = OneOffEvent("Weekly purchase", start, -123.45, repeat="weekly", until=end)
        base = run_forecast(self.store, 50, 40)
        eid = self.store.add_event(e)
        changed = run_forecast(self.store, 50, 40)
        expected_dates = [start+timedelta(days=7*i) for i in range(4)]
        deductions = np.array([-123.45*sum(t <= d for t in expected_dates) for d in base.dates])
        np.testing.assert_allclose(changed.cash_det-base.cash_det, deductions, atol=1e-9)
        np.testing.assert_allclose(changed.networth_det-base.networth_det, deductions, atol=1e-9)
        for p in base.cash_pct:
            np.testing.assert_allclose(changed.cash_pct[p]-base.cash_pct[p], deductions, atol=1e-9)
        flows = scheduled_flows(self.store, self.today+timedelta(days=1), self.today+timedelta(days=50))
        self.assertEqual([f.when for f in flows], expected_dates)
        self.assertTrue(all(f.event_id == eid and f.amount == -123.45 for f in flows))
        entries = upcoming_schedule(self.store, self.today+timedelta(days=50)).entries
        self.assertEqual([f.when for f in entries], expected_dates)
        self.assertTrue(all(f.account_id is None for f in entries))
        self.assertTrue(self.store.transactions().empty)
        self.assertEqual(self.store.recurring(), [])

    def test_scenario_only_repeating_income_is_applied_without_saving(self):
        start = self.today+timedelta(days=1)
        e = OneOffEvent("Side income", start, 50, repeat="biweekly")
        base = run_forecast(self.store, 30, 20)
        alt = run_forecast(self.store, 30, 20, Scenario(events=[e]))
        self.assertAlmostEqual(alt.cash_det[-1]-base.cash_det[-1], 150)
        self.assertEqual(self.store.events(), [])

    def test_past_start_only_applies_occurrences_after_the_balance_date(self):
        base = run_forecast(self.store, 14, 20)
        self.store.add_event(OneOffEvent("Ongoing purchase", self.today-timedelta(days=7),
                                        -50, repeat="weekly"))
        alt = run_forecast(self.store, 14, 20)
        self.assertAlmostEqual(alt.cash_det[0], base.cash_det[0])
        self.assertAlmostEqual(alt.cash_det[6], base.cash_det[6])
        self.assertAlmostEqual(alt.cash_det[7]-base.cash_det[7], -50)
        self.assertAlmostEqual(alt.cash_det[14]-base.cash_det[14], -100)

    def test_current_hypotheticals_do_not_change_historical_refits(self):
        origin = self.today-timedelta(days=30)
        base = run_forecast(self.store, 30, 20, as_of=origin)
        self.store.add_event(OneOffEvent("Hypothetical subscription", origin,
                                       -100, repeat="weekly"))
        after = run_forecast(self.store, 30, 20, as_of=origin)
        np.testing.assert_array_equal(base.cash_det, after.cash_det)

    def test_edit_updates_one_row_retains_inactive_state_and_is_undoable(self):
        original = OneOffEvent("Bill", self.today+timedelta(days=1), -20, active=False)
        eid = self.store.add_event(original)
        self.store.enable_undo()
        end = original.when+timedelta(days=90)
        self.store.update_event(eid, label="Regular purchase", amount=-45.67,
                                repeat="monthly", until=end)
        edited = replace(original, label="Regular purchase", amount=-45.67,
                         repeat="monthly", until=end)
        self.assertEqual(self.store.events(), [edited])
        self.assertFalse(self.store.events()[0].active)
        self.store.undo.undo()
        self.assertEqual(self.store.events(), [original])
        self.store.undo.redo()
        self.store.close()
        self.store = Store(self.root/"events.db")
        self.assertEqual(self.store.events(), [edited])
        self.store.update_event(eid, repeat="once", until=None)
        self.assertEqual(self.store.events()[0].repeat, "once")
        self.assertIsNone(self.store.events()[0].until)

    def test_invalid_schedules_are_rejected_without_partial_writes(self):
        valid = OneOffEvent("Purchase", self.today, -10)
        for invalid in [replace(valid, repeat="bad"), replace(valid, amount=float("nan")),
                        replace(valid, amount=0), replace(valid, label=" "),
                        replace(valid, repeat="weekly", until=self.today-timedelta(days=1))]:
            with self.subTest(event=invalid):
                with self.assertRaises(ValueError):
                    self.store.add_events([valid, invalid])
                self.assertEqual(self.store.events(), [])
        eid = self.store.add_event(valid)
        with self.assertRaises(ValueError):
            self.store.update_event(eid, repeat="weekly", until=self.today-timedelta(days=1))
        self.assertEqual(self.store.events(), [valid])

    def test_legacy_schema_migrates_existing_events_as_once(self):
        path = self.root/"legacy.db"
        with closing(sqlite3.connect(path)) as con, con:
            con.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, label TEXT, when_date TEXT, amount REAL, active INTEGER)")
            con.execute("INSERT INTO events VALUES(1,'Repair','2026-10-20',-100,1)")
        old = Store(path)
        try:
            self.assertEqual(old.events(), [OneOffEvent("Repair", date(2026,10,20), -100, id=1)])
        finally:
            old.close()


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(EventTests))


if __name__ == "__main__":
    unittest.main()
