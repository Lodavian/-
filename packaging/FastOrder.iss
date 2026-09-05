#define MyAppName "FastOrder"
#define MyAppDisplayName "极速下单"
#define MyAppVersion "1.0.0"
#define MyAppExeName "FastOrder.exe"

; CI must inject the legal publisher, e.g. /DMyAppPublisher="Example Ltd.".
#ifndef MyAppPublisher
  #error MyAppPublisher must be supplied by the release build
#endif

#ifndef SignToolName
  #error SignToolName must be supplied by the signed release build
#endif

; Override with /DStagingDir=... when compiling from CI. The staging tree must
; contain the already-built one-folder application and no runtime data.
#ifndef StagingDir
  #define StagingDir "..\dist\FastOrder"
#endif

[Setup]
AppId={{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\FastOrder
DefaultGroupName={#MyAppDisplayName}
OutputBaseFilename=FastOrder-Setup-1.0.0
MinVersion=10.0.19045
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
ChangesAssociations=no
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#StagingDir}\FastOrder.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
; The application must create this named mutex before serving requests. This
; makes upgrades fail safely while FastOrder is still running.
AppMutex=Local\FastOrder.V1
; CI injects this named command with ISCC /S; it signs setup and uninstaller.
SignTool={#SignToolName}
SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#StagingDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{commondesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppDisplayName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

; Deliberately no [UninstallDelete] entries: LOCALAPPDATA data and Windows
; Credential Manager entries are user data and must survive uninstall.

[Code]
function ParseSemanticVersion(const Value: String; var Packed: Int64): Boolean;
var
  Parts: TArrayOfString;
  Major, Minor, Patch: Integer;
begin
  Parts := SplitString(Value, '.');
  Result := GetArrayLength(Parts) >= 3;
  if not Result then Exit;
  Major := StrToIntDef(Parts[0], -1);
  Minor := StrToIntDef(Parts[1], -1);
  Patch := StrToIntDef(Parts[2], -1);
  Result := (Major >= 0) and (Minor >= 0) and (Patch >= 0);
  if Result then Packed := PackVersionComponents(Major, Minor, Patch, 0);
end;

function InitializeSetup(): Boolean;
var
  InstalledVersion: String;
  InstalledPacked, CandidatePacked: Int64;
begin
  Result := True;
  if RegQueryStringValue(HKLM64,
       'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5E1CC7C3-2A6F-4E71-9C2A-6F1BBE4D6D0A}_is1',
       'DisplayVersion', InstalledVersion) and
     ParseSemanticVersion(InstalledVersion, InstalledPacked) and
     ParseSemanticVersion('{#MyAppVersion}', CandidatePacked) and
     (InstalledPacked > CandidatePacked) then
  begin
    MsgBox('检测到更高版本，禁止降级安装。', mbError, MB_OK);
    Result := False;
  end;
end;
