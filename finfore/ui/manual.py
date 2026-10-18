"""Open the bundled user manual in the system's PDF reader."""
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from ..util import resource_path


def manual_path() -> Path:
    return resource_path("docs", "manual", "financing_with_doug_manual.pdf")


def open_manual(parent=None) -> bool:
    path = manual_path()
    if not path.is_file():
        QMessageBox.warning(parent, "Manual unavailable",
                            "The user manual is missing from this installation. "
                            "Reinstall Doug to restore it.")
        return False
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(parent, "Couldn't open the manual",
                            f"Open this file in a PDF reader:\n{path}")
        return False
    return True
