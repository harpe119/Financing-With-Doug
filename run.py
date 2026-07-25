"""Financing with Doug — entry point.

    python run.py            # uses ~/.ledgerline/ledgerline.db
    python run.py --db my.db # explicit database file
"""
import argparse
import sys
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMessageBox

from finfore.store import Store
from finfore.ui import theme
from finfore.ui.main_window import MainWindow


def _install_excepthook() -> None:
    """Report unhandled exceptions instead of dying silently.

    PyQt aborts the process when a Python exception escapes a slot invoked from
    C++ — the user sees the window vanish with no message and no way to report
    what happened. Replacing sys.excepthook keeps the app alive and shows what
    broke, which turns a hard crash into a recoverable annoyance.
    """
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(text)
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Doug hit an error")
            box.setText("Doug did something wrong, but the app is still running.\n"
                        "If this keeps happening, the details below are the "
                        "useful part of a bug report.")
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass
    sys.excepthook = hook


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Financing with Doug — local personal-finance history "
                    "and forecasting")
    ap.add_argument("--db",
                    default=str(Path.home() / ".ledgerline" / "ledgerline.db"),
                    help="SQLite database file (created if missing)")
    ap.add_argument("--no-splash", action="store_true",
                    help="skip the startup splash (Doug will be hurt)")
    ap.add_argument("--splash-seconds", type=float, default=2.6,
                    help="how long to keep the splash up (default 2.6)")
    # A frozen GUI build can be handed arguments by the OS (macOS passes -psn_*),
    # so unknown flags are ignored rather than aborting with a usage error.
    args, _unknown = ap.parse_known_args()

    app = QApplication(sys.argv)
    app.setApplicationName("Financing with Doug")
    app.setOrganizationName("Doug")
    _install_excepthook()
    theme.apply(app)

    from finfore import __version__
    from finfore.ui.splash import Splash, close_pyinstaller_splash
    close_pyinstaller_splash()          # hand off from the bootloader splash

    splash = None
    if not args.no_splash:
        splash = Splash(__version__, min_seconds=args.splash_seconds)
        splash.show()
        splash.status("Waking Doug up…")

    store = Store(args.db)
    if splash:
        splash.status()
    win = MainWindow(store, real_db_path=args.db)
    if splash:
        splash.hold(win)          # keep it up long enough to actually read
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
