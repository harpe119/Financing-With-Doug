"""Add / edit account dialog. Fields adapt to the account type."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                             QVBoxLayout)

from ..models import Account, AccountType
from .widgets import IntSpinBox, MoneySpinBox, hint

_TYPES = [AccountType.CHECKING, AccountType.SAVINGS, AccountType.CREDIT,
          AccountType.LOAN, AccountType.INVESTMENT]

# Rates are quoted to four places on real loan paperwork (6.4900%, 3.8750%),
# and rounding one to 6.49% moves a 30-year balance by a visible amount. Two
# places is right for a return assumption you are guessing at anyway; four is
# right for a number copied off a statement.
RATE_DECIMALS = 4


def _money_spin(lo=-1e9, hi=1e9) -> QDoubleSpinBox:
    s = MoneySpinBox()
    s.setRange(lo, hi)
    s.setDecimals(2)
    s.setGroupSeparatorShown(True)
    s.setPrefix("$ ")
    s.setToolTip("Type the amount as you would say it — 1234.56, or 1234. "
                 "Dollar signs and commas are fine.")
    return s


def _pct_spin(lo=0.0, hi=100.0, decimals: int = 2,
              step: float = 1.0) -> QDoubleSpinBox:
    s = MoneySpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setSuffix(" %")
    return s


def _rate_spin(lo=0.0, hi=100.0) -> QDoubleSpinBox:
    """A percentage entered from a statement — full precision, small steps."""
    return _pct_spin(lo, hi, decimals=RATE_DECIMALS, step=0.05)


class AccountDialog(QDialog):
    def __init__(self, parent=None, account: Account | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit account" if account else "Add account")
        self.setMinimumWidth(380)
        self._editing = account

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(9)
        lay.addLayout(form)

        self.name = QLineEdit()
        self.type = QComboBox()
        for t in _TYPES:
            self.type.addItem(t.label, t)
        self.balance = _money_spin()
        self.balance_date = QDateEdit(QDate.currentDate())
        self.balance_date.setCalendarPopup(True)
        self.balance_date.setDisplayFormat("yyyy-MM-dd")

        form.addRow("Name", self.name)
        form.addRow("Type", self.type)
        self.balance_label = QLabel("Balance")
        form.addRow(self.balance_label, self.balance)
        form.addRow("Balance as of", self.balance_date)

        self.balance_hint = hint("")
        lay.addWidget(self.balance_hint)

        # loan fields
        self.apr = _rate_spin()
        self.apr.setToolTip("The rate from your statement — four decimal "
                            "places are kept (e.g. 6.4900%).")
        self.payment = _money_spin(0)
        self.payment_day = IntSpinBox()
        self.payment_day.setRange(1, 28)
        self._loan_rows = [
            (QLabel("APR"), self.apr),
            (QLabel("Monthly payment"), self.payment),
            (QLabel("Payment day of month"), self.payment_day),
        ]
        # investment fields
        self.annual_return = _pct_spin(-50, 50)
        self.annual_return.setValue(7.0)
        self.volatility = _pct_spin(0, 100)
        self.volatility.setValue(15.0)
        self.contribution = _money_spin(0)
        self._inv_rows = [
            (QLabel("Expected annual return"), self.annual_return),
            (QLabel("Volatility (σ, annual)"), self.volatility),
            (QLabel("Monthly contribution"), self.contribution),
        ]
        for lbl, w in self._loan_rows + self._inv_rows:
            form.addRow(lbl, w)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.type.currentIndexChanged.connect(self._sync_fields)
        self._sync_fields()

        if account:
            self.name.setText(account.name)
            self.type.setCurrentIndex(_TYPES.index(account.type))
            self.balance.setValue(
                -account.balance if account.type == AccountType.CREDIT
                else account.balance)
            d = account.balance_date
            self.balance_date.setDate(QDate(d.year, d.month, d.day))
            self.apr.setValue(account.apr * 100)
            self.payment.setValue(account.monthly_payment)
            self.payment_day.setValue(account.payment_day)
            self.annual_return.setValue(account.annual_return * 100)
            self.volatility.setValue(account.volatility * 100)
            self.contribution.setValue(account.monthly_contribution)

    @staticmethod
    def _owed_type(t: AccountType) -> bool:
        """Types entered as a positive amount owed rather than a balance held.

        A card and a loan are both money you owe, and asking for one as −1,500
        and the other as +1,500 is a distinction with no meaning to the person
        typing. Both are entered positive here; the credit card's stored sign
        is flipped on the way in and out, because internally a card sits on the
        cash side of the ledger where a purchase has to be negative.
        """
        return t in (AccountType.CREDIT, AccountType.LOAN)

    def _sync_fields(self) -> None:
        t: AccountType = self.type.currentData()
        self.balance_label.setText(
            "Amount owed" if self._owed_type(t) else "Balance")
        for lbl, w in self._loan_rows:
            lbl.setVisible(t == AccountType.LOAN)
            w.setVisible(t == AccountType.LOAN)
        for lbl, w in self._inv_rows:
            lbl.setVisible(t == AccountType.INVESTMENT)
            w.setVisible(t == AccountType.INVESTMENT)
        tips = {
            AccountType.CHECKING: "Import this account's CSV so history and "
                                  "habits can be learned from it.",
            AccountType.SAVINGS: "Import this account's CSV so history and "
                                 "habits can be learned from it.",
            AccountType.CREDIT: "Enter what you owe as a positive number — "
                                "the same way as a loan. A negative figure "
                                "means the card is in credit. Import its CSV "
                                "like a cash account.",
            AccountType.LOAN: "Enter what you owe as a positive number. "
                              "Import the lender's CSV — balances or payment "
                              "activity — to draw its history. A payment "
                              "detected in your cash imports links automatically.",
            AccountType.INVESTMENT: "Tracked by dated balances and projected "
                                    "with an expected return. Import a "
                                    "balance or activity CSV to give it a "
                                    "history line.",
        }
        self.balance_hint.setText(tips[t])
        self.adjustSize()

    def _validate(self) -> None:
        # Spin boxes only fold their text into value() when editing ends, which
        # clicking OK does trigger. Doing it explicitly costs nothing and means
        # no route to accept can ever save the figure that was there before.
        for w in (self.balance, self.apr, self.payment, self.annual_return,
                  self.volatility, self.contribution, self.payment_day):
            w.interpretText()
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def get_account(self) -> Account:
        d = self.balance_date.date()
        a = Account(
            name=self.name.text().strip(),
            type=self.type.currentData(),
            balance=(-self.balance.value()
                     if self.type.currentData() == AccountType.CREDIT
                     else self.balance.value()),
            balance_date=date(d.year(), d.month(), d.day()),
            apr=self.apr.value() / 100.0,
            annual_return=self.annual_return.value() / 100.0,
            volatility=self.volatility.value() / 100.0,
            monthly_contribution=self.contribution.value(),
            monthly_payment=self.payment.value(),
            payment_day=self.payment_day.value(),
        )
        if self._editing:
            a.id = self._editing.id
        return a
