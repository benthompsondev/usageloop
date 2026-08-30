#ifndef AppName
  #define AppName "Window Sentinel"
#endif
#ifndef AppVersion
  #define AppVersion "0.6.0"
#endif
#ifndef AppExeName
  #define AppExeName "WindowSentinel.exe"
#endif
#ifndef AppPublisher
  #define AppPublisher "Ben Thompson"
#endif
#ifndef AppId
  #define AppId "{{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}"
#endif
#ifndef InstallerBaseName
  #define InstallerBaseName "WindowSentinel-Setup"
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
SetupIconFile=windowsentinel.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\dist\WindowSentinel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\WindowSentinelStatus.exe"; Parameters: "--unregister"; RunOnceId: "RemoveSentinelClaudeStatusLine"; Flags: runhidden

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey
