"""Defects that only show at the window's smallest size, or between neighbouring controls."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QWidget

from test_onboard import Fixture


class SmallestWindow(Fixture):
    demo = True

    def setUp(self):
        super().setUp()
        self.win.resize(980, 620)
        self.win.mode_all.click()
        self.settle()


class SpendingLegendTests(SmallestWindow, unittest.TestCase):
    def test_the_donut_legend_fits_inside_its_card_at_the_minimum_width(self):
        page = self.go('Spending')
        card = page.donut.parentWidget()
        while card is not None and not (card.objectName() == 'card' and card.minimumWidth() > 0):
            card = card.parentWidget()
        self.assertIsNotNone(card)
        self.assertGreaterEqual(card.minimumWidth(), 404)
        legend_host = next(w for w in card.findChildren(QWidget) if w.width() == 210)
        right_edge = legend_host.mapTo(card, legend_host.rect().topRight()).x()
        self.assertLessEqual(right_edge, card.width())
        self.assertGreaterEqual(page.donut.width(), 150)


class CalendarFitTests(SmallestWindow, unittest.TestCase):
    def test_a_card_that_would_only_say_choose_an_account_is_not_shown(self):
        page = self.go('Calendar')
        self.assertTrue(page.balance.isHidden())
        index = next(i for i in range(page.account_pick.count()) if isinstance(page.account_pick.itemData(i), int))
        page.account_pick.setCurrentIndex(index)
        self.settle()
        self.assertFalse(page.balance.isHidden())
        self.assertNotIn('Choose an account', page.balance.sub.text())

    def test_the_table_drops_its_account_column_rather_than_hide_the_balance(self):
        page = self.go('Calendar')
        header = page.table.horizontalHeader()
        self.assertLess(page.table.viewport().width(), page.ACCOUNT_COLUMN_NEEDS)
        self.assertTrue(page.table.isColumnHidden(2))
        last = page.table.columnCount() - 1
        shown_right = header.sectionViewportPosition(last) + header.sectionSize(last)
        self.assertLessEqual(shown_right, page.table.viewport().width() + 1)
        self.assertEqual(page.table.horizontalHeaderItem(last).text(), 'Balance')

    def test_a_wide_window_brings_the_account_column_back_and_the_item_names_its_account(self):
        self.win.resize(1500, 900)
        page = self.go('Calendar')
        self.assertFalse(page.table.isColumnHidden(2))
        self.assertIn(' · ', page.table.item(0, 1).toolTip())

    def test_the_headers_say_what_each_amount_is_when_pointed_at(self):
        page = self.go('Calendar')
        self.assertEqual(page.table.horizontalHeaderItem(3).toolTip(), 'Scheduled money in')
        self.assertEqual(page.table.horizontalHeaderItem(4).toolTip(), 'Scheduled money out')


class StripAndButtonTests(SmallestWindow, unittest.TestCase):
    def test_the_data_quality_line_is_as_tall_as_its_text_and_not_a_box(self):
        page = self.go('Projection')
        card = page.data_quality
        self.assertLess(card.height(), 52)
        self.assertLessEqual(card.height(), card.sizeHint().height() + 2)

    def test_clear_checkpoints_is_as_compact_as_the_buttons_beside_it(self):
        page = self.go('Progress')
        self.assertEqual(page.clear_btn.objectName(), 'dangerSmall')
        self.assertEqual(page.clear_btn.height(), page.fill_btn.height())

    def test_delete_goal_is_a_compact_danger_button_without_a_local_override(self):
        page = self.go('Goals')
        self.assertEqual(page.delete_btn.objectName(), 'dangerSmall')
        self.assertEqual(page.delete_btn.styleSheet(), '')
        self.assertEqual(page.delete_btn.height(), page.edit_btn.height())


class HeadlineSizeTests(SmallestWindow, unittest.TestCase):
    def pixels(self, label):
        label.ensurePolished()
        return label.font().pixelSize()

    def test_the_line_that_is_a_pages_result_is_17px_wherever_it_appears(self):
        self.assertEqual(self.pixels(self.go('Dashboard').verdict), 17)
        self.assertEqual(self.pixels(self.go('Spending').means), 17)
        self.assertEqual(self.pixels(self.go('Monthly budget').verdict), 17)
        self.assertEqual(self.pixels(self.go('Projection').risk), 17)


class ForecastFirstRunTests(Fixture, unittest.TestCase):
    def test_the_first_run_video_button_is_named_like_the_explain_menu_item(self):
        page = self.go('Projection')
        self.assertEqual(page.start_video_btn.text(), 'Watch the video')
        self.assertEqual(page.explain_btn.actions_by_key['video'].text(), 'Watch the video')


if __name__ == '__main__':
    unittest.main()
