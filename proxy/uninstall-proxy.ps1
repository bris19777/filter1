# Remove the filter1 mitmproxy content filter and revert its system changes.
$ErrorActionPreference = "SilentlyContinue"

# stop and remove the proxy service
schtasks /End /TN filter1-proxy | Out-Null
schtasks /Delete /TN filter1-proxy /F | Out-Null

# turn off the system proxy and unlock the UI
$is = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings"
Set-ItemProperty $is -Name ProxyEnable -Value 0 -Type DWord
Remove-ItemProperty $is -Name ProxyServer
Remove-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Internet Explorer\Control Panel" -Name Proxy
Remove-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Windows\CurrentVersion\Internet Settings" -Name ProxySettingsPerUser

# allow QUIC again and remove the browser proxy policy
Remove-NetFirewallRule -DisplayName "filter1 block QUIC"
foreach ($b in @("HKLM:\SOFTWARE\Policies\Google\Chrome","HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
  Remove-ItemProperty $b -Name QuicAllowed
  Remove-ItemProperty $b -Name ProxyMode
  Remove-ItemProperty $b -Name ProxyServer
}

# remove the mitmproxy root certificate we installed
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*mitmproxy*" } |
  ForEach-Object { Remove-Item $_.PSPath -Force }

Write-Host "filter1 proxy removed and system proxy reverted."
