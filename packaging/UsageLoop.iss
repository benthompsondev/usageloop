#ifndef AppName
  #define AppName "UsageLoop"
#endif
#ifndef AppVersion
  #define AppVersion "0.8.0"
#endif
#ifndef AppExeName
  #define AppExeName "UsageLoop.exe"
#endif
#ifndef AppPublisher
  #define AppPublisher "Ben Thompson"
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
[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#InstallerBaseName}
SetupIconFile={#AppIconFile}
UninstallDisplayIcon={app}\{#AppExeName}
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
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey
