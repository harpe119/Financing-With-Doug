"""Record and inspect money earmarked for one goal."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFormLayout, QFrame, QHeaderView, QLineEdit, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from ..categorize import BUSINESS_CATEGORY
from ..funding import ACTIONS, cash_capacity, goal_buckets
from ..models import AccountType
from . import theme
from .account_dialog import _money_spin
from .widgets import hint


def dollars(value):
    return f'${value:,.2f}'


class GoalFundingDialog(QDialog):
    def __init__(self, store, goal_id, parent=None):
        super().__init__(parent)
        self.store, self.goal_id = store, goal_id
        self.setWindowTitle('Goal activity')
        self.resize(760, 650)
        self.setMinimumSize(580, 420)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll)
        self.summary = hint('')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.accounts = {a.id: a.name for a in store.accounts()}
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.action = QComboBox()
        for key, label in ACTIONS.items():
            self.action.addItem(label, key)
        self.account = QComboBox()
        self.account.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.account.setMinimumContentsLength(16)
        self.account.addItem('Cash not yet located in an account', None)
        for a in store.accounts():
            if a.type in (AccountType.CHECKING, AccountType.SAVINGS):
                self.account.addItem(a.name, a.id)
        self.amount = _money_spin(0, 1e9)
        self.when = QDateEdit(QDate.currentDate())
        self.when.setCalendarPopup(True)
        self.when.setMaximumDate(QDate.currentDate())
        self.when.setDisplayFormat('MMM d, yyyy')
        self.transaction = QComboBox()
        self.transaction.setMinimumContentsLength(16)
        self.transaction.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.note = QLineEdit()
        self.note.setPlaceholderText('Optional note')
        for label, widget in [('Activity', self.action), ('Account holding savings', self.account),
            ('Amount', self.amount), ('Date', self.when), ('Link imported transaction', self.transaction),
            ('Note', self.note)]:
            form.addRow(label, widget)
        layout.addLayout(form)
        self.context = hint('')
        layout.addWidget(self.context)
        self.error = hint('')
        self.error.setStyleSheet(f'color: {theme.RED};')
        layout.addWidget(self.error)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Date', 'Activity', 'Amount', 'Account', 'Note / transaction'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(150)
        layout.addWidget(self.table, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('Record activity')
        self.buttons.accepted.connect(self._record)
        self.buttons.rejected.connect(self.accept)
        root.addWidget(self.buttons)
        self.action.currentIndexChanged.connect(self._sync)
        self.account.currentIndexChanged.connect(self._sync)
        self.transaction.currentIndexChanged.connect(self._tx_changed)
        self._refresh()

    def _refresh(self):
        goal = next(g for g in self.store.goals() if g.id == self.goal_id)
        self.summary.setText(f'{goal.name} · Target {dollars(goal.amount)} · '
            f'Still saved {dollars(goal.saved_amount)} · Purchases {dollars(goal.spent_amount)}')
        entries = self.store.goal_activity(self.goal_id)
        self.table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            values = [entry['when_date'], ACTIONS[entry['action']], dollars(entry['amount']),
                      self.accounts.get(entry['account_id'], 'Unlocated cash'), entry['note']]
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(i, j, item)
        self._sync()

    def _sync(self):
        action, aid = self.action.currentData(), self.account.currentData()
        buckets = goal_buckets(self.store, self.goal_id)
        capacities, free = cash_capacity(self.store)
        self.context.setText(
            f'This goal holds {dollars(buckets.get(aid, 0))} here. '
            + (f'Unassigned net cash: {dollars(free)}. ' if action == 'contribute' else '')
            + ('Locate money already counted in “Still saved”; this adds no new savings.' if action == 'assign'
               else 'Purchases count toward this goal and reduce savings held. For a reserve you intend to rebuild, use Release savings.' if action == 'purchase'
               else 'Release reduces progress so you can rebuild this reserve later.' if action == 'release'
               else 'Earmarks existing cash. No bank transfer or new income is created.')
            + (f' This account has {dollars(capacities.get(aid, 0))} before unlocated earmarks.'
               if aid is not None and action in ('assign', 'contribute') else ''))
        previous = self.transaction.currentData()
        self.transaction.blockSignals(True)
        self.transaction.clear()
        self.transaction.addItem('No transaction — record an earmark only', None)
        if aid is not None and action in ('contribute', 'purchase'):
            tx = self.store.transactions(aid)
            tx = tx[(tx.date.dt.date <= date.today()) & (tx.category != BUSINESS_CATEGORY)]
            tx = tx[tx.amount > 0] if action == 'contribute' else tx[(tx.amount < 0) & (tx.category != 'Transfer')]
            for row in tx.sort_values(['date', 'id'], ascending=False).itertuples():
                self.transaction.addItem(f'{row.date:%Y-%m-%d} · {row.description} · {dollars(row.amount)}', int(row.id))
        self.transaction.setCurrentIndex(max(0, self.transaction.findData(previous)))
        self.transaction.setEnabled(self.transaction.count() > 1)
        self.transaction.blockSignals(False)
        self._tx_changed()

    def _tx_changed(self):
        txid = self.transaction.currentData()
        self.when.setEnabled(txid is None)
        if txid is not None:
            row = self.store.transactions().set_index('id').loc[txid]
            self.when.setDate(QDate(row.date.year, row.date.month, row.date.day))

    def _record(self):
        try:
            self.store.record_goal_activity(self.goal_id, self.action.currentData(), self.amount.value(),
                self.when.date().toPyDate(), self.account.currentData(), self.transaction.currentData(), self.note.text())
        except ValueError as exc:
            self.error.setStyleSheet(f'color: {theme.RED};')
            self.error.setText(str(exc))
            return
        self.error.setStyleSheet(f'color: {theme.TEXT};')
        self.error.setText('Activity recorded. Use Undo in the main window to reverse it.')
        self.amount.setValue(0)
        self.note.clear()
        self._refresh()
