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
AppVersion=1.1.3
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

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup-tasks.ps1"" -InstallDir ""{app}"" -Server ""{#MyServer}"" -Token ""{#MyToken}"""; \
  StatusMsg: "Installing filter1 (DNS + proxy)..."; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall-all.ps1"""; \
  Flags: runhidden; RunOnceId: "filter1removeall"

[Code]
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
