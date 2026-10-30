; Inno Setup script (optional). Installs the program to Program Files; user data lives in %LOCALAPPDATA%\AtlasLibrary and is never touched by install/uninstall.
#ifndef AppVersion
#define AppVersion "1.1.0"
#endif
[Setup]
AppName=Atlas Library
AppVersion={#AppVersion}
AppPublisher=Atlas Library
DefaultDirName={autopf}\Atlas Library
DefaultGroupName=Atlas Library
OutputDir=..\dist
OutputBaseFilename=AtlasLibrary-Setup-{#AppVersion}
SetupIconFile=..\assets\atlas.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\AtlasLibrary.exe
[Files]
Source: "..\dist\AtlasLibrary\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\Atlas Library"; Filename: "{app}\AtlasLibrary.exe"
Name: "{autodesktop}\Atlas Library"; Filename: "{app}\AtlasLibrary.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"
[Run]
Filename: "{app}\AtlasLibrary.exe"; Description: "Start Atlas Library"; Flags: nowait postinstall skipifsilent
