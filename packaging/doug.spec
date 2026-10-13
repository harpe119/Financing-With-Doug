# PyInstaller spec for Financing with Doug.
#
#   pip install pyinstaller
#   pyinstaller packaging/doug.spec
#
# Output lands in dist/. Build on the OS you are shipping to — PyInstaller does
# not cross-compile, so a Windows .exe has to be built on Windows.
#
# Notes on the choices below:
#
# * onefile vs onedir. This spec builds onedir (a folder). It starts noticeably
#   faster, because onefile unpacks the whole bundle to a temp directory on
#   every launch. For handing to family, zip the folder — or flip ONEFILE to
#   True below if a single icon matters more than startup speed.
# * Data files are the sample CSVs, forecast video, and
#   Getting started guide in docs/ — all resolved through
#   finfore.util.resource_path, which follows sys._MEIPASS, so they work in
#   onedir and onefile alike. docs/ has to ship: Help → Getting started reads
#   the Markdown at runtime rather than embedding a copy, so a build without it
#   opens a window that apologises.
# * excludes matter more than usual here: pandas drags in matplotlib and
#   friends if they happen to be installed, which can double the bundle.
# * Icon: drop your icon file directly in this packaging/ folder, named
#   icon.ico on Windows or icon.icns on macOS (Linux ignores this — .desktop
#   files carry their own icon reference, set up separately). PyInstaller does
#   NOT convert formats for you: Windows needs a real multi-resolution .ico,
#   macOS needs a real .icns. A renamed .png will fail or be ignored. Use an
#   online converter or `iconutil`/ImageMagick if you only have a .png.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ONEFILE = False
APP_NAME = "Financing with Doug"

SPEC_DIR = Path(SPECPATH)     # PyInstaller injects SPECPATH at spec eval time
_icon_candidates = {
    "win32": SPEC_DIR / "icon.ico",
    "darwin": SPEC_DIR / "icon.icns",
}
ICON = _icon_candidates.get(sys.platform)
if ICON is not None and not ICON.exists():
    ICON = None    # not fatal — build proceeds with the default icon

hiddenimports = collect_submodules("pyqtgraph")

a = Analysis(
    ["../run.py"],
    pathex=[".."],
    binaries=[],
    datas=[("../sample_data", "sample_data"), ("../docs", "docs"),
           ("../assets/monte_carlo.mp4", "assets"),
           ("../assets/progress.mp4", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "IPython", "jupyter", "notebook",
        "PyQt5", "PySide2", "PySide6", "tkinter", "test", "unittest",
        "sqlite3.test", "pandas.tests", "numpy.tests",
        "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas,
        name=APP_NAME, console=False, upx=False,
        icon=str(ICON) if ICON else None,
    )
else:
    exe = EXE(
        pyz, a.scripts,
        exclude_binaries=True, name=APP_NAME, console=False, upx=False,
        icon=str(ICON) if ICON else None,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, name=APP_NAME,
    )

# macOS only: wrap the result in a proper .app bundle. Uncomment to use — the
# .app is what gives macOS users a double-clickable icon in Finder/Dock;
# without it they get a bare Unix executable inside the dist folder.
# app = BUNDLE(coll, name=f"{APP_NAME}.app",
#              icon=str(ICON) if ICON else None,
#              bundle_identifier="com.example.financingwithdoug")
