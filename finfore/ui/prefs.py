"""Where the app's own preferences live.

Preferences are the Simple/Everything choice and the date of the last update
check — small things, but they are read on startup and they decide what the
window looks like before anyone has clicked anything.

One function, for two reasons. The organisation/application pair is what
selects the storage location (a registry key on Windows, a plist on macOS, an
ini file on Linux), so writing it out at each call site means a typo opens a
second, empty store of settings that reads as "it forgot my choice". And it is
the seam the test suite redirects: the pair addresses the *developer's own*
settings, so without somewhere to redirect, a test asserting a default asserts
whatever was last clicked in the real app, and the suite writes its values back
over it. `build.ps1` refuses to build on a failing test, which is how a UI
toggle ends up blocking a release.
"""
from __future__ import annotations

from PyQt6.QtCore import QSettings

ORG = "Doug"
APP = "Financing with Doug"


def app_settings() -> QSettings:
    """The app's preference store. Always reached through here."""
    return QSettings(ORG, APP)
