#ifndef AppName
  #define AppName "UsageLoop"
#endif
#ifndef AppVersion
  #define AppVersion "1.3.3"
#endif
#ifndef AppExeName
  #define AppExeName "UsageLoop.exe"
#endif
#ifndef AppPublisher
  #define AppPublisher "UsageLoop"
#endif
#ifndef AppId
  #define AppId "{{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}"
#endif
#ifndef AppIdGuid
  #define AppIdGuid "{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}"
#endif
#ifndef InstallerBaseName
  #define InstallerBaseName "UsageLoop-Setup"
#endif
#ifndef AppIconFile
  #define AppIconFile "usageloop.ico"
#endif
#ifndef DistFolder
  #define DistFolder "UsageLoop"
#endif
#ifndef LegacyInstallFolder
  #define LegacyInstallFolder "Window Sentinel"
#endif
[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
UsePreviousAppDir=no
DefaultGroupName={#AppName}
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#InstallerBaseName}
SetupIconFile={#AppIconFile}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName},0
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\dist\{#DistFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\UsageLoopStatus.exe"

[Icons]
Name: "{userprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; IconIndex: 0

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey

[Code]
procedure WriteRequiredString(const KeyName, ValueName, ValueData: String);
begin
  if not RegWriteStringValue(HKCU, KeyName, ValueName, ValueData) then
    RaiseException('UsageLoop could not repair its Windows app registration.');
end;

procedure RepairUsageLoopRegistration;
var
  UninstallKey: String;
  UninstallExe: String;
  InstallDir: String;
begin
  UninstallKey :=
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdGuid}_is1';
  UninstallExe := ExpandConstant('{uninstallexe}');
  InstallDir := AddBackslash(ExpandConstant('{app}'));

  { Every historical public build used this one AppId and registry view. }
  { Rewriting that exact record repairs beta entries that still reference }
  { the deleted Window Sentinel folder without scanning other products. }
  if not FileExists(UninstallExe) then
    RaiseException('UsageLoop could not create its canonical uninstaller.');

  WriteRequiredString(UninstallKey, 'DisplayName', '{#AppName}');
  WriteRequiredString(UninstallKey, 'DisplayVersion', '{#AppVersion}');
  WriteRequiredString(UninstallKey, 'Publisher', '{#AppPublisher}');
  WriteRequiredString(UninstallKey, 'InstallLocation', InstallDir);
  WriteRequiredString(
    UninstallKey,
    'DisplayIcon',
    ExpandConstant('{app}\{#AppExeName},0')
  );
  WriteRequiredString(
    UninstallKey,
    'UninstallString',
    '"' + UninstallExe + '"'
  );
  WriteRequiredString(
    UninstallKey,
    'QuietUninstallString',
    '"' + UninstallExe + '" /SILENT'
  );
  WriteRequiredString(UninstallKey, 'Inno Setup: App Path', ExpandConstant('{app}'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  StartupCommand: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  RepairUsageLoopRegistration;

  { Preserve the user's existing startup choice while repairing its executable. }
  if RegQueryStringValue(
    HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Run',
    '{#AppName}',
    StartupCommand
  ) then
    RegWriteStringValue(
      HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Run',
      '{#AppName}',
      '"' + ExpandConstant('{app}\{#AppExeName}') + '" --background'
    );

  { Delete legacy files only after the new install and uninstall metadata exist. }
  DelTree(
    ExpandConstant('{localappdata}\Programs\{#LegacyInstallFolder}'),
    True,
    True,
    True
  );
  DelTree(
    ExpandConstant('{userprograms}\{#LegacyInstallFolder}'),
    True,
    True,
    True
  );
end;
