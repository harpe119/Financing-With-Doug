"""A visible evidence check next to the forecast, with actionable detail."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from ..data_quality import QualityReport, inspect_data_quality
from . import theme
from .widgets import Card, hint


class DataQualityCard(Card):
    def __init__(self, main):
        super().__init__("Data quality")
        self.main = main
        self.report: QualityReport | None = None
        self.status = QLabel("Not checked yet")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self.detail = hint("")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.detail)
        self.review_btn = QPushButton("Review data")
        self.review_btn.setObjectName("small")
        self.review_btn.clicked.connect(self.show_details)
        row = QHBoxLayout()
        row.addWidget(self.review_btn)
        row.addStretch(1)
        self.body.addLayout(row)

    def clear(self) -> None:
        self.report = None
        self.status.setText("Not checked yet")
        self.detail.setText("")

    def refresh(self, store) -> None:
        self.report = inspect_data_quality(store)
        self.status.setText(self.report.summary)
        self.status.setStyleSheet(f"color: {theme.AMBER if self.report.concerns else theme.TEXT};")
        concerns = "; ".join(c.title for c in self.report.concerns)
        latest = (self.report.last_transaction.isoformat()
                  if self.report.last_transaction else "none recorded")
        self.detail.setText(
            (concerns + ".\n" if concerns else "")
            + f"Checked {self.report.as_of.isoformat()} · Latest cash transaction: {latest}. "
            "These checks cannot establish that the forecast is accurate.")

    def show_details(self) -> None:
        # Refresh at click time too: an import or balance edit may have changed
        # the store since this page last became visible.
        self.refresh(self.main.store)
        dialog = QDialog(self.window())
        dialog.setWindowTitle("Data quality — what the forecast is based on")
        dialog.resize(660, 680)
        layout = QVBoxLayout(dialog)
        layout.addWidget(hint(
            f"Checked {self.report.as_of.isoformat()}. Review the records below before "
            "interpreting a financial trend. These reminders do not measure forecast accuracy."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        # Put issues first, but also expose the checks that found no issue.
        ordered = sorted(self.report.checks, key=lambda c: not c.needs_review)
        for check in ordered:
            card = Card(check.title)
            flag = QLabel("Review" if check.needs_review else "No issue flagged")
            flag.setStyleSheet(f"color: {theme.AMBER if check.needs_review else theme.MUTED};")
            card.body.addWidget(flag)
            explanation = hint(check.detail)
            # Account names come from imported/user-controlled data, not HTML.
            explanation.setTextFormat(Qt.TextFormat.PlainText)
            card.body.addWidget(explanation)
            action = QPushButton(check.action)
            action.setObjectName("small")

            def navigate(_checked=False, page=check.page):
                dialog.accept()
                self.main.show_page(page)

            action.clicked.connect(navigate)
            card.body.addWidget(action)
            rows.addWidget(card)
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
