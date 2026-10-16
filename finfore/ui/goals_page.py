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

import numpy as np
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFrame,
                             QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..goals import (GOAL, LOAN, POLICIES, build_plan, deadline_plan,
                     emergency_goal_amount, speedups, surplus_monthly, targets)
from ..models import Goal
from ..insights import monthly_aggregates
from ..store import Store
from ..util import fmt_money
from . import charts, theme
from .account_dialog import _money_spin
from .widgets import Card, FlowLayout, IntSpinBox, hsplit, hint, vsplit
from .goal_funding import GoalFundingDialog

_SERIES_COLORS = [theme.ACCENT, theme.BLUE, theme.AMBER, theme.PURPLE,
                  theme.RED, "#7FD4C1", "#C7D0DB", "#E08CB4"]


class GoalDialog(QDialog):
    """Add or edit one savings goal."""

    def __init__(self, parent=None, goal: Goal | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit goal" if goal else "Add goal")
        self.setMinimumWidth(360)
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
        self.explanation.setMinimumHeight(60)
        self.error = hint("")
        self.error.setStyleSheet(f"color: {theme.RED};")
        form.addRow("Name", self.name)
        form.addRow("Target", self.amount)
        form.addRow("Track progress by", self.kind)
        form.addRow(self.saved_label, self.saved)
        form.addRow(self.explanation)
        form.addRow(self.error)
        self.kind.currentIndexChanged.connect(self._sync_kind)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
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
            "Enter only money already set aside for this goal. Use Goal activity "
            "to record later contributions, purchases, or money released from a reserve."
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
            self.error.setText("Enter a target greater than $0.")
            self.amount.setFocus()
            return
        if self.kind.currentData() == "savings" and self.saved.value() > self.amount.value():
            self.error.setText("Already saved exceeds the target. Raise the target or correct savings.")
            self.saved.setFocus()
            return
        if (self.kind.currentData() == "savings" and self._goal and
                self.saved.value() + self._goal.spent_amount > self.amount.value() + .005):
            self.error.setText("Target must cover savings plus purchases already recorded.")
            return
        self.accept()

    def values(self) -> tuple[str, float, str, float]:
        return (self.name.text().strip(), self.amount.value(),
                self.kind.currentData(), self.saved.value())


class GoalsPage(QWidget):
    COLS = ["#", "Target", "Kind", "Amount", "Progress", "Total /mo", "Done by"]

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._plan = None
        self._keys: list[str] = []
        self._split_boxes: dict = {}
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(0)

        # ---------------- ladder ----------------
        ladder = Card("Priority ladder")
        form = QFormLayout()
        income_row = QHBoxLayout()
        self.surplus = _money_spin(0, 1e6)
        self.surplus.setToolTip(
            "What is left over each month after everything you already spend, "
            "including loan minimums. Detected from your ledger — override it "
            "if you know better.")
        self.surplus.valueChanged.connect(self._surplus_changed)
        income_row.addWidget(self.surplus, 1)
        detect = QPushButton("Use detected")
        detect.setObjectName("small")
        detect.clicked.connect(self._use_detected)
        income_row.addWidget(detect)
        form.addRow("Spare /mo", income_row)
        self.policy = QComboBox()
        for key, label in POLICIES.items():
            self.policy.addItem(label, key)
        self.policy.currentIndexChanged.connect(self._policy_changed)
        form.addRow("Split", self.policy)
        ladder.body.addLayout(form)
        self.surplus_note = hint("")
        ladder.body.addWidget(self.surplus_note)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (0, 2, 3, 4, 5, 6):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self._edit())
        ladder.body.addWidget(self.table, 1)
        self.payment_note = hint("")
        self.payment_note.setTextFormat(Qt.TextFormat.PlainText)
        ladder.body.addWidget(self.payment_note)
        self.table.itemSelectionChanged.connect(self._show_payment_breakdown)

        actions_host = QWidget()
        acts = FlowLayout(actions_host, spacing=6)
        self.up = QPushButton("▲ Higher")
        self.up.setObjectName("small")
        self.up.setToolTip("Move this target up the priority ladder")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down = QPushButton("▼ Lower")
        self.down.setObjectName("small")
        self.down.clicked.connect(lambda: self._move(1))
        add = QPushButton("Add goal…")
        add.setObjectName("primary")
        add.clicked.connect(self._add)
        self.ef_btn = QPushButton("Add emergency fund…")
        self.ef_btn.setToolTip("Sized from your own essential spending")
        self.ef_btn.clicked.connect(self._add_emergency)
        edit = QPushButton("Edit…")
        edit.clicked.connect(self._edit)
        self.funding_btn = QPushButton("Goal activity…")
        self.funding_btn.clicked.connect(self._funding)
        plan_btn = QPushButton("Monthly spending plan")
        plan_btn.clicked.connect(lambda: self.main.show_page("Plan"))
        rm = QPushButton("Delete")
        rm.setObjectName("danger")
        rm.clicked.connect(self._delete)
        for b in (self.up, self.down, add, self.ef_btn, self.funding_btn, plan_btn, edit, rm):
            acts.addWidget(b)
        action_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        action_policy.setHeightForWidth(True)
        actions_host.setSizePolicy(action_policy)
        ladder.body.addWidget(actions_host)

        self.custom_note = hint("")
        ladder.body.addWidget(self.custom_note)

        # ---------------- projection ----------------
        chart_card = Card("Projected goal progress")
        self.plot = charts.new_plot(money_left=False, legend=True)
        self.plot.setMinimumHeight(170)
        self.plot.getAxis("left").setLabel("funded")
        chart_card.body.addWidget(self.plot)
        self.plan_note = hint("")
        self.plan_note.setWordWrap(True)
        chart_card.body.addWidget(self.plan_note)

        cascade_card = Card("Payment rollover")
        self.cascade = QLabel("")
        self.cascade.setWordWrap(True)
        self.cascade.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cascade.setAlignment(Qt.AlignmentFlag.AlignTop)
        cascade_scroll = QScrollArea()
        cascade_scroll.setWidgetResizable(True)
        cascade_scroll.setWidget(self.cascade)
        cascade_card.body.addWidget(cascade_scroll, 1)

        faster_card = Card("Getting there sooner")
        drow = QFormLayout()
        # Any rung, not just the top one. "What would it take to have the
        # computer by July, while still clearing the card first?" is the
        # question people actually have, and it is about a goal partway down
        # the ladder by definition.
        self.target_pick = QComboBox()
        self.target_pick.setMinimumWidth(150)
        self.target_pick.setToolTip(
            "Which target to cost. Anything above it on the ladder keeps its "
            "priority, so the answer accounts for what you are already "
            "committed to.")
        self.target_pick.currentIndexChanged.connect(
            lambda _i: self._render_faster())
        drow.addRow("Target", self.target_pick)
        self.by_date = QDateEdit()
        self.by_date.setCalendarPopup(True)
        self.by_date.setDisplayFormat("MMM yyyy")
        self.by_date.setDate(QDate.currentDate().addMonths(12))
        self.by_date.setToolTip(
            "Pick a date and Doug works out what would have to give — by name.")
        self.by_date.dateChanged.connect(lambda _d: self._render_faster())
        drow.addRow("By", self.by_date)
        faster_card.body.addLayout(drow)
        self.faster = QLabel("")
        self.faster.setWordWrap(True)
        self.faster.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.faster.setAlignment(Qt.AlignmentFlag.AlignTop)
        fscroll = QScrollArea()
        fscroll.setWidgetResizable(True)
        fscroll.setWidget(self.faster)
        faster_card.body.addWidget(fscroll, 1)

        details = vsplit(cascade_card, faster_card, sizes=[160, 260])
        lower = hsplit(chart_card, details, sizes=[500, 500])
        outer = vsplit(ladder, lower, sizes=[420, 430])
        for w in (ladder, chart_card, cascade_card, faster_card):
            w.setMinimumWidth(220)
            w.setMinimumHeight(110)
            w.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Ignored)
        # Preserve usable controls at the minimum window height. Shrinking a
        # four-panel page indefinitely was reducing buttons to a single letter
        # and date fields to a few pixels; scroll the page once it cannot fit.
        # Give the table the full page width: target names, payments and dates
        # must remain readable together, including at the minimum window size.
        outer.setMinimumHeight(900)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(outer)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll)

    # ---------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._refresh_inner()
        except Exception as e:
            self.plan_note.setText(f"Could not build a plan: {e}")

    def _refresh_inner(self) -> None:
        self._loading = True
        try:
            i = self.policy.findData(self.store.plan_policy())
            self.policy.setCurrentIndex(max(0, i))
            detected = surplus_monthly(self.store)
            stored = self.store.get_setting("plan_surplus", None)
            self.surplus.setValue(float(stored) if stored is not None else detected)
        finally:
            self._loading = False
        self._rerun()

    def _use_detected(self) -> None:
        self.store.set_setting("plan_surplus", None)
        self._refresh_inner()

    def _surplus_changed(self) -> None:
        if self._loading:
            return
        self.store.set_setting("plan_surplus", self.surplus.value())
        self._rerun()

    def _policy_changed(self) -> None:
        if self._loading:
            return
        self.store.set_plan_policy(self.policy.currentData())
        self._rerun()

    def _rerun(self) -> None:
        if self._loading:
            return
        self._show_surplus_calculation()
        self._plan = build_plan(self.store, policy=self.policy.currentData(),
                                surplus=self.surplus.value())
        self._render_table()
        self._render_chart()
        self._render_cascades()
        self._render_faster()

    def _show_surplus_calculation(self) -> None:
        if self.store.get_setting("spending_plan", None) is not None:
            from ..spending_plan import calculate_plan
            plan = calculate_plan(self.store)
            leftover = plan.remaining(self.surplus.value())
            self.surplus_note.setText(
                f"Monthly Plan: ${plan.amounts['income']:,.2f} income − "
                f"${plan.expenses:,.2f} spending, reserves & investing = "
                f"${plan.available:,.2f} available. This ladder uses ${self.surplus.value():,.2f}; "
                f"${leftover:,.2f} remains unassigned. Regular loan payments are already budgeted. "
                "Use Monthly spending plan to inspect or change the budget.")
            return
        agg = monthly_aggregates(self.store.transactions())
        manual = self.store.get_setting("plan_surplus", None) is not None
        prefix = "Manual amount. Detected: " if manual else "Detected: "
        if agg is None:
            self.surplus_note.setText(prefix + "no completed months of personal transactions yet.")
            return
        net = f"{'−' if agg.surplus < 0 else '+'}${abs(agg.surplus):,.2f}"
        note = (f"{prefix}${agg.income:,.2f} income − "
                f"${agg.spending:,.2f} spending ≈ {net}/mo. "
                f"Weighted across {agg.n_months} completed month"
                f"{'s' if agg.n_months != 1 else ''}; recent months count more. "
                "The current month, transfers and reimbursable business amounts are excluded.")
        note += " Recorded loan payments are already in spending; spare money is extra on top."
        if agg.surplus < 0:
            note += " The detected amount available for goals is $0 because spending exceeds income."
        if manual:
            note += " Use detected keeps this amount updated after imports."
        self.surplus_note.setText(note)
        self.surplus_note.setToolTip(
            "Each recorded completed calendar month is weighted by "
            "0.5^(months older than the newest / 3), using up to 24 months. "
            "Income and spending each equal sum(weight × monthly total) / "
            "sum(weights). Spare is max(0, income − spending). "
            "Loan payments count when recorded in personal spending; "
            "this is observed surplus, not a guarantee of future free cash.")

    # ---------------------------------------------------------------
    def _render_table(self) -> None:
        plan = self._plan
        allocs = plan.allocations
        custom_mode = self.policy.currentData() == "custom"
        selected = self._selected_key()
        self.table.horizontalHeaderItem(5).setText("Extra /mo" if custom_mode else "Total /mo")
        stored = self.store.plan_allocations()
        self._keys = [a.target.key for a in allocs]
        self._split_boxes = {}
        self.table.clearContents()
        self.table.setRowCount(len(allocs))
        for r, a in enumerate(allocs):
            t = a.target
            rank = QTableWidgetItem(str(r + 1))
            rank.setData(Qt.ItemDataRole.UserRole, t.key)
            name = QTableWidgetItem(t.name)
            name.setToolTip(t.name)
            kind = QTableWidgetItem("Loan" if t.kind == LOAN else
                                    ("Goal · net worth" if t.goal_kind == "networth"
                                     else "Savings" if t.goal_kind == "savings"
                                     else "Goal · cash"))
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
            when = QTableWidgetItem(
                "already met" if t.complete else
                (a.eta.strftime("%b %Y") if a.eta else "beyond 30 yrs"))
            if a.eta is None and not t.complete:
                when.setForeground(theme.qcolor(theme.RED))
            for c, item in enumerate((rank, name, kind, amt, prog, per, when)):
                if item is None:
                    continue
                if t.complete:
                    item.setForeground(theme.qcolor(theme.MUTED))
                self.table.setItem(r, c, item)
            if per is None:
                self.table.setCellWidget(r, 5, self._split_editor(t, stored))
        self._sync_custom_note()
        if self._keys:
            self.table.selectRow(self._keys.index(selected) if selected in self._keys else 0)
        self._show_payment_breakdown()

    @staticmethod
    def _payment_breakdown(a) -> str:
        if a.target.kind == LOAN:
            return (f"{a.name}: {fmt_money(a.target.required)}/mo existing + "
                    f"{fmt_money(a.monthly)}/mo extra = {fmt_money(a.total_monthly)}/mo total. "
                    f"Existing payment: {a.target.payment_source}.")
        return (f"{a.name}: {fmt_money(a.monthly)}/mo planned savings. "
                "Record contributions, purchases or released savings using Goal activity."
                if a.target.goal_kind == "savings" else
                f"{a.name}: {fmt_money(a.monthly)}/mo toward this balance milestone.")

    def _show_payment_breakdown(self) -> None:
        a = self._plan.by_key(self._selected_key()) if self._plan else None
        note = self._payment_breakdown(a) if a else ""
        if a and a.target.goal_kind == "savings":
            goal = next((g for g in self.store.goals() if g.id == a.target.goal_id), None)
            if goal:
                note += (f" Still saved: ${goal.saved_amount:,.2f}; purchases toward goal: "
                         f"${goal.spent_amount:,.2f}. Progress includes both.")
        self.payment_note.setText(note)
        if hasattr(self, "funding_btn"):
            self.funding_btn.setEnabled(bool(a and a.target.goal_kind == "savings"))

    def _split_editor(self, target, stored: dict) -> QWidget:
        """A live $/mo box for one rung, shown only under the custom policy."""
        box = _money_spin(0, 1e6)
        box.setValue(float(stored.get(target.key, 0.0)))
        box.setToolTip(
            "Your share for this target. The amounts are read as a ratio, not "
            "a cap — whatever you enter is scaled to the spare income above, "
            "so nothing is left unallocated.")
        box.valueChanged.connect(self._custom_changed)
        self._split_boxes[target.key] = box
        return box

    def _custom_changed(self) -> None:
        if self._loading:
            return
        self.store.set_plan_allocations(
            {k: b.value() for k, b in self._split_boxes.items()})
        # Rebuild the plan, but not the editors: replacing the widget the user
        # is typing into would steal focus on every keystroke.
        self._plan = build_plan(self.store, policy="custom",
                                surplus=self.surplus.value())
        self._render_chart()
        self._render_cascades()
        self._render_faster()
        self._sync_custom_note()
        for r, key in enumerate(self._keys):
            a = self._plan.by_key(key)
            if a is None:
                continue
            item = self.table.item(r, 5) if key not in self._split_boxes else None
            if item is not None:
                item.setText(fmt_money(a.total_monthly) if a.total_monthly > 0.005 else "—")
                item.setToolTip(self._payment_breakdown(a))
            self.table.item(r, 6).setText(
                "already met" if a.target.complete else
                a.eta.strftime("%b %Y") if a.eta else "beyond 30 yrs")
        self._show_payment_breakdown()

    def _sync_custom_note(self) -> None:
        if self.policy.currentData() != "custom":
            self.custom_note.setText(POLICIES.get(self.policy.currentData(), ""))
            return
        total = sum(b.value() for b in self._split_boxes.values())
        if total <= 0:
            self.custom_note.setText(
                "Set an extra monthly amount against each target in the Extra /mo "
                "column. Everything at zero falls back to funding the top "
                "target first.")
            return
        shares = "  ·  ".join(
            f"{self._name_for(k)} {b.value() / total * 100:.0f}%"
            for k, b in self._split_boxes.items() if b.value() > 0)
        self.custom_note.setText(
            f"Splitting {fmt_money(self.surplus.value())}/mo as — {shares}")

    def _name_for(self, key: str) -> str:
        a = self._plan.by_key(key) if self._plan else None
        return a.target.name if a else key

    def _render_chart(self) -> None:
        plan = self._plan
        p = self.plot
        p.clear()
        if p.plotItem.legend:
            p.plotItem.legend.clear()
        charts.unlock_view(p)
        if not plan.dates or not plan.allocations:
            self.plan_note.setText(
                "Add a goal or a loan to see a plan. " +
                " ".join(plan.warnings))
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
        series = []
        for k, a in enumerate(plan.allocations):
            ys = np.array(plan.progress[a.target.key][:last]) * 100.0
            colour = _SERIES_COLORS[k % len(_SERIES_COLORS)]
            charts.line(p, x, ys, colour, 2.0, escape(a.target.name),
                        dashed=(a.target.kind == LOAN))
            series.append(ys)
        floor = charts.soft_floor(p, 100.0, "100% · funded", theme.ACCENT, slot="zero")
        floor.label.setPosition(0.6)
        charts.lock_view(p, float(x[0]), float(x[-1]), series,
                         include_y=[0.0, 100.0])
        done = plan.all_done
        bits = [f"{fmt_money(plan.surplus)}/mo spare, split "
                f"{POLICIES[plan.policy].split('—')[0].strip().lower()}"]
        if done:
            bits.append(f"everything funded by {done.strftime('%b %Y')}")
        bits += [f"⚠ {w}" for w in plan.warnings]
        self.plan_note.setText("   ·   ".join(bits))

    def _render_cascades(self) -> None:
        plan = self._plan
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
        self.target_pick.setEnabled(bool(live))

    def _render_faster(self) -> None:
        plan = self._plan
        self._sync_target_pick()
        live = [a for a in plan.allocations if not a.target.complete]
        if not live:
            self.faster.setText("Every target on the ladder is already met.")
            return
        key = self.target_pick.currentData()
        focus = next((a for a in live if a.target.key == key), live[0])
        try:
            ups = speedups(self.store, plan, target_key=focus.target.key)
        except Exception as e:
            self.faster.setText(f"Could not cost the options: {e}")
            return
        # Rank among the *live* rungs, matching goals.deadline_plan. Counting
        # completed targets too would tell someone their second remaining goal
        # is "priority 3", which is a different and wrong claim.
        rank = live.index(focus) + 1
        where = ("Next up" if focus is live[0]
                 else f"Priority {rank} on the ladder")
        head = (f"<div style='margin-bottom:6px'>{where}: "
                f"<b>{focus.target.name}</b>"
                + (f", on track for {focus.eta.strftime('%b %Y')}"
                   if focus.eta else ", not reached at this rate") + ".</div>")
        if focus is not live[0]:
            head += (f"<div style='color:{theme.MUTED};margin-bottom:6px'>"
                     f"Everything above it keeps its priority, so these figures "
                     f"are on top of what you are already committed to.</div>")
        rows = []
        for s in ups:
            if s.months_saved <= 0:
                continue
            when = s.new_eta.strftime("%b %Y") if s.new_eta else "—"
            rows.append(
                f"<div style='margin-bottom:4px'>"
                f"<span style='color:{theme.ACCENT}'>"
                f"+{fmt_money(s.extra)}/mo</span> → {when} "
                f"<span style='color:{theme.MUTED}'>"
                f"({s.months_saved} month{'s' if s.months_saved != 1 else ''} "
                f"sooner)</span></div>")
        if not rows:
            rows = ["<div style='color:#8B98A9'>Extra saving doesn't move this "
                    "one — it is already funded from the first month.</div>"]
        self.faster.setText(head + "".join(rows) + self._deadline_html(focus))

    def _deadline_html(self, focus) -> str:
        """What hitting the user's chosen date would actually cost, by name."""
        qd = self.by_date.date()
        try:
            dl = deadline_plan(self.store, self._plan, focus.target.key,
                               date(qd.year(), qd.month(), 1))
        except Exception as e:
            return f"<div style='color:{theme.RED}'>Couldn't cost that date: {e}</div>"

        head = (f"<hr style='border:none;border-top:1px solid {theme.BORDER}'>"
                f"<div style='margin:6px 0'><b>By {dl.by.strftime('%b %Y')}</b> "
                f"— {dl.target}. Standalone pace: "
                f"<span style='color:{theme.ACCENT}'>"
                f"{fmt_money(dl.needed_monthly)}/mo</span>, including this month. "
                f"The extra budget below also accounts for higher priorities.</div>")
        if dl.already_met or (dl.feasible and not dl.cuts):
            return head + (f"<div style='color:{theme.ACCENT}'>{dl.note}</div>")
        if not dl.feasible:
            tail = (f"<div style='color:{theme.BLUE};margin-top:6px'>"
                    f"{dl.advice}</div>" if dl.advice else "")
            return head + (
                f"<div style='color:{theme.RED};margin-bottom:4px'>"
                f"Not reachable by then.</div>"
                f"<div style='color:{theme.MUTED}'>{dl.note}</div>" + tail)

        rows = [f"<div style='margin-bottom:4px'>Find "
                f"<b>{fmt_money(dl.shortfall)}/mo</b> more. The smallest set of "
                f"discretionary cuts that covers it:</div>"]
        for c in dl.cuts:
            tag = ("recurring" if c.recurring else
                   "uncategorised — check this is really optional"
                   if c.bucket == "unknown" else "spending")
            colour = theme.AMBER if c.bucket == "unknown" else theme.TEXT
            rows.append(
                f"<div style='margin-bottom:3px'>· <span style='color:{colour}'>"
                f"{c.name}</span> — {fmt_money(c.monthly)}/mo "
                f"<span style='color:{theme.MUTED}'>({tag})</span></div>")
        rows.append(
            f"<div style='color:{theme.MUTED};margin-top:6px'>Only wants are "
            f"listed. Essentials and loan payments are never proposed as cuts, "
            f"so if the list above is short of the number, the date is out of "
            f"reach rather than merely expensive.</div>")
        if dl.advice:
            rows.append(f"<div style='color:{theme.BLUE};margin-top:6px'>"
                        f"{dl.advice}</div>")
        return head + "".join(rows)

    # ---------------------------------------------------------------
    def _selected_key(self) -> str | None:
        r = self.table.currentRow()
        if r < 0 or r >= len(self._keys):
            return None
        return self._keys[r]

    def _funding(self) -> None:
        key = self._selected_key()
        if key and key.startswith("goal:"):
            gid = int(key.split(":")[1])
            goal = next((g for g in self.store.goals() if g.id == gid), None)
            if goal and goal.kind == "savings":
                GoalFundingDialog(self.store, gid, self).exec()
                self._rerun()
                self.main.refresh_header()
                return
        QMessageBox.information(self, "Select a savings goal",
                                "Choose a purchase or reserve goal. Loan payments are recorded in your bank ledger.")

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
        self.store.set_plan_order(keys)
        self._rerun()
        self.table.selectRow(j)

    def _add(self) -> None:
        dlg = GoalDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, amount, kind, saved = dlg.values()
        with self.store.undo_group(f"Add goal “{name}”"):
            gid = self.store.add_goal(Goal(name, amount, kind, saved_amount=saved))
            self.store.set_plan_order(self._keys + [f"goal:{gid}"])
        self._rerun()
        self.main.refresh_header()

    def _add_emergency(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Emergency fund goal")
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
                f"{fmt_money(amt)} — {months.value()} × your own essential "
                f"monthly spending." if amt > 0 else
                "Not enough categorised spending yet to size this. Import more "
                "history, or add it as an ordinary goal with your own figure.")
        months.valueChanged.connect(sync)
        sync()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        amt = emergency_goal_amount(self.store, months.value())
        if amt <= 0:
            QMessageBox.information(
                self, "Can't size it yet",
                "Essential spending can't be established from the ledger yet.")
            return
        with self.store.undo_group("Add emergency fund goal"):
            gid = self.store.add_goal(
                Goal(f"Emergency fund ({months.value()} mo)", amt, "savings"))
            # An emergency fund is the one target mainstream guidance is
            # unanimous about putting first, so it goes to the top rather than
            # the bottom.
            self.store.set_plan_order([f"goal:{gid}"] + self._keys)
        self._rerun()
        self.main.refresh_header()

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
        g = next((x for x in self.store.goals() if x.id == gid), None)
        if g is None:
            return
        dlg = GoalDialog(self, goal=g)
        if self.store.goal_activity(gid):
            dlg.saved.setEnabled(False)
            dlg.kind.setEnabled(False)
            dlg.explanation.setText("Savings are tracked in Goal activity. Record contributions, "
                                    "purchases and releases there to preserve the funding history.")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, amount, kind, saved = dlg.values()
        try:
            self.store.update_goal(gid, name=name, amount=amount, kind=kind, saved_amount=saved)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not update goal", str(exc))
            return
        self._rerun()
        self.main.refresh_header()

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
        if QMessageBox.question(self, "Delete goal",
                                "Remove this goal from the ladder?") \
                != QMessageBox.StandardButton.Yes:
            return
        with self.store.undo_group("Delete goal"):
            self.store.delete_goal(gid)
            self.store.set_plan_order([k for k in self._keys if k != key])
        self._rerun()
        self.main.refresh_header()
