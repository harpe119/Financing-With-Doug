"""Calendar and dated list of bills, income, and other scheduled items."""
from __future__ import annotations

import calendar as month_dates
from datetime import date

from PyQt6.QtCore import QDate, QEvent, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QTextCharFormat
from PyQt6.QtWidgets import (QCalendarWidget, QComboBox, QHBoxLayout,
                             QHeaderView, QPushButton, QTableWidget,
                             QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..calendar import upcoming_schedule
from ..models import AccountType
from ..util import fmt_money, fmt_day
from . import theme
from .first_use import (NO_ACCOUNTS, NO_TRANSACTIONS, READY, FirstUsePanel,
                        setup_stage)
from .widgets import Card, StatCard, hint, hsplit


class TodayCalendar(QCalendarWidget):
    """A calendar that rings today, whatever else a cell is doing.

    The cell for today used to be told apart only by being the selected one, so
    the moment another day was clicked nothing said which day it was. A ring
    drawn over the cell works with every other mark: the green selection, and
    the red, green or blue digits that say what is scheduled. It is neutral
    (Signal White), like the dashed "today" line on the charts.
    """

    def paintCell(self, painter, rect, date) -> None:
        super().paintCell(painter, rect, date)
        if date == QDate.currentDate():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(theme.TEXT))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect).adjusted(3, 3, -3, -3), 5, 5)
            painter.restore()


class CalendarPage(QWidget):
    COLS = ("Date", "Item", "Account", "In", "Out", "Balance")
    COL_TIPS = ("", "", "", "Scheduled money in", "Scheduled money out",
                "The account's scheduled balance after this item")

    def __init__(self, store, main):
        super().__init__()
        self.setObjectName("page")
        self.store, self.main = store, main
        self._marked = []
        self._day = None
        self._entries = []
        self._accounts = {}
        self._schedule = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)
        # Until something repeats there is nothing to put on a calendar, so the
        # page says what it will hold instead of showing an empty month.
        self.first_use = FirstUsePanel(
            main, "Your bills and paydays will show up here",
            "Doug finds the bills and paychecks that repeat in your statements, "
            "then lists them here by date with what each does to your balance.",
            {NO_ACCOUNTS: "Add an account, then import its statements.",
             NO_TRANSACTIONS: "Import a CSV from your bank. About three months "
                              "of statements show the repeats best.",
             READY: "Nothing has repeated often enough yet. A bill or paycheck "
                    "needs at least three matching entries at a regular "
                    "interval, so more history may find some."},
            ready_label="Import more history…", ready_action=main.open_import)
        outer.addWidget(self.first_use, 0, Qt.AlignmentFlag.AlignTop)
        self.body = QWidget()
        outer.addWidget(self.body, 1)
        root = QVBoxLayout(self.body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        bar = QHBoxLayout()
        self.account_pick = QComboBox()
        self.account_pick.setMinimumWidth(200)
        self.account_pick.setAccessibleName("Show scheduled items for")
        self.account_pick.currentIndexChanged.connect(self._render)
        bar.addWidget(self.account_pick)
        self.month_btn = QPushButton("Show whole month")
        self.month_btn.setObjectName("small")
        self.month_btn.clicked.connect(self._whole_month)
        bar.addWidget(self.month_btn)
        bar.addStretch(1)
        root.addLayout(bar)
        cards = QHBoxLayout()
        self.money_in = StatCard("Scheduled money in")
        self.money_out = StatCard("Scheduled money out")
        self.balance = StatCard("After scheduled items")
        for card in (self.money_in, self.money_out, self.balance):
            cards.addWidget(card)
        root.addLayout(cards)
        self.calendar = TodayCalendar()
        self.calendar.setMinimumDate(QDate.currentDate())
        self.calendar.setMaximumDate(QDate.currentDate().addMonths(12))
        self.calendar.setGridVisible(True)
        self.restyle()
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.currentPageChanged.connect(self._month_changed)
        self.calendar.setAccessibleName("Calendar of scheduled bills and paydays")
        # Qt's own month arrows are icon-only buttons with no name of their own.
        for object_name, name in (("qt_calendar_prevmonth", "Previous month"),
                                  ("qt_calendar_nextmonth", "Next month")):
            arrow = self.calendar.findChild(QToolButton, object_name)
            if arrow is not None:
                arrow.setAccessibleName(name)
        self.calendar.clicked.connect(self._select_day)
        calendar_card = Card("Calendar")
        calendar_card.body.addWidget(self.calendar)
        calendar_card.body.addWidget(hint("Green: income. Red: payments. Blue: both. The ringed day is today. Select a day to see its items."))
        table_card = Card("Scheduled items")
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setAccessibleName("Scheduled items")
        # Tab moves on to the next control; the arrow keys move between cells. Left
        # on, Tab walked every cell and never left the table.
        self.table.setTabKeyNavigation(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        for column in range(len(self.COLS)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive if column == 1
                else QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(1, 140)
        self.table.viewport().installEventFilter(self)
        for column, tip in enumerate(self.COL_TIPS):
            if tip:
                self.table.horizontalHeaderItem(column).setToolTip(tip)
        table_card.body.addWidget(self.table)
        # The table is the wider of the two: six columns have to fit beside a
        # month at the window's minimum width without a sideways scrollbar.
        root.addWidget(hsplit(calendar_card, table_card, sizes=[330, 670]), 1)
        self.note = hint("")
        root.addWidget(self.note)

    def restyle(self) -> None:
        """Weekday headers in the current palette; Qt keeps these formats itself."""
        header_style = QTextCharFormat()
        header_style.setBackground(QColor(theme.SURFACE2))
        header_style.setForeground(QColor(theme.TEXT))
        self.calendar.setHeaderTextFormat(header_style)
        weekday_style = QTextCharFormat()
        weekday_style.setForeground(QColor(theme.TEXT))
        for weekday in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.calendar.setWeekdayTextFormat(weekday, weekday_style)

    def refresh(self):
        nothing_found = not self.store.recurring()
        self.first_use.setVisible(nothing_found)
        self.body.setVisible(not nothing_found)
        if nothing_found:
            self.first_use.sync(setup_stage(self.store))
        self.calendar.setMinimumDate(QDate.currentDate())
        selected = self.account_pick.currentData()
        self._accounts = {a.id: a for a in self.store.accounts() if a.type.is_cashlike}
        self.account_pick.blockSignals(True)
        self.account_pick.clear()
        self.account_pick.addItem("All accounts", "all")
        for aid, account in self._accounts.items():
            self.account_pick.addItem(account.name, aid)
        self.account_pick.addItem("Unassigned items", "unassigned")
        index = self.account_pick.findData(selected)
        self.account_pick.setCurrentIndex(max(index, 0))
        self.account_pick.blockSignals(False)
        self._load_month()

    def reset_for_store(self):
        self._day = None
        self._schedule = None
        self.account_pick.setCurrentIndex(0)
        self.calendar.setCurrentPage(QDate.currentDate().year(), QDate.currentDate().month())

    def _load_month(self):
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        end = date(year, month, month_dates.monthrange(year, month)[1])
        self._schedule = upcoming_schedule(self.store, end)
        self._entries = [entry for entry in self._schedule.entries
                         if (entry.when.year, entry.when.month) == (year, month)]
        self._render()

    def _month_changed(self, *_args):
        self._day = None
        self._load_month()

    def _select_day(self, day):
        self._day = day.toPyDate()
        self._render()

    # Below this width the six columns no longer fit without a sideways scrollbar
    # that hides the balance, so the Account column gives way: it is the one the
    # row can do without (the account is in the item's tooltip).
    ACCOUNT_COLUMN_NEEDS = 560

    def _sync_account_column(self) -> None:
        selected = self.account_pick.currentData()
        roomy = self.table.viewport().width() >= self.ACCOUNT_COLUMN_NEEDS
        self.table.setColumnHidden(
            2, isinstance(selected, int) or len(self._accounts) <= 1 or not roomy)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_account_column()
        return super().eventFilter(obj, event)

    def _whole_month(self):
        self._day = None
        self._render()

    def _render(self, *_args):
        if self._schedule is None:
            return
        selected = self.account_pick.currentData()
        entries = [e for e in self._entries if selected == "all"
                   or (selected == "unassigned" and e.account_id is None)
                   or e.account_id == selected]
        for marked in self._marked:
            self.calendar.setDateTextFormat(marked, QTextCharFormat())
        self._marked = []
        dates = sorted({e.when for e in entries})
        for day in dates:
            daily = [e.amount for e in entries if e.when == day]
            incoming, outgoing = any(v > 0 for v in daily), any(v < 0 for v in daily)
            color = theme.BLUE if incoming and outgoing else theme.ACCENT if incoming else theme.RED
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            style.setFontWeight(700)
            qday = QDate(day.year, day.month, day.day)
            self.calendar.setDateTextFormat(qday, style)
            self._marked.append(qday)
        rows = [e for e in entries if self._day is None or e.when == self._day]
        self.money_in.set_money(sum(e.amount for e in rows if e.amount > 0))
        self.money_out.set_money(-sum(e.amount for e in rows if e.amount < 0))
        # A balance needs an account; "Choose an account" on a card of its own
        # was a card with nothing in it. Likewise the Account column says nothing
        # once there is one account to name.
        self.balance.setVisible(isinstance(selected, int))
        self._sync_account_column()
        if isinstance(selected, int):
            closing = self._schedule.closing_balances.get(selected, 0.0)
            account = self._accounts[selected]
            if account.type == AccountType.CREDIT:
                self.balance.title.setText("Card position · month end")
            else:
                self.balance.title.setText("Scheduled balance · month end")
            self.balance.set_money(closing, colored=True)
        else:
            self.balance.title.setText("Account balance")
            self.balance.set_text("—", sub="Choose an account")
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            account = self._accounts.get(entry.account_id)
            values = (fmt_day(entry.when, year=False, weekday=True), entry.label,
                      account.name if account else "Unassigned",
                      fmt_money(entry.amount) if entry.amount > 0 else "",
                      fmt_money(-entry.amount) if entry.amount < 0 else "",
                      fmt_money(entry.balance_after) if entry.balance_after is not None else "—")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                # With the Account column dropped for room, the item still says
                # whose it is when pointed at.
                cell.setToolTip(f"{value} · {values[2]}" if column == 1 else value)
                self.table.setItem(row, column, cell)
        unknown = sum(e.account_id is None for e in self._entries)
        message = ("Dates and amounts are estimates from recurring patterns. The schedule starts tomorrow. "
                   "Account balances include assigned scheduled items only; everyday spending, transfers, "
                   "and unassigned items are excluded. Loan payments repeat here without a payoff calculation; "
                   "use Forecast for payoff estimates. Card balances below zero represent money owed.")
        if unknown:
            message += f" {unknown} item(s) have no confirmed payment account; use Unassigned items to review them."
        if any(item.active and item.kind == "loan_payment" and item.linked_account_id is None
               for item in self.store.recurring()):
            message += " Unlinked loan patterns are excluded; link them to a loan in Detected bills & pay."
        if not self._entries:
            message = "No scheduled items this month. Import transactions and review Detected bills & pay to see bills and income."
        elif not rows:
            message = "No scheduled items for this selection. " + message
        self.note.setText(message)
