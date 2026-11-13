; KINO installer for Windows (Inno Setup 6.1 or newer).
; Installs for the current user only: no administrator rights, and KINO can
; later update itself in place. User data (~\.kino_data.db, the settings and
; %APPDATA%\KINO) is never touched, neither on update nor on uninstall.
;
; Build: iscc /DAppVersion=1.2.0 installer\kino.iss   (after pyinstaller kino_windows.spec)

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=x.y.z installer\kino.iss
#endif

[Setup]
; Never change AppId: Windows uses it to recognise an installed KINO.
AppId={{FA7C78FD-BE8D-4DB7-99D2-C935E2869F55}
AppName=KINO
AppVersion={#AppVersion}
AppVerName=KINO {#AppVersion}
AppPublisher=KINO
AppPublisherURL=https://github.com/isleepverywellatnight/kino
AppSupportURL=https://github.com/isleepverywellatnight/kino/issues
AppUpdatesURL=https://github.com/isleepverywellatnight/kino/releases
DefaultDirName={localappdata}\Programs\KINO
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1809 or newer (WebView2).
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=KINO-Setup-{#AppVersion}-x64
SetupIconFile=..\kino.ico
UninstallDisplayIcon={app}\KINO.exe
UninstallDisplayName=KINO
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; KINO must be closed first: see InitializeSetup below.
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; The previous version's libraries: an update must not keep stale modules.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\KINO\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\KINO"; Filename: "{app}\KINO.exe"
Name: "{autodesktop}\KINO"; Filename: "{app}\KINO.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KINO.exe"; Description: "{cm:LaunchProgram,KINO}"; Flags: nowait postinstall skipifsilent
; An update started by KINO itself (/UPDATE=1) relaunches it at the end.
Filename: "{app}\KINO.exe"; Flags: nowait; Check: IsKinoUpdate

[Code]
const
  KinoMutex = 'Local\KINO_SINGLE_INSTANCE_MUTEX';
  WebView2Id = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Bootstrapper = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

function IsKinoUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') <> '0';
end;

function WebView2VersionIn(RootKey: Integer; SubKey: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, SubKey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Installed: Boolean;
begin
  Result := WebView2VersionIn(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2Id)
    or WebView2VersionIn(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Id)
    or WebView2VersionIn(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Id);
end;

procedure InstallWebView2;
var
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := 'Installation de Microsoft Edge WebView2…';
  try
    DownloadTemporaryFile(WebView2Bootstrapper, 'MicrosoftEdgeWebview2Setup.exe', '', nil);
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '',
                SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      RaiseException('code ' + IntToStr(ResultCode));
  except
    SuppressibleMsgBox('KINO a besoin de Microsoft Edge WebView2, qui n''a pas pu être installé automatiquement.'
      + #13#10 + 'Installez-le depuis https://developer.microsoft.com/microsoft-edge/webview2/ puis relancez KINO.',
      mbInformation, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WebView2Installed then
    InstallWebView2;
end;

// KINO holds KinoMutex while it runs. An update started by KINO (/UPDATE) waits
// up to 60 s for it to quit; a manual install asks the user to close it.
function InitializeSetup: Boolean;
var
  Waited: Integer;
begin
  Result := True;
  if IsKinoUpdate then
  begin
    Waited := 0;
    while CheckForMutexes(KinoMutex) and (Waited < 120) do
    begin
      Sleep(500);
      Waited := Waited + 1;
    end;
  end;
  while CheckForMutexes(KinoMutex) do
  begin
    if SuppressibleMsgBox('KINO est ouvert. Fermez-le, puis cliquez sur OK pour continuer.',
                          mbError, MB_OKCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;
