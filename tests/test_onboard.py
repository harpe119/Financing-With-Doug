"""The first day: every page that has nothing yet says so once and offers the next step."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pandas as pd
from PyQt6.QtCore import QEvent, QSettings, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QMessageBox

from finfore.demo import load_demo
from finfore.models import Account, AccountType
from finfore.store import Store
from finfore.ui import prefs, theme
from finfore.ui.first_use import NO_ACCOUNTS, NO_TRANSACTIONS, READY, setup_stage
from finfore.ui.main_window import MainWindow
from test_usability import desktop_test_app


class Fixture:
    """A window on a database that starts empty (or with a demo, if asked)."""
    demo = False

    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'first-day.db')
        self.prefs = patch.object(prefs, 'app_settings', lambda: QSettings(
            str(Path(self.tmp.name) / 'prefs.ini'), QSettings.Format.IniFormat))
        self.prefs.start()
        if self.demo:
            load_demo(self.store, 'balanced')
        self.previous_sheet = self.app.styleSheet()
        theme.apply(self.app)
        self.win = MainWindow(self.store)
        self.win.resize(1280, 900)
        self.win.show()
        self.settle()

    def tearDown(self):
        self.win.close()
        self.app.processEvents()
        self.win.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.setStyleSheet(self.previous_sheet)
        self.store.close()
        self.prefs.stop()
        self.tmp.cleanup()

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def go(self, name):
        self.win.show_page(name)
        self.settle()
        return self.win.current_page()

    def add_account(self):
        self.store.add_account(Account('Everyday Checking', AccountType.CHECKING,
                                       balance=3200.0, balance_date=date.today()))
        self.win.refresh_current()
        self.settle()

    def rail(self, name):
        return self.win.nav.item(self.win._rail_names.index(name))


class StageTests(Fixture, unittest.TestCase):
    def test_the_stage_follows_accounts_then_statements(self):
        self.assertEqual(setup_stage(self.store), NO_ACCOUNTS)
        self.add_account()
        self.assertEqual(setup_stage(self.store), NO_TRANSACTIONS)
        aid = self.store.accounts()[0].id
        self.store.add_transactions(pd.DataFrame([dict(
            account_id=aid, date=date.today(), description='Coffee', amount=-4.5,
            category='Dining', import_hash='one')]))
        self.assertEqual(setup_stage(self.store), READY)
        self.assertTrue(self.store.has_transactions())


class EmptyPageTests(Fixture, unittest.TestCase):
    PAGES = (('Spending', 'Your spending will show up here', 'body_split'),
             ('Calendar', 'Your bills and paydays will show up here', 'body'),
             ('Recurring', 'No bills or paychecks found yet', 'table'))

    def panel_text(self, page):
        return page.first_use.title.text()

    def test_each_empty_page_says_what_it_will_hold_and_hides_what_would_be_empty(self):
        for name, title, hidden in self.PAGES:
            page = self.go(name)
            self.assertTrue(page.first_use.isVisibleTo(page), name)
            self.assertEqual(self.panel_text(page), title.upper())
            self.assertFalse(getattr(page, hidden).isVisibleTo(page), f'{name}: {hidden}')

    def test_the_button_follows_the_step_the_person_is_on(self):
        page = self.go('Spending')
        self.assertEqual(page.first_use.primary.text(), 'Add an account')
        self.assertTrue(page.first_use.demo.isVisibleTo(page))
        self.add_account()
        page = self.go('Spending')
        self.assertEqual(page.first_use.primary.text(), 'Import a CSV…')
        self.assertIn('One complete month', page.first_use.why.text())

    def test_the_add_an_account_step_opens_the_dialog_and_the_import_step_opens_the_import(self):
        page = self.go('Calendar')
        with patch.object(self.win, 'add_account') as dialog:
            page.first_use.primary.click()
        dialog.assert_called_once()
        self.assertEqual(type(self.win.current_page()).__name__, 'CalendarPage')    # no detour
        self.add_account()
        page = self.go('Calendar')
        with patch.object(self.win, 'open_import') as opened:
            page.first_use.primary.click()
        opened.assert_called_once()

    def test_the_demo_link_loads_the_demo_without_asking(self):
        page = self.go('Recurring')
        with patch.object(self.win, '_load_demo') as demo:
            page.first_use.demo.click()
        demo.assert_called_once_with('balanced', confirm=False)

    def test_a_ledger_with_no_repeats_says_so_and_offers_to_look_again(self):
        self.add_account()
        aid = self.store.accounts()[0].id
        self.store.add_transactions(pd.DataFrame([dict(
            account_id=aid, date=date.today(), description='One off', amount=-9.0,
            category='Shopping', import_hash='x1')]))
        page = self.go('Recurring')
        self.assertTrue(page.first_use.isVisibleTo(page))
        self.assertIn('at least three matching entries', page.first_use.why.text())
        self.assertEqual(page.first_use.primary.text(), 'Re-detect from ledger')
        self.assertFalse(page.first_use.demo.isVisibleTo(page))
        with patch.object(page, '_redetect') as again:
            page.first_use._act()
        again.assert_called_once()


class DataHidesThePanelsTests(Fixture, unittest.TestCase):
    demo = True

    def test_with_data_the_panels_are_gone_and_the_pages_are_whole(self):
        for name, _title, hidden in EmptyPageTests.PAGES:
            page = self.go(name)
            self.assertFalse(page.first_use.isVisibleTo(page), name)
            self.assertTrue(getattr(page, hidden).isVisibleTo(page), name)

    def test_the_rail_has_nothing_dimmed_once_there_is_data(self):
        for name in self.win._rail_names:
            item = self.rail(name)
            self.assertEqual(item.toolTip(), '')
            self.assertFalse(item.font().italic())


class RailTests(Fixture, unittest.TestCase):
    def test_sections_that_fill_in_from_statements_are_dimmed_until_there_are_some(self):
        for name in ('Bills & payday', 'Forecast'):
            item = self.rail(name)
            self.assertTrue(item.font().italic(), name)
            self.assertTrue(item.toolTip(), name)
            self.assertEqual(item.data(Qt.ItemDataRole.AccessibleDescriptionRole), item.toolTip())
        for name in ('Home', 'Accounts', 'Plan'):
            self.assertFalse(self.rail(name).font().italic(), name)
            self.assertEqual(self.rail(name).toolTip(), '')

    def test_the_forecast_tooltip_changes_once_there_is_an_account(self):
        before = self.rail('Forecast').toolTip()
        self.add_account()
        after = self.rail('Forecast').toolTip()
        self.assertNotEqual(before, after)
        self.assertIn('balances alone', after)

    def test_importing_statements_brings_the_rail_back_to_normal(self):
        self.add_account()
        aid = self.store.accounts()[0].id
        self.store.add_transactions(pd.DataFrame([dict(
            account_id=aid, date=date.today(), description='Coffee', amount=-4.5,
            category='Dining', import_hash='c1')]))
        self.win.refresh_header()
        for name in self.win._rail_names:
            self.assertFalse(self.rail(name).font().italic(), name)

    def test_a_dimmed_item_is_muted_and_a_normal_one_is_not(self):
        self.assertEqual(self.rail('Forecast').foreground().color().name().lower(), theme.MUTED.lower())
        self.assertEqual(self.rail('Home').foreground().color().name().lower(), theme.RAIL_TEXT.lower())


class FirstImportTests(Fixture, unittest.TestCase):
    def fake_import(self, store, count=3):
        aid = store.accounts()[0].id

        class Dialog:
            def __init__(inner, *_args, **_kwargs):
                pass

            def exec(inner):
                store.add_transactions(pd.DataFrame([dict(
                    account_id=aid, date=date.today(), description=f'Row {i}', amount=-5.0 - i,
                    category='Dining', import_hash=f'fake{i}{store.con.total_changes}')
                    for i in range(count)]))
                return True
        return Dialog

    def test_with_no_account_the_import_button_opens_the_add_account_dialog(self):
        with patch.object(QMessageBox, 'information', side_effect=AssertionError('no modal')), \
                patch.object(self.win, 'add_account') as dialog:
            self.win.open_import()
        dialog.assert_called_once()
        self.assertIn('Add an account first', self.win.statusBar().currentMessage())

    def test_the_first_import_ends_on_home_where_the_answer_is(self):
        self.add_account()
        self.go('Spending')
        with patch('finfore.ui.main_window.ImportDialog', self.fake_import(self.store)):
            self.win.open_import()
        self.settle()
        self.assertEqual(type(self.win.current_page()).__name__, 'DashboardPage')
        self.assertIn('This is where you stand', self.win.statusBar().currentMessage())

    def test_a_later_import_leaves_the_person_where_they_were(self):
        self.add_account()
        with patch('finfore.ui.main_window.ImportDialog', self.fake_import(self.store, 3)):
            self.win.open_import()
        page = self.go('Spending')
        with patch('finfore.ui.main_window.ImportDialog', self.fake_import(self.store, 2)):
            self.win.open_import()
        self.settle()
        self.assertIs(self.win.current_page(), page)

    def test_a_cancelled_first_import_changes_nothing(self):
        self.add_account()
        page = self.go('Accounts')

        class Cancelled:
            def __init__(self, *_a, **_k):
                pass

            def exec(self):
                return False
        with patch('finfore.ui.main_window.ImportDialog', Cancelled):
            self.win.open_import()
        self.assertIs(self.win.current_page(), page)


class PlanSetupTests(Fixture, unittest.TestCase):
    def plan(self):
        return self.go('Monthly budget')

    def test_the_budget_says_what_it_is_waiting_for_once_and_offers_the_next_step(self):
        page = self.plan()
        self.assertEqual(page.verdict.text(), 'Monthly plan not ready')
        self.assertIn('Doug builds this plan from one complete month', page.setup.text())
        self.assertEqual(page.import_btn.text(), 'Add an account')
        self.assertTrue(page.equation.isHidden())
        with patch.object(self.win, 'add_account') as dialog:
            page.import_btn.click()
        dialog.assert_called_once()

    def test_with_an_account_the_button_imports(self):
        self.add_account()
        page = self.plan()
        self.assertEqual(page.import_btn.text(), 'Import a CSV…')
        with patch.object(self.win, 'open_import') as opened:
            page.import_btn.click()
        opened.assert_called_once()


class NeedsALookExplainedTests(Fixture, unittest.TestCase):
    demo = True

    def test_the_review_link_says_why_these_transactions_are_flagged(self):
        from PyQt6.QtWidgets import QPushButton
        dash = self.go('Dashboard')
        review = [b for b in dash.needs_look.findChildren(QPushButton) if b.text() == 'Review']
        self.assertTrue(review)
        self.assertIn('guessed these categories', review[0].toolTip())
        page = self.go('Transactions')
        self.assertIn('guessed these categories', page.attention_btn.toolTip())


class AddAccountFlowTests(Fixture, unittest.TestCase):
    class FakeDialog:
        """Stands in for the add-account dialog: accepts and hands back an account."""
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            from PyQt6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted

        def get_account(self):
            return Account('Everyday Checking', AccountType.CHECKING, balance=3200.0,
                           balance_date=date.today())

    def test_add_account_works_from_home_without_going_to_the_accounts_page(self):
        dash = self.go('Overview')
        self.assertEqual(dash.steps[0]['btn'].text(), 'Add an account')
        with patch('finfore.ui.accounts.AccountDialog', self.FakeDialog):
            dash.steps[0]['btn'].click()
        self.settle()
        self.assertEqual(len(self.store.accounts()), 1)
        self.assertIs(self.win.current_page(), dash)
        self.assertTrue(dash.steps[1]['btn'].isVisibleTo(dash.welcome))
        self.assertEqual(dash.steps[1]['btn'].objectName(), 'primary')

    def test_the_home_step_button_asks_the_main_window_for_the_dialog(self):
        dash = self.go('Overview')
        with patch.object(self.win, 'add_account') as dialog:
            dash.steps[0]['btn'].click()
        dialog.assert_called_once()

    def test_a_cancelled_dialog_adds_nothing(self):
        class Cancelled(self.FakeDialog):
            def exec(self):
                from PyQt6.QtWidgets import QDialog
                return QDialog.DialogCode.Rejected
        with patch('finfore.ui.accounts.AccountDialog', Cancelled):
            self.win.add_account()
        self.assertEqual(self.store.accounts(), [])


class FirstRunLayoutTests(Fixture, unittest.TestCase):
    def test_with_one_account_the_figures_lead_and_the_next_step_sits_under_them(self):
        self.add_account()
        dash = self.go('Overview')
        top = lambda w: w.mapTo(dash.body, w.rect().topLeft()).y()
        figures_bottom = top(dash.stats_w) + dash.stats_w.height()
        self.assertLess(top(dash.stats_w), top(dash.welcome))
        card = dash.welcome.findChildren(QFrame)[0]
        self.assertLessEqual(card.mapTo(dash.body, card.rect().topLeft()).y() - figures_bottom, 60)

    def test_with_no_account_the_welcome_stands_alone_and_is_centred(self):
        dash = self.go('Overview')
        self.assertTrue(dash.stats_w.isHidden())
        card = dash.welcome.findChildren(QFrame)[0]
        above = card.mapTo(dash.welcome, card.rect().topLeft()).y()
        below = dash.welcome.height() - above - card.height()
        self.assertGreater(above, 20)
        self.assertGreater(below, 20)

    def test_the_keyboard_starts_on_the_step_to_take(self):
        dash = self.go('Overview')
        self.win.activateWindow()
        self.settle()
        dash.focus_first_step()
        self.settle()
        self.assertTrue(dash.steps[0]['btn'].hasFocus())


class ImportReadingTests(Fixture, unittest.TestCase):
    def dialog(self, name, text):
        from finfore.ui.import_dialog import ImportDialog
        self.add_account()
        path = Path(self.tmp.name) / name
        path.write_text(text)
        dialog = ImportDialog(self.store, self.win, account_id=self.store.accounts()[0].id)
        dialog.show()
        self.settle()
        return dialog, str(path)

    STATEMENT = ('Date,Description,Amount\n'
                 + ''.join(f'2026-01-{d:02d},STORE {d},-{10 + d}.00\n' for d in range(1, 12))
                 + '2026-01-12,ACME PAYROLL,2000.00\n')
    POSITIVE = 'Date,Description,Amount\n' + ''.join(f'2026-01-{d:02d},STORE {d},{10 + d}.00\n' for d in range(1, 12))

    def test_before_a_file_is_chosen_there_is_nothing_to_read_or_adjust(self):
        dialog, _ = self.dialog('s.csv', self.STATEMENT)
        self.assertTrue(dialog.read_card.isHidden())
        self.assertTrue(dialog.adjust.isHidden())
        self.assertIn('Ctrl+Z', ' '.join(label.text() for label in dialog.findChildren(QLabel)))
        dialog.close()

    def test_a_good_file_is_summarised_in_plain_terms_with_its_first_rows(self):
        dialog, path = self.dialog('s.csv', self.STATEMENT)
        dialog.path.setText(path)
        dialog._reload()
        self.settle()
        self.assertEqual(dialog.read_summary.text(),
                         '12 transactions · Jan 1, 2026 to Jan 12, 2026 · 11 money out, 1 money in')
        self.assertEqual(dialog.read_table.rowCount(), 3)
        self.assertEqual(dialog.read_table.item(0, 1).text(), 'STORE 1')
        self.assertEqual(dialog.read_table.item(0, 2).text(), '-$11.00')
        self.assertFalse(dialog.read_warning.isVisibleTo(dialog))
        self.assertFalse(dialog.adjust.expanded)
        self.assertTrue(dialog.go.isEnabled())
        dialog.close()

    def test_the_columns_are_behind_adjust_and_open_when_asked(self):
        dialog, path = self.dialog('s.csv', self.STATEMENT)
        dialog.path.setText(path)
        dialog._reload()
        self.settle()
        self.assertFalse(dialog.c_date.isVisibleTo(dialog))
        dialog.adjust.header.click()
        self.settle()
        self.assertTrue(dialog.adjust.expanded)
        self.assertTrue(dialog.c_date.isVisibleTo(dialog))
        self.assertGreaterEqual(dialog.height(), 700)
        dialog.close()

    def test_a_file_of_all_positive_amounts_is_flagged_and_flipping_clears_it(self):
        dialog, path = self.dialog('p.csv', self.POSITIVE)
        dialog.path.setText(path)
        dialog._reload()
        self.settle()
        self.assertTrue(dialog.read_warning.isVisibleTo(dialog))
        self.assertIn('Nearly every row reads as money in', dialog.read_warning.text())
        dialog.sign.setCurrentIndex(dialog.sign.findData('flip'))
        self.settle()
        self.assertFalse(dialog.read_warning.isVisibleTo(dialog))
        self.assertIn('11 money out, 0 money in', dialog.read_summary.text())
        dialog.close()

    def test_an_unreadable_setup_says_why_and_opens_the_columns_by_itself(self):
        dialog, path = self.dialog('bad.csv', 'When,What,How much\n12/31/2025,COFFEE,abc\n')
        dialog.path.setText(path)
        dialog._reload()
        self.settle()
        self.assertIn("can't read this file", dialog.read_summary.text())
        self.assertTrue(dialog.read_warning.isVisibleTo(dialog))
        self.assertTrue(dialog.read_warning.text())
        self.assertTrue(dialog.adjust.expanded)
        self.assertTrue(dialog.read_table.isHidden())
        dialog.close()

    def test_choosing_a_column_updates_the_reading(self):
        dialog, path = self.dialog('bad.csv', 'When,What,How much\n12/31/2025,COFFEE,-4.50\n01/02/2026,RENT,-900.00\n')
        dialog.path.setText(path)
        dialog._reload()
        for combo, column in ((dialog.c_date, 'When'), (dialog.c_desc, 'What'), (dialog.c_amount, 'How much')):
            combo.setCurrentIndex(combo.findData(column))
        self.settle()
        self.assertIn('2 transactions', dialog.read_summary.text())
        self.assertFalse(dialog.read_warning.isVisibleTo(dialog))
        dialog.close()

    def test_importing_still_uses_the_same_settings_the_summary_read(self):
        dialog, path = self.dialog('s.csv', self.STATEMENT)
        dialog.path.setText(path)
        dialog._reload()
        cfg = dialog._config()
        self.assertEqual((cfg.col_date, cfg.col_desc, cfg.col_amount), ('Date', 'Description', 'Amount'))
        self.assertEqual(cfg.sign_mode, 'standard')
        dialog.close()


class DialogHintAndBannerTests(Fixture, unittest.TestCase):
    def test_the_cents_rule_is_said_under_the_balance_field(self):
        from finfore.ui.account_dialog import AccountDialog
        dialog = AccountDialog(self.win)
        dialog.show()
        self.settle()
        self.assertEqual(dialog.cents_hint.text(), 'Type 320000 for $3,200.00, or paste $3,200.00.')
        balance_y = dialog.balance.mapTo(dialog, dialog.balance.rect().bottomLeft()).y()
        hint_y = dialog.cents_hint.mapTo(dialog, dialog.cents_hint.rect().topLeft()).y()
        self.assertLess(hint_y - balance_y, 16)
        dialog.close()

    def test_the_demo_banner_carries_its_own_way_back(self):
        self.assertTrue(self.win.banner_bar.isHidden())
        self.win.demo_key = 'balanced'
        self.win._sync_banner()
        self.settle()
        self.assertTrue(self.win.banner_bar.isVisibleTo(self.win))
        self.assertEqual(self.win.banner_back.text(), 'Return to my own data')
        self.assertNotIn('File →', self.win.banner.text())
        with patch.object(self.win, 'return_to_real') as back:
            self.win.banner_back.click()
        back.assert_called_once()
        self.win.demo_key = None
        self.win._sync_banner()
        self.assertTrue(self.win.banner_bar.isHidden())
        self.assertTrue(self.win.banner.isHidden())


class StaleNamesTests(unittest.TestCase):
    def test_nothing_still_sends_a_person_to_recurring_patterns(self):
        root = Path(__file__).resolve().parent.parent / 'finfore'
        offenders = [str(p.relative_to(root)) for p in root.rglob('*.py')
                     if 'Recurring patterns' in p.read_text(encoding='utf-8')
                     .replace('Recurring patterns are excluded', '')]     # a code comment
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()
