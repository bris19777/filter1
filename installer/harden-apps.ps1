# filter1 optional hardening: block known VPN client executables from launching,
# by file name, via Image File Execution Options (IFEO).
#
# This is an OPT-IN layer, kept out of the default installer on purpose: the IFEO
# "Debugger" mechanism is a well-known malware signature (MITRE T1546.012) that
# Windows Defender / SmartScreen frequently flag, which can block the whole
# install. Run this only on machines you administer and can reach, after the main
# install. Undo it any time with unharden-apps.ps1 (the uninstaller also removes
# these entries).
#
# Blocking is by executable name, so it can be bypassed by renaming the file; for
# real coverage combine it with the default-deny firewall (harden-firewall) and a
# standard (non-admin) child account.
#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$vpnApps = @("ProtonVPN.exe","ProtonVPNService.exe","nordvpn.exe","NordVPN.exe",
  "expressvpn.exe","ExpressVPN.exe","openvpn.exe","openvpn-gui.exe","wireguard.exe",
  "tunnelbear.exe","Windscribe.exe","windscribe.exe","hola.exe","psiphon3.exe",
  "hss.exe","HotspotShield.exe","surfshark.exe")

$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
foreach ($ap in $vpnApps) {
  New-Item -Path "$ifeo\$ap" -Force | Out-Null
  Set-ItemProperty -Path "$ifeo\$ap" -Name "Debugger" -Value "$env:SystemRoot\System32\cmd.exe /c exit"
}
Write-Host "filter1: VPN app execution blocking enabled for $($vpnApps.Count) apps."
