# Shipping a version

Everything here is for you, not for the people you hand the app to. They get a
`Setup.exe` and a Next-Next-Finish wizard.

---

## One-time setup

**1. Install the two build tools.**

```powershell
# into the same environment you run the app with — not whatever `python` is on PATH
C:\Users\caleb\VSCODE\VENV\Scripts\python.exe -m pip install pyinstaller
```

and [Inno Setup 6](https://jrsoftware.org/isdl.php) — the standard, free Windows
installer builder. Accept its defaults.

**2. Tell the app where its releases live.** In [`finfore/update.py`](../finfore/update.py):

```python
GITHUB_OWNER = "your-github-username"     # currently empty
GITHUB_REPO  = "FinancingWithDoug"
```

Until this is set, **Help → Check for updates…** politely says update checking
isn't configured rather than failing like a network error.

---

## Two changelogs, on purpose

| File | Who reads it | What goes in |
|---|---|---|
| `CHANGELOG.md` | you, later | why a thing was done, what was tested, what broke |
| `docs/whats-new.md` | your family | what they will notice, in plain sentences |

`docs/whats-new.md` is what gets published as the **release notes**, which
means it is also what the app shows in the *"an update is available"* window.
That is the whole reason it exists: the one moment somebody is guaranteed to
read your notes is when they are deciding whether to click Install, and a wall
of implementation detail at that moment tells them nothing.

Write both. They are different documents about the same work, not one document
written twice.

---

## Every release

```powershell
# 1. bump the version — this one line is the only place it is written
#    finfore/__init__.py:  __version__ = "1.16.0"
# 2. write the engineering entry in CHANGELOG.md
# 3. write the plain-English entry in docs/whats-new.md
# 4. build
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

`build.ps1` runs the test suite first and refuses to build if anything fails —
a broken build that auto-installs itself onto five other machines is the one
outcome worth engineering against. It then produces, in `dist\installer\`:

| File | What it is |
|---|---|
| `FinancingWithDoug-1.16.0-Setup.exe` | the download |
| `SHA256SUMS.txt` | what the in-app updater checks the download against |

**Upload both** to the GitHub release. A missing `SHA256SUMS.txt` isn't fatal —
the updater treats it as "no checksum published" — but publishing it turns a
truncated download into a clear message instead of a corrupt install.

```powershell
git tag v1.16.0
git push --tags
# --notes-file, not --notes: the plain-English entry is what family sees in the
# update window, so it is the thing worth publishing.
gh release create v1.16.0 `
  "dist\installer\FinancingWithDoug-1.16.0-Setup.exe" `
  "dist\installer\SHA256SUMS.txt" `
  --title "v1.16.0" --notes-file docs\whats-new-latest.md
```

(`gh` takes the whole file, so keep a trimmed copy of just this version's
section, or paste the section with `--notes`. The full `docs/whats-new.md`
accumulates every version and would republish the lot each time.)

The tag must match the version — the updater compares `tag_name` against
`__version__`, so `v1.16.0` (or `1.16.0`; the leading `v` is stripped) is what
makes existing installs notice.

Useful flags: `-SkipBuild` reuses `dist\` when you have only changed the
installer script; `-SkipInstaller` stops after PyInstaller; `-Python <path>`
overrides interpreter detection.

---

## What the family sees

- **Installing.** Double-click, Next, Next, Finish. No administrator password —
  it installs per-user into `%LOCALAPPDATA%`, which is the difference between
  "double-click it" and "ring me". A desktop shortcut is offered.
- **SmartScreen.** The first run of an unsigned installer shows *"Windows
  protected your PC"*. **More info → Run anyway.** This is unavoidable without
  a code-signing certificate (~$200–400/year, and even then reputation takes
  time to build). Send a screenshot with the link; it only happens once.
- **Updating.** The app checks GitHub once a day in the background and says
  nothing unless there is something newer. When there is, it shows the release
  notes and offers **Download and install**; it verifies the checksum, closes
  itself, runs the installer, and reopens. **Help → Check for updates…** does
  the same on demand and always answers, including "you're up to date".
- **Their data is never touched** — by an update or by an uninstall. It lives in
  `%USERPROFILE%\.ledgerline\ledgerline.db`, outside the install directory. The
  uninstaller says so before it runs.

---

## Sharing a private repo with family

The awkward one. Three options:

| | What they do | Cost |
|---|---|---|
| **Public repo** | Click a link | Your *code* is public — your data never was, it is all local SQLite |
| Private + collaborators | Need a GitHub account, signed in, to download; the updater needs a token **compiled into the app** | Free, but a token inside a downloadable app is not really private |
| Private dev repo + small public releases repo | Click a link | Two repos to keep in step |

**Recommended: make it public.** There are no secrets in the source, no
database ever leaves the machine, and it removes every obstacle at once — no
accounts, no tokens, and the updater becomes a plain anonymous HTTPS request.

If it must stay private, the two-repo split is the clean version: development
stays private, and a public `financing-with-doug-releases` repo holds nothing
but the installers. Point `GITHUB_REPO` at that one.

Avoid embedding a personal access token. It grants read access to the whole
repo to anyone who unzips the app, and it expires — at which point everyone's
update button breaks at once.
