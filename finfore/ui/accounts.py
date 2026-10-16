"""Accounts page — the balance-sheet side of the model."""
from __future__ import annotations

from datetime import date

import pandas as pd
from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog,
                             QDialogButtonBox, QFileDialog, QFormLayout,
                             QFrame, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMenu, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..account_view import (AccountStatus, account_statuses, ago_text,
                            held_amount, money_exact, owed_amount,
                            split_accounts, summary_line, terms_text)
from ..models import Account, AccountType
from ..ingest import (ImportConfig, SIGN_MODES, balances_from_activity,
                      guess_mapping, guess_snapshot_mapping, parse_snapshots,
                      parse_transactions, read_raw, sniff_delimiter)
from ..pipeline import reimport_with_saved_mapping, refresh_derived
from ..store import Store
from ..util import fmt_money
from . import theme
from .account_dialog import AccountDialog, _money_spin
from .widgets import form_row, hint


def confirm(parent, title: str, text: str, yes_text: str,
            no_text: str = "Keep it", destructive: bool = True) -> bool:
    """Ask before something that cannot be undone casually.

    The buttons say what they do ("Delete account" / "Keep it") instead of
    Yes / No, and the *safe* answer is the default, so pressing Enter or Esc
    keeps the data. The old Yes/No boxes defaulted to Yes.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(yes_text, QMessageBox.ButtonRole.AcceptRole)
    if destructive:
        # QMessageBox styles its buttons as they are added, so a name set
        # afterwards only takes effect once the button is polished again.
        yes.setObjectName("danger")
        yes.style().unpolish(yes)
        yes.style().polish(yes)
    no = box.addButton(no_text, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


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
        mode_label = QLabel("This file lists")
        mrow.addWidget(mode_label)
        self.mode = QComboBox()
        mode_label.setBuddy(self.mode)
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
        form_row(bform, "Date column", self.c_date)
        form_row(bform, "Balance column", self.c_balance)
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
        form_row(aform, "Date column", self.a_date)
        form_row(aform, "Description column", self.a_desc)
        form_row(aform, "Amount column", self.a_amount)
        form_row(aform, "Credit/debit indicator", self.a_indicator)
        form_row(aform, "Debit / money out", self.a_debit)
        form_row(aform, "Credit / money in", self.a_credit)
        form_row(aform, "Amount signs", self.a_sign)
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
        self.ok.setDefault(True)
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
        self.setWindowTitle(f"Balance history — {account.name}")
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
            "No snapshots yet. Add one whenever you know the exact balance on "
            "a past date." if cashlike else
            "No snapshots yet. Add them one at a time, or use Import CSV… to "
            "load a balance-over-time export from your lender or brokerage.")
        lay.addWidget(self.empty_note)

        # Only for loans and investments: a cash account's pre-history is
        # governed by its ledger and its anchor, not by snapshots.
        self.pre_row = None
        if not cashlike:
            self.pre_row = QWidget()
            prl = QHBoxLayout(self.pre_row)
            prl.setContentsMargins(0, 0, 0, 0)
            prl.addWidget(QLabel("Before the first snapshot, assume"))
            self.prehistory = QComboBox()
            self.prehistory.addItem("the balance was $0", "zero")
            self.prehistory.addItem("the first known balance, held flat", "flat")
            self.prehistory.setToolTip(
                "A loan taken out in 2025 was not a debt in 2023, so the "
                "default is that there was nothing there before the records "
                "start. Change it for an account that arrived already carrying "
                "a balance — a mortgage moved from another lender, or a "
                "brokerage account transferred in with its holdings.")
            self.prehistory.currentIndexChanged.connect(self._save_prehistory)
            prl.addWidget(self.prehistory, 1)
            lay.addWidget(self.pre_row)

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
        # Enter closes the window. It used to press "Add…", so Enter on a
        # selected snapshot opened an empty entry form instead.
        for b in (add, edit, rm, imp):
            b.setAutoDefault(False)
        close.setDefault(True)
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
        if self.pre_row is not None:
            # With no snapshots there is no "before the first one" to describe,
            # and the balance is held flat regardless — offering the choice
            # there would be offering a control that does nothing.
            self.pre_row.setVisible(not df.empty)
            mode = self.store.prehistory_mode(self.account.id)
            self.prehistory.blockSignals(True)
            self.prehistory.setCurrentIndex(
                max(0, self.prehistory.findData(mode)))
            self.prehistory.blockSignals(False)

    def _save_prehistory(self) -> None:
        self.store.set_prehistory_mode(self.account.id,
                                       self.prehistory.currentData())

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
        if confirm(self, "Delete snapshot",
                   "Delete this snapshot?\n\nUndo (Ctrl+Z) brings it back.",
                   yes_text="Delete snapshot", no_text="Keep it"):
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

    COLS = ["Account", "Verdict", "Rows", "Money in", "Money out", "Net",
            "Balance entered", "Implied opening", "If it started at $0"]

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
        self.removed_note = hint("")
        lay.addWidget(self.removed_note)

        bb = QDialogButtonBox()
        self.dup_btn = bb.addButton("Find duplicate rows…",
                                    QDialogButtonBox.ButtonRole.ActionRole)
        self.dup_btn.setToolTip(
            "Look for one transaction that the bank exported twice")
        self.dup_btn.clicked.connect(self._find_duplicates)
        self.undo_btn = bb.addButton("Restore removed rows",
                                     QDialogButtonBox.ButtonRole.ResetRole)
        self.undo_btn.setToolTip(
            "Forget every duplicate removal, so the next import brings them back")
        self.undo_btn.clicked.connect(self._unsuppress)
        # Dismissing the dashboard warning has to be reversible from somewhere,
        # or "Got it" is a one-way door on the only thing telling you the
        # history is wrong.
        self.unack_btn = bb.addButton("Show dashboard warning again",
                                      QDialogButtonBox.ButtonRole.ResetRole)
        self.unack_btn.setToolTip(
            "Undo a “Got it” — bring the dashboard warning back for every "
            "account it was dismissed on")
        self.unack_btn.clicked.connect(self._unacknowledge)
        close = bb.addButton(QDialogButtonBox.StandardButton.Close)
        # Enter closes. It used to press "Restore removed rows", which is not
        # what anyone reaching for Enter in a read-only table means.
        for b in (self.dup_btn, self.undo_btn, self.unack_btn):
            b.setAutoDefault(False)
        close.setDefault(True)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._reload()

    def _find_duplicates(self) -> None:
        DuplicateDialog(self.store, self).exec()
        self._reload()

    def _unacknowledge(self) -> None:
        n = len(self.store.ledger_acknowledgements())
        if not n:
            QMessageBox.information(
                self, "Nothing dismissed",
                "The dashboard warning hasn't been dismissed for any account.")
            return
        self.store.set_ledger_acknowledgements({})
        self._reload()
        QMessageBox.information(
            self, "Warning restored",
            f"The dashboard will warn about {n} account"
            f"{'s' if n != 1 else ''} again.")

    def _unsuppress(self) -> None:
        n = len(self.store.suppressed_hashes())
        if not n:
            QMessageBox.information(self, "Nothing to restore",
                                    "No rows have been removed as duplicates.")
            return
        if confirm(
                self, "Restore removed rows",
                f"Forget {n} duplicate removal{'s' if n != 1 else ''}?\n\n"
                f"The rows themselves come back the next time you import the "
                f"CSV they came from.",
                yes_text="Forget removals", no_text="Keep them",
                destructive=False):
            self.store.unsuppress_all()
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
            # The verdict is words, and amber when it needs a look. A colour on
            # the figure alone was the only cue, and the selected-row style
            # replaced it; green also vouched for a number that was only
            # checked in one direction.
            verdict = "No red flags" if i.ok else "Needs a look"
            cells = [i.account, verdict, str(i.n_rows), _c(i.inflow),
                     _c(i.outflow), _c(i.net_flow, True), _c(i.entered),
                     _c(i.opening), _c(i.if_opened_at_zero)]
            for c, v in enumerate(cells):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter)
                if c in (1, 7) and not i.ok:
                    it.setForeground(theme.qcolor(theme.AMBER))
                self.table.setItem(r, c, it)
            if not i.ok:
                bad.append(i)
        n_sup = len(self.store.suppressed_hashes())
        n_ack = len(self.store.ledger_acknowledgements())
        notes = []
        if n_sup:
            notes.append(f"{n_sup} row{'s' if n_sup != 1 else ''} previously "
                         f"removed as duplicates and held out of future "
                         f"imports.")
        if n_ack:
            notes.append(f"The dashboard warning is dismissed for {n_ack} "
                         f"account{'s' if n_ack != 1 else ''}; this table "
                         f"always shows every one regardless.")
        self.removed_note.setText(" ".join(notes))
        if not rows:
            self.note.setText("No cash accounts with imported transactions yet.")
        elif not bad:
            self.note.setText(
                "No account would have had to start below zero to fit its "
                "transactions, so none is flagged. This check cannot tell "
                "whether a balance is recent or an export is complete: the "
                "Status column on the Accounts page covers that.")
        else:
            parts = []
            for i in bad:
                parts.append(
                    f"<b>{i.account}</b> needs a look. Its rows total "
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


class DuplicateDialog(QDialog):
    """Review and remove rows that look like one transaction posted twice.

    Nothing is removed without being looked at. Similarity finds candidates;
    the opening-balance figure at the bottom is what decides between them, and
    it updates as boxes are ticked so the effect of a choice is visible before
    it is made rather than after.
    """

    COLS = ["Remove", "Account", "Date", "Amount", "Keeping", "Removing", "Match"]

    def __init__(self, store: Store, parent=None, account_id=None):
        super().__init__(parent)
        self.store = store
        self.account_id = account_id
        self.setWindowTitle("Possible duplicate rows")
        self.setMinimumSize(920, 520)
        self._rows: list = []          # (checkbox, pair, report)

        lay = QVBoxLayout(self)
        lay.addWidget(hint(
            "Banks sometimes export one transaction twice — commonly around a "
            "system change, where the same event appears in both an old and a "
            "new format. The import can't collapse those on its own, because "
            "the wording or posting date differs meaningfully and two genuine identical purchases on one day "
            "must be kept.<br><br>Ticked rows are the ones whose removal moves "
            "an impossible opening balance back toward zero. Everything else is "
            "listed but left alone — two identical transfers on the same day "
            "are usually two real transfers. Removed rows are remembered, so a "
            "later <b>Update from CSV</b> will not bring them back."))

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        h = self.table.horizontalHeader()
        for c, mode in enumerate([QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.ResizeToContents]):
            h.setSectionResizeMode(c, mode)
        lay.addWidget(self.table, 1)

        self.effect = QLabel("")
        self.effect.setWordWrap(True)
        lay.addWidget(self.effect)

        bb = QDialogButtonBox()
        self.go = bb.addButton("Remove ticked rows",
                               QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setObjectName("danger")
        close = bb.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        # The destructive button must never be the one Enter presses.
        self.go.setAutoDefault(False)
        close.setDefault(True)
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._reload()

    def _reload(self) -> None:
        from ..dedup import suggest
        self._reports = suggest(self.store, self.account_id)
        self._rows = []
        total = sum(len(r.pairs) for r in self._reports)
        self.table.setRowCount(total)
        r = 0
        for rep in self._reports:
            for p in rep.pairs:
                box = QCheckBox()
                box.setAccessibleName(f"Remove “{p.drop_desc}” on {p.when.isoformat()}")
                box.setChecked(p.drop_id in rep.suggested)
                box.stateChanged.connect(lambda _s: self._sync_effect())
                self.table.setCellWidget(r, 0, box)
                cells = [rep.account, p.when.isoformat(),
                         f"{'+' if p.amount > 0 else '-'}${abs(p.amount):,.2f}",
                         p.keep_desc, p.drop_desc, f"{p.score:.2f}"]
                for c, v in enumerate(cells, start=1):
                    it = QTableWidgetItem(v)
                    if c == 3:
                        it.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(r, c, it)
                self._rows.append((box, p, rep))
                r += 1
        self.go.setEnabled(total > 0)
        self._sync_effect()

    def _sync_effect(self) -> None:
        if not self._rows:
            self.effect.setText("No candidate duplicates found.")
            return
        parts = []
        for rep in self._reports:
            picked = [p.drop_id for b, p, rp in self._rows
                      if rp is rep and b.isChecked()]
            after = rep.opening_after(picked)
            colour = (theme.POSITIVE if after >= -25 else theme.AMBER)
            parts.append(
                f"<div><b>{rep.account}</b>: opening balance "
                f"{money_exact(rep.opening)} → <span style='color:{colour}'>"
                f"{money_exact(after)}</span> if the ticked rows go "
                f"({len(picked)} of {len(rep.pairs)}).</div>")
        parts.append(
            f"<div style='color:{theme.MUTED};margin-top:4px'>An opening near "
            f"zero means the ledger and the balance finally agree. If it is "
            f"still a long way below, some rows are missing from the export or "
            f"the balance itself needs correcting — removing more rows will "
            f"not fix that, it will only hide it.</div>")
        self.effect.setText("".join(parts))

    def _apply(self) -> None:
        drop = [p.drop_id for b, p, _ in self._rows if b.isChecked()]
        if not drop:
            QMessageBox.information(self, "Nothing ticked",
                                    "Tick the rows you want removed first.")
            return
        if not confirm(
                self, "Remove rows",
                f"Remove {len(drop)} transaction"
                f"{'s' if len(drop) != 1 else ''}?\n\n"
                f"They will also be remembered as removed, so importing the "
                f"same CSV again will not bring them back. Undo (Ctrl+Z) "
                f"restores them, and Accounts → Check my balances can forget "
                f"the removals later.",
                yes_text="Remove rows", no_text="Keep them"):
            return
        from ..dedup import apply as apply_dedup
        from ..pipeline import refresh_derived
        with self.store.undo_group("Remove duplicate rows"):
            n = apply_dedup(self.store, drop)
            refresh_derived(self.store, checkpoint=False)
        QMessageBox.information(
            self, "Removed",
            f"Removed {n} row{'s' if n != 1 else ''}.")
        self.accept()


class UpdateBalanceDialog(QDialog):
    """The one-step way to say what an account holds today.

    Editing an account to change one number means opening a form of eight
    fields. This asks for the two things that actually change — the balance and
    the day it was true — and re-anchors the account's history to them.
    """

    def __init__(self, account: Account, parent=None):
        super().__init__(parent)
        self.account = account
        self.setWindowTitle(f"Update balance · {account.name}")
        self.setMinimumSize(460, 260)
        self.resize(480, 280)
        owed = account.type in (AccountType.CREDIT, AccountType.LOAN)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(16)
        form = QFormLayout()
        form.setSpacing(12)
        lay.addLayout(form)
        self.balance = _money_spin(0 if owed else -1e9)
        shown = -account.balance if account.type == AccountType.CREDIT else account.balance
        self.balance.setValue(shown)
        label = QLabel("Amount owed" if owed else "Balance")
        label.setBuddy(self.balance)
        form.addRow(label, self.balance)
        self.when = QDateEdit(QDate.currentDate())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("MMM d, yyyy")
        asof = QLabel("As of")
        asof.setBuddy(self.when)
        form.addRow(asof, self.when)
        lay.addWidget(hint(
            "Use the figure on your latest statement or banking app, and the "
            "day it was true. Doug re-anchors this account’s history to it. "
            "Type digits as cents: 1234 becomes $12.34."))
        lay.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Save balance")
        ok.setObjectName("primary")
        ok.setDefault(True)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _accept(self) -> None:
        self.balance.interpretText()
        self.accept()

    def get_values(self) -> tuple[float, date]:
        value = self.balance.value()
        stored = -value if self.account.type == AccountType.CREDIT else value
        d = self.when.date()
        return stored, date(d.year(), d.month(), d.day())


class AccountTable(QTableWidget):
    """The accounts list, with the keys a list should answer to.

    Enter opens the selected account for editing and Delete starts the delete
    flow (which still asks first); neither did anything before.
    """

    editRequested = pyqtSignal()
    deleteRequested = pyqtSignal()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.editRequested.emit()
            ev.accept()
            return
        if ev.key() == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
            ev.accept()
            return
        super().keyPressEvent(ev)


_FIX_TIPS = {
    "check": ("The imported transactions and the balance you entered can’t "
              "both be right. Open Check my balances to see which to fix."),
    "import": ("Import a newer statement so Doug can learn from recent "
               "activity."),
    "balance": ("Enter today’s balance so the history is anchored to a recent "
                "figure."),
}


class AccountsPage(QWidget):
    COLS = ["Name", "Type", "Balance", "Updated", "Status", "Going forward"]
    _RIGHT = 2

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._statuses: dict[int, AccountStatus] = {}
        self._simple = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        # --- what this page is, and whether the numbers on it can be trusted ---
        head = QHBoxLayout()
        head.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = QLabel("Your accounts")
        self.title.setObjectName("pageTitle")
        titles.addWidget(self.title)
        self.summary = hint("")
        titles.addWidget(self.summary)
        head.addLayout(titles, 1)
        self.check_btn = QPushButton("Check my balances…")
        self.check_btn.setObjectName("link")
        self.check_btn.setToolTip("Does each account's imported history agree "
                                  "with the balance you entered?")
        self.check_btn.clicked.connect(self._check_balances)
        head.addWidget(self.check_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.add_btn = QPushButton("Add account")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add)
        head.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # --- the selected account's actions --------------------------------
        # Edit, Update, Snapshots and Delete only mean something once a row is
        # chosen, so they live in a bar that exists only then. Delete moved out
        # of reach of Edit, into the menu.
        self.sel_bar = QFrame()
        self.sel_bar.setObjectName("selBar")
        sb = QHBoxLayout(self.sel_bar)
        sb.setContentsMargins(14, 10, 14, 10)
        sb.setSpacing(8)
        info = QVBoxLayout()
        info.setSpacing(1)
        self.sel_name = QLabel("")
        self.sel_name.setObjectName("stepTitle")
        info.addWidget(self.sel_name)
        self.sel_status = hint("")
        info.addWidget(self.sel_status)
        sb.addLayout(info, 1)
        self.fix_btn = QPushButton("Check this account")
        self.fix_btn.clicked.connect(self._check_balances)
        self.balance_btn = QPushButton("Update balance")
        self.balance_btn.setToolTip("Enter what the account holds today")
        self.balance_btn.clicked.connect(self._update_balance)
        self.csv_btn = QPushButton("Update from CSV…")
        self.csv_btn.clicked.connect(self._update_csv)
        self.more_btn = QPushButton("More")
        menu = QMenu(self.more_btn)
        self.act_edit = menu.addAction("Edit details…\tEnter")
        self.act_edit.triggered.connect(self._edit)
        self.act_snap = menu.addAction("Balance history…")
        self.act_snap.setToolTip("Dated balances that correct or extend the "
                                 "account's history")
        self.act_snap.triggered.connect(self._snapshot)
        menu.addSeparator()
        self.act_delete = menu.addAction("Delete account…\tDel")
        self.act_delete.triggered.connect(self._delete)
        self.more_btn.setMenu(menu)
        for b in (self.fix_btn, self.balance_btn, self.csv_btn, self.more_btn):
            sb.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.sel_bar.setVisible(False)
        lay.addWidget(self.sel_bar)

        # --- the list ------------------------------------------------------
        self.table = AccountTable(0, len(self.COLS))
        self.table.setAccessibleName("Accounts")
        self.table.setAccessibleDescription(
            "Your accounts, grouped into what you have and what you owe, each "
            "with its balance and whether it can be trusted.")
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        vh = self.table.verticalHeader()
        vh.setVisible(False)
        vh.setMinimumSectionSize(34)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr = self.table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                                | Qt.AlignmentFlag.AlignVCenter)
        hdr.setMinimumSectionSize(70)
        hdr.setStretchLastSection(False)
        # Name and the two sentences take the spare width; the figures size to
        # what they hold, so nothing is clipped and no column is wasted.
        for c, mode in enumerate((QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.Stretch)):
            hdr.setSectionResizeMode(c, mode)
        for c in range(len(self.COLS)):
            h = self.table.horizontalHeaderItem(c)
            h.setTextAlignment((Qt.AlignmentFlag.AlignRight if c == self._RIGHT
                                else Qt.AlignmentFlag.AlignLeft)
                               | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(lambda _: self._edit())
        self.table.editRequested.connect(self._edit)
        self.table.deleteRequested.connect(self._delete)
        lay.addWidget(self.table, 1)

        self.footer = hint(
            "Money you owe is shown as a positive amount, under What you owe. "
            "A card in credit counts as money you have.")
        lay.addWidget(self.footer)

        # --- first run -----------------------------------------------------
        self.empty = self._build_empty()
        lay.addWidget(self.empty, 1)

    # ---------------------------------------------------------------
    def _build_empty(self) -> QWidget:
        """What a person with no accounts sees: what to do, in the same voice
        as the dashboard's welcome."""
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 8, 0, 8)
        outer.addStretch(1)
        mid = QHBoxLayout()
        mid.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(600)
        card.setMinimumWidth(400)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 24, 28, 22)
        cl.setSpacing(12)
        t = QLabel("Add the accounts you want Doug to keep track of")
        t.setObjectName("welcomeTitle")
        t.setWordWrap(True)
        cl.addWidget(t)
        for head, body in (
                ("Money you have", "checking, savings and investments"),
                ("Money you owe", "credit cards and loans")):
            row = QVBoxLayout()
            row.setSpacing(1)
            h = QLabel(head)
            h.setObjectName("stepTitle")
            row.addWidget(h)
            row.addWidget(hint(body[0].upper() + body[1:]))
            cl.addLayout(row)
        cl.addWidget(hint(
            "For each one you’ll enter today’s balance and the day it’s from. "
            "Importing its statements comes next, and that is what lets Doug "
            "see your habits."))
        actions = QHBoxLayout()
        go = QPushButton("Add your first account")
        go.setObjectName("primary")
        go.clicked.connect(self._add)
        actions.addWidget(go)
        demo = QPushButton("Explore with a demo first")
        demo.setObjectName("link")
        demo.setToolTip("Opens fictional data in a separate file; your own "
                        "data is untouched")
        demo.clicked.connect(
            lambda: self.main._load_demo("balanced", confirm=False))
        actions.addWidget(demo)
        actions.addStretch(1)
        cl.addLayout(actions)
        self.empty_btn = go
        mid.addWidget(card, 10)
        mid.addStretch(1)
        outer.addLayout(mid)
        outer.addStretch(2)
        return wrap

    # ---------------------------------------------------------------
    def set_simple(self, on: bool) -> None:
        """Simple view hides the maintenance tool; everything else stays."""
        self._simple = bool(on)
        self.act_snap.setVisible(not on)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if self.table.isVisible():
            self.table.resizeRowsToContents()

    # ---------------------------------------------------------------
    @staticmethod
    def _cell(text: str = "", right: bool = False, font=None, color: str | None = None,
              enabled: bool = False, tip: str | None = None) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        align = Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft
        it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        if font is not None:
            it.setFont(font)
        if color:
            it.setForeground(theme.qcolor(color))
        if tip:
            it.setToolTip(tip)
        if not enabled:
            it.setFlags(Qt.ItemFlag.NoItemFlags)
        return it

    def refresh(self) -> None:
        accounts = self.store.accounts()
        keep = self._selected_id()
        self._statuses = account_statuses(self.store)
        self.summary.setText(summary_line(accounts, self._statuses))
        has = bool(accounts)
        self.empty.setVisible(not has)
        for w in (self.table, self.footer, self.check_btn, self.add_btn):
            w.setVisible(has)
        if not has:
            self.sel_bar.setVisible(False)
            self.table.setRowCount(0)
            return

        have, owe = split_accounts(accounts)
        have_total = sum(held_amount(a) for a in have)
        owe_total = sum(owed_amount(a) for a in owe)
        rows: list[tuple] = []
        if have:
            rows.append(("group", "What you have"))
            rows += [("acct", a, False) for a in have]
            rows.append(("total", "Total you have", have_total, theme.TEXT))
        if owe:
            rows.append(("group", "What you owe"))
            rows += [("acct", a, True) for a in owe]
            rows.append(("total", "Total you owe", owe_total, theme.RED))
        if have and owe:
            net = have_total - owe_total
            rows.append(("total", "Net worth", net,
                         theme.POSITIVE if net >= 0 else theme.NEGATIVE))

        self.table.clearSpans()
        self.table.setRowCount(len(rows))
        label_font = QFont()
        label_font.setPixelSize(11)
        label_font.setBold(True)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        mono = theme.mono_font(13, True)
        select_row = -1
        for r, row in enumerate(rows):
            kind = row[0]
            if kind == "group":
                self.table.setItem(r, 0, self._cell(row[1].upper(), font=label_font,
                                                    color=theme.MUTED))
                self.table.setSpan(r, 0, 1, len(self.COLS))
                continue
            if kind == "total":
                _k, text, amount, color = row
                bold = theme.mono_font(13, True)
                lab = self._cell(text, font=self._bold(), color=theme.MUTED)
                self.table.setItem(r, 0, lab)
                self.table.setSpan(r, 0, 1, 2)
                self.table.setItem(r, self._RIGHT, self._cell(
                    money_exact(amount), right=True, font=bold, color=color))
                continue
            _k, a, is_owed = row
            st = self._statuses.get(a.id) or AccountStatus("")
            shown = owed_amount(a) if is_owed else held_amount(a)
            amount_color = (theme.RED if is_owed or shown < 0 else None)
            status_color = theme.AMBER if st.level == "caution" else theme.MUTED
            tip = _FIX_TIPS.get(st.fix or "", "")
            vals = [
                self._cell(a.name, enabled=True, tip=a.name),
                self._cell(a.type.label, enabled=True),
                self._cell(money_exact(shown), right=True, font=mono,
                           color=amount_color, enabled=True),
                self._cell(ago_text(st.age_days), enabled=True,
                           tip=(st.anchor.isoformat() if st.anchor else None)),
                self._cell(st.text, color=status_color, enabled=True,
                           tip=(f"{st.text}. {tip}" if tip else st.text)),
                self._cell(terms_text(a), enabled=True, tip=terms_text(a)),
            ]
            vals[0].setData(Qt.ItemDataRole.UserRole, a.id)
            for c, it in enumerate(vals):
                self.table.setItem(r, c, it)
            if a.id == keep:
                select_row = r
        self.table.resizeRowsToContents()
        if select_row >= 0:
            self.table.selectRow(select_row)
        self._on_selection()

    @staticmethod
    def _bold():
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        return f

    # ---------------------------------------------------------------
    def _selected_id(self) -> int | None:
        """The account that is *visibly* selected, and nothing else.

        This used to read the table's current row. The current row and the
        selection are different things: after clicking away, or tabbing into the
        table, a row can be current without being highlighted, so Delete could
        prompt about an account that no longer looked chosen.
        """
        sel = self.table.selectionModel()
        if sel is None:
            return None
        rows = sel.selectedRows()
        if not rows:
            return None
        it = self.table.item(rows[0].row(), 0)
        if it is None:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def _selected(self) -> Account | None:
        aid = self._selected_id()
        return self.store.account(aid) if aid is not None else None

    def _select_id(self, account_id: int) -> None:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == account_id:
                self.table.selectRow(r)
                self.table.scrollToItem(it)
                return

    def _set_primary(self, btn: QPushButton, on: bool) -> None:
        btn.setObjectName("primary" if on else "")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _on_selection(self) -> None:
        a = self._selected()
        self.sel_bar.setVisible(a is not None)
        if a is None:
            return
        st = self._statuses.get(a.id) or AccountStatus("")
        self.sel_name.setText(a.name)
        self.sel_status.setText(st.text)
        self.sel_status.setStyleSheet(
            f"color: {theme.AMBER};" if st.level == "caution" else "")
        self.fix_btn.setVisible(st.fix == "check")
        self._set_primary(self.fix_btn, st.fix == "check")
        self._set_primary(self.balance_btn, st.fix == "balance")
        self._set_primary(self.csv_btn, st.fix == "import")
        self.csv_btn.setText("Update from CSV…" if a.type.is_cashlike
                             else "Import balance history…")
        self.csv_btn.setToolTip(
            "Import a newer CSV for this account. Cash accounts reuse their "
            "saved column mapping; loans and investments open the "
            "balance-history importer.")
        self.act_snap.setVisible(not self._simple)

    # ---------------------------------------------------------------
    def _add(self) -> None:
        dlg = AccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        acct = dlg.get_account()
        new_id = self.store.add_account(acct)
        self.refresh()
        self._select_id(new_id)
        self.main.refresh_header()
        nxt = (" Next: import its statement." if acct.type.is_cashlike else "")
        self.main.status(f"Added {acct.name}.{nxt}")

    def _edit(self) -> None:
        a = self._selected()
        if not a:
            return
        dlg = AccountDialog(self, account=a)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.store.update_account(dlg.get_account())
            self.refresh()
            self.main.refresh_header()

    def _update_balance(self) -> None:
        a = self._selected()
        if not a:
            return
        dlg = UpdateBalanceDialog(a, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        balance, when = dlg.get_values()
        a.balance, a.balance_date = balance, when
        self.store.update_account(a)
        self.refresh()
        self.main.refresh_current()
        self.main.status(f"Balance updated for {a.name}.")

    def _delete(self) -> None:
        a = self._selected()
        if not a:
            return
        n_tx = len(self.store.transactions(a.id))
        n_snap = len(self.store.snapshots(a.id))
        parts = []
        if n_tx:
            parts.append(f"{n_tx} imported transaction{'s' if n_tx != 1 else ''}")
        if n_snap:
            parts.append(f"{n_snap} balance snapshot{'s' if n_snap != 1 else ''}")
        removes = ("the account" + (", its " + " and ".join(parts) if parts else ""))
        if confirm(self, "Delete account",
                   f"Delete “{a.name}”?\n\nThis removes {removes}. You can bring "
                   f"it all back with Undo (Ctrl+Z).",
                   yes_text="Delete account", no_text="Keep it"):
            self.store.delete_account(a.id)
            self.refresh()
            self.main.refresh_header()

    def _update_csv(self) -> None:
        a = self._selected()
        if a is None:
            return
        if not a.type.is_cashlike:
            # No saved mapping to reuse — a loan/investment import writes
            # balance points, not ledger rows — so send it to its own importer
            # rather than telling the user their account has never been
            # imported, which was the old (and wrong) answer here.
            self._import_history(a)
            return
        if self.store.get_import_mapping(a.id) is None:
            # Never imported: go straight to the first-import dialog for this
            # account instead of a message that sends the person somewhere else.
            self.main.import_for_account(a)
            self.refresh()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Newer CSV for {a.name}", "",
            "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            with self.store.undo_group(f"Update {a.name} from CSV"):
                added, skipped = reimport_with_saved_mapping(self.store, a.id, path)
                n_rec = refresh_derived(self.store)
        except LookupError:
            self.main.import_for_account(a)
            self.refresh()
            return
        except ValueError as e:
            QMessageBox.warning(self, "Nothing was imported",
                                f"{e}\n\nYour data is unchanged.")
            return
        self.main.refresh_current()
        QMessageBox.information(
            self, "Update complete",
            f"Added {added} new transaction{'s' if added != 1 else ''} "
            f"({skipped} already-imported rows skipped). {n_rec} recurring "
            f"pattern{'s' if n_rec != 1 else ''} tracked.")

    def _check_balances(self) -> None:
        BalanceCheckDialog(self.store, self).exec()
        self.refresh()

    def _import_history(self, account: Account) -> None:
        dlg = SnapshotImportDialog(self.store, account, self)
        if dlg.exec():
            self.refresh()
            self.main.refresh_current()
            self.main.status(f"History imported for {account.name}.")

    def _snapshot(self) -> None:
        a = self._selected()
        if a is None:
            return
        dlg = SnapshotManagerDialog(self.store, a, self)
        dlg.exec()
        self.refresh()
        self.main.status(f"Snapshots updated for {a.name}.")
        self.main.refresh_current()
