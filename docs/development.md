# Working on the code

For whoever is editing this, rather than using it. The user-facing pages are
[`../README.md`](../README.md) and [`getting-started.md`](getting-started.md);
the maths is in [`methodology.md`](methodology.md); the release checklist is in
[`releasing.md`](releasing.md).

## Running from source

```bash
pip install -r requirements.txt
python run.py
```

Python ≥ 3.10, PyQt6 and pyqtgraph. Your data lives at
`~/.ledgerline/ledgerline.db` by default; pass `--db path.db` to use another
file — which is the safe way to poke at something without touching a real
ledger.

Run the test suite with `python tests/test_core.py`. It exercises ingestion and
dedup, transfer pairing, recurrence detection, history reconstruction, forecast
invariants and a fair amount of the UI, and `build.ps1` refuses to build if any
of it fails.

## The demo datasets

**File → Load demo dataset** offers five situations, each in its **own database
file** so a demo never touches a real ledger — **File → Return to my own data**
puts you back, and a banner marks demo data while you are in it.

| | grade | what it shows |
|---|---|---|
| **Balanced** | A− | The bundled 18-month ledger in two CSV dialects, a car loan, a brokerage and three goals — the only profile that also exercises the import mapper |
| **Struggling** | D+ | A deficit with wants over guideline: money is tight *and* the spending is part of why |
| **Thriving** | A+ | A 53% savings rate, investments compounding, debts nearly gone |
| **Turnaround** | B+ | Discretionary spending halves five months in — built to give the Progress page a real, statistically significant change to detect |
| **Spiralling** | F | Three high-interest debts, spending at 1.7× income, cash projected to −$47k within a year at 100% shortfall probability and a runway of zero months |

## Building a release

On Windows, one command does the lot:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

It runs the test suite first and refuses to build if anything fails — a broken
build that auto-installs itself onto five other machines is the one outcome
worth engineering against — then runs PyInstaller, wraps the result with Inno
Setup (`packaging/installer.iss`), and writes
`FinancingWithDoug-<version>-Setup.exe` plus a `SHA256SUMS.txt` into
`dist/installer/`. Upload **both** to the GitHub release: the checksum file is
what the in-app updater checks the download against, and the tag has to match
`__version__` for existing installs to notice. The version is read from
`finfore/__init__.py`, so it is written in one place and cannot drift from what
the app reports. [`releasing.md`](releasing.md) covers the whole loop.

**The bundle underneath.** `packaging/doug.spec` is usable on its own —
`pip install pyinstaller` then `pyinstaller packaging/doug.spec`, and the result
lands in `dist/`. PyInstaller does not cross-compile, so build on the OS you are
shipping to. The spec defaults to a folder build rather than one-file because
one-file unpacks itself on every launch; flip `ONEFILE` if a single icon matters
more. Bundled data is resolved through `finfore.util.resource_path`, which
follows `sys._MEIPASS` inside a frozen build and the source tree otherwise.

**Custom icon.** Drop the icon file directly in `packaging/`, next to
`doug.spec`, named:

- `icon.ico` on Windows — must be a real multi-resolution `.ico`, not a
  renamed `.png`
- `icon.icns` on macOS — must be a real `.icns`

The spec finds it automatically by filename and OS; nothing else to edit. If
you only have a `.png`, convert it first (an online favicon/ico generator
works for Windows; `iconutil` or ImageMagick for macOS). Linux ignores this —
icons there come from a `.desktop` file, a separate step not covered by the
spec.

**What to send when there is no installer.** On Windows the answer is the
`Setup.exe` above. Everywhere else, `dist/Financing with Doug/` is the whole
program — the binary plus every library it needs — so zip that entire folder
and send the zip; it will not run if separated from the files beside it. Don't
send just the executable. On macOS, enabling the `BUNDLE` step in the spec
(commented out by default) gets you a proper `Financing with Doug.app`, which
is itself the double-clickable, Dock-draggable unit — zip and send that
instead of the raw `dist/` folder. First launch will need a right-click → Open
on macOS (Gatekeeper), since the build isn't code-signed — worth a heads-up so
it doesn't look like a virus warning.

## Project layout

```
run.py                    entry point (QApplication + MainWindow)
finfore/
  models.py               dataclasses: accounts, recurring items, goals, scenario
  ingest.py               CSV sniffing, mapping, parsing, dedup hashing
  categorize.py           keyword rules + internal-transfer pairing
  insights.py             taxonomy, habits score, composition, suggestions
  goals.py                priority ladder over debts + goals, snowball cascade
  progress.py             checkpoints, path adherence, permutation test
  learning.py             correction capture, merchant clustering, similarity, ML seam
  recurrence.py           normalization, cadence classification, occurrences
  history.py              balance reconstruction and headline statistics
  forecast.py             deterministic + Monte Carlo engine, loans, goals
  pipeline.py             import + refresh-derived orchestration
  alerts.py               bills that moved, gated on each bill's own MAD
  dedup.py                one transaction a bank exported twice, found by balance
  report.py               one calendar month as a self-contained HTML page
  store.py                SQLite persistence
  undo.py                 snapshot undo/redo stack (Ctrl+Z), bounded in memory
  update.py               "is there something newer?" — stdlib only, no Qt
  demo.py                 one-click demo loader
  ui/                     PyQt6 theme, charts, dialogs, eight pages
  ui/prefs.py             the one door to QSettings (and the test suite's seam)
  ui/video.py             bundled explainer videos, with a system-player fallback
  ui/guide.py             renders docs/getting-started.md in-app
docs/getting-started.md   the user-facing walkthrough (Help -> Getting started)
docs/methodology.md       every formula, and why it is that one
docs/development.md       this file
docs/releasing.md         bump, build, tag, publish — and what family sees
docs/whats-new.md         plain-English release notes (published with the release)
docs/images/              screenshots used by README.md
packaging/build.ps1       test → PyInstaller → Inno Setup → checksums
packaging/doug.spec       the PyInstaller bundle
packaging/installer.iss   the per-user Windows installer
assets/                   explainer videos (bundled into the build)
scripts/generate_sample_data.py   seeded synthetic bank exports
sample_data/              bundled demo CSVs + meta.json
tests/test_core.py        core-logic test suite
```

## Adding screenshots to the README

Pictures live in `docs/images/` and are referenced with a repo-relative path:

```markdown
![Alt text describing the picture](docs/images/dashboard.png)
```

`README.md` has commented-out placeholders at each spot where one belongs —
search it for `PICTURE:`. Fill one in by dropping the file into `docs/images/`
and deleting the `<!--` and `-->` around the line beneath it.

PNG is the right format for screenshots. Keep them under about 500 KB each —
GitHub stores every version of a binary file forever, so a 4 MB screenshot
replaced three times is 12 MB in the repo permanently. Windows' **Win+Shift+S**
gives you a cropped grab; anything that saves as PNG at roughly 1200 px wide is
plenty.
