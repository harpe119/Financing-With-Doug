"""CSV import dialog.

Auto-sniffs delimiter and column mapping, shows a raw preview, and lets the
user override everything. On import, the shared pipeline categorizes rows,
pairs internal transfers, and re-runs recurring detection.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                             QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QDialogButtonBox, QHeaderView, QScrollArea, QWidget)
from PyQt6.QtCore import Qt

from ..ingest import ImportConfig, SIGN_MODES, guess_mapping, read_raw, sniff_delimiter
from ..models import AccountType
from ..pipeline import import_with_config, import_summary, refresh_derived
from ..import_matching import PostingDateReviewRequired
from ..store import Store
from .widgets import Card, CollapsibleCard, form_row, hint
from ..util import fmt_money


def confirm_posting_dates(parent, changes):
    dlg = QDialog(parent)
    dlg.setWindowTitle('Review older posting dates')
    dlg.resize(860, 460)
    lay = QVBoxLayout(dlg)
    count = len(changes)
    entry = 'entry used a purchase date' if count == 1 else 'entries used purchase dates'
    lay.addWidget(hint(f'{count} older {entry}. The CSV supplies these bank posting dates. '
                       'Updating them changes balance history while keeping categories, review history and goal links. '
                       'Cancel leaves the import unchanged. Undo reverses the complete import.'))
    table = QTableWidget(len(changes), 5)
    table.setHorizontalHeaderLabels(['Description', 'Amount', 'Old date', 'Posting date', 'Reference'])
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setAccessibleName('Proposed posting date corrections')
    for i, change in enumerate(changes):
        for j, value in enumerate([change['description'], fmt_money(change['amount'], signed=True),
                                   change['old_date'], change['date'], change['source_reference']]):
            table.setItem(i,j,QTableWidgetItem(str(value)))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
    lay.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Update dates and import')
    buttons.button(QDialogButtonBox.StandardButton.Ok).setAutoDefault(False)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    return dlg.exec() == QDialog.DialogCode.Accepted


class ImportDetailsDialog(QDialog):
    def __init__(self, store, parent=None, account_id=None):
        super().__init__(parent)
        self.setWindowTitle('Latest CSV import')
        self.resize(820,480)
        lay = QVBoxLayout(self)
        lay.addWidget(hint('Unposted entries from the latest imported CSV for each account are shown below. '
                           'They are excluded from balances, spending and forecasts. This is an export snapshot, '
                           'not a live list of bank holds. Import a newer CSV to replace it.'))
        reports, rows = [], []
        for account in store.accounts():
            if account_id is not None and account.id != account_id:
                continue
            report = store.get_setting(f'import_report:{account.id}')
            if report:
                reports.append(f'{account.name}: {import_summary(store, account.id)}')
                rows.extend((account.name, row) for row in report.get('pending',[]))
        note = QLabel('\n'.join(reports) or 'No CSV import details recorded yet.')
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        lay.addWidget(note)
        table = QTableWidget(len(rows),5)
        table.setHorizontalHeaderLabels(['Account','Transaction date','Description','Amount','Reference'])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i,(account,row) in enumerate(rows):
            for j,value in enumerate([account,row.get('date') or 'Unknown',row['description'],
                                       fmt_money(row['amount'],signed=True),row.get('reference','')]):
                table.setItem(i,j,QTableWidgetItem(str(value)))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        lay.addWidget(table)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

_DELIMS = [("Auto-detect", "auto"), ("Comma  ,", ","), ("Semicolon  ;", ";"),
           ("Tab", "\t"), ("Pipe  |", "|")]


class ImportDialog(QDialog):
    _NO_ACCOUNT = ("There is no checking, savings or credit-card account to "
                   "import into yet. Close this window, add one under "
                   "Accounts → Add account, then import again.")
    _CHOOSE_FILE = ("Choose the CSV file you downloaded from your bank. Doug "
                    "suggests which columns to use; you can check them before "
                    "anything is imported.")
    _CHECK_PREVIEW = ('Use Posting Date for balance history; weekend purchases may post later. '
                      'Transaction Date is kept separately. Check amount signs: spending is negative and income positive.')

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
        opts = QGridLayout()
        self.delim = QComboBox()
        for label, val in _DELIMS:
            self.delim.addItem(label, val)
        self.sign = QComboBox()
        self.sign.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sign.setMinimumContentsLength(16)
        self.sign.addItem("Auto-detect", "auto")
        for key, label in SIGN_MODES.items():
            self.sign.addItem(label, key)
        self.dayfirst = QCheckBox("Day-first dates (31/12/2025)")
        sep_label = QLabel("Column separator")
        sep_label.setBuddy(self.delim)
        opts.addWidget(sep_label,0,0)
        opts.addWidget(self.delim,0,1)
        opts.addWidget(self.dayfirst,0,2)
        sign_label = QLabel("Amount signs")
        sign_label.setBuddy(self.sign)
        opts.addWidget(sign_label,1,0)
        opts.addWidget(self.sign,1,1,1,2)
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
        form_row(form, "Posting / balance date", self.c_date)
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
        mapping_host = QWidget()
        mapping_layout = QVBoxLayout(mapping_host)
        mapping_layout.setContentsMargins(0,0,0,0)
        mapping_layout.addWidget(map_card)
        source = CollapsibleCard('Source details · optional',expanded=False)
        sf = QFormLayout()
        self.c_transaction_date = QComboBox()
        self.c_reference = QComboBox()
        self.c_status = QComboBox()
        form_row(sf,'Transaction date',self.c_transaction_date)
        form_row(sf,'Bank reference',self.c_reference)
        form_row(sf,'Pending / posted status',self.c_status)
        source.body.addLayout(sf)
        source.body.addWidget(hint('Explicit pending status or a blank Posting Date keeps an entry outside the posted ledger. '
                                   'A missing status column does not establish that every row is posted.'))
        mapping_layout.addWidget(source)
        mapping_layout.addStretch()
        mapping_scroll = QScrollArea()
        mapping_scroll.setWidgetResizable(True)
        mapping_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        mapping_scroll.setWidget(mapping_host)
        mapping_scroll.setMinimumHeight(160)
        lay.addWidget(mapping_scroll,1)

        # preview
        self.preview = QTableWidget(0, 0)
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setAlternatingRowColors(True)
        self.preview.setMinimumHeight(110)
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
            self.go.setEnabled(False)
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
                           (self.c_transaction_date, 'transaction_date'),
                           (self.c_reference, 'reference'), (self.c_status, 'status'),
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
            col_transaction_date=self.c_transaction_date.currentData() or '',
            col_reference=self.c_reference.currentData() or '', col_status=self.c_status.currentData() or '',
        )
        try:
            # One press of Ctrl-Z has to take back the whole import. Without
            # the group this is four separate steps — rows, saved mapping,
            # re-categorisation, patterns and checkpoint — and undoing "an
            # import" would mean pressing it until the numbers looked right.
            with self.store.undo_group("Import CSV"):
                try:
                    added, skipped = import_with_config(self.store, cfg)
                except PostingDateReviewRequired as review:
                    if not confirm_posting_dates(self, review.changes):
                        return
                    added, skipped = import_with_config(self.store, cfg, approve_date_updates=True)
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
            import_summary(self.store, cfg.account_id) + '\n'
            f"Doug found {n_rec} recurring bill or income "
            f"pattern{'s' if n_rec != 1 else ''}. Review "
            f"{'it' if n_rec == 1 else 'them'} under Bills & payday → "
            f"Recurring patterns.")
        self.accept()
