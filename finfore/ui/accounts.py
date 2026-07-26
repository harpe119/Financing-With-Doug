"""Accounts page — the balance-sheet side of the model."""
from __future__ import annotations

from datetime import date

import pandas as pd
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from ..models import Account, AccountType
from ..ingest import (ImportConfig, SIGN_MODES, balances_from_activity,
                      guess_mapping, guess_snapshot_mapping, parse_snapshots,
                      parse_transactions, read_raw, sniff_delimiter)
from ..pipeline import reimport_with_saved_mapping, refresh_derived
from ..store import Store
from ..util import fmt_money, fmt_rate
from . import theme
from .account_dialog import AccountDialog, _money_spin
from .widgets import hint


class SnapshotEntryDialog(QDialog):
    """Add or edit a single snapshot's date/balance."""

    def __init__(self, parent=None, when: date | None = None,
                 balance: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle("Edit snapshot" if when else "Add snapshot")
        form = QFormLayout(self)
        self.when = QDateEdit(QDate(when.year, when.month, when.day)
                              if when else QDate.currentDate())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd")
        self.balance = _money_spin()
        self.balance.setValue(balance)
        form.addRow("Date", self.when)
        form.addRow("Balance", self.balance)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def get_values(self) -> tuple[date, float]:
        d = self.when.date()
        return date(d.year(), d.month(), d.day()), self.balance.value()


class SnapshotImportDialog(QDialog):
    """Import a loan/investment CSV as balance snapshots.

    Two shapes of file are accepted, because lenders export both and the user
    should not have to know which they were sent. A *balances* file lists a
    date and a balance per row and is read straight off. An *activity* file —
    far more common, and what a bank's own export tool produces — lists dated
    payments and charges; the balance history is integrated out of those flows
    and pinned to the account's current balance.
    """

    BALANCES, ACTIVITY = "balances", "activity"

    def __init__(self, store: Store, account: Account, parent=None):
        super().__init__(parent)
        self.store = store
        self.account = account
        self.setWindowTitle(f"Import history — {account.name}")
        self.setMinimumWidth(520)
        self._delim = ","
        self._columns: list[str] = []
        self._sample = None

        lay = QVBoxLayout(self)
        lay.addWidget(hint(
            "Loan and investment accounts get their history from dated "
            "balances. Import either a balance-over-time export, or an "
            "activity export of payments and charges — Doug will work the "
            "balances out of the flows. Rows match existing snapshots by date."))

        prow = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("No file selected")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        prow.addWidget(self.path, 1)
        prow.addWidget(browse)
        lay.addLayout(prow)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("This file lists"))
        self.mode = QComboBox()
        self.mode.addItem("Payments / activity — dated amounts", self.ACTIVITY)
        self.mode.addItem("Balances — a balance on each date", self.BALANCES)
        self.mode.currentIndexChanged.connect(self._sync_mode)
        mrow.addWidget(self.mode, 1)
        lay.addLayout(mrow)

        # --- balances mode ---
        self.bal_box = QWidget()
        bform = QFormLayout(self.bal_box)
        bform.setContentsMargins(0, 0, 0, 0)
        self.c_date = QComboBox()
        self.c_balance = QComboBox()
        bform.addRow("Date column", self.c_date)
        bform.addRow("Balance column", self.c_balance)
        lay.addWidget(self.bal_box)

        # --- activity mode ---
        self.act_box = QWidget()
        aform = QFormLayout(self.act_box)
        aform.setContentsMargins(0, 0, 0, 0)
        self.a_date = QComboBox()
        self.a_desc = QComboBox()
        self.a_amount = QComboBox()
        self.a_indicator = QComboBox()
        self.a_debit = QComboBox()
        self.a_credit = QComboBox()
        self.a_sign = QComboBox()
        self.a_sign.addItem("Auto-detect", "auto")
        for key, label in SIGN_MODES.items():
            self.a_sign.addItem(label, key)
        aform.addRow("Date column", self.a_date)
        aform.addRow("Description column", self.a_desc)
        aform.addRow("Amount column", self.a_amount)
        aform.addRow("Credit/debit indicator", self.a_indicator)
        aform.addRow("Debit / money out", self.a_debit)
        aform.addRow("Credit / money in", self.a_credit)
        aform.addRow("Amounts", self.a_sign)
        self.reduces = QCheckBox("Payments in this file reduce the balance")
        self.reduces.setChecked(True)
        self.reduces.setToolTip(
            "On a loan export the payments are credits to the lender, so they "
            "pay the balance down. Untick if the preview below comes out "
            "upside down.")
        aform.addRow("", self.reduces)
        lay.addWidget(self.act_box)

        self.dayfirst = QCheckBox("Day comes first in dates (e.g. 31/01/2025)")
        lay.addWidget(self.dayfirst)

        self.preview = hint("")
        lay.addWidget(self.preview)

        btns = QDialogButtonBox()
        self.ok = btns.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setObjectName("primary")
        self.ok.setEnabled(False)
        btns.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self._do_import)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        for c in (self.c_date, self.c_balance, self.a_date, self.a_desc,
                  self.a_amount, self.a_indicator, self.a_debit, self.a_credit,
                  self.a_sign):
            c.currentIndexChanged.connect(self._update_preview)
        for b in (self.dayfirst, self.reduces):
            b.stateChanged.connect(self._update_preview)
        self._sync_mode()

    # ---------------------------------------------------------------
    def _sync_mode(self) -> None:
        activity = self.mode.currentData() == self.ACTIVITY
        self.act_box.setVisible(activity)
        self.bal_box.setVisible(not activity)
        self.adjustSize()
        self._update_preview()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose CSV", "", "CSV files (*.csv *.txt);;All files (*)")
        if not path:
            return
        self.path.setText(path)
        try:
            self._delim = sniff_delimiter(path)
            self._sample = read_raw(path, self._delim, nrows=50)
            self._columns = list(self._sample.columns)
        except Exception as e:
            QMessageBox.warning(self, "Could not read file", str(e))
            return

        bal = guess_snapshot_mapping(self._columns)
        act = guess_mapping(self._columns, self._sample)
        pairs = [(self.c_date, bal["date"]), (self.c_balance, bal["balance"]),
                 (self.a_date, act["date"]), (self.a_desc, act["desc"]),
                 (self.a_amount, act["amount"]),
                 (self.a_indicator, act["indicator"]),
                 (self.a_debit, act["debit"]), (self.a_credit, act["credit"])]
        optional = {self.a_indicator, self.a_debit, self.a_credit}
        for combo, guess in pairs:
            combo.blockSignals(True)
            combo.clear()
            if combo in optional:
                combo.addItem("—", "")
            for col in self._columns:
                combo.addItem(col, col)
            i = combo.findData(guess)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)
        # A file with an activity shape (a description column, and either an
        # indicator or a debit/credit pair) is almost certainly a statement,
        # not a balance history — start on the mode that will actually work.
        looks_like_activity = bool(act["desc"]) and bool(
            act["amount"] or act["debit"] or act["credit"])
        self.mode.setCurrentIndex(0 if looks_like_activity else 1)
        self._sync_mode()

    # ---------------------------------------------------------------
    def _sign_mode(self) -> str:
        sign = self.a_sign.currentData()
        if sign != "auto":
            return sign
        if self.a_indicator.currentData() and self.a_amount.currentData():
            return "indicator"
        if (self.a_debit.currentData() or self.a_credit.currentData()) \
                and not self.a_amount.currentData():
            return "debit_credit"
        return "standard"

    def _records(self) -> list[tuple[date, float]]:
        """Parse the file into (date, balance) records for the current mode."""
        if self.mode.currentData() == self.BALANCES:
            return parse_snapshots(self.path.text(), self.c_date.currentData(),
                                   self.c_balance.currentData(), self._delim,
                                   self.dayfirst.isChecked())
        cfg = ImportConfig(
            path=self.path.text(),
            account_id=self.account.id,
            delimiter=self._delim,
            dayfirst=self.dayfirst.isChecked(),
            sign_mode=self._sign_mode(),
            col_date=self.a_date.currentData() or "",
            col_desc=self.a_desc.currentData() or "",
            col_amount=self.a_amount.currentData() or "",
            col_debit=self.a_debit.currentData() or "",
            col_credit=self.a_credit.currentData() or "",
            col_indicator=self.a_indicator.currentData() or "",
        )
        df = parse_transactions(cfg)
        return balances_from_activity(df, self.account.balance_date,
                                      self.account.balance,
                                      payments_reduce=self.reduces.isChecked())

    def _update_preview(self) -> None:
        if not self.path.text() or not self._columns:
            self.ok.setEnabled(False)
            self.preview.setText("")
            return
        try:
            recs = self._records()
        except ValueError as e:
            self.preview.setText(str(e))
            self.ok.setEnabled(False)
            return
        d0, b0 = recs[0]
        d1, b1 = recs[-1]
        note = (f"{len(recs)} balance points — {d0.isoformat()} at "
                f"{fmt_money(b0)} → {d1.isoformat()} at {fmt_money(b1)}.")
        if self.mode.currentData() == self.ACTIVITY:
            note += (f"  Worked back from the {fmt_money(self.account.balance)} "
                     f"balance on {self.account.balance_date.isoformat()}; "
                     f"principal only, so interest not itemised in the file "
                     f"isn't reflected. If the direction looks wrong, untick "
                     f"the box above.")
        self.preview.setText(note)
        self.ok.setEnabled(True)

    def _do_import(self) -> None:
        try:
            recs = self._records()
        except ValueError as e:
            QMessageBox.warning(self, "Import problem", str(e))
            return
        added, updated = self.store.import_snapshots(self.account.id, recs)
        QMessageBox.information(
            self, "Import complete",
            f"Added {added} balance point{'s' if added != 1 else ''}, "
            f"updated {updated}. {self.account.name} now has a history line, "
            f"and its current balance reflects the most recent row.")
        self.accept()


