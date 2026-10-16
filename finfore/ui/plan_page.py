"""A monthly spending plan with one shared allowance for the goal ladder."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QFormLayout, QFrame, QHeaderView, QLabel,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..goals import build_plan
from ..funding import cash_capacity
from ..spending_plan import LABELS, calculate_plan
from . import theme
from .account_dialog import _money_spin
from .goal_funding import GoalFundingDialog, dollars
from .spending_details import SpendingDetailsDialog
from .widgets import Card, FlowLayout, hint

_HISTORY_LINES = {'income', 'essential', 'debt', 'frivolous', 'giving', 'unknown'}


class PlanPage(QWidget):
    def __init__(self, store, main):
        super().__init__()
        self.setObjectName('page')
        self.store, self.main = store, main
        self._loading = False
        self._dirty = False
        self._signature = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        self.verdict = QLabel('')
        self.verdict.setWordWrap(True)
        self.verdict.setTextFormat(Qt.TextFormat.PlainText)
        self.verdict.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(self.verdict)
        self.equation = hint('')
        layout.addWidget(self.equation)
        self.setup = hint('')
        layout.addWidget(self.setup)
        self.import_btn = QPushButton('Import transactions…')
        self.import_btn.clicked.connect(main.open_import)
        layout.addWidget(self.import_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        budget = Card('Your monthly spending plan')
        budget.body.addWidget(hint('Start with recent history, then override what has changed. '
            'This is a reusable monthly plan; recorded funding below tracks what you actually set aside.'))
        self.table = QTableWidget(len(LABELS), 4)
        self.table.setHorizontalHeaderLabels(['Monthly amount', 'Detected', 'Override', 'Your plan'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 120), (2, 85), (3, 155)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        self.table.setFixedHeight(40 * len(LABELS) + 40)
        self.boxes, self.checks = {}, {}
        for row, (key, label) in enumerate(LABELS.items()):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem(''))
            check = QCheckBox()
            check.setAccessibleName(f'Override {label}')
            box = _money_spin(0, 1e9)
            box.setAccessibleName(label)
            self.table.setCellWidget(row, 2, check)
            self.table.setCellWidget(row, 3, box)
            self.boxes[key], self.checks[key] = box, check
            check.toggled.connect(lambda _on, k=key: self._override_changed(k))
            box.valueChanged.connect(self._changed)
        budget.body.addWidget(self.table)
        self.source = hint('')
        budget.body.addWidget(self.source)
        details = QPushButton('Inspect income & spending transactions…')
        details.clicked.connect(self._details)
        budget.body.addWidget(details, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(budget)
        funding = Card('Goal funding')
        form = QFormLayout()
        self.goal_budget = _money_spin(0, 1e9)
        self.goal_budget.setAccessibleName('Monthly allowance for goals and extra debt')
        self.goal_budget.valueChanged.connect(self._changed)
        form.addRow('To goals & extra debt / month', self.goal_budget)
        funding.body.addLayout(form)
        funding.body.addWidget(hint('Shares follow the priority and split on Goals. Regular loan payments '
            'are already budgeted above. Saving this plan updates the same extra-money allowance on Goals.'))
        self.goal_table = QTableWidget(0, 4)
        self.goal_table.setHorizontalHeaderLabels(['Goal / loan', 'Planned extra / mo', 'Set aside this month', 'Still saved'])
        self.goal_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.goal_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.goal_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.goal_table.verticalHeader().hide()
        self.goal_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.goal_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.goal_table.setMinimumHeight(160)
        funding.body.addWidget(self.goal_table)
        actions = QWidget()
        flow = FlowLayout(actions, spacing=8)
        self.activity_btn = QPushButton('Record / view goal activity…')
        self.activity_btn.clicked.connect(self._activity)
        self.goal_table.itemSelectionChanged.connect(self._sync_activity)
        goals = QPushButton('Manage goals & priorities')
        goals.clicked.connect(lambda: main.show_page('Goals'))
        for button in (self.activity_btn, goals):
            flow.addWidget(button)
        funding.body.addWidget(actions)
        self.funding_note = hint('')
        funding.body.addWidget(self.funding_note)
        layout.addWidget(funding)
        layout.addStretch()
        self.scroll.setWidget(content)
        root.addWidget(self.scroll)
        footer = QWidget()
        flow = FlowLayout(footer, spacing=8)
        self.save_btn = QPushButton('Save spending plan')
        self.save_btn.setObjectName('primary')
        self.save_btn.clicked.connect(self._save)
        reload_btn = QPushButton('Reload saved plan')
        reload_btn.clicked.connect(self._reload)
        self.state = hint('')
        self.state.setWordWrap(False)
        for widget in (self.save_btn, reload_btn, self.state):
            flow.addWidget(widget)
        root.addWidget(footer)
        self._goal_ids = []

    def reset_for_store(self):
        self._dirty = False
        self._signature = None

    def refresh(self):
        signature = (self.store.get_setting('spending_plan'), self.store.get_setting('plan_surplus'))
        if self._dirty and signature == self._signature:
            self._render()
            return
        self._loading = True
        plan = calculate_plan(self.store)
        self._signature = signature
        for row, key in enumerate(LABELS):
            self.table.item(row, 1).setText(dollars(plan.detected[key]))
            self.checks[key].setChecked(key in plan.overrides)
            self.boxes[key].setValue(plan.amounts[key])
            self.boxes[key].setEnabled(key in plan.overrides)
        stored = self.store.get_setting('plan_surplus')
        self.goal_budget.setValue(stored if stored is not None else max(0, plan.available))
        self._loading = False
        self._dirty = False
        self._render()

    def _overrides(self):
        return {k: box.value() for k, box in self.boxes.items() if self.checks[k].isChecked()}

    def _override_changed(self, key):
        if self._loading:
            return
        self.boxes[key].setEnabled(self.checks[key].isChecked())
        if not self.checks[key].isChecked():
            self.boxes[key].setValue(calculate_plan(self.store, {}).detected[key])
        self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        self._dirty = True
        self._render()

    def _render(self):
        plan = calculate_plan(self.store, self._overrides())
        self._ready = bool(plan.details) or _HISTORY_LINES.issubset(plan.overrides)
        self.setup.setVisible(not self._ready)
        self.import_btn.setVisible(not self._ready)
        self.setup.setText('No complete months of history yet. Import a complete month, or check '
            'Override for income and each spending line through Uncategorised spending. '
            'Enter $0 explicitly where nothing is planned.')
        for row, key in enumerate(LABELS):
            self.table.item(row, 1).setText('—' if not plan.details and key in _HISTORY_LINES
                and not (key == 'debt' and plan.required_debt > 0) else dollars(plan.detected[key]))
            if not self.checks[key].isChecked():
                self.boxes[key].blockSignals(True)
                self.boxes[key].setValue(plan.amounts[key])
                self.boxes[key].blockSignals(False)
        budget = self.goal_budget.value()
        remaining = plan.remaining(budget)
        self.verdict.setText((f'{dollars(abs(remaining))} / month ' +
            ('over budget' if remaining < -.005 else 'left unassigned'))
            if self._ready else 'Monthly plan not ready')
        self.verdict.setStyleSheet(f'font-size: 20px; font-weight: 600; color: '
                                  f'{theme.TEXT if not self._ready else theme.RED if remaining < -.005 else theme.ACCENT};')
        self.equation.setText((f'{dollars(plan.amounts["income"])} income − '
            f'{dollars(plan.expenses)} spending & reserves − {dollars(budget)} goals & extra debt.')
            if self._ready else 'Enter income and spending to calculate the monthly amount available.')
        n = plan.details.aggregates.n_months if plan.details else 0
        self.source.setText((f'History uses {n} complete months; recent months count more. '
            if n else 'No complete months yet. Enter overrides or import older transactions. ')
            + f'Debt uses the higher of recorded payments or {dollars(plan.required_debt)} in current loan commitments. '
            'Investment contributions come from Accounts. Transfers and reimbursable business are excluded. '
            'The extra reserve is for costs not already covered by spending above.')
        ladder = build_plan(self.store, surplus=budget)
        savings = {g.id: g for g in self.store.goals() if g.kind == 'savings'}
        month = date.today().strftime('%Y-%m')
        actual = {}
        for entry in self.store.goal_activity():
            if entry['when_date'].startswith(month) and entry['action'] in ('contribute', 'release'):
                sign = -1 if entry['action'] == 'release' else 1
                actual[entry['goal_id']] = actual.get(entry['goal_id'], 0) + sign * entry['amount']
        previous = self._selected_goal()
        self._goal_ids = []
        self.goal_table.setRowCount(len(ladder.allocations))
        for i, allocation in enumerate(ladder.allocations):
            goal = savings.get(allocation.target.goal_id)
            self._goal_ids.append(goal.id if goal else None)
            values = [allocation.target.name, dollars(allocation.monthly),
                      dollars(actual.get(goal.id, 0)) if goal else
                      'See bank ledger' if allocation.target.kind == 'loan' else 'Balance milestone',
                      dollars(goal.saved_amount) if goal else '—']
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.goal_table.setItem(i, j, item)
        if previous in self._goal_ids and previous is not None:
            self.goal_table.selectRow(self._goal_ids.index(previous))
        elif savings:
            self.goal_table.selectRow(next(i for i, gid in enumerate(self._goal_ids) if gid is not None))
        self._sync_activity()
        warnings = []
        if plan.amounts['debt'] + .005 < plan.required_debt:
            warnings.append('Debt override is below current loan payments; Goals still includes those payments.')
        if not ladder.allocations:
            warnings.append('Add a goal on Goals to start a funding plan.')
        warnings.extend(ladder.warnings)
        capacity, _free = cash_capacity(self.store)
        names = {a.id: a.name for a in self.store.accounts()}
        for aid, available in capacity.items():
            if available < -.005:
                warnings.append(f'{names[aid]} has {dollars(-available)} less than its goal earmarks. Review its balance and goal activity.')
        self.funding_note.setText(' '.join(warnings) +
            ' Monthly actual = recorded contributions minus releases; purchases remain goal progress. '
            'Override spending already covered by a goal reserve to avoid budgeting that purchase twice. '
            'Plan amounts are intentions and do not change the Forecast scenario or move money.')
        self.state.setText('Complete income & spending first' if not self._ready else 'Unsaved changes' if self._dirty else
                           'Saved monthly plan' if self.store.get_setting('spending_plan') is not None else 'Preview — save to use on Goals')
        self.save_btn.setEnabled(self._ready and (self._dirty or self.store.get_setting('spending_plan') is None))

    def _selected_goal(self):
        row = self.goal_table.currentRow()
        return self._goal_ids[row] if 0 <= row < len(self._goal_ids) else None

    def _sync_activity(self):
        self.activity_btn.setEnabled(self._selected_goal() is not None)

    def _activity(self):
        gid = self._selected_goal()
        if gid is not None:
            GoalFundingDialog(self.store, gid, self).exec()
            self._render()
            self.main.refresh_header()

    def _details(self):
        plan = calculate_plan(self.store, self._overrides())
        SpendingDetailsDialog(plan.details, '<h3>Your spending plan</h3><p>The history tab shows '
            'the transactions behind detected amounts. Debt is carved out of necessities once. '
            'Your overrides, extra reserves and investment commitments are planning assumptions.</p>',
            {a.id: a.name for a in self.store.accounts()}, self).exec()

    def _save(self):
        if not self._ready:
            return
        self.store.save_spending_plan(self._overrides(), self.goal_budget.value())
        self._dirty = False
        self.refresh()
        self.main.refresh_header()
        self.main.status('Spending plan saved. Goals uses this monthly extra-money allowance.')

    def _reload(self):
        self._dirty = False
        self.refresh()
