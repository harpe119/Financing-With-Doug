"""Recurring page — review what the detector believes about your cash flow."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..models import AccountType, CADENCE_LABEL
from ..pipeline import refresh_derived
from ..recurrence import next_occurrence
from ..store import Store
from ..util import fmt_money
from . import theme
from .widgets import hint


def _find_by_value(combo: QComboBox, want) -> int:
    """QComboBox.findData compares Python payloads by identity; compare by
    value instead."""
    for i in range(combo.count()):
        if combo.itemData(i) == want:
            return i
    return -1


class RecurringPage(QWidget):
    COLS = ["On", "Name", "Cadence", "Amount", "≈ /month", "Next", "Conf.",
            "Treat as"]

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        bar.addWidget(hint(
            "Patterns found in your ledger. Active items are replayed "
            "forward by the forecast — uncheck “On” or set “Treat as” → "
            "Ignore to exclude one that still occurs but shouldn't count. "
            "To remove a pattern that no longer happens at all (e.g. a "
            "former job's income), select its row and click Delete."), 1)
        redo = QPushButton("Re-detect from ledger")
        redo.clicked.connect(self._redetect)
        bar.addWidget(redo)
        delete = QPushButton("Delete")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete)
        bar.addWidget(delete)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._item_changed)
        lay.addWidget(self.table, 1)

    # ---------------------------------------------------------------
    def refresh(self) -> None:
        self._loading = True
        items = self.store.recurring()
        loans = [a for a in self.store.accounts() if a.type == AccountType.LOAN]
        today = date.today()
        self.table.setRowCount(len(items))
        pos = QBrush(QColor(theme.POSITIVE))
        neg = QBrush(QColor(theme.NEGATIVE))
        for r, it in enumerate(items):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsSelectable)
            on.setCheckState(Qt.CheckState.Checked if it.active
                             else Qt.CheckState.Unchecked)
            on.setData(Qt.ItemDataRole.UserRole, it.id)

            name = QTableWidgetItem(it.name)
            cad = QTableWidgetItem(CADENCE_LABEL[it.cadence])
            amt = QTableWidgetItem(fmt_money(it.amount, signed=True))
            amt.setForeground(pos if it.amount >= 0 else neg)
            amt.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            mo = QTableWidgetItem(fmt_money(it.monthly_equivalent, signed=True))
            mo.setForeground(pos if it.amount >= 0 else neg)
            mo.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
            nxt = next_occurrence(it, today)
            nx = QTableWidgetItem(nxt.isoformat() if nxt else "—")
            conf = QTableWidgetItem(f"{it.confidence * 100:.0f}%")

            for col, item in enumerate((on, name, cad, amt, mo, nx, conf)):
                self.table.setItem(r, col, item)

            combo = QComboBox()
            combo.addItem("Income", ("income", None))
            combo.addItem("Bill", ("bill", None))
            combo.addItem("Ignore", ("ignore", None))
            for l in loans:
                combo.addItem(f"Loan payment → {l.name}", ("loan_payment", l.id))
            want = (it.kind, it.linked_account_id if it.kind == "loan_payment"
                    else None)
            idx = _find_by_value(combo, want)
            if idx < 0 and it.kind == "loan_payment":
                idx = _find_by_value(combo, ("bill", None))
            combo.setCurrentIndex(max(idx, 0))
            combo.currentIndexChanged.connect(
                lambda _i, c=combo, iid=it.id: self._kind_changed(iid, c))
            self.table.setCellWidget(r, 7, combo)
        self._loading = False

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        iid = item.data(Qt.ItemDataRole.UserRole)
        self.store.update_recurring(
            iid, active=item.checkState() == Qt.CheckState.Checked)

    def _kind_changed(self, item_id: int, combo: QComboBox) -> None:
        if self._loading:
            return
        kind, link = combo.currentData()
        self.store.update_recurring(item_id, kind=kind, linked_account_id=link)
        self.main.status("Recurring item updated.")

    def _redetect(self) -> None:
        n = refresh_derived(self.store)
        self.refresh()
        self.main.status(f"Detection refreshed — {n} patterns tracked.")

    def _delete(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            self.main.status("Select a row to delete first.")
            return
        item_id = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
        name = self.table.item(r, 1).text()
        ok = QMessageBox.question(
            self, "Delete recurring pattern",
            f"Delete “{name}”?\n\n"
            "Use this only for patterns that no longer occur at all — if "
            "matching transactions are still in your ledger, re-detecting "
            "will simply find it again. To exclude a pattern that still "
            "happens but shouldn't count in the forecast, uncheck “On” or "
            "set “Treat as” → Ignore instead.")
        if ok == QMessageBox.StandardButton.Yes:
            self.store.delete_recurring(item_id)
            self.refresh()
            self.main.status(f"Deleted “{name}”.")
