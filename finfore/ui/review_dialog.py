"""Review-uncategorized dialog — the active-learning queue.

Transactions collapse to far fewer merchants than rows, so labelling merchants
rather than transactions is the fast way out of a large uncategorised pile. The
queue is ordered by how much each decision is worth (total dollars, weighted
toward recent and recurring spend), so working top-down clears the bulk of the
total in a few dozen clicks.
"""
from __future__ import annotations

import pandas as pd

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QHeaderView, QLabel, QMessageBox, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from ..learning import (feedback_key, scoring_window_months,
                        uncategorized_clusters, uncategorized_summary)
from ..store import Store
from ..util import fmt_money, fmt_day
from .widgets import FlowLayout, hint

# Trailing windows the queue can be narrowed to. The default matches the span
# the Insights habits score actually reads, because clearing the pile in order
# to fix that score is the reason most people open this dialog.
#
# The old label for that entry read "Scoring window (24 months)", which is true
# and misleading in the same breath: 24 months is only the point past which a
# month contributes nothing, while the 3-month half-life means the last quarter
# already carries half the score. Reading "24 months" and concluding the score
# treats two years evenly is the obvious inference and the wrong one.
_WINDOWS = [("Last 3 months", 3), ("Last 6 months", 6), ("Last 12 months", 12),
            (f"Last {scoring_window_months()} months (weighted)", scoring_window_months()),
            ("Everything", None)]
_DEFAULT_WINDOW = 3          # index of the scoring-window entry

