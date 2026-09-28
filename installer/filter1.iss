; filter1 all-in-one installer (Inno Setup).
; Bundles a private Python venv (dnslib + mitmproxy), both agents, and the addon.
; Installs the DNS agent AND the proxy. No network downloads at install time.
; Server URL and agent token are injected at build time via ISCC /D flags.

#ifndef MyServer
  #define MyServer "https://bsd-filter1.fly.dev"
#endif
#ifndef MyToken
  #define MyToken "CHANGE-ME"
#endif

[Setup]
AppId={{B5D0F17A-11C2-4E8B-9E4A-F117E1000001}
AppName=filter1
AppVersion=1.1.6
AppPublisher=BSD
DefaultDirName={commonpf}\filter1
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputBaseFilename=filter1-setup
Compression=lzma2/max
SolidCompression=yes
UninstallDisplayName=filter1 parental control
WizardStyle=modern

[Files]
Source: "payload\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; Start Menu shortcut so the parent can double-click to see local status / why
; something isn't working (server registration, DNS, proxy, certificate).
Name: "{commonprograms}\filter1\מצב filter1 (אבחון)"; Filename: "{app}\filter1-status.bat"
Name: "{commonprograms}\filter1\הסרת filter1"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-tasks.ps1"" -InstallDir ""{app}"" -Server ""{#MyServer}"" -Token ""{#MyToken}"""; \
  StatusMsg: "Installing filter1 (DNS + proxy)..."; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall-all.ps1"""; \
  Flags: runhidden; RunOnceId: "filter1removeall"

[Code]
// Registry key Inno writes its uninstall info under (AppId + "_is1"), used to
// detect an existing install so the same setup file can also uninstall/upgrade.
function UninstallKey(): String;
begin
  Result := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{B5D0F17A-11C2-4E8B-9E4A-F117E1000001}_is1';
end;

// Run once at startup. If filter1 is already installed, offer to uninstall from
// this very file (Yes), reinstall/upgrade in place (No), or cancel. Choosing
// uninstall runs the existing uninstaller, which still enforces the parent code.
function InitializeSetup(): Boolean;
var
  UninstStr: String;
  ResultCode, Answer: Integer;
begin
  Result := True;
  if RegQueryStringValue(HKLM, UninstallKey(), 'UninstallString', UninstStr) then
  begin
    Answer := MsgBox('filter1 כבר מותקן.' + #13#10#13#10 +
      'Yes = הסרה' + #13#10 +
      'No = התקנה מחדש / עדכון לגרסה זו' + #13#10 +
      'Cancel = ביטול',
      mbConfirmation, MB_YESNOCANCEL);
    if Answer = IDYES then
    begin
      Exec(RemoveQuotes(UninstStr), '', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
      Result := False;   // stop: we uninstalled instead of installing
    end
    else if Answer = IDCANCEL then
      Result := False;
    // IDNO falls through: proceed with an in-place upgrade
  end;
end;

// Runs BEFORE any file is copied. Stop the running agent/proxy first, otherwise
// the in-use bundled python.exe / mitmdump.exe would cause sharing violations
// when upgrading over an existing install.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  App, Cmd: String;
begin
  App := ExpandConstant('{app}');
  Cmd := '-NoProfile -ExecutionPolicy Bypass -Command "' +
    'Stop-ScheduledTask -TaskName filter1 -ErrorAction SilentlyContinue; ' +
    'Stop-ScheduledTask -TaskName filter1-proxy -ErrorAction SilentlyContinue; ' +
    'Get-Process python,pythonw,mitmdump -ErrorAction SilentlyContinue | ' +
    'Where-Object { $_.Path -like ''' + App + '\*''' + ' } | ' +
    'Stop-Process -Force -ErrorAction SilentlyContinue' +
    '"';
  Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1000);   // let file handles release before the copy starts
  Result := '';
end;

// The code prompt + server verification is done by a bundled PowerShell script
// (verify-uninstall.ps1) which exits 0 only when the code is valid. Uninstall
// is aborted unless it returns 0.
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
  Params: String;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{app}\verify-uninstall.ps1') +
    '" -Server "{#MyServer}" -Token "{#MyToken}"';
  if not Exec('powershell.exe', Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    MsgBox('לא ניתן להריץ את בדיקת קוד ההסרה.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Result := (ResultCode = 0);
  if not Result then
    MsgBox('קוד הסרה שגוי או שרת לא זמין. ההסרה בוטלה.', mbError, MB_OK);
end;
