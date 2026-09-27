# filter1 OPTIONAL layer 3: default-deny outbound firewall.
# Blocks ALL outbound traffic except an allowlist of approved apps, so any VPN
# client (even a renamed one, even stealth-over-443) cannot reach the network.
#
# WARNING: this is a strong control. Run it locally on a machine you can reach,
# and test. If something you need loses internet, run unharden-firewall.ps1 to
# revert immediately. Requires administrator.

$ErrorActionPreference = "Stop"
$grp = "filter1-lockdown"

Write-Host "== filter1 default-deny outbound =="
Remove-NetFirewallRule -Group $grp -ErrorAction SilentlyContinue

$sys = "$env:SystemRoot\System32"

# core Windows processes, so the OS keeps updating/activating/working
$allow = @(
  "$sys\svchost.exe", "$sys\lsass.exe", "$sys\wininit.exe", "$sys\services.exe",
  "$sys\spoolsv.exe", "$sys\backgroundTaskHost.exe", "$sys\taskhostw.exe",
  "$sys\RuntimeBroker.exe", "$sys\dllhost.exe", "$sys\SearchApp.exe",
  "$sys\smartscreen.exe", "$sys\MoUsoCoreWorker.exe", "$sys\wuauclt.exe"
)

# the filter1 agent (needs DNS forwarding + HTTPS to the control server)
$allow += @(
  "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
  "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
  "C:\Program Files\Python312\pythonw.exe",
  "C:\Program Files\Python312\python.exe"
) | Where-Object { Test-Path $_ }

# approved browsers
$allow += @(
  "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles}\Mozilla Firefox\firefox.exe",
  "${env:ProgramFiles(x86)}\Mozilla Firefox\firefox.exe"
) | Where-Object { Test-Path $_ }

foreach ($p in ($allow | Select-Object -Unique)) {
  New-NetFirewallRule -DisplayName ("filter1 allow " + (Split-Path $p -Leaf)) `
    -Group $grp -Direction Outbound -Action Allow -Program $p `
    -ErrorAction SilentlyContinue | Out-Null
}

# essential low-level traffic not tied to a single exe
New-NetFirewallRule -DisplayName "filter1 allow DHCP" -Group $grp -Direction Outbound -Action Allow -Protocol UDP -RemotePort 67,68 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 allow DNS" -Group $grp -Direction Outbound -Action Allow -Protocol UDP -RemotePort 53 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 allow DNS TCP" -Group $grp -Direction Outbound -Action Allow -Protocol TCP -RemotePort 53 -ErrorAction SilentlyContinue | Out-Null

Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Block
Write-Host "Default-deny outbound is ON. Only approved apps reach the network."
Write-Host "Revert any time with: unharden-firewall.ps1"
