"""Transactions page — the ledger, filterable and correctable.

Re-categorising is the main interaction here, and it is deliberately available
three ways: pick a new category straight from the row's Category cell,
right-click a selection, or use the footer control for a large batch. Every
merchant correction is written to the feedback table via ``learning.py`` as a
training label. Reimbursable business is a per-transaction exception, so it
does not teach the importer to classify all travel from a merchant as business.
"""
from __future__ import annotations

import pandas as pd
from PyQt6.QtCore import QDate, QEvent, QItemSelectionModel, QModelIndex, Qt
from PyQt6.QtGui import QAction, QBrush, QColor
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMenu, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from ..categorize import BUSINESS_CATEGORY, PERSONAL_EXCLUSIONS, RULES
from ..learning import coverage, record_corrections
from .review_dialog import ReviewUncategorizedDialog
from ..store import Store
from ..util import fmt_date, fmt_money
from . import theme
from .widgets import FlowLayout, hint
from .account_dialog import _money_spin

# How many rows to put on screen at once. Every row carries a live QComboBox
# for its category, so "just render them all" is not free: a few thousand rows
# is thousands of widgets to build before the page will paint, and the list is
# then far too long to be read as a list. Recent activity is what people
# actually come here for; older rows are a search away.
PAGE_SIZE = 25
_ALL_CATS = sorted({cat for _, cat in RULES} |
                   {"Other income", "Misc", "Transfer", BUSINESS_CATEGORY})

_SINCE_CHOICES = [("Last 30 days", 30), ("Last 90 days", 90),
                  ("Last 12 months", 365), ("All time", None),
                  ("Since date…", "custom")]


