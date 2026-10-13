"""Add / edit account dialog. Fields adapt to the account type."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                             QVBoxLayout)

from ..models import Account, AccountType
from . import theme
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
    s.setToolTip("Enter digits as cents: 1234 becomes $12.34. Backspace removes "
                 "the last digit. You can also paste a formatted amount such as $1,234.56.")
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
        self.name.setPlaceholderText("e.g. Everyday checking")
        self.type = QComboBox()
        for t in _TYPES:
            self.type.addItem(t.label, t)
        self.balance = _money_spin()
        self.balance_date = QDateEdit(QDate.currentDate())
        self.balance_date.setCalendarPopup(True)
        self.balance_date.setDisplayFormat("MMM d, yyyy")

        form.addRow("Name", self.name)
        form.addRow("Type", self.type)
        self.balance_label = QLabel("Balance")
        self.balance_label.setBuddy(self.balance)
        form.addRow(self.balance_label, self.balance)
        form.addRow("Balance as of", self.balance_date)

        self.balance_hint = hint("")
        lay.addWidget(self.balance_hint)
        # Shown only while editing, and only while the date is following the
        # balance. Retyping a balance and leaving its old date in place quietly
        # makes the account look stale (and re-anchors its history to the wrong
        # day), so the date moves to today and says so.
        self.date_hint = hint("“Balance as of” moved to today because you changed "
                              "the balance. Change the date if the figure is "
                              "from another day.")
        self.date_hint.setVisible(False)
        lay.addWidget(self.date_hint)
        self._date_touched = False
        self._orig_balance: float | None = None

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.RED};")
        self.error.setVisible(False)
        lay.addWidget(self.error)
        self.name.textChanged.connect(lambda _t: self.error.setVisible(False))

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
        self.annual_return.setToolTip(
            "The yearly growth you are assuming. It is an assumption, not a "
            "promise, and the forecast shows a range around it.")
        self.volatility.setToolTip(
            "How much the value typically swings up and down in a year. "
            "Higher means a bumpier ride and a wider forecast range.")
        self._inv_rows = [
            (QLabel("Expected yearly return"), self.annual_return),
            (QLabel("Yearly ups and downs (volatility)"), self.volatility),
            (QLabel("Monthly contribution"), self.contribution),
        ]
        for lbl, w in self._loan_rows + self._inv_rows:
            lbl.setBuddy(w)
            form.addRow(lbl, w)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Save changes" if account else "Add account")
        ok.setObjectName("primary")
        ok.setDefault(True)
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
            self._orig_balance = self.balance.value()
            self.apr.setValue(account.apr * 100)
            self.payment.setValue(account.monthly_payment)
            self.payment_day.setValue(account.payment_day)
            self.annual_return.setValue(account.annual_return * 100)
            self.volatility.setValue(account.volatility * 100)
            self.contribution.setValue(account.monthly_contribution)
        # Connected last, so filling the form in does not count as the person
        # changing the balance or choosing a date.
        self.balance.valueChanged.connect(self._balance_changed)
        self.balance_date.dateChanged.connect(self._date_edited)

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
        entry = " Type digits as cents: 1234 becomes $12.34."
        tips = {
            AccountType.CHECKING: "Money you have, ready to spend. Import this "
                                  "account's CSV so history and habits can be "
                                  "learned from it." + entry,
            AccountType.SAVINGS: "Money you have, set aside. Import this "
                                 "account's CSV so history and habits can be "
                                 "learned from it." + entry,
            AccountType.CREDIT: "Money you owe. Enter what you owe as a "
                                "positive number — the same way as a loan. A "
                                "negative figure means the card is in credit. "
                                "Import its CSV like a cash account." + entry,
            AccountType.LOAN: "Money you owe. Enter what you owe as a positive "
                              "number. Import the lender's CSV — balances or "
                              "payment activity — to draw its history. A "
                              "payment detected in your cash imports links "
                              "automatically." + entry,
            AccountType.INVESTMENT: "Money you've invested. Tracked by dated "
                                    "balances and projected with an expected "
                                    "return. Import a balance or activity CSV "
                                    "to give it a history line." + entry,
        }
        self.balance_hint.setText(tips[t])
        self.adjustSize()

    def _balance_changed(self, value: float) -> None:
        """While editing, a changed balance carries its date to today."""
        if (self._editing is None or self._orig_balance is None
                or self._date_touched):
            return
        if abs(value - self._orig_balance) < 0.005:
            self.date_hint.setVisible(False)
            d = self._editing.balance_date
            self.balance_date.blockSignals(True)
            self.balance_date.setDate(QDate(d.year, d.month, d.day))
            self.balance_date.blockSignals(False)
            return
        self.balance_date.blockSignals(True)
        self.balance_date.setDate(QDate.currentDate())
        self.balance_date.blockSignals(False)
        self.date_hint.setVisible(True)

    def _date_edited(self, _d) -> None:
        """Once the person sets the date themselves, stop moving it for them."""
        self._date_touched = True
        self.date_hint.setVisible(False)

    def _validate(self) -> None:
        # Spin boxes only fold their text into value() when editing ends, which
        # clicking OK does trigger. Doing it explicitly costs nothing and means
        # no route to accept can ever save the figure that was there before.
        for w in (self.balance, self.apr, self.payment, self.annual_return,
                  self.volatility, self.contribution, self.payment_day):
            w.interpretText()
        if not self.name.text().strip():
            self.error.setText("Enter a name for this account, such as "
                               "“Everyday checking”.")
            self.error.setVisible(True)
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
