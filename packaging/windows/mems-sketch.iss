; The Windows installer (Inno Setup 6), from the PyInstaller folder:
;   iscc /DAppVersion=0.2.0 /DSourceDir=dist\mems-sketch /DOutputDir=out packaging\windows\mems-sketch.iss
; Installs for the current user (no administrator rights needed), with a
; Start menu entry and an uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\mems-sketch"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{6F3B7C2E-5D41-4A8B-9E0C-2B7D4F1A9C63}
AppName=MEMS Sketch
AppVersion={#AppVersion}
AppPublisher=mems-sketch
AppPublisherURL=https://github.com/TorosBenjamin/mems-sketch
DefaultDirName={autopf}\MEMS Sketch
DefaultGroupName=MEMS Sketch
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=MEMS_Sketch-{#AppVersion}-windows-setup
SetupIconFile=..\icons\mems-sketch.ico
UninstallDisplayIcon={app}\mems-sketch.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MEMS Sketch"; Filename: "{app}\mems-sketch.exe"
Name: "{group}\Uninstall MEMS Sketch"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MEMS Sketch"; Filename: "{app}\mems-sketch.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\mems-sketch.exe"; Description: "{cm:LaunchProgram,MEMS Sketch}"; Flags: nowait postinstall skipifsilent
