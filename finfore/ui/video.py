"""Bundled explainer videos, played in a dialog.

Qt Multimedia is used when it is available and working, and the system's own
video player when it is not. That fallback is not defensive padding: Qt's
multimedia stack is a separate backend (ffmpeg on Windows, AVFoundation on
macOS, GStreamer on Linux) whose plugins are the single most common thing to
go missing from a PyInstaller bundle, and the failure mode is a black
rectangle with no error. Handing the file to the OS always works, so a video
that cannot play in-app still plays.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QSlider, QVBoxLayout)

from ..util import resource_path
from .widgets import hint

# Videos live in assets/ and resolve through resource_path, so the same call
# works from a source checkout and from inside a frozen build.
VIDEOS = {
    "monte_carlo": ("assets/monte_carlo.mp4",
                    "How the Monte Carlo forecast works"),
}


def video_path(key: str) -> Path | None:
    """Absolute path to a bundled video, or None if it isn't there."""
    rel, _title = VIDEOS.get(key, (None, None))
    if rel is None:
        return None
    p = resource_path(*rel.split("/"))
    return p if p.exists() else None


def has_video(key: str) -> bool:
    return video_path(key) is not None


def _multimedia():
    """Import Qt Multimedia, or return None if this build hasn't got it."""
    try:
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        return QMediaPlayer, QAudioOutput, QVideoWidget
    except Exception:
        return None


def open_externally(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class VideoDialog(QDialog):
    """A minimal player: play/pause, a scrubber, and a way out to the OS."""

    def __init__(self, path: Path, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(760, 520)
        self._path = path
        self._duration = 0
        self._recovered = False

        mm = _multimedia()
        if mm is None:
            raise RuntimeError("Qt Multimedia is not available in this build.")
        QMediaPlayer, QAudioOutput, QVideoWidget = mm

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        self.view = QVideoWidget()
        self.view.setMinimumHeight(300)
        lay.addWidget(self.view, 1)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view)

        bar = QHBoxLayout()
        self.play_btn = QPushButton("Pause")
        self.play_btn.setObjectName("small")
        self.play_btn.clicked.connect(self._toggle)
        bar.addWidget(self.play_btn)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 0)
        self.scrub.sliderMoved.connect(self.player.setPosition)
        bar.addWidget(self.scrub, 1)
        self.clock = QLabel("0:00 / 0:00")
        self.clock.setObjectName("hint")
        bar.addWidget(self.clock)
        lay.addLayout(bar)

        foot = QHBoxLayout()
        self.note = hint("")
        foot.addWidget(self.note, 1)
        ext = QPushButton("Open in my video player")
        ext.setObjectName("small")
        ext.setToolTip("Play this in whatever app normally opens .mp4 files")
        ext.clicked.connect(lambda: open_externally(self._path))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot.addWidget(ext)
        foot.addWidget(close)
        lay.addLayout(foot)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)

        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    # -----------------------------------------------------------------
    @staticmethod
    def _mmss(ms: int) -> str:
        s = max(0, ms) // 1000
        return f"{s // 60}:{s % 60:02d}"

    def _on_duration(self, ms: int) -> None:
        self._duration = ms
        self.scrub.setRange(0, ms)
        self._sync_clock()

    def _on_position(self, ms: int) -> None:
        if not self.scrub.isSliderDown():
            self.scrub.setValue(ms)
        self._sync_clock()

    def _sync_clock(self) -> None:
        self.clock.setText(f"{self._mmss(self.player.position())} / "
                           f"{self._mmss(self._duration)}")

    def _on_state(self, state) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("Pause" if playing else "Play")

    def _toggle(self) -> None:
        from PyQt6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_error(self, _err, msg: str) -> None:
        """Hand the file to the OS rather than showing a dead rectangle.

        A backend failure arrives here as a signal, not an exception — there is
        no other place to catch it. It means the multimedia plugin could not be
        loaded (missing ffmpeg libraries beside the plugin is the usual cause,
        and a bundle is where that happens), so retrying in-app is pointless.
        Opening the file externally is the one thing that reliably works, so do
        that instead of asking the user to click a second button to get what
        they already asked for.
        """
        if self._recovered:
            return
        self._recovered = True
        self.note.setText(f"Couldn't play it here ({msg or 'no codec'}) — "
                          f"opening in your video player instead.")
        self.note.setStyleSheet("color: #E0A83C;")
        opened = open_externally(self._path)
        if opened:
            self.accept()
        else:
            self.note.setText(
                f"Couldn't play it here ({msg or 'no codec'}), and no app is "
                f"registered for .mp4 files. The video is at {self._path}.")

    def closeEvent(self, event) -> None:          # noqa: N802
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass
        super().closeEvent(event)


def play(key: str, parent=None) -> bool:
    """Show ``key``'s video. Returns False only if there is nothing to show."""
    path = video_path(key)
    if path is None:
        QMessageBox.information(
            parent, "No video bundled",
            "This copy doesn't include the explainer video. It lives at "
            f"assets/{VIDEOS.get(key, ('?',))[0].split('/')[-1]} in the "
            "project folder.")
        return False
    title = VIDEOS[key][1]
    if _multimedia() is None:
        open_externally(path)
        return True
    try:
        dlg = VideoDialog(path, title, parent)
    except Exception:
        open_externally(path)
        return True
    dlg.exec()
    return True
