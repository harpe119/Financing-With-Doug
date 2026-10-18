"""The keyboard can reach everything, and no headline sounds surer than its data."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pandas as pd
from PyQt6.QtCore import QDate, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QScrollArea, QTableWidget

from finfore.data_quality import data_freshness
from finfore.models import Account, AccountType
from finfore.ui import charts, theme
from test_onboard import Fixture


def tx_rows(account_id, start, end, tag):
    """A small, regular ledger: a paycheck every other Friday-ish and some spending."""
    rows, d, i = [], start, 0
    while d <= end:
        rows.append(dict(account_id=account_id, date=d, description=f'GROCER {i}',
                         amount=-60.0, category='Groceries', import_hash=f'{tag}-g{i}'))
        if i % 14 == 0:
            rows.append(dict(account_id=account_id, date=d, description='ACME PAYROLL',
                             amount=2000.0, category='Income', import_hash=f'{tag}-p{i}'))
        d += timedelta(days=1)
        i += 1
    return pd.DataFrame(rows)


class LedgerFixture(Fixture):
    """An empty database with one checking account, ready for a ledger of chosen age."""

    def add_ledger(self, days_ago_start, days_ago_end, balance=3000.0, tag='L'):
        today = date.today()
        aid = self.store.add_account(Account('Checking', AccountType.CHECKING,
                                             balance=balance, balance_date=today))
        self.store.add_transactions(tx_rows(
            aid, today - timedelta(days=days_ago_start), today - timedelta(days=days_ago_end), tag))
        self.win.refresh_current()
        self.settle()


# ---------------------------------------------------------------- keyboard ----------
class TabOrderTests(Fixture, unittest.TestCase):
    demo = True

    def tab_from_rail(self, presses=6):
        self.win.nav.setFocus()
        self.settle()
        seen = []
        for _ in range(presses):
            QTest.keyClick(QApplication.focusWidget() or self.win, Qt.Key.Key_Tab)
            self.settle()
            seen.append(QApplication.focusWidget())
        return seen

    def test_tab_leaves_the_rail_on_the_two_sections_that_show_no_tab_bar_in_simple_view(self):
        self.win.mode_simple.click()
        for name in ('Balances', 'Projection'):
            self.go(name)
            seen = self.tab_from_rail()
            self.assertTrue(any(w is not self.win.nav for w in seen), f'{name}: stuck on the rail')
        self.assertEqual(self.win.accounts_tabs.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(self.win.forecast_tabs.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_sections_with_a_tab_bar_keep_it_in_the_tab_order(self):
        self.win.mode_all.click()
        self.assertEqual(self.win.accounts_tabs.focusPolicy(), Qt.FocusPolicy.TabFocus)
        self.go('Balances')
        seen = self.tab_from_rail(2)
        self.assertEqual(type(seen[0]).__name__, 'QTabBar')

    def test_every_table_that_used_to_swallow_tab_lets_it_through(self):
        self.win.mode_all.click()
        for name, attr in (('Calendar', 'table'), ('Recurring', 'table'), ('Progress', 'table'),
                           ('Monthly budget', 'table'), ('Monthly budget', 'category_table')):
            table = getattr(self.go(name), attr)
            self.assertFalse(table.tabKeyNavigation(), f'{name}.{attr}')

    def test_tab_moves_on_from_the_detected_bills_table(self):
        self.win.mode_all.click()
        page = self.go('Recurring')
        page.table.setFocus()
        self.settle()
        for _ in range(3):
            QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
            self.settle()
        focus = QApplication.focusWidget()
        self.assertFalse(focus is page.table or page.table.isAncestorOf(focus))

    def test_a_chart_is_not_a_tab_stop_that_does_nothing(self):
        self.win.mode_all.click()
        for name in ('Overview', 'Projection', 'Progress', 'Spending'):
            page = self.go(name)
            for plot in page.findChildren(charts.ChartPlot):
                self.assertEqual(plot.focusPolicy(), Qt.FocusPolicy.NoFocus, name)

    def test_a_page_scroll_area_is_named_and_not_a_second_stop_when_it_holds_controls(self):
        self.win.mode_all.click()
        for name in ('Overview', 'Goals', 'Projection', 'Monthly budget'):
            page = self.go(name)
            areas = page.findChildren(QScrollArea)
            self.assertTrue(areas, name)
            for area in areas:
                self.assertTrue(area.accessibleName().endswith((' page', ' panel')), name)
                self.assertEqual(area.focusPolicy(), Qt.FocusPolicy.NoFocus, name)


class FocusAndDisabledLookTests(Fixture, unittest.TestCase):
    def test_the_stylesheet_gives_focus_a_look_where_the_selected_state_hid_it(self):
        for rule in ('QSlider::handle:horizontal:focus', 'QPushButton#range:checked:focus',
                     'QTabBar::tab:focus', 'QSpinBox:disabled'):
            self.assertIn(rule, theme.QSS)

    def test_a_disabled_amount_box_no_longer_looks_like_an_enabled_one(self):
        from finfore.ui.account_dialog import _money_spin
        box = _money_spin(0, 1e6)
        box.setValue(1234.56)
        box.resize(160, 34)
        box.show()
        self.settle()
        enabled = box.grab().toImage()
        box.setEnabled(False)
        self.settle()
        disabled = box.grab().toImage()
        differing = sum(enabled.pixel(x, y) != disabled.pixel(x, y)
                        for x in range(enabled.width()) for y in range(enabled.height()))
        self.assertGreater(differing, 200)
        box.deleteLater()

    def test_disabled_text_is_one_token(self):
        self.assertEqual(theme.DISABLED_TEXT, '#7A8798')
        self.assertGreaterEqual(theme.QSS.count(theme.DISABLED_TEXT), 2)


class AccessibleNameTests(Fixture, unittest.TestCase):
    demo = True

    def test_what_a_screen_reader_would_have_met_unnamed_now_has_a_name(self):
        self.win.mode_all.click()
        cal = self.go('Calendar')
        self.assertEqual(cal.account_pick.accessibleName(), 'Show scheduled items for')
        self.assertEqual(cal.table.accessibleName(), 'Scheduled items')
        from PyQt6.QtWidgets import QToolButton
        arrows = {b.objectName(): b.accessibleName() for b in cal.calendar.findChildren(QToolButton)}
        self.assertEqual(arrows['qt_calendar_prevmonth'], 'Previous month')
        self.assertEqual(arrows['qt_calendar_nextmonth'], 'Next month')
        self.assertEqual(self.go('Recurring').table.accessibleName(), 'Detected bills and pay')
        progress = self.go('Progress')
        self.assertEqual(progress.pick.accessibleName(), 'Compare against')
        self.assertEqual(progress.table.accessibleName(), 'Checkpoints')
        plan = self.go('Monthly budget')
        self.assertEqual((plan.table.accessibleName(), plan.category_table.accessibleName()),
                         ('Monthly budget lines', 'Spending categories'))
        fc = self.go('Projection')
        self.assertEqual(fc.view_pick.accessibleName(), 'Chart to show')
        self.assertEqual(fc.plot_cash.accessibleName(), 'Cash projection chart')


# ------------------------------------------------------------------ thin data -------
class FreshnessTests(unittest.TestCase):
    def frame(self, first, last):
        days = pd.date_range(first, last, freq='D')
        return pd.DataFrame({'date': days})

    def test_under_four_weeks_is_thin(self):
        today = date(2026, 10, 17)
        fresh = data_freshness(self.frame(date(2026, 10, 1), today), today)
        self.assertTrue(fresh.thin)
        self.assertFalse(fresh.stale)
        self.assertEqual(fresh.span_days, 17)

    def test_a_ledger_that_ended_over_a_month_ago_is_stale(self):
        today = date(2026, 10, 17)
        fresh = data_freshness(self.frame(date(2026, 3, 1), date(2026, 7, 17)), today)
        self.assertTrue(fresh.stale)
        self.assertFalse(fresh.thin)
        self.assertEqual(fresh.age_days, 92)

    def test_thirty_days_is_still_fresh_and_a_demo_is_never_stale(self):
        today = date(2026, 10, 17)
        old = self.frame(date(2026, 3, 1), date(2026, 7, 17))
        self.assertFalse(data_freshness(self.frame(date(2026, 3, 1), today - timedelta(days=30)), today).stale)
        self.assertFalse(data_freshness(old, today, demo=True).stale)

    def test_no_transactions_is_thin_and_has_no_last_date(self):
        fresh = data_freshness(pd.DataFrame({'date': []}))
        self.assertTrue(fresh.thin)
        self.assertIsNone(fresh.last)


class HomeVerdictTests(LedgerFixture, unittest.TestCase):
    def verdict(self):
        self.go('Overview')
        return self.win.current_page()

    def test_ten_days_of_statements_do_not_earn_an_on_track(self):
        self.add_ledger(9, 0)
        page = self.verdict()
        self.assertEqual(page.verdict.text(), 'Too early to say where your cash is headed.')
        self.assertIn('Only 10 days of statements', page.verdict_detail.text())
        self.assertEqual(page.verdict_btn.text(), 'Import more history…')
        with patch.object(self.win, 'open_import') as opened:
            page.verdict_btn.click()
        opened.assert_called_once()
        self.assertEqual(page.verdict_band.property('caution'), 'true')

    def test_old_statements_qualify_the_reassurance_and_say_how_old(self):
        self.add_ledger(160, 45)
        page = self.verdict()
        text = page.verdict.text()
        self.assertTrue(text.startswith('Going by older statements, '), text)
        self.assertIn('Based on statements through', page.verdict_detail.text())
        self.assertIn('45 days ago', page.verdict_detail.text())
        self.assertEqual(page.verdict_band.property('caution'), 'true')

    def test_fresh_statements_get_the_plain_verdict(self):
        self.add_ledger(160, 0)
        page = self.verdict()
        self.assertNotIn('older statements', page.verdict.text())
        self.assertNotIn('Too early', page.verdict.text())
        self.assertNotIn('Based on statements through', page.verdict_detail.text())

    def test_a_demo_is_not_called_stale(self):
        self.add_ledger(160, 45)
        self.win.demo_key = 'balanced'
        self.win.refresh_current()
        page = self.verdict()
        self.assertNotIn('older statements', page.verdict.text())
        self.assertNotIn('Based on statements through', page.verdict_detail.text())

    def test_bad_news_is_never_held_back_for_thin_data(self):
        self.add_ledger(9, 0, balance=-4000.0)
        page = self.verdict()
        self.assertNotEqual(page.verdict.text(), 'Too early to say where your cash is headed.')


class ForecastHeadlineTests(LedgerFixture, unittest.TestCase):
    def test_stale_statements_are_named_under_the_headline(self):
        self.add_ledger(160, 45)
        page = self.go('Projection')
        self.assertIn('Based on statements through', page.risk.text())
        self.assertIn('45 days ago', page.risk.text())

    def test_fresh_statements_have_no_such_line(self):
        self.add_ledger(160, 0)
        page = self.go('Projection')
        self.assertNotIn('Based on statements through', page.risk.text())

    def test_a_thin_ledger_is_neutral_not_teal(self):
        self.add_ledger(9, 0)
        page = self.go('Projection')
        self.assertEqual(page.risk.text(), 'Too early to say where your cash is headed.')
        self.assertIn(theme.TEXT, page.risk.styleSheet())
        self.assertNotIn(theme.ACCENT, page.risk.styleSheet())


class AxisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_usability import desktop_test_app
        cls.app = desktop_test_app()

    def test_a_flat_series_gets_a_frame_that_ticks_can_tell_apart(self):
        lo, hi = charts._y_bounds(np.array([3200.0] * 6), [], 0.08)
        self.assertGreater(hi - lo, 30)
        self.assertLess(lo, 3200)
        self.assertGreater(hi, 3200)

    def test_dense_ticks_are_spelled_out_the_same_way_at_every_level(self):
        axis = charts.MoneyAxis(orientation='left')
        major = axis.tickStrings([3190.0, 3200.0, 3210.0], 1, 10)
        minor = axis.tickStrings([3195.0, 3205.0, 3215.0], 1, 5)
        self.assertEqual(major, ['$3,190', '$3,200', '$3,210'])
        self.assertEqual(minor, ['$3,195', '$3,205', '$3,215'])

    def test_a_label_that_would_repeat_is_printed_once(self):
        axis = charts.MoneyAxis(orientation='left')
        labels = axis.tickStrings([5.0, 5.0, 5.0], 1, 0.0)
        self.assertEqual([label for label in labels if label], ['$5'])

    def test_ordinary_ticks_keep_their_short_form(self):
        axis = charts.MoneyAxis(orientation='left')
        self.assertEqual(axis.tickStrings([20000.0, 30000.0, 40000.0], 1, 10000), ['$20k', '$30k', '$40k'])


class TrackRecordCarriedForwardTests(Fixture, unittest.TestCase):
    demo = True

    def legend(self):
        self.win.mode_all.click()
        page = self.go('Progress')
        return [label.text for _, label in page.plot.plotItem.legend.items]

    def test_after_the_last_transaction_the_line_is_dashed_and_says_it_is_carried_forward(self):
        # the window is not in demo mode, and the demo ledger ends months before today
        names = self.legend()
        self.assertTrue(any(n.startswith('Carried forward · no transactions since') for n in names), names)

    def test_a_demo_is_measured_against_its_own_last_day(self):
        self.win.demo_key = 'balanced'
        names = self.legend()
        self.assertFalse(any('Carried forward' in n for n in names), names)
        self.assertIn('What actually happened', names)


# ---------------------------------------------------------------- asked for ---------
class CashFlowAxisTests(Fixture, unittest.TestCase):
    demo = True

    def flow(self):
        page = self.go('Spending')
        page.charts.flow_card.set_expanded(True)
        self.settle()
        return page.charts.plot_flow

    def test_only_time_moves_on_the_monthly_cash_flow_chart(self):
        vb = self.flow().getViewBox()
        self.assertEqual(list(vb.state['mouseEnabled']), [True, False])
        (_, _), (y_lo, y_hi) = vb.viewRange()
        limits = vb.state['limits']
        self.assertAlmostEqual(limits['yLimits'][0], y_lo, places=3)
        self.assertAlmostEqual(limits['yLimits'][1], y_hi, places=3)
        self.assertAlmostEqual(limits['yRange'][0], y_hi - y_lo, places=3)
        self.assertAlmostEqual(limits['yRange'][1], y_hi - y_lo, places=3)

    def test_the_vertical_range_cannot_be_pushed_around_even_in_code(self):
        vb = self.flow().getViewBox()
        before = vb.viewRange()[1]
        vb.setYRange(-1e6, 1e6, padding=0)
        vb.setYRange(10, 11, padding=0)
        after = vb.viewRange()[1]
        self.assertAlmostEqual(before[0], after[0], places=3)
        self.assertAlmostEqual(before[1], after[1], places=3)

    def test_the_hint_says_what_the_chart_does_now(self):
        plot = self.flow()
        self.assertIn('move through time', plot.toolTip())
        self.assertNotIn('stretch', plot.toolTip())

    def test_other_money_charts_keep_their_free_vertical_axis(self):
        self.win.mode_all.click()
        plot = self.go('Overview').plot_hist
        self.assertEqual(list(plot.getViewBox().state['mouseEnabled']), [True, True])

    def test_a_chart_that_was_locked_can_be_unlocked_for_reuse(self):
        plot = self.flow()
        charts.unlock_view(plot)
        self.assertEqual(list(plot.getViewBox().state['mouseEnabled']), [True, True])


class HomeOrderTests(Fixture, unittest.TestCase):
    demo = True

    def test_the_chart_comes_before_what_needs_a_look(self):
        dash = self.go('Overview')
        top = lambda w: w.mapTo(dash.body, dash.body.rect().topLeft()).y()
        self.assertLess(top(dash.hist_card), top(dash.needs_look))
        self.assertLess(top(dash.stats_w), top(dash.hist_card))

    def test_the_chart_starts_higher_on_the_page_than_it_did_under_the_strip(self):
        dash = self.go('Overview')
        top = lambda w: w.mapTo(dash.body, dash.body.rect().topLeft()).y()
        figures_bottom = top(dash.stats_w) + dash.stats_w.height()
        self.assertLessEqual(top(dash.hist_card) - figures_bottom, 24)


class CalendarTodayTests(Fixture, unittest.TestCase):
    def painted(self, calendar, day):
        image = QImage(48, 48, QImage.Format.Format_ARGB32)
        image.fill(QColor(theme.FIELD))
        painter = QPainter(image)
        calendar.paintCell(painter, QRect(0, 0, 48, 48), day)
        painter.end()
        return image

    def ringed(self, image):
        ring = QColor(theme.TEXT)
        return sum(1 for x in range(48) for y in (3, 4, 43, 44)
                   if QColor(image.pixel(x, y)).red() > 200 and abs(QColor(image.pixel(x, y)).red() - ring.red()) < 40)

    def test_today_is_ringed_and_other_days_are_not(self):
        cal = self.go('Calendar').calendar
        today = QDate.currentDate()
        self.assertGreater(self.ringed(self.painted(cal, today)), 20)
        self.assertEqual(self.ringed(self.painted(cal, today.addDays(1))), 0)
        self.assertEqual(self.ringed(self.painted(cal, today.addDays(-1))), 0)

    def test_the_ring_survives_selecting_another_day(self):
        page = self.go('Calendar')
        page.calendar.setSelectedDate(QDate.currentDate().addDays(3))
        self.settle()
        self.assertGreater(self.ringed(self.painted(page.calendar, QDate.currentDate())), 20)

    def test_the_legend_says_what_the_ring_is(self):
        from test_clarify import visible_texts
        texts = ' '.join(visible_texts(self.go('Calendar')))
        self.assertIn('The ringed day is today', texts)


if __name__ == '__main__':
    unittest.main()
