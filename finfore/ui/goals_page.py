"""Goals page — one priority ladder over debts and savings goals.

The ladder is the whole idea. Loans and goals are the same kind of thing here:
something that consumes money until it is finished and then stops. Ordering
them together is what lets a car payment become an emergency fund the month it
clears, instead of dispersing back into ordinary spending — which is what
actually happens to most people's freed-up payments.
"""
from __future__ import annotations

from datetime import date
from html import escape
import logging
import math
import sqlite3

import numpy as np
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QBoxLayout, QComboBox, QDialog, QDialogButtonBox,
                             QFrame, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..goals import (GOAL, LOAN, PLAN_HORIZON_MONTHS, POLICIES, build_plan, deadline_plan,
                     emergency_goal_amount, speedups, surplus_monthly)
from ..models import Goal
from ..insights import monthly_aggregates
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .account_dialog import _money_spin
from .widgets import (Card, ChartKey, CollapsibleCard, FlowLayout, IntSpinBox, PopupDateEdit,
                      WrappingLabel, form_row, hint)
from .goal_funding import GoalFundingDialog
from .goal_funding_map import GoalFundingMap, goal_color

_log = logging.getLogger(__name__)

# The projection plots percent funded, so its vertical range is known: 0 to 100
# with a little headroom each side, and never zoomed in past a tenth of that.
_Y_HEADROOM = 8.0
_MIN_Y_SPAN = 10.0


