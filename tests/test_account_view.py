"""What each Accounts row says, tested without a window."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finfore.account_view import (account_statuses, ago_text, duration_text,
                                  held_amount, money_exact, owed_amount,
                                  payoff_months, split_accounts, summary_line,
                                  terms_text)
from finfore.history import headline_stats
from finfore.models import Account, AccountType
from finfore.store import Store


class AccountViewTests(unittest.TestCase):
    today = date(2026, 10, 11)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "accounts.db")
        self._seq = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def account(self, name="Checking", kind=AccountType.CHECKING, days=0, balance=1000.0, **kw):
        return self.store.add_account(Account(
            name, kind, balance=balance,
            balance_date=self.today - timedelta(days=days), **kw))

    def transactions(self, aid, days_ago):
        rows = []
        for n in days_ago:
            self._seq += 1
            rows.append(dict(account_id=aid, date=self.today - timedelta(days=n),
                             amount=-10, category="Groceries", description="ALDI",
                             import_hash=f"view-{self._seq}"))
        self.store.add_transactions(pd.DataFrame(rows))

    def status(self, aid):
        return account_statuses(self.store, self.today)[aid]

    # ---- status ---------------------------------------------------------
    def test_recent_balance_and_activity_is_up_to_date(self):
        aid = self.account()
        self.transactions(aid, [1, 20, 60])
        s = self.status(aid)
        self.assertEqual((s.text, s.level, s.fix), ("Up to date", "ok", None))

    def test_a_cash_account_with_no_transactions_offers_the_import(self):
        s = self.status(self.account())
        self.assertEqual((s.text, s.level, s.fix),
                         ("No transactions imported yet", "info", "import"))

    def test_old_transactions_and_old_balance_are_both_said_in_amber(self):
        aid = self.account(days=45)
        self.transactions(aid, [50, 80])
        s = self.status(aid)
        self.assertEqual(s.level, "caution")
        self.assertIn("Last transaction 50 days ago", s.text)
        self.assertIn("Balance 45 days old", s.text)
        self.assertEqual(s.fix, "import")      # the first thing worth doing

    def test_stale_balance_alone_offers_a_balance_update(self):
        aid = self.account(days=40)
        self.transactions(aid, [1, 5])
        s = self.status(aid)
        self.assertEqual((s.text, s.level, s.fix),
                         ("Balance 40 days old", "caution", "balance"))

    def test_a_future_dated_balance_is_flagged(self):
        aid = self.account(days=-3)
        self.transactions(aid, [1])
        self.assertIn("future", self.status(aid).text)

    def test_loans_and_investments_only_age_by_their_balance(self):
        loan = self.account("Loan", AccountType.LOAN, days=5, balance=5000)
        inv = self.account("Brokerage", AccountType.INVESTMENT, days=90, balance=9000)
        sts = account_statuses(self.store, self.today)
        self.assertEqual(sts[loan].text, "Up to date")
        self.assertEqual(sts[inv].text, "Balance 90 days old")
        self.assertEqual(sts[inv].fix, "balance")

    def test_an_impossible_history_is_flagged_for_the_balance_check(self):
        # Money in far exceeds the balance: the account would have had to open
        # deeply negative for the numbers to agree.
        aid = self.account(balance=50.0)
        rows = [dict(account_id=aid, date=self.today - timedelta(days=n), amount=500,
                     category="Income", description="PAYROLL", import_hash=f"imp-{n}")
                for n in range(1, 20)]
        self.store.add_transactions(pd.DataFrame(rows))
        s = self.status(aid)
        self.assertEqual((s.level, s.fix), ("caution", "check"))
        self.assertIn("Doesn’t add up", s.text)

    def test_amber_never_red_and_no_green(self):
        aid = self.account(days=60)
        self.assertNotIn(self.status(aid).level, ("bad", "error", "good"))

    def test_summary_line(self):
        a = self.account("A")
        self.transactions(a, [1])
        sts = account_statuses(self.store, self.today)
        accts = self.store.accounts()
        self.assertEqual(summary_line(accts, sts), "1 account. Its balance is recent.")
        b = self.account("B", days=50)
        self.transactions(b, [60])
        sts = account_statuses(self.store, self.today)
        self.assertEqual(summary_line(self.store.accounts(), sts), "2 accounts. 1 needs a look.")
        self.assertEqual(summary_line([], {}), "No accounts yet.")

    # ---- grouping ties to net worth ---------------------------------------
    def test_groups_add_up_to_the_header_net_worth(self):
        self.account("Checking", AccountType.CHECKING, balance=1500.25)
        self.account("Savings", AccountType.SAVINGS, balance=8000)
        self.account("Card", AccountType.CREDIT, balance=-1200.5)      # owed
        self.account("Credit in hand", AccountType.CREDIT, balance=40)  # in credit
        self.account("Car loan", AccountType.LOAN, balance=7000)
        self.account("Brokerage", AccountType.INVESTMENT, balance=2000)
        accounts = self.store.accounts()
        have, owe = split_accounts(accounts)
        self.assertEqual({a.name for a in owe}, {"Card", "Car loan"})
        net = (sum(held_amount(a) for a in have)
               - sum(owed_amount(a) for a in owe))
        self.assertAlmostEqual(net, headline_stats(self.store)["networth"], places=6)

    # ---- the words ----------------------------------------------------------
    def test_payoff_months(self):
        self.assertEqual(payoff_months(1000, 0, 100), 10)
        self.assertEqual(payoff_months(0, 0.05, 100), 0)
        self.assertIsNone(payoff_months(1000, 0.2, 10), "payment below the interest never pays off")
        self.assertIsNone(payoff_months(1000, 0.05, 0))
        # Matches the Forecast demo loan: about 39 months.
        self.assertEqual(payoff_months(14250, 0.064, 412), 39)

    def test_duration_text(self):
        self.assertEqual(duration_text(1), "1 month")
        self.assertEqual(duration_text(18), "18 months")
        self.assertEqual(duration_text(39), "3 years")

    def test_terms_are_plain_and_labelled(self):
        loan = Account("Auto", AccountType.LOAN, balance=14250, apr=0.064,
                       monthly_payment=412, payment_day=15)
        self.assertEqual(terms_text(loan),
                         "6.4% APR · $412/mo on day 15 · paid off in about 3 years")
        inv = Account("Brokerage", AccountType.INVESTMENT, balance=1,
                      annual_return=0.0725, volatility=0.15, monthly_contribution=400)
        self.assertEqual(terms_text(inv),
                         "Assumes 7.25% a year, swinging ±15% · adds $400/mo")
        self.assertEqual(terms_text(Account("C", AccountType.CHECKING)), "—")
        stuck = Account("Bad", AccountType.LOAN, balance=1000, apr=0.2, monthly_payment=10)
        self.assertIn("doesn’t cover the interest", terms_text(stuck))

    def test_money_keeps_cents_and_puts_the_sign_first(self):
        self.assertEqual(money_exact(12415.57), "$12,415.57")
        self.assertEqual(money_exact(-1469), "-$1,469.00")
        self.assertEqual(money_exact(1522.17, signed=True), "+$1,522.17")
        self.assertEqual(money_exact(-0.001), "$0.00")

    def test_ago_text(self):
        self.assertEqual([ago_text(n) for n in (0, 1, 12, 90, 800, -2)],
                         ["Today", "Yesterday", "12 days ago", "3 months ago",
                          "2 years ago", "In the future"])


if __name__ == "__main__":
    unittest.main()
