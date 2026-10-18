"""A visible evidence check next to the forecast, with actionable detail."""
from __future__ import annotations

from ..util import fmt_day

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..data_quality import QualityReport, inspect_data_quality
from . import theme
from .widgets import Card, hint


class DataQualityCard(QWidget):
    """One line saying whether the forecast's inputs have gaps, and a way in.

    It used to be a card with a status, a paragraph and a button, in the middle
    of the Forecast page. The sentence that matters is "4 checks need review";
    the paragraph is what each check found, and that lives in the dialog behind
    the button (and in this line's tooltip).
    """

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.report: QualityReport | None = None
        # One line, as tall as its text. The Forecast page once sized it as a
        # vertical stack and made it twice that; this keeps a parent with room
        # to spare from stretching it either.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.status = QLabel("Not checked yet")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        # The full sentence, kept for the tooltip and for anything that reads it.
        self.detail = hint("")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.hide()
        self.review_btn = QPushButton("Review data")
        self.review_btn.setObjectName("small")
        self.review_btn.clicked.connect(self.show_details)
        row.addWidget(self.review_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _style(self, concerns: bool) -> None:
        name = "ledgerWarning" if concerns else "qualityOk"
        if self.status.objectName() != name:
            self.status.setObjectName(name)
            # Polishing again leaves the old rule's padding on a QLabel, so the
            # warning box came up with its text against the border; resetting
            # the sheet re-reads every rule that matches the new name.
            self.status.setStyleSheet(" ")
            self.status.setStyleSheet("")

    def clear(self) -> None:
        self.report = None
        self.status.setText("Data quality: not checked yet")
        self.status.setToolTip("")
        self.detail.setText("")
        self._style(False)

    def refresh(self, store) -> None:
        self.report = inspect_data_quality(store)
        self.status.setText(f"Data quality: {self.report.summary[0].lower()}{self.report.summary[1:]}")
        self._style(bool(self.report.concerns))
        concerns = "; ".join(c.title for c in self.report.concerns)
        latest = (fmt_day(self.report.last_transaction)
                  if self.report.last_transaction else "none recorded")
        self.detail.setText(
            (concerns + ".\n" if concerns else "")
            + f"Checked {fmt_day(self.report.as_of)} · Latest cash transaction: {latest}. "
            "These checks cannot establish that the forecast is accurate.")
        self.status.setToolTip(self.detail.text())

    def show_details(self) -> None:
        # Refresh at click time too: an import or balance edit may have changed
        # the store since this page last became visible.
        self.refresh(self.main.store)
        dialog = QDialog(self.window())
        dialog.setWindowTitle("Data quality — what the forecast is based on")
        dialog.resize(660, 680)
        layout = QVBoxLayout(dialog)
        layout.addWidget(hint(
            f"Checked {fmt_day(self.report.as_of)}. Review the records below before "
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
