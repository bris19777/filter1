param([string]$Server, [string]$Token)
# Prompt for the uninstall code and verify it against the control server.
# Exit 0 only if the code is valid; the installer aborts uninstall otherwise.
#
# $Server may list several control-server URLs (comma/semicolon/space separated).
# We try each until one RESPONDS, so a domain blocked by a filtering network
# (e.g. Rimon) fails over to a reachable/whitelisted one. The first server that
# answers is authoritative for whether the code is valid; we move on only when a
# server can't be reached. A reachable server that rejects the code exits 1
# (fails closed — uninstall stays blocked), which is the safe direction.
Add-Type -AssemblyName Microsoft.VisualBasic

$servers = @($Server -split '[,;\s]+') |
  ForEach-Object { $_.Trim([char[]]@('"', "'", '<', '>', ' ')) } |
  Where-Object { $_ }
if (-not $servers) { exit 1 }

$code = [Microsoft.VisualBasic.Interaction]::InputBox(
  "הזן את קוד ההסרה מלוח הבקרה:", "הסרת filter1", "")
if (-not $code) { exit 1 }
$enc = [uri]::EscapeDataString($code.Trim())

foreach ($s in $servers) {
  $base = $s.TrimEnd('/')
  try {
    $u = "$base/api/verify-uninstall?token=$Token&code=$enc"
    $resp = Invoke-RestMethod -Uri $u -TimeoutSec 15
    # this server answered — it decides, we do not keep trying others
    if ($resp.ok) { exit 0 } else { exit 1 }
  } catch {
    continue   # unreachable (blocked/offline) — try the next server
  }
}
# no configured server could be reached
exit 1
