#define AppName "Window Sentinel"
#define AppVersion "0.4.0"
#define AppExeName "WindowSentinel.exe"

[Setup]
AppId={{907EA79E-18FD-4A38-BBD0-35FF22D0BD82}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Ben Thompson
DefaultDirName={localappdata}\Programs\Window Sentinel
DefaultGroupName=Window Sentinel
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=WindowSentinel-Setup
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
Name: "{group}\Window Sentinel"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open Window Sentinel"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Window Sentinel"; Flags: uninsdeletevalue dontcreatekey
