"""Transactions page — the ledger, filterable and correctable.

Re-categorising is the main interaction here, and it is deliberately available
three ways: pick a new category straight from the row's Category cell,
right-click a selection, or use the footer control for a large batch. Every
correction is written to the feedback table via ``learning.py`` as a training
label, so the importer stops making the same mistake and a classifier has a
corpus to learn from later.
"""
from __future__ import annotations

import pandas as pd
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QAction, QBrush, QColor
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMenu, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..categorize import RULES
from ..learning import coverage, record_corrections
from .review_dialog import ReviewUncategorizedDialog
from ..store import Store
from ..util import fmt_date, fmt_money
from . import theme
from .widgets import hint

# How many rows to put on screen at once. Every row carries a live QComboBox
# for its category, so "just render them all" is not free: a few thousand rows
# is thousands of widgets to build before the page will paint, and the list is
# then far too long to be read as a list. Recent activity is what people
# actually come here for; older rows are a search away.
PAGE_SIZE = 25
_ALL_CATS = sorted({cat for _, cat in RULES} | {"Other income", "Misc", "Transfer"})

_SINCE_CHOICES = [("Last 30 days", 30), ("Last 90 days", 90),
                  ("Last 12 months", 365), ("All time", None),
                  ("Since date…", "custom")]


def all_categories(store: Store | None = None) -> list[str]:
    """Built-in categories plus any the user has actually used."""
    cats = set(_ALL_CATS)
    if store is not None:
        try:
            df = store.transactions()
            if not df.empty:
                cats |= {c for c in df["category"].dropna().unique() if c}
            cats |= set(store.learned_categories().values())
        except Exception:
            pass
    return sorted(cats)


