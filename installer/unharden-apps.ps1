# filter1: remove the optional VPN-app execution blocking added by harden-apps.ps1.
# Removes only the IFEO entries whose Debugger is our "cmd.exe /c exit" sentinel,
# so unrelated IFEO entries are left untouched.
#Requires -RunAsAdministrator
$ErrorActionPreference = "SilentlyContinue"

$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
$removed = 0
Get-ChildItem $ifeo | ForEach-Object {
  $dbg = (Get-ItemProperty $_.PSPath -Name Debugger -ErrorAction SilentlyContinue).Debugger
  if ($dbg -like "*cmd.exe /c exit*") {
    Remove-Item $_.PSPath -Recurse -Force
    $removed++
  }
}
Write-Host "filter1: removed VPN app execution blocking ($removed entries)."