class BusinessReimbursementDialog(QDialog):
    def __init__(self, deposit: float, reimbursement: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Business reimbursement in this deposit")
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        explanation = hint(
            f"Bank deposit: {fmt_money(deposit)}. Enter the part your employer "
            "repaid for business expenses. The remainder counts as personal income. "
            "Your bank deposit stays unchanged. Enter zero to remove the split.")
        form.addRow(explanation)
        self.amount = _money_spin(0, deposit)
        self.amount.setValue(reimbursement)
        form.addRow("Business reimbursement", self.amount)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


def all_categories(store: Store | None = None, transactions=None) -> list[str]:
    """Built-in categories plus any the user has actually used."""
    cats = set(_ALL_CATS)
    if store is not None:
        try:
            df = store.transactions() if transactions is None else transactions
            if not df.empty:
                cats |= {c for c in df["category"].dropna().unique() if c}
        except Exception:
            pass
        try:
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
        self._load_error: Exception | None = None
        self._shown = PAGE_SIZE

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        controls = QVBoxLayout()
        controls.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        imp = QPushButton("Import CSV…")
        imp.setObjectName("primary")
        imp.clicked.connect(lambda: self.main.open_import())
        review = QPushButton("Review uncategorized…")
        review.setToolTip("Choose categories for uncategorized transactions, grouped by merchant.")
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
        self.search.setMinimumWidth(220)
        self.search.setAccessibleName("Search descriptions")
        bar.addWidget(imp)
        bar.addWidget(review)
        search_label = QLabel("Search")
        search_label.setBuddy(self.search)
        bar.addWidget(search_label)
        bar.addWidget(self.search, 1)
        controls.addLayout(bar)

        # Keep actions separate from filters. Whole label/control groups wrap
        # together when the custom date or a smaller window needs another row.
        filters = FlowLayout(spacing=16)
        for text, combo, length in (("Account", self.f_account, 14),
                                    ("Category", self.f_category, 18),
                                    ("Period", self.f_since, 14)):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(length)
            combo.currentTextChanged.connect(combo.setToolTip)
            group = QWidget()
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            label = QLabel(text)
            label.setBuddy(combo)
            combo.setAccessibleName(text)
            row.addWidget(label)
            row.addWidget(combo)
            if combo is self.f_since:
                self.since_date.setAccessibleName("Show transactions since date")
                row.addWidget(self.since_date)
            filters.addWidget(group)
        controls.addLayout(filters)
        lay.addLayout(controls)
        lay.addSpacing(8)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        for col in range(len(self.COLS)):
            align = (Qt.AlignmentFlag.AlignRight if col == 4
                     else Qt.AlignmentFlag.AlignLeft)
            self.table.horizontalHeaderItem(col).setTextAlignment(
                align | Qt.AlignmentFlag.AlignVCenter)
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
                                  QHeaderView.ResizeMode.Fixed,
                                  QHeaderView.ResizeMode.Stretch,
                                  QHeaderView.ResizeMode.Fixed,
                                  QHeaderView.ResizeMode.ResizeToContents]):
            hdr.setSectionResizeMode(i, mode)
        self.table.setWordWrap(False)
        lay.addWidget(self.table, 1)

        self.empty_state = QWidget(self.table.viewport())
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(16, 16, 16, 16)
        empty_layout.setSpacing(12)
        self.empty_message = QLabel("")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message.setWordWrap(True)
        empty_layout.addWidget(self.empty_message)
        self.empty_action = QPushButton("")
        self.empty_action.setObjectName("small")
        self.empty_action.clicked.connect(self._empty_action_clicked)
        empty_layout.addWidget(self.empty_action, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.empty_state.hide()
        self._empty_mode = ""
        self.table.viewport().installEventFilter(self)

        self.category_help = hint(
            "Change a category in its row, or select several rows and choose Set category. "
            "Doug remembers ordinary corrections for future imports from that merchant.\n\n"
            "Use Business (reimbursable) for work expenses and separate repayment deposits. "
            "These entries stay out of your spending score and forecast history. "
            "For a repayment included in a paycheck, select the deposit and choose "
            "Separate reimbursement. Business labels apply only to the selected entries.")
        self.category_help.setVisible(False)

        more_row = QHBoxLayout()
        more_row.setSpacing(8)
        self.count_lbl = QLabel("")
        self.count_lbl.setWordWrap(True)
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

        self.bulk_actions = QWidget()
        foot = QHBoxLayout(self.bulk_actions)
        foot.setContentsMargins(0, 4, 0, 4)
        foot.setSpacing(8)
        self.selected_lbl = QLabel("")
        foot.addWidget(self.selected_lbl, 1)
        category_label = QLabel("Category")
        foot.addWidget(category_label)
        self.set_cat = QComboBox()
        self.set_cat.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.set_cat.setMinimumContentsLength(18)
        self.set_cat.setMaximumWidth(280)
        self.set_cat.currentTextChanged.connect(self.set_cat.setToolTip)
        self.set_cat.setPlaceholderText("Choose category…")
        self.set_cat.addItems(self._cats)
        self.set_cat.setCurrentIndex(-1)
        apply_btn = QPushButton("Set category")
        self.apply_category_btn = apply_btn
        apply_btn.setEnabled(False)
        category_label.setBuddy(self.set_cat)
        self.set_cat.setAccessibleName("Category for selected transactions")
        self.set_cat.currentIndexChanged.connect(self._sync_selection_controls)
        apply_btn.setObjectName("small")
        apply_btn.clicked.connect(self._apply_category)
        foot.addWidget(self.set_cat)
        foot.addWidget(apply_btn)
        lay.addWidget(self.bulk_actions)

        self.reimbursement_actions = QWidget()
        reimbursement_row = QHBoxLayout(self.reimbursement_actions)
        reimbursement_row.setContentsMargins(0, 0, 0, 0)
        reimbursement_row.setSpacing(8)
        self.reimbursement_note = hint("")
        reimbursement_row.addWidget(self.reimbursement_note, 1)
        self.reimbursement_btn = QPushButton("Separate reimbursement…")
        self.reimbursement_btn.setObjectName("small")
        self.reimbursement_btn.clicked.connect(self._edit_selected_reimbursement)
        reimbursement_row.addWidget(self.reimbursement_btn)
        self.reimbursement_actions.hide()
        lay.addWidget(self.reimbursement_actions)

        help_row = QHBoxLayout()
        help_row.setSpacing(8)
        self.learned_lbl = hint("")
        help_row.addWidget(self.learned_lbl, 1)
        self.category_help_btn = QPushButton("Category help")
        self.category_help_btn.setObjectName("link")
        self.category_help_btn.setCheckable(True)
        self.category_help_btn.toggled.connect(self._toggle_category_help)
        help_row.addWidget(self.category_help_btn)
        lay.addLayout(help_row)
        lay.addWidget(self.category_help)
        self.table.itemSelectionChanged.connect(self._sync_selection_controls)
        self._sync_selection_controls()

        # Any change to what is being filtered starts the page count over —
        # carrying "showing 300" across into a different filter would silently
        # dump hundreds of rows onto a search the user expected to narrow things.
        for w in (self.f_account, self.f_category, self.f_since):
            w.currentIndexChanged.connect(self._refilter)
        self.since_date.dateChanged.connect(self._refilter)
        self.search.textChanged.connect(self._refilter)

    # ---------------------------------------------------------------
    def reset_for_store(self) -> None:
        """Do not carry transaction IDs or pending edits into another ledger."""
        self._df = pd.DataFrame()
        self._load_error = None
        self._shown = PAGE_SIZE
        self.table.clearSelection()
        self.table.setRowCount(0)
        self._cats = list(_ALL_CATS)
        for combo, text in ((self.f_account, "All accounts"),
                            (self.f_category, "All categories")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(text, None)
            combo.blockSignals(False)
            combo.setToolTip(text)
        for widget in (self.f_since, self.search, self.set_cat):
            widget.blockSignals(True)
            if widget is self.search:
                widget.clear()
            elif widget is self.f_since:
                widget.setCurrentIndex(2)
                widget.setToolTip(widget.currentText())
            else:
                widget.clear()
                widget.addItems(self._cats)
                widget.setCurrentIndex(-1)
                widget.setToolTip("")
            widget.blockSignals(False)
        self.since_date.hide()
        self.learned_lbl.clear()
        self.category_help_btn.setChecked(False)
        self._render()

    def _toggle_category_help(self, visible: bool) -> None:
        self.category_help.setVisible(visible)
        self.category_help_btn.setText("Hide category help" if visible
                                       else "Category help")

    def _sync_selection_controls(self, *_args) -> None:
        if self._suspend_cell_signal:
            return
        count = len(self._selected_ids())
        self.bulk_actions.setVisible(count > 0)
        self.selected_lbl.setText(f"{count} transaction{'s' if count != 1 else ''} selected")
        self.apply_category_btn.setEnabled(count > 0 and self.set_cat.currentIndex() >= 0)
        deposit = self._selected_deposit()
        self.reimbursement_actions.setVisible(deposit is not None)
        if deposit is not None:
            reimbursement = float(deposit.get("business_reimbursement", 0))
            if reimbursement > 0:
                self.reimbursement_note.setText(
                    f"Personal income {fmt_money(float(deposit['amount']) - reimbursement)} "
                    f"· reimbursement {fmt_money(reimbursement)}")
                self.reimbursement_btn.setText("Edit reimbursement…")
            else:
                self.reimbursement_note.setText("For a business repayment included in this deposit.")
                self.reimbursement_btn.setText("Separate reimbursement…")

    def _selected_deposit(self):
        ids = self._selected_ids()
        if len(ids) != 1 or self._df.empty:
            return None
        rows = self._df[self._df["id"] == ids[0]]
        if rows.empty:
            return None
        row = rows.iloc[0]
        if row["amount"] > 0 and row["category"] not in PERSONAL_EXCLUSIONS:
            return row
        return None

    def _edit_selected_reimbursement(self) -> None:
        deposit = self._selected_deposit()
        if deposit is not None:
            self._edit_business_reimbursement(int(deposit["id"]))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns()
        self._position_empty_state()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._position_empty_state()
        return super().eventFilter(watched, event)

    def _position_empty_state(self) -> None:
        width = min(460, max(0, self.table.viewport().width() - 32))
        self.empty_state.setFixedWidth(width)
        self.empty_state.adjustSize()
        self.empty_state.move(
            max(0, (self.table.viewport().width() - width) // 2),
            max(0, (self.table.viewport().height() - self.empty_state.height()) // 2))

    def _show_empty_state(self, mode: str, message: str, action: str) -> None:
        self._empty_mode = mode
        self.empty_message.setText(message)
        self.empty_action.setText(action)
        self._position_empty_state()
        self.empty_state.show()

    def _empty_action_clicked(self) -> None:
        if self._empty_mode == "new":
            self.main.open_import()
        elif self._empty_mode == "error":
            self.refresh()
        else:
            self._clear_filters()

    def _clear_filters(self) -> None:
        for widget in (self.f_account, self.f_category, self.f_since, self.search):
            previous = widget.blockSignals(True)
            if widget is self.search:
                widget.clear()
            elif widget is self.f_since:
                widget.setCurrentIndex(widget.findData(None))
            else:
                widget.setCurrentIndex(0)
            widget.blockSignals(previous)
            if isinstance(widget, QComboBox):
                widget.setToolTip(widget.currentText())
        self._refilter()

    def _show_display_error(self, error: Exception) -> None:
        self._load_error = error
        self._df = pd.DataFrame()
        self._suspend_cell_signal = False
        self.table.setRowCount(0)
        self._sync_selection_controls()
        self.more_btn.hide()
        self.all_btn.hide()
        self.count_lbl.setText("Transactions could not be shown.")
        self._show_empty_state("error", "Transactions could not be shown. Try loading them again.",
                               "Retry")
        self.empty_message.setToolTip(str(error))

    def _show_action_error(self, title: str, error: Exception, message: str | None = None) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(title)
        box.setInformativeText(message or "Review the ledger before trying again. "
                               "If the problem continues, close and reopen Doug.")
        box.setDetailedText(str(error))
        try:
            box.exec()
        finally:
            box.deleteLater()

    def _fit_columns(self) -> None:
        """Reserve description and amount space even with long account names."""
        width = self.table.viewport().width()
        self.table.setColumnWidth(1, max(110, min(170, int(width * .16))))
        self.table.setColumnWidth(self.CAT_COL,
                                  max(190, min(240, int(width * .25))))

    def _refilter(self) -> None:
        """A filter changed: show the newest page again, not the old depth."""
        self.since_date.setVisible(self.f_since.currentData() == "custom")
        self.table.clearSelection()
        self._shown = PAGE_SIZE
        self._render()

    def _show_more(self) -> None:
        self._shown += PAGE_SIZE
        self._render()

    def _show_all(self) -> None:
        self._shown = None
        self._render()

    def refresh(self) -> bool:
        try:
            self._refresh_inner()
        except Exception as e:                      # never abort the app
            self._show_display_error(e)
        return self._load_error is None

    def _refresh_inner(self) -> None:
        accounts = self.store.accounts()
        self._accounts = {a.id: a.name for a in accounts}
        self._account_types = {a.id: a.type.value for a in accounts}
        self._df = self.store.transactions()
        self._cats = all_categories(self.store, self._df)

        cur_set = self.set_cat.currentText()
        self.set_cat.blockSignals(True)
        self.set_cat.clear()
        self.set_cat.addItems(self._cats)
        j = self.set_cat.findText(cur_set)
        self.set_cat.setCurrentIndex(j if j >= 0 else -1)
        self.set_cat.blockSignals(False)
        self.set_cat.setToolTip(self.set_cat.currentText())
        self._sync_selection_controls()

        acct_items = [("All accounts", None)] + [(n, i) for i, n
                                                 in self._accounts.items()]
        if self._df.empty:
            cat_items = [("All categories", None)]
        else:
            seen = sorted({c for c in self._df["category"].dropna().unique()})
            current_category = self.f_category.currentData()
            if current_category and current_category not in seen:
                seen = sorted([*seen, current_category])
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
            combo.setToolTip(combo.currentText())

        try:
            cov = coverage(self.store)
            self.learned_lbl.setText(
                f"{cov['labels']} correction{'s' if cov['labels'] != 1 else ''} saved for future imports"
                if cov["labels"] else "")
            self.learned_lbl.setToolTip("")
        except Exception as e:
            self.learned_lbl.setText("Saved import preferences could not be loaded.")
            self.learned_lbl.setToolTip(str(e))
        self._load_error = None
        self._render()

    def _render(self) -> None:
        if self._load_error is not None:
            self._show_display_error(self._load_error)
            return
        selection_model = self.table.selectionModel()
        blocked = selection_model.blockSignals(True)
        table_blocked = self.table.blockSignals(True)
        updates = self.table.updatesEnabled()
        self.table.setUpdatesEnabled(False)
        try:
            self._render_inner()
        except Exception as e:
            self._show_display_error(e)
        finally:
            self._suspend_cell_signal = False
            selection_model.blockSignals(blocked)
            self.table.blockSignals(table_blocked)
            self.table.setUpdatesEnabled(updates)
            self._sync_selection_controls()

    def show_category(self, category: str, days: int | None = None) -> None:
        """Open on one category, optionally limited to the last ``days``.

        Used by the dashboard's category chart: a bar is only worth clicking if
        it leads to the transactions it adds up.
        """
        i = self.f_category.findData(category)
        if i >= 0:
            self.f_category.setCurrentIndex(i)
        if days is not None:
            j = self.f_since.findData(days)
            if j >= 0:
                self.f_since.setCurrentIndex(j)
        self.f_account.setCurrentIndex(0)
        self.search.clear()
        self._render()

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
        selected = set(self._selected_ids())
        current = self.table.item(self.table.currentRow(), 0)
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.table.clearSelection()
        self.table.selectionModel().setCurrentIndex(QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)
        self.empty_state.hide()
        self.empty_message.setToolTip("")
        df = self._df
        if df.empty:
            self.table.setRowCount(0)
            self.count_lbl.setText("No imported transactions")
            self.more_btn.setVisible(False)
            self.all_btn.setVisible(False)
            self._show_empty_state("new", "Import a bank CSV to see your transactions here.",
                                   "Import CSV…")
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
        cutoff = self._since_cutoff()
        undated = int(df["date"].isna().sum())
        older = int((df["date"] < cutoff).sum()) if cutoff is not None else 0
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
            a.setToolTip(a.text())
            desc.setToolTip(desc.text())
            amount = float(row["amount"]) if pd.notna(row["amount"]) else 0.0
            amt = QTableWidgetItem(fmt_money(amount, signed=True))
            amt.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
            amt.setForeground(pos if amount >= 0 else neg)
            reimbursement = float(row.get("business_reimbursement", 0))
            if reimbursement > 0:
                amt.setToolTip(f"Includes {fmt_money(reimbursement)} business reimbursement. "
                               f"Personal amount: {fmt_money(amount - reimbursement)}.")
                desc.setText(f"{row['description']}\nIncludes {fmt_money(reimbursement)} reimbursement")
                desc.setToolTip(f"{row['description']}\n{amt.toolTip()}")
            self.table.setRowHeight(r, 56 if reimbursement > 0 else 38)
            for col, item in zip((0, 1, 2, 4), (d, a, desc, amt)):
                self.table.setItem(r, col, item)
            self.table.setCellWidget(r, self.CAT_COL,
                                     self._category_editor(row))
            selection_model = self.table.selectionModel()
            index = self.table.model().index(r, 0)
            if int(row["id"]) in selected:
                selection_model.select(index, QItemSelectionModel.SelectionFlag.Select |
                                       QItemSelectionModel.SelectionFlag.Rows)
            if int(row["id"]) == current_id:
                selection_model.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self._suspend_cell_signal = False
        self._fit_columns()
        self._sync_selection_controls()

        shown = len(df)
        remaining = total - shown
        self.more_btn.setVisible(remaining > 0)
        self.all_btn.setVisible(remaining > PAGE_SIZE)
        self.more_btn.setText(f"Show {min(PAGE_SIZE, remaining)} more")
        self.all_btn.setText(f"Show all {total}")
        parts = [f"showing {shown} of {total}" if remaining
                 else f"{total} transaction{'s' if total != 1 else ''}",
                 f"net cash flow {fmt_money(net, signed=True)}"]
        if cutoff is not None and older:
            parts.append(f"{older} older than "
                         f"{cutoff.date().isoformat()} hidden")
        if cutoff is not None and undated:
            parts.append(f"{undated} without a date hidden")
        self.count_lbl.setText(" · ".join(parts))
        self.count_lbl.setToolTip("Net cash flow adds all amounts matching these filters, "
                                 "including rows beyond the first page and business entries.")
        if total == 0:
            self._show_empty_state("filtered", "No transactions match these filters.",
                                   "Clear filters")

    def _category_editor(self, row) -> QComboBox:
        """A per-row category picker — the direct way to fix one mislabel."""
        box = QComboBox()
        box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.addItems(self._cats)
        current = str(row["category"])
        i = box.findText(current)
        if i < 0:
            box.insertItem(0, current)
            i = 0
        box.setCurrentIndex(i)
        box.setToolTip(current)
        box.setProperty("tx_id", int(row["id"]))
        box.setProperty("previous", current)
        box._ledger = self.store
        box.currentTextChanged.connect(
            lambda text, b=box: self._on_cell_category_changed(b, text))
        return box

    def _on_cell_category_changed(self, box: QComboBox, text: str) -> None:
        if self._suspend_cell_signal or box._ledger is not self.store:
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
        menu_store = self.store
        if len(ids) == 1:
            rows = self._df[self._df["id"] == ids[0]] if not self._df.empty else pd.DataFrame()
            if rows.empty:
                menu.deleteLater()
                self.refresh()
                return
            row = rows.iloc[0]
            if row["amount"] > 0 and row["category"] not in PERSONAL_EXCLUSIONS:
                action = menu.addAction("Edit reimbursement…" if row.get("business_reimbursement", 0) > 0
                                        else "Separate reimbursement…")
                action.triggered.connect(lambda _checked=False, tx_id=ids[0]:
                                         self._for_store(menu_store,
                                             lambda: self._edit_business_reimbursement(tx_id)))
                menu.addSeparator()
        head = menu.addAction(f"Set category for {len(ids)} transaction"
                              f"{'s' if len(ids) != 1 else ''}")
        head.setEnabled(False)
        menu.addSeparator()
        for cat in self._cats:
            act = QAction(cat, menu)
            act.triggered.connect(
                lambda _c=False, c=cat, i=list(ids): self._for_store(menu_store,
                    lambda: self._recategorize(i, c)))
            menu.addAction(act)
        try:
            menu.exec(self.table.viewport().mapToGlobal(point))
        finally:
            menu.deleteLater()

    def _for_store(self, store: Store, action) -> None:
        if self.store is store:
            action()
        else:
            self.main.status("The ledger changed. Select the transactions again.")

    def _fresh_rows(self, tx_ids: list[int]) -> pd.DataFrame:
        """Verify an action against stored rows, rather than an old display."""
        fresh = self.store.transactions()
        rows = fresh[fresh["id"].isin(tx_ids)]
        if len(rows) != len(set(tx_ids)):
            raise ValueError("Some selected transactions are no longer available. "
                             "Refresh the ledger and select them again.")
        return rows

    def _refresh_after_action(self) -> bool:
        loaded = self.refresh()
        try:
            self.main.refresh_header()
        except Exception:
            # Even if the financial summary cannot be read, expose the undo
            # step for a write that completed before the read failed.
            sync_undo = getattr(self.main, "_sync_undo", None)
            if sync_undo is not None:
                sync_undo()
            return False
        return loaded

    def _edit_business_reimbursement(self, tx_id: int) -> None:
        dialog = None
        dialog_store = self.store
        try:
            row = self._fresh_rows([tx_id]).iloc[0]
            if row["amount"] <= 0 or row["category"] in PERSONAL_EXCLUSIONS:
                raise ValueError("Select a personal income deposit to separate its reimbursement.")
            dialog = BusinessReimbursementDialog(float(row["amount"]),
                float(row.get("business_reimbursement", 0)), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if self.store is not dialog_store:
                raise ValueError("The ledger changed while this dialog was open. "
                                 "Select the deposit again.")
            value = dialog.amount.value()
            self.store.set_business_reimbursement(tx_id, value)
        except Exception as e:
            self._refresh_after_action()
            self._show_action_error("Could not save reimbursement", e)
            return
        finally:
            if dialog is not None:
                dialog.deleteLater()
        if not self._refresh_after_action():
            self.main.status("Reimbursement saved, but the view could not be refreshed.")
        elif value > 0:
            self.main.status(f"{fmt_money(value)} reimbursement separated. "
                             "Bank deposit unchanged.")
        else:
            self.main.status("Reimbursement split removed. The full deposit counts as personal income.")

    def _review_uncategorized(self) -> None:
        dlg = None
        try:
            dlg = ReviewUncategorizedDialog(self.store, self._cats, self)
            dlg.exec()
        except Exception as e:
            self._show_action_error("Could not open transaction review", e)
        finally:
            if dlg is not None:
                dlg.deleteLater()
            self._refresh_after_action()

    def _apply_category(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "Select transactions",
                                    "Select one or more transaction rows, then choose a category.")
            return
        self._recategorize(ids, self.set_cat.currentText())

    def _recategorize(self, tx_ids: list[int], category: str) -> None:
        """Single path for every re-categorisation route: write the label,
        record the correction as training data, refresh."""
        if not tx_ids or not category:
            return
        category_saved = False
        try:
            rows = self._fresh_rows(tx_ids)
            changed = rows[rows["category"] != category]
            if changed.empty:
                self._refresh_after_action()
                self.main.status(f"Selected transactions already use “{category}”.")
                return
            with self.store.undo_group(f"Set category to {category}"):
                self.store.set_category(changed["id"].astype(int).tolist(), category)
                category_saved = True
                n_labels = record_corrections(
                    self.store, changed, category,
                    account_types=self._account_types, source="manual")
        except Exception as e:
            self._refresh_after_action()
            if category_saved:
                self._show_action_error("Category changed; import preference not saved", e,
                    "Use Undo to reverse the category change, then try again.")
            else:
                self._show_action_error("Could not finish category change", e)
            return
        if not self._refresh_after_action():
            self.main.status("Category saved, but the view could not be refreshed.")
            return
        note = f"Category set to “{category}” for {len(changed)} transaction"
        note += "s." if len(changed) != 1 else "."
        if n_labels:
            note += " Saved for future imports from matching merchants."
        elif category == BUSINESS_CATEGORY:
            note += " Business label applies only to these entries."
        self.main.status(note)
