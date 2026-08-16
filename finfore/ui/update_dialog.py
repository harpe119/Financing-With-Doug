"""The update button, and the quiet check behind it.

Two entry points. **Check for updates**, its own button on the menu bar, is the
one people press, and it answers even when the answer is "you are up to date" —
a button that does nothing visible when there is no news reads as broken. The
startup check is silent unless there is something to say, runs on a background
thread so it cannot stall the window, and holds itself to once a day.

Nothing downloads without being asked, and nothing is ever run without the
release notes having been shown first.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QMessageBox, QProgressDialog, QPushButton,
                             QTextBrowser, QVBoxLayout)

from .. import __version__, update
from . import prefs, theme

_LAST_CHECK = "update/last_check"
_ENABLED = "update/check_on_start"


def frozen() -> bool:
    """Is this the packaged app, as opposed to a source checkout?

    It matters: the installer replaces an installed program. Run from source,
    the honest advice is `git pull`, and offering to download an installer over
    the top of a working tree would be actively unhelpful.
    """
    return bool(getattr(sys, "frozen", False))


class _Checker(QObject):
    """Runs one check off the GUI thread and reports back on it."""
    done = pyqtSignal(object, str)          # (Release | None, error)

    def start(self) -> None:
        def work():
            try:
                rel = update.check(__version__)
                self.done.emit(rel, "")
            except update.UpdateError as e:
                self.done.emit(None, str(e))
            except Exception as e:           # noqa: BLE001 — never crash a thread
                self.done.emit(None, f"{type(e).__name__}: {e}")
        threading.Thread(target=work, daemon=True).start()


class UpdateDialog(QDialog):
    """What is new, and one button to get it."""

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release
        self.chose_install = False
        self.setWindowTitle("An update is available")
        self.setMinimumSize(520, 400)
        self.resize(620, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(9)

        head = QLabel(f"Version {release.version} is available")
        head.setStyleSheet("font-size: 17px; font-weight: 600;")
        lay.addWidget(head)

        bits = [f"You have {__version__}."]
        if release.published:
            bits.append(f"Released {release.published.strftime('%d %B %Y')}.")
        if release.has_installer:
            bits.append(f"{release.asset_name} · {release.size_mb:.0f} MB.")
        sub = QLabel(" ".join(bits))
        sub.setObjectName("hint")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        notes = QTextBrowser()
        notes.setObjectName("infoBody")
        notes.setOpenExternalLinks(True)
        notes.document().setDefaultStyleSheet(
            f"body {{ color: {theme.TEXT}; }}"
            f"h1, h2, h3 {{ color: {theme.ACCENT}; }}"
            f"code {{ color: {theme.AMBER}; }}")
        notes.setMarkdown(release.notes or "_No notes were published with this "
                                           "release._")
        notes.moveCursor(notes.textCursor().MoveOperation.Start)
        lay.addWidget(notes, 1)

        lay.addWidget(QLabel(
            "Your accounts, transactions and settings are not touched by an "
            "update — they live in a separate file."))

        row = QHBoxLayout()
        page = QPushButton("Open the release page")
        page.setObjectName("small")
        page.clicked.connect(self._open_page)
        row.addWidget(page)
        row.addStretch(1)
        later = QPushButton("Not now")
        later.clicked.connect(self.reject)
        row.addWidget(later)
        if release.has_installer and frozen():
            go = QPushButton("Download and install")
            go.setObjectName("primary")
            go.setDefault(True)
            go.clicked.connect(self._install)
            row.addWidget(go)
        lay.addLayout(row)

        if not frozen():
            lay.addWidget(QLabel(
                "This copy is running from source, so there is nothing for an "
                "installer to replace — use git pull instead."))

    def _open_page(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        if self.release.page_url:
            QDesktopServices.openUrl(QUrl(self.release.page_url))

    def _install(self) -> None:
        self.chose_install = True
        self.accept()


def _download_and_run(release, parent) -> bool:
    """Fetch the installer, then hand over to it and quit.

    Windows cannot replace a running executable, so the sequence has to be
    *launch the installer, then exit* — not the other way round. The installer
    waits for this process to go away before it starts copying.
    """
    dest = Path(tempfile.gettempdir()) / "financing-with-doug-update"
    bar = QProgressDialog("Downloading the update…", "Cancel", 0, 100, parent)
    bar.setWindowTitle("Downloading")
    bar.setWindowModality(Qt.WindowModality.ApplicationModal)
    bar.setMinimumDuration(0)
    bar.setValue(0)

    def progress(done: int, total: int) -> bool:
        if total:
            bar.setMaximum(100)
            bar.setValue(int(100 * done / total))
            bar.setLabelText(f"Downloading the update… "
                             f"{done / 1_048_576:.0f} of "
                             f"{total / 1_048_576:.0f} MB")
        else:
            bar.setMaximum(0)          # length unknown: an indeterminate bar
        QApplication.processEvents()
        return not bar.wasCanceled()

    try:
        path = update.download(release, dest, progress=progress)
    except update.UpdateError as e:
        bar.close()
        if str(e) != "cancelled":
            QMessageBox.warning(parent, "The update didn't download", str(e))
        return False
    finally:
        bar.close()

    box = QMessageBox(parent)
    box.setWindowTitle("Ready to install")
    box.setText(f"Version {release.version} has been downloaded.")
    box.setInformativeText(
        "Financing with Doug will close and the installer will open. Follow "
        "the prompts, and it will start again when it's done.\n\n"
        "Your data is not affected.")
    box.setStandardButtons(QMessageBox.StandardButton.Cancel)
    go = box.addButton("Close and install", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(go)
    box.exec()
    if box.clickedButton() is not go:
        return False

    try:
        if sys.platform == "win32":
            os.startfile(str(path))          # noqa: S606 — a file we just fetched
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:                   # noqa: BLE001
        QMessageBox.warning(
            parent, "Couldn't start the installer",
            f"{e}\n\nThe file is saved at:\n{path}\n\nRunning it by hand does "
            f"the same thing.")
        return False
    QTimer.singleShot(0, QApplication.instance().quit)
    return True


# ---------------------------------------------------------------------------
def check_now(parent) -> None:
    """The menu bar's Check for updates button.  Always answers."""
    if not update.configured():
        QMessageBox.information(
            parent, "Update checking isn't set up",
            "This build doesn't know where to look for updates.\n\n"
            "Set GITHUB_OWNER in finfore/update.py to the account hosting the "
            "releases, then rebuild.")
        return

    parent.setCursor(Qt.CursorShape.WaitCursor)
    checker = _Checker(parent)

    def finished(rel, err) -> None:
        parent.unsetCursor()
        prefs.app_settings().setValue(_LAST_CHECK, date.today().isoformat())
        if err:
            QMessageBox.warning(parent, "Couldn't check for updates", err)
        elif rel is None:
            QMessageBox.information(
                parent, "You're up to date",
                f"Version {__version__} is the latest there is.")
        else:
            _offer(rel, parent)

    checker.done.connect(finished)
    parent._update_checker = checker         # keep it alive past this frame
    checker.start()


def check_quietly(parent) -> None:
    """The startup check: speaks only if there is something to say.

    Once a day at most, off the GUI thread, and silent about failures — a
    laptop opened on a train should not be greeted with a network error.
    """
    if not (update.configured() and frozen()):
        return
    s = prefs.app_settings()
    if not s.value(_ENABLED, True, type=bool):
        return
    if str(s.value(_LAST_CHECK, "")) == date.today().isoformat():
        return

    checker = _Checker(parent)

    def finished(rel, err) -> None:
        s.setValue(_LAST_CHECK, date.today().isoformat())
        if rel is not None and not err:
            _offer(rel, parent)

    checker.done.connect(finished)
    parent._update_checker = checker
    QTimer.singleShot(2500, checker.start)   # let the window settle first


def _offer(release, parent) -> None:
    dlg = UpdateDialog(release, parent)
    dlg.exec()
    if dlg.chose_install:
        _download_and_run(release, parent)
