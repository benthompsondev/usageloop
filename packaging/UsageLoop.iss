#ifndef AppName
  #define AppName "UsageLoop"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.2"
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
procedure CurStepChanged(CurStep: TSetupStep);
var
  StartupCommand: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

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
