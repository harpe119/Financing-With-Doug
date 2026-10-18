"""Goal selection, keyboard traversal, literal names and recoverable failures."""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtGui import QTextDocument
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QBoxLayout, QDialog, QLabel, QMessageBox,
                             QPushButton, QScrollArea)

from finfore.models import Account, AccountType, Goal
from finfore.store import Store
from finfore.ui import prefs
from finfore.ui.goal_funding import GoalFundingDialog
from finfore.ui.goals_page import GoalDialog
from finfore.ui.main_window import MainWindow
from test_goal_funding_map import FundingFixture
from test_usability import desktop_test_app


class GoalHardeningTests(FundingFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        super().setUp()
        self.prefs_patch = patch.object(prefs, 'app_settings', lambda:
            QSettings(str(Path(self.temp.name) / 'prefs.ini'), QSettings.Format.IniFormat))
        self.prefs_patch.start()
        self.win = MainWindow(self.store)
        self.win.show()
        self.win.show_page('Goals')
        self.app.processEvents()
        self.page = self.win.current_page()

    def tearDown(self):
        self.win.close()
        QTest.qWait(1)
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.prefs_patch.stop()
        super().tearDown()

    def test_selection_is_shared_and_survives_reorder_refresh_and_undo(self):
        page = self.page
        page.funding_map.select_goal(self.gids[2])
        self.assertEqual(page._selected_key(), f'goal:{self.gids[2]}')
        page._move(-1)
        self.assertEqual(page._selected_key(), f'goal:{self.gids[2]}')
        self.assertEqual(page.funding_map.selected_goal, self.gids[2])
        self.store.undo.undo()
        page.refresh()
        self.assertEqual(page._selected_key(), f'goal:{self.gids[2]}')
        page.table.selectRow(page._keys.index(f'goal:{self.gids[1]}'))
        self.assertEqual(page.funding_map.selected_goal, self.gids[1])
        self.assertIn('Funded goal', page.funding_map.selection_note.text())

    def test_clear_highlight_and_removed_selection_do_not_target_another_goal(self):
        page = self.page
        page.funding_map.clear_btn.click()
        page.refresh()
        self.assertIsNone(page._selected_key())
        self.assertFalse(page.edit_btn.isEnabled())
        self.assertFalse(page.funding_map.record_btn.isEnabled())
        page.funding_map.select_goal(self.gids[2])
        self.store.delete_goal(self.gids[2])
        page.refresh()
        self.assertIsNone(page._selected_key())
        self.assertIsNone(page.funding_map.selected_goal)
        self.assertFalse(page.delete_btn.isEnabled())

    def test_loan_and_milestone_selection_clear_only_the_savings_highlight(self):
        loan = self.store.add_account(Account('Loan', AccountType.LOAN, 1000, self.today, monthly_payment=100))
        milestone = self.store.add_goal(Goal('Household cash', 5000, 'cash'))
        self.page.refresh()
        for key in (f'loan:{loan}', f'goal:{milestone}'):
            self.page.table.selectRow(self.page._keys.index(key))
            self.assertEqual(self.page._selected_key(), key)
            self.assertIsNone(self.page.funding_map.selected_goal)
            self.assertFalse(self.page.funding_map.record_btn.isEnabled())
            self.page.refresh()
            self.assertEqual(self.page._selected_key(), key)

    def test_database_switch_does_not_reuse_the_previous_goal_selection(self):
        self.page.funding_map.select_goal(self.gids[2])
        other = Store(Path(self.temp.name) / 'other.db')
        try:
            gid = other.add_goal(Goal('Other household', 100, 'savings'))
            self.page.store = other
            self.page.refresh()
            self.assertEqual(self.page._selected_key(), f'goal:{gid}')
            self.assertEqual(self.page.funding_map.selected_goal, gid)
            self.assertIn('Other household', self.page.selection_note.text())
        finally:
            self.page.store = self.store
            other.close()

    def test_tab_leaves_read_only_table_and_custom_inputs(self):
        page = self.page
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Tab)
        self.assertIsNot(self.app.focusWidget(), page.table)
        page.policy.setCurrentIndex(page.policy.findData('custom'))
        first = next(iter(page._split_boxes.values()))
        first.setFocus()
        for _ in range(len(page._split_boxes) + 8):
            widget = self.app.focusWidget()
            if widget in (page.up, page.down, page.edit_btn, page.delete_btn):
                break
            QTest.keyClick(widget, Qt.Key.Key_Tab)
        else:
            self.fail('Tab never reached the actions after the custom share inputs')

    # ---- distilled layout -------------------------------------------------
    def test_ladder_keeps_four_row_actions_and_goal_activity_lives_on_the_map(self):
        page = self.page
        self.assertFalse(hasattr(page, 'funding_btn'))
        self.assertFalse(hasattr(page.funding_map, 'edit_btn'))
        self.assertFalse(hasattr(page.funding_map, 'add_btn'))
        row_actions = page.up.parentWidget().findChildren(QPushButton)
        self.assertEqual(set(row_actions), {page.up, page.down, page.edit_btn, page.delete_btn})
        page.funding_map.select_goal(self.gids[2])
        self.assertTrue(page.funding_map.record_btn.isEnabled())
        self.assertIn('House', page.selection_note.text())

    def test_milestones_are_one_sentence_on_the_map_not_a_second_list(self):
        self.store.add_goal(Goal('Cash baseline', 1000, 'cash'))
        self.store.add_goal(Goal('Net worth target', 90000, 'networth'))
        self.page.refresh()
        labels = self.page.funding_map.milestones.findChildren(QLabel)
        self.assertEqual(len(labels), 1)
        self.assertIn('2 balance milestones', labels[0].text())
        self.assertFalse(self.page.funding_map.milestones.findChildren(QPushButton))

    def test_investment_caveat_shows_only_when_goal_money_is_in_an_investment(self):
        self.page.refresh()
        self.assertTrue(self.page.funding_map.investment_note.isHidden())
        self.record(0, 3, 100)
        self.page.refresh()
        self.assertFalse(self.page.funding_map.investment_note.isHidden())

    def test_map_appears_only_once_a_savings_goal_exists(self):
        for gid in self.gids:
            self.store.delete_goal(gid)
        self.store.add_goal(Goal('Household cash', 5000, 'cash'))
        self.page.refresh()
        self.assertTrue(self.page.funding_map.isHidden())
        self.assertFalse(self.page.ladder.isHidden())
        self.store.add_goal(Goal('Holiday', 800, 'savings'))
        self.page.refresh()
        self.assertFalse(self.page.funding_map.isHidden())

    def test_empty_ladder_shows_a_start_card_instead_of_planning_machinery(self):
        for gid in self.gids:
            self.store.delete_goal(gid)
        self.page.refresh()
        self.assertEqual(self.page._keys, [])
        self.assertFalse(self.page.start_card.isHidden())
        for panel in (self.page.ladder, self.page.lower, self.page.chart_card, self.page.funding_map):
            self.assertTrue(panel.isHidden())
        self.assertFalse(self.page.edit_btn.isEnabled())
        self.assertFalse(self.page.delete_btn.isEnabled())
        self.store.add_goal(Goal('Holiday', 800, 'savings'))
        self.page.refresh()
        self.assertTrue(self.page.start_card.isHidden())
        self.assertFalse(self.page.ladder.isHidden())

    def test_failed_rebuild_keeps_the_panels_and_never_shows_the_start_card(self):
        with patch('finfore.ui.goals_page.build_plan', side_effect=ValueError('bad plan')):
            with self.assertLogs('finfore.ui.goals_page', level='ERROR'):
                self.page._rerun()
        self.assertTrue(self.page.start_card.isHidden())
        self.assertFalse(self.page.ladder.isHidden())
        self.assertIn('unavailable', self.page.plan_note.text())

    def test_table_is_sized_to_its_rows_and_scrolls_past_ten(self):
        page = self.page
        page._fit_table()
        header = max(page.table.horizontalHeader().sizeHint().height(),
                     page.table.horizontalHeader().fontMetrics().height() + 16)
        frame = 2 * page.table.frameWidth()
        self.assertEqual(page.table.height(), header + 4 * page.ROW_HEIGHT + frame)
        for i in range(8):
            self.store.add_goal(Goal(f'Extra {i}', 100 + i, 'savings'))
        page.refresh()
        self.assertEqual(page.table.rowCount(), 12)
        self.assertEqual(page.table.height(), header + page.MAX_ROWS * page.ROW_HEIGHT + frame)

    def test_no_zebra_rows_and_no_nested_scroll_areas(self):
        self.assertFalse(self.page.table.alternatingRowColors())
        self.assertEqual(self.page.findChildren(QScrollArea), [self.page.scroll])

    def test_split_note_appears_only_for_the_custom_split(self):
        page = self.page
        self.assertTrue(page.custom_note.isHidden())
        page.policy.setCurrentIndex(page.policy.findData('custom'))
        self.assertFalse(page.custom_note.isHidden())
        self.assertIn('Relative shares', page.custom_note.text())
        page.policy.setCurrentIndex(page.policy.findData('focus'))
        self.assertTrue(page.custom_note.isHidden())

    def test_plan_sentence_sits_with_the_ladder_and_names_the_finish_month(self):
        self.store.set_setting('plan_surplus', 350)
        self.page.refresh()
        text = self.page.plan_note.text()
        self.assertIn('Estimate: every goal is funded by', text)
        self.assertIn('$350', text)
        self.assertIs(self.page.plan_note.parentWidget(), self.page.ladder)

    def test_date_verdict_comes_before_the_extra_per_month_list(self):
        self.store.set_setting('plan_surplus', 350)
        self.page.refresh()
        text = self.page.faster.text()
        verdict = text.index('takes')
        self.assertIn('Next up', text[:verdict])
        extra = text.find('Or put extra toward it each month')
        if extra >= 0:
            self.assertGreater(extra, verdict)

    def test_extra_per_month_rows_collapse_results_that_land_on_the_same_month(self):
        from types import SimpleNamespace
        eta = date(2030, 6, 1)
        ups = [SimpleNamespace(extra=50, months_saved=1, new_eta=eta),
               SimpleNamespace(extra=100, months_saved=1, new_eta=eta),
               SimpleNamespace(extra=250, months_saved=3, new_eta=date(2030, 4, 1)),
               SimpleNamespace(extra=500, months_saved=0, new_eta=None)]
        html = self.page._speedup_html(ups)
        self.assertEqual(html.count('/mo</span>'), 2)
        self.assertIn('+$50', html)
        self.assertNotIn('+$100', html)
        self.assertIn('already funded from the first month', self.page._speedup_html([]))

    def test_simple_view_folds_the_chart_and_the_full_view_opens_it(self):
        self.page.set_simple(True)
        self.assertFalse(self.page.chart_card.expanded)
        self.assertFalse(self.page.plot.isVisible())
        self.page.set_simple(False)
        self.assertTrue(self.page.chart_card.expanded)
        self.assertTrue(self.page.plot.isVisible())
        self.assertGreaterEqual(self.page.plot.height(), 200)

    def test_one_page_scroll_holds_at_the_minimum_window(self):
        self.store.set_setting('plan_surplus', 350)
        self.win.resize(980, 620)
        self.page.set_simple(False)
        self.page.refresh()
        self.app.processEvents()
        self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.page.lower.layout_.direction(), QBoxLayout.Direction.TopToBottom)
        self.win.resize(1280, 900)
        self.app.processEvents()
        self.assertEqual(self.page.lower.layout_.direction(), QBoxLayout.Direction.LeftToRight)

    # ---- first run -----------------------------------------------------------
    def test_goals_without_a_monthly_amount_say_what_is_missing_not_that_they_fail(self):
        page = self.page
        self.assertEqual(page.surplus.value(), 0)
        self.assertTrue(page._waiting_for_allowance())
        self.assertIn('needs a monthly amount', page.plan_note.text())
        self.assertNotIn('30 years', page.plan_note.text())
        self.assertNotIn('No spare income', page.plan_note.text())
        self.assertFalse(page.allowance_btn.isHidden())
        self.assertIn('Once there is a monthly amount', page.faster.text())
        self.assertIn('Once there is a monthly amount', page.cascade.text())
        self.assertNotIn('Not reachable', page.faster.text())
        self.assertFalse(page.by_date.isEnabled())
        self.assertEqual(page.table.item(0, 6).text(), 'Needs a monthly amount')
        self.assertNotIn('Not within 30 yrs', [page.table.item(r, 6).text() for r in range(page.table.rowCount())])
        page.allowance_btn.click()
        self.assertIs(self.app.focusWidget(), page.surplus)
        page.surplus.setValue(350)
        self.assertFalse(page._waiting_for_allowance())
        self.assertTrue(page.allowance_btn.isHidden())
        self.assertTrue(page.by_date.isEnabled())
        self.assertIn('Next up', page.faster.text())
        self.assertIn('Estimate: every goal is funded by', page.plan_note.text())
        self.assertNotIn('Needs a monthly amount', [page.table.item(r, 6).text() for r in range(page.table.rowCount())])

    def test_a_goal_that_truly_cannot_finish_still_reads_as_a_caution(self):
        page = self.page
        self.store.set_setting('plan_surplus', 1)
        self.store.update_goal(self.gids[2], amount=10_000_000)
        page.refresh()
        self.assertFalse(page._waiting_for_allowance())
        cells = [page.table.item(r, 6).text() for r in range(page.table.rowCount())]
        self.assertIn('Not within 30 yrs', cells)
        self.assertIn('30 years', page.plan_note.text())

    def test_start_card_previews_an_emergency_fund_from_the_persons_own_numbers(self):
        for gid in self.gids:
            self.store.delete_goal(gid)
        self.store.set_setting('plan_surplus', 500)
        with patch('finfore.ui.goals_page.emergency_goal_amount', return_value=12000):
            self.page.refresh()
        note = self.page.start_note.text()
        self.assertIn('$12,000', note)
        self.assertIn('roughly 24 months', note)
        self.assertIn('estimates', note)
        self.assertTrue(self.page.start_demo.isHidden())

    def test_start_card_without_history_explains_where_dates_come_from_and_offers_the_demo(self):
        for gid in self.gids:
            self.store.delete_goal(gid)
        with patch('finfore.ui.goals_page.emergency_goal_amount', return_value=0), \
                patch.object(self.store, 'accounts', return_value=[]):
            self.page.refresh()
        self.assertIn('Import transactions', self.page.start_note.text())
        self.assertNotIn('$', self.page.start_note.text())
        self.assertFalse(self.page.start_demo.isHidden())

    def test_map_says_how_to_begin_when_there_is_no_account_to_map(self):
        other = Store(Path(self.temp.name) / 'no-accounts.db')
        try:
            other.add_goal(Goal('Holiday', 2000, 'savings'))
            self.page.store = other
            self.page.refresh()
            fm = self.page.funding_map
            self.assertFalse(fm.isHidden())
            self.assertFalse(fm.no_accounts.isHidden())
            self.assertIn('planned savings', self.page.payment_note.text())
            self.assertNotIn('Goal activity', self.page.payment_note.text())
            for part in (fm.summary, fm.reconciliation, fm.key_label, fm.account_panel,
                         fm.goal_panel, fm.selection_bar):
                self.assertTrue(part.isHidden())
        finally:
            self.page.store = self.store
            other.close()
        self.page.refresh()
        self.assertTrue(self.page.funding_map.no_accounts.isHidden())
        self.assertFalse(self.page.funding_map.account_panel.isHidden())

    def test_a_freed_loan_payment_with_no_goal_waiting_says_so(self):
        for gid in self.gids:
            self.store.delete_goal(gid)
        self.store.add_account(Account('Small loan', AccountType.LOAN, 500, self.today, monthly_payment=100))
        self.store.set_setting('plan_surplus', 0)
        self.page.refresh()
        text = self.page.cascade.text()
        self.assertIn('is free, and no goal is waiting for it', text)
        self.assertNotIn('ladder complete', text)
        self.store.add_goal(Goal('Holiday', 800, 'savings'))
        self.page.refresh()
        self.assertNotIn('no goal is waiting', self.page.cascade.text())

    def test_adding_a_goal_reports_when_it_will_be_done_in_the_status_bar(self):
        self.store.set_setting('plan_surplus', 350)
        self.page.refresh()
        def accept(dlg):
            dlg.name.setText('Holiday')
            dlg.amount.setValue(700)
            return QDialog.DialogCode.Accepted
        with patch.object(GoalDialog, 'exec', accept):
            self.page._add()
        message = self.win.statusBar().currentMessage()
        self.assertIn('Added “Holiday”', message)
        self.assertIn('is done by', message)
        self.assertIn('$350', message)
        self.store.set_setting('plan_surplus', 0)
        self.page.refresh()
        with patch.object(GoalDialog, 'exec', accept):
            self.page._add()
        self.assertIn('Enter a monthly amount', self.win.statusBar().currentMessage())

    # ---- polish ---------------------------------------------------------------
    def test_delete_confirmation_has_a_coral_destructive_button_and_keeps_the_safe_default(self):
        seen = {}
        def inspect(box):
            remove = next(b for b in box.buttons() if box.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole)
            seen['name'] = remove.objectName()
            seen['default'] = box.buttonRole(box.defaultButton())
            return 0
        self.page.funding_map.select_goal(self.gids[0])
        with patch.object(QMessageBox, 'exec', inspect):
            self.page._delete()
        self.assertEqual(seen['name'], 'danger')
        self.assertEqual(seen['default'], QMessageBox.ButtonRole.RejectRole)

    def test_loan_buttons_explain_themselves_instead_of_naming_an_impossible_action(self):
        loan = self.store.add_account(Account('Car', AccountType.LOAN, 1000, self.today, monthly_payment=100))
        self.page.refresh()
        self.page.table.selectRow(self.page._keys.index(f'loan:{loan}'))
        self.assertFalse(self.page.delete_btn.isEnabled())
        self.assertIn('paid off', self.page.delete_btn.toolTip())
        self.assertIn('Accounts', self.page.edit_btn.toolTip())
        self.page.table.selectRow(self.page._keys.index(f'goal:{self.gids[0]}'))
        self.assertEqual(self.page.delete_btn.toolTip(), 'Delete goal for Baseline')

    def test_tab_order_runs_table_then_amount_shortcut_then_row_actions(self):
        page = self.page
        self.assertTrue(page.allowance_btn.isVisibleTo(page))
        chain, widget = [], page.table.nextInFocusChain()
        while widget is not page.table and len(chain) < 400:
            if widget.focusPolicy() != Qt.FocusPolicy.NoFocus and widget.isVisibleTo(page):
                chain.append(widget)
            widget = widget.nextInFocusChain()
        self.assertEqual(chain[:5], [page.allowance_btn, page.up, page.down, page.edit_btn, page.delete_btn])

    def test_a_loan_is_drawn_coral_and_dashed_and_goals_keep_their_map_colours(self):
        from finfore.ui.goal_funding_map import goal_color
        from finfore.ui import theme
        self.store.add_account(Account('Car', AccountType.LOAN, 1000, self.today, monthly_payment=100))
        self.page.refresh()
        items = {item.name(): item for item in self.page.plot.listDataItems()}
        loan = items['Car']
        self.assertEqual(loan.opts['pen'].color().name().lower(), theme.RED.lower())
        self.assertEqual(loan.opts['pen'].style(), Qt.PenStyle.DashLine)
        goal = items['House']
        self.assertEqual(goal.opts['pen'].color().name().lower(), goal_color(self.gids[2]).lower())

    def test_dialogs_are_wide_enough_for_their_own_placeholder(self):
        dlg = GoalDialog(self.win)
        dlg.show()
        self.app.processEvents()
        fm = dlg.name.fontMetrics()
        self.assertGreaterEqual(dlg.name.width() - 16, fm.horizontalAdvance(dlg.name.placeholderText()))
        dlg.close()

    def test_projection_stays_inside_zero_to_one_hundred_percent_and_its_last_point(self):
        page = self.page
        self.store.set_setting('plan_surplus', 350)
        page.set_simple(False)
        page.refresh()
        self.app.processEvents()
        vb = page.plot.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        self.assertAlmostEqual(y0, -8)
        self.assertAlmostEqual(y1, 108)
        vb.setRange(yRange=(900, 1000), padding=0)
        self.assertLessEqual(vb.viewRange()[1][1], 108 + 1e-6)
        vb.setRange(yRange=(-5000, 5000), padding=0)
        low, high = vb.viewRange()[1]
        self.assertGreaterEqual(low, -8 - 1e-6)
        self.assertLessEqual(high, 108 + 1e-6)
        vb.setRange(xRange=(x1, x1 + 3650 * 86400), padding=0)
        self.assertLessEqual(vb.viewRange()[0][1], x1 + 1e-6)

    def test_projection_reframes_when_the_plan_changes_so_no_stale_limits_remain(self):
        page = self.page
        self.store.set_setting('plan_surplus', 100)
        page.set_simple(False)
        page.refresh()
        self.app.processEvents()
        vb = page.plot.getViewBox()
        long_end = vb.viewRange()[0][1]
        vb.setRange(xRange=(vb.viewRange()[0][0], vb.viewRange()[0][0] + 90 * 86400), padding=0)
        page.surplus.setValue(2000)
        self.app.processEvents()
        (x0, x1), (y0, y1) = vb.viewRange()
        self.assertLess(x1, long_end)
        self.assertGreater(x1 - x0, 90 * 86400 * 1.5)
        self.assertAlmostEqual(y0, -8)
        self.assertAlmostEqual(y1, 108)
        limits = vb.state['limits']['xLimits']
        self.assertAlmostEqual(limits[1], x1, delta=1)

    def test_finish_by_field_is_a_reachable_month_picker_with_sane_limits(self):
        from PyQt6.QtCore import QDate
        from finfore.ui.widgets import PopupDateEdit
        page = self.page
        field = page.by_date
        self.assertIsInstance(field, PopupDateEdit)
        today = QDate.currentDate()
        self.assertEqual(field.minimumDate(), today)
        self.assertEqual(field.maximumDate(), today.addYears(30))
        field.setDate(QDate(1999, 1, 1))
        self.assertEqual(field.date(), today)
        field.setDate(QDate(2999, 1, 1))
        self.assertEqual(field.date(), today.addYears(30))
        self.assertTrue(field.accessibleName())
        by_label = next(l for l in page.findChildren(QLabel) if l.text() == 'By')
        self.assertIs(by_label.buddy(), field)
        pick_label = next(l for l in page.findChildren(QLabel) if l.text() == 'Goal')
        self.assertIs(pick_label.buddy(), page.target_pick)
        self.assertIn('Alt+Down', field.toolTip())

    def test_finish_by_limits_follow_the_calendar_when_the_app_stays_open(self):
        from PyQt6.QtCore import QDate
        page = self.page
        with patch.object(QDate, 'currentDate', staticmethod(lambda: QDate(2035, 3, 9))):
            page.refresh()
        self.assertEqual(page.by_date.minimumDate(), QDate(2035, 3, 9))
        self.assertEqual(page.by_date.maximumDate(), QDate(2065, 3, 9))

    # ---- Plan section: Goals and Monthly budget are tabs of one rail item ------
    def test_plan_is_one_rail_item_with_goals_first_and_the_budget_second(self):
        win = self.win
        self.assertEqual([win.nav.item(i).text() for i in range(win.nav.count())],
                         ['Home', 'Accounts', 'Bills & payday', 'Plan', 'Forecast'])
        tabs = win.plan_tabs
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['Goals', 'Monthly budget'])
        self.assertIs(tabs.widget(0), self.page)
        win.show_page('Accounts')
        win.nav.setCurrentRow(3)
        self.assertIs(win.current_page(), self.page)
        for name in ('Goals', 'Plan', 'Monthly budget'):
            win.show_page(name)
            self.assertEqual(win.nav.currentItem().text(), 'Plan')
        self.assertEqual(type(win.current_page()).__name__, 'PlanPage')
        win.show_page('Goals')
        self.assertIs(win.current_page(), self.page)

    def test_every_legacy_page_name_still_resolves(self):
        for name in ('Dashboard', 'Spending', 'Insights', 'Accounts', 'Transactions', 'Bills & payday',
                     'Calendar', 'Recurring', 'Plan', 'Goals', 'Forecast', 'Projection', 'Progress'):
            self.assertIn(name, self.win._destinations, name)
            row, _page = self.win._destinations[name]
            self.assertLess(row, len(self.win.sections), name)

    def test_the_goals_budget_link_and_the_forecast_link_stay_inside_the_plan_item(self):
        budget_link = next(b for b in self.page.findChildren(QPushButton) if b.text() == 'Monthly budget')
        budget_link.click()
        self.assertEqual(type(self.win.current_page()).__name__, 'PlanPage')
        self.assertEqual(self.win.nav.currentItem().text(), 'Plan')
        self.win.show_page('Projection')
        self.win.pages[6].manage_goals_btn.click()
        self.assertIs(self.win.current_page(), self.page)
        self.assertEqual(self.win.nav.currentItem().text(), 'Plan')

    def test_switching_plan_tabs_refreshes_the_page_that_appears(self):
        with patch.object(self.win.pages[9], 'refresh') as refresh:
            self.win.plan_tabs.setCurrentIndex(1)
            refresh.assert_called_once()

    def test_the_selected_tab_has_an_underline_not_only_a_colour(self):
        from finfore.ui import theme
        selected = theme.QSS[theme.QSS.index('QTabBar::tab:selected'):].split('}')[0]
        self.assertIn('border-bottom: 2px solid', selected)

    def test_unicode_and_markup_names_remain_literal_in_rich_projections(self):
        name = 'Reserve <b>literal</b> & 家族 مرحبا 🎯 ' + 'LongName' * 15
        self.store.update_goal(self.gids[0], name=name)
        self.store.set_setting('plan_surplus', 350)
        self.page.refresh()
        doc = QTextDocument()
        doc.setHtml(self.page.faster.text())
        self.assertIn(name, doc.toPlainText())
        self.assertIn('&lt;b&gt;literal&lt;/b&gt;', self.page.faster.text())
        self.assertIn(name, self.page.selection_note.text())
        self.win.resize(980, 620)
        self.app.processEvents()
        self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(), 0)

    def test_plan_failure_removes_stale_results_and_retry_recovers(self):
        with patch('finfore.ui.goals_page.build_plan', side_effect=ValueError('bad plan')):
            with self.assertLogs('finfore.ui.goals_page', level='ERROR'):
                self.page._rerun()
        self.assertIsNone(self.page._plan)
        self.assertEqual(self.page.table.rowCount(), 0)
        self.assertEqual(self.page.plot.listDataItems(), [])
        self.assertFalse(self.page.funding_map.isHidden())
        self.assertTrue(self.page.funding_map.record_btn.isEnabled())
        self.assertFalse(self.page.error_host.isHidden())
        self.page.retry_btn.click()
        self.assertIsNotNone(self.page._plan)
        self.assertEqual(self.page.table.rowCount(), 4)
        self.assertTrue(self.page.error_host.isHidden())

    def test_map_failure_leaves_planning_available_and_retry_recovers(self):
        with patch('finfore.ui.goal_funding_map.funding_overview', side_effect=sqlite3.OperationalError('busy')):
            with self.assertLogs('finfore.ui.goals_page', level='ERROR'):
                self.page._rerun()
        self.assertTrue(self.page.funding_map.isHidden())
        self.assertEqual(self.page.table.rowCount(), 4)
        self.page.retry_btn.click()
        self.assertFalse(self.page.funding_map.isHidden())

    def test_failed_share_save_keeps_entries_and_retries_the_same_values(self):
        self.page.policy.setCurrentIndex(self.page.policy.findData('custom'))
        key = f'goal:{self.gids[0]}'
        with patch.object(self.store, 'set_plan_allocations', side_effect=sqlite3.OperationalError('locked')):
            self.page._split_boxes[key].setValue(7)
        self.assertEqual(self.page._split_boxes[key].value(), 7)
        self.assertNotIn(key, self.store.plan_allocations())
        self.assertFalse(self.page.error_host.isHidden())
        self.page.retry_btn.click()
        self.assertEqual(self.store.plan_allocations()[key], 7)
        self.assertTrue(self.page.error_host.isHidden())

    def test_invalid_allowance_has_a_recovery_path_without_nan_in_widgets(self):
        self.store.set_setting('plan_surplus', float('nan'))
        with self.assertLogs('finfore.ui.goals_page', level='ERROR'):
            self.page.refresh()
        self.assertIsNone(self.page._plan)
        self.page._use_detected()
        self.assertIsNotNone(self.page._plan)
        self.assertIsNone(self.store.get_setting('plan_surplus'))

    def test_activity_save_failure_preserves_fields_and_rapid_clicks_do_not_duplicate(self):
        dlg = GoalFundingDialog(self.store, self.gids[0], self.win)
        dlg.account.setCurrentIndex(dlg.account.findData(self.aids[0]))
        dlg.amount.setValue(100)
        dlg.note.setText('Keep this note 家族')
        with patch.object(self.store, 'record_goal_activity', side_effect=sqlite3.OperationalError('locked')):
            dlg.record_btn.click()
        self.assertEqual(dlg.amount.value(), 100)
        self.assertEqual(dlg.note.text(), 'Keep this note 家族')
        self.assertTrue(dlg.record_btn.isEnabled())
        for _ in range(10):
            dlg.record_btn.click()
        self.assertEqual(len(self.store.goal_activity(self.gids[0])), 1)
        self.assertFalse(dlg.record_btn.isEnabled())
        self.assertIn('Activity recorded', dlg.error.text())

    def test_post_save_read_failure_does_not_offer_to_repeat_the_write(self):
        dlg = GoalFundingDialog(self.store, self.gids[0], self.win)
        dlg.amount.setValue(100)
        with patch.object(dlg, '_refresh_inner', side_effect=sqlite3.OperationalError('locked')):
            dlg.record_btn.click()
        self.assertEqual(len(self.store.goal_activity(self.gids[0])), 1)
        self.assertIn('Activity saved.', dlg.error.text())
        self.assertEqual(dlg.amount.value(), 0)
        self.assertFalse(dlg.record_btn.isEnabled())
        dlg.refresh_btn.click()
        self.assertFalse(dlg._read_failed)
        self.assertEqual(dlg.error.text(), '')

    def test_goal_and_priority_writes_roll_back_together(self):
        before = self.store.plan_order()
        self.store.con.executescript("""
            CREATE TRIGGER reject_priority BEFORE INSERT ON settings
            WHEN NEW.key='plan_order'
            BEGIN SELECT RAISE(ABORT, 'test priority failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_goal(Goal('Must roll back', 100, 'savings'), plan_order=before)
        self.assertEqual(len(self.store.goals()), 4)
        self.assertEqual(self.store.plan_order(), before)
        self.record(0, 0, 100)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.delete_goal(self.gids[0])
        self.assertEqual(len(self.store.goal_activity(self.gids[0])), 1)
        self.assertEqual(self.store.plan_order(), before)

    def test_delete_defaults_to_keep_and_undo_restores_activity_and_priority(self):
        self.record(0, 0, 100)
        self.page.refresh()
        order = self.store.plan_order()
        def keep(box):
            self.assertEqual(box.buttonRole(box.defaultButton()), QMessageBox.ButtonRole.RejectRole)
            box.show()
            QTest.keyClick(box, Qt.Key.Key_Return)
            self.assertIs(box.clickedButton(), box.defaultButton())
            return 0
        with patch.object(QMessageBox, 'exec', keep):
            self.page._delete()
        self.assertEqual(len(self.store.goals()), 4)
        def remove(box):
            next(b for b in box.buttons() if box.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole).click()
            return 0
        with patch.object(QMessageBox, 'exec', remove):
            self.page._delete()
        self.assertEqual(len(self.store.goals()), 3)
        self.assertIsNone(self.page._selected_key())
        self.assertNotIn(f'goal:{self.gids[0]}', self.store.plan_order())
        self.store.undo.undo()
        self.page.refresh()
        self.assertEqual(self.store.plan_order(), order)
        self.assertEqual(len(self.store.goal_activity(self.gids[0])), 1)

    def test_failed_add_keeps_dialog_values_without_a_partial_goal(self):
        attempts = []
        def edit(dlg):
            if not attempts:
                dlg.name.setText('Retry 家族')
                dlg.amount.setValue(200)
                attempts.append(True)
                return QDialog.DialogCode.Accepted
            self.assertEqual(dlg.name.text(), 'Retry 家族')
            self.assertEqual(dlg.amount.value(), 200)
            self.assertIn('not saved', dlg.error.text())
            return QDialog.DialogCode.Rejected
        with patch.object(GoalDialog, 'exec', edit), patch.object(self.store, 'add_goal', side_effect=sqlite3.OperationalError('locked')):
            self.page._add()
        self.assertEqual(len(self.store.goals()), 4)


def run_tests():
    return unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(GoalHardeningTests))


if __name__ == '__main__':
    unittest.main()
