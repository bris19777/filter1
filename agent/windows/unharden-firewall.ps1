# Revert filter1 layer 3 (default-deny outbound) back to normal.
$ErrorActionPreference = "Stop"
Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Allow
Remove-NetFirewallRule -Group "filter1-lockdown" -ErrorAction SilentlyContinue
Write-Host "Default-deny outbound is OFF. Reverted to normal."
