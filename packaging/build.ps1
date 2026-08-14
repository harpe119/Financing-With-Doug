# Build a releasable Windows installer, end to end.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Produces, in dist\installer\ :
#   FinancingWithDoug-<version>-Setup.exe   the thing family downloads
#   SHA256SUMS.txt                          what the in-app updater verifies
#
# Upload BOTH to the GitHub release. The updater treats a missing SHA256SUMS.txt
# as "no checksum published" and carries on, but publishing it is what turns a
# truncated download into a clear message instead of a corrupt install.
#
# Requirements:
#   pip install pyinstaller
#   Inno Setup 6            https://jrsoftware.org/isdl.php
#
# The version is read from finfore/__init__.py, so it is never typed twice and
# cannot drift from what the app reports under Help -> About.

[CmdletBinding()]
param(
    [switch]$SkipBuild,      # reuse an existing dist\ (installer changes only)
    [switch]$SkipInstaller,  # PyInstaller only, no .exe wrapper
    [string]$Python = ""     # interpreter to build with; auto-detected if empty
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- which interpreter -----------------------------------------------------
# This matters more than it looks. PyInstaller bundles the Python and the Qt of
# whichever interpreter runs it, so building with a different one from the one
# the app is developed against ships a different app - or fails halfway through
# with an import error that says nothing about the real cause. `python` on PATH
# is very often not the venv you think it is.
function Resolve-Python {
    param([string]$Explicit)
    $candidates = @()
    if ($Explicit)          { $candidates += $Explicit }
    if ($env:VIRTUAL_ENV)   { $candidates += (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe") }
    $candidates += @(
        (Join-Path $root ".venv\Scripts\python.exe"),
        (Join-Path (Split-Path -Parent $root) "VENV\Scripts\python.exe"),
        "python"
    )
    # The probe asks Python to *report* whether PyInstaller is importable
    # rather than trying to import it, so a missing package produces an exit
    # code and not a traceback. That matters: redirecting a native command's
    # stderr in Windows PowerShell wraps every line in an ErrorRecord, which
    # $ErrorActionPreference = "Stop" then treats as a fatal error - so the
    # script would die while merely looking for an interpreter.
    $probe = @"
import sys
try:
    import importlib.util as u
    sys.exit(0 if u.find_spec('PyInstaller') else 1)
except Exception:
    sys.exit(1)
"@
    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if ($c -ne "python" -and -not (Test-Path $c)) { continue }
        try {
            & $c -c $probe
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch {
            continue        # not on PATH at all
        }
    }
    return $null
}

$py = Resolve-Python -Explicit $Python
if (-not $py) {
    throw @"
No interpreter with PyInstaller installed was found.

Install it into the environment you run the app with:
    <that python> -m pip install pyinstaller

or point this script at one:
    packaging\build.ps1 -Python C:\path\to\venv\Scripts\python.exe
"@
}
$pyVer = (& $py -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
Write-Host "Building with $py (Python $pyVer)" -ForegroundColor DarkGray

# --- running the tools -----------------------------------------------------
# Build tools log their progress to stderr as a matter of course - PyInstaller
# and ISCC both do, and neither means anything is wrong by it. Windows
# PowerShell turns a native command's stderr into ErrorRecords, so with
# $ErrorActionPreference = "Stop" the first ordinary line of a build log
# becomes a fatal error and the build dies at 1% having done nothing wrong.
# The exit code is the only signal that actually means success or failure.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try   { & $Exe @Arguments }
    finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

# --- version ---------------------------------------------------------------
$initPath = Join-Path $root "finfore\__init__.py"
$m = Select-String -Path $initPath -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $m) { throw "Could not read __version__ from $initPath" }
$version = $m.Matches[0].Groups[1].Value
Write-Host "Financing with Doug $version" -ForegroundColor Cyan

# --- tests -----------------------------------------------------------------
# A build that ships a failing test suite is a build nobody can trust to
# auto-update itself onto five other machines.
Write-Host "`n[1/4] Running the test suite..." -ForegroundColor Cyan
$env:PYTHONIOENCODING = "utf-8"
Invoke-Native $py @("tests\test_core.py") "The test suite"

# --- app -------------------------------------------------------------------
if ($SkipBuild) {
    Write-Host "`n[2/4] Skipping PyInstaller (-SkipBuild)." -ForegroundColor Yellow
} else {
    Write-Host "`n[2/4] Building the app with PyInstaller..." -ForegroundColor Cyan
    if (Test-Path "build") { Remove-Item "build" -Recurse -Force }
    if (Test-Path "dist\Financing with Doug") {
        Remove-Item "dist\Financing with Doug" -Recurse -Force
    }
    Invoke-Native $py @("-m", "PyInstaller", "--noconfirm",
                        "packaging\doug.spec") "PyInstaller"
}

$appExe = "dist\Financing with Doug\Financing with Doug.exe"
if (-not (Test-Path $appExe)) { throw "Expected $appExe - the build did not produce it." }

if ($SkipInstaller) {
    Write-Host "`nDone (app only): dist\Financing with Doug\" -ForegroundColor Green
    exit 0
}

# --- installer -------------------------------------------------------------
Write-Host "`n[3/4] Wrapping it in an installer..." -ForegroundColor Cyan
# Any installed version, newest first - the folder is named for the major
# version ("Inno Setup 6", "Inno Setup 7"), so pinning one in a path is a
# guarantee of breaking on the next release. Sorted numerically rather than by
# name, or "Inno Setup 10" would one day rank below "Inno Setup 7".
$iscc = $null
foreach ($r in @("$env:ProgramFiles", "${env:ProgramFiles(x86)}")) {
    if (-not $r -or -not (Test-Path $r)) { continue }
    $dirs = Get-ChildItem -Path $r -Filter "Inno Setup *" -Directory `
                -ErrorAction SilentlyContinue |
            Sort-Object { [int]($_.Name -replace '\D', '') } -Descending
    foreach ($d in $dirs) {
        $c = Join-Path $d.FullName "ISCC.exe"
        if (Test-Path $c) { $iscc = $c; break }
    }
    if ($iscc) { break }
}
if (-not $iscc) {
    $cmd = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) {
    throw "Inno Setup not found. Install it from https://jrsoftware.org/isdl.php"
}
Write-Host "  using $iscc" -ForegroundColor DarkGray

Invoke-Native $iscc @("/DAppVersion=$version", "packaging\installer.iss") `
    "Inno Setup"

# --- checksum --------------------------------------------------------------
Write-Host "`n[4/4] Writing SHA256SUMS.txt..." -ForegroundColor Cyan
$outDir = Join-Path $root "dist\installer"
$setup = Get-ChildItem $outDir -Filter "*-Setup.exe" |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $setup) { throw "No installer found in $outDir" }
$hash = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash.ToLower()
# Two spaces then the bare filename: the sha256sum format the updater parses.
"$hash  $($setup.Name)" | Out-File (Join-Path $outDir "SHA256SUMS.txt") `
    -Encoding ascii

$mb = [math]::Round($setup.Length / 1MB, 1)
Write-Host "`nReady to publish:" -ForegroundColor Green
Write-Host "  $($setup.FullName)  ($mb MB)"
Write-Host "  $(Join-Path $outDir 'SHA256SUMS.txt')"
Write-Host "`nNext:" -ForegroundColor Cyan
Write-Host "  git tag v$version; git push --tags"
Write-Host "  gh release create v$version `"$($setup.FullName)`" `"$(Join-Path $outDir 'SHA256SUMS.txt')`" --title `"v$version`" --notes-file CHANGELOG.md"
