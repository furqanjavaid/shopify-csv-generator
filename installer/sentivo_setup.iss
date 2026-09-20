#define MyAppName "Sentivo Tools"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Sentivo Limited"
#define MyAppURL "https://sentivo.co"
#define MyAppExeName "SentivoTools.exe"

[Setup]
AppId={{8F3A2B1C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Script lives in installer\ — SourceDir is project root; OutputDir is next to this script
SourceDir=..
OutputDir=installer\output
OutputBaseFilename=SentivoToolsSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
CloseApplicationsFilter=*SentivoTools.exe
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\SentivoTools.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "version.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer\python-3.11.9-amd64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\python-3.11.9-amd64.exe"; Parameters: "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"; StatusMsg: "Installing Python 3.11..."; Check: not PythonInstalled; Flags: runhidden waituntilterminated

Filename: "cmd.exe"; Parameters: "/c pip install playwright requests python-docx pandas openpyxl Pillow customtkinter beautifulsoup4 lxml colorama matplotlib"; StatusMsg: "Installing Sentivo Tools dependencies..."; Flags: runhidden waituntilterminated

Filename: "cmd.exe"; Parameters: "/c playwright install chromium"; StatusMsg: "Setting up browser engine for Store Auditor..."; Flags: runhidden waituntilterminated

Filename: "{app}\{#MyAppExeName}"; Description: "Launch Sentivo Tools"; Flags: nowait postinstall skipifsilent

[Code]
function PythonInstalled: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec('python', '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure InitializeWizard;
begin
  WizardForm.Caption := 'Sentivo Tools Setup — by Sentivo Limited';
end;
