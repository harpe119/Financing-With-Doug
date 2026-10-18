"""The Accounts section: balances and transactions as two tabs of one rail item."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pandas as pd
from PyQt6.QtCore import QEvent, QSettings
from PyQt6.QtWidgets import QApplication, QDialog

from finfore.models import Account, AccountType
from finfore.store import Store
from finfore.ui import prefs
from finfore.ui.main_window import MainWindow
from test_usability import desktop_test_app


class AccountsSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'nav.db')
        self.prefs = patch.object(prefs, 'app_settings', lambda: QSettings(
            str(Path(self.tmp.name) / 'prefs.ini'), QSettings.Format.IniFormat))
        self.prefs.start()
        old = date.today() - timedelta(days=2)
        self.checking = self.store.add_account(Account('Everyday checking', AccountType.CHECKING, 3000, old))
        self.savings = self.store.add_account(Account('Rainy day', AccountType.SAVINGS, 8000, old))
        self.loan = self.store.add_account(Account('Car loan', AccountType.LOAN, 9000, old, monthly_payment=300))
        for account, name in ((self.checking, 'Grocer'), (self.savings, 'Interest')):
            self.store.add_transactions(pd.DataFrame([dict(
                account_id=account, date=date.today() - timedelta(days=5), description=name,
                amount=-40 if account == self.checking else 12, category='Groceries',
                import_hash=f'nav-{name}')]))
        self.win = MainWindow(self.store)
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.store.close()
        self.prefs.stop()
        self.tmp.cleanup()

    def names(self, tabs):
        return [tabs.tabText(i) for i in range(tabs.count())]

    def test_transactions_is_a_tab_of_accounts_not_its_own_rail_item(self):
        win = self.win
        self.assertEqual([win.nav.item(i).text() for i in range(win.nav.count())],
                         ['Home', 'Accounts', 'Bills & payday', 'Plan', 'Forecast'])
        self.assertEqual(self.names(win.accounts_tabs), ['Balances', 'Transactions'])
        self.assertEqual(len(win.sections), win.nav.count())
        for name, row in (('Accounts', 1), ('Balances', 1), ('Transactions', 1), ('Recurring', 2),
                          ('Calendar', 2), ('Goals', 3), ('Plan', 3), ('Monthly budget', 3),
                          ('Projection', 4), ('Progress', 4)):
            self.assertEqual(win._destinations[name][0], row, name)

    def test_page_names_open_the_right_tab_and_the_rail_remembers_the_last_one(self):
        win = self.win
        win.show_page('Transactions')
        self.assertEqual(type(win.current_page()).__name__, 'TransactionsPage')
        self.assertEqual(win.nav.currentItem().text(), 'Accounts')
        win.show_page('Dashboard')
        win.nav.setCurrentRow(1)
        self.assertEqual(type(win.current_page()).__name__, 'TransactionsPage')
        win.show_page('Accounts')
        self.assertEqual(type(win.current_page()).__name__, 'AccountsPage')

    def test_switching_tabs_refreshes_the_page_that_appears(self):
        self.win.show_page('Accounts')
        with patch.object(self.win.pages[2], 'refresh') as refresh:
            self.win.accounts_tabs.setCurrentIndex(1)
            refresh.assert_called_once()

    def test_view_transactions_opens_that_accounts_ledger_with_other_filters_cleared(self):
        win = self.win
        win.show_page('Transactions')
        tx = win.current_page()
        tx.search.setText('left over')
        tx.f_review.setCurrentIndex(1)
        win.show_page('Accounts')
        accounts = win.current_page()
        accounts._select_id(self.savings)
        self.assertFalse(accounts.view_btn.isHidden())
        accounts.view_btn.click()
        self.app.processEvents()
        self.assertIs(win.current_page(), tx)
        self.assertEqual(win.nav.currentItem().text(), 'Accounts')
        self.assertEqual(tx.f_account.currentData(), self.savings)
        self.assertEqual(tx.f_review.currentIndex(), 0)
        self.assertEqual(tx.search.text(), '')
        self.assertIsNone(tx.f_category.currentData())

    def test_view_transactions_is_offered_only_for_accounts_that_hold_transactions(self):
        win = self.win
        win.show_page('Accounts')
        accounts = win.current_page()
        accounts._select_id(self.loan)
        self.assertTrue(accounts.view_btn.isHidden())
        accounts._select_id(self.checking)
        self.assertFalse(accounts.view_btn.isHidden())

    def test_the_selection_bar_keeps_at_most_four_action_controls(self):
        win = self.win
        win.show_page('Accounts')
        accounts = win.current_page()
        accounts._select_id(self.checking)
        bar_buttons = [b for b in accounts.sel_bar.findChildren(type(accounts.more_btn))
                       if b.objectName() != 'link' and not b.isHidden()]
        self.assertLessEqual(len(bar_buttons), 4)
        self.assertEqual(accounts.view_btn.objectName(), 'link')

    def test_add_account_still_opens_the_account_dialog_from_anywhere(self):
        self.win.show_page('Dashboard')
        with patch('finfore.ui.accounts.AccountDialog') as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.win.open_account()
            dialog.assert_called_once()


class HomeFixture:
    """A window on the demo ledger, shared by the Home and Simple-view tests."""

    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        from finfore.demo import load_demo
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'home.db')
        self.prefs = patch.object(prefs, 'app_settings', lambda: QSettings(
            str(Path(self.tmp.name) / 'prefs.ini'), QSettings.Format.IniFormat))
        self.prefs.start()
        load_demo(self.store, 'balanced', checkpoints=0)
        self.win = MainWindow(self.store)
        self.win.resize(1280, 900)
        self.win.show()
        self.win.show_page('Dashboard')
        self.app.processEvents()
        self.dash = self.win.pages[0]
        self.dash.refresh()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.store.close()
        self.prefs.stop()
        self.tmp.cleanup()

    def rows(self):
        return [(self.dash.look_rows.itemAt(i).layout().itemAt(0).widget().text(),
                 self.dash.look_rows.itemAt(i).layout().itemAt(1).widget())
                for i in range(self.dash.look_rows.count())]



class HomeTests(HomeFixture, unittest.TestCase):
    """Home leads with the verdict and says what needs a look; Import is always at hand."""

    def test_the_first_rail_item_is_home_and_dashboard_still_opens_it(self):
        self.assertEqual(self.win.nav.item(0).text(), 'Home')
        self.win.show_page('Accounts')
        self.win.show_page('Dashboard')
        self.assertEqual(self.win.nav.currentItem().text(), 'Home')
        self.win.show_page('Home')
        self.assertEqual(self.win.nav.currentItem().text(), 'Home')

    def test_the_strip_lists_at_most_three_things_each_with_a_link(self):
        self.assertFalse(self.dash.needs_look.isHidden())
        rows = self.rows()
        self.assertTrue(0 < len(rows) <= 3)
        for sentence, button in rows:
            self.assertTrue(sentence)
            self.assertEqual(button.objectName(), 'link')
            self.assertIn(sentence, button.accessibleName())
        self.assertTrue(any('transaction' in sentence and ('need a look' in sentence or 'needs a look' in sentence)
                            for sentence, _ in rows))

    def test_the_review_link_opens_transactions_on_what_needs_attention(self):
        review = next(b for s, b in self.rows() if b.text() == 'Review')
        review.click()
        self.app.processEvents()
        tx = self.win.current_page()
        self.assertEqual(type(tx).__name__, 'TransactionsPage')
        self.assertEqual(tx.f_review.currentData(), 'attention')
        self.assertEqual(self.win.nav.currentItem().text(), 'Accounts')

    def test_the_balances_link_opens_accounts_and_the_checks_link_opens_the_forecast(self):
        links = {button.text(): button for _s, button in self.rows()}
        if 'Check balances' in links:
            links['Check balances'].click()
            self.assertEqual(type(self.win.current_page()).__name__, 'AccountsPage')
        self.win.show_page('Dashboard')
        self.dash.refresh()
        links = {button.text(): button for _s, button in self.rows()}
        if 'See checks' in links:
            links['See checks'].click()
            self.assertEqual(type(self.win.current_page()).__name__, 'ForecastPage')

    def test_the_strip_is_absent_when_nothing_needs_a_look_on_a_first_run_and_while_expanded(self):
        with patch.object(type(self.dash), '_look_items', return_value=[]):
            self.dash.refresh()
        self.assertTrue(self.dash.needs_look.isHidden())
        self.dash.refresh()
        self.assertFalse(self.dash.needs_look.isHidden())
        self.dash.expand_btn.click()
        self.assertTrue(self.dash.needs_look.isHidden())
        self.dash.expand_btn.click()
        self.assertFalse(self.dash.needs_look.isHidden())
        empty = Store(Path(self.tmp.name) / 'empty.db')
        try:
            self.dash.store = empty
            self.dash.refresh()
            self.assertTrue(self.dash.needs_look.isHidden())
        finally:
            self.dash.store = self.store
            empty.close()

    def test_the_strip_title_is_amber_not_coral(self):
        from finfore.ui import theme
        style = self.dash.needs_look.title.styleSheet()
        self.assertIn(theme.AMBER, style)
        self.assertNotIn(theme.RED, style)

    def test_the_verdict_comes_first_then_the_figures_then_the_chart_then_what_needs_a_look(self):
        top = lambda w: w.mapTo(self.dash.body, self.dash.body.rect().topLeft()).y()
        self.assertLess(top(self.dash.verdict_band), top(self.dash.stats_w))
        self.assertLess(top(self.dash.stats_w), top(self.dash.hist_card))
        self.assertLess(top(self.dash.hist_card), top(self.dash.needs_look))

    def test_import_is_in_the_header_on_every_page_and_opens_the_menus_flow(self):
        for name in ('Home', 'Accounts', 'Bills & payday', 'Plan', 'Forecast'):
            self.win.show_page(name)
            self.assertTrue(self.win.import_btn.isVisibleTo(self.win), name)
        with patch.object(MainWindow, 'open_import') as opened:
            other = MainWindow(self.store)
            try:
                other.show()
                other.import_btn.click()
                opened.assert_called_once()
            finally:
                other.close()
                other.deleteLater()


class SimpleViewTabsTests(HomeFixture, unittest.TestCase):
    """Simple view shows fewer tabs; a link to a hidden one reveals it."""

    def sections(self):
        w = self.win
        return {'Home': w.dashboard_tabs, 'Accounts': w.accounts_tabs, 'Bills': w.bills_tabs,
                'Plan': w.plan_tabs, 'Forecast': w.forecast_tabs}

    def labels(self, tabs):
        return [tabs.tabText(i) for i in range(tabs.count()) if tabs.isTabVisible(i)]

    def setUp(self):
        super().setUp()
        if not self.win.simple_mode:
            self.win._toggle_mode()

    def test_the_tabs_are_renamed_for_what_they_are(self):
        w = self.win
        self.assertEqual([w.forecast_tabs.tabText(i) for i in range(w.forecast_tabs.count())],
                         ['Projection', 'Track record'])
        self.assertEqual([w.bills_tabs.tabText(i) for i in range(w.bills_tabs.count())],
                         ['Calendar', 'Detected bills && pay'])    # && is how a tab shows a single &

    def test_simple_view_hides_transactions_and_track_record_and_their_tab_bars(self):
        s = self.sections()
        self.assertEqual(self.labels(s['Accounts']), ['Balances'])
        self.assertEqual(self.labels(s['Forecast']), ['Projection'])
        self.assertTrue(s['Accounts'].tabBar().isHidden())
        self.assertTrue(s['Forecast'].tabBar().isHidden())
        for name in ('Home', 'Bills', 'Plan'):
            self.assertEqual(len(self.labels(s[name])), 2, name)
            self.assertFalse(s[name].tabBar().isHidden(), name)

    def test_everything_view_shows_every_tab(self):
        self.win._toggle_mode()
        s = self.sections()
        self.assertEqual(self.labels(s['Accounts']), ['Balances', 'Transactions'])
        self.assertEqual(self.labels(s['Forecast']), ['Projection', 'Track record'])
        for tabs in s.values():
            self.assertFalse(tabs.tabBar().isHidden())

    def test_a_link_to_a_hidden_tab_reveals_it_and_it_stays_until_the_view_is_toggled(self):
        w = self.win
        w.show_page('Transactions')
        self.assertEqual(type(w.current_page()).__name__, 'TransactionsPage')
        self.assertEqual(self.labels(w.accounts_tabs), ['Balances', 'Transactions'])
        self.assertFalse(w.accounts_tabs.tabBar().isHidden())
        w.show_page('Dashboard')
        w.show_page('Accounts')
        self.assertEqual(self.labels(w.accounts_tabs), ['Balances', 'Transactions'])
        w.show_page('Progress')
        self.assertEqual(type(w.current_page()).__name__, 'ProgressPage')
        self.assertEqual(self.labels(w.forecast_tabs), ['Projection', 'Track record'])
        w._toggle_mode()
        w._toggle_mode()
        self.assertEqual(self.labels(w.accounts_tabs), ['Balances'])
        self.assertEqual(self.labels(w.forecast_tabs), ['Projection'])

    def test_switching_to_simple_while_on_a_hidden_tab_lands_on_the_visible_one(self):
        w = self.win
        w._toggle_mode()
        w.show_page('Transactions')
        w.show_page('Progress')
        w._toggle_mode()
        self.assertEqual(self.labels(w.accounts_tabs), ['Balances'])
        w.show_page('Accounts')
        self.assertEqual(type(w.current_page()).__name__, 'AccountsPage')
        w.show_page('Projection')
        self.assertEqual(type(w.current_page()).__name__, 'ForecastPage')

    def test_the_home_review_link_still_works_in_simple_view(self):
        review = next(b for _s, b in self.rows() if b.text() == 'Review')
        review.click()
        self.app.processEvents()
        tx = self.win.current_page()
        self.assertEqual(type(tx).__name__, 'TransactionsPage')
        self.assertEqual(tx.f_review.currentData(), 'attention')
        self.assertIn('Transactions', self.labels(self.win.accounts_tabs))

    def test_view_transactions_on_a_balance_reveals_the_ledger_in_simple_view(self):
        w = self.win
        w.show_page('Accounts')
        accounts = w.current_page()
        cash = next(a for a in self.store.accounts() if a.type.is_cashlike)
        accounts._select_id(cash.id)
        accounts.view_btn.click()
        self.assertEqual(type(w.current_page()).__name__, 'TransactionsPage')
        self.assertEqual(w.current_page().f_account.currentData(), cash.id)

    def test_the_documentation_describes_the_five_sidebar_sections(self):
        root = Path(__file__).resolve().parent.parent
        guide = (root / 'docs' / 'getting-started.md').read_text()
        readme = (root / 'README.md').read_text()
        self.assertIn('five sections in the sidebar', guide)
        self.assertNotIn('seven sections', guide)
        for text in (guide, readme):
            for stale in ('Recurring patterns', '| Goals |', '| Transactions |', '→ Progress'):
                self.assertNotIn(stale, text, stale)
            self.assertIn('Track record', text)


if __name__ == '__main__':
    unittest.main()
