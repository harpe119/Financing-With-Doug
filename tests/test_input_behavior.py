"""Scrolling over entries must scroll the page without changing any input."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QDate, QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QComboBox, QDateEdit, QDoubleSpinBox,
                             QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from finfore.ui.input_behavior import install_entry_wheel_guard
from finfore.ui.widgets import MoneySpinBox, PopupDateEdit


class EntryWheelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_entry_wheel_guard(cls.app)

    def setUp(self):
        self.scroll = QScrollArea()
        self.content = QWidget()
        self.layout = QVBoxLayout(self.content)
        self.scroll.setWidget(self.content)
        self.scroll.setWidgetResizable(True)
        self.scroll.resize(400, 300)

    def tearDown(self):
        self.scroll.close()

    def show_entry(self, entry):
        self.layout.addWidget(entry)
        self.layout.addSpacing(1500)
        self.scroll.show()
        self.app.processEvents()

    def wheel(self, target, *, pixels=False):
        pos = target.rect().center()
        event = QWheelEvent(
            QPointF(pos), QPointF(target.mapToGlobal(pos)),
            QPoint(0, -40) if pixels else QPoint(),
            QPoint() if pixels else QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(target, event)
        self.app.processEvents()

    def test_numeric_date_dropdown_and_slider_ignore_wheel_even_with_focus(self):
        for kind in (QSpinBox, QDoubleSpinBox, MoneySpinBox, QDateEdit,
                     QComboBox, QSlider):
            for focused in (False, True):
                with self.subTest(control=kind.__name__, focused=focused):
                    entry = kind()
                    if isinstance(entry, QComboBox):
                        entry.addItems(["One", "Two", "Three"])
                        entry.setCurrentIndex(1)
                        read = entry.currentIndex
                        signal = entry.currentIndexChanged
                    elif isinstance(entry, QDateEdit):
                        entry.setDate(QDate(2026, 10, 17))
                        read = entry.date
                        signal = entry.dateChanged
                    else:
                        entry.setRange(0, 100)
                        entry.setValue(50)
                        read = entry.value
                        signal = entry.valueChanged
                    self.layout.insertWidget(0, entry)
                    self.content.setMinimumHeight(2000)
                    self.scroll.show()
                    self.app.processEvents()
                    self.scroll.verticalScrollBar().setValue(0)
                    entry.setFocus() if focused else entry.clearFocus()
                    before = read()
                    changes = []
                    signal.connect(changes.append)
                    self.wheel(entry)
                    self.assertEqual(read(), before)
                    self.assertEqual(changes, [])
                    self.assertGreater(self.scroll.verticalScrollBar().value(), 0)
                    self.layout.removeWidget(entry)
                    entry.deleteLater()

    def test_wheel_over_embedded_text_editor_also_scrolls_page(self):
        entry = MoneySpinBox()
        entry.setValue(50)
        self.show_entry(entry)
        entry.setFocus()
        self.wheel(entry.lineEdit())
        self.assertEqual(entry.value(), 50)
        self.assertGreater(self.scroll.verticalScrollBar().value(), 0)

    def test_pixel_scrolling_does_not_change_entry(self):
        entry = QSpinBox()
        entry.setValue(50)
        self.show_entry(entry)
        self.wheel(entry, pixels=True)
        self.assertEqual(entry.value(), 50)

    def test_keyboard_and_click_editing_still_work(self):
        entry = QSpinBox()
        entry.setValue(50)
        self.show_entry(entry)
        entry.setFocus()
        QTest.keyClick(entry, Qt.Key.Key_Up)
        self.assertEqual(entry.value(), 51)
        entry.selectAll()
        QTest.keyClicks(entry, "72")
        QTest.keyClick(entry, Qt.Key.Key_Return)
        self.assertEqual(entry.value(), 72)
        option = entry.style().subControlRect(
            entry.style().ComplexControl.CC_SpinBox,
            self.spin_option(entry), entry.style().SubControl.SC_SpinBoxUp, entry)
        QTest.mouseClick(entry, Qt.MouseButton.LeftButton, pos=option.center())
        self.assertEqual(entry.value(), 73)

    def test_open_dropdown_list_can_still_scroll_without_selecting(self):
        entry = QComboBox()
        entry.addItems([f"Choice {i}" for i in range(100)])
        self.show_entry(entry)
        entry.showPopup()
        self.app.processEvents()
        self.wheel(entry.view().viewport())
        self.assertEqual(entry.currentIndex(), 0)
        self.assertGreater(entry.view().verticalScrollBar().value(), 0)
        entry.hidePopup()

    @staticmethod
    def spin_option(entry):
        from PyQt6.QtWidgets import QStyleOptionSpinBox
        option = QStyleOptionSpinBox()
        entry.initStyleOption(option)
        return option


def run_tests():
    return unittest.TextTestRunner().run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EntryWheelTests))


class PopupDateEditTests(unittest.TestCase):
    """The calendar has to be reachable by a click on the date and by keyboard."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_entry_wheel_guard(cls.app)

    def setUp(self):
        self.edit = PopupDateEdit()
        self.edit.setDisplayFormat("MMM yyyy")
        self.edit.resize(260, 34)
        self.edit.show()
        self.app.processEvents()

    def tearDown(self):
        self.edit.calendarWidget().parentWidget().hide()
        self.edit.close()

    def popup_open(self):
        self.app.processEvents()
        return self.edit.calendarWidget().parentWidget().isVisible()

    def test_clicking_the_date_itself_opens_the_calendar(self):
        self.assertFalse(self.popup_open())
        QTest.mouseClick(self.edit.lineEdit(), Qt.MouseButton.LeftButton)
        self.assertTrue(self.popup_open())

    def test_the_arrow_still_opens_it(self):
        QTest.mouseClick(self.edit, Qt.MouseButton.LeftButton,
                         pos=QPoint(self.edit.width() - 10, self.edit.height() // 2))
        self.assertTrue(self.popup_open())

    def test_alt_down_and_f4_open_it_and_plain_arrows_only_change_the_date(self):
        self.edit.setDate(QDate(2030, 5, 1))
        self.edit.setFocus()
        QTest.keyClick(self.edit, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        self.assertTrue(self.popup_open())
        self.assertEqual(self.edit.date(), QDate(2030, 5, 1))
        self.edit.calendarWidget().parentWidget().hide()
        QTest.keyClick(self.edit, Qt.Key.Key_F4)
        self.assertTrue(self.popup_open())
        self.edit.calendarWidget().parentWidget().hide()
        QTest.keyClick(self.edit, Qt.Key.Key_Up)
        self.assertFalse(self.popup_open())
        self.assertNotEqual(self.edit.date(), QDate(2030, 5, 1))

    def test_a_disabled_field_does_not_open_and_the_wheel_still_does_nothing(self):
        self.edit.setEnabled(False)
        QTest.mouseClick(self.edit.lineEdit(), Qt.MouseButton.LeftButton)
        self.assertFalse(self.popup_open())
        self.edit.setEnabled(True)
        before = self.edit.date()
        event = QWheelEvent(QPointF(5, 5), QPointF(self.edit.mapToGlobal(QPoint(5, 5))),
                            QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(self.edit, event)
        self.assertEqual(self.edit.date(), before)

    def test_picking_a_day_in_the_popup_sets_the_date_and_closes_it(self):
        self.edit.setDate(QDate(2030, 5, 1))
        QTest.mouseClick(self.edit.lineEdit(), Qt.MouseButton.LeftButton)
        calendar = self.edit.calendarWidget()
        calendar.setSelectedDate(QDate(2031, 2, 14))
        calendar.activated.emit(QDate(2031, 2, 14))
        self.app.processEvents()
        self.assertEqual(self.edit.date(), QDate(2031, 2, 14))
        self.assertFalse(self.popup_open())


if __name__ == "__main__":
    unittest.main()
