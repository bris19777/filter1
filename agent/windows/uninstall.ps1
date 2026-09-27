# filter1 Windows client uninstaller.
# Requires the one-time uninstall code generated in the control panel.

$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:ProgramFiles "filter1"
$cfg = Join-Path $InstallDir "filter1.cfg"

if (-not (Test-Path $cfg)) {
    Write-Error "filter1 does not appear to be installed."
    exit 1
}

# read server + token from the config
$server = ""; $token = ""
foreach ($line in Get-Content $cfg) {
    if ($line -match "^\s*server\s*=\s*(.+)$") { $server = $Matches[1].Trim() }
    if ($line -match "^\s*token\s*=\s*(.+)$")  { $token  = $Matches[1].Trim() }
}

$code = Read-Host "Enter the uninstall code from the control panel"
try {
    $u = "$server/api/verify-uninstall?token=$token&code=$([uri]::EscapeDataString($code))"
    $resp = Invoke-RestMethod -Uri $u -TimeoutSec 15
} catch {
    Write-Error "Could not reach the control server to verify the code."
    exit 1
}
if (-not $resp.ok) {
    Write-Error "Wrong uninstall code. Aborting."
    exit 1
}

Write-Host "Code verified. Removing filter1..."

# stop and remove the task
schtasks /End /TN filter1 2>$null | Out-Null
schtasks /Delete /TN filter1 /F 2>$null | Out-Null

# restore DNS to automatic on all interfaces
Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | ForEach-Object {
    try { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses } catch {}
}

# re-enable browser DNS-over-HTTPS (undo the install-time policy)
Remove-ItemProperty "HKLM:\SOFTWARE\Policies\Google\Chrome" -Name DnsOverHttpsMode -ErrorAction SilentlyContinue
Remove-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Name DnsOverHttpsMode -ErrorAction SilentlyContinue
Remove-Item "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Recurse -ErrorAction SilentlyContinue

# remove the VPN-blocking firewall rules
Remove-NetFirewallRule -Group "filter1" -ErrorAction SilentlyContinue

# unblock VPN apps we blocked from running (remove our IFEO debugger entries)
$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
Get-ChildItem $ifeo -ErrorAction SilentlyContinue | ForEach-Object {
    $dbg = (Get-ItemProperty $_.PSPath -Name Debugger -ErrorAction SilentlyContinue).Debugger
    if ($dbg -like "*cmd.exe /c exit*") {
        Remove-Item $_.PSPath -Recurse -ErrorAction SilentlyContinue
    }
}

# remove files
Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
Write-Host "filter1 removed."
