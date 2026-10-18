"""One word for one thing, one number for one fact, and labels that say what they do."""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (QAbstractButton, QGroupBox, QLabel, QTabBar,
                             QTableWidget, QTreeWidget)

from finfore.insights import breakdown, monthly_aggregates
from finfore.spending_plan import LABELS
from finfore.ui import theme
from test_distill import DistillFixture

# Words the product retired. Each had a plainer or already-standard replacement:
# Frivolous/Necessities/Essential(s) -> Wants/Needs, British spellings -> US.
RETIRED = re.compile(r"Frivolous|Necessities|Essentials,|categoris|behaviour|colour|"
                     r"\bDiscretionary\b|\bUncategorised\b|Closing posted|overdrawn|"
                     r"Kept from income|\bP50\b|Off the map", re.I)


def visible_texts(root) -> list[str]:
    """Every string a person could read on a page: labels, buttons, tabs, headers."""
    out = [w.text() for w in root.findChildren(QLabel)]
    out += [b.text() for b in root.findChildren(QAbstractButton)]
    out += [g.title() for g in root.findChildren(QGroupBox)]
    for bar in root.findChildren(QTabBar):
        out += [bar.tabText(i) for i in range(bar.count())]
    for table in root.findChildren(QTableWidget):
        out += [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())
                if table.horizontalHeaderItem(c) is not None]
    for tree in root.findChildren(QTreeWidget):
        header = tree.headerItem()
        out += [header.text(c) for c in range(tree.columnCount())] if header else []
    return [t for t in out if t]


PAGES = ('Dashboard', 'Spending', 'Accounts', 'Transactions', 'Bills', 'Recurring',
         'Goals', 'Plan', 'Projection', 'Progress')


class SavedFigureTests(DistillFixture, unittest.TestCase):
    def test_home_and_spending_say_the_same_percent_is_saved(self):
        agg = monthly_aggregates(self.store.transactions())
        percent = f"{agg.savings_rate * 100:.0f}%"
        self.win.show_page('Dashboard')
        dash = self.win.current_page()
        self.assertEqual(dash.card_save.value.text(), percent)
        self.win.show_page('Spending')
        spending = self.win.current_page()
        self.assertIn(f"{percent} saved", " ".join(visible_texts(spending)))

    def test_home_names_the_basis_and_the_spending_it_stands_on(self):
        agg = monthly_aggregates(self.store.transactions())
        self.win.show_page('Dashboard')
        sub = self.win.current_page().card_save.sub.text()
        self.assertTrue(sub.startswith('A typical month'), sub)
        self.assertIn(f"${agg.spending:,.0f}", sub)

    def test_the_last_ninety_days_stand_in_only_until_a_complete_month_exists(self):
        self.win.show_page('Dashboard')
        dash = self.win.current_page()
        with patch('finfore.ui.dashboard.monthly_aggregates', return_value=None):
            dash.refresh()
        self.assertTrue(dash.card_save.sub.text().startswith('Last 3 months'),
                        dash.card_save.sub.text())
        self.assertEqual(dash.card_save.title.text(), 'SAVED FROM INCOME')


class ViewSwitchTests(DistillFixture, unittest.TestCase):
    def test_the_switch_names_both_views_and_lights_the_one_that_is_on(self):
        win = self.win
        self.assertEqual((win.mode_simple.text(), win.mode_all.text()), ('Simple', 'Everything'))
        self.assertEqual(win.mode_box.accessibleName(), 'View')
        self.assertTrue(win.simple_mode)          # the app opens on Simple
        self.assertTrue(win.mode_simple.isChecked())
        self.assertFalse(win.mode_all.isChecked())

    def test_choosing_a_view_switches_to_it_and_choosing_the_current_one_changes_nothing(self):
        win = self.win
        win.mode_simple.click()
        self.assertTrue(win.simple_mode)
        self.assertTrue(win.mode_simple.isChecked())
        win.mode_all.click()
        self.assertFalse(win.simple_mode)
        self.assertTrue(win.mode_all.isChecked())
        self.assertFalse(win.mode_simple.isChecked())
        win.mode_all.click()
        self.assertFalse(win.simple_mode)
        self.assertTrue(win.mode_all.isChecked())
        win.mode_simple.click()
        self.assertTrue(win.simple_mode)

    def test_each_view_says_what_it_shows_in_its_tooltip(self):
        self.assertIn('essentials', self.win.mode_simple.toolTip())
        self.assertIn('advanced', self.win.mode_all.toolTip())


