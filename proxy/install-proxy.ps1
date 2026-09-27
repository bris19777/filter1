# filter1 layer 5 (ADVANCED): full TLS-intercepting content filter via mitmproxy.
#
# Filters HTTPS by hostname even against DoH/ECH, because mitmproxy terminates
# TLS. Installs the mitmproxy root certificate, forces the machine through the
# local proxy, blocks QUIC so nothing escapes over UDP 443, and runs mitmdump as
# a SYSTEM service with our filter_addon.py.
#
# WARNING - read before running:
#  * Only on machines YOU administer. The proxy sees all plaintext HTTPS.
#  * Apps with certificate pinning (banking, WhatsApp, some Google/Microsoft
#    services, most mobile-style apps) WILL break and must be bypassed.
#  * Test on ONE machine you can reach. Keep uninstall-proxy.ps1 handy.
#  * Requires administrator. Reuses filter1.cfg + device_id from the DNS agent,
#    so install the DNS agent first.

$ErrorActionPreference = "Stop"
$InstallDir = Join-Path $env:ProgramFiles "filter1"
$Conf = Join-Path $env:ProgramData "filter1\mitmproxy"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# create a registry key only if missing (New-Item -Force fails on existing keys)
function NK($p) { if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null } }

function Get-PyArch($py) {
  try {
    $v = & $py -c "import sys;print(sys.version)" 2>$null
    if ($v -match "\(ARM64\)") { return "ARM64" }
    if ($v -match "\(AMD64\)") { return "AMD64" }
    return ""
  } catch { return "" }
}
function All-Pythons {
  $c = @()
  Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { $c += $_.Source }
  $c += @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "C:\Program Files\Python312\python.exe","C:\Program Files\Python311\python.exe")
  return $c | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
}
function Find-X64Python {
  # mitmproxy needs binary wheels that may be missing for ARM64 Python, so
  # always prefer a 64-bit (AMD64) Python, which runs under emulation on ARM.
  foreach ($p in (All-Pythons)) { if ((Get-PyArch $p) -eq "AMD64") { return $p } }
  return $null
}

Write-Host "== filter1 mitmproxy content filter =="
$py = Find-X64Python
if (-not $py -and (Get-Command winget -ErrorAction SilentlyContinue)) {
  Write-Host "Installing 64-bit Python via winget..."
  winget install -e --id Python.Python.3.12 --architecture x64 --scope machine `
    --accept-source-agreements --accept-package-agreements
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine")
  $py = Find-X64Python
}
if (-not $py) {
  Write-Host "Downloading 64-bit Python from python.org..."
  $pyUrl = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
  $tmp = Join-Path $env:TEMP "python-x64-setup.exe"
  Invoke-WebRequest -Uri $pyUrl -OutFile $tmp
  Start-Process $tmp -ArgumentList "/quiet","InstallAllUsers=1","PrependPath=1","Include_launcher=0" -Wait
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine")
  $py = Find-X64Python
}
if (-not $py) { Write-Error "Could not find or install 64-bit Python."; return }
Write-Host "Using x64 Python: $py"

# 1. install mitmproxy
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install mitmproxy | Out-Null
$mitm = Join-Path (Split-Path $py) "Scripts\mitmdump.exe"
if (-not (Test-Path $mitm)) { $mitm = Join-Path (Split-Path $py) "mitmdump.exe" }
if (-not (Test-Path $mitm)) { Write-Error "mitmdump.exe not found after install."; return }

# 2. generate the mitmproxy root CA into a fixed confdir
New-Item -ItemType Directory -Force -Path $Conf | Out-Null
$p = Start-Process $mitm -ArgumentList "--set","confdir=$Conf","--listen-port","8080","-q" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 6
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
$ca = Join-Path $Conf "mitmproxy-ca-cert.cer"
if (-not (Test-Path $ca)) { $ca = Join-Path $Conf "mitmproxy-ca-cert.pem" }
if (-not (Test-Path $ca)) { Write-Error "CA not generated. Check mitmdump ran."; return }

# 3. trust the CA machine-wide (Chrome/Edge use the Windows store)
Import-Certificate -FilePath $ca -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
# Firefox: trust Windows enterprise roots
NK "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates"
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates" -Name ImportEnterpriseRoots -Value 1 -Type DWord

# 4. copy the filtering addon
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item (Join-Path $ScriptDir "filter_addon.py") $InstallDir -Force
$addon = Join-Path $InstallDir "filter_addon.py"

# 5. force the machine through the local proxy, for all users, and lock the UI
$is = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings"
NK $is
Set-ItemProperty $is -Name ProxyEnable -Value 1 -Type DWord
Set-ItemProperty $is -Name ProxyServer -Value "127.0.0.1:8080"
Set-ItemProperty $is -Name ProxyOverride -Value "<local>"
$pol = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\CurrentVersion\Internet Settings"
NK $pol
Set-ItemProperty $pol -Name ProxySettingsPerUser -Value 0 -Type DWord
$iepol = "HKLM:\SOFTWARE\Policies\Microsoft\Internet Explorer\Control Panel"
NK $iepol
Set-ItemProperty $iepol -Name Proxy -Value 1 -Type DWord

# 6. block QUIC so browsers fall back to interceptable TCP
New-NetFirewallRule -DisplayName "filter1 block QUIC" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 443 -ErrorAction SilentlyContinue | Out-Null
foreach ($b in @("HKLM:\SOFTWARE\Policies\Google\Chrome","HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
  NK $b
  Set-ItemProperty -Path $b -Name QuicAllowed -Value 0 -Type DWord
}

# 7. run mitmdump as a SYSTEM scheduled task at boot, restart on failure
$args = "--set confdir=$Conf -s `"$addon`" --listen-host 127.0.0.1 --listen-port 8080 -q"
$action = New-ScheduledTaskAction -Execute $mitm -Argument $args
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -RestartInterval (New-TimeSpan -Minutes 1) `
  -RestartCount 999 -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "filter1-proxy" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "filter1-proxy"

Write-Host "Done. mitmproxy content filter running on 127.0.0.1:8080."
Write-Host "Test browsing now. If a pinned app (e.g. banking) breaks, add its"
Write-Host "host to a mitmproxy ignore list - see proxy/README.md."
