"""A view of recorded goal savings and the accounts holding them."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import (QBoxLayout, QButtonGroup, QFrame, QHBoxLayout,
                             QPushButton, QRadioButton, QSizePolicy,
                             QToolTip, QVBoxLayout, QWidget)

from ..funding import funding_overview
from ..goals import targets
from ..models import AccountType
from . import theme
from .goal_funding import dollars
from .widgets import Card, WrappingLabel


def goal_color(goal_id):
    # Stable identities across refreshes and changes in priority. Amber and
    # coral retain their warning/debt meanings rather than identifying goals.
    return theme.GOAL_COLORS[(goal_id - 1) % len(theme.GOAL_COLORS)]


def segment_text_color(fill):
    """Choose readable text against the composited, possibly dimmed segment."""
    surface = QColor(theme.SURFACE)
    alpha = fill.alphaF()
    channels = [alpha * a + (1 - alpha) * b for a, b in
                zip(fill.getRgbF()[:3], surface.getRgbF()[:3])]
    def luminance(rgb):
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(w * v for w, v in zip((.2126, .7152, .0722), linear))
    background = luminance(channels)
    def contrast(color):
        light = luminance(color.getRgbF()[:3])
        return (max(background, light) + .05) / (min(background, light) + .05)
    return max((QColor(theme.BG), QColor(theme.TEXT)), key=contrast)


def plain_label(text='', *, muted=False):
    label = WrappingLabel(text)
    if muted:
        label.setObjectName('hint')
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


class AccountAllocationChart(QWidget):
    goalSelected = pyqtSignal(int)
    # One account is a block: name, bar and detail sit 4px apart, and 16px of air
    # separates it from the next block. At the old even spacing the detail line
    # was as close to the following account's name as to its own bar.
    NAME_H, BAR_Y, BAR_H, DETAIL_Y, DETAIL_H = 20, 24, 22, 50, 20
    ROW_HEIGHT = 86

    def __init__(self):
        super().__init__()
        self.accounts, self.goals, self.selected = [], {}, None
        self.hit_regions = []
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Goal savings by account')
        self.setMinimumWidth(0)

    def set_data(self, overview, selected):
        self.accounts = overview.accounts
        self.goals = {g.id: g for g in overview.goals}
        self.selected = selected
        self.setFixedHeight(max(70, len(self.accounts) * self.ROW_HEIGHT
                                - (self.ROW_HEIGHT - self.DETAIL_Y - self.DETAIL_H)))
        evidence = []
        for account in self.accounts:
            parts = [f'{self.goals[gid].name}: {dollars(value)}'
                     for gid, value in account.goals.items()]
            parts.append(f'No goal assigned here: {dollars(account.unassigned)}')
            evidence.append(f'{account.name}, {account.kind.label}, balance {dollars(account.balance)}. '
                            + '; '.join(parts))
        self.setAccessibleDescription('\n'.join(evidence))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        self.hit_regions = []
        if not self.accounts:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                             'Add a checking, savings or investment account to see where goals are held.')
            return
        scale = max(1.0, *(max(a.balance, sum(max(0, v) for v in a.goals.values()))
                           for a in self.accounts))
        width = max(1, self.width() - 4)
        fm = painter.fontMetrics()
        for index, account in enumerate(self.accounts):
            y = index * self.ROW_HEIGHT
            amount = dollars(account.balance)
            painter.setFont(theme.mono_font(13))
            amount_width = painter.fontMetrics().horizontalAdvance(amount)
            painter.setFont(self.font())
            painter.setPen(QColor(theme.TEXT))
            label = fm.elidedText(account.name, Qt.TextElideMode.ElideRight,
                                  max(20, width - amount_width - 12))
            painter.drawText(QRectF(0, y, width, self.NAME_H), Qt.AlignmentFlag.AlignLeft, label)
            painter.setFont(theme.mono_font(13))
            painter.drawText(QRectF(0, y, width, self.NAME_H), Qt.AlignmentFlag.AlignRight, amount)
            painter.setFont(self.font())
            bar_y = y + self.BAR_Y
            track = QRectF(0, bar_y, max(0, account.balance) / scale * width, self.BAR_H)
            painter.fillRect(track, QColor(theme.BORDER_STRONG))
            if account.balance > 0:
                self.hit_regions.append((track, None, f'{account.name}\nNo goal assigned here: '
                                         f'{dollars(account.unassigned)}'))
            x = 0.0
            for gid, value in account.goals.items():
                if value <= 0:
                    continue
                rect = QRectF(x, bar_y, value / scale * width, self.BAR_H)
                color = QColor(goal_color(gid))
                if self.selected is not None and gid != self.selected:
                    color.setAlpha(65)
                painter.fillRect(rect, color)
                painter.setPen(QPen(QColor(theme.SURFACE), 1))
                painter.drawRect(rect)
                if gid == self.selected:
                    painter.setPen(QPen(QColor(theme.TEXT), 2))
                    painter.drawRect(rect.adjusted(1, 1, -1, -1))
                text = self.goals[gid].name
                if rect.width() > fm.horizontalAdvance(text) + 14:
                    painter.setPen(segment_text_color(color))
                    painter.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, text)
                self.hit_regions.append((rect, gid, f'{account.name}\n{text}: {dollars(value)}'))
                x += rect.width()
            painter.setPen(QColor(theme.MUTED))
            detail = f'{account.kind.label} · No goal assigned here: {dollars(account.unassigned)}'
            if account.unassigned < -.005:
                painter.setPen(QColor(theme.AMBER))
                detail = (f'{account.kind.label} · Earmarks exceed balance by {dollars(-account.unassigned)}'
                          if account.goals else f'{account.kind.label} · Negative balance')
                marker = max(0, account.balance) / scale * width
                painter.drawLine(QPointF(marker, bar_y - 3), QPointF(marker, bar_y + self.BAR_H + 3))
            painter.drawText(QRectF(0, y + self.DETAIL_Y, width, self.DETAIL_H), Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(detail, Qt.TextElideMode.ElideRight, width))
        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.ACCENT), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def mouseMoveEvent(self, event):
        for rect, gid, text in reversed(self.hit_regions):
            if rect.contains(event.position()):
                self.setCursor(Qt.CursorShape.PointingHandCursor if gid else Qt.CursorShape.ArrowCursor)
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()
        self.unsetCursor()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, gid, _text in reversed(self.hit_regions):
                if gid and rect.contains(event.position()):
                    self.goalSelected.emit(gid)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        keys = list(self.goals)
        if keys and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
            index = keys.index(self.selected) if self.selected in keys else (-1 if step > 0 else 0)
            self.goalSelected.emit(keys[(index + step) % len(keys)])
            event.accept()
            return
        super().keyPressEvent(event)


class GoalProgressStrip(QWidget):
    def __init__(self, goal):
        super().__init__()
        self.goal = goal
        self.setFixedHeight(14)
        self.setAccessibleName(f'{goal.name}: {dollars(goal.saved)} held, '
            f'{dollars(goal.spent)} purchased, {dollars(goal.remaining)} still needed')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        painter.fillRect(self.rect(), QColor(theme.FIELD))
        goal = self.goal
        scale = self.width() / max(goal.target, .01)
        located = max(0, goal.saved - max(0, goal.unlocated))
        painter.fillRect(QRectF(0, 0, min(self.width(), located * scale), self.height()),
                         QColor(goal_color(goal.id)))
        if goal.unlocated > 0:
            rect = QRectF(located * scale, 0, min(self.width() - located * scale,
                          goal.unlocated * scale), self.height())
            painter.fillRect(rect, QBrush(QColor(goal_color(goal.id)), Qt.BrushStyle.Dense4Pattern))
        if goal.spent > 0:
            painter.fillRect(QRectF(min(goal.saved, goal.target) * scale, 0,
                max(0, min(goal.spent, goal.target - goal.saved)) * scale, self.height()),
                QBrush(QColor(theme.REFERENCE), Qt.BrushStyle.BDiagPattern))
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class GoalFundingMap(Card):
    recordRequested = pyqtSignal(int, bool)
    selectionChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__('Where your goal savings live')
        self.setStyleSheet(f'''
            QRadioButton {{
                border: 1px solid transparent; border-radius: 4px;
                padding: 2px; background: transparent;
            }}
            QRadioButton:focus {{ border-color: {theme.ACCENT}; }}
            QRadioButton::indicator {{
                width: 14px; height: 14px; border-radius: 8px;
                border: 1px solid {theme.BORDER_STRONG}; background: {theme.FIELD};
            }}
            QRadioButton::indicator:checked {{
                background: {theme.ACCENT}; border-color: {theme.ACCENT};
            }}
            QRadioButton::indicator:hover {{ border-color: {theme.ACCENT}; }}
        ''')
        self.overview = None
        self.selected_goal = None
        self.rows = {}
        self.summary = plain_label()
        families = ', '.join(f'"{name}"' for name in theme.MONO_FAMILIES)
        self.summary.setStyleSheet(f'font-family: {families}; font-size: 18px; font-weight: 600;')
        self.reconciliation = plain_label(muted=True)
        self.location_note = plain_label(muted=True)
        self.location_note.setStyleSheet(f'color: {theme.AMBER};')
        # What the money adds up to: one block, lines 3px apart.
        self.no_accounts = plain_label('Add a checking, savings or investment account on Accounts and '
            'this shows which account holds the money set aside for each goal.', muted=True)
        self.body.addWidget(self.no_accounts)
        totals = QVBoxLayout()
        totals.setSpacing(3)
        for label in (self.summary, self.reconciliation, self.location_note):
            totals.addWidget(label)
        self.body.addLayout(totals)
        # The picture and its key: the key sits close above what it explains.
        figure = QVBoxLayout()
        figure.setSpacing(8)
        self.key_label = plain_label('Solid = held in an account · patterned = no account chosen yet · '
            'striped grey = already spent.', muted=True)
        figure.addWidget(self.key_label)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(32)
        self.account_panel = QWidget()
        account_layout = QVBoxLayout(self.account_panel)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setSpacing(8)
        account_layout.addWidget(plain_label('Accounts · bars use the same dollar scale', muted=True))
        self.account_chart = AccountAllocationChart()
        self.account_chart.goalSelected.connect(self.select_goal)
        account_layout.addWidget(self.account_chart)
        account_layout.addStretch()
        self.goal_panel = QWidget()
        self.goal_layout = QVBoxLayout(self.goal_panel)
        self.goal_layout.setContentsMargins(0, 0, 0, 0)
        self.goal_layout.setSpacing(8)
        self.group = QButtonGroup(self)
        self.columns.addWidget(self.account_panel, 1)
        self.columns.addWidget(self.goal_panel, 1)
        figure.addLayout(self.columns)
        self.body.addLayout(figure)
        # The goal actions sit under the picture they act on and keep their
        # height with or without a selection, so choosing a goal moves nothing.
        self.selection_note = plain_label()
        self.record_btn = QPushButton('Goal activity…')
        self.record_btn.clicked.connect(lambda: self.recordRequested.emit(self.selected_goal, False))
        self.locate_btn = QPushButton('Locate savings…')
        self.locate_btn.clicked.connect(lambda: self.recordRequested.emit(self.selected_goal, True))
        self.clear_btn = QPushButton('Clear selection')
        self.clear_btn.setObjectName('link')
        self.clear_btn.clicked.connect(lambda: self.select_goal(None))
        self.selection_bar = QWidget()
        self.selection_bar.setMinimumHeight(36)
        bar = QHBoxLayout(self.selection_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addWidget(self.selection_note, 1)
        for button in (self.record_btn, self.locate_btn, self.clear_btn):
            bar.addWidget(button)
        self.body.addWidget(self.selection_bar)
        self.milestones = QWidget()
        self.milestone_layout = QVBoxLayout(self.milestones)
        self.milestone_layout.setContentsMargins(0, 0, 0, 0)
        self.investment_note = plain_label('Money earmarked in investments stays invested; nothing here sells, '
            'transfers or locks it, and maturity or withdrawal limits are not modeled.', muted=True)
        footnotes = QVBoxLayout()
        footnotes.setSpacing(3)
        footnotes.addWidget(self.milestones)
        footnotes.addWidget(self.investment_note)
        self.body.addSpacing(4)
        self.body.addLayout(footnotes)
        self.setMinimumWidth(0)
        for panel in (self.account_panel, self.goal_panel):
            panel.setMinimumWidth(0)
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._sync_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        direction = (QBoxLayout.Direction.LeftToRight if self.width() >= 860
                     else QBoxLayout.Direction.TopToBottom)
        if self.columns.direction() != direction:
            self.columns.setDirection(direction)

    @staticmethod
    def _clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def refresh(self, store):
        if getattr(self, '_source_store', None) is not store:
            self.selected_goal = None
            self._source_store = store
        self.overview = overview = funding_overview(store)
        by_account = {a.id: a.name for a in overview.accounts}
        self.summary.setText(f'{dollars(overview.cash + overview.investments)} across '
                             f'{len(overview.accounts)} funding accounts')
        saved = sum(g.saved for g in overview.goals)
        remainder = (f'{dollars(overview.unassigned)} not earmarked' if overview.unassigned >= 0
                     else f'{dollars(-overview.unassigned)} funding deficit')
        text = (f'Cash {dollars(overview.cash)} + investments {dollars(overview.investments)} · '
                f'{dollars(saved)} recorded as saved · {remainder}')
        if abs(overview.card_balance) > .005:
            text += f' (after card balances of {dollars(overview.card_balance)})'
        self.reconciliation.setText(text)
        unlocated = sum(g.unlocated for g in overview.goals)
        notes = []
        if unlocated > .005:
            notes.append(f'{dollars(unlocated)} saved has no account assigned. Select its goal and '
                'choose Locate savings. Account remainders may contain this money.')
        if (saved > .005 and overview.unassigned < -.005) or any(
                a.goals and a.unassigned < -.005 for a in overview.accounts):
            notes.append('Recorded earmarks exceed funding balances. Review account balances and goal activity.')
        if any(g.unlocated < -.005 or any(v < -.005 for v in g.accounts.values()) for g in overview.goals):
            notes.append('Some funding records do not reconcile. Review goal activity before assigning more.')
        self.location_note.setText(' '.join(notes))
        self.location_note.setVisible(bool(notes))
        self._clear(self.goal_layout)
        for button in self.group.buttons():
            self.group.removeButton(button)
        self.rows = {}
        self.goal_layout.addWidget(plain_label('Goals · each bar is measured against its own goal amount', muted=True))
        if not overview.goals:
            self.goal_layout.addWidget(plain_label('Add a savings goal to reserve money for a purchase '
                'or a baseline you want to protect. Then record where its savings are held.'))
        for goal in overview.goals:
            row = QFrame()
            row.setObjectName('goalFundingRow')
            row.setStyleSheet(f'QFrame#goalFundingRow {{ border: none; border-bottom: 1px solid {theme.BORDER}; }}')
            layout = QVBoxLayout(row)
            layout.setContentsMargins(0, 4, 0, 10)
            layout.setSpacing(5)
            heading = QHBoxLayout()
            radio = QRadioButton()
            radio.setAccessibleName(f'Highlight {goal.name}')
            radio.setFixedWidth(24)
            self.group.addButton(radio, goal.id)
            radio.toggled.connect(lambda checked, gid=goal.id: self.select_goal(gid) if checked else None)
            name = plain_label(goal.name)
            name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            heading.addWidget(radio)
            heading.addWidget(name, 1)
            status = plain_label(goal.status)
            heading.addWidget(status)
            layout.addLayout(heading)
            layout.addWidget(GoalProgressStrip(goal))
            progress = (f'{dollars(goal.saved)} held · {dollars(goal.remaining)} still needed '
                        f'of {dollars(goal.target)}')
            if goal.spent > .005:
                progress += f' · {dollars(goal.spent)} purchased'
            layout.addWidget(plain_label(progress, muted=True))
            sources = [f'{by_account[aid]} {dollars(value)}' for aid, value in goal.accounts.items()]
            if goal.unlocated > .005:
                sources.append(f'Account not assigned {dollars(goal.unlocated)}')
            label = plain_label(' · '.join(sources) if sources else 'No savings held in an account yet.', muted=True)
            layout.addWidget(label)
            row.mousePressEvent = lambda event, gid=goal.id: self.select_goal(gid)
            self.rows[goal.id] = row
            self.goal_layout.addWidget(row)
        self.goal_layout.addStretch()
        self._clear(self.milestone_layout)
        milestones = [t for t in targets(store) if t.goal_kind in ('cash', 'networth')]
        if milestones:
            count = len(milestones)
            self.milestone_layout.addWidget(plain_label(
                f'{count} balance milestone{"s" if count != 1 else ""} (listed in the ladder below) '
                'measure household balances and do not reserve an account, so they can include money '
                'already earmarked above.', muted=True))
        self.investment_note.setVisible(any(
            a.kind == AccountType.INVESTMENT and any(v > .005 for v in a.goals.values())
            for a in overview.accounts))
        self.milestones.setVisible(bool(milestones))
        # Without an account there is nowhere for a goal's money to be, so the card
        # says how to begin rather than showing a $0.00 total and an empty chart.
        has_accounts = bool(overview.accounts)
        self.no_accounts.setVisible(not has_accounts)
        for part in (self.summary, self.reconciliation, self.key_label, self.account_panel,
                     self.goal_panel, self.selection_bar):
            part.setVisible(has_accounts)
        self.select_goal(self.selected_goal if self.selected_goal in self.rows else None)

    def select_goal(self, goal_id):
        if goal_id not in self.rows:
            goal_id = None
        changed = goal_id != self.selected_goal
        self.selected_goal = goal_id
        if self.overview is not None:
            self.account_chart.set_data(self.overview, goal_id)
        self.group.setExclusive(False)
        for button in self.group.buttons():
            button.blockSignals(True)
            button.setChecked(self.group.id(button) == goal_id)
            button.blockSignals(False)
        self.group.setExclusive(True)
        self._sync_buttons()
        if changed:
            self.selectionChanged.emit(goal_id)

    def _sync_buttons(self):
        goal = next((g for g in self.overview.goals if g.id == self.selected_goal), None) if self.overview else None
        self.selection_note.setText(f'Selected: {goal.name}' if goal else
            'Select a goal, or one of its account segments, to see where its money is.'
            if self.overview and self.overview.goals else 'Add a goal to start locating its savings.')
        self.record_btn.setEnabled(goal is not None)
        self.locate_btn.setEnabled(bool(goal and goal.unlocated > .005))
        self.clear_btn.setEnabled(goal is not None)
        # Only offer what can be done now: nothing while nothing is chosen, and
        # Locate savings only for a goal that has savings with no account.
        self.record_btn.setVisible(goal is not None)
        self.locate_btn.setVisible(self.locate_btn.isEnabled())
        self.clear_btn.setVisible(goal is not None)
        for button in (self.record_btn, self.locate_btn):
            button.setToolTip(f'{button.text().rstrip("…")}: {goal.name}' if goal else 'Select a goal in the map first.')
            button.setAccessibleName(f'{button.text().rstrip("…")}: {goal.name}' if goal else button.text())
