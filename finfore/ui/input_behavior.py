"""Shared interaction rules for data entry controls."""
from PyQt6.QtCore import QEvent, QObject, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication,
                             QComboBox, QLineEdit, QSlider)


class EntryWheelGuard(QObject):
    """Let wheel events reach the page without editing the hovered entry.

    Apply even when the entry has focus. Clicking or typing is deliberate;
    scrolling the surrounding page should never change saved values.
    Scrollbars, chart navigation and open dropdown lists keep their scrolling.
    """

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        entry = watched
        if isinstance(entry, QLineEdit):
            entry = entry.parentWidget()
        if not isinstance(entry, (QAbstractSpinBox, QComboBox, QSlider)):
            return False
        # Consuming the event alone would leave a dead patch in the page.
        # Forward it to the enclosing scroll area's viewport, preserving both
        # mouse-wheel steps and touchpad pixel deltas.
        parent = entry.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                viewport = parent.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(),
                    event.inverted(), device=event.pointingDevice())
                QApplication.sendEvent(viewport, forwarded)
                if forwarded.isAccepted():
                    break
            parent = parent.parentWidget()
        event.accept()
        return True


def install_entry_wheel_guard(app):
    """Cover existing controls, dialogs and dynamically created table editors."""
    if not hasattr(app, "_entry_wheel_guard"):
        app._entry_wheel_guard = EntryWheelGuard(app)
        app.installEventFilter(app._entry_wheel_guard)
