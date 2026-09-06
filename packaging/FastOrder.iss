#define MyAppName "FastOrder"
#define MyAppDisplayName "极速下单"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppExeName "FastOrder.exe"

; The installer has two deliberately separate release lanes.  The build
; script supplies exactly one of these defines; keeping the decision here
; means the generated RC can never accidentally inherit release signing
; settings.
#ifdef InternalUnsignedRc
  #ifdef FormalSigned
    #error InternalUnsignedRc and FormalSigned are mutually exclusive
  #endif
  #define MyAppPublisher "FastOrder Internal Candidate"
  #ifndef MyOutputBaseFilename
    #define MyOutputBaseFilename "FastOrder-Setup-1.0.0-rc1"
  #endif
#else
  #ifndef FormalSigned
    #error Exactly one installer release mode must be selected
  #endif
  #ifndef MyAppPublisher
    #error MyAppPublisher must be supplied by the formal release build
  #endif
  #ifndef SignToolName
    #error SignToolName must be supplied by the formal signed release build
  #endif
  #ifndef MyOutputBaseFilename
    #define MyOutputBaseFilename "FastOrder-Setup-1.0.0"
  #endif
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
; Formal value is OutputBaseFilename=FastOrder-Setup-1.0.0; the internal RC
; value appends -rc1 so the two artifacts cannot be confused.
OutputBaseFilename={#MyOutputBaseFilename}
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
; FormalSigned injects this named command with ISCC /S; it signs setup and
; uninstaller.  InternalUnsignedRc intentionally has no SignTool directive.
#ifdef FormalSigned
SignTool={#SignToolName}
SignedUninstaller=yes
#else
SignedUninstaller=no
#endif

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
  BaseValue: String;
  MajorText, MinorText, PatchText: String;
  SuffixAt, DotAt: Integer;
  Major, Minor, Patch: Integer;
begin
  { Accept prerelease upgrade inputs such as 0.9.9-qa while comparing the
    numeric semantic version components. }
  BaseValue := Value;
  SuffixAt := Pos('-', BaseValue);
  if SuffixAt > 0 then Delete(BaseValue, SuffixAt, Length(BaseValue));

  DotAt := Pos('.', BaseValue);
  if DotAt <= 1 then begin Result := False; Exit; end;
  MajorText := Copy(BaseValue, 1, DotAt - 1);
  Delete(BaseValue, 1, DotAt);

  DotAt := Pos('.', BaseValue);
  if DotAt <= 1 then begin Result := False; Exit; end;
  MinorText := Copy(BaseValue, 1, DotAt - 1);
  PatchText := Copy(BaseValue, DotAt + 1, Length(BaseValue));
  if (PatchText = '') or (Pos('.', PatchText) > 0) then begin Result := False; Exit; end;

  Major := StrToIntDef(MajorText, -1);
  Minor := StrToIntDef(MinorText, -1);
  Patch := StrToIntDef(PatchText, -1);
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