class OneWordForOneThingTests(DistillFixture, unittest.TestCase):
    def test_the_spending_groups_have_one_set_of_names(self):
        from types import SimpleNamespace
        agg = SimpleNamespace(essential=3500.0, frivolous=760.0, subscriptions=25.0,
                              giving=50.0, unknown=40.0)
        self.assertEqual([label for label, *_ in breakdown(agg)],
                         ['Needs', 'Other wants', 'Subscriptions', 'Giving', 'Uncategorized'])
        agg.subscriptions = 0.0
        self.assertEqual([label for label, *_ in breakdown(agg)],
                         ['Needs', 'Wants', 'Giving', 'Uncategorized'])
        self.assertEqual(LABELS['essential'], 'Needs, excluding debt payments')
        self.assertEqual(LABELS['frivolous'], 'Wants')
        self.assertEqual(LABELS['unknown'], 'Uncategorized spending')

    def test_the_retired_words_are_gone_from_every_page_in_both_views(self):
        for simple in (True, False):
            self.win.mode_simple.click() if simple else self.win.mode_all.click()
            for name in PAGES:
                self.win.show_page(name)
                self.settle()
                for text in visible_texts(self.win.current_page()):
                    self.assertIsNone(RETIRED.search(text), f"{name}, simple={simple}: {text!r}")

    def test_the_details_dialog_uses_the_same_group_names(self):
        from finfore.ui.spending_details import GROUPS, SpendingDetailsDialog
        self.assertEqual([label for _, label in GROUPS],
                         ['Income', 'Needs', 'Wants', 'Giving', 'Uncategorized'])
        opened = []
        self.win.show_page('Spending')
        spending = self.win.current_page()
        with patch.object(SpendingDetailsDialog, 'exec', lambda dialog: opened.append(dialog) or 0):
            spending.how_btn.click()
        self.assertEqual(len(opened), 1)
        for text in visible_texts(opened[0]):
            self.assertIsNone(RETIRED.search(text), repr(text))

    def test_the_track_record_chart_says_middle_outcome_rather_than_p50(self):
        self.win.show_page('Progress')
        page = self.win.current_page()
        self.settle()
        legend = page.plot.plotItem.legend
        names = [label.text for _, label in legend.items]
        self.assertTrue(names, 'the comparison chart should have a legend')
        self.assertTrue(any('middle outcome' in n for n in names), names)
        self.assertFalse(any('P50' in n for n in names), names)
        self.assertEqual(page.card_spend.title.text(), 'SPENDING ON WANTS')

    def test_a_status_outside_the_whole_forecast_range_says_so(self):
        import inspect
        from finfore.ui import progress_page
        source = inspect.getsource(progress_page)
        self.assertIn('"broken": "Outside the range"', source)
        self.assertNotIn('Off the map', source)


class GoalsWordsTests(DistillFixture, unittest.TestCase):
    TARGET = re.compile(r"\b[Tt]argets?\b(?![- ]date)")

    def goals(self):
        self.win.show_page('Goals')
        self.settle()
        return self.win.current_page()

    def test_the_ladder_calls_its_rows_goals_and_types_them_plainly(self):
        page = self.goals()
        headers = [page.table.horizontalHeaderItem(c).text() for c in range(page.table.columnCount())]
        self.assertEqual(headers[:2], ['#', 'Goal'])
        self.assertEqual(headers[2], 'Type')
        kinds = {page.table.item(r, 2).text() for r in range(page.table.rowCount())}
        self.assertTrue(kinds <= {'Loan payoff', 'Savings goal', 'Cash milestone', 'Net worth milestone'}, kinds)
        self.assertEqual(page.edit_btn.text(), 'Edit goal…')

    def test_target_is_kept_for_a_target_date_and_nothing_else_the_page_says(self):
        page = self.goals()
        for text in visible_texts(page):
            self.assertIsNone(self.TARGET.search(text), repr(text))

    def test_the_add_goal_dialog_asks_for_a_goal_amount(self):
        from finfore.ui.goals_page import GoalDialog
        dlg = GoalDialog(self.win)
        labels = [label.text() for label in dlg.findChildren(QLabel)]
        self.assertIn('Goal amount', labels)
        self.assertNotIn('Target', labels)
        dlg.name.setText('Vacation')
        dlg.amount.setValue(0)
        dlg._ok()
        self.assertIn('goal amount', dlg.error.text())


