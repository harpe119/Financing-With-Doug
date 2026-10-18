"""A monthly spending plan with one shared allowance for the goal ladder."""
from __future__ import annotations

from datetime import date
import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QFrame, QHeaderView, QLabel,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..goals import build_plan, surplus_monthly
from ..funding import funding_capacity
from ..spending_plan import CATEGORY_LABELS, LABELS, calculate_plan, category_group
from . import theme
from .account_dialog import _money_spin
from .first_use import NO_ACCOUNTS, NO_TRANSACTIONS, READY, setup_stage
from .goal_funding import GoalFundingDialog, dollars
from .spending_details import SpendingDetailsDialog
from .widgets import Card, CollapsibleCard, FlowLayout, hint

_HISTORY_LINES = {'income', 'essential', 'debt', 'frivolous', 'giving', 'unknown'}


class PlanPage(QWidget):
    def __init__(self, store, main):
        super().__init__()
        self.setObjectName('page')
        self.store, self.main = store, main
        self._loading = False
        self._dirty = False
        self._signature = None
        self._default_allowance = 0.0
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
        self.verdict.setObjectName('headline')
        layout.addWidget(self.verdict)
        self.equation = hint('')
        layout.addWidget(self.equation)
        self.setup = hint('')
        layout.addWidget(self.setup)
        self.import_btn = QPushButton('Import a CSV…')
        self.import_btn.clicked.connect(self._setup_action)
        layout.addWidget(self.import_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        budget = Card('Your monthly spending plan')
        budget.body.addWidget(hint('Start with recent history, then override what has changed. '
            'This is a reusable monthly plan; recorded funding below tracks what you actually set aside.'))
        self.boxes, self.checks = {}, {}
        self.table = self._budget_table(LABELS)
        self.table.setAccessibleName('Monthly budget lines')
        self.table.setFixedHeight(40 * len(LABELS) + 40)
        budget.body.addWidget(self.table)
        self.source = hint('')
        budget.body.addWidget(self.source)
        details = QPushButton('Inspect income && spending transactions…')
        details.clicked.connect(self._details)
        budget.body.addWidget(details, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(budget)
        categories = CollapsibleCard('Adjust individual spending categories', expanded=False)
        categories.body.addWidget(hint('Change dining, subscriptions or another category to make room for goals. '
            'These amounts feed the totals above. Turn off a total override before editing its categories.'))
        self.category_table = self._budget_table(CATEGORY_LABELS)
        self.category_table.setAccessibleName('Spending categories')
        self.category_table.setMinimumHeight(260)
        categories.body.addWidget(self.category_table)
        layout.addWidget(categories)
        self._tables = [(self.table, LABELS), (self.category_table, CATEGORY_LABELS)]
        funding = Card('Goal funding')
        # One allowance for the whole planning loop. It is typed on the Goals tab
        # and saved as it is typed; this page only shows it, and offers to adopt
        # what the budget above leaves over.
        self.allowance_line = hint('')
        self.allowance_line.setTextFormat(Qt.TextFormat.PlainText)
        funding.body.addWidget(self.allowance_line)
        self.use_btn = QPushButton('')
        self.use_btn.setObjectName('small')
        self.use_btn.clicked.connect(self._use_available)
        funding.body.addWidget(self.use_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        funding.body.addWidget(hint('Shares follow the priority and split on the Goals tab. Regular loan '
            'payments are already budgeted above. The allowance saves as you type there; saving this '
            'plan keeps it.'))
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
        flow.addWidget(self.activity_btn)
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

    def _budget_table(self, labels):
        table = QTableWidget(len(labels), 4)
        table.setHorizontalHeaderLabels(['Monthly amount', 'Detected', 'Override', 'Your plan'])
        # Each row holds a checkbox and an amount box; Tab goes box to box and then on
        # past the table instead of circling the cells.
        table.setTabKeyNavigation(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(40)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1, 120), (2, 85), (3, 155)):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(col, width)
        for row, (key, label) in enumerate(labels.items()):
            table.setItem(row, 0, QTableWidgetItem(label))
            table.setItem(row, 1, QTableWidgetItem(''))
            check = QCheckBox()
            check.setAccessibleName(f'Override {label}')
            box = _money_spin(0, 1e9)
            box.setAccessibleName(label)
            table.setCellWidget(row, 2, check)
            table.setCellWidget(row, 3, box)
            self.boxes[key], self.checks[key] = box, check
            check.toggled.connect(lambda _on, k=key: self._override_changed(k))
            box.valueChanged.connect(self._changed)
        return table

    def reset_for_store(self):
        self._dirty = False
        self._signature = None
        self._default_allowance = 0.0

    def _allowance(self) -> float:
        """The one monthly allowance: what Goals saved, else what the saved budget leaves."""
        stored = self.store.get_setting('plan_surplus')
        try:
            return float(stored) if stored is not None else self._default_allowance
        except (TypeError, ValueError):
            return self._default_allowance

    def refresh(self):
        signature = (self.store.get_setting('spending_plan'), self.store.get_setting('plan_surplus'))
        if self._dirty and signature == self._signature:
            self._render()
            return
        # Merge saved changes from elsewhere without throwing away unrelated
        # edits in this page's draft: only rows changed locally override the
        # incoming saved values.
        draft = self._overrides() if self._dirty else {}
        previous = (self._signature[0] or {}) if self._signature else {}
        changed = ({key for key in set(draft) | set(previous)
                    if draft.get(key) != previous.get(key)} if self._dirty else set())
        self._loading = True
        plan = calculate_plan(self.store)
        overrides = dict(plan.overrides)
        for key in changed:
            if key in draft:
                overrides[key] = draft[key]
            else:
                overrides.pop(key, None)
        draft_plan = calculate_plan(self.store, overrides)
        self._signature = signature
        for key in self.boxes:
            self.checks[key].setChecked(key in overrides)
            self.boxes[key].setValue(draft_plan.amounts[key])
            self.boxes[key].setEnabled(key in overrides)
        self._default_allowance = max(0.0, plan.available)
        self._loading = False
        self._dirty = overrides != plan.overrides
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

    def _setup_action(self) -> None:
        """The next step: an account first, then its statements."""
        if setup_stage(self.store) == NO_ACCOUNTS:
            self.main.add_account()
        else:
            self.main.open_import()

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
        stage = setup_stage(self.store)
        manual = ('type your own amounts below: tick Override on a row and enter $0 '
                  'where nothing is planned.')
        self.setup.setText({
            NO_ACCOUNTS: 'Doug builds this plan from one complete month of your statements. '
                         'Add an account and import its statements, or ' + manual,
            NO_TRANSACTIONS: 'Doug builds this plan from one complete month of your statements. '
                             'Import one, or ' + manual,
            READY: 'Only complete months count, and there is not one yet. Import an '
                   'earlier statement, or ' + manual,
        }[stage])
        self.import_btn.setText('Add an account' if stage == NO_ACCOUNTS else
                                'Import more history…' if stage == READY else 'Import a CSV…')
        for table, labels in self._tables:
            for row, key in enumerate(labels):
                table.item(row, 1).setText('—' if not plan.details and key in _HISTORY_LINES
                    and not (key == 'debt' and plan.required_debt > 0) else dollars(plan.detected[key]))
        for key in self.boxes:
            parent_override = category_group(key) in plan.overrides
            self.checks[key].setEnabled(not parent_override)
            self.boxes[key].setEnabled(self.checks[key].isChecked() and not parent_override)
            if not self.checks[key].isChecked():
                self.boxes[key].blockSignals(True)
                self.boxes[key].setValue(plan.amounts[key])
                self.boxes[key].blockSignals(False)
        budget = self._allowance()
        stored = self.store.get_setting('plan_surplus')
        available = max(0.0, plan.available)
        if stored is None:
            self.allowance_line.setText(
                f'No allowance is chosen yet, so Goals is planning with '
                f'{dollars(surplus_monthly(self.store))} a month (income minus spending, '
                f'before reserves and investing). This budget leaves {dollars(available)}.')
        else:
            self.allowance_line.setText(
                f'Your allowance is {dollars(budget)} a month. Change it on the Goals tab. '
                f'This budget leaves {dollars(available)} available.')
        differs = stored is None or abs(budget - available) >= .005
        self.use_btn.setText(f'Use {dollars(available)} as my allowance')
        self.use_btn.setVisible(self._ready and differs)
        remaining = plan.remaining(budget)
        if stored is None:
            # With no allowance chosen there is nothing to be "left unassigned"
            # against: the page used to subtract its own figure from itself and
            # report $0.00, while Goals planned with a different one. Say what the
            # budget leaves; the line below names what Goals is using meanwhile.
            self.verdict.setText(f'{dollars(available)} / month available for goals and extra debt'
                                 if self._ready else 'Monthly plan not ready')
            colour = theme.TEXT if not self._ready else theme.ACCENT
        else:
            self.verdict.setText((f'{dollars(abs(remaining))} / month ' +
                ('over budget' if remaining < -.005 else 'left unassigned'))
                if self._ready else 'Monthly plan not ready')
            colour = (theme.TEXT if not self._ready else theme.RED if remaining < -.005
                      else theme.ACCENT)
        self.verdict.setStyleSheet(f'color: {colour};')
        self.equation.setText(
            (f'{dollars(plan.amounts["income"])} income − {dollars(plan.expenses)} spending, '
             f'reserves and investing = {dollars(available)} available.' if stored is None else
             f'{dollars(plan.amounts["income"])} income − '
             f'{dollars(plan.expenses)} spending, reserves and investing − {dollars(budget)} goals and extra debt.')
            if self._ready else '')
        self.equation.setVisible(self._ready)       # the setup paragraph says it once
        n = plan.details.aggregates.n_months if plan.details else 0
        self.source.setText((f'History uses {n} complete months; recent months count more. '
            if n else '')
            + f'Debt uses the higher of recorded payments or {dollars(plan.required_debt)} in current loan commitments. '
            'Investment contributions come from Accounts. Transfers and reimbursable business are excluded. '
            'The extra reserve is for costs not already covered by spending above.')
        ladder = build_plan(self.store, surplus=budget, debt_budget=plan.amounts['debt'])
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
        capacity, _free = funding_capacity(self.store)
        names = {a.id: a.name for a in self.store.accounts()}
        for aid, available in capacity.items():
            if available < -.005:
                warnings.append(f'{names[aid]} has {dollars(-available)} less than its goal earmarks. Review its balance and goal activity.')
        self.funding_note.setText(' '.join(warnings) +
            ' Monthly actual = recorded contributions minus releases; purchases remain goal progress. '
            'Override spending already covered by a goal reserve to avoid budgeting that purchase twice. '
            'Enable Use Goals funding plan on Forecast to apply the saved income, spending, investment '
            'contributions and goal-directed loan payments. Reserves stay in cash; '
            'goal earmarks retain their cash or investment holdings. '
            'Saving a plan does not move money.')
        self.state.setText('Complete income and spending first' if not self._ready else 'Unsaved changes' if self._dirty else
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
        overrides = self._overrides()
        stored = self.store.get_setting('plan_surplus')
        # A chosen allowance is kept. With none chosen, saving the budget makes the
        # allowance follow it, so the budget just saved leaves nothing over or short.
        allowance = (self._allowance() if stored is not None else
                     round(max(0.0, calculate_plan(self.store, overrides).available), 2))
        self.store.save_spending_plan(overrides, allowance)
        self._dirty = False
        self.refresh()
        self.main.refresh_header()
        self.main.status('Spending plan saved. Goals uses this monthly extra-money allowance.')

    def _use_available(self):
        plan = calculate_plan(self.store, self._overrides())
        amount = round(max(0.0, plan.available), 2)
        try:
            with self.store.undo_group('Use the budget as my allowance'):
                self.store.set_setting('plan_surplus', amount)
        except sqlite3.Error:
            self.main.status('The allowance was not saved. Check access to the database file and try again.')
            return
        self.refresh()
        self.main.refresh_header()
        self.main.status(f'Allowance set to {dollars(amount)} a month. Goals now uses it.')

    def _reload(self):
        self._dirty = False
        self.refresh()