# How many merchants the table will show at once. Previously 60 with nothing
# saying so, which is how "I labelled every row and the list didn't clear"
# happens: the summary counted 80 merchants, the table held 60, and the 20 the
# user never saw were still there afterwards.
MAX_ROWS = 250


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
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay.addWidget(self.summary)
        lay.addWidget(hint(
            "Choose categories by merchant and inspect affected transactions below. "
            "Suggestions stay unselected until you accept them. Existing reviewed "
            "or known categories are preserved."))

        wrow = FlowLayout(spacing=10)
        wrow.addWidget(QLabel("Period"))
        self.window = QComboBox()
        for label, months in _WINDOWS:
            self.window.addItem(label, months)
        self.window.setCurrentIndex(_DEFAULT_WINDOW)
        self.window.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.window.setMinimumContentsLength(24)
        self.window.setToolTip(
            "The habits score uses the last 24 months, giving recent months more weight. "
            "Review covers unknown entries in the selected period. "
            "Include older matching entries extends that scope.")
        self.window.currentIndexChanged.connect(self._reload)
        wrow.addWidget(self.window)
        self.hidden_note = hint("")
        wrow.addWidget(self.hidden_note)
        lay.addLayout(wrow)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setTabKeyNavigation(False)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        vh = self.table.verticalHeader()
        vh.setVisible(False)
        # Merchant names are long and the Category cell holds a live combo box;
        # at the default ~30px both get clipped through the middle of the text.
        vh.setDefaultSectionSize(40)
        vh.setMinimumSectionSize(40)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(self.COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.table, 1)
        self.table.itemSelectionChanged.connect(self._preview)
        self.scope = QWidget()
        scope_row = FlowLayout(self.scope, spacing=12)
        self.remember = QCheckBox('Remember for future imports')
        self.remember.setToolTip('Check to reuse these merchant categories on future imports. Business labels stay per transaction.')
        self.include_older = QCheckBox('Include older matching entries')
        self.include_older.toggled.connect(self._preview)
        self.include_older.toggled.connect(self._update_pending)
        scope_row.addWidget(self.remember)
        scope_row.addWidget(self.include_older)
        lay.addWidget(self.scope)
        self.preview_note = hint('Select a merchant to inspect its transactions.')
        self.preview_note.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.preview_note)
        self.preview = QTableWidget(0, 5)
        self.preview.setHorizontalHeaderLabels(['Date', 'Account', 'Description', 'Amount', 'Category'])
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview.verticalHeader().hide()
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.preview.setFixedHeight(115)
        lay.addWidget(self.preview)

        row = QHBoxLayout()
        accept_all = QPushButton("Accept all suggestions")
        accept_all.setToolTip("Select suggested categories. Apply selections saves them.")
        accept_all.clicked.connect(self._accept_suggested)
        row.addWidget(accept_all)
        self.status = hint("")
        row.addWidget(self.status, 1)
        lay.addLayout(row)
        self.pending_note = hint('Choose categories to prepare a review.')
        lay.addWidget(self.pending_note)

        btns = QDialogButtonBox()
        apply_btn = btns.addButton("Apply selections",
                                   QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn.setObjectName("primary")
        self.apply_btn = apply_btn
        self.apply_btn.setEnabled(False)
        btns.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.accept)
        lay.addWidget(btns)

        self._reload()

    # ---------------------------------------------------------------
    def _reload(self) -> None:
        try:
            self._reload_inner()
        except Exception as exc:
            self._clusters, self._boxes = [], []
            self.table.setRowCount(0)
            self.preview.setRowCount(0)
            self.summary.setText('Could not load transaction review.')
            self.preview_note.setText(f'Close this window and retry. {exc}')

    def _reload_inner(self) -> None:
        months = self.window.currentData()
        self._clusters = uncategorized_clusters(self.store, limit=MAX_ROWS,
                                                months=months)
        s = uncategorized_summary(self.store, months=months)
        shown = min(len(self._clusters), s["merchants"])
        more = max(0, s["merchants"] - shown)
        if s["merchants"]:
            # Say plainly when the table is not the whole list. Without this,
            # labelling every visible row and finding the queue non-empty looks
            # like the button failed.
            head = (f"{s['merchants']} unlabelled merchants"
                    + (f" (showing the top {shown})" if more else ""))
            concentration = (f" — the top 10 merchants are {s['top10_share'] * 100:.0f}% of it"
                             if s['merchants'] > 10 else '')
            self.summary.setText(
                f"{head} · {s['transactions']} transactions · "
                f"{fmt_money(s['total'])} total" + concentration)
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
            box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            box.setAccessibleName(f'Category for merchant {c.display}')
            box.addItem("— leave uncategorized —", "")
            box.addItems(self.categories)
            if c.suggestion:
                box.setToolTip(f'Suggestion: {c.suggestion}, based on “{c.suggestion_source}”. '
                               'Choose it explicitly after checking the entries.')
            self.table.setCellWidget(r, 5, box)
            self._boxes.append(box)
            box.currentIndexChanged.connect(self._update_pending)
        self.status.setText("")
        self._preview()
        self._update_pending()

    def _update_pending(self, *_):
        if not hasattr(self, 'pending_note'):
            return
        try:
            tx = self.store.transactions()
            selected = [(c, b) for c, b in zip(self._clusters, self._boxes) if b.currentIndex() > 0]
            count = sum(len(self._matching_rows(c, tx)) for c, _ in selected)
            merchants = f'{len(selected)} merchant' + ('s' if len(selected) != 1 else '')
            transactions = f'{count} transaction' + ('s' if count != 1 else '')
            self.pending_note.setText(f'{merchants} · {transactions} ready to review.'
                if selected else 'Choose a category, or select suggested categories, before saving.')
            self.apply_btn.setText(f'Review {transactions}' if count else 'Apply selections')
            self.apply_btn.setEnabled(count > 0)
        except Exception:
            self.pending_note.setText('Could not check the pending entries. Close and reopen review.')
            self.apply_btn.setEnabled(False)

    def _matching_rows(self, cluster, tx):
        keys = [feedback_key(d, a) for d, a in zip(tx.description, tx.amount)]
        mask = (pd.Series(keys, index=tx.index).eq(cluster.norm_key) &
                tx.category.isin({'Misc', 'Other income', ''}) & tx.category_reviewed.eq(0))
        if not self.include_older.isChecked():
            mask &= tx.id.isin(cluster.tx_ids)
        return tx[mask]

    def _preview(self, *_):
        try:
            self._preview_inner()
        except Exception as exc:
            self.preview.setRowCount(0)
            self.preview_note.setText(f'Could not inspect these transactions. Close and reopen review. {exc}')

    def _preview_inner(self):
        if not hasattr(self, 'preview'):
            return
        row = self.table.currentRow()
        self.preview.setRowCount(0)
        if not 0 <= row < len(self._clusters):
            self.preview_note.setText('Select a merchant to inspect its transactions.')
            return
        cluster = self._clusters[row]
        rows = self._matching_rows(cluster, self.store.transactions()).sort_values(['date','id'], ascending=False)
        note = f'{len(rows)} transaction{"s" if len(rows) != 1 else ""} would be updated for this merchant.'
        if cluster.suggestion:
            note += f' Suggested {cluster.suggestion}, based on your correction for “{cluster.suggestion_source}”.'
        self.preview_note.setText(note)
        self.preview.setRowCount(min(100, len(rows)))
        accounts = {a.id: a.name for a in self.store.accounts()}
        for i, entry in enumerate(rows.head(100).itertuples()):
            values = [fmt_day(entry.date) if pd.notna(entry.date) else 'Undated',
                accounts.get(entry.account_id, '?'), entry.description, f'${entry.amount:,.2f}', entry.category]
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.preview.setItem(i, j, item)
        if len(rows) > 100:
            self.preview_note.setText(note + ' Preview shows the newest 100.')

    def _accept_suggested(self) -> None:
        n = 0
        for box, c in zip(self._boxes, self._clusters):
            if c.suggestion:
                i = box.findText(c.suggestion)
                if i >= 0:
                    blocked = box.blockSignals(True)
                    box.setCurrentIndex(i)
                    box.blockSignals(blocked)
                    n += 1
        self.status.setText(f"{n} suggestion{'s' if n != 1 else ''} selected — "
                            f"review the pending count before saving.")
        self._update_pending()

    def _apply(self) -> None:
        picks = [(c, b.currentText()) for c, b in zip(self._clusters, self._boxes)
                 if b.currentData() != "" and b.currentIndex() > 0]
        if not picks:
            QMessageBox.information(self, "Nothing selected",
                                    "Choose a category for at least one merchant.")
            return
        try:
            tx = self.store.transactions()
            choices = {}
            for cluster, category in picks:
                rows = self._matching_rows(cluster, tx)
                choices.update({int(i): category for i in rows.id})
            if not choices:
                raise ValueError('These entries have changed or were already reviewed. Reload the review window.')
            result = self.store.review_transactions(list(choices), choices, remember=self.remember.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, 'Categories were not applied', str(exc))
            return
        self._reload()
        self.status.setText(f"Reviewed {result['reviewed']} transactions. "
            + ('Remembered for future imports.' if result['remembered'] else 'Future import rules unchanged.'))