class TransactionsPage(QWidget):
    COLS = ["Date", "Account", "Description", "Category", "Amount"]
    CAT_COL = 3

    def __init__(self, store: Store, main):
        super().__init__()
        self.setObjectName("page")
        self.store = store
        self.main = main
        self._df = pd.DataFrame()
        self._accounts: dict[int, str] = {}
        self._account_types: dict[int, str] = {}
        self._cats: list[str] = list(_ALL_CATS)
        self._suspend_cell_signal = False
        self._shown = PAGE_SIZE

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        imp = QPushButton("Import CSV…")
        imp.setObjectName("primary")
        imp.clicked.connect(lambda: self.main.open_import())
        review = QPushButton("Review uncategorized…")
        review.setToolTip("Label whole merchants at once, biggest dollars first")
        review.clicked.connect(self._review_uncategorized)
        self.f_account = QComboBox()
        self.f_category = QComboBox()
        self.f_since = QComboBox()
        for label, val in _SINCE_CHOICES:
            self.f_since.addItem(label, val)
        self.f_since.setCurrentIndex(2)          # last 12 months
        self.since_date = QDateEdit(QDate.currentDate().addMonths(-3))
        self.since_date.setCalendarPopup(True)
        self.since_date.setDisplayFormat("yyyy-MM-dd")
        self.since_date.setVisible(False)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search descriptions…")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(imp)
        bar.addWidget(review)
        bar.addSpacing(10)
        bar.addWidget(QLabel("Account"))
        bar.addWidget(self.f_account)
        bar.addWidget(QLabel("Category"))
        bar.addWidget(self.f_category)
        bar.addWidget(QLabel("Show"))
        bar.addWidget(self.f_since)
        bar.addWidget(self.since_date)
        bar.addWidget(self.search, 1)
        lay.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        vh = self.table.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(38)      # taller rows so descriptions and the inline category picker aren't clipped
        vh.setMinimumSectionSize(38)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        hdr = self.table.horizontalHeader()
        for i, mode in enumerate([QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents]):
            hdr.setSectionResizeMode(i, mode)
        lay.addWidget(self.table, 1)

        lay.addWidget(hint(
            "Wrong category? Change it in the Category column, or right-click a "
            "selection. Corrections are remembered — future imports of the same "
            "merchant are labelled the way you fixed them."))

        more_row = QHBoxLayout()
        self.count_lbl = hint("")
        more_row.addWidget(self.count_lbl, 1)
        self.more_btn = QPushButton(f"Show {PAGE_SIZE} more")
        self.more_btn.setObjectName("small")
        self.more_btn.clicked.connect(self._show_more)
        self.all_btn = QPushButton("Show all")
        self.all_btn.setObjectName("small")
        self.all_btn.clicked.connect(self._show_all)
        more_row.addWidget(self.more_btn)
        more_row.addWidget(self.all_btn)
        lay.addLayout(more_row)

        foot = QHBoxLayout()
        self.learned_lbl = hint("")
        foot.addWidget(self.learned_lbl, 1)
        foot.addSpacing(12)
        foot.addWidget(QLabel("Set category for selected:"))
        self.set_cat = QComboBox()
        self.set_cat.addItems(self._cats)
        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("small")
        apply_btn.clicked.connect(self._apply_category)
        foot.addWidget(self.set_cat)
        foot.addWidget(apply_btn)
        lay.addLayout(foot)

        # Any change to what is being filtered starts the page count over —
        # carrying "showing 300" across into a different filter would silently
        # dump hundreds of rows onto a search the user expected to narrow things.
        for w in (self.f_account, self.f_category, self.f_since):
            w.currentIndexChanged.connect(self._refilter)
        self.since_date.dateChanged.connect(self._refilter)
        self.search.textChanged.connect(self._refilter)

    # ---------------------------------------------------------------
    def _refilter(self) -> None:
        """A filter changed: show the newest page again, not the old depth."""
        self.since_date.setVisible(self.f_since.currentData() == "custom")
        self._shown = PAGE_SIZE
        self._render()

    def _show_more(self) -> None:
        self._shown += PAGE_SIZE
        self._render()

    def _show_all(self) -> None:
        self._shown = None
        self._render()

    def refresh(self) -> None:
        try:
            self._refresh_inner()
        except Exception as e:                      # never abort the app
            self.table.setRowCount(0)
            self.count_lbl.setText(f"Could not display transactions: {e}")

    def _refresh_inner(self) -> None:
        accounts = self.store.accounts()
        self._accounts = {a.id: a.name for a in accounts}
        self._account_types = {a.id: a.type.value for a in accounts}
        self._df = self.store.transactions()
        self._cats = all_categories(self.store)

        cur_set = self.set_cat.currentText()
        self.set_cat.blockSignals(True)
        self.set_cat.clear()
        self.set_cat.addItems(self._cats)
        j = self.set_cat.findText(cur_set)
        self.set_cat.setCurrentIndex(j if j >= 0 else 0)
        self.set_cat.blockSignals(False)

        acct_items = [("All accounts", None)] + [(n, i) for i, n
                                                 in self._accounts.items()]
        if self._df.empty:
            cat_items = [("All categories", None)]
        else:
            seen = sorted({c for c in self._df["category"].dropna().unique()})
            cat_items = [("All categories", None)] + [(c, c) for c in seen]
        for combo, items in ((self.f_account, acct_items),
                             (self.f_category, cat_items)):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for label, val in items:
                combo.addItem(label, val)
            i = combo.findData(cur)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)

        cov = coverage(self.store)
        self.learned_lbl.setText(
            f"{cov['labels']} correction{'s' if cov['labels'] != 1 else ''} learned"
            if cov["labels"] else "")
        self._render()

    def _render(self) -> None:
        try:
            self._render_inner()
        except Exception as e:
            self.table.setRowCount(0)
            self.count_lbl.setText(f"Could not display transactions: {e}")

    def _since_cutoff(self) -> pd.Timestamp | None:
        """The oldest date to display, or None for no limit."""
        v = self.f_since.currentData()
        if v is None:
            return None
        if v == "custom":
            d = self.since_date.date()
            return pd.Timestamp(d.year(), d.month(), d.day())
        return pd.Timestamp.today().normalize() - pd.Timedelta(days=int(v))

    def _render_inner(self) -> None:
        df = self._df
        if df.empty:
            self.table.setRowCount(0)
            self.count_lbl.setText("No transactions yet — import a CSV to begin.")
            self.more_btn.setVisible(False)
            self.all_btn.setVisible(False)
            return
        acct = self.f_account.currentData()
        cat = self.f_category.currentData()
        q = self.search.text().strip().lower()
        if acct is not None:
            df = df[df["account_id"] == acct]
        if cat is not None:
            df = df[df["category"] == cat]
        if q:
            df = df[df["description"].fillna("").str.lower()
                    .str.contains(q, regex=False, na=False)]
        matched = len(df)
        cutoff = self._since_cutoff()
        if cutoff is not None:
            df = df[df["date"].notna() & (df["date"] >= cutoff)]
        total = len(df)
        net = df["amount"].sum()
        df = df.sort_values("date", ascending=False, na_position="last")
        if self._shown is not None:
            df = df.head(self._shown)

        self._suspend_cell_signal = True
        self.table.setRowCount(len(df))
        pos = QBrush(QColor(theme.POSITIVE))
        neg = QBrush(QColor(theme.NEGATIVE))
        for r, (_, row) in enumerate(df.iterrows()):
            d = QTableWidgetItem(fmt_date(row["date"]))
            d.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            a = QTableWidgetItem(self._accounts.get(row["account_id"], "?"))
            desc = QTableWidgetItem(str(row["description"]))
            amount = float(row["amount"]) if pd.notna(row["amount"]) else 0.0
            amt = QTableWidgetItem(fmt_money(amount, signed=True))
            amt.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            amt.setForeground(pos if amount >= 0 else neg)
            for col, item in zip((0, 1, 2, 4), (d, a, desc, amt)):
                self.table.setItem(r, col, item)
            self.table.setCellWidget(r, self.CAT_COL,
                                     self._category_editor(row))
        self._suspend_cell_signal = False

        shown = len(df)
        remaining = total - shown
        self.more_btn.setVisible(remaining > 0)
        self.all_btn.setVisible(remaining > PAGE_SIZE)
        self.more_btn.setText(f"Show {min(PAGE_SIZE, remaining)} more")
        self.all_btn.setText(f"Show all {total}")
        parts = [f"showing {shown} of {total}" if remaining
                 else f"{total} transaction{'s' if total != 1 else ''}",
                 f"net {fmt_money(net, signed=True)}"]
        if cutoff is not None and matched > total:
            parts.append(f"{matched - total} older than "
                         f"{cutoff.date().isoformat()} hidden")
        self.count_lbl.setText(" · ".join(parts))

    def _category_editor(self, row) -> QComboBox:
        """A per-row category picker — the direct way to fix one mislabel."""
        box = QComboBox()
        box.addItems(self._cats)
        current = str(row["category"])
        i = box.findText(current)
        if i < 0:
            box.insertItem(0, current)
            i = 0
        box.setCurrentIndex(i)
        box.setProperty("tx_id", int(row["id"]))
        box.setProperty("previous", current)
        box.currentTextChanged.connect(
            lambda text, b=box: self._on_cell_category_changed(b, text))
        return box

    def _on_cell_category_changed(self, box: QComboBox, text: str) -> None:
        if self._suspend_cell_signal:
            return
        tx_id = box.property("tx_id")
        previous = box.property("previous")
        if tx_id is None or text == previous:
            return
        self._recategorize([int(tx_id)], text)

    # ---------------------------------------------------------------
    def _selected_ids(self) -> list[int]:
        ids = []
        for r in sorted({i.row() for i in self.table.selectedIndexes()}):
            item = self.table.item(r, 0)
            if item is not None:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _context_menu(self, point) -> None:
        ids = self._selected_ids()
        if not ids:
            row = self.table.rowAt(point.y())
            item = self.table.item(row, 0) if row >= 0 else None
            if item is None:
                return
            ids = [int(item.data(Qt.ItemDataRole.UserRole))]
        menu = QMenu(self)
        head = menu.addAction(f"Re-categorise {len(ids)} transaction"
                              f"{'s' if len(ids) != 1 else ''}")
        head.setEnabled(False)
        menu.addSeparator()
        for cat in self._cats:
            act = QAction(cat, self)
            act.triggered.connect(
                lambda _c=False, c=cat, i=list(ids): self._recategorize(i, c))
            menu.addAction(act)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def _review_uncategorized(self) -> None:
        dlg = ReviewUncategorizedDialog(self.store, self._cats, self)
        dlg.exec()
        self.refresh()
        self.main.refresh_current()

    def _apply_category(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Nothing selected",
                                    "Select one or more rows first.")
            return
        self._recategorize(ids, self.set_cat.currentText())

    def _recategorize(self, tx_ids: list[int], category: str) -> None:
        """Single path for every re-categorisation route: write the label,
        record the correction as training data, refresh."""
        if not tx_ids or not category:
            return
        try:
            rows = self._df[self._df["id"].isin(tx_ids)]
            changed = rows[rows["category"] != category]
            with self.store.undo_group(f"Set category to {category}"):
                self.store.set_category(tx_ids, category)
                n_labels = record_corrections(
                    self.store, changed, category,
                    account_types=self._account_types, source="manual")
        except Exception as e:
            QMessageBox.warning(self, "Could not update category", str(e))
            return
        self.refresh()
        self.main.refresh_header()
        note = f"Category set to “{category}” for {len(tx_ids)} transaction"
        note += "s." if len(tx_ids) != 1 else "."
        if n_labels:
            note += f" Learned {n_labels} correction{'s' if n_labels != 1 else ''}."
        self.main.status(note)