class GoalDialog(QDialog):
    """Add or edit one savings goal."""

    MIN_WIDTH = 420     # wide enough for the name placeholder without clipping

    def __init__(self, parent=None, goal: Goal | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit goal" if goal else "Add goal")
        self.setMinimumWidth(self.MIN_WIDTH)
        self._goal = goal
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Emergency fund, new laptop, holiday…")
        self.amount = _money_spin(0, 1e9)
        self.kind = QComboBox()
        self.kind.addItem("Savings for a purchase or reserve", "savings")
        self.kind.addItem("Cash balance milestone", "cash")
        self.kind.addItem("Net worth milestone", "networth")
        self.saved = _money_spin(0, 1e9)
        self.saved_label = QLabel("Already saved")
        self.explanation = hint("")
        self.explanation.setTextFormat(Qt.TextFormat.PlainText)
        self.explanation.setMinimumHeight(60)
        self.error = hint("")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setStyleSheet(f"color: {theme.RED};")
        form.addRow("Name", self.name)
        form.addRow("Goal amount", self.amount)
        form.addRow("Track progress by", self.kind)
        form.addRow(self.saved_label, self.saved)
        form.addRow(self.explanation)
        form.addRow(self.error)
        self.kind.currentIndexChanged.connect(self._sync_kind)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Save changes" if goal else "Add goal")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        if goal:
            self.name.setText(goal.name)
            self.amount.setValue(goal.amount)
            i = self.kind.findData(goal.kind)
            self.kind.setCurrentIndex(max(0, i))
            self.saved.setValue(goal.saved_amount)
        self._sync_kind()

    def _sync_kind(self) -> None:
        savings = self.kind.currentData() == "savings"
        self.saved.setVisible(savings)
        self.saved_label.setVisible(savings)
        self.explanation.setText(
            "Enter money already set aside. After saving, use Locate savings to assign "
            "it to an account. Use Goal activity for later contributions, purchases or releases."
            if savings else
            "Uses total net cash, including money set aside for savings goals. "
            "Cash milestones share progress by priority; reordering can change it."
            if self.kind.currentData() == "cash" else
            "Uses total assets minus debts, shared by priority with other net "
            "worth milestones. This can include investments and goal savings.")

    def _ok(self) -> None:
        if not self.name.text().strip():
            self.error.setText("Give this goal a name.")
            self.name.setFocus()
            return
        if self.amount.value() <= 0:
            self.error.setText("Enter a goal amount greater than $0.")
            self.amount.setFocus()
            return
        if self.kind.currentData() == "savings" and self.saved.value() > self.amount.value():
            self.error.setText("Already saved is more than the goal amount. Raise the goal amount or correct what is saved.")
            self.saved.setFocus()
            return
        if (self.kind.currentData() == "savings" and self._goal and
                self.saved.value() + self._goal.spent_amount > self.amount.value() + .005):
            self.error.setText("The goal amount must cover savings plus purchases already recorded.")
            return
        self.accept()

    def values(self) -> tuple[str, float, str, float]:
        return (self.name.text().strip(), self.amount.value(),
                self.kind.currentData(), self.saved.value())


class _Columns(QWidget):
    """Panels side by side, stacked once the page is too narrow for both."""

    def __init__(self, *panes, breakpoint: int = 860):
        super().__init__()
        self._breakpoint = breakpoint
        self.layout_ = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(16)
        for pane in panes:
            pane.setMinimumWidth(0)
            pane.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.layout_.addWidget(pane, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        direction = (QBoxLayout.Direction.LeftToRight if self.width() >= self._breakpoint
                     else QBoxLayout.Direction.TopToBottom)
        if self.layout_.direction() != direction:
            self.layout_.setDirection(direction)


class GoalsPage(QWidget):
    COLS = ["#", "Goal", "Type", "Amount", "Progress", "Total /mo", "Done by"]
    ROW_HEIGHT = 36
    MIN_ROWS, MAX_ROWS = 2, 10

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._plan = None
        self._keys: list[str] = []
        self._split_boxes: dict = {}
        self._split_amounts: dict = {}
        self._loading = False
        self._syncing_selection = False
        self._selected_target = None
        self._selection_initialized = False
        self._source_store = store
        self._simple = False
        self._retry = self.refresh

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(0)
        self.error_host = QWidget()
        error_layout = QHBoxLayout(self.error_host)
        error_layout.setContentsMargins(0, 0, 0, 10)
        self.error_note = QLabel()
        self.error_note.setWordWrap(True)
        self.error_note.setTextFormat(Qt.TextFormat.PlainText)
        self.error_note.setStyleSheet(f'color: {theme.AMBER};')
        self.retry_btn = QPushButton('Retry')
        self.retry_btn.clicked.connect(lambda: self._retry())
        error_layout.addWidget(self.error_note, 1)
        error_layout.addWidget(self.retry_btn)
        root.addWidget(self.error_host)
        self.error_host.hide()
        self.funding_map = GoalFundingMap()
        self.funding_map.recordRequested.connect(self._record_for_goal)
        self.funding_map.selectionChanged.connect(self._map_selected)

        # ---------------- first run ----------------
        # With nothing on the ladder there is nothing to plan, so the page says
        # so once and offers the two ways to begin, instead of showing an empty
        # table, an empty chart and two panels that explain their own emptiness.
        self.start_card = Card("Nothing to plan yet")
        start_text = WrappingLabel(
            "Add something you are saving toward, like an emergency fund or a purchase. "
            "Loans added on Accounts join the list too. Doug puts them in order and "
            "shows when each one gets done.")
        start_text.setTextFormat(Qt.TextFormat.PlainText)
        self.start_card.body.addWidget(start_text)
        # Filled from the person's own data when there is any: what an
        # emergency fund would be and how long their spare money would take.
        self.start_note = WrappingLabel()
        self.start_note.setObjectName('hint')
        self.start_note.setTextFormat(Qt.TextFormat.PlainText)
        self.start_card.body.addWidget(self.start_note)
        start_row = QHBoxLayout()
        start_row.setSpacing(8)
        start_add = QPushButton("Add goal…")
        start_add.setObjectName("primary")
        start_add.clicked.connect(self._add)
        start_ef = QPushButton("Add emergency fund…")
        start_ef.setToolTip("Sized from your own essential spending")
        start_ef.clicked.connect(self._add_emergency)
        self.start_demo = QPushButton("Explore with a demo first")
        self.start_demo.setObjectName("link")
        self.start_demo.setToolTip("Opens fictional data in a separate file; your own data is untouched")
        self.start_demo.clicked.connect(lambda: self.main._load_demo("balanced", confirm=False))
        start_row.addWidget(start_add)
        start_row.addWidget(start_ef)
        start_row.addWidget(self.start_demo)
        start_row.addStretch(1)
        self.start_card.body.addLayout(start_row)

        # ---------------- ladder ----------------
        # Two kinds of control, kept apart. Adding a goal belongs to the whole
        # ladder, so it sits in the card header. Moving, editing and deleting
        # belong to one row, so they sit under the table beside the name of the
        # row they will act on. Recording where savings are held lives on the
        # map above, next to the accounts it is about.
        self.ladder = ladder = Card()
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("PRIORITY LADDER")
        title.setObjectName("cardTitle")
        head.addWidget(title, 1)
        self.ef_btn = QPushButton("Add emergency fund…")
        self.ef_btn.setObjectName("small")
        self.ef_btn.setToolTip("Sized from your own essential spending")
        self.ef_btn.clicked.connect(self._add_emergency)
        self.add_btn = QPushButton("Add goal…")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add)
        head.addWidget(self.ef_btn)
        head.addWidget(self.add_btn)
        ladder.body.addLayout(head)

        # The two levers that drive everything below, side by side and no wider
        # than their contents. A dollar amount does not need a 900px field, and
        # on a narrow page the pairs wrap onto a second row instead.
        inputs = QWidget()
        inputs_flow = FlowLayout(inputs, spacing=10)
        self.surplus = _money_spin(0, 1e9)
        self.surplus.setAccessibleName('Monthly allowance for goals and extra debt')
        self.surplus.setToolTip(
            "What is left over each month after everything you already spend, "
            "including loan minimums. Uses the saved monthly Plan when available, "
            "otherwise your ledger. This is the same allowance shown on Plan.")
        self.surplus.setMinimumWidth(150)
        self.surplus.valueChanged.connect(self._surplus_changed)
        detect = QPushButton("Use Plan / history")
        detect.setToolTip("Clear the manual allowance. Use the saved monthly Plan, or recent history if no Plan is saved.")
        detect.setObjectName("small")
        detect.clicked.connect(self._use_detected)
        plan_btn = QPushButton("Monthly budget")
        plan_btn.setObjectName("link")
        plan_btn.setToolTip("Open the monthly budget this allowance comes from")
        plan_btn.clicked.connect(lambda: self.main.show_page("Monthly budget"))
        self.policy = QComboBox()
        for key, label in POLICIES.items():
            self.policy.addItem(label, key)
        self.policy.currentIndexChanged.connect(self._policy_changed)
        for caption, widgets, gap in (("Monthly allowance", (self.surplus, detect, plan_btn), 18),
                                      ("How to divide it", (self.policy,), 0)):
            pair = QWidget()
            if caption == "How to divide it":
                self.policy_pair = pair
            row = QHBoxLayout(pair)
            # The trailing margin widens the gap between the two groups on one
            # row without indenting the second group when it wraps beneath.
            row.setContentsMargins(0, 0, gap, 0)
            row.setSpacing(8)
            label = QLabel(caption)
            label.setBuddy(widgets[0])
            row.addWidget(label)
            for widget in widgets:
                row.addWidget(widget)
            inputs_flow.addWidget(pair)
        inputs_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        inputs_policy.setHeightForWidth(True)
        inputs.setSizePolicy(inputs_policy)
        self.surplus_note = hint("")
        self.surplus_note.setTextFormat(Qt.TextFormat.PlainText)
        # The note explains the allowance, so it sits tight under the controls.
        inputs_group = QVBoxLayout()
        inputs_group.setSpacing(4)
        inputs_group.addWidget(inputs)
        inputs_group.addWidget(self.surplus_note)
        ladder.body.addLayout(inputs_group)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        for c in range(len(self.COLS)):
            self.table.horizontalHeaderItem(c).setTextAlignment(
                (Qt.AlignmentFlag.AlignRight if c in (3, 5) else Qt.AlignmentFlag.AlignLeft)
                | Qt.AlignmentFlag.AlignVCenter)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setAccessibleName('Goal and loan priorities')
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.ROW_HEIGHT)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3, 4, 5, 6):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self._edit())
        # The estimate (and, in Custom, what the share boxes mean) read as part of
        # the table, so they sit 6px under it. The selected row and its actions
        # are a separate group, set apart by the wider gap above them.
        self.plan_note = WrappingLabel()
        self.plan_note.setTextFormat(Qt.TextFormat.PlainText)
        self.allowance_btn = QPushButton("Enter an amount")
        self.allowance_btn.setObjectName("link")
        self.allowance_btn.setToolTip("Go to the monthly allowance field")
        self.allowance_btn.clicked.connect(self._focus_allowance)
        note_row = QHBoxLayout()
        note_row.setSpacing(8)
        note_row.addWidget(self.plan_note, 1)
        note_row.addWidget(self.allowance_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.custom_note = hint("")
        self.custom_note.setTextFormat(Qt.TextFormat.PlainText)
        table_group = QVBoxLayout()
        table_group.setSpacing(6)
        table_group.addWidget(self.table)
        table_group.addWidget(self.custom_note)
        table_group.addLayout(note_row)
        ladder.body.addLayout(table_group)

        self.selection_note = WrappingLabel()
        self.selection_note.setTextFormat(Qt.TextFormat.PlainText)
        self.payment_note = WrappingLabel()
        self.payment_note.setObjectName('hint')
        self.payment_note.setTextFormat(Qt.TextFormat.PlainText)

        actions_host = QWidget()
        acts = FlowLayout(actions_host, spacing=6)
        self.up = QPushButton("▲ Higher")
        self.up.setObjectName("small")
        self.up.setToolTip("Move this goal up the priority ladder")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down = QPushButton("▼ Lower")
        self.down.setObjectName("small")
        self.down.setToolTip("Move this goal down the priority ladder")
        self.down.clicked.connect(lambda: self._move(1))
        self.edit_btn = QPushButton("Edit goal…")
        self.edit_btn.setObjectName("small")
        self.edit_btn.clicked.connect(self._edit)
        self.delete_btn = QPushButton("Delete goal")
        self.delete_btn.setObjectName("dangerSmall")
        self.delete_btn.clicked.connect(self._delete)
        for b in (self.up, self.down, self.edit_btn, self.delete_btn):
            acts.addWidget(b)
        action_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        action_policy.setHeightForWidth(True)
        actions_host.setSizePolicy(action_policy)

        selection_group = QVBoxLayout()
        selection_group.setSpacing(2)
        selection_group.addWidget(self.selection_note)
        selection_group.addWidget(self.payment_note)
        selection_group.addSpacing(6)
        selection_group.addWidget(actions_host)
        ladder.body.addSpacing(6)
        ladder.body.addLayout(selection_group)

        # ---------------- what happens next ----------------
        cascade_card = Card("Payment rollover")
        self.cascade = WrappingLabel()
        self.cascade.setWordWrap(True)
        self.cascade.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cascade.setAlignment(Qt.AlignmentFlag.AlignTop)
        cascade_card.body.addWidget(self.cascade)
        cascade_card.body.addStretch(1)

        faster_card = Card("Getting there sooner")
        drow = QFormLayout()
        # Any rung, not just the top one. "What would it take to have the
        # computer by July, while still clearing the card first?" is the
        # question people actually have, and it is about a goal partway down
        # the ladder by definition.
        self.target_pick = QComboBox()
        self.target_pick.setMinimumWidth(150)
        self.target_pick.setToolTip(
            "Which goal to cost. Anything above it on the ladder keeps its "
            "priority, so the answer accounts for what you are already "
            "committed to.")
        self.target_pick.currentIndexChanged.connect(
            lambda _i: self._render_faster())
        self.target_pick.setAccessibleName("Goal to finish")
        form_row(drow, "Goal", self.target_pick)
        # Only the month and year count, and the plan looks 30 years ahead, so
        # the field offers this month to thirty years out and nothing else.
        self.by_date = PopupDateEdit()
        self.by_date.setDisplayFormat("MMM yyyy")
        self._sync_by_limits()
        self.by_date.setDate(QDate.currentDate().addMonths(12))
        self.by_date.setAccessibleName("Finish by: month and year")
        self.by_date.setToolTip(
            "Pick the month to finish by and Doug works out what would have to give, "
            "by name. Click the field, or press Alt+Down, for a calendar.")
        self.by_date.dateChanged.connect(lambda _d: self._render_faster())
        form_row(drow, "By", self.by_date)
        faster_card.body.addLayout(drow)
        self.faster = WrappingLabel()
        self.faster.setWordWrap(True)
        self.faster.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.faster.setAlignment(Qt.AlignmentFlag.AlignTop)
        faster_card.body.addWidget(self.faster)
        faster_card.body.addStretch(1)
        self.lower = _Columns(cascade_card, faster_card)

        # ---------------- projection ----------------
        # The chart draws what the table's Done by column and the rollover
        # already say, so it opens only in the full view.
        self.chart_card = CollapsibleCard("Projected goal progress", expanded=True)
        self.plot = charts.new_plot(money_left=False)
        self.plot.setFixedHeight(300 + charts.AXIS_ALLOWANCE)
        self.plot.getAxis("left").setLabel("Funded (%)")
        self.chart_card.body.addWidget(self.plot)
        self._chart_key = None

        self.table.itemSelectionChanged.connect(self._table_selected)

        # One scroll for the whole page. Nothing inside it scrolls on its own,
        # so every answer is reached by the same gesture.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)
        for w in (self.start_card, self.funding_map, self.ladder, self.lower, self.chart_card):
            content_layout.addWidget(w)
        content_layout.addStretch(1)
        self.start_card.hide()
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll)

    def set_simple(self, on: bool) -> None:
        """Keep the answers, fold the picture of them.

        The chart repeats the Done by column and the rollover as lines. It stays
        one click away in the simple view and opens by default in the full one.
        """
        self._simple = bool(on)
        self.chart_card.set_expanded(not on)
        self._apply_simple_view()

    def _apply_simple_view(self) -> None:
        """Show the ladder a relative needs, and nothing hidden that changes it.

        Type and Total /mo repeat what the selected row says underneath, and the
        split method is a power setting. The split stays visible whenever it is
        not the default, and so does the Total /mo column in Custom, so no
        setting can be changed without being visible.
        """
        simple = self._simple
        custom = self.policy.currentData() == "custom"
        self.table.setColumnHidden(2, simple)
        self.table.setColumnHidden(5, simple and not custom)
        self.policy_pair.setVisible(not simple or self.policy.currentData() != "focus")

    # ---------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._refresh_inner()
        except Exception:
            _log.exception('Could not refresh Goals')
            self.funding_map.hide()
            self._clear_plan()
            self._set_error('Goals could not be refreshed. Check the monthly allowance and account data, then retry.')

    def _set_error(self, message, retry=None):
        self.error_note.setText(message)
        self.error_host.setVisible(bool(message))
        self._retry = retry or self.refresh
        self.retry_btn.setText('Retry save' if retry else 'Retry')

    def _clear_plan(self):
        self._plan = None
        self._syncing_selection = True
        try:
            self._keys = []
            self._split_boxes = {}
            self._split_amounts = {}
            self.table.setRowCount(0)
            self.target_pick.clear()
        finally:
            self._syncing_selection = False
        self.plot.clear()
        self._set_chart_key([])
        self.target_pick.setEnabled(False)
        self.by_date.setEnabled(False)
        self.plan_note.setText('Plan unavailable. Use Retry above to rebuild it.')
        self.plan_note.show()
        self.allowance_btn.hide()
        self.cascade.setText('Payment rollover is unavailable until the plan can be rebuilt.')
        self.faster.setText('Target-date comparisons are unavailable until the plan can be rebuilt.')
        self.custom_note.clear()
        self.custom_note.hide()
        self._show_payment_breakdown()
        self._sync_empty()

    def _sync_empty(self) -> None:
        """Show the start card instead of planning machinery when nothing is on the ladder.

        A failed rebuild is not an empty ladder: the plan is None then, and the
        panels stay put under the error and its Retry.
        """
        empty = self._plan is not None and not self._plan.allocations
        self.start_card.setVisible(empty)
        for panel in (self.ladder, self.lower, self.chart_card):
            panel.setVisible(not empty)
        if empty:
            self._refresh_start_card()

    def _refresh_start_card(self) -> None:
        """Say what a first goal would look like, using the person's own numbers."""
        try:
            amount = emergency_goal_amount(self.store, 3)
            has_accounts = bool(self.store.accounts())
        except Exception:
            _log.exception('Could not size a starting goal')
            amount, has_accounts = 0.0, True
        allowance = self.surplus.value()
        if amount > 0:
            note = f"From your spending, three months of essentials is about {fmt_money(amount)}."
            if allowance > 0:
                months = math.ceil(amount / allowance)
                note += (f" Setting aside about {fmt_money(allowance)} a month, that would take "
                         f"roughly {months} month{'s' if months != 1 else ''}. These are estimates "
                         "from your own history.")
        else:
            note = ("Doug works out dates from what you can set aside each month. Import "
                    "transactions so it can detect that, or enter an amount once your first goal is added.")
        self.start_note.setText(note)
        self.start_demo.setVisible(not has_accounts)

    def _waiting_for_allowance(self) -> bool:
        """True when goals have no date only because no monthly amount is set."""
        plan = self._plan
        return bool(plan and plan.surplus <= 0.005 and any(
            a.target.kind == GOAL and not a.target.complete and a.eta is None
            for a in plan.allocations))

    def _focus_allowance(self) -> None:
        self.scroll.ensureWidgetVisible(self.surplus)
        self.surplus.setFocus()
        self.surplus.selectAll()

    @staticmethod
    def _when_text(a, waiting: bool) -> str:
        if a.target.complete:
            return "already met"
        if a.eta:
            return a.eta.strftime("%b %Y")
        return "Needs a monthly amount" if waiting and a.target.kind == GOAL else "Not within 30 yrs"

    def _sync_by_limits(self) -> None:
        """This month to thirty years out, matching the plan's horizon."""
        today = QDate.currentDate()
        self.by_date.setMinimumDate(today)
        self.by_date.setMaximumDate(today.addYears(PLAN_HORIZON_MONTHS // 12))

    def _refresh_inner(self) -> None:
        if self._source_store is not self.store:
            self._source_store = self.store
            self._selected_target = None
            self._selection_initialized = False
        self._sync_by_limits()
        self._loading = True
        try:
            i = self.policy.findData(self.store.plan_policy())
            self.policy.setCurrentIndex(max(0, i))
            detected = surplus_monthly(self.store)
            stored = self.store.get_setting("plan_surplus", None)
            amount = float(stored) if stored is not None else detected
            if not math.isfinite(amount) or not 0 <= amount <= self.surplus.maximum():
                raise ValueError('Invalid monthly allowance')
            self.surplus.setValue(amount)
        finally:
            self._loading = False
        self._apply_simple_view()
        self._rerun()

    def _use_detected(self) -> None:
        try:
            with self.store.undo_group("Use the Plan or history allowance"):
                self.store.set_setting("plan_surplus", None)
        except sqlite3.Error:
            self._set_error('The automatic allowance could not be saved. Check access to the database file and retry.', self._use_detected)
            return
        self.refresh()
        self.main.refresh_header()

    def _surplus_changed(self) -> None:
        if self._loading:
            return
        try:
            with self.store.undo_group("Change the allowance"):
                self.store.set_setting("plan_surplus", self.surplus.value())
        except sqlite3.Error:
            self._set_error('The allowance was not saved. Projections still use the last saved amount. Check access to the database file and retry.', self._surplus_changed)
            return
        self._rerun()
        self.main.refresh_header()

    def _policy_changed(self) -> None:
        if self._loading:
            return
        self._apply_simple_view()
        try:
            self.store.set_plan_policy(self.policy.currentData())
        except sqlite3.Error:
            self._set_error('The split method was not saved. Projections still use the last saved method. Check access to the database file and retry.', self._policy_changed)
            return
        self._rerun()
        self.main.refresh_header()

    def _rerun(self) -> None:
        if self._loading:
            return
        errors = []
        self._syncing_selection = True
        try:
            self.funding_map.refresh(self.store)
            self.funding_map.setVisible(bool(self.funding_map.overview.goals))
        except Exception:
            _log.exception('Could not read goal funding locations')
            self.funding_map.hide()
            errors.append('Savings locations could not be loaded. Check account and goal data, then retry.')
        finally:
            self._syncing_selection = False
        try:
            self._show_surplus_calculation()
            self._plan = build_plan(self.store, policy=self.policy.currentData(),
                                    surplus=self.surplus.value())
            self._render_table()
            self._render_chart()
            self._render_plan_note()
            self._render_cascades()
            self._render_faster()
            self._sync_empty()
        except Exception:
            _log.exception('Could not build the goal plan')
            self._clear_plan()
            errors.append('The funding plan could not be rebuilt. Check the monthly allowance and account data, then retry.')
        self._set_error(' '.join(errors))

    def _select_target(self, key, *, remember=True):
        if self._syncing_selection:
            return
        self._selected_target = key if key in self._keys else None
        if remember:
            self._selection_initialized = True
        self._syncing_selection = True
        try:
            if self._selected_target is None:
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
            else:
                self.table.selectRow(self._keys.index(self._selected_target))
            target = self._plan.by_key(self._selected_target) if self._plan else None
            self.funding_map.select_goal(target.target.goal_id if target and target.target.goal_kind == 'savings' else None)
        finally:
            self._syncing_selection = False
        self._show_payment_breakdown()

    def _map_selected(self, goal_id):
        if self._syncing_selection:
            return
        key = f'goal:{goal_id}' if goal_id is not None else None
        if self._plan is None:
            self._selected_target = key
            self._selection_initialized = True
        else:
            self._select_target(key)

    def _table_selected(self):
        if not self._syncing_selection:
            self._select_target(self._selected_key())

    def _show_surplus_calculation(self) -> None:
        if self.store.get_setting("spending_plan", None) is not None:
            from ..spending_plan import calculate_plan
            plan = calculate_plan(self.store)
            leftover = plan.remaining(self.surplus.value())
            balance_note = (f"${leftover:,.2f} remains unassigned." if leftover >= 0 else
                            f"This allowance is ${-leftover:,.2f} over budget.")
            self.surplus_note.setText(
                f"Monthly Plan: ${plan.amounts['income']:,.2f} income − "
                f"${plan.expenses:,.2f} spending, reserves & investing = "
                f"${plan.available:,.2f} available. This ladder uses ${self.surplus.value():,.2f}. "
                f"{balance_note}")
            self.surplus_note.setToolTip("Regular loan payments are already budgeted, so this allowance is extra on top of them.")
            return
        agg = monthly_aggregates(self.store.transactions())
        manual = self.store.get_setting("plan_surplus", None) is not None
        prefix = "Manual amount. Detected: " if manual else "Detected: "
        if agg is None:
            self.surplus_note.setText(prefix + "no completed months of personal transactions yet. "
                "Build a budget on Plan, or enter a monthly allowance above.")
            return
        net = f"{'−' if agg.surplus < 0 else '+'}${abs(agg.surplus):,.2f}"
        note = (f"{prefix}${agg.income:,.2f} income − "
                f"${agg.spending:,.2f} spending ≈ {net}/mo, "
                f"from {agg.n_months} completed month"
                f"{'s' if agg.n_months != 1 else ''} of history.")
        if agg.surplus < 0:
            note += " The detected amount available for goals is $0 because spending exceeds income."
        if manual:
            note += " Use Plan / history clears the manual amount and keeps the allowance updated."
        self.surplus_note.setText(note)
        self.surplus_note.setToolTip(
            "Recorded loan payments are already in spending, so spare money is extra on top. "
            "The current month, transfers and reimbursable business amounts are excluded. "
            "Each recorded completed calendar month is weighted by "
            "0.5^(months older than the newest / 3), using up to 24 months. "
            "Income and spending each equal sum(weight × monthly total) / "
            "sum(weights). Spare is max(0, income − spending). "
            "Loan payments count when recorded in personal spending; "
            "this is observed surplus, not a guarantee of future free cash.")

    # ---------------------------------------------------------------
    def _render_table(self) -> None:
        self._syncing_selection = True
        self.table.blockSignals(True)
        try:
            self._fill_table()
        finally:
            self.table.blockSignals(False)
            self._syncing_selection = False
        previous = self.table
        for box in self._split_boxes.values():
            QWidget.setTabOrder(previous, box)
            previous = box
        QWidget.setTabOrder(previous, self.allowance_btn)
        QWidget.setTabOrder(self.allowance_btn, self.up)
        self._select_target(self._selected_target, remember=False)

    def _fill_table(self) -> None:
        plan = self._plan
        allocs = plan.allocations
        waiting = self._waiting_for_allowance()
        custom_mode = self.policy.currentData() == "custom"
        self.table.horizontalHeaderItem(5).setText("Share → Extra /mo" if custom_mode else "Total /mo")
        stored = self.store.plan_allocations()
        self._keys = [a.target.key for a in allocs]
        self._split_boxes = {}
        self._split_amounts = {}
        self.table.clearContents()
        self.table.setRowCount(len(allocs))
        for r, a in enumerate(allocs):
            t = a.target
            rank = QTableWidgetItem(str(r + 1))
            rank.setData(Qt.ItemDataRole.UserRole, t.key)
            name = QTableWidgetItem(t.name)
            name.setToolTip(t.name)
            kind = QTableWidgetItem("Loan payoff" if t.kind == LOAN else
                                    ("Net worth milestone" if t.goal_kind == "networth"
                                     else "Savings goal" if t.goal_kind == "savings"
                                     else "Cash milestone"))
            amt = QTableWidgetItem(fmt_money(t.amount))
            amt.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            prog = QTableWidgetItem(
                "done ✓" if t.complete
                else f"{t.fraction * 100:.0f}%  ·  {fmt_money(t.progress)}")
            per = QTableWidgetItem(
                fmt_money(a.total_monthly) if a.total_monthly > 0.005 else "—")
            per.setToolTip(self._payment_breakdown(a))
            per.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            if custom_mode and not t.complete:
                per = None      # replaced by a live editor below
            when = QTableWidgetItem(self._when_text(a, waiting))
            if a.eta is None and not t.complete:
                # Waiting on an amount is not a verdict on the goal: stay neutral.
                when.setForeground(theme.qcolor(
                    theme.MUTED if waiting and t.kind == GOAL else theme.AMBER))
            for c, item in enumerate((rank, name, kind, amt, prog, per, when)):
                if item is None:
                    continue
                if t.complete:
                    item.setForeground(theme.qcolor(theme.MUTED))
                self.table.setItem(r, c, item)
            if per is None:
                self.table.setCellWidget(r, 5, self._split_editor(t, stored))
        self._sync_custom_note()
        self._fit_table()
        if not self._selection_initialized:
            self._selected_target = self._keys[0] if self._keys else None
            self._selection_initialized = bool(self._keys)
        elif self._selected_target not in self._keys:
            self._selected_target = None

    def _fit_table(self) -> None:
        """Size the table to its rows, so there is no empty well under a short list."""
        rows = max(self.MIN_ROWS, min(self.table.rowCount(), self.MAX_ROWS))
        header = self.table.horizontalHeader()
        # Before the page is first shown the stylesheet has not applied, and the
        # header reports its unpadded height. Floor it on the font so a table
        # fitted early is never short enough to clip its last row.
        header_h = max(header.sizeHint().height(), header.fontMetrics().height() + 16)
        self.table.setFixedHeight(header_h + rows * self.ROW_HEIGHT + 2 * self.table.frameWidth())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_table()

    @staticmethod
    def _payment_breakdown(a, named: bool = True, activity_hint: bool = True) -> str:
        who = f"{a.name}: " if named else ""
        if a.target.kind == LOAN:
            return (f"{who}{fmt_money(a.target.required)}/mo existing + "
                    f"{fmt_money(a.monthly)}/mo extra = {fmt_money(a.total_monthly)}/mo total. "
                    f"Existing payment: {a.target.payment_source}.")
        return (f"{who}{fmt_money(a.monthly)}/mo planned savings."
                + (" Record contributions, purchases or released savings with Goal activity on the map above."
                   if activity_hint else "")
                if a.target.goal_kind == "savings" else
                f"{who}{fmt_money(a.monthly)}/mo toward this balance milestone.")

    def _show_payment_breakdown(self) -> None:
        a = self._plan.by_key(self._selected_key()) if self._plan else None
        overview = self.funding_map.overview
        note = (self._payment_breakdown(a, named=False,
                                        activity_hint=bool(overview and overview.accounts))
                if a else "")
        if a and a.target.goal_kind == "savings":
            goal = next((g for g in self.store.goals() if g.id == a.target.goal_id), None)
            if goal:
                note += (f" Still saved: ${goal.saved_amount:,.2f}; purchases toward goal: "
                         f"${goal.spent_amount:,.2f}. Progress includes both.")
        self.payment_note.setText(note)
        self.payment_note.setVisible(bool(note))
        self.selection_note.setStyleSheet("font-weight: 600;" if a else "")
        self.selection_note.setText(a.name if a else
            'Moving, editing and deleting are unavailable until the plan is rebuilt.' if self._plan is None else
            'Select a goal above to move, edit or delete it.' if self._keys else
            'No goals yet. Add a goal to start planning.')
        self.up.setEnabled(bool(a and self.table.currentRow() > 0))
        self.down.setEnabled(bool(a and self.table.currentRow() < len(self._keys) - 1))
        self.edit_btn.setEnabled(a is not None)
        self.delete_btn.setEnabled(bool(a and a.target.kind == GOAL))
        for button in (self.edit_btn, self.delete_btn):
            button.setToolTip(f'{button.text().rstrip("…")} for {a.name}' if a else 'Select a target in the ladder first.')
            button.setAccessibleName(f'{button.text().rstrip("…")}: {a.name}' if a else button.text())
        if a and a.target.kind == LOAN:
            self.edit_btn.setToolTip("Loan balance, rate and payment are edited on Accounts; its priority is set here.")
            self.delete_btn.setToolTip("A loan leaves the ladder when it is paid off or its account is deleted.")

    def _split_editor(self, target, stored: dict) -> QWidget:
        """A relative share beside the resulting first-month extra allocation."""
        box = _money_spin(0, 1e6)
        box.setPrefix("")
        box.setMinimumWidth(box.minimumSizeHint().width())
        box.setAccessibleName(f"Relative share for {target.name}")
        box.setValue(float(stored.get(target.key, 0.0)))
        box.setToolTip(
            "Relative shares divide the full monthly allowance. Shares of 1 and 1 "
            "split it equally; 2 and 1 give twice as much to the first target. "
            "The dollar amount beside this field is the extra allocation in the first plan month.")
        box.valueChanged.connect(self._custom_changed)
        self._split_boxes[target.key] = box
        result = QLabel()
        result.setTextFormat(Qt.TextFormat.PlainText)
        result.setFont(theme.mono_font(12))
        result.setToolTip("Extra funding in the first plan month. Existing loan payments are separate.")
        self._split_amounts[target.key] = result
        a = self._plan.by_key(target.key)
        result.setText(f"→ ${a.monthly:,.2f}" if a else "→ $0.00")
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 2, 0)
        layout.setSpacing(4)
        layout.addWidget(box, 1)
        layout.addWidget(result)
        holder.setMinimumWidth(layout.minimumSize().width())
        return holder

    def _custom_changed(self) -> None:
        if self._loading:
            return
        try:
            self.store.set_plan_allocations(
                {k: b.value() for k, b in self._split_boxes.items()})
        except sqlite3.Error:
            self._set_error('Custom shares were not saved. Projections still use the last saved shares. Check access to the database file and retry.', self._custom_changed)
            return
        try:
            self._update_custom_plan()
        except Exception:
            _log.exception('Could not update custom goal projections')
            self._clear_plan()
            self._set_error('The custom split was saved, but its projections could not be rebuilt. Check account and goal data, then retry.')

    def _update_custom_plan(self):
        # Rebuild the plan, but not the editors: replacing the widget the user
        # is typing into would steal focus on every keystroke.
        self._plan = build_plan(self.store, policy="custom",
                                surplus=self.surplus.value())
        self._render_chart()
        self._render_cascades()
        self._render_faster()
        self._sync_custom_note()
        waiting = self._waiting_for_allowance()
        for r, key in enumerate(self._keys):
            a = self._plan.by_key(key)
            if a is None:
                continue
            item = self.table.item(r, 5) if key not in self._split_boxes else None
            if item is not None:
                item.setText(fmt_money(a.total_monthly) if a.total_monthly > 0.005 else "—")
                item.setToolTip(self._payment_breakdown(a))
            if key in self._split_amounts:
                self._split_amounts[key].setText(f"→ ${a.monthly:,.2f}")
                holder = self.table.cellWidget(r, 5)
                holder.setMinimumWidth(holder.layout().minimumSize().width())
            cell = self.table.item(r, 6)
            cell.setText(self._when_text(a, waiting))
            cell.setForeground(theme.qcolor(
                theme.MUTED if a.target.complete else
                theme.TEXT if a.eta else
                theme.MUTED if waiting and a.target.kind == GOAL else theme.AMBER))
        self.table.resizeColumnToContents(5)
        self._render_plan_note()
        self._show_payment_breakdown()
        self._set_error('')
        self.main.refresh_header()

    def _sync_custom_note(self) -> None:
        if self.policy.currentData() != "custom":
            self.custom_note.clear()
            self.custom_note.hide()
            return
        self.custom_note.show()
        total = sum(b.value() for b in self._split_boxes.values())
        if not self._split_boxes:
            self.custom_note.setText("No unfinished goals to divide the allowance between.")
            return
        explanation = (f"Relative shares divide the full ${self.surplus.value():,.2f} monthly allowance. "
            "Shares of 1 and 1 split it equally. Arrows show extra dollars in the first plan month; "
            "existing loan payments are separate.")
        if total <= 0:
            self.custom_note.setText(
                explanation + " All shares are zero, so the first unfinished goal receives the allowance.")
            return
        self.custom_note.setText(explanation)

    def _name_for(self, key: str) -> str:
        a = self._plan.by_key(key) if self._plan else None
        return a.target.name if a else key

    def _render_chart(self) -> None:
        plan = self._plan
        p = self.plot
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        # The data envelope changes with every plan edit, so the chart re-frames
        # instead of keeping a view that may now reach past the new limits.
        p.forget_view()
        if not plan.dates or not plan.allocations:
            self._set_chart_key([])
            return
        # Trim the tail: once everything is done the lines are flat, and 30
        # years of flat line makes the interesting part unreadable.
        n = len(plan.dates)
        last = n
        for a in plan.allocations:
            if a.months is not None:
                last = max(last if last < n else 0, a.months + 2)
        last = min(n, max(6, last))
        x = charts.to_ts(plan.dates[:last])
        series, key = [], []
        for a in plan.allocations:
            ys = np.array(plan.progress[a.target.key][:last]) * 100.0
            # Debt is coral and dashed; each goal keeps the identity colour it has on the map.
            colour = theme.RED if a.target.kind == LOAN else goal_color(a.target.goal_id)
            charts.line(p, x, ys, colour, 2.0, escape(a.target.name),
                        dashed=(a.target.kind == LOAN))
            series.append(ys)
            key.append((a.target.name, colour, "dash" if a.target.kind == LOAN else "line"))
        self._set_chart_key(key)
        floor = charts.soft_floor(p, 100.0, "100% · funded", theme.ACCENT, slot="zero")
        floor.label.setPosition(0.6)
        # Bounded on every side: 0 to 100% vertically, and the time axis ends just
        # past the last point, with no extra room to the right to get lost in.
        charts.lock_view(p, float(x[0]), float(x[-1]), series,
                         include_y=[0.0, 100.0], slack_right=False,
                         y_limits=(-_Y_HEADROOM, 100.0 + _Y_HEADROOM),
                         min_y_span=_MIN_Y_SPAN)

    def _set_chart_key(self, entries: list[tuple]) -> None:
        """A key under the plot, so no line is ever drawn behind its own label."""
        if self._chart_key is not None:
            self.chart_card.body.removeWidget(self._chart_key)
            self._chart_key.deleteLater()
            self._chart_key = None
        if not entries:
            return
        key = ChartKey(entries)
        # Names are the user's own text; never let one render as markup.
        for label in key.findChildren(QLabel):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.chart_card.body.addWidget(key)
        self._chart_key = key

    def _render_plan_note(self) -> None:
        plan = self._plan
        if plan is None:
            return
        bits = []
        done = plan.all_done
        waiting = self._waiting_for_allowance()
        if waiting:
            # The reason there are no dates is one missing number, so say that,
            # and drop the two engine warnings that restate it as a 30-year failure.
            bits.append("Doug needs a monthly amount to work out dates.")
        elif plan.allocations and done:
            bits.append(f"Estimate: every goal is funded by {done.strftime('%b %Y')} "
                        f"at {fmt_money(plan.surplus)}/mo.")
        skipped = ("No spare income is available", "Some goals are not reached within")
        bits += [f"⚠ {w}" for w in plan.warnings if not (waiting and w.startswith(skipped))]
        self.plan_note.setText("\n".join(bits))
        self.plan_note.setVisible(bool(bits))
        self.allowance_btn.setVisible(waiting)

    def _render_cascades(self) -> None:
        plan = self._plan
        if not plan.allocations:
            self.cascade.setText("Add a goal to see how its funding passes to the next goal when it is complete.")
            return
        if all(a.target.complete for a in plan.allocations):
            self.cascade.setText("All goals are already complete. There is no funding to roll over.")
            return
        if self._waiting_for_allowance() and not plan.cascades:
            self.cascade.setText(
                "Once there is a monthly amount, this shows when each goal finishes "
                "and where its payment goes next.")
            return
        if not plan.cascades:
            self.cascade.setText(
                "Nothing completes inside the plan horizon yet, so nothing "
                "frees up. This panel fills in as loans retire and goals fund.")
            return
        rows = []
        for c in plan.cascades[:12]:
            colour = theme.AMBER if c.kind == LOAN else theme.ACCENT
            what = ("paid off" if c.kind == LOAN else "funded")
            if c.kind == LOAN and c.destination and c.amount > 0.005:
                tail = (f" → <b>{fmt_money(c.amount)}/mo</b> rolls into "
                        f"{escape(c.destination)}")
            elif c.destination:
                tail = f" → next up: {escape(c.destination)}"
            elif c.kind == LOAN and c.amount > 0.005 and not any(
                    a.target.kind == GOAL for a in plan.allocations):
                tail = (f" → <b>{fmt_money(c.amount)}/mo</b> is free, and no goal is waiting "
                        "for it. Add one above to give it a job.")
            else:
                tail = " → ladder complete; that money is yours to redirect"
            rows.append(
                f"<div style='margin-bottom:5px'>"
                f"<span style='color:{theme.MUTED}'>"
                f"{c.when.strftime('%b %Y')}</span>&nbsp; "
                f"<span style='color:{colour}'>{escape(c.source)} {what}</span>"
                f"{tail}</div>")
        self.cascade.setText("".join(rows))

    def _sync_target_pick(self) -> None:
        """Offer every unfinished rung, keeping the current choice if it lives."""
        live = [a for a in self._plan.allocations if not a.target.complete]
        prev = self.target_pick.currentData()
        self.target_pick.blockSignals(True)
        self.target_pick.clear()
        for i, a in enumerate(live):
            self.target_pick.addItem(
                f"{a.target.name}" + ("  (next up)" if i == 0 else ""),
                a.target.key)
        j = self.target_pick.findData(prev)
        self.target_pick.setCurrentIndex(j if j >= 0 else 0)
        self.target_pick.blockSignals(False)
        usable = bool(live) and not self._waiting_for_allowance()
        self.target_pick.setEnabled(usable)
        self.by_date.setEnabled(usable)

    def _render_faster(self) -> None:
        plan = self._plan
        if plan is None:
            return
        self._sync_target_pick()
        live = [a for a in plan.allocations if not a.target.complete]
        if not live:
            self.faster.setText("Every goal on the ladder is already met." if plan.allocations else
                "No goals or loans yet. Add a goal to compare monthly funding and target dates.")
            return
        if self._waiting_for_allowance():
            self.faster.setText(
                "Once there is a monthly amount, pick a goal and a date here to see what "
                "it would take, and how much sooner extra saving gets you there.")
            return
        key = self.target_pick.currentData()
        focus = next((a for a in live if a.target.key == key), live[0])
        try:
            ups = speedups(self.store, plan, target_key=focus.target.key)
        except Exception as e:
            self.faster.setText(f"Could not cost the options: {escape(str(e))}")
            return
        # Rank among the *live* rungs, matching goals.deadline_plan. Counting
        # completed targets too would tell someone their second remaining goal
        # is "priority 3", which is a different and wrong claim.
        rank = live.index(focus) + 1
        where = ("Next up" if focus is live[0]
                 else f"Priority {rank} on the ladder")
        head = (f"<div>{where}: <b>{escape(focus.target.name)}</b>"
                + (f", on track for {focus.eta.strftime('%b %Y')}"
                   if focus.eta else ", not reached at this rate") + ".</div>")
        if focus is not live[0]:
            head += (f"<div style='color:{theme.MUTED}'>"
                     f"Goals above it keep their priority.</div>")
        self.faster.setText(head + self._deadline_html(focus) + self._speedup_html(ups))

    def _speedup_html(self, ups) -> str:
        """Extra monthly saving, one row per distinct result.

        +$50 and +$100 that both land on the same month are one finding, not two.
        """
        rows, seen = [], set()
        for s in ups:
            if s.months_saved <= 0 or s.new_eta in seen:
                continue
            seen.add(s.new_eta)
            when = s.new_eta.strftime("%b %Y") if s.new_eta else "—"
            rows.append(
                f"<div style='margin-bottom:3px'>"
                f"<span style='color:{theme.ACCENT}'>"
                f"+{fmt_money(s.extra)}/mo</span> → {when} "
                f"<span style='color:{theme.MUTED}'>"
                f"({s.months_saved} month{'s' if s.months_saved != 1 else ''} "
                f"sooner)</span></div>")
        if not rows:
            return (f"<div style='color:{theme.MUTED};margin-top:8px'>Extra saving doesn't move this "
                    "one — it is already funded from the first month.</div>")
        return (f"<div style='color:{theme.MUTED};margin:8px 0 4px'>"
                "Or put extra toward it each month:</div>" + "".join(rows))

    def _deadline_html(self, focus) -> str:
        """What hitting the user's chosen date would actually cost, by name."""
        qd = self.by_date.date()
        try:
            dl = deadline_plan(self.store, self._plan, focus.target.key,
                               date(qd.year(), qd.month(), 1))
        except Exception as e:
            return f"<div style='color:{theme.AMBER}'>Couldn't cost that date: {escape(str(e))}</div>"

        head = (f"<div style='margin:8px 0 4px'>To be done by "
                f"<b>{dl.by.strftime('%b %Y')}</b> takes "
                f"<span style='color:{theme.ACCENT}'>{fmt_money(dl.needed_monthly)}/mo</span> "
                f"on its own, counting this month.</div>")
        if dl.already_met or (dl.feasible and not dl.cuts):
            return head + (f"<div style='color:{theme.ACCENT}'>{escape(dl.note)}</div>")
        if not dl.feasible:
            tail = (f"<div style='color:{theme.BLUE};margin-top:6px'>"
                    f"{escape(dl.advice)}</div>" if dl.advice else "")
            return head + (
                f"<div style='color:{theme.AMBER};margin-bottom:4px'>"
                f"Not reachable by then.</div>"
                f"<div style='color:{theme.MUTED}'>{escape(dl.note)}</div>" + tail)

        rows = [f"<div style='margin-bottom:4px'>Find "
                f"<b>{fmt_money(dl.shortfall)}/mo</b> more, on top of what higher "
                f"priorities already take. The smallest set of cuts to wants "
                f"that covers it:</div>"]
        for c in dl.cuts:
            tag = ("recurring" if c.recurring else
                   "uncategorized — check this is really optional"
                   if c.bucket == "unknown" else "spending")
            colour = theme.AMBER if c.bucket == "unknown" else theme.TEXT
            rows.append(
                f"<div style='margin-bottom:3px'>· <span style='color:{colour}'>"
                f"{escape(c.name)}</span> — {fmt_money(c.monthly)}/mo "
                f"<span style='color:{theme.MUTED}'>({tag})</span></div>")
        rows.append(
            f"<div style='color:{theme.MUTED};margin-top:6px'>Only wants are "
            f"listed; essentials and loan payments are never cut. If the list "
            f"falls short of the number, the date is out of reach.</div>")
        if dl.advice:
            rows.append(f"<div style='color:{theme.BLUE};margin-top:6px'>"
                        f"{escape(dl.advice)}</div>")
        return head + "".join(rows)

    # ---------------------------------------------------------------
    def _selected_key(self) -> str | None:
        r = self.table.currentRow()
        if r < 0 or r >= len(self._keys) or not self.table.selectionModel().hasSelection():
            return None
        return self._keys[r]

    def _record_for_goal(self, goal_id: int, locate: bool) -> None:
        try:
            goal = next((g for g in self.store.goals() if g.id == goal_id), None)
            dialog = GoalFundingDialog(self.store, goal_id, self) if goal and goal.kind == 'savings' else None
        except sqlite3.Error:
            self._set_error('Goal activity could not be opened. Check access to the database file and retry.',
                            lambda: self._record_for_goal(goal_id, locate))
            return
        if goal is None or goal.kind != 'savings':
            self.refresh()
            return
        if locate:
            from ..funding import goal_buckets
            dialog.action.setCurrentIndex(dialog.action.findData('assign'))
            dialog.amount.setValue(max(0.0, goal_buckets(self.store, goal_id).get(None, 0.0)))
        dialog.exec()
        self._rerun()
        self.main.refresh_header()

    def _move(self, delta: int) -> None:
        key = self._selected_key()
        if key is None:
            QMessageBox.information(self, "Nothing selected",
                                    "Pick a row to move first.")
            return
        keys = list(self._keys)
        i = keys.index(key)
        j = i + delta
        if not 0 <= j < len(keys):
            return
        keys[i], keys[j] = keys[j], keys[i]
        try:
            self.store.set_plan_order(keys)
        except sqlite3.Error:
            self._set_error('Priority could not be saved. Check access to the database file, retry loading, then move the target again.')
            return
        self._rerun()
        self.main.refresh_header()
        self.table.selectRow(j)

    def _add(self) -> None:
        dlg = GoalDialog(self)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            name, amount, kind, saved = dlg.values()
            try:
                with self.store.undo_group(f"Add goal “{name}”"):
                    gid = self.store.add_goal(Goal(name, amount, kind, saved_amount=saved),
                                              plan_order=self._keys)
            except (ValueError, sqlite3.Error) as exc:
                dlg.error.setText(str(exc) if isinstance(exc, ValueError) else
                    'Goal not saved. Check access to the database file, then try Add goal again.')
                continue
            self._selected_target = f'goal:{gid}'
            self._selection_initialized = True
            self._rerun()
            self.main.refresh_header()
            self._announce_added(gid, name)
            return

    def _announce_added(self, gid: int, name: str) -> None:
        """Say when the new goal lands, so adding one answers the question that prompted it."""
        a = self._plan.by_key(f'goal:{gid}') if self._plan else None
        if a is None:
            return
        if a.target.complete:
            msg = f"Added “{name}”. It is already fully funded."
        elif a.eta:
            msg = (f"Added “{name}”. At {fmt_money(self._plan.surplus)}/mo it is done by "
                   f"{a.eta.strftime('%b %Y')}.")
        elif self._waiting_for_allowance():
            msg = f"Added “{name}”. Enter a monthly amount to see when it will be done."
        else:
            msg = f"Added “{name}”. At this rate it is not reached within 30 years."
        self.main.status(msg)

    def _add_emergency(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Emergency fund goal")
        dlg.setMinimumWidth(GoalDialog.MIN_WIDTH)
        form = QFormLayout(dlg)
        months = IntSpinBox()
        months.setRange(1, 12)
        months.setValue(3)
        months.setSuffix(" months")
        preview = hint("")
        form.addRow("Cover", months)
        form.addRow("", preview)
        form.addRow(hint("This creates a savings goal. Use Edit afterward to enter "
                         "money already set aside for emergencies."))

        def sync() -> None:
            amt = emergency_goal_amount(self.store, months.value())
            preview.setText(
                f"{fmt_money(amt)} — {months.value()} × your own "
                f"monthly spending on needs." if amt > 0 else
                "Not enough categorized spending yet to size this. Import more "
                "history, or add it as an ordinary goal with your own figure.")
        months.valueChanged.connect(sync)
        sync()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText('Add emergency fund')
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        amt = emergency_goal_amount(self.store, months.value())
        if amt <= 0:
            QMessageBox.information(
                self, "Can't size it yet",
                "Spending on needs can't be established from the ledger yet.")
            return
        try:
            with self.store.undo_group("Add emergency fund goal"):
                gid = self.store.add_goal(
                    Goal(f"Emergency fund ({months.value()} mo)", amt, "savings"),
                    plan_order=self._keys, first=True)
        except sqlite3.Error:
            self._set_error('The emergency fund was not saved. Check access to the database file, then add it again.')
            return
        self._selected_target = f'goal:{gid}'
        self._selection_initialized = True
        self._rerun()
        self.main.refresh_header()
        self._announce_added(gid, f"Emergency fund ({months.value()} mo)")

    def _edit(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        if key.startswith("loan:"):
            QMessageBox.information(
                self, "Edit this on the Accounts page",
                "Loan balance, APR and payment come from the account itself — "
                "edit it on the Accounts page. Its priority is set here.")
            return
        gid = int(key.split(":")[1])
        self._edit_goal(gid)

    def _edit_goal(self, gid: int) -> None:
        try:
            g = next((x for x in self.store.goals() if x.id == gid), None)
            activity = self.store.goal_activity(gid) if g else []
        except sqlite3.Error:
            self._set_error('The goal could not be opened for editing. Check access to the database file and retry.',
                            lambda: self._edit_goal(gid))
            return
        if g is None:
            return
        dlg = GoalDialog(self, goal=g)
        if activity:
            dlg.saved.setEnabled(False)
            dlg.kind.setEnabled(False)
            dlg.explanation.setText("Savings are tracked in Goal activity. Record contributions, "
                                    "purchases and releases there to preserve the funding history.")
        while dlg.exec() == QDialog.DialogCode.Accepted:
            name, amount, kind, saved = dlg.values()
            try:
                self.store.update_goal(gid, name=name, amount=amount, kind=kind, saved_amount=saved)
            except (ValueError, sqlite3.Error) as exc:
                dlg.error.setText(str(exc) if isinstance(exc, ValueError) else
                    'Changes not saved. Check access to the database file, then try Save changes again.')
                continue
            self._rerun()
            self.main.refresh_header()
            return

    def _delete(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        if key.startswith("loan:"):
            QMessageBox.information(
                self, "Loans live on the Accounts page",
                "A loan leaves the ladder when it is paid off or the account "
                "is deleted.")
            return
        gid = int(key.split(":")[1])
        try:
            goal = next((g for g in self.store.goals() if g.id == gid), None)
        except sqlite3.Error:
            self._set_error('The goal could not be loaded for deletion. Check access to the database file and retry loading.')
            return
        if goal is None:
            self.refresh()
            return
        question = QMessageBox(self)
        question.setWindowTitle('Delete goal')
        question.setTextFormat(Qt.TextFormat.PlainText)
        question.setText(f'Delete “{goal.name}”?')
        question.setInformativeText('This deletes the goal and its activity history. Use Undo to restore them.')
        remove = question.addButton('Delete goal', QMessageBox.ButtonRole.DestructiveRole)
        # QMessageBox styles buttons as they are added; polish again so the name applies.
        remove.setObjectName('danger')
        remove.style().unpolish(remove)
        remove.style().polish(remove)
        keep = question.addButton('Keep goal', QMessageBox.ButtonRole.RejectRole)
        question.setDefaultButton(keep)
        question.setEscapeButton(keep)
        question.exec()
        if question.clickedButton() is not remove:
            return
        try:
            self.store.delete_goal(gid)
        except sqlite3.Error:
            self._set_error('The goal could not be deleted. Check access to the database file, retry loading, then delete it again.')
            return
        self._rerun()
        self.main.refresh_header()