class SnapshotManagerDialog(QDialog):
    """List an account's snapshots with add / edit / delete."""

    def __init__(self, store: Store, account: Account, parent=None):
        super().__init__(parent)
        self.store = store
        self.account = account
        self.setWindowTitle(f"Snapshots — {account.name}")
        self.setMinimumSize(420, 420)

        lay = QVBoxLayout(self)
        cashlike = account.type.is_cashlike
        lay.addWidget(hint(
            "Snapshots are dated balance readings. For loans and investments "
            "they draw the history line directly. For cash accounts the "
            "history comes from the transaction ledger, and the newest "
            "snapshot is used as the balance the ledger is anchored to — so a "
            "snapshot is how you correct drift after an incomplete import."
            if cashlike else
            "Snapshots are dated balance readings; they draw the history line "
            "for this account. The current balance on the Accounts page always "
            "acts as the most recent point."))

        self.count_note = hint("")
        lay.addWidget(self.count_note)
        self.empty_note = hint(
            "No snapshots yet. Add them one at a time, or use Import CSV… to "
            "load a balance-over-time export from your lender or brokerage.")
        lay.addWidget(self.empty_note)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Date", "Balance"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self._edit())
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        add = QPushButton("Add…")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        edit = QPushButton("Edit…")
        edit.clicked.connect(self._edit)
        rm = QPushButton("Delete")
        rm.setObjectName("danger")
        rm.clicked.connect(self._delete)
        imp = QPushButton("Import CSV…")
        imp.clicked.connect(self._import_csv)
        bar.addWidget(add)
        bar.addWidget(edit)
        bar.addWidget(rm)
        bar.addWidget(imp)
        bar.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        lay.addLayout(bar)

        self._reload()

    def _import_csv(self) -> None:
        dlg = SnapshotImportDialog(self.store, self.account, self)
        if dlg.exec():
            self._reload()

    def _reload(self) -> None:
        """Repopulate the table from the store.

        This method's body had been absorbed into _import_csv, leaving every
        caller — including __init__ — raising AttributeError, which is why the
        Snapshots button appeared to do nothing at all.
        """
        df = self.store.snapshots(self.account.id)
        self.table.setRowCount(len(df))
        for r, (_, row) in enumerate(df.iterrows()):
            d_item = QTableWidgetItem(pd.Timestamp(row["date"]).date().isoformat())
            d_item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            b_item = QTableWidgetItem(fmt_money(row["balance"]))
            b_item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 0, d_item)
            self.table.setItem(r, 1, b_item)
        self.empty_note.setVisible(df.empty)
        self.count_note.setText(
            f"{len(df)} snapshot{'s' if len(df) != 1 else ''} · current balance "
            f"{fmt_money(self.account.balance)} as of "
            f"{self.account.balance_date.isoformat()}")

    def _selected_id(self) -> int | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        return self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)

    def _add(self) -> None:
        dlg = SnapshotEntryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d, bal = dlg.get_values()
            self.store.add_snapshot(self.account.id, d, bal)
            self._reload()

    def _edit(self) -> None:
        sid = self._selected_id()
        if sid is None:
            return
        r = self.table.currentRow()
        cur_date = date.fromisoformat(self.table.item(r, 0).text())
        df = self.store.snapshots(self.account.id)
        cur_bal = float(df.loc[df["id"] == sid, "balance"].iloc[0])
        dlg = SnapshotEntryDialog(self, when=cur_date, balance=cur_bal)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d, bal = dlg.get_values()
            try:
                self.store.update_snapshot(sid, d, bal)
            except ValueError as e:
                QMessageBox.warning(self, "Can't save snapshot", str(e))
                return
            self._reload()

    def _delete(self) -> None:
        sid = self._selected_id()
        if sid is None:
            return
        ok = QMessageBox.question(self, "Delete snapshot",
                                  "Delete this snapshot?")
        if ok == QMessageBox.StandardButton.Yes:
            self.store.delete_snapshot(sid)
            self._reload()


