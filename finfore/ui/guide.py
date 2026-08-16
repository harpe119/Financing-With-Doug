"""The bundled Getting started guide, shown in a readable window.

Written once as Markdown in ``docs/`` so the same file is legible in a text
editor, on GitHub, and inside the app — Qt renders Markdown natively, so there
is no conversion step to keep in sync and no second copy to drift.

If the file is missing from a build the window says so and offers the folder,
rather than opening blank.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QPushButton, QTextBrowser,
                             QVBoxLayout)

from ..util import resource_path
from . import theme

DOC = "docs/getting-started.md"


def guide_path() -> Path | None:
    p = resource_path(*DOC.split("/"))
    return p if p.exists() else None


class GuideDialog(QDialog):
    """A scrollable reader for the guide."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Getting started with Financing with Doug")
        self.setMinimumSize(760, 620)
        self.resize(860, 760)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        # A measured line length and generous leading — this is the one place in
        # the app someone reads several hundred words in a row, and the dense
        # dashboard styling is wrong for that.
        self.view.document().setDefaultStyleSheet(
            f"body {{ color: {theme.TEXT}; }}"
            f"h1 {{ color: {theme.ACCENT}; }}"
            f"h2 {{ color: {theme.ACCENT}; }}"
            f"h3 {{ color: {theme.BLUE}; }}"
            f"a  {{ color: {theme.BLUE}; }}"
            f"code {{ color: {theme.AMBER}; }}"
            f"blockquote {{ color: {theme.MUTED}; }}"
            f"th {{ color: {theme.MUTED}; }}")
        f = QFont()
        f.setPointSize(10)
        self.view.setFont(f)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.open_btn = QPushButton("Open in my text editor")
        self.open_btn.setObjectName("small")
        self.open_btn.setToolTip("Open the Markdown file itself, to read or "
                                 "print outside the app")
        self.open_btn.clicked.connect(self._open_externally)
        close = QPushButton("Close")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        row.addWidget(self.open_btn)
        row.addWidget(close)
        lay.addLayout(row)

        self._load()

    # -----------------------------------------------------------------
    def _load(self) -> None:
        path = guide_path()
        if path is None:
            self.open_btn.setEnabled(False)
            self.view.setMarkdown(
                "# Getting started\n\n"
                "The guide file did not ship with this copy. It should be at "
                f"`{DOC}` inside the program folder.\n\n"
                "Everything it covers is also in **Help → Methodology** and in "
                "`docs/methodology.md` beside the program.\n")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            self.view.setMarkdown(f"# Getting started\n\nCouldn't read the "
                                  f"guide: {e}\n")
            return
        self.view.setMarkdown(text)
        # setMarkdown leaves the cursor wherever it lands; start at the top.
        self.view.moveCursor(self.view.textCursor().MoveOperation.Start)
        self.view.ensureCursorVisible()

    def _open_externally(self) -> None:
        path = guide_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def show_guide(parent=None) -> None:
    GuideDialog(parent).exec()
