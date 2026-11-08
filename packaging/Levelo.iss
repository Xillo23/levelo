; Levelo - Inno Setup script. Built by build.py, which passes:
;   AppVersion, AppPublisher, SourceDir (PyInstaller folder), BuildDir, RootDir

#define AppName "Levelo"
#define AppExe "Levelo.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "Levelo contributors"
#endif

[Setup]
AppId={{0E751030-8D6B-4B68-B9D3-D2E0DF59A9A0}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=Levelo-{#AppVersion}-Setup
SetupIconFile={#RootDir}\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile={#BuildDir}\wizard-large-164.bmp,{#BuildDir}\wizard-large-246.bmp,{#BuildDir}\wizard-large-328.bmp
WizardSmallImageFile={#BuildDir}\wizard-small-55.bmp,{#BuildDir}\wizard-small-64.bmp,{#BuildDir}\wizard-small-83.bmp,{#BuildDir}\wizard-small-110.bmp,{#BuildDir}\wizard-small-138.bmp
Compression=lzma2/ultra64
SolidCompression=yes
ShowLanguageDialog=auto
CloseApplications=no
RestartApplications=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
en.AutoStart=Start automatically with Windows (recommended)
it.AutoStart=Avvia automaticamente con Windows (consigliato)
es.AutoStart=Iniciar automáticamente con Windows (recomendado)
de.AutoStart=Automatisch mit Windows starten (empfohlen)
fr.AutoStart=Lancer automatiquement avec Windows (recommandé)
pt.AutoStart=Iniciar automaticamente com o Windows (recomendado)
ru.AutoStart=Запускать вместе с Windows (рекомендуется)
pl.AutoStart=Uruchamiaj automatycznie z Windows (zalecane)
tr.AutoStart=Windows ile otomatik başlat (önerilen)
zh.AutoStart=开机时自动启动（推荐）
ja.AutoStart=Windows 起動時に自動的に開始する（推奨）

[Tasks]
Name: "autostart"; Description: "{cm:AutoStart}"; Check: IsFreshInstall
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RootDir}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[InstallDelete]
; clean leftovers of the previous version's runtime before copying the new one
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; autostart: chosen only on the first install. On updates the user's choice
; (made later in the app) is kept as it is.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Levelo"; ValueData: """{app}\{#AppExe}"" --background"; Tasks: autostart; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Levelo"; Flags: deletevalue; Check: IsFreshInstall and not WizardIsTaskSelected('autostart')
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Levelo"; Flags: uninsdeletevalue
; language chosen in the wizard -> used by the app on first start
Root: HKCU; Subkey: "Software\Levelo"; ValueType: string; ValueName: "Language"; ValueData: "{language}"; Flags: uninsdeletekey; Check: IsFreshInstall

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\Levelo"

[Code]
{ True on a first install, False when updating an existing installation:
  the settings in %APPDATA%\Levelo and the autostart choice are left alone. }
function IsFreshInstall(): Boolean;
begin
  Result := not RegKeyExists(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{0E751030-8D6B-4B68-B9D3-D2E0DF59A9A0}_is1');
end;

{ Ask a running Levelo to quit cleanly, so it restores every volume
  before files are replaced or removed. }
procedure QuitRunningApp(const Exe: String);
var
  RC: Integer;
begin
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRunningApp(ExpandConstant('{app}\{#AppExe}'));
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  QuitRunningApp(ExpandConstant('{app}\{#AppExe}'));
  Result := True;
end;
