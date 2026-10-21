"""Bank holdings and cash commitments stay distinct in calculations and charts."""
from datetime import date, timedelta
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from finfore.forecast import run_forecast
from finfore.models import Account, AccountType, Goal, Scenario
from finfore.store import Store
from finfore.util import month_firsts_between
from finfore.ui import charts
from test_onboard import Fixture


class CashAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'cash.db')
        self.today = date.today()
        self.aid = self.store.add_account(Account('Checking', AccountType.CHECKING,
                                                 5000, self.today))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def budget(self, spending=1000, reserve=100, allowance=200):
        self.store.save_spending_plan(dict(income=3000, essential=spending, debt=0,
            frivolous=0, giving=0, unknown=0, investing=0, reserve=reserve), allowance)

    def test_saved_and_future_goal_money_stays_in_bank_and_net_worth(self):
        self.store.add_goal(Goal('Vacation', 1000, 'savings', saved_amount=300))
        self.budget()
        res = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        # 300 already earmarked; 200 each future first; 100/month reserve.
        expected = 300 + np.arange(91) * 100 / 30.44
        for first in month_firsts_between(self.today, res.dates[-1]):
            expected[(first - self.today).days:] += 200
        np.testing.assert_allclose(res.reserved_cash, expected)
        self.assertAlmostEqual(res.cash_det[-1], 5000 + 2000 * 90 / 30.44)
        np.testing.assert_allclose(res.networth_det, res.cash_det)
        np.testing.assert_allclose(res.free_cash_det, res.cash_det - expected)
        for percentile in res.cash_pct:
            np.testing.assert_allclose(res.free_cash_pct[percentile],
                                       res.cash_pct[percentile] - expected)
        self.assertEqual(self.store.goals()[0].saved_amount, 300)

    def test_reduced_spending_increases_bank_cash_under_same_funding(self):
        self.store.add_goal(Goal('Vacation', 1000, 'savings'))
        self.budget(spending=1000)
        before = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        self.budget(spending=700)
        after = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        self.assertAlmostEqual(after.cash_det[-1] - before.cash_det[-1], 300 * 90 / 30.44)
        np.testing.assert_allclose(after.reserved_cash, before.reserved_cash)

    def test_without_plan_only_recorded_cash_earmarks_reduce_free_cash(self):
        self.store.add_goal(Goal('Vacation', 1000, 'savings', saved_amount=300))
        self.budget()
        result = run_forecast(self.store, 90, 20)
        np.testing.assert_array_equal(result.cash_det, np.full(91, 5000))
        np.testing.assert_array_equal(result.reserved_cash, np.full(91, 300))

    def test_investments_purchases_and_balance_milestones_do_not_double_reserve(self):
        invest = self.store.add_account(Account('Investments', AccountType.INVESTMENT,
                                                2000, self.today))
        gid = self.store.add_goal(Goal('Vacation', 1200, 'savings', saved_amount=800))
        self.store.record_goal_activity(gid, 'assign', 500, self.today, invest)
        self.store.record_goal_activity(gid, 'purchase', 100, self.today, None)
        self.store.add_goal(Goal('Cash milestone', 1000, 'cash'))
        self.store.add_goal(Goal('Net worth milestone', 1000, 'networth'))
        self.budget(reserve=0, allowance=1000)
        result = run_forecast(self.store, 90, 20, Scenario(follow_goal_plan=True))
        self.assertEqual(result.reserved_cash[0], 200)  # 700 saved minus 500 invested
        self.assertEqual(result.reserved_cash[-1], 600)  # only the 400 still needed
        self.assertEqual(next(g for g in self.store.goals() if g.id == gid).spent_amount, 100)

    def test_loan_payoff_cascades_into_savings_without_a_second_cash_debit(self):
        loan = self.store.add_account(Account('Loan', AccountType.LOAN, 100,
                                              self.today, monthly_payment=100))
        gid = self.store.add_goal(Goal('Vacation', 500, 'savings'))
        self.store.set_plan_order([f'loan:{loan}', f'goal:{gid}'])
        self.store.set_setting('plan_surplus', 0)
        result = run_forecast(self.store, 100, 20, Scenario(follow_goal_plan=True))
        self.assertEqual(result.cash_det[-1], 4900)
        firsts = month_firsts_between(self.today, result.dates[-1])
        self.assertEqual(result.reserved_cash[(firsts[0] - self.today).days], 0)
        self.assertEqual(result.reserved_cash[(firsts[1] - self.today).days], 100)

    def test_overcommitted_free_cash_does_not_create_a_bank_shortfall(self):
        self.store.add_goal(Goal('Vacation', 10000, 'savings', saved_amount=6000))
        result = run_forecast(self.store, 30, 20)
        self.assertEqual(result.free_cash_det[0], -1000)
        self.assertEqual(result.cash_breach['zero'], 0)
        self.assertEqual(result.shortfall.prob_any, 0)

    def test_past_forecasts_do_not_use_current_earmarks_or_plan(self):
        self.store.add_goal(Goal('Vacation', 1000, 'savings', saved_amount=300))
        self.budget()
        result = run_forecast(self.store, 30, 20, Scenario(follow_goal_plan=True),
                              as_of=self.today - timedelta(days=10))
        np.testing.assert_array_equal(result.reserved_cash, np.zeros(31))
        np.testing.assert_array_equal(result.free_cash_det, result.cash_det)


