param(
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$Server,
  [Parameter(Mandatory=$true)][string]$Token
)
# Post-install configuration for the all-in-one filter1 installer. Everything it
# needs (Python, dnslib, mitmproxy) is already bundled under $InstallDir\py, so
# this makes NO network downloads. Installs both the DNS agent and the proxy.

$ErrorActionPreference = "Continue"
$py  = Join-Path $InstallDir "py\python.exe"
$pyw = Join-Path $InstallDir "py\pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
$agent = Join-Path $InstallDir "agent.py"
$runProxy = Join-Path $InstallDir "run_proxy.py"
$Conf = Join-Path $env:ProgramData "filter1\mitmproxy"

function NK($p) { if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null } }

# ---- config file (shared by both layers) ----
$cfg = Join-Path $InstallDir "filter1.cfg"
[System.IO.File]::WriteAllText($cfg, "server=$Server`ntoken=$Token")

# ---- ensure the Visual C++ runtime the bundled Python needs (clean machines
#      often lack it, which would stop python.exe from starting at all) ----
$vc = Join-Path $InstallDir "vc_redist.x64.exe"
if (Test-Path $vc) {
  Start-Process $vc -ArgumentList "/quiet","/norestart" -Wait -ErrorAction SilentlyContinue
}

# ---- self-test: prove the bundled Python runs before we rely on it ----
New-Item -ItemType Directory -Force -Path (Join-Path $env:ProgramData "filter1") | Out-Null
$selftest = Join-Path $env:ProgramData "filter1\selftest.log"
"=== python + dnslib ===" | Out-File $selftest
& $py -c "import sys, dnslib; print('py', sys.version); print('dnslib ok')" *>> $selftest 2>&1
"exit=$LASTEXITCODE" | Out-File $selftest -Append
"=== mitmproxy ===" | Out-File $selftest -Append
& $py -c "import mitmproxy; print('mitmproxy import ok')" *>> $selftest 2>&1
"=== agent --once ===" | Out-File $selftest -Append
& $py "$agent" --config "$cfg" --once *>> $selftest 2>&1
# only enable the proxy layer if the agent could actually reach the server;
# otherwise routing the browser through a dead proxy would cut it off
$agentOk = ($LASTEXITCODE -eq 0)
"agentOk=$agentOk" | Out-File $selftest -Append

# ============================ DNS AGENT ============================
Stop-ScheduledTask -TaskName "filter1" -ErrorAction SilentlyContinue | Out-Null
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*filter1\agent.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$a1 = New-ScheduledTaskAction -Execute $pyw -Argument "`"$agent`" --config `"$cfg`""
$t1 = New-ScheduledTaskTrigger -AtStartup
$pr = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$st = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "filter1" -Action $a1 -Trigger $t1 -Principal $pr -Settings $st -Force | Out-Null
Start-ScheduledTask -TaskName "filter1"

# ============================ BROWSER HARDENING ============================
# disable DNS-over-HTTPS so browsers can't bypass the filter
New-Item -Path "HKLM:\SOFTWARE\Policies\Google\Chrome" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Google\Chrome" -Name DnsOverHttpsMode -Value "off"
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Name DnsOverHttpsMode -Value "off"
NK "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS"
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Name Enabled -Value 0 -Type DWord
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Name Locked -Value 1 -Type DWord

# block common VPN tunnel protocols
Remove-NetFirewallRule -Group "filter1" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "filter1 block WireGuard" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 51820 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block OpenVPN" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 1194 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block IKEv2" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 500,4500 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block PPTP" -Group "filter1" -Direction Outbound -Action Block -Protocol TCP -RemotePort 1723 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block L2TP" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 1701 -ErrorAction SilentlyContinue | Out-Null

# block known VPN clients from running
$vpnApps = @("ProtonVPN.exe","ProtonVPNService.exe","nordvpn.exe","NordVPN.exe",
  "expressvpn.exe","ExpressVPN.exe","openvpn.exe","openvpn-gui.exe","wireguard.exe",
  "tunnelbear.exe","Windscribe.exe","windscribe.exe","hola.exe","psiphon3.exe",
  "hss.exe","HotspotShield.exe","surfshark.exe")
$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
foreach ($ap in $vpnApps) {
  New-Item -Path "$ifeo\$ap" -Force | Out-Null
  Set-ItemProperty -Path "$ifeo\$ap" -Name "Debugger" -Value "$env:SystemRoot\System32\cmd.exe /c exit"
}

# ============================ PROXY (mitmproxy) ============================
# Only enable the proxy layer if the agent proved it can reach the server. If
# not, we must NOT route the browser through a proxy, or it would be cut off.
if ($agentOk) {
  # generate the mitmproxy root CA by running mitmdump briefly on a temp port
  New-Item -ItemType Directory -Force -Path $Conf | Out-Null
  $genArgs = "-c `"import sys;from mitmproxy.tools.main import mitmdump;sys.argv=['mitmdump','--set','confdir=$Conf','--listen-port','8099','-q'];mitmdump()`""
  $p = Start-Process $py -ArgumentList $genArgs -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 8
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  $ca = Join-Path $Conf "mitmproxy-ca-cert.cer"
  if (-not (Test-Path $ca)) { $ca = Join-Path $Conf "mitmproxy-ca-cert.pem" }
  if (Test-Path $ca) {
    Import-Certificate -FilePath $ca -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    NK "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates"
    Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates" -Name ImportEnterpriseRoots -Value 1 -Type DWord
  }

  # force browsers through the proxy via BROWSER POLICY only (NOT the system-wide
  # WinINET proxy, which makes Windows block app launches during its zone checks)
  New-NetFirewallRule -DisplayName "filter1 block QUIC" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 443 -ErrorAction SilentlyContinue | Out-Null
  foreach ($b in @("HKLM:\SOFTWARE\Policies\Google\Chrome","HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
    New-Item -Path $b -Force | Out-Null
    Set-ItemProperty -Path $b -Name QuicAllowed -Value 0 -Type DWord
    Set-ItemProperty -Path $b -Name ProxyMode -Value "fixed_servers"
    Set-ItemProperty -Path $b -Name ProxyServer -Value "127.0.0.1:8080"
  }

  # run the proxy as a SYSTEM task
  Stop-ScheduledTask -TaskName "filter1-proxy" -ErrorAction SilentlyContinue | Out-Null
  Get-Process mitmdump -ErrorAction SilentlyContinue | Stop-Process -Force
  $a2 = New-ScheduledTaskAction -Execute $pyw -Argument "`"$runProxy`""
  $a2t = New-ScheduledTaskTrigger -AtStartup
  Register-ScheduledTask -TaskName "filter1-proxy" -Action $a2 -Trigger $a2t -Principal $pr -Settings $st -Force | Out-Null
  Start-ScheduledTask -TaskName "filter1-proxy"
  Write-Host "filter1 installed: DNS agent + proxy running."
} else {
  "PROXY SKIPPED: agent could not reach the server (see errors above)." | Out-File $selftest -Append
  Write-Host "filter1 installed: DNS agent only (proxy skipped - server unreachable)."
}
