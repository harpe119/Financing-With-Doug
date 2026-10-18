"""What was taken off the screen, and that nothing it did was lost."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QEvent, QSettings
from PyQt6.QtWidgets import QApplication, QLabel

from finfore.demo import load_demo
from finfore.store import Store
from finfore.ui import charts, prefs, theme
from finfore.ui.main_window import MainWindow
from test_usability import desktop_test_app


class DistillFixture:
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'distill.db')
        self.prefs = patch.object(prefs, 'app_settings', lambda: QSettings(
            str(Path(self.tmp.name) / 'prefs.ini'), QSettings.Format.IniFormat))
        self.prefs.start()
        load_demo(self.store, 'balanced', checkpoints=2)
        self.win = MainWindow(self.store)
        self.win.resize(1280, 900)
        self.win.show()
        if not self.win.simple_mode:
            self.win._toggle_mode()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.store.close()
        self.prefs.stop()
        self.tmp.cleanup()

    def settle(self):
        for _ in range(3):
            self.app.processEvents()


class HomeAndSpendingTests(DistillFixture, unittest.TestCase):
    def test_home_has_one_chart_and_the_two_supporting_charts_moved_to_spending(self):
        self.win.show_page('Dashboard')
        dash = self.win.pages[0]
        for gone in ('flow_card', 'cat_card', 'plot_flow', 'plot_cat', 'split', '_cats'):
            self.assertFalse(hasattr(dash, gone), gone)
        self.assertEqual([p for p in dash.findChildren(charts.ChartPlot)], [dash.plot_hist])
        self.win.show_page('Spending')
        spending = self.win.current_page()
        self.assertEqual(sorted(p.accessibleName() for p in spending.charts.findChildren(charts.ChartPlot)),
                         ['Monthly cash flow chart', 'Spending by category chart'])

    def test_the_spending_charts_start_folded_and_a_redraw_does_not_unfold_them(self):
        self.win.show_page('Spending')
        spending = self.win.current_page()
        sc = spending.charts
        self.assertFalse(sc.flow_card.expanded)
        self.assertFalse(sc.cat_card.expanded)
        self.assertFalse(sc.plot_flow.isVisibleTo(spending))
        sc.flow_card.set_expanded(True)
        self.assertTrue(sc.plot_flow.isVisibleTo(spending))
        self.assertFalse(sc.plot_cat.isVisibleTo(spending))
        spending.refresh()
        self.assertTrue(sc.flow_card.expanded)
        self.assertFalse(sc.cat_card.expanded)

    def test_the_moved_charts_keep_their_summaries_and_show_their_axes(self):
        self.win.show_page('Spending')
        spending = self.win.current_page()
        spending.refresh()
        sc = spending.charts
        self.assertIn('came in', sc.flow_summary)
        self.assertEqual(sc.plot_flow.accessibleDescription(), sc.flow_summary)
        self.assertIn('is the biggest category', sc.cat_summary)
        sc.flow_card.set_expanded(True)
        sc.cat_card.set_expanded(True)
        self.settle()
        for plot in (sc.plot_flow, sc.plot_cat):
            self.assertEqual(plot.plotItem.geometry().height(), plot.viewport().height())
            self.assertGreaterEqual(plot.height(), 170)

    def test_an_opened_card_is_tall_enough_to_hold_its_chart(self):
        # The cash flow card also holds a wrapping key, and a layout that wraps
        # once opened it with the plot squeezed to nothing.
        self.win.show_page('Spending')
        spending = self.win.current_page()
        sc = spending.charts
        sc.flow_card.set_expanded(True)
        sc.cat_card.set_expanded(True)
        self.settle()
        for card, plot in ((sc.flow_card, sc.plot_flow), (sc.cat_card, sc.plot_cat)):
            inside = card.rect().contains(plot.mapTo(card, plot.rect().bottomLeft()))
            self.assertTrue(inside, card.header.text())
            self.assertGreaterEqual(plot.height(), charts.AXIS_ALLOWANCE + 170)

    def test_a_category_bar_still_leads_to_that_categorys_transactions(self):
        self.win.show_page('Spending')
        spending = self.win.current_page()
        spending.refresh()
        name = spending.charts._cats.index[0]
        spending.charts._open_category(name)
        page = self.win.current_page()
        self.assertEqual(type(page).__name__, 'TransactionsPage')
        self.assertEqual(page.f_category.currentData(), name)
        self.assertEqual(page.f_since.currentData(), 90)

    def test_the_spending_charts_clear_when_there_is_nothing_to_draw(self):
        empty = Store(Path(self.tmp.name) / 'empty.db')
        try:
            self.win.show_page('Spending')
            spending = self.win.current_page()
            spending.store = empty
            spending.refresh()
            self.assertEqual(spending.charts.flow_summary, '')
            self.assertEqual(spending.charts.cat_summary, '')
        finally:
            spending.store = self.store
            empty.close()

    def test_the_header_no_longer_carries_a_tagline(self):
        texts = [label.text() for label in self.win.findChildren(QLabel)]
        self.assertFalse(any('local finance' in text for text in texts))
        self.assertTrue(any(text.startswith('v') and text[1:2].isdigit() for text in texts))


class ExplainMenuTests(DistillFixture, unittest.TestCase):
    def test_forecast_has_one_explain_control_holding_the_video_and_the_written_explanation(self):
        self.win.show_page('Projection')
        fc = self.win.current_page()
        self.assertEqual(fc.explain_btn.text(), 'Explain')
        self.assertIsNotNone(fc.explain_btn.menu())
        self.assertEqual([a.text() for a in fc.explain_btn.menu().actions()],
                         ['Watch the video', 'How this works'])
        from finfore.ui.widgets import InfoDialog
        opened = []
        with patch.object(InfoDialog, 'exec', lambda dialog: opened.append(dialog) or 0):
            fc.explain_btn.actions_by_key['how'].trigger()
        self.assertEqual(len(opened), 1)

    def test_track_record_folds_three_buttons_into_one_menu_and_each_item_works(self):
        self.win.show_page('Progress')
        page = self.win.current_page()
        self.assertEqual([a.text() for a in page.explain_btn.menu().actions()],
                         ['What is a checkpoint?', 'Watch the video', 'How this works'])
        from finfore.ui import video
        from finfore.ui.widgets import InfoDialog
        opened = []
        with patch.object(InfoDialog, 'exec', lambda dialog: opened.append(dialog.windowTitle()) or 0), \
                patch.object(video, 'play', return_value=True) as player:
            page.explain_btn.actions_by_key['checkpoint'].trigger()
            page.explain_btn.actions_by_key['how'].trigger()
            page.video_action.trigger()
        self.assertEqual(len(opened), 2)
        player.assert_called_once_with(page, kind='progress')


class DataQualityLineTests(DistillFixture, unittest.TestCase):
    def test_data_quality_is_one_strip_with_the_detail_in_its_tooltip_and_the_dialog(self):
        self.win.show_page('Projection')
        card = self.win.current_page().data_quality
        self.assertNotEqual(type(card).__mro__[1].__name__, 'Card')
        self.assertTrue(card.status.text().startswith('Data quality: '))
        self.assertTrue(card.detail.isHidden())
        self.assertIn('These checks cannot establish', card.status.toolTip())
        self.assertLess(card.height(), 70)
        if card.report.concerns:
            self.assertEqual(card.status.objectName(), 'ledgerWarning')
        card.report = None
        card.clear()
        self.assertEqual(card.status.objectName(), 'qualityOk')
        self.assertIn('not checked yet', card.status.text())

    def test_the_warning_box_keeps_its_own_padding_after_the_quiet_line_was_shown(self):
        # Renaming a label used to leave the quiet line's padding on the
        # warning box, so its text sat against the border.
        previous = self.app.styleSheet()
        theme.apply(self.app)
        self.addCleanup(self.app.setStyleSheet, previous)
        self.win.show_page('Projection')
        card = self.win.current_page().data_quality
        card.clear()
        self.settle()
        quiet = card.status.contentsRect().left()
        card.refresh(self.store)
        self.settle()
        self.assertTrue(card.report.concerns)
        self.assertEqual(card.status.objectName(), 'ledgerWarning')
        self.assertGreaterEqual(card.status.contentsRect().left(), 10)
        self.assertGreater(card.status.contentsRect().left(), quiet)

    def test_a_clean_report_reads_quietly(self):
        from finfore.data_quality import QualityReport
        self.win.show_page('Projection')
        card = self.win.current_page().data_quality
        clean = QualityReport(as_of=__import__('datetime').date.today(), checks=(),
                              first_transaction=None, last_transaction=None)
        with patch('finfore.ui.data_quality.inspect_data_quality', return_value=clean):
            card.refresh(self.store)
        self.assertEqual(card.status.objectName(), 'qualityOk')
        self.assertIn('no gaps flagged', card.status.text())


class GoalsSimpleViewTests(DistillFixture, unittest.TestCase):
    def goals(self):
        self.win.show_page('Goals')
        self.settle()
        return self.win.current_page()

    def test_simple_view_trims_the_ladder_to_what_a_relative_reads(self):
        page = self.goals()
        self.assertTrue(page.table.isColumnHidden(2))
        self.assertTrue(page.table.isColumnHidden(5))
        for column in (0, 1, 3, 4, 6):
            self.assertFalse(page.table.isColumnHidden(column), column)
        self.assertTrue(page.policy_pair.isHidden())

    def test_everything_view_shows_every_column_and_the_split(self):
        self.win._toggle_mode()
        page = self.goals()
        self.assertTrue(all(not page.table.isColumnHidden(c) for c in range(page.table.columnCount())))
        self.assertFalse(page.policy_pair.isHidden())

    def test_a_split_that_is_not_the_default_is_never_hidden_in_simple_view(self):
        page = self.goals()
        page.policy.setCurrentIndex(page.policy.findData('weighted'))
        self.assertFalse(page.policy_pair.isHidden())
        page.policy.setCurrentIndex(page.policy.findData('custom'))
        self.assertFalse(page.table.isColumnHidden(5))
        page.policy.setCurrentIndex(page.policy.findData('focus'))
        self.assertTrue(page.policy_pair.isHidden())
        self.assertTrue(page.table.isColumnHidden(5))

    def test_the_hidden_columns_still_hold_their_values_for_the_selection_note(self):
        page = self.goals()
        self.assertTrue(page.table.item(0, 2).text())
        self.assertIn('/mo', page.payment_note.text())


if __name__ == '__main__':
    unittest.main()
