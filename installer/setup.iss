; setup.iss — SPARSH ONT Run Manager
; Full Inno Setup 6 installer script
;
; Compile with:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
;
; Or run build.py on Windows — it calls ISCC automatically.
; The {#...} directives are resolved by the Inno preprocessor.

#define AppName      "SPARSH Run Manager"
#define AppVersion   "1.0.0"
#define AppPublisher "SPARSH Team"
#define AppURL       "https://github.com/your-org/sparsh"
#define AppExeName   "SPARSH Run Manager.exe"
#ifndef BundleDir
  #define BundleDir "dist\SPARSH Run Manager"
#endif

; ── [Setup] ────────────────────────────────────────────────────────────────────

[Setup]
AppId                    = {{A3F2C1B4-9E7D-4F2A-8C3E-1D5B6A9F0E2C}
AppName                  = {#AppName}
AppVersion               = {#AppVersion}
AppVerName               = {#AppName} {#AppVersion}
AppPublisher             = {#AppPublisher}
AppPublisherURL          = {#AppURL}
AppSupportURL            = {#AppURL}/issues
AppUpdatesURL            = {#AppURL}/releases

; Install for current user only — no admin rights needed
PrivilegesRequired       = lowest
PrivilegesRequiredOverridesAllowed = dialog

DefaultDirName           = {localappdata}\{#AppName}
DefaultGroupName         = {#AppName}
DisableProgramGroupPage  = yes

; Output
OutputDir                = .
OutputBaseFilename       = SPARSH-RunManager-Setup-{#AppVersion}
SetupIconFile            = ..\assets\icon.ico

; Compression
Compression              = lzma2/ultra64
SolidCompression         = yes
LZMAUseSeparateProcess   = yes

; UI
WizardStyle              = modern
WizardSizePercent        = 120
WizardResizable          = no
ShowLanguageDialog       = no

; Uninstall
UninstallDisplayName     = {#AppName}
UninstallDisplayIcon     = {app}\{#AppExeName}
CreateUninstallRegKey    = yes

; Minimum Windows version: Windows 10
MinVersion               = 10.0

; Architecture
ArchitecturesAllowed             = x64compatible
ArchitecturesInstallIn64BitMode  = x64compatible

; Versioning info shown in Add/Remove Programs
VersionInfoVersion       = {#AppVersion}
VersionInfoCompany       = {#AppPublisher}
VersionInfoDescription   = {#AppName} Installer
VersionInfoCopyright     = Copyright (C) 2026 {#AppPublisher}

; ── [Languages] ────────────────────────────────────────────────────────────────

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ── [Messages] — custom wizard text ───────────────────────────────────────────

[Messages]
WelcomeLabel1=Welcome to {#AppName} Setup
WelcomeLabel2=This will install {#AppName} {#AppVersion} on your computer.%n%nSPARSH Run Manager monitors Oxford Nanopore sequencing runs in real time and automatically triggers SPARSH methylation analysis when new BAM files are ready.%n%nIt is recommended that you close all other applications before continuing.
FinishedHeadingLabel=Installation complete
FinishedLabel={#AppName} has been installed on your computer.%n%nClick Finish to close Setup.

; ── [Tasks] — optional install options ────────────────────────────────────────

[Tasks]
Name: "desktopicon";    Description: "Create a &desktop shortcut";          GroupDescription: "Shortcuts:"; Flags: checked
Name: "startupicon";   Description: "Launch automatically at &Windows startup"; GroupDescription: "Startup:";   Flags: unchecked

; ── [Files] ────────────────────────────────────────────────────────────────────

[Files]
; Main application bundle (produced by PyInstaller)
Source: "{#BundleDir}\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "*.pdb"

; ── [Icons] — Start Menu + Desktop ────────────────────────────────────────────

[Icons]
; Start Menu shortcut (under user's personal Programs folder)
Name: "{userprograms}\{#AppName}"; \
  Filename: "{app}\{#AppExeName}"; \
  Comment: "Real-time ONT run monitoring and SPARSH analysis"; \
  IconFilename: "{app}\{#AppExeName}"

; Desktop shortcut (optional)
Name: "{userdesktop}\{#AppName}"; \
  Filename: "{app}\{#AppExeName}"; \
  Comment: "Real-time ONT run monitoring and SPARSH analysis"; \
  IconFilename: "{app}\{#AppExeName}"; \
  Tasks: desktopicon

; Startup shortcut (optional, current user only)
Name: "{userstartup}\{#AppName}"; \
  Filename: "{app}\{#AppExeName}"; \
  Tasks: startupicon

; ── [Registry] ─────────────────────────────────────────────────────────────────

[Registry]
; Register in Add/Remove Programs with extra metadata
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppName}"; \
  ValueType: string; ValueName: "DisplayName";          ValueData: "{#AppName}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppName}"; \
  ValueType: string; ValueName: "DisplayVersion";       ValueData: "{#AppVersion}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppName}"; \
  ValueType: string; ValueName: "Publisher";            ValueData: "{#AppPublisher}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppName}"; \
  ValueType: string; ValueName: "URLInfoAbout";         ValueData: "{#AppURL}"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppName}"; \
  ValueType: string; ValueName: "EstimatedSize";        ValueData: "180000"

; ── [Run] — post-install ───────────────────────────────────────────────────────

[Run]
; Offer to launch the app when the installer finishes
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent unchecked

; ── [UninstallDelete] — clean up user data on uninstall ───────────────────────

[UninstallDelete]
; Remove the config file written by the app
Type: files; Name: "{localappdata}\{#AppName}\sparsh_config.json"
Type: dirifempty; Name: "{localappdata}\{#AppName}"

; ── [Code] — Pascal script for custom installer logic ─────────────────────────

[Code]

{ Check if the app is currently running before install/uninstall }
function IsAppRunning(const ExeName: string): Boolean;
var
  WbemLocator, WbemServices, WbemObjectSet: Variant;
  Count: Integer;
begin
  Result := False;
  try
    WbemLocator  := CreateOleObject('WbemScripting.SWbemLocator');
    WbemServices := WbemLocator.ConnectServer('', 'root\CIMV2', '', '');
    WbemObjectSet := WbemServices.ExecQuery(
      'SELECT * FROM Win32_Process WHERE Name = ''' + ExeName + '''');
    Count := WbemObjectSet.Count;
    Result := Count > 0;
  except
    Result := False;
  end;
end;

function InitializeSetup(): Boolean;
begin
  if IsAppRunning('SPARSH Run Manager.exe') then
  begin
    MsgBox(
      'SPARSH Run Manager is currently running.' + #13#10 +
      'Please close it before continuing.',
      mbError, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  if IsAppRunning('SPARSH Run Manager.exe') then
  begin
    MsgBox(
      'SPARSH Run Manager is currently running.' + #13#10 +
      'Please close it before uninstalling.',
      mbError, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;
