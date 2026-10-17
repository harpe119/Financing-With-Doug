"""Transactions page — the ledger, filterable and correctable.

Re-categorising is the main interaction here, and it is deliberately available
three ways: pick a new category straight from the row's Category cell,
right-click a selection, or use the footer control for a large batch. Every
merchant correction is written to the feedback table via ``learning.py`` as a
training label. Reimbursable business is a per-transaction exception, so it
does not teach the importer to classify all travel from a merchant as business.
"""
from __future__ import annotations

from html import escape
from pathlib import Path
import os
import tempfile

import pandas as pd
from PyQt6.QtCore import QDate, QEvent, QItemSelectionModel, QModelIndex, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLayout,
                             QLabel, QLineEdit, QMenu, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QScrollArea, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                             QVBoxLayout, QWidget)

from ..categorize import BUSINESS_CATEGORY, PERSONAL_EXCLUSIONS, RULES
from ..category_review import SOURCES, needs_attention, reviewed_dataset
from ..history import transaction_balances
from ..account_view import money_exact
from .review_dialog import ReviewUncategorizedDialog
from ..store import Store
from ..util import fmt_date, fmt_money
from . import theme
from .widgets import FlowLayout, InfoDialog, hint
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

_REVIEW_STATE = Qt.ItemDataRole.UserRole + 1
_ATTENTION = Qt.ItemDataRole.UserRole + 2


