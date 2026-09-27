# Full removal of both filter1 layers. The uninstall code is verified by the
# installer's uninstaller BEFORE this runs, so this just reverts everything.
$ErrorActionPreference = "SilentlyContinue"

# stop and remove both scheduled tasks
foreach ($t in @("filter1","filter1-proxy")) {
  schtasks /End /TN $t | Out-Null
  schtasks /Delete /TN $t /F | Out-Null
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*filter1*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# restore DNS to automatic
Get-NetAdapter | Where-Object { $_.Status -eq "Up" } |
  ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }
Clear-DnsClientCache

# turn off the system proxy
$is = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings"
Set-ItemProperty $is -Name ProxyEnable -Value 0 -Type DWord
Remove-ItemProperty $is -Name ProxyServer

# re-enable DoH and remove browser proxy/QUIC policies
foreach ($b in @("HKLM:\SOFTWARE\Policies\Google\Chrome","HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
  Remove-ItemProperty $b -Name DnsOverHttpsMode
  Remove-ItemProperty $b -Name QuicAllowed
  Remove-ItemProperty $b -Name ProxyMode
  Remove-ItemProperty $b -Name ProxyServer
}
Remove-Item "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Recurse
Remove-Item "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates" -Recurse

# remove firewall rules
Remove-NetFirewallRule -Group "filter1"

# unblock VPN apps (remove our IFEO debugger entries)
$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
Get-ChildItem $ifeo | ForEach-Object {
  $dbg = (Get-ItemProperty $_.PSPath -Name Debugger -ErrorAction SilentlyContinue).Debugger
  if ($dbg -like "*cmd.exe /c exit*") { Remove-Item $_.PSPath -Recurse }
}

# remove the mitmproxy root certificate
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*mitmproxy*" } |
  ForEach-Object { Remove-Item $_.PSPath -Force }

# clear mitmproxy conf + device state
Remove-Item -Recurse -Force (Join-Path $env:ProgramData "filter1")

Write-Host "filter1 fully removed."
