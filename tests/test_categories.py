"""Categorization safety checks using hand-labeled synthetic descriptions.

These fixtures test specific defects and compatibility; they are not an
accuracy estimate for a real bank export or an unseen household.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finfore.categorize import categorize, pair_transfers
from finfore.learning import (NearestMerchant, feedback_key, full_chain,
                              label_cluster, predict_one, recategorize_all,
                              record_corrections, training_frame,
                              uncategorized_clusters)
from finfore.models import Account, AccountType
from finfore.pipeline import refresh_derived
from finfore.recurrence import detect
from finfore.store import Store


MERCHANT_CASES = (
    ("WATERSTONES BOOKSHOP", -30, "Misc"),
    ("PARENT CHILD DAYCARE", -100, "Misc"),
    ("SHELLFISH MARKET", -40, "Misc"),
    ("GYMSHARK", -70, "Misc"),
    ("RENTAL EQUIPMENT", -70, "Misc"),
    ("FLOWERS BY SHELLY", -40, "Misc"),
    ("INTERESTING BOOKS", -25, "Misc"),
    ("POWERFUL FITNESS", -50, "Health & fitness"),
    ("PAYCHECKER SOFTWARE", -20, "Misc"),
    ("COSTCO GAS #124", -45, "Fuel"),
    ("COSTCO WHOLESALE #124", -100, "Groceries"),
    ("SAM'S CLUB GAS #423", -45, "Fuel"),
    ("DISNEY WORLD TICKETS", -200, "Travel"),
    ("DISNEYLAND", -200, "Travel"),
    ("DISNEY PLUS", -15, "Subscriptions"),
    ("AMAZON PRIME MEMBERSHIP", -15, "Subscriptions"),
    ("AMZN PRIME", -15, "Subscriptions"),
    ("AMAZON MKTPL", -30, "Shopping"),
    ("SHELL ENERGY", -120, "Utilities"),
    ("SHELL OIL #44", -45, "Fuel"),
    ("ENTERPRISE RENT-A-CAR", -120, "Travel"),
    ("UBER-EATS", -20, "Dining"),
    ("UBER TRIP", -20, "Transport"),
    ("CAFÉ AROMA", -10, "Dining"),
    ("AT&T WIRELESS", -80, "Utilities"),
    ("T–MOBILE", -80, "Utilities"),
    ("APPLE.COM/BILL", -5, "Subscriptions"),
    ("APPLE COM BILL", -5, "Subscriptions"),
    ("H-E-B #123", -80, "Groceries"),
    ("HEB", -80, "Groceries"),
    ("REI", -80, "Shopping"),
    ("BP#224", -45, "Fuel"),
    ("DIRECT DEPOSIT ACME", 1500, "Income"),
    ("INTEREST PAYMENT", 5, "Interest & dividends"),
    ("DIVIDENDS", 5, "Interest & dividends"),
    ("CREDIT CARD INTEREST CHARGE", -20, "Debt payment"),
    ("FINANCE INTEREST", -20, "Debt payment"),
    ("RED CROSS DONATION", -50, "Charitable donation"),
    ("DONATIONS", -50, "Charitable donation"),
    ("COMMUNITY FOUNDATION", -50, "Charitable donation"),
    ("FOUNDATION BOOKSHOP", -30, "Misc"),
    ("TRANSFER TO SAVINGS", -200, "Transfer"),
    ("ONLINE TRANSFER FROM CHECKING", 200, "Transfer"),
    ("VANGUARD BROKERAGE TRANSFER", -200, "Transfer"),
    ("WISE TRANSFER TO MARY", -100, "Misc"),
    ("VENMO TRANSFER", -100, "Misc"),
    ("UNKNOWN TRANSFER RECEIVED", 100, "Other income"),
    ("AMEX EPAYMENT", -100, "Debt payment"),
    ("DISCOVER E-PAYMENT", -100, "Debt payment"),
    ("UNKNOWN VENDOR", -100, "Misc"),
    ("UNKNOWN VENDOR", 100, "Other income"),
)


class KeywordTests(unittest.TestCase):
    def test_hand_labeled_descriptions(self):
        for description, amount, expected in MERCHANT_CASES:
            with self.subTest(description=description):
                self.assertEqual(categorize(description, amount), expected)

    def test_store_number_and_case_variants(self):
        for description in ("ALDI5501", "aldi #5501", "POS ALDI*5501"):
            self.assertEqual(categorize(description, -50), "Groceries")
        self.assertEqual(categorize(None, -20), "Misc")


class TransferTests(unittest.TestCase):
    def rows(self, left="UNKNOWN", right="UNKNOWN", category_left="Misc",
             category_right="Other income", amount=50, day_gap=1, same_account=False):
        return pd.DataFrame([
            dict(account_id=1, date=pd.Timestamp("2026-01-01"), amount=-amount,
                 description=left, category=category_left),
            dict(account_id=1 if same_account else 2,
                 date=pd.Timestamp("2026-01-01") + pd.Timedelta(days=day_gap),
                 amount=amount, description=right, category=category_right),
        ])

    def test_equal_amounts_alone_do_not_hide_spending(self):
        self.assertFalse(pair_transfers(self.rows()).any())
        self.assertFalse(pair_transfers(self.rows("ALDI", "PAYROLL", "Groceries", "Income")).any())
        self.assertFalse(pair_transfers(self.rows("CARD PAYMENT", "PAYROLL", "Debt payment", "Income")).any())
        self.assertFalse(pair_transfers(self.rows("CARD INTEREST CHARGE", "INTEREST PAID",
                                                "Debt payment", "Interest & dividends")).any())

    def test_supported_transfer_and_card_payment_pairs(self):
        self.assertTrue(pair_transfers(self.rows("TRANSFER TO SAVINGS", "BANK CREDIT")).all())
        self.assertTrue(pair_transfers(self.rows("AMEX EPAYMENT", "PAYMENT THANK YOU",
                                               "Debt payment", "Other income")).all())

    def test_dates_account_identity_zero_and_one_to_one(self):
        self.assertFalse(pair_transfers(self.rows("TRANSFER", day_gap=5)).any())
        self.assertFalse(pair_transfers(self.rows("TRANSFER", same_account=True)).any())
        self.assertFalse(pair_transfers(self.rows("TRANSFER", amount=0)).any())
        tx = self.rows("TRANSFER")
        tx = pd.concat([tx, tx.iloc[[1]]], ignore_index=True)
        self.assertEqual(int(pair_transfers(tx).sum()), 2)
        tx.loc[0, "date"] = pd.NaT
        self.assertFalse(pair_transfers(tx).any())


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "categories.db")
        self.account = self.store.add_account(Account("Checking", AccountType.CHECKING,
                                                     balance=1000, balance_date=date.today()))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def correct(self, description, amount, category):
        rows = pd.DataFrame([dict(description=description, amount=amount,
                                  category="Misc", account_id=self.account)])
        record_corrections(self.store, rows, category)

    def add(self, description, amount, category="Misc", days_ago=0):
        self.store.add_transactions(pd.DataFrame([dict(
            account_id=self.account, date=date.today() - timedelta(days=days_ago),
            description=description, amount=amount, category=category,
            import_hash=f"{description}-{amount}-{days_ago}-{len(self.store.transactions())}")]))

    def test_refunds_and_purchases_do_not_share_similarity_labels(self):
        self.correct("BLUE BOTTLE COFFEE", -10, "Dining")
        near = NearestMerchant.from_store(self.store)
        self.assertEqual(near.predict("SQ *BLUE BOTTLE 042", -15), "Dining")
        self.assertIsNone(near.predict("SQ *BLUE BOTTLE 042", 15))
        self.correct("BLUE BOTTLE COFFEE", 10, "Other income")
        near = NearestMerchant.from_store(self.store)
        self.assertEqual(near.predict("SQ *BLUE BOTTLE 042", 15), "Other income")
        self.assertEqual(near.predict("SQ *BLUE BOTTLE 042", -15), "Dining")

    def test_conflicting_categories_abstain_regardless_of_order(self):
        labels = [("ACME STORAGE UNIT", "Housing"), ("ACME STORAGE UNIT", "Shopping")]
        for order in (labels, labels[::-1]):
            near = NearestMerchant(order)
            self.assertIsNone(near.predict("ACME STORAGE DEPOT", -100))
            self.assertIsNone(near.explain("ACME STORAGE DEPOT", -100)[0])

    def test_repeated_examples_of_same_category_do_not_cause_conflict(self):
        near = NearestMerchant([("ACME STORAGE UNIT", "Housing"),
                                ("ACME STORAGE BUILDING", "Housing")])
        self.assertEqual(near.predict("ACME STORAGE DEPOT", -100), "Housing")
        self.assertIsNone(near.predict("ACMECO STORAGE UNIT", -100))

    def test_transfer_not_propagated_but_explicit_correction_wins(self):
        self.correct("ACME BANK SAVINGS", -100, "Transfer")
        self.assertIsNone(NearestMerchant.from_store(self.store).predict("ACME BANK CHECKING", -100))
        self.assertEqual(predict_one(full_chain(self.store), "ACME BANK SAVINGS", -100), "Transfer")
        self.correct("ACME BANK SAVINGS", -100, "Housing")
        self.assertEqual(predict_one(full_chain(self.store), "ACME BANK SAVINGS", -100), "Housing")

    def test_inflow_queue_remembers_correct_direction(self):
        self.add("MYSTERY PAYROLLSOURCE", 150, "Other income")
        cluster = uncategorized_clusters(self.store, months=None)[0]
        label_cluster(self.store, cluster, "Income")
        frame = training_frame(self.store)
        self.assertEqual(frame.iloc[0]["is_inflow"], 1)
        self.assertGreater(frame.iloc[0]["amount"], 0)
        self.assertEqual(predict_one(full_chain(self.store), "MYSTERY PAYROLLSOURCE", 150), "Income")
        self.assertNotEqual(predict_one(full_chain(self.store), "MYSTERY PAYROLLSOURCE", -150), "Income")

    def test_legacy_inflow_feedback_keeps_its_direction_without_rewriting_history(self):
        self.store.add_category_feedback([dict(norm_key=feedback_key("MYSTERY BONUS", 100),
            description="MYSTERY BONUS", amount=-100, new_category="Income", source="review-queue")])
        frame = training_frame(self.store)
        self.assertEqual(frame.iloc[0]["amount"], 100)
        self.assertEqual(frame.iloc[0]["is_inflow"], 1)
        self.assertEqual(self.store.category_feedback().iloc[0]["amount"], -100)
        near = NearestMerchant.from_store(self.store)
        self.assertIsNone(near.predict("MYSTERY BONUS PAYOUT", -100))
        self.assertEqual(near.predict("MYSTERY BONUS PAYOUT", 100), "Income")

    def test_queue_labels_older_unknown_rows_and_preserves_known_categories(self):
        self.add("MYSTERY SHOP", -40, days_ago=1)
        self.add("MYSTERY SHOP", -50, days_ago=800)
        self.add("MYSTERY SHOP", -60, "Groceries", days_ago=2)
        cluster = uncategorized_clusters(self.store, months=3)[0]
        self.assertEqual(cluster.n_tx, 1)
        self.assertEqual(label_cluster(self.store, cluster, "Shopping"), 2)
        self.assertEqual(list(self.store.transactions().sort_values("id")["category"]),
                         ["Shopping", "Shopping", "Groceries"])
        self.assertEqual(recategorize_all(self.store), 0)

    def test_manual_categories_survive_transfer_refresh(self):
        other = self.store.add_account(Account("Savings", AccountType.SAVINGS,
                                               balance=1000, balance_date=date.today()))
        self.add("TRANSFER TO SAVINGS", -50, "Housing")
        self.store.add_transactions(pd.DataFrame([dict(account_id=other, date=date.today(),
            description="BANK CREDIT", amount=50, category="Other income", import_hash="other")]))
        self.correct("TRANSFER TO SAVINGS", -50, "Housing")
        refresh_derived(self.store, checkpoint=False)
        self.assertEqual(list(self.store.transactions().sort_values("id")["category"]),
                         ["Housing", "Other income"])

    def test_recurring_detection_uses_ledger_category(self):
        tx = pd.DataFrame([dict(date=date(2026, month, 1), description="NAVIENT", amount=-100,
                                category="Shopping") for month in range(1, 5)])
        self.assertEqual(detect(tx)[0].kind, "bill")
        tx["category"] = "Debt payment"
        self.assertEqual(detect(tx)[0].kind, "loan_payment")

    def test_matching_unknowns_can_still_be_paired(self):
        other = self.store.add_account(Account("Savings", AccountType.SAVINGS,
                                               balance=1000, balance_date=date.today()))
        self.add("TRANSFER TO SAVINGS", -50)
        self.store.add_transactions(pd.DataFrame([dict(account_id=other, date=date.today(),
            description="BANK CREDIT", amount=50, category="Other income", import_hash="pair")]))
        refresh_derived(self.store, checkpoint=False)
        self.assertTrue(self.store.transactions()["category"].eq("Transfer").all())


def run_tests():
    return unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))


if __name__ == "__main__":
    unittest.main()
