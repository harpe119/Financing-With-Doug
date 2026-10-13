"""CSV import dialog.

Auto-sniffs delimiter and column mapping, shows a raw preview, and lets the
user override everything. On import, the shared pipeline categorizes rows,
pairs internal transfers, and re-runs recurring detection.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                             QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from ..ingest import ImportConfig, SIGN_MODES, guess_mapping, read_raw, sniff_delimiter
from ..models import AccountType
from ..pipeline import import_with_config, refresh_derived
from ..store import Store
from .widgets import Card, form_row, hint

_DELIMS = [("Auto-detect", "auto"), ("Comma  ,", ","), ("Semicolon  ;", ";"),
           ("Tab", "\t"), ("Pipe  |", "|")]


class ImportDialog(QDialog):
    _NO_ACCOUNT = ("There is no checking, savings or credit-card account to "
                   "import into yet. Close this window, add one under "
                   "Accounts → Add account, then import again.")
    _CHOOSE_FILE = ("Choose the CSV file you downloaded from your bank. Doug "
                    "suggests which columns to use; you can check them before "
                    "anything is imported.")
    _CHECK_PREVIEW = ("Check that Date, Description and Amount point at the "
                      "right columns in the preview. Spending should be saved "
                      "as negative and income as positive; if your bank shows "
                      "it the other way round, change Amount signs.")

    def __init__(self, store: Store, parent=None, account_id: int | None = None):
        super().__init__(parent)
        self.store = store
        self.result_counts: tuple[int, int] | None = None
        self.setWindowTitle("Import bank CSV")
        self.setMinimumSize(720, 560)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # file + account
        top = QGridLayout()
        top.setHorizontalSpacing(10)
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("Choose a CSV export from your bank…")
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        self.account = QComboBox()
        for a in store.accounts():
            if a.type.is_cashlike:
                self.account.addItem(f"{a.name}  ({a.type.label})", a.id)
        if account_id is not None:
            i = self.account.findData(account_id)
            if i >= 0:
                self.account.setCurrentIndex(i)
        file_label = QLabel("File")
        file_label.setBuddy(self.path)
        top.addWidget(file_label, 0, 0)
        top.addWidget(self.path, 0, 1)
        top.addWidget(browse, 0, 2)
        acct_label = QLabel("Account")
        acct_label.setBuddy(self.account)
        top.addWidget(acct_label, 1, 0)
        top.addWidget(self.account, 1, 1, 1, 2)
        lay.addLayout(top)

        # Import stays disabled until a file is chosen; say what is expected
        # instead of leaving a grey button to explain itself.
        self.note = hint(self._NO_ACCOUNT if self.account.count() == 0
                         else self._CHOOSE_FILE)
        lay.addWidget(self.note)

        # options
        opts = QHBoxLayout()
        self.delim = QComboBox()
        for label, val in _DELIMS:
            self.delim.addItem(label, val)
        self.sign = QComboBox()
        self.sign.addItem("Auto-detect", "auto")
        for key, label in SIGN_MODES.items():
            self.sign.addItem(label, key)
        self.dayfirst = QCheckBox("Day-first dates (31/12/2025)")
        sep_label = QLabel("Column separator")
        sep_label.setBuddy(self.delim)
        opts.addWidget(sep_label)
        opts.addWidget(self.delim)
        opts.addSpacing(12)
        sign_label = QLabel("Amount signs")
        sign_label.setBuddy(self.sign)
        opts.addWidget(sign_label)
        opts.addWidget(self.sign, 1)
        opts.addWidget(self.dayfirst)
        lay.addLayout(opts)

        # column mapping
        map_card = Card("Column mapping")
        form = QFormLayout()
        form.setSpacing(7)
        self.c_date = QComboBox()
        self.c_desc = QComboBox()
        self.c_amount = QComboBox()
        self.c_indicator = QComboBox()
        self.c_debit = QComboBox()
        self.c_credit = QComboBox()
        form_row(form, "Date", self.c_date)
        form_row(form, "Description", self.c_desc)
        form_row(form, "Amount", self.c_amount)
        form_row(form, "Credit/debit indicator", self.c_indicator)
        form_row(form, "Debit / money out", self.c_debit)
        form_row(form, "Credit / money in", self.c_credit)
        map_card.body.addLayout(form)
        map_card.body.addWidget(hint(
            "Banks lay out amounts in one of three ways. Pick the columns that "
            "match yours: one Amount column (spending is negative), an Amount "
            "column plus a column marking each row as credit or debit, or "
            "separate Debit and Credit columns. Running-balance columns are "
            "ignored."))
        lay.addWidget(map_card)

        # preview
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setAlternatingRowColors(True)
        lay.addWidget(self.preview, 1)

        # actions
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.go = QPushButton("Import")
        self.go.setObjectName("primary")
        self.go.setDefault(True)
        self.go.clicked.connect(self._import)
        self.go.setEnabled(False)
        actions.addWidget(cancel)
        actions.addWidget(self.go)
        lay.addLayout(actions)

        self.delim.currentIndexChanged.connect(self._reload)
        self._columns: list[str] = []

    # ---------------------------------------------------------------
    def _browse(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Choose CSV", "",
                                           "CSV files (*.csv *.txt);;All files (*)")
        if p:
            self.path.setText(p)
            self._reload()

    def _delimiter(self) -> str:
        v = self.delim.currentData()
        return sniff_delimiter(self.path.text()) if v == "auto" else v

    def _reload(self) -> None:
        p = self.path.text()
        if not p:
            return
        try:
            df = read_raw(p, self._delimiter(), nrows=50)
        except Exception as e:
            QMessageBox.warning(
                self, "Couldn't read this file",
                "Doug couldn't open this file as a table. Check that it is a "
                "CSV exported from your bank, or try a different Column "
                f"separator.\n\nDetails: {e}")
            return
        self._columns = list(df.columns)
        g = guess_mapping(self._columns, df)
        df = df.head(8)
        for combo, key in ((self.c_date, "date"), (self.c_desc, "desc"),
                           (self.c_amount, "amount"),
                           (self.c_indicator, "indicator"),
                           (self.c_debit, "debit"), (self.c_credit, "credit")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("—", "")
            for c in self._columns:
                combo.addItem(c, c)
            i = combo.findData(g[key])
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)
        # raw preview
        self.preview.setColumnCount(len(df.columns))
        self.preview.setHorizontalHeaderLabels([str(c) for c in df.columns])
        self.preview.setRowCount(len(df))
        for r in range(len(df)):
            for c in range(len(df.columns)):
                self.preview.setItem(r, c, QTableWidgetItem(str(df.iat[r, c])))
        self.preview.resizeColumnsToContents()
        self.go.setEnabled(self.account.count() > 0)
        self.note.setText(self._CHECK_PREVIEW if self.account.count() > 0
                          else self._NO_ACCOUNT)

    def _import(self) -> None:
        if self.account.currentData() is None:
            QMessageBox.information(
                self, "No account to import into",
                "Add a checking, savings or credit-card account first "
                "(Accounts → Add account), then import again.")
            return
        sign = self.sign.currentData()
        if sign == "auto":
            if self.c_indicator.currentData() and self.c_amount.currentData():
                sign = "indicator"
            elif (self.c_debit.currentData() or self.c_credit.currentData()) \
                    and not self.c_amount.currentData():
                sign = "debit_credit"
            else:
                sign = "standard"
        cfg = ImportConfig(
            path=self.path.text(),
            account_id=self.account.currentData(),
            delimiter=self._delimiter(),
            dayfirst=self.dayfirst.isChecked(),
            sign_mode=sign,
            col_date=self.c_date.currentData() or "",
            col_desc=self.c_desc.currentData() or "",
            col_amount=self.c_amount.currentData() or "",
            col_debit=self.c_debit.currentData() or "",
            col_credit=self.c_credit.currentData() or "",
            col_indicator=self.c_indicator.currentData() or "",
        )
        try:
            # One press of Ctrl-Z has to take back the whole import. Without
            # the group this is four separate steps — rows, saved mapping,
            # re-categorisation, patterns and checkpoint — and undoing "an
            # import" would mean pressing it until the numbers looked right.
            with self.store.undo_group("Import CSV"):
                added, skipped = import_with_config(self.store, cfg)
                n_rec = refresh_derived(self.store)
        except ValueError as e:
            QMessageBox.warning(
                self, "Nothing was imported",
                f"{e}\n\nYour data is unchanged. Check the column choices "
                "above and try again.")
            return
        self.result_counts = (added, skipped)
        QMessageBox.information(
            self, "Import complete",
            f"Added {added} transaction{'s' if added != 1 else ''}; "
            f"{skipped} already imported {'was' if skipped == 1 else 'were'} "
            f"skipped.\n"
            f"Doug found {n_rec} recurring bill or income "
            f"pattern{'s' if n_rec != 1 else ''}. Review "
            f"{'it' if n_rec == 1 else 'them'} under Bills & payday → "
            f"Recurring patterns.")
        self.accept()
