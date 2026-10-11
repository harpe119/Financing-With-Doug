"""Open the bundled forecast explanation in the computer's video player."""
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from ..util import resource_path


def video_path() -> Path | None:
    path = resource_path("assets", "monte_carlo.mp4")
    return path if path.is_file() else None


def play(parent=None) -> bool:
    path = video_path()
    if path is None:
        QMessageBox.warning(parent, "Video unavailable",
                            "The forecast video is missing from this installation.")
        return False
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(parent, "Couldn't open the video",
                            f"Open this file in a video player:\n{path}")
        return False
    return True
