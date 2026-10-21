"""Review selected transactions sharing an exact merchant name."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHeaderView, QLabel, QMessageBox, QPushButton,
                             QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..categorize import BUSINESS_CATEGORY
from ..category_review import matching_merchant_transactions, merchant_rule_transactions
from ..util import fmt_day, fmt_money
from .widgets import FlowLayout, hint

_UNKNOWN = {'Misc', 'Other income', ''}
_FIELDS = ['account_id', 'date', 'description', 'amount', 'category',
           'category_source', 'category_reason', 'category_reviewed',
           'category_reviewed_at', 'business_reimbursement']


class MerchantReviewDialog(QDialog):
    def __init__(self, store, transaction_id: int, categories: list[str], parent=None):
        super().__init__(parent)
        self.store, self.transaction_id = store, transaction_id
        self._loading = False
        self.setWindowTitle('Review transactions with the same name')
        self.setMinimumSize(820, 480)
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        self.name = QLabel()
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setWordWrap(True)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.name)
        self.summary = hint('')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        layout.addWidget(hint(
            'Matches the exact name, ignoring case and extra spaces, across all accounts and dates. '
            'Only unreviewed entries in the selected transaction’s category start checked. '
            'Check individual rows when personal and business purchases share a name.'))
        row = FlowLayout(spacing=8)
        label = QLabel('Category for checked entries')
        self.category = QComboBox()
        self.category.setAccessibleName(label.text())
        label.setBuddy(self.category)
        self.category.addItem('Choose category…', '')
        self.category.addItems(categories)
        row.addWidget(label)
        row.addWidget(self.category)
        self.reload_btn = QPushButton('Reload preview')
        self.reload_btn.clicked.connect(self._reload)
        row.addWidget(self.reload_btn)
        layout.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setAccessibleName('Matching transactions to review')
        self.table.setHorizontalHeaderLabels(
            ['Review', 'Date', 'Account', 'Description', 'Amount', 'Current category', 'Status'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._update_pending)
        layout.addWidget(self.table, 1)
        choices = FlowLayout(spacing=8)
        self.same_category_btn = QPushButton('Select this category')
        self.same_category_btn.clicked.connect(lambda: self._select('category'))
        self.unknown_btn = QPushButton('Select uncategorized')
        self.unknown_btn.clicked.connect(lambda: self._select('unknown'))
        self.clear_btn = QPushButton('Clear selection')
        self.clear_btn.clicked.connect(lambda: self._select('none'))
        for button in (self.same_category_btn, self.unknown_btn, self.clear_btn):
            choices.addWidget(button)
        layout.addLayout(choices)
        self.remember = QCheckBox('Remember category for future imports')
        self.remember.setToolTip(
            'Use only for a name with a consistent category, such as Chipotle. '
            'Remembered rules can also match merchant name variants. Conflicting categories '
            'or business entries in those matches prevent remembering here. '
            'Future purchases still need review.')
        layout.addWidget(self.remember)
        self.scope_note = hint('')
        self.scope_note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.scope_note)
        self.status = hint('')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox()
        self.apply_btn = buttons.addButton('Review checked entries', QDialogButtonBox.ButtonRole.ActionRole)
        self.apply_btn.setObjectName('primary')
        self.apply_btn.clicked.connect(self._apply)
        buttons.addButton('Close', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.category.currentIndexChanged.connect(self._update_pending)
        self._reload()

    def _reload(self):
        self._loading = True
        try:
            rows = matching_merchant_transactions(self.store, self.transaction_id)
            self._snapshot = rows.set_index('id')
            self._rule_rows = merchant_rule_transactions(self.store, rows.id)
            source = self._snapshot.loc[self.transaction_id]
            self.name.setText(str(source.description))
            self.name.setAccessibleName(str(source.description))
            self.summary.setText(
                f'{len(rows)} matching transaction{"s" if len(rows) != 1 else ""} · '
                f'{int(rows.category_reviewed.sum())} already reviewed')
            current = str(source.category)
            index = self.category.findText(current)
            if index < 0:
                self.category.addItem(current)
                index = self.category.count() - 1
            self.category.setCurrentIndex(index if current not in _UNKNOWN else 0)
            accounts = {a.id: a.name for a in self.store.accounts()}
            self.table.setRowCount(len(rows))
            for i, entry in enumerate(rows.itertuples(index=False)):
                reviewed = bool(entry.category_reviewed)
                check = QTableWidgetItem('')
                check.setData(Qt.ItemDataRole.UserRole, int(entry.id))
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                               | (Qt.ItemFlag.NoItemFlags if reviewed else Qt.ItemFlag.ItemIsUserCheckable))
                check.setCheckState(Qt.CheckState.Checked if not reviewed and current not in _UNKNOWN
                                    and entry.category == current else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.AccessibleTextRole,
                              f'Review {entry.description}, {fmt_day(entry.date)}, '
                              f'{accounts.get(entry.account_id, "Unknown account")}')
                check.setToolTip('Already reviewed; its category will be kept.' if reviewed
                                 else 'Check to apply the chosen category to this transaction.')
                self.table.setItem(i, 0, check)
                for j, text in enumerate((fmt_day(entry.date), accounts.get(entry.account_id, '?'),
                        entry.description, fmt_money(entry.amount, signed=True), entry.category,
                        'Reviewed — kept' if reviewed else 'Not reviewed'), 1):
                    item = QTableWidgetItem(str(text))
                    item.setToolTip(str(text))
                    self.table.setItem(i, j, item)
            self.status.clear()
        except Exception as exc:
            self._snapshot = None
            self._rule_rows = None
            self.category.setCurrentIndex(0)
            self.table.setRowCount(0)
            self.status.setText(f'Could not load matching transactions. Reload the preview. {exc}')
        finally:
            self._loading = False
        self._update_pending()

    def _selected_ids(self):
        return [int(item.data(Qt.ItemDataRole.UserRole)) for i in range(self.table.rowCount())
                if (item := self.table.item(i, 0)).checkState() == Qt.CheckState.Checked
                and item.flags() & Qt.ItemFlag.ItemIsUserCheckable]

    def _select(self, mode):
        self._loading = True
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                continue
            category = self.table.item(i, 5).text()
            selected = (category == self.category.currentText() if mode == 'category'
                        else category in _UNKNOWN if mode == 'unknown' else False)
            # Category/unknown buttons add to the selection; Clear removes it.
            if selected or mode == 'none':
                item.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self._loading = False
        self._update_pending()

    def _update_pending(self, *_):
        if self._loading:
            return
        ids = self._selected_ids()
        category = self.category.currentText() if self.category.currentIndex() > 0 else ''
        known = (set(self._rule_rows.category) - _UNKNOWN) if self._rule_rows is not None else set()
        mixed = len(known | ({category} if category else set())) > 1
        business = (BUSINESS_CATEGORY in known or category == BUSINESS_CATEGORY or
                    (self._rule_rows is not None and self._rule_rows.business_reimbursement.gt(0).any()))
        can_remember = bool(category and not mixed and not business)
        if not can_remember:
            self.remember.setChecked(False)
        self.remember.setEnabled(can_remember)
        self.scope_note.setText(
            'This merchant has conflicting categories or business entries. Only checked transactions '
            'will change; future import rules stay as they are.' if mixed or business else
            'Existing reviews are kept. Remembering a category does not mark future purchases reviewed.')
        self.apply_btn.setEnabled(bool(ids and category))
        self.apply_btn.setText(f'Review {len(ids)} checked entr{"y" if len(ids) == 1 else "ies"}'
                               if ids else 'Review checked entries')
        self.same_category_btn.setEnabled(bool(category))

    def _apply(self):
        ids = self._selected_ids()
        if not ids or self.category.currentIndex() <= 0:
            return
        try:
            if getattr(self.parentWidget(), 'store', self.store) is not self.store:
                raise ValueError('The ledger changed. Close this window and select the transactions again.')
            fresh = self.store.transactions().set_index('id')
            if (not set(ids).issubset(fresh.index) or
                    not fresh.loc[ids, _FIELDS].equals(self._snapshot.loc[ids, _FIELDS])):
                raise ValueError('These transactions changed while the preview was open. '
                                 'Reload the preview and check your selections again.')
            if self.remember.isChecked():
                matching = merchant_rule_transactions(self.store, ids)
                known = set(matching.category) - _UNKNOWN
                if (not self.remember.isEnabled() or BUSINESS_CATEGORY in known or
                        len(known | {self.category.currentText()}) > 1 or
                        matching.business_reimbursement.gt(0).any()):
                    raise ValueError('This name now includes conflicting categories or business entries. '
                                     'Reload the preview and leave remembering off.')
            result = self.store.review_transactions(ids, self.category.currentText(),
                                                   remember=self.remember.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, 'Transactions were not reviewed', str(exc))
            return
        self._reload()
        self.status.setText(f'Reviewed {result["reviewed"]} transaction'
            + ('s' if result['reviewed'] != 1 else '') + '. ' +
            ('Category remembered for future imports.' if result['remembered'] else 'Future import rules unchanged.'))
