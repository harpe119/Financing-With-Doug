"""Recurring page — review what the detector believes about your cash flow."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QBrush, QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QMenu, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..models import AccountType, CADENCE_LABEL
from ..pipeline import refresh_derived
from ..recurrence import next_occurrence
from ..store import Store
from ..util import fmt_money
from . import theme
from .widgets import hint


class RecurringPage(QWidget):
    # No "Treat as" column. It duplicated the On checkbox for the only choice
    # people actually made (Ignore), and the rest — income vs bill, and linking
    # a payment to its loan — is detected automatically and needed so rarely
    # that a permanent column of combo boxes was the wrong price for it. Both
    # still available on right-click.
    COLS = ["On", "Name", "Cadence", "Amount", "≈ /month", "Next", "Conf."]

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
            "These patterns supply the calendar and forecast. Uncheck On to exclude an item. "
            "Delete patterns that have ended. Right-click to change the type or link a loan payment."), 1)
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
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        vh = self.table.verticalHeader()
        vh.setDefaultSectionSize(36)
        vh.setMinimumSectionSize(36)
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
                if it.kind == "loan_payment":
                    item.setToolTip("Linked to a loan — charged once by the "
                                    "amortisation, not twice.")
                self.table.setItem(r, col, item)

        self._loading = False

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        iid = item.data(Qt.ItemDataRole.UserRole)
        self.store.update_recurring(
            iid, active=item.checkState() == Qt.CheckState.Checked)
        self.main.refresh_header()

    def _context_menu(self, point) -> None:
        """Right-click: the rare reclassifications, off the main surface."""
        r = self.table.rowAt(point.y())
        if r < 0 or self.table.item(r, 0) is None:
            return
        iid = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
        item = next((i for i in self.store.recurring() if i.id == iid), None)
        if item is None:
            return
        menu = QMenu(self)
        head = menu.addAction(f"“{item.name}” is…")
        head.setEnabled(False)
        menu.addSeparator()
        for label, kind in (("Income", "income"), ("A bill", "bill"),
                            ("Not real — ignore it", "ignore")):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(item.kind == kind)
            act.triggered.connect(
                lambda _c=False, i=iid, k=kind: self._set_kind(i, k, None))
            menu.addAction(act)
        loans = [a for a in self.store.accounts()
                 if a.type == AccountType.LOAN]
        if loans:
            menu.addSeparator()
            for l in loans:
                act = QAction(f"A payment on {l.name}", self)
                act.setCheckable(True)
                act.setChecked(item.kind == "loan_payment"
                               and item.linked_account_id == l.id)
                act.triggered.connect(
                    lambda _c=False, i=iid, lid=l.id:
                    self._set_kind(i, "loan_payment", lid))
                menu.addAction(act)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def _set_kind(self, item_id: int, kind: str, link) -> None:
        self.store.update_recurring(item_id, kind=kind, linked_account_id=link)
        self.refresh()
        self.main.refresh_header()
        self.main.status("Recurring item updated.")

    def _redetect(self) -> None:
        n = refresh_derived(self.store)
        self.refresh()
        self.main.refresh_header()
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
            "happens but shouldn't count in the forecast, uncheck “On”, or "
            "right-click it and choose “Not real — ignore it”, instead.")
        if ok == QMessageBox.StandardButton.Yes:
            self.store.delete_recurring(item_id)
            self.refresh()
            self.main.refresh_header()
            self.main.status(f"Deleted “{name}”.")
