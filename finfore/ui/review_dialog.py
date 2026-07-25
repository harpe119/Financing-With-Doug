"""Review-uncategorized dialog — the active-learning queue.

Transactions collapse to far fewer merchants than rows, so labelling merchants
rather than transactions is the fast way out of a large uncategorised pile. The
queue is ordered by how much each decision is worth (total dollars, weighted
toward recent and recurring spend), so working top-down clears the bulk of the
total in a few dozen clicks.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..learning import (label_cluster, recategorize_all, scoring_window_months,
                        uncategorized_clusters, uncategorized_summary)
from ..store import Store
from ..util import fmt_money
from .widgets import hint

# Trailing windows the queue can be narrowed to. The default matches the span
# the Insights habits score actually reads, because clearing the pile in order
# to fix that score is the reason most people open this dialog.
_WINDOWS = [("Last 3 months", 3), ("Last 6 months", 6), ("Last 12 months", 12),
            (f"Scoring window ({scoring_window_months()} months)",
             scoring_window_months()),
            ("Everything", None)]
_DEFAULT_WINDOW = 3          # index of the scoring-window entry


class ReviewUncategorizedDialog(QDialog):
    COLS = ["Merchant", "Txns", "Total", "Recent 90d", "Per month", "Category"]

    def __init__(self, store: Store, categories: list[str], parent=None):
        super().__init__(parent)
        self.store = store
        self.categories = categories
        self.setWindowTitle("Review uncategorized spending")
        self.setMinimumSize(880, 560)
        self._clusters = []
        self._boxes: list[QComboBox] = []

        lay = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay.addWidget(self.summary)
        lay.addWidget(hint(
            "Each row is one merchant, not one transaction — labelling it fixes "
            "every past and future charge from that merchant at once. The list "
            "is ordered by how much each decision is worth, so the top of the "
            "list clears the most. Suggested categories come from merchants you "
            "have already labelled; blank means no confident match."))

        wrow = QHBoxLayout()
        wrow.addWidget(QLabel("Only merchants seen in the"))
        self.window = QComboBox()
        for label, months in _WINDOWS:
            self.window.addItem(label, months)
        self.window.setCurrentIndex(_DEFAULT_WINDOW)
        self.window.setToolTip(
            "The habits score weights each month by 0.5^(age/3), so a charge "
            "two years old counts about 1/256 of last month's. Labelling those "
            "merchants is real work that barely moves the score. Labels you do "
            "apply still cover every matching row, however old.")
        self.window.currentIndexChanged.connect(self._reload)
        wrow.addWidget(self.window)
        wrow.addStretch(1)
        self.hidden_note = hint("")
        wrow.addWidget(self.hidden_note)
        lay.addLayout(wrow)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        accept_all = QPushButton("Accept all suggestions")
        accept_all.setToolTip("Apply every non-blank suggested category at once")
        accept_all.clicked.connect(self._accept_suggested)
        row.addWidget(accept_all)
        row.addStretch(1)
        self.status = hint("")
        row.addWidget(self.status)
        lay.addLayout(row)

        btns = QDialogButtonBox()
        apply_btn = btns.addButton("Apply selections",
                                   QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn.setObjectName("primary")
        btns.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.accept)
        lay.addWidget(btns)

        self._reload()

    # ---------------------------------------------------------------
    def _reload(self) -> None:
        months = self.window.currentData()
        self._clusters = uncategorized_clusters(self.store, limit=60,
                                                months=months)
        s = uncategorized_summary(self.store, months=months)
        if s["merchants"]:
            self.summary.setText(
                f"{s['merchants']} unlabelled merchants · {s['transactions']} "
                f"transactions · {fmt_money(s['total'])} total — the top 10 "
                f"merchants alone are {s['top10_share'] * 100:.0f}% of it")
        elif months:
            self.summary.setText("Nothing uncategorized in this window.")
        else:
            self.summary.setText("Nothing uncategorized. ")
        self.hidden_note.setText(
            f"{s['hidden_merchants']} older merchant"
            f"{'s' if s['hidden_merchants'] != 1 else ''} "
            f"({fmt_money(s['hidden_total'])}) not shown"
            if s["hidden_merchants"] else "")
        self.table.setRowCount(len(self._clusters))
        self._boxes = []
        for r, c in enumerate(self._clusters):
            name = c.display if len(c.display) <= 46 else c.display[:45] + "…"
            if c.is_recurring:
                name = "↻  " + name
            cells = [name, str(c.n_tx), fmt_money(c.total),
                     fmt_money(c.recent_total), fmt_money(c.monthly)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col >= 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                if c.is_recurring and col == 0:
                    item.setToolTip("Detected as a recurring charge — labelling "
                                    "this pays off every month")
                self.table.setItem(r, col, item)
            box = QComboBox()
            box.addItem("— leave uncategorized —", "")
            box.addItems(self.categories)
            if c.suggestion:
                i = box.findText(c.suggestion)
                if i >= 0:
                    box.setCurrentIndex(i)
                    box.setToolTip(f"Suggested from a merchant you already "
                                   f"labelled (similarity {c.confidence:.2f})")
            self.table.setCellWidget(r, 5, box)
            self._boxes.append(box)
        self.status.setText("")

    def _accept_suggested(self) -> None:
        n = 0
        for box, c in zip(self._boxes, self._clusters):
            if c.suggestion:
                i = box.findText(c.suggestion)
                if i >= 0:
                    box.setCurrentIndex(i)
                    n += 1
        self.status.setText(f"{n} suggestion{'s' if n != 1 else ''} selected — "
                            f"press Apply to commit.")

    def _apply(self) -> None:
        picks = [(c, b.currentText()) for c, b in zip(self._clusters, self._boxes)
                 if b.currentData() != "" and b.currentIndex() > 0]
        if not picks:
            QMessageBox.information(self, "Nothing selected",
                                    "Choose a category for at least one merchant.")
            return
        moved = 0
        for cluster, cat in picks:
            moved += label_cluster(self.store, cluster, cat)
        propagated = recategorize_all(self.store)
        self._reload()
        QMessageBox.information(
            self, "Categories applied",
            f"Labelled {moved} transaction{'s' if moved != 1 else ''} across "
            f"{len(picks)} merchant{'s' if len(picks) != 1 else ''}.\n\n"
            + (f"{propagated} further transactions were matched automatically "
               f"from similar merchant names."
               if propagated else
               "No further transactions matched automatically yet — the more "
               "merchants you label, the more this will catch on its own."))
