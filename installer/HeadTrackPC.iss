; Inno Setup script: HeadTrackPC-<version>-Setup.exe from the PyInstaller folder dist\HeadTrackPC.
; Build:  ISCC.exe /DAppVersion=1.0.0 installer\HeadTrackPC.iss   (ISCC is preinstalled on GitHub's
; windows runners; locally install Inno Setup 6). Installs per user by default (no admin prompt)
; into %LOCALAPPDATA%\Programs\HeadTrack PC; the user may choose "for all users" instead.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "HeadTrack PC"
#define AppExe "HeadTrackPC.exe"
#define SourceDir "..\dist\HeadTrackPC"

[Setup]
AppId={{7E3C1F5A-9B2D-4C8E-A1F0-5D6B7C8E9F01}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=HeadTrack
AppPublisherURL=https://github.com/thanhkaiba/pc-beam-eyes-tracker
AppSupportURL=https://github.com/thanhkaiba/pc-beam-eyes-tracker/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=HeadTrackPC-{#AppVersion}-Setup
SetupIconFile=headtrack.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
