"""Forecast in Simple view opens on the answer; the what-if controls are one click away."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pyqtgraph as pg

from test_harden import LedgerFixture
from test_onboard import Fixture


def floor_labels(plot):
    return [item.label.textItem.toPlainText() for item in plot.plotItem.items
            if isinstance(item, pg.InfiniteLine) and item.label is not None]


class DrawerTests(Fixture, unittest.TestCase):
    demo = True

    def forecast(self, simple=True):
        (self.win.mode_simple if simple else self.win.mode_all).click()
        self.settle()
        return self.go('Projection')

    def test_simple_view_opens_on_the_answer_with_one_button_for_what_if(self):
        page = self.forecast()
        self.assertFalse(page.control_scroll.isVisibleTo(page))
        self.assertTrue(page.try_btn.isVisibleTo(page))
        self.assertEqual(page.try_btn.text(), 'Try a change')
        self.assertFalse(page.run_btn.isVisibleTo(page))
        self.assertTrue(page.risk.isVisibleTo(page))
        self.assertGreater(page.chart_scroll.width(), 900)

    def test_the_button_opens_and_closes_the_drawer(self):
        page = self.forecast()
        page.try_btn.click()
        self.settle()
        self.assertTrue(page.control_scroll.isVisibleTo(page))
        self.assertEqual(page.try_btn.text(), 'Hide changes')
        self.assertTrue(page.income_slider.isVisibleTo(page))
        self.assertTrue(page.reset_btn.isVisibleTo(page))
        self.assertFalse(page.run_btn.isVisibleTo(page))
        page.try_btn.click()
        self.settle()
        self.assertFalse(page.control_scroll.isVisibleTo(page))
        self.assertEqual(page.try_btn.text(), 'Try a change')

    def test_everything_view_keeps_the_column_and_the_refresh_button(self):
        page = self.forecast(simple=False)
        self.assertTrue(page.control_scroll.isVisibleTo(page))
        self.assertFalse(page.try_btn.isVisibleTo(page))
        self.assertTrue(page.run_btn.isVisibleTo(page))

    def test_going_back_to_everything_closes_the_drawer_state(self):
        page = self.forecast()
        page.try_btn.click()
        self.win.mode_all.click()
        self.settle()
        self.assertFalse(page.try_btn.isChecked())
        self.assertTrue(page.control_scroll.isVisibleTo(page))

    def test_open_controls_opens_the_drawer_in_simple_view_and_changes_nothing_otherwise(self):
        page = self.forecast()
        page.open_controls()
        self.assertTrue(page.control_scroll.isVisibleTo(page))
        page = self.forecast(simple=False)
        page.open_controls()
        self.assertTrue(page.control_scroll.isVisibleTo(page))

    def test_the_drawer_offers_the_three_biggest_categories_and_never_hides_a_changed_one(self):
        page = self.forecast(simple=False)
        everything = len(page._cat_sliders)
        self.assertGreater(everything, 3)
        page._cat_sliders[4].slider.setValue(80)
        self.settle()
        self.win.mode_simple.click()
        self.settle()
        page = self.go('Projection')
        page.try_btn.click()
        self.settle()
        visible = [i for i, row in enumerate(page._cat_sliders) if row.isVisibleTo(page)]
        self.assertEqual(visible, [0, 1, 2, 4])
        page._cat_sliders[4].slider.setValue(100)
        self.win.refresh_current()
        self.settle()
        self.assertEqual([i for i, row in enumerate(page._cat_sliders) if row.isVisibleTo(page)], [0, 1, 2])

    def test_the_starter_buffer_label_is_left_off_simple_views_chart(self):
        simple = floor_labels(self.forecast().plot_cash)
        self.assertFalse(any('starter buffer' in label for label in simple), simple)
        self.assertTrue(any('debt below this' in label for label in simple), simple)
        everything = floor_labels(self.forecast(simple=False).plot_cash)
        self.assertTrue(any('starter buffer' in label for label in everything), everything)

    def test_the_forecast_pages_own_account_button_uses_the_same_dialog(self):
        with patch.object(self.win, 'add_account') as dialog:
            self.win.open_account()
        dialog.assert_called_once()


class VerdictLinkTests(LedgerFixture, unittest.TestCase):
    def test_see_what_could_change_this_opens_the_what_if_drawer(self):
        self.win.mode_simple.click()
        self.add_ledger(160, 0, balance=-4000.0)
        dash = self.go('Overview')
        self.assertEqual(dash.verdict_btn.text(), 'See what could change this')
        dash.verdict_btn.click()
        self.settle()
        page = self.win.current_page()
        self.assertEqual(type(page).__name__, 'ForecastPage')
        self.assertTrue(page.control_scroll.isVisibleTo(page))

    def test_see_the_forecast_stays_closed(self):
        self.win.mode_simple.click()
        self.add_ledger(160, 0)
        dash = self.go('Overview')
        self.assertEqual(dash.verdict_btn.text(), 'See the forecast')
        dash.verdict_btn.click()
        self.settle()
        page = self.win.current_page()
        self.assertEqual(type(page).__name__, 'ForecastPage')
        self.assertFalse(page.control_scroll.isVisibleTo(page))


if __name__ == '__main__':
    unittest.main()
