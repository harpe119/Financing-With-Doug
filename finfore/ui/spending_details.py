"""A monthly spending figure with a trace back to its ledger entries."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHeaderView, QTabWidget, QTextBrowser, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ..insights import MonthlyBreakdown
from ..util import fmt_money, fmt_day
from .widgets import InfoDialog, hint

def _money(value: float, decimals=2) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.{decimals}f}"


GROUPS = [("income", "Income"), ("essential", "Needs"),
          ("frivolous", "Wants"), ("giving", "Giving"), ("unknown", "Uncategorized")]


class SpendingDetailsDialog(InfoDialog):
    def __init__(self, details: MonthlyBreakdown | None, score_html: str,
                 account_names: dict[int, str], parent=None):
        super().__init__("Where your monthly figures come from", score_html, parent,
                         width=940, height=700)
        self._rows = {}
        self._accounts = account_names
        layout = self.layout()
        position = layout.indexOf(self.view)
        layout.removeWidget(self.view)
        self.tabs = QTabWidget()
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 8, 0, 0)
        box.addWidget(hint(
            "Expand a category to see its transactions. Monthly contributions use "
            "the same complete months and recency weights as the spending figures. "
            "Refunds reduce the category they are assigned to."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Group / category / transaction", "Date", "Ledger amount", "Monthly contribution"])
        self.tree.setAccessibleName("Monthly spending categories and contributing transactions")
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setHorizontalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 105), (2, 130), (3, 170)):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.tree.setColumnWidth(column, width)
        box.addWidget(self.tree, 1)
        self.tree.itemExpanded.connect(self._expand_category)
        self.tabs.addTab(body, "Monthly breakdown")
        self.tabs.addTab(self.view, "How the score works")
        self.months = QTextBrowser()
        self.months.setObjectName("infoBody")
        self.months.document().setDefaultStyleSheet(self.view.document().defaultStyleSheet())
        self.tabs.addTab(self.months, "Months && exclusions")
        layout.insertWidget(position, self.tabs, 1)
        if details is None:
            box.insertWidget(1, hint("No complete months of personal transactions are available yet."))
            self.months.setPlainText("Import transactions covering at least one complete calendar month.")
            return
        agg = details.aggregates
        summary = hint(f"Income {fmt_money(agg.income)}/mo · Spending {fmt_money(agg.spending)}/mo · "
                       f"Leftover {fmt_money(agg.surplus)}/mo. "
                       "Group totals use unrounded amounts; displayed rows may differ by a few cents when added.")
        box.insertWidget(0, summary)
        for key, label in GROUPS:
            rows = details.groups[key]
            total = getattr(agg, key)
            group = QTreeWidgetItem(self.tree, [label, "", "", _money(total)])
            group.setData(3, Qt.ItemDataRole.UserRole, total)
            font = group.font(0)
            font.setBold(True)
            for col in (0, 3):
                group.setFont(col, font)
            self._align_amounts(group)
            sums = rows.groupby("category")["contribution"].sum().sort_values(ascending=False)
            for category, value in sums.items():
                entries = rows[rows["category"] == category].sort_values("date", ascending=False)
                item = QTreeWidgetItem(group, [f"{category} ({len(entries)})", "", "", _money(value)])
                item.setData(0, Qt.ItemDataRole.UserRole, str(category))
                item.setData(3, Qt.ItemDataRole.UserRole, float(value))
                item.setToolTip(0, f"{category} · {len(entries)} transactions. Expand to inspect the ledger entries.")
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                self._rows[id(item)] = entries
                self._align_amounts(item)
            net = float(rows["contribution"].sum())
            if net < 0:
                adjustment = QTreeWidgetItem(group, ["Credit above spending (group floored at $0)", "", "",
                                                     _money(-net)])
                adjustment.setData(3, Qt.ItemDataRole.UserRole, -net)
                self._align_amounts(adjustment)
            group.setExpanded(True)
        weights = "".join(f"<tr><td>{month}</td><td>{share:.2%}</td></tr>"
                          for month, share in sorted(details.month_shares.items(), reverse=True))
        self.months.setHtml(
            f"<h3>{agg.n_months} complete months</h3>"
            f"<p>Newer months count more. A month's weight halves every {agg.half_life:g} months. "
            "The shares below add to 100%. Each ledger amount is multiplied by its month's share "
            "to obtain its contribution in dollars per month. A category with no entries in a "
            "selected month contributes zero for that month.</p>"
            f"<table><tr><th>Month</th><th>Share of average</th></tr>{weights}</table>"
            "<h3>What is left out</h3><p>The current month, future dates, and months outside "
            "the selected history are excluded. Up to 24 complete months with personal transactions "
            "are used. Transfers and Business (reimbursable) never enter these figures. A reimbursement "
            "marked inside a paycheck is subtracted from income; hover its transaction to see "
            "the original deposit and the personal amount used.</p>"
            "<p>Income counts positive entries in income categories. Uncategorized counts outflows in "
            "categories that have not been assigned to a spending group. Negative income entries "
            "and positive uncategorized entries are excluded. Categorized refunds offset spending in "
            "that group; a group's net spending cannot fall below zero.</p>"
            "<p>These figures use your transactions, rather than just recurring bills. "
            "For example, Needs also includes groceries, utilities, insurance, fuel, transport, "
            "debt payments, and health &amp; fitness.</p>")

    @staticmethod
    def _align_amounts(item):
        for column in (2, 3):
            item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _expand_category(self, item):
        rows = self._rows.pop(id(item), None)
        if rows is None:
            return
        for _, row in rows.iterrows():
            description = str(row.get("description", "Transaction"))
            account = self._accounts.get(int(row.get("account_id", 0)), "Account")
            contribution = float(row["contribution"])
            child = QTreeWidgetItem(item, [description, fmt_day(row["date"]),
                                          _money(float(row["ledger_amount"])),
                                          _money(contribution)])
            child.setData(0, Qt.ItemDataRole.UserRole, int(row.get("id", 0)))
            child.setData(3, Qt.ItemDataRole.UserRole, contribution)
            note = (f"{description}\n{account} · {row['date']:%Y-%m-%d}\n"
                    f"Ledger: {float(row['ledger_amount']):,.2f}\n"
                    f"Reimbursement excluded: {float(row['reimbursement_excluded']):,.2f}\n"
                    f"Personal amount used: {float(row['amount']):,.2f}\n"
                    f"Month share: {float(row['month_share']):.4%}\n"
                    f"Monthly contribution: {contribution:,.4f}")
            for column in range(4):
                child.setToolTip(column, note)
            self._align_amounts(child)
