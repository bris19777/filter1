# filter1 Windows client installer.
# Run elevated (install.bat does this for you). Installs the agent, registers it
# as a SYSTEM scheduled task that starts at boot and restarts on failure, and
# points the machine's DNS at the local filter.
#
# Transparent parental-control software: it installs under its real name and is
# managed by the machine administrator (the parent).

# ============================ EDIT THESE ============================
$Server = "https://bsd-filter1.fly.dev"   # the public URL of your control server
$Token  = "PASTE-AGENT-TOKEN-HERE"        # must match AGENT_TOKEN on the server
# ===================================================================

$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:ProgramFiles "filter1"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-PythonW {
    $c = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\pythonw.exe",
        "C:\Python312\pythonw.exe", "C:\Python311\pythonw.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

Write-Host "== filter1 client installer =="

# 1. Ensure Python is available (install via winget if missing).
$pyw = Find-PythonW
if (-not $pyw) {
    Write-Host "Python not found. Installing via winget..."
    winget install -e --id Python.Python.3.12 --scope machine `
        --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine")
    $pyw = Find-PythonW
}
if (-not $pyw) {
    Write-Error "Could not find or install Python. Install Python 3 manually, then re-run."
    exit 1
}
$py = $pyw -replace "pythonw.exe$","python.exe"
Write-Host "Using Python: $py"

# 2. Install the DNS library.
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install dnslib | Out-Null

# 3. Copy the agent and write its config.
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item (Join-Path (Split-Path $ScriptDir -Parent) "agent.py") $InstallDir -Force
$cfg = Join-Path $InstallDir "filter1.cfg"
[System.IO.File]::WriteAllText($cfg, "server=$Server`ntoken=$Token")
Write-Host "Installed to $InstallDir"

# 4. Register a scheduled task: at boot, as SYSTEM, restart on failure.
$agent = Join-Path $InstallDir "agent.py"
$action = New-ScheduledTaskAction -Execute $pyw `
    -Argument "`"$agent`" --config `"$cfg`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "filter1" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "filter1"
Write-Host "Scheduled task 'filter1' registered and started."

# Disable browser DNS-over-HTTPS so browsers cannot bypass the filter.
New-Item -Path "HKLM:\SOFTWARE\Policies\Google\Chrome" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Google\Chrome" -Name DnsOverHttpsMode -Value "off"
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Name DnsOverHttpsMode -Value "off"
New-Item -Path "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Name Enabled -Value 0 -Type DWord
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Name Locked -Value 1 -Type DWord
Write-Host "Browser DNS-over-HTTPS disabled."

# Firewall: block common VPN tunnel protocols so a VPN cannot bypass the filter.
Remove-NetFirewallRule -Group "filter1" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "filter1 block WireGuard" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 51820 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block OpenVPN" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 1194 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block IKEv2" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 500,4500 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block PPTP" -Group "filter1" -Direction Outbound -Action Block -Protocol TCP -RemotePort 1723 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block L2TP" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 1701 -ErrorAction SilentlyContinue | Out-Null
Write-Host "VPN protocol ports blocked in the firewall."

# Block known VPN clients from running at all (Image File Execution Options).
$vpnApps = @("ProtonVPN.exe","ProtonVPNService.exe","ProtonVPN.WireGuardService.exe",
  "nordvpn.exe","NordVPN.exe","expressvpn.exe","ExpressVPN.exe","openvpn.exe",
  "openvpn-gui.exe","wireguard.exe","wg.exe","tunnelbear.exe","Windscribe.exe",
  "windscribe.exe","hola.exe","psiphon3.exe","hss.exe","HotspotShield.exe","surfshark.exe")
$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
foreach ($a in $vpnApps) {
  New-Item -Path "$ifeo\$a" -Force | Out-Null
  Set-ItemProperty -Path "$ifeo\$a" -Name "Debugger" -Value "$env:SystemRoot\System32\cmd.exe /c exit"
}
Write-Host "Known VPN apps blocked from running."
Write-Host "Done. The machine will appear in your control panel within a minute."
