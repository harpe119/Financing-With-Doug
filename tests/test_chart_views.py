"""Axis stability through content changes and explicit reset interactions."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from finfore.ui import charts


class ChartViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.plot = charts.new_plot()
        self.plot.resize(640, 300)
        self.plot.show()
        self.app.processEvents()

    def tearDown(self):
        self.plot.close()

    def assertRangeEqual(self, actual, expected):
        for axis, target in zip(actual, expected):
            for value, desired in zip(axis, target):
                self.assertAlmostEqual(value, desired, places=5)

    def test_redraw_with_shorter_data_keeps_pan_and_zoom_then_resets(self):
        p = self.plot
        charts.lock_view(p, 0, 300 * charts.DAY, [[100, 200]])
        vb = p.getViewBox()
        vb.setRange(xRange=(200 * charts.DAY, 250 * charts.DAY),
                    yRange=(-500, 1000), padding=0)
        before = vb.viewRange()
        p.clear()
        charts.unlock_view(p)
        charts.line(p, [0, 30 * charts.DAY], [3000, 4000], "white")
        charts.lock_view(p, 0, 30 * charts.DAY, [[3000, 4000]])
        self.app.processEvents()
        self.assertRangeEqual(vb.viewRange(), before)
        QTest.mouseClick(p.reset_axes_btn, Qt.MouseButton.LeftButton)
        self.assertLess(vb.viewRange()[0][1], 50 * charts.DAY)
        self.assertGreater(vb.viewRange()[1][0], 2000)
        self.assertFalse(any(vb.autoRangeEnabled()))

    def test_layers_update_reset_target_without_moving_axes(self):
        p = self.plot
        charts.lock_view(p, 0, 300 * charts.DAY, [[-1000, 2000]])
        before = p.viewRange()
        charts.reframe(p, [[100, 200]], [], 90)
        self.assertRangeEqual(p.viewRange(), before)
        p.reset_axes_btn.click()
        self.assertAlmostEqual(p.viewRange()[0][1] - p.viewRange()[0][0],
                               90 * charts.DAY)
        self.assertGreater(p.viewRange()[1][0], 0)
        charts.show_window(p, 30)
        self.assertAlmostEqual(p.viewRange()[0][1] - p.viewRange()[0][0],
                               30 * charts.DAY)

    def test_auto_fitted_charts_keep_view_and_reset_to_new_data(self):
        p = self.plot
        charts.line(p, [0, 1], [0, 100], "white")
        p.autoRange()
        p.setRange(xRange=(0.2, 0.8), yRange=(20, 30), padding=0)
        before = p.viewRange()
        p.clear()
        charts.line(p, [10, 20], [1000, 2000], "white")
        p.autoRange()
        self.app.processEvents()
        self.assertRangeEqual(p.viewRange(), before)
        p.reset_axes_btn.click()
        self.assertLess(p.viewRange()[1][0], 1000)
        self.assertGreater(p.viewRange()[1][1], 2000)

    def test_new_store_or_chart_type_gets_a_fresh_frame(self):
        p = self.plot
        charts.lock_view(p, 0, 300 * charts.DAY, [[100, 200]])
        p.forget_view()
        self.assertFalse(p.reset_axes_btn.isEnabled())
        p.clear()
        charts.lock_view(p, 0, 30 * charts.DAY, [[3000, 4000]])
        self.assertGreater(p.viewRange()[1][0], 2000)
        self.assertTrue(p.reset_axes_btn.isEnabled())
        # The native button has a separate strip above the plot viewport.
        self.assertLess(p.reset_axes_btn.geometry().bottom(), p.viewport().y())


    def test_bounded_chart_cannot_leave_its_band_or_run_past_its_last_point(self):
        p = self.plot
        charts.lock_view(p, 0, 300 * charts.DAY, [[0, 100]], include_y=[0, 100],
                         slack_right=False, y_limits=(-8, 108), min_y_span=10)
        vb = p.getViewBox()
        self.assertRangeEqual([vb.viewRange()[1]], [(-8, 108)])
        for requested in ((500, 600), (-900, -800), (-1000, 1000)):
            vb.setRange(yRange=requested, padding=0)
            low, high = vb.viewRange()[1]
            self.assertGreaterEqual(low, -8 - 1e-6, requested)
            self.assertLessEqual(high, 108 + 1e-6, requested)
        vb.setRange(yRange=(50, 51), padding=0)
        low, high = vb.viewRange()[1]
        self.assertGreaterEqual(high - low, 10 - 1e-6)
        x_end = vb.viewRange()[0][1]
        vb.setRange(xRange=(250 * charts.DAY, 900 * charts.DAY), padding=0)
        self.assertLessEqual(vb.viewRange()[0][1], x_end + 1e-6)
        self.assertLess(x_end, 330 * charts.DAY)

    def test_money_charts_keep_unbounded_y_and_the_slack_to_the_right(self):
        p = self.plot
        charts.lock_view(p, 0, 300 * charts.DAY, [[100, 200]])
        vb = p.getViewBox()
        vb.setRange(yRange=(5000, 6000), padding=0)
        self.assertGreater(vb.viewRange()[1][0], 4000)
        vb.setRange(xRange=(250 * charts.DAY, 500 * charts.DAY), padding=0)
        self.assertGreater(vb.viewRange()[0][1], 330 * charts.DAY)


class ChartAxisTests(unittest.TestCase):
    """Every chart must show its bottom axis, and its money labels must be distinct."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_plot_is_laid_out_to_its_viewport_so_the_bottom_axis_is_visible(self):
        plot = charts.new_plot()
        plot.resize(640, 300)
        plot.show()
        self.app.processEvents()
        for size in ((640, 300), (500, 260), (900, 420)):
            plot.resize(*size)
            self.app.processEvents()
            self.assertEqual(plot.plotItem.geometry().height(), plot.viewport().height(), size)
            self.assertLessEqual(plot.getAxis('bottom').geometry().bottom(),
                                 plot.viewport().height(), size)
        plot.close()

    def test_every_chart_in_the_program_shows_its_bottom_axis(self):
        import tempfile
        from unittest.mock import patch
        from PyQt6.QtCore import QEvent, QSettings
        from finfore.demo import load_demo
        from finfore.store import Store
        from finfore.ui import prefs
        from finfore.ui.main_window import MainWindow
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                prefs, 'app_settings', lambda: QSettings(
                    os.path.join(tmp, 'p.ini'), QSettings.Format.IniFormat)):
            store = Store(Path(tmp) / 'axes.db')
            load_demo(store, 'balanced', checkpoints=2)
            win = MainWindow(store)
            win.simple_mode = True
            win._toggle_mode()
            win.resize(1280, 900)
            win.show()
            seen = 0
            for name in ('Dashboard', 'Projection', 'Progress', 'Goals'):
                win.show_page(name)
                for _ in range(3):
                    self.app.processEvents()
                for plot in win.current_page().findChildren(charts.ChartPlot):
                    if not plot.isVisible():
                        continue
                    seen += 1
                    self.assertEqual(plot.plotItem.geometry().height(), plot.viewport().height(),
                                     f'{name}: plot not sized to its viewport')
                    axis = plot.getAxis('bottom')
                    self.assertLessEqual(axis.geometry().bottom(), plot.viewport().height(), name)
            self.assertGreaterEqual(seen, 4)
            win.close()
            win.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            store.close()

    def test_money_ticks_get_more_precision_only_when_they_would_repeat(self):
        axis = charts.MoneyAxis(orientation='left')
        tight = axis.tickStrings([20000, 20500, 21000, 21500, 22000], 1, 500)
        self.assertEqual(len(set(tight)), len(tight))
        self.assertIn('$20.5k', tight)
        wide = axis.tickStrings([0, 10000, 20000, 30000], 1, 10000)
        self.assertEqual(wide, ['$0', '$10k', '$20k', '$30k'])
        fine = axis.tickStrings([20000, 20050, 20100], 1, 50)
        self.assertEqual(len(set(fine)), 3)
        self.assertEqual(axis.tickStrings([-5000, 0, 5000], 1, 5000), ['-$5.0k', '$0', '$5.0k'])

    def test_labels_for_reference_lines_keep_their_short_form(self):
        self.assertEqual(charts._short(12345), '$12k')
        self.assertEqual(charts._short(1500), '$1.5k')
        self.assertEqual(charts._short(-250), '-$250')
        self.assertEqual(charts._short(2_500_000), '$2.5M')


if __name__ == "__main__":
    unittest.main()
