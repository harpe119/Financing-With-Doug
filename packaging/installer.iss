; Inno Setup script for Financing with Doug.
;
;   1. Build the app first:   pyinstaller packaging/doug.spec
;   2. Then this:             iscc packaging/installer.iss
;      (or just run packaging/build.ps1, which does both)
;
; Output: dist/installer/FinancingWithDoug-<version>-Setup.exe — one file to
; upload to a GitHub release, and one file for family to double-click.
;
; Inno Setup is free (https://jrsoftware.org/isinfo.php) and is what most
; Windows desktop software has been installed by for twenty years, so the
; wizard looks like every other wizard. That familiarity is the point.
;
; Three decisions worth knowing about:
;
; * It installs **per-user**, into %LOCALAPPDATA%. That means no UAC prompt and
;   no administrator password — which for a program handed to family is the
;   difference between "double-click it" and "ring me". The cost is that it
;   installs for whoever runs it rather than for everyone on the machine,
;   which for a personal finance app is correct anyway.
; * `CloseApplications` lets the installer wait for a running copy to exit
;   rather than failing on a locked file. The in-app updater quits before
;   launching this, but somebody who downloads the installer by hand while the
;   app is open should not get a cryptic error.
; * The uninstaller deliberately does **not** delete the ledger in
;   %USERPROFILE%\.ledgerline. Uninstalling a program is not the same as asking
;   it to destroy your financial records, and if it were, this is not where you
;   would want to be asked.

#define AppName        "Financing with Doug"
#define AppShortName   "FinancingWithDoug"
#define Publisher      "Caleb Harper"
#define ExeName        "Financing with Doug.exe"
#define SourceDir      "..\dist\Financing with Doug"

; Version comes from finfore/__init__.py via build.ps1 (-DAppVersion=...).
; The fallback keeps a bare `iscc installer.iss` working.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3C21D6-4A57-4F1E-9C0B-6D2E7A9B4E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#AppVersion}

; Per-user: no administrator prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppShortName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

OutputDir=..\dist\installer
OutputBaseFilename={#AppShortName}-{#AppVersion}-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

; Wait for a running copy to close instead of failing on a locked file.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#ExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
; Launch after installing. `nowait postinstall skipifsilent` is what makes the
; in-app updater feel seamless: the app quits, the installer runs, the app
; comes back.
Filename: "{app}\{#ExeName}"; Description: "Start {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only what we installed. The ledger in %USERPROFILE%\.ledgerline stays.
Type: filesandordirs; Name: "{app}"

[Messages]
; Say plainly that uninstalling leaves the data alone — the question everybody
; has at this point, answered before it is asked.
ConfirmUninstall=Remove %1?%n%nYour accounts, transactions and settings are stored separately and will NOT be deleted — reinstalling picks up exactly where you left off.