class NoSwallowedAmpersandTests(DistillFixture, unittest.TestCase):
    """A single & in a button or tab is read by Qt as a shortcut key and not drawn."""

    def swallowed(self, root):
        found = []
        for button in root.findChildren(QAbstractButton):
            if not QKeySequence.mnemonic(button.text()).isEmpty():
                found.append(button.text())
        for bar in root.findChildren(QTabBar):
            for i in range(bar.count()):
                if not QKeySequence.mnemonic(bar.tabText(i)).isEmpty():
                    found.append(bar.tabText(i))
        return found

    def test_no_button_or_tab_on_any_page_loses_its_ampersand(self):
        for simple in (True, False):
            self.win.mode_simple.click() if simple else self.win.mode_all.click()
            for name in PAGES:
                self.win.show_page(name)
                self.settle()
                self.assertEqual(self.swallowed(self.win.current_page()), [], name)
            self.assertEqual(self.swallowed(self.win), [])

    def test_the_two_names_that_carry_an_ampersand_still_read_as_written(self):
        self.assertEqual(self.win.bills_tabs.tabText(1).replace('&&', '&'), 'Detected bills & pay')
        self.win.show_page('Plan')
        details = [b.text() for b in self.win.current_page().findChildren(QAbstractButton)
                   if b.text().startswith('Inspect income')]
        self.assertEqual([t.replace('&&', '&') for t in details], ['Inspect income & spending transactions…'])

    def test_the_spending_details_dialog_keeps_the_ampersand_in_its_tab(self):
        from finfore.ui.spending_details import SpendingDetailsDialog
        opened = []
        self.win.show_page('Spending')
        with patch.object(SpendingDetailsDialog, 'exec', lambda dialog: opened.append(dialog) or 0):
            self.win.current_page().how_btn.click()
        self.assertEqual(self.swallowed(opened[0]), [])


class BalanceWordsTests(DistillFixture, unittest.TestCase):
    def test_the_balance_field_asks_for_the_balance_at_the_end_of_the_day(self):
        from finfore.ui.account_dialog import AccountDialog
        account = AccountDialog(account=self.store.accounts()[0])
        if account.type.currentData().is_cashlike:
            self.assertEqual(account.balance_label.text(), 'Balance at end of day')
        self.assertEqual(account.overdraft.text(), 'Allow this account to go below $0')


if __name__ == '__main__':
    unittest.main()


class OwnRulesTests(DistillFixture, unittest.TestCase):
    """The product's own rules, where the second critique found them broken."""

    def test_one_way_to_write_a_day(self):
        from datetime import date
        from finfore.util import fmt_date, fmt_day
        self.assertEqual(fmt_day(date(2026, 10, 20)), 'Oct 20, 2026')
        self.assertEqual(fmt_day(date(2026, 10, 20), year=False, weekday=True), 'Tue, Oct 20')
        self.assertEqual(fmt_date('2026-11-01'), 'Nov 1, 2026')
        self.assertEqual(fmt_day(None), '—')
        self.assertEqual(fmt_day('not a date'), '—')

    def test_no_screen_shows_an_iso_date(self):
        import re
        self.win._toggle_mode()
        iso = re.compile(r'\b20\d\d-\d\d-\d\d\b')
        for name in PAGES:
            self.win.show_page(name)
            self.settle()
            page = self.win.current_page()
            texts = visible_texts(page)
            for table in page.findChildren(QTableWidget):
                texts += [table.item(r, c).text() for r in range(min(table.rowCount(), 5))
                          for c in range(table.columnCount()) if table.item(r, c)]
            for text in texts:
                self.assertIsNone(iso.search(text), f'{name}: {text!r}')

    def test_destructive_confirms_name_their_buttons_and_default_to_keep(self):
        from PyQt6.QtWidgets import QMessageBox
        seen = []

        def inspect(box):
            seen.append(([b.text() for b in box.buttons()], box.defaultButton().text()))
            return 0
        with patch.object(QMessageBox, 'exec', inspect):
            self.win._reset()
            self.win.show_page('Recurring')
            page = self.win.current_page()
            page.table.selectRow(0)
            page._delete()
        self.assertEqual(seen[0], (['Delete everything', 'Keep my data'], 'Keep my data'))
        self.assertEqual(seen[1], (['Delete pattern', 'Keep it'], 'Keep it'))
        self.assertTrue(self.store.accounts())

    def test_needs_a_look_uses_one_verb(self):
        dash = self.win.pages[0]
        rows = dash._look_items(self.store.transactions())
        self.assertTrue(rows)
        for sentence, _label, _action in rows:
            self.assertTrue(sentence.endswith(('need a look', 'needs a look')), sentence)

    def test_the_donut_says_its_shares_are_of_spending(self):
        self.win.show_page('Spending')
        page = self.win.current_page()
        self.settle()
        self.assertEqual(page.legend_caption.text(), 'Share of spending')
        self.assertTrue(page.legend_caption.isVisibleTo(page))

    def test_forecast_risk_and_an_outside_the_range_status_are_amber_never_coral_or_violet(self):
        import inspect
        from finfore.ui import forecast_page, progress_page
        self.assertIn('"high": theme.AMBER', inspect.getsource(forecast_page))
        self.assertEqual(progress_page._STATUS_COLOR['broken'], theme.AMBER)

    def test_the_budget_never_reports_zero_left_against_its_own_figure(self):
        self.assertIsNone(self.store.get_setting('plan_surplus'))
        self.win.show_page('Monthly budget')
        page = self.win.current_page()
        self.assertIn('available for goals and extra debt', page.verdict.text())
        self.assertNotIn('left unassigned', page.verdict.text())
        self.assertIn('= ', page.equation.text())
        self.assertIn('Goals is planning with', page.allowance_line.text())