class BalanceCheckDialog(QDialog):
    """Per-account reconciliation: does the ledger agree with the balance?

    The reconstruction is exact arithmetic — B(start) = B(today) − ΣF — so when
    a history looks impossible it is because the inputs disagree, not because
    the maths is wrong. Showing every account side by side is what makes that
    obvious: three landing on a sensible opening and one on −$1,469 identifies
    the culprit in a glance, which no per-account warning does as well.
    """

    COLS = ["Account", "Rows", "Money in", "Money out", "Net", "Balance entered",
            "Implied opening", "If it started at $0"]

    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Check my balances")
        self.setMinimumSize(940, 420)

        lay = QVBoxLayout(self)
        lay.addWidget(hint(
            "Each row rebuilds an account backwards from the balance you "
            "entered. <b>Implied opening</b> is what the account must have held "
            "on its first imported day for the balance and the transactions to "
            "agree. A deposit account cannot open far below zero, so a large "
            "negative there means the balance, the as-of date, or the export "
            "itself needs a look — not that the history is being computed "
            "wrongly.<br><br><b>If it started at $0</b> is what the balance "
            "would be today had the account been empty at the top of the "
            "export. When that figure looks like the real balance and the one "
            "you entered doesn't, the entered balance is what to fix."))

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.COLS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table, 1)

        self.note = hint("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self._reload()

    def _reload(self) -> None:
        from ..history import reconcile_all
        rows = reconcile_all(self.store)
        self.table.setRowCount(len(rows))
        bad = []
        for r, i in enumerate(rows):
            # Cents matter here: this table is for spotting a discrepancy, and
            # rounding to whole dollars can hide a small one entirely.
            def _c(x, sign=False):
                s = "+" if sign and x > 0 else ("-" if x < 0 else "")
                return f"{s}${abs(x):,.2f}"
            cells = [i.account, str(i.n_rows), _c(i.inflow), _c(i.outflow),
                     _c(i.net_flow, True), _c(i.entered), _c(i.opening),
                     _c(i.if_opened_at_zero)]
            for c, v in enumerate(cells):
                it = QTableWidgetItem(v)
                if c >= 1:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
                if c == 6:
                    it.setForeground(theme.qcolor(
                        theme.POSITIVE if i.ok else theme.RED))
                self.table.setItem(r, c, it)
            if not i.ok:
                bad.append(i)
        if not rows:
            self.note.setText("No cash accounts with imported transactions yet.")
        elif not bad:
            self.note.setText(
                "Every account reconciles — the balances you entered and the "
                "transactions you imported agree with each other.")
        else:
            parts = []
            for i in bad:
                parts.append(
                    f"<b>{i.account}</b> does not reconcile. Its rows total "
                    f"{fmt_money(i.net_flow, signed=True)}, so with a balance "
                    f"of {fmt_money(i.entered)} it must have opened at "
                    f"{fmt_money(i.opening)}. If the account started empty, "
                    f"the balance today would be "
                    f"<b>{fmt_money(i.if_opened_at_zero)}</b> — check whether "
                    f"that is the figure you meant, or whether this export "
                    f"belongs to a different account.")
                if i.duplicates:
                    parts.append(
                        f"{i.duplicates} row{'s' if i.duplicates != 1 else ''} "
                        f"in it look like one transaction posted twice under "
                        f"different wording "
                        f"({fmt_money(i.duplicate_value, signed=True)}).")
            self.note.setText(" ".join(parts))


class AccountsPage(QWidget):
    COLS = ["Name", "Type", "Balance", "As of", "Rate", "Payment / contribution"]

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        bar = QHBoxLayout()
        add = QPushButton("Add account")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        edit = QPushButton("Edit")
        edit.clicked.connect(self._edit)
        rm = QPushButton("Delete")
        rm.setObjectName("danger")
        rm.clicked.connect(self._delete)
        snap = QPushButton("Snapshots…")
        snap.clicked.connect(self._snapshot)
        update = QPushButton("Update from CSV…")
        update.setToolTip("Import a newer CSV for the selected account — cash "
                          "accounts reuse their saved column mapping; loans "
                          "and investments open the balance/activity importer")
        update.clicked.connect(self._update_csv)
        check_bal = QPushButton("Check my balances…")
        check_bal.setToolTip("Does each account's imported history agree with "
                             "the balance you entered?")
        check_bal.clicked.connect(self._check_balances)
        for b in (add, edit, rm, snap, update, check_bal):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _: self._edit())
        lay.addWidget(self.table, 1)

        lay.addWidget(hint(
            "Loans store the amount owed as a positive balance; credit cards as "
            "a negative balance. The forecast amortizes loans monthly at APR/12 "
            "and grows investments at the expected return."))

    # ---------------------------------------------------------------
    def refresh(self) -> None:
        accounts = self.store.accounts()
        self.table.setRowCount(len(accounts))
        for r, a in enumerate(accounts):
            rate = "—"
            paycol = "—"
            if a.type == AccountType.LOAN:
                rate = f"{fmt_rate(a.apr)} APR"
                if a.monthly_payment:
                    paycol = f"{fmt_money(a.monthly_payment)}/mo · day {a.payment_day}"
            elif a.type == AccountType.INVESTMENT:
                rate = f"{a.annual_return * 100:.1f}% ± {a.volatility * 100:.0f}%"
                if a.monthly_contribution:
                    paycol = f"{fmt_money(a.monthly_contribution)}/mo"
            elif a.type == AccountType.CREDIT and a.apr:
                rate = f"{fmt_rate(a.apr)} APR"
            vals = [a.name, a.type.label, fmt_money(a.balance),
                    a.balance_date.isoformat(), rate, paycol]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, a.id)
                self.table.setItem(r, c, it)

    def _selected(self) -> Account | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        aid = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
        return self.store.account(aid)

    def _add(self) -> None:
        dlg = AccountDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.store.add_account(dlg.get_account())
            self.refresh()
            self.main.refresh_header()

    def _edit(self) -> None:
        a = self._selected()
        if not a:
            return
        dlg = AccountDialog(self, account=a)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.store.update_account(dlg.get_account())
            self.refresh()
            self.main.refresh_header()

    def _delete(self) -> None:
        a = self._selected()
        if not a:
            return
        ok = QMessageBox.question(
            self, "Delete account",
            f"Delete “{a.name}” and all of its transactions and snapshots?")
        if ok == QMessageBox.StandardButton.Yes:
            self.store.delete_account(a.id)
            self.refresh()
            self.main.refresh_header()

    def _update_csv(self) -> None:
        a = self._selected()
        if a is None:
            QMessageBox.information(self, "Update from CSV",
                                    "Select an account first.")
            return
        if not a.type.is_cashlike:
            # No saved mapping to reuse — a loan/investment import writes
            # balance points, not ledger rows — so send it to its own importer
            # rather than telling the user their account has never been
            # imported, which was the old (and wrong) answer here.
            self._import_history(a)
            return
        if self.store.get_import_mapping(a.id) is None:
            QMessageBox.information(
                self, "No saved setup yet",
                f"“{a.name}” hasn't been imported before, so there's no saved "
                f"column mapping to reuse. Do a first import via File → Import "
                f"CSV; after that, Update will remember the setup.")
            self.main.open_import()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Newer CSV for {a.name}", "",
            "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            added, skipped = reimport_with_saved_mapping(self.store, a.id, path)
        except LookupError:
            self.main.open_import()
            return
        except ValueError as e:
            QMessageBox.warning(self, "Import problem", str(e))
            return
        n_rec = refresh_derived(self.store)
        self.main.refresh_current()
        QMessageBox.information(
            self, "Update complete",
            f"Added {added} new transaction{'s' if added != 1 else ''} "
            f"({skipped} already-imported rows skipped). {n_rec} recurring "
            f"patterns are tracked.")

    def _check_balances(self) -> None:
        BalanceCheckDialog(self.store, self).exec()

    def _import_history(self, account: Account) -> None:
        dlg = SnapshotImportDialog(self.store, account, self)
        if dlg.exec():
            self.refresh()
            self.main.refresh_current()
            self.main.status(f"History imported for {account.name}.")

    def _snapshot(self) -> None:
        accounts = self.store.accounts()
        if not accounts:
            QMessageBox.information(self, "No accounts",
                                    "Add an account first.")
            return
        a = self._selected()
        if a is None:
            a = self._pick_account(accounts)
            if a is None:
                return
        dlg = SnapshotManagerDialog(self.store, a, self)
        dlg.exec()
        self.refresh()
        self.main.status(f"Snapshots updated for {a.name}.")
        self.main.refresh_current()

    def _pick_account(self, options: list[Account]) -> Account | None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Choose account")
        form = QFormLayout(dlg)
        combo = QComboBox()
        for a in options:
            combo.addItem(f"{a.name}  ({a.type.label})", a.id)
        form.addRow("Account", combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return self.store.account(combo.currentData())
        return None
