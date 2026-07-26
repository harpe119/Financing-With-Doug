"""Startup splash — and Doug.

Two different delays hide behind "the app is slow to start", and they need
different fixes:

1. **Before Python runs.** A frozen one-file build unpacks itself to a temp
   directory first. Nothing here can help with that — PyInstaller's own
   ``--splash`` covers it, because the C bootloader draws it before the
   interpreter exists.
2. **After Python starts.** Importing pandas, numpy, PyQt6 and pyqtgraph costs
   a couple of seconds, then the database opens and six pages are built. That
   is this file's job.

The artwork is drawn in code rather than loaded from a PNG, which keeps the
build to a single ``--add-data`` entry for the sample CSVs and nothing else.

Doug supervises.
"""
from __future__ import annotations

import random
import time

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

from . import theme

_W, _H = 480, 280

# Doug was the first model, and Doug was not good. Doug has since read a book.
DOUG_LINES = [
    "Doug is warming up…",
    "Doug is carrying the one…",
    "Doug is counting on his fingers (all of them this time)…",
    "Doug has been told that money can be negative…",
    "Doug no longer divides by zero. Mostly.",
    "Doug is double-checking. Then triple-checking.",
    "Doug learned about compound interest and hasn't stopped talking about it…",
    "Doug is remembering everything you corrected him on…",
    "Doug is choosing not to guess this time…",
    "Doug has grown as a person. And as a program.",
    "Doug read the entire manual. Twice.",
    "Doug is being supervised by a slightly newer Doug…",
    "Doug promises the decimal point is in the right place…",
    "Doug thought about overfitting, and decided against it…",
    "Doug is sorting your receipts alphabetically, then giving up and sorting them by date…",
]

DOUG_FACTS = [
    "Doug's first model predicted every number would be 7. It was wrong 91% of the time, "
    "which Doug described as \u201ca strong start\u201d.",
    "Doug once classified a paycheck as a small sandwich. Doug has since apologised.",
    "Doug's original loss function was Doug simply feeling bad about it.",
    "Doug does not gamble. Doug has run the numbers.",
    "Doug has been asked to stop calling the Monte Carlo simulation \u201cthe casino\u201d.",
    "Doug wants you to know that past performance does not guarantee future results, "
    "and that Doug learned this the hard way.",
    "Doug worked as an intern on internal rocket ballistics simulation before"
    "Doug is not a licensed financial advisor. Doug is barely a licensed Doug.",
]


def doug_fact() -> str:
    return random.choice(DOUG_FACTS)


def _render(version: str) -> QPixmap:
    pm = QPixmap(_W, _H)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    card = QRectF(0, 0, _W, _H)
    grad = QLinearGradient(0, 0, 0, _H)
    grad.setColorAt(0.0, QColor(theme.SURFACE))
    grad.setColorAt(1.0, QColor(theme.BG))
    p.setBrush(grad)
    p.setPen(QPen(QColor(theme.BORDER), 1))
    p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

    # a rising line over a zero baseline — the product in one gesture
    base_y = _H - 86
    p.setPen(QPen(QColor(theme.RED), 2))
    p.drawLine(40, int(base_y), _W - 40, int(base_y))
    p.setPen(QPen(QColor(theme.ACCENT), 2.4))
    pts = [(40, base_y + 18), (128, base_y - 4), (216, base_y + 8),
           (300, base_y - 28), (376, base_y - 22), (_W - 40, base_y - 50)]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        p.drawLine(int(x1), int(y1), int(x2), int(y2))

    f = QFont()
    f.setPointSize(22)
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QPen(QColor(theme.TEXT)))
    p.drawText(QRectF(0, 38, _W, 40), Qt.AlignmentFlag.AlignHCenter,
               "Financing with Doug")

    f.setPointSize(9)
    f.setWeight(QFont.Weight.Normal)
    p.setFont(f)
    p.setPen(QPen(QColor(theme.MUTED)))
    p.drawText(QRectF(0, 74, _W, 20), Qt.AlignmentFlag.AlignHCenter,
               f"private personal finance  ·  v{version}")
    f.setPointSize(8)
    p.setFont(f)
    p.setPen(QPen(QColor(theme.ACCENT_DIM)))
    p.drawText(QRectF(0, 92, _W, 18), Qt.AlignmentFlag.AlignHCenter,
               "Doug has been studying.")
    p.end()
    return pm


class Splash(QSplashScreen):
    """Splash with a rotating status line and a guaranteed minimum lifetime."""

    def __init__(self, version: str, min_seconds: float = 2.6):
        super().__init__(_render(version))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._t0 = time.monotonic()
        self._min = float(min_seconds)
        self._lines = random.sample(DOUG_LINES, k=min(4, len(DOUG_LINES)))
        self._i = 0

    def status(self, text: str | None = None) -> None:
        """Show the next Doug line, or a specific message."""
        if text is None:
            text = self._lines[self._i % len(self._lines)]
            self._i += 1
        self.showMessage(
            text,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            QColor(theme.MUTED))
        QApplication.processEvents()

    def hold(self, window=None) -> None:
        """Keep the splash up for its minimum lifetime, then hand off.

        Startup is fast enough that the splash would otherwise flash past
        unread. Rather than blocking the thread with sleep(), this keeps the
        event loop turning so the window paints and the status line animates
        while the remaining time runs down.
        """
        deadline = self._t0 + self._min
        last = 0.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            now = time.monotonic()
            if now - last > 0.7:
                self.status()
                last = now
            QApplication.processEvents()
            time.sleep(0.03)
        if window is not None:
            self.finish(window)
        else:
            self.close()


def close_pyinstaller_splash() -> None:
    """Dismiss PyInstaller's bootloader splash, if this is a frozen build.

    ``pyi_splash`` only exists inside an executable built with ``--splash``,
    hence the guard.
    """
    try:
        import pyi_splash  # type: ignore
        pyi_splash.close()
    except Exception:
        pass
