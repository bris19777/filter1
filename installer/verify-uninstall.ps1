param([string]$Server, [string]$Token)
# Prompt for the uninstall code and verify it against the control server.
# Exit 0 only if the code is valid; the installer aborts uninstall otherwise.
Add-Type -AssemblyName Microsoft.VisualBasic
$code = [Microsoft.VisualBasic.Interaction]::InputBox(
  "הזן את קוד ההסרה מלוח הבקרה:", "הסרת filter1", "")
if (-not $code) { exit 1 }
try {
  $u = "$Server/api/verify-uninstall?token=$Token&code=$([uri]::EscapeDataString($code.Trim()))"
  $resp = Invoke-RestMethod -Uri $u -TimeoutSec 15
  if ($resp.ok) { exit 0 } else { exit 1 }
} catch {
  exit 1
}
