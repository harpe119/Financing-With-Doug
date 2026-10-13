"""Open the bundled forecast explanation in the computer's video player."""
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox

from ..util import resource_path


def video_path(kind="forecast") -> Path | None:
    filename = {"forecast": "monte_carlo.mp4", "progress": "progress.mp4"}[kind]
    path = resource_path("assets", filename)
    return path if path.is_file() else None


def play(parent=None, kind="forecast") -> bool:
    path = video_path() if kind == "forecast" else video_path(kind)
    if path is None:
        QMessageBox.warning(parent, "Video unavailable",
                            f"The {kind} video is missing from this installation.")
        return False
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        QMessageBox.warning(parent, "Couldn't open the video",
                            f"Open this file in a video player:\n{path}")
        return False
    return True
