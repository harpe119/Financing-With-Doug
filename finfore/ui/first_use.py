"""What a page says before it has anything to show, and the one thing to do next.

A person who has just installed Doug is on one of three steps: no accounts, no
statements, or data. Home walks them through the first two; this is the same
voice on every other page, so that clicking Spending or Bills & payday on the
first day lands on a sentence and a button, not an empty table.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy

from .widgets import Card, WrappingLabel

NO_ACCOUNTS, NO_TRANSACTIONS, READY = "no-accounts", "no-transactions", "ready"


def setup_stage(store) -> str:
    """Where this person is on the way in: accounts, then statements, then data."""
    if not store.accounts():
        return NO_ACCOUNTS
    return READY if store.has_transactions() else NO_TRANSACTIONS


class FirstUsePanel(Card):
    """A card for an empty page: what will be here, what it needs, one button.

    The button follows the step the person is on. With no accounts it adds one;
    with no statements it imports one; with data but still nothing to show it is
    whatever the page offers (``ready_label``/``ready_action``) or absent. The
    demo link is there until there is real data, because exploring fictional
    money is the quickest way to see what the page is for.
    """

    def __init__(self, main, title: str, what: str, why: dict[str, str],
                 ready_label: str = "", ready_action=None):
        super().__init__(title)
        self.main = main
        self._why = why
        self._ready = (ready_label, ready_action)
        self._stage = NO_ACCOUNTS
        self.what = WrappingLabel(what)
        self.what.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.what)
        self.why = WrappingLabel()
        self.why.setObjectName("hint")
        self.why.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.why)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.primary = QPushButton()
        self.primary.setObjectName("primary")
        self.primary.clicked.connect(self._act)
        row.addWidget(self.primary)
        self.demo = QPushButton("Explore with a demo first")
        self.demo.setObjectName("link")
        self.demo.setToolTip("Opens fictional data in a separate file; your own data is untouched")
        self.demo.clicked.connect(lambda: self.main._load_demo("balanced", confirm=False))
        row.addWidget(self.demo)
        row.addStretch(1)
        self.body.addLayout(row)
        # As tall as its words and no taller: left alone, a layout with nothing
        # else visible stretches the card over the whole page and spreads three
        # lines of text across it.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setVisible(False)

    def sync(self, stage: str) -> None:
        self._stage = stage
        self.why.setText(self._why.get(stage, ""))
        self.why.setVisible(bool(self.why.text()))
        if stage == NO_ACCOUNTS:
            label = "Add an account"
        elif stage == NO_TRANSACTIONS:
            label = "Import a CSV…"
        else:
            label = self._ready[0]
        self.primary.setText(label)
        self.primary.setVisible(bool(label))
        self.demo.setVisible(stage != READY)

    def _act(self) -> None:
        if self._stage == NO_ACCOUNTS:
            self.main.add_account()
        elif self._stage == NO_TRANSACTIONS:
            self.main.open_import()
        elif self._ready[1] is not None:
            self._ready[1]()