class CashViewTests(Fixture, unittest.TestCase):
    demo = True

    def forecast(self):
        self.win.mode_all.click()
        self.settle()
        page = self.go('Projection')
        page.view_pick.setCurrentIndex(page.view_pick.findData('both'))
        return page

    def test_both_metrics_use_one_plot_and_share_the_reset_range(self):
        page = self.forecast()
        self.assertIs(page.plot_cash, page.plot_nw)
        self.assertEqual(len(page.cash_card.findChildren(charts.ChartPlot)), 1)
        self.assertTrue(page.cash_legend.isVisibleTo(page))
        self.assertTrue(page.nw_legend.isVisibleTo(page))
        self.assertFalse(page.nw_legend.actions['zero'].isEnabled())
        page.plot_cash.reset_axes_btn.click()
        low, high = page.plot_cash.viewRange()[1]
        for reg in (page.cash_series, page.nw_series):
            line = reg._groups['median'][0]
            self.assertGreaterEqual(float(line.yData.min()), low)
            self.assertLessEqual(float(line.yData.max()), high)
        page.view_pick.setCurrentIndex(page.view_pick.findData('networth'))
        self.assertEqual(page.cash_series._groups, {})
        self.assertTrue(page.nw_series._groups['median'][0].isVisible())
        self.assertFalse(page.risk_panel.isVisibleTo(page))
        self.assertTrue(page.nw_legend.actions['zero'].isEnabled())
        page.nw_legend.actions['zero'].setChecked(False)
        self.assertFalse(page.nw_series._groups['zero'][0].isVisible())

    def test_display_switch_updates_curves_and_comparison_without_running_model(self):
        page = self.forecast()
        self.store.add_goal(Goal('Extra savings', 2000, 'savings', saved_amount=500))
        page.refresh()
        page.overlay.setChecked(True)
        page.run()
        result, before, verdict = page._res, page.plot_cash.viewRange(), page.risk.text()
        worth = page.nw_series._groups['median'][0].yData.copy()
        with patch('finfore.ui.forecast_page.run_forecast') as run:
            page.cash_view.setCurrentIndex(page.cash_view.findData('free'))
            page.view_pick.setCurrentIndex(page.view_pick.findData('cash'))
            page.view_pick.setCurrentIndex(page.view_pick.findData('both'))
        run.assert_not_called()
        self.assertIs(page._res, result)
        self.assertEqual(page.plot_cash.viewRange(), before)
        self.assertEqual(page.risk.text(), verdict)
        self.assertNotIn('history', page.cash_series._groups)
        np.testing.assert_array_equal(page.nw_series._groups['median'][0].yData, worth)
        np.testing.assert_array_equal(page.cash_series._groups['median'][0].yData,
                                      result.free_cash_pct[50])
        np.testing.assert_array_equal(page.cash_series._groups['baseline'][0].yData,
                                      page._base.free_cash_pct[50])
        self.assertIn('Middle free cash outcome', page.cash_compare.text())
        self.assertEqual(self.store.get_setting('forecast_cash_view'), 'free')
        page.refresh()
        self.assertEqual(page.cash_view.currentData(), 'free')
        page.cash_view.setCurrentIndex(page.cash_view.findData('bank'))
        np.testing.assert_array_equal(page.cash_series._groups['median'][0].yData,
                                      page._res.cash_pct[50])


def run_tests():
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                               for case in (CashAccountingTests, CashViewTests))
    return unittest.TextTestRunner(verbosity=1).run(suite)


if __name__ == '__main__':
    unittest.main()