class LedgerTable(QTableWidget):
    edit_category = pyqtSignal()

    def focusNextPrevChild(self, forward):
        # QAbstractItemView otherwise wraps Tab around its cells forever.
        return QWidget.focusNextPrevChild(self, forward)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self.edit_category.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class CategoryPicker(QComboBox):
    """Open before choosing; scrolling or a closed-arrow key must not save."""
    def keyPressEvent(self, event):
        if (not self.view().isVisible() and event.key() in
                (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Space,
                 Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


class DescriptionDelegate(QStyledItemDelegate):
    """Keep merchant text and a short review state together, without a column."""
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ''
        style = opt.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(opt.palette.highlightedText().color() if selected else theme.qcolor(theme.TEXT))
        line = opt.fontMetrics.height()
        rect = opt.rect.adjusted(6, 4, -6, -line - 5)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter |
                         Qt.TextFlag.TextWordWrap, text)
        painter.setPen(opt.palette.highlightedText().color() if selected else
                       theme.qcolor(theme.AMBER if index.data(_ATTENTION) else theme.MUTED))
        painter.drawText(opt.rect.adjusted(6, opt.rect.height() - line - 4, -6, -3),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         index.data(_REVIEW_STATE) or '')
        painter.restore()

    def sizeHint(self, option, index):
        width = max(1, option.widget.columnWidth(index.column()) - 12)
        rect = option.fontMetrics.boundingRect(0, 0, width, 10000,
            Qt.TextFlag.TextWordWrap, str(index.data() or ''))
        return QSize(width, rect.height() + option.fontMetrics.height() + 12)


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


def _review_time(value):
    stamp = pd.to_datetime(value, errors='coerce')
    return stamp.strftime('%b %d, %Y at %H:%M') if pd.notna(stamp) else 'Unknown date'


class TransactionsPage(QWidget):
    COLS = ["Date", "Account", "Description", "Category", "Amount", "Account balance", "Category source"]
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
        self._balances = pd.DataFrame()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)
        lay = QVBoxLayout(self.content)
        lay.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        controls = QVBoxLayout()
        controls.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.attention_btn = QPushButton('Review entries')
        self.attention_btn.setObjectName('primary')
        self.attention_btn.clicked.connect(self._open_attention)
        self.tools_btn = QPushButton('Tools…')
        self.tools_menu = QMenu(self.tools_btn)
        self.tools_btn.setMenu(self.tools_menu)
        self.tools_menu.addAction('Import CSV…', lambda: self.main.open_import())
        self.tools_menu.addAction('Latest CSV import…', self._import_details)
        self.tools_menu.addAction('Review uncategorized merchants…', self._review_uncategorized)
        self.tools_menu.addSeparator()
        self.f_account = QComboBox()
        self.f_category = QComboBox()
        self.f_since = QComboBox()
        self.f_review = QComboBox()
        for label, value in [('All transactions', 'all'), ('Needs attention', 'attention'),
                             ('Not reviewed', 'unreviewed'), ('Reviewed by me', 'reviewed'),
                             ('Similar merchant matches', 'similarity')]:
            self.f_review.addItem(label, value)
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
        search_label = QLabel("Search")
        search_label.setBuddy(self.search)
        bar.addWidget(search_label)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.attention_btn)
        bar.addWidget(self.tools_btn)
        controls.addLayout(bar)

        # Keep actions separate from filters. Whole label/control groups wrap
        # together when the custom date or a smaller window needs another row.
        self.filter_host = QWidget()
        filters = FlowLayout(self.filter_host, spacing=12)
        for text, combo, length in (("Account", self.f_account, 14),
                                    ("Category", self.f_category, 18),
                                    ("Period", self.f_since, 14),
                                    ("Review", self.f_review, 18)):
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
        self.clear_btn = QPushButton('Clear filters')
        self.clear_btn.setObjectName('small')
        self.clear_btn.clicked.connect(self._clear_filters)
        filters.addWidget(self.clear_btn)
        controls.addWidget(self.filter_host)
        self.show_balances = QAction("Running account balances", self, checkable=True)
        self.show_sources = QAction('Category sources', self, checkable=True)
        self.tools_menu.addAction(self.show_balances)
        self.tools_menu.addAction(self.show_sources)
        self.show_sources.toggled.connect(lambda on: (self.table.setColumnHidden(6, not on), self._fit_columns()))
        self.show_balances.setToolTip("Calculate each cash account's balance after every transaction, using the full ledger.")
        self.balance_controls = QWidget()
        debug = QHBoxLayout(self.balance_controls)
        debug.setContentsMargins(0, 0, 0, 0)
        debug.setSpacing(12)
        self.negative_only = QCheckBox("Negative daily closes only")
        self.balance_order = QComboBox()
        self.balance_order.addItem("Newest first", "newest")
        self.balance_order.addItem("Oldest first", "oldest")
        self.balance_order.setAccessibleName("Transaction date order")
        self.duplicates_btn = QAction("Review duplicates…", self)
        self.duplicates_btn.setToolTip("Review possible duplicate postings; choose which entries to remove.")
        self.duplicates_btn.triggered.connect(self._review_duplicates)
        self.tools_menu.addAction(self.duplicates_btn)
        debug.addWidget(self.negative_only)
        debug.addWidget(self.balance_order)
        debug.addStretch(1)
        self.balance_controls.hide()
        controls.addWidget(self.balance_controls)
        balance_explanation = (
            "Balances use your dated balance and the full bank ledger, including transfers and business costs. "
            "Filters do not change them. Same-day rows use import order, which may differ from bank posting order. "
            "Negative credit-card balances mean money owed.")
        self.balance_note = hint('Balances use the full bank ledger and your dated balance. '
                                 '≈ means same-day order is unknown. Negative credit-card balances mean money owed.')
        self.balance_note.setToolTip(balance_explanation)
        self.balance_note.hide()
        controls.addWidget(self.balance_note)
        self.remember_category = QAction('Remember category changes for future imports', self, checkable=True)
        self.remember_category.setToolTip('Only category changes use this choice. Mark reviewed confirms the selected entries only. Business labels are always per transaction.')
        self.confirm_btn = QPushButton('Mark reviewed')
        self.confirm_btn.setObjectName('primary')
        self.confirm_btn.clicked.connect(self._confirm_selected)
        self.selection_more = QPushButton('More…')
        self.selection_menu = QMenu(self.selection_more)
        self.selection_more.setMenu(self.selection_menu)
        self.why_btn = self.selection_menu.addAction('Transaction details…', self._explain_selected)
        self.selection_menu.addAction(self.remember_category)
        self.remember_category.toggled.connect(lambda _: self._sync_selection_controls())
        lay.addLayout(controls)
        lay.addSpacing(8)

        self.table = LedgerTable(0, len(self.COLS))
        self.table.setTabKeyNavigation(False)
        self.table.setAccessibleName('Transactions')
        self.table.setAccessibleDescription('Use arrow keys to select transactions, Enter to choose a category, and Tab to leave the ledger.')
        self.table.edit_category.connect(self._edit_current_category)
        self.table.setItemDelegateForColumn(2, DescriptionDelegate(self.table))
        # Preserve a useful ledger below wrapped filters and balance controls,
        # including the slightly taller Windows native form metrics.
        self.table.setMinimumHeight(170)
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeaderItem(0).setToolTip('Posting / balance date. Transaction Date and bank Reference are available in Transaction details.')
        for col in range(len(self.COLS)):
            align = (Qt.AlignmentFlag.AlignRight if col in (4, 5)
                     else Qt.AlignmentFlag.AlignLeft)
            self.table.horizontalHeaderItem(col).setTextAlignment(
                align | Qt.AlignmentFlag.AlignVCenter)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        vh = self.table.verticalHeader()
        vh.setVisible(False)
        vh.setDefaultSectionSize(38)      # taller rows so descriptions and the inline category picker aren't clipped
        vh.setMinimumSectionSize(38)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        hdr = self.table.horizontalHeader()
        for i, mode in enumerate([QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.Fixed,
                                  QHeaderView.ResizeMode.Interactive,
                                  QHeaderView.ResizeMode.Fixed,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents,
                                  QHeaderView.ResizeMode.ResizeToContents]):
            hdr.setSectionResizeMode(i, mode)
        self.table.setColumnHidden(5, True)
        self.table.setColumnHidden(6, True)
        self.table.setWordWrap(True)
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
        summary = QVBoxLayout(self.bulk_actions)
        summary.setContentsMargins(0, 0, 0, 0)
        summary.setSpacing(4)
        self.selection_controls = QWidget()
        selection = FlowLayout(self.selection_controls, spacing=8)
        summary.addWidget(self.selection_controls)
        self.selected_lbl = QLabel('')
        selection.addWidget(self.selected_lbl)
        selection.addWidget(self.confirm_btn)
        self.scope_note = hint('Category changes apply only to the selected entries.')
        self.scope_note.setWordWrap(True)
        summary.addWidget(self.scope_note)
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
        self.set_cat.setAccessibleName("Category for selected transactions")
        self.set_cat.currentIndexChanged.connect(self._sync_selection_controls)
        apply_btn.setObjectName("small")
        apply_btn.clicked.connect(self._apply_category)
        selection.addWidget(self.set_cat)
        selection.addWidget(apply_btn)
        selection.addWidget(self.selection_more)
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

        self.learned_lbl = hint("")
        lay.addWidget(self.learned_lbl)
        self.export_labels_btn = QAction('Export reviewed data…', self)
        self.export_labels_btn.setToolTip('Save a local CSV of current human-reviewed labels, dates and merchant groups for later evaluation. Automatic guesses are excluded.')
        self.export_labels_btn.triggered.connect(self._export_reviewed)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.export_labels_btn)
        self.category_help_btn = self.tools_menu.addAction('Category help…', self._open_category_help)
        self.table.itemSelectionChanged.connect(self._sync_selection_controls)
        self._sync_selection_controls()
        self.content.installEventFilter(self)

        # Any change to what is being filtered starts the page count over —
        # carrying "showing 300" across into a different filter would silently
        # dump hundreds of rows onto a search the user expected to narrow things.
        for w in (self.f_account, self.f_category, self.f_since, self.f_review):
            w.currentIndexChanged.connect(self._refilter)
        self.since_date.dateChanged.connect(self._refilter)
        self.search.textChanged.connect(self._refilter)
        self.show_balances.toggled.connect(self._balance_view_changed)
        self.negative_only.toggled.connect(self._refilter)
        self.balance_order.currentIndexChanged.connect(self._refilter)

    # ---------------------------------------------------------------
    def _balance_view_changed(self, visible):
        self.table.setColumnHidden(5, not visible)
        self.balance_controls.setVisible(visible)
        self.balance_note.setVisible(visible)
        if visible:
            self._balances = transaction_balances(self.store, self._df)
        self._refilter()

    def _review_duplicates(self):
        from .accounts import DuplicateDialog
        DuplicateDialog(self.store, self, account_id=self.f_account.currentData()).exec()
        self.refresh()
        self.main.refresh_header()

    def _import_details(self):
        from .import_dialog import ImportDetailsDialog
        ImportDetailsDialog(self.store,self).exec()

    def reset_for_store(self) -> None:
        """Do not carry transaction IDs or pending edits into another ledger."""
        self._df = pd.DataFrame()
        self._balances = pd.DataFrame()
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
        self.f_review.setCurrentIndex(0)
        self.remember_category.setChecked(False)
        self.negative_only.setChecked(False)
        self.learned_lbl.clear()
        self._render()

    def _open_category_help(self):
        InfoDialog('Reviewing transaction categories',
            '<p>Choose a category in a row, or select several rows and use Set category. '
            'Press Enter on a selected transaction to open its category list.</p>'
            '<p>Mark reviewed confirms the selected entries only. To reuse a category change, '
            'choose More → Remember category changes for future imports before editing. '
            'Keep this off for purchases from retailers that sell many kinds of items.</p>'
            '<p>Needs attention includes older categories, uncertain matches, broad retailers '
            'and possible transfers. Not reviewed also includes ordinary automatic categories.</p>'
            '<p>Business (reimbursable) keeps work expenses and separate repayments out of personal '
            'spending estimates. For a repayment inside your paycheck, select the deposit and '
            'choose Separate reimbursement. The bank deposit keeps its full amount.</p>', self).exec()

    def _open_attention(self):
        self._clear_filters()
        self.f_review.setCurrentIndex(self.f_review.findData('attention'))

    def _sync_selection_controls(self, *_args) -> None:
        if self._suspend_cell_signal:
            return
        count = len(self._selected_ids())
        self.confirm_btn.setEnabled(count > 0)
        self.why_btn.setEnabled(count == 1)
        self.bulk_actions.setVisible(count > 0)
        self.selected_lbl.setText(f"{count} transaction{'s' if count != 1 else ''} selected")
        self.scope_note.setText('Category changes will also be remembered for future imports.'
            if self.remember_category.isChecked() else 'Category changes apply only to the selected entries.')
        self._fit_selection_height()
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
        self._fit_filter_height()
        self._fit_columns()
        self._position_empty_state()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._position_empty_state()
            self._fit_columns()
        elif watched is self.content and event.type() == QEvent.Type.Resize:
            self._fit_filter_height()
            self._fit_columns()
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
        widgets = (self.f_review, self.f_account, self.f_category,
                   self.f_since, self.search, self.negative_only)
        blocked = [w.blockSignals(True) for w in widgets]
        try:
            self.f_review.setCurrentIndex(0)
            self.f_account.setCurrentIndex(0)
            self.f_category.setCurrentIndex(0)
            self.f_since.setCurrentIndex(self.f_since.findData(None))
            self.search.clear()
            self.negative_only.setChecked(False)
            for widget in widgets:
                if isinstance(widget, QComboBox):
                    widget.setToolTip(widget.currentText())
        finally:
            for widget, previous in zip(widgets, blocked):
                widget.blockSignals(previous)
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
        if getattr(self, '_fitting_columns', False):
            return
        self._fitting_columns = True
        try:
            width = self.table.viewport().width()
            self.table.setColumnWidth(1, max(110, min(160, int(width * .14))))
            self.table.setColumnWidth(self.CAT_COL, max(165, min(220, int(width * .21))))
            occupied = sum(self.table.columnWidth(c) for c in range(len(self.COLS))
                           if c != 2 and not self.table.isColumnHidden(c))
            # Extra evidence columns scroll horizontally rather than erasing
            # the merchant identity needed to investigate them.
            self.table.setColumnWidth(2, max(250, width - occupied))
            self.table.resizeRowsToContents()
        finally:
            self._fitting_columns = False

    def _fit_selection_height(self):
        if not hasattr(self, 'bulk_actions'):
            return
        width = max(1, self.content.width() - 40)
        controls_height = self.selection_controls.layout().heightForWidth(width)
        self.selection_controls.setFixedHeight(controls_height)
        scope_height = max(self.scope_note.fontMetrics().height(), self.scope_note.heightForWidth(width))
        self.scope_note.setMinimumHeight(scope_height)
        self.bulk_actions.setFixedHeight(controls_height + scope_height + 4)

    def _fit_filter_height(self):
        if hasattr(self, 'filter_host'):
            self.filter_host.setFixedHeight(self.filter_host.layout().heightForWidth(max(1, self.content.width() - 40)))
        self._fit_selection_height()

    def _refilter(self) -> None:
        """A filter changed: show the newest page again, not the old depth."""
        self.since_date.setVisible(self.f_since.currentData() == "custom")
        self._fit_filter_height()
        self.clear_btn.setEnabled(bool(self.f_account.currentData() is not None
            or self.f_category.currentData() is not None or self.f_since.currentData() is not None
            or self.f_review.currentData() != 'all' or self.search.text() or self.negative_only.isChecked()))
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
        if self.show_balances.isChecked():
            self._balances = transaction_balances(self.store, self._df)
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
            reviewed = int(self._df.category_reviewed.sum())
            attention = int(needs_attention(self._df).sum())
            self.learned_lbl.setText(f'{reviewed} reviewed · {attention} need attention across the ledger')
            self.attention_btn.setText(f'{attention} need attention')
            self.attention_btn.setEnabled(attention > 0)
            self.attention_btn.setVisible(not self._df.empty)
            self.filter_host.setVisible(not self._df.empty)
            self._fit_filter_height()
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
        self.f_review.setCurrentIndex(0)
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
        review = self.f_review.currentData()
        if review == 'attention':
            df = df[needs_attention(df)]
        elif review == 'unreviewed':
            df = df[df.category_reviewed.eq(0)]
        elif review == 'reviewed':
            df = df[df.category_reviewed.eq(1)]
        elif review == 'similarity':
            df = df[df.category_source.eq('similarity') & df.category_reviewed.eq(0)]
        if q:
            df = df[df["description"].fillna("").str.lower()
                    .str.contains(q, regex=False, na=False)]
        cutoff = self._since_cutoff()
        undated = int(df["date"].isna().sum())
        older = int((df["date"] < cutoff).sum()) if cutoff is not None else 0
        if cutoff is not None:
            df = df[df["date"].notna() & (df["date"] >= cutoff)]
        if self.show_balances.isChecked() and self.negative_only.isChecked():
            negative_ids = self._balances.index[self._balances['day_close'] < 0] if not self._balances.empty else []
            df = df[df.id.isin(negative_ids)]
        total = len(df)
        net = df["amount"].sum()
        oldest = self.show_balances.isChecked() and self.balance_order.currentData() == "oldest"
        df = df.sort_values(["date", "id"], ascending=oldest, na_position="last", kind="stable")
        if self._shown is not None:
            df = df.head(self._shown)

        self._suspend_cell_signal = True
        self.table.setRowCount(len(df))
        pos = QBrush(QColor(theme.POSITIVE))
        neg = QBrush(QColor(theme.NEGATIVE))
        attention = needs_attention(df)
        for r, (_, row) in enumerate(df.iterrows()):
            d = QTableWidgetItem(fmt_date(row["date"]))
            d.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            if pd.notna(row.get('transaction_date')):
                d.setToolTip(f"Ledger date: {fmt_date(row['date'])}\nTransaction date: {fmt_date(row['transaction_date'])}")
            a = QTableWidgetItem(self._accounts.get(row["account_id"], "?"))
            desc = QTableWidgetItem(str(row["description"]))
            a.setToolTip(a.text())
            desc.setToolTip(desc.text())
            state = ('Reviewed' if row.category_reviewed else
                     'Needs checking' if attention.loc[row.name] else 'Not reviewed')
            desc.setData(_REVIEW_STATE, state)
            desc.setData(_ATTENTION, state == 'Needs checking')
            desc.setData(Qt.ItemDataRole.AccessibleTextRole, f'{row.description}. {state}.')
            desc.setData(Qt.ItemDataRole.AccessibleDescriptionRole, row.category_reason)
            amount = float(row["amount"]) if pd.notna(row["amount"]) else 0.0
            amt = QTableWidgetItem(money_exact(amount, signed=True) if self.show_balances.isChecked()
                                   else fmt_money(amount, signed=True))
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
            if self.show_balances.isChecked():
                balance = QTableWidgetItem("—")
                balance.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if int(row["id"]) in self._balances.index:
                    detail = self._balances.loc[int(row["id"])]
                    value = float(detail["after"])
                    balance.setText(('≈ ' if detail['unordered'] else '') + money_exact(value))
                    balance.setData(Qt.ItemDataRole.UserRole, value)
                    balance.setForeground(neg if value < 0 else pos)
                    crossing = ('Same-day order is unknown; this calculated crossing below $0 is not a verified overdraft.\n'
                                if detail['unordered'] and detail['before'] >= 0 > value else
                                'Calculated closing balance is below $0; check the ledger and observation.\n'
                                if value < 0 and not detail['unordered'] else '')
                    balance.setToolTip(
                        f"{crossing}{a.text()}\nBefore: {money_exact(detail['before'])}\n"
                        f"Transaction: {money_exact(amount, signed=True)}\nAfter: {money_exact(value)}\n"
                        f"Anchor: {money_exact(detail['anchor_balance'])} at end of {detail['anchor_date']}.\n"
                        f"Daily close: {money_exact(detail['day_close'])}.\n"
                        "Includes all account transactions, including those hidden by filters.\n"
                        "Same-day order follows import IDs; the bank may post in another order.")
                else:
                    balance.setToolTip("A dated transaction in a checking, savings or credit-card account is required. "
                                       "Investment and loan histories use balance snapshots on Dashboard.")
                self.table.setItem(r, 5, balance)
            self.table.setCellWidget(r, self.CAT_COL,
                                     self._category_editor(row))
            reviewed = bool(row.category_reviewed)
            label = 'Reviewed by you' if reviewed else SOURCES.get(row.category_source, 'Not reviewed')
            source = QTableWidgetItem(label)
            source.setToolTip(row.category_reason or 'This category predates tracking; review it before treating it as a verified label.')
            if not reviewed and needs_attention(pd.DataFrame([row])).iloc[0]:
                source.setForeground(QBrush(theme.qcolor(theme.AMBER)))
            self.table.setItem(r, 6, source)
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
        box = CategoryPicker()
        box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        box.setAccessibleName(f'Category for {row.description}, {fmt_date(row.date)}')
        box.setAccessibleDescription('Open the list and choose a category to save. Tab leaves the ledger.')
        box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.addItems(self._cats)
        current = str(row["category"])
        i = box.findText(current)
        if i < 0:
            box.insertItem(0, current)
            i = 0
        box.setCurrentIndex(i)
        box.setToolTip(current + '\n' + ('Reviewed by you. ' if row.category_reviewed else 'Not yet reviewed. ')
                       + (row.category_reason or 'Older category; no original decision was recorded.'))
        box.setProperty("tx_id", int(row["id"]))
        box.setProperty("previous", current)
        box._ledger = self.store
        box.textActivated.connect(
            lambda text, b=box: self._on_cell_category_changed(b, text))
        return box

    def _on_cell_category_changed(self, box: QComboBox, text: str) -> None:
        if self._suspend_cell_signal or box._ledger is not self.store:
            return
        tx_id = box.property("tx_id")
        previous = box.property("previous")
        if tx_id is None or text == previous:
            return
        ledger = self.store
        QTimer.singleShot(0, lambda: self._save_inline_category(ledger, int(tx_id), text))

    def _save_inline_category(self, ledger, tx_id, category):
        if self.store is not ledger:
            return
        # Move focus before replacing editors. Deleting a focused combo can
        # deactivate the native popup's owner as Qt finishes closing it.
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)
        restore = self.window().isActiveWindow()
        self._recategorize([tx_id], category)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == tx_id:
                self.table.setCurrentCell(row, self.CAT_COL, QItemSelectionModel.SelectionFlag.NoUpdate)
                break
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)
        if restore:
            QTimer.singleShot(0, lambda: self._restore_ledger_focus(ledger))

    def _restore_ledger_focus(self, ledger):
        if (self.store is ledger and self.isVisible() and
                QApplication.activeWindow() in (None, self.window()) and
                QApplication.focusWidget() in (None, self.table)):
            # Qt can finish deleting the old native combo popup after the
            # redraw. Restore this edit's owner, without taking another
            # widget's focus if the user has moved on.
            QApplication.setActiveWindow(self.window())
            self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def _edit_current_category(self):
        row = self.table.currentRow()
        if row >= 0:
            box = self.table.cellWidget(row, self.CAT_COL)
            box.setFocus(Qt.FocusReason.ShortcutFocusReason)
            box.showPopup()

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
        if not tx_ids or not category:
            return
        try:
            self._fresh_rows(tx_ids)
            result = self.store.review_transactions(tx_ids, category,
                remember=self.remember_category.isChecked())
        except Exception as exc:
            self._refresh_after_action()
            self._show_action_error('Could not finish category review', exc)
            return
        self._refresh_after_action()
        tail = (' Remembered for future imports.' if result['remembered'] else
                ' Applies only to these transactions.')
        message = f"Reviewed {result['reviewed']} transactions as “{category}”." + tail
        self.learned_lbl.setText(message)
        self.main.status(message)

    def _confirm_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        try:
            self._fresh_rows(ids)
            result = self.store.review_transactions(ids, remember=False)
        except Exception as exc:
            self._show_action_error('Could not confirm categories', exc)
            return
        self._refresh_after_action()
        message = f"Confirmed {result['reviewed']} transaction categories. Future import rules unchanged."
        self.learned_lbl.setText(message)
        self.main.status(message)

    def _explain_selected(self):
        ids = self._selected_ids()
        if len(ids) != 1:
            return
        try:
            row = self._fresh_rows(ids).iloc[0]
            status = 'Reviewed by you' if row.category_reviewed else 'Not yet reviewed'
            reason = row.category_reason or 'This category predates decision tracking. No original reason was saved.'
            html = (f'<h3>{escape(str(row.description))}</h3>'
                f'<p>{escape(fmt_date(row.date))} · {escape(self._accounts.get(row.account_id, "Unknown account"))}'
                f' · {escape(money_exact(row.amount, signed=True))}</p>'
                f'<p><b>{escape(str(row.category))}</b> · {escape(status)}</p>'
                f'<p>{escape(reason)}</p><p>Source: {escape(SOURCES.get(row.category_source, row.category_source))}</p>')
            if row.category_reviewed_at:
                html += f'<p>Last reviewed: {escape(_review_time(row.category_reviewed_at))}</p>'
            for label,key in [('Transaction date','transaction_date'),('Posting date','posting_date')]:
                if pd.notna(row.get(key)):
                    html += f'<p>{label}: {escape(fmt_date(row[key]))}</p>'
            html += f'<p>Bank reference: {escape(str(row.get("source_reference") or "Not recorded"))}</p>'
            balances = transaction_balances(self.store)
            if ids[0] in balances.index:
                balance = balances.loc[ids[0]]
                html += (f'<h3>Calculated account balance</h3><p>Before: {money_exact(balance["before"])}'
                    f' · After: {money_exact(balance["after"])}</p>'
                    f'<p>Based on {money_exact(balance["anchor_balance"])} at the end of '
                    f'{escape(str(balance["anchor_date"]))}. Includes the full bank ledger, '
                    'even when filters hide entries. Same-day order follows import order and is unverified. '
                    f'Daily close: {money_exact(balance["day_close"])}.</p>')
            history = self.store.category_feedback()
            history = history[history.transaction_id.eq(ids[0])].tail(20)
            if not history.empty:
                html += '<h3>Your review history</h3>'
                for entry in history.iloc[::-1].itertuples():
                    scope = 'remembered' if entry.remember else 'this transaction only'
                    html += (f'<p>{escape(_review_time(entry.created_at))}: {escape(entry.old_category)} → '
                             f'{escape(entry.new_category)} ({scope})</p>')
            html += ('<p>Automatic categories have not been checked by you. '
                     'Mark reviewed confirms only the selected entries. Category changes affect future '
                     'imports only when you enable remembering in More.</p>')
            InfoDialog('Transaction details', html, self, width=650, height=550).exec()
        except Exception as exc:
            self._show_action_error('Could not load category details', exc)

    def _export_reviewed(self):
        pending = None
        ledger = self.store
        try:
            path, _ = QFileDialog.getSaveFileName(self, 'Export human-reviewed transaction data',
                'reviewed-transactions.csv', 'CSV files (*.csv)')
            if not path:
                return
            if self.store is not ledger:
                raise ValueError('The open ledger changed. Start the export again.')
            target = Path(path).expanduser().resolve()
            if target == ledger.path.resolve() or (target.exists() and os.path.samefile(target, ledger.path)):
                raise ValueError('Choose a CSV file, not the open database.')
            frame = reviewed_dataset(ledger)
            fd, pending = tempfile.mkstemp(prefix='.reviewed-', suffix='.csv', dir=target.parent)
            os.close(fd)
            frame.to_csv(pending, index=False, encoding='utf-8')
            os.replace(pending, target)
            pending = None
            self.main.status(f'Exported {len(frame)} human-reviewed transactions to {target}.')
        except Exception as exc:
            self._show_action_error('Could not export reviewed data', exc)
        finally:
            if pending is not None:
                Path(pending).unlink(missing_ok=True)
