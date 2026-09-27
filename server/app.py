#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""filter1 control server (multi-device).

Each on-device agent generates a stable machine id on first run and registers
itself by polling the config API. The parent then controls every machine
separately from the Hebrew RTL web UI. New machines inherit the global defaults.

Single-file Flask app with a JSON store on disk, in the same spirit as the
bsd-invoices project.
"""

import json
import os
import re
import secrets
import time
from functools import wraps

from flask import (Flask, Response, jsonify, redirect, render_template_string,
                   request, session, url_for)

APP_VERSION = "0.9.0"

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FILTER1_DATA", os.path.join(BASE, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

PARENT_PASSWORD = os.environ.get("PARENT_PASSWORD", "changeme")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "DEV")
SECRET = os.environ.get("SECRET", "dev-secret-change-me")

MODES = ("open", "lockdown", "blacklist", "whitelist")
ONLINE_WINDOW = 180  # seconds since last_seen to count a device as online

DEFAULT_BLOCKLISTS = [
    # StevenBlack unified + porn (~150k domains, includes the major adult sites)
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts",
    # Sinfonietta pornography list, as a second reliable adult-content source
    "https://raw.githubusercontent.com/Sinfonietta/hostfiles/master/pornography-hosts",
]

# The per-device policy. New devices are created from these defaults.
DEVICE_DEFAULTS = {
    "mode": "open",
    "whitelist": [],
    "blacklist_manual": [],
}

DEFAULT_STORE = {
    "defaults": dict(DEVICE_DEFAULTS),
    "blocklists": DEFAULT_BLOCKLISTS,   # shared across all devices
    "devices": {},                      # device_id -> device record
    "uninstall_code": "",
    "updated_at": 0,
}


def load_store():
    if not os.path.exists(CONFIG_PATH):
        save_store(DEFAULT_STORE)
        return json.loads(json.dumps(DEFAULT_STORE))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        st = json.load(f)
    for k, v in DEFAULT_STORE.items():
        st.setdefault(k, v)
    return st


def save_store(st):
    st["updated_at"] = int(time.time())
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


app = Flask(__name__)
app.secret_key = SECRET


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return fn(*a, **k)
    return wrapper


def clean_domains(text):
    out = []
    for line in (text or "").splitlines():
        d = line.strip().lower()
        if not d or d.startswith("#"):
            continue
        d = d.replace("https://", "").replace("http://", "").split("/")[0]
        if d and d not in out:
            out.append(d)
    return out


SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


# ---------------------------------------------------------------- agent API

@app.get("/api/config")
def api_config():
    """Polled by each agent. Registers unknown devices, returns that device's
    policy. Protected by a shared token; identified by device_id."""
    token = request.args.get("token") or request.headers.get("X-Agent-Token")
    if token != AGENT_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    device_id = (request.args.get("device_id") or "").strip()
    if not device_id or not SAFE_ID.match(device_id):
        return jsonify({"error": "bad device_id"}), 400
    name = (request.args.get("name") or "").strip()[:80]

    st = load_store()
    dev = st["devices"].get(device_id)
    now = int(time.time())
    if dev is None:
        dev = dict(DEVICE_DEFAULTS)
        dev.update({
            "name": name or device_id,
            "hostname": name,
            "first_seen": now,
        })
        st["devices"][device_id] = dev
    # update presence
    dev["last_seen"] = now
    if name:
        dev["hostname"] = name
    # status the agent reports about the policy it has actually applied
    try:
        dev["blocked_count"] = int(request.args.get("count"))
    except (TypeError, ValueError):
        pass
    amode = request.args.get("amode")
    if amode:
        dev["active_mode"] = amode
    save_store(st)

    return jsonify({
        "device_id": device_id,
        "mode": dev["mode"],
        "whitelist": dev["whitelist"],
        "blacklist_manual": dev["blacklist_manual"],
        "blocklists": st["blocklists"],
        "uninstall_code": st["uninstall_code"],
        "updated_at": st["updated_at"],
    })


@app.get("/api/verify-uninstall")
def api_verify_uninstall():
    """Used by the Windows uninstaller to confirm the parent's one-time code."""
    token = request.args.get("token") or request.headers.get("X-Agent-Token")
    if token != AGENT_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    code = (request.args.get("code") or "").strip().upper()
    st = load_store()
    ok = bool(st["uninstall_code"]) and code == st["uninstall_code"]
    return jsonify({"ok": ok})


@app.get("/agent.py")
def serve_agent():
    """Serve the agent source so the one-line installer can fetch it."""
    for p in (os.path.join(BASE, "client_agent.py"),
              os.path.join(BASE, "..", "agent", "agent.py")):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return Response(f.read(), mimetype="text/plain")
    return "agent source not found", 404


@app.get("/install.ps1")
def serve_installer():
    """Return a self-elevating PowerShell installer with the server URL and the
    agent token injected. Usage on the client (admin PowerShell):
        iex (irm 'https://<server>/install.ps1?token=<AGENT_TOKEN>')
    """
    token = (request.args.get("token") or "").strip().strip("<>\"' ")
    base = request.host_url.rstrip("/")
    script = INSTALLER_PS1.replace("__SERVER__", base).replace("__TOKEN__", token)
    return Response(script, mimetype="text/plain")


@app.get("/filter_addon.py")
def serve_filter_addon():
    """Serve the mitmproxy filtering addon for the proxy one-line installer."""
    for p in (os.path.join(BASE, "proxy_filter_addon.py"),
              os.path.join(BASE, "..", "proxy", "filter_addon.py")):
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return Response(f.read(), mimetype="text/plain")
    return "filter addon not found", 404


@app.get("/install-proxy.ps1")
def serve_proxy_installer():
    """Return the self-elevating installer for the ADVANCED mitmproxy content
    filter. Deliberately a separate one-liner (not part of /install.ps1) because
    it is invasive. Usage on the client (admin PowerShell):
        iex (irm 'https://<server>/install-proxy.ps1?token=<AGENT_TOKEN>')
    """
    token = (request.args.get("token") or "").strip().strip("<>\"' ")
    base = request.host_url.rstrip("/")
    script = PROXY_INSTALLER_PS1.replace("__SERVER__", base).replace("__TOKEN__", token)
    return Response(script, mimetype="text/plain")


@app.get("/healthz")
def healthz():
    return "ok", 200


# ---------------------------------------------------------------- web UI

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password") == PARENT_PASSWORD:
            session["auth"] = True
            return redirect(url_for("index"))
        error = "סיסמה שגויה"
    return render_template_string(LOGIN_HTML, error=error, version=APP_VERSION)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


MODE_LABELS = {
    "open": ("הכל מותר", "גלישה חופשית ללא סינון"),
    "lockdown": ("חסימה מלאה", "רק הרשימה הלבנה מותרת, כל השאר חסום"),
    "blacklist": ("רשימה שחורה", "חוסם רשימות ציבוריות של אתרים בעייתיים"),
    "whitelist": ("רשימה לבנה", "רק אתרים שאישרת מותרים"),
}


@app.get("/")
@login_required
def index():
    st = load_store()
    now = int(time.time())
    devices = []
    for did, d in sorted(st["devices"].items(),
                         key=lambda kv: kv[1].get("last_seen", 0), reverse=True):
        last = d.get("last_seen", 0)
        online = (now - last) <= ONLINE_WINDOW
        mode = d.get("mode", "open")
        active = d.get("active_mode")
        bc = d.get("blocked_count")
        if not online:
            status = ""
        elif active and active != mode:
            status = "מחיל שינוי…"
        elif mode == "blacklist":
            status = f"פעיל · {bc:,} חסומים" if bc else "טוען רשימה…"
        else:
            status = "פעיל"
        devices.append({
            "id": did,
            "name": d.get("name") or did,
            "hostname": d.get("hostname", ""),
            "mode": mode,
            "mode_label": MODE_LABELS.get(mode)[0],
            "online": online,
            "last_seen": last,
            "ago": human_ago(now - last) if last else "מעולם לא",
            "status": status,
        })
    return render_template_string(
        INDEX_HTML, devices=devices, version=APP_VERSION,
        uninstall_code=st["uninstall_code"])


@app.get("/device/<device_id>")
@login_required
def device(device_id):
    st = load_store()
    d = st["devices"].get(device_id)
    if not d:
        return redirect(url_for("index"))
    return render_template_string(
        DEVICE_HTML, did=device_id, d=d, modes=MODES, labels=MODE_LABELS,
        version=APP_VERSION,
        wl="\n".join(d.get("whitelist", [])),
        bl="\n".join(d.get("blacklist_manual", [])),
        blocklists="\n".join(st["blocklists"]),
    )


@app.post("/device/<device_id>/save")
@login_required
def device_save(device_id):
    st = load_store()
    d = st["devices"].get(device_id)
    if not d:
        return redirect(url_for("index"))
    name = (request.form.get("name") or "").strip()[:80]
    if name:
        d["name"] = name
    mode = request.form.get("mode", "open")
    if mode in MODES:
        d["mode"] = mode
    d["whitelist"] = clean_domains(request.form.get("whitelist"))
    d["blacklist_manual"] = clean_domains(request.form.get("blacklist_manual"))
    # blocklists are shared across devices
    st["blocklists"] = [l.strip() for l in
                        (request.form.get("blocklists") or "").splitlines()
                        if l.strip() and not l.strip().startswith("#")]
    save_store(st)
    return redirect(url_for("device", device_id=device_id))


@app.post("/device/<device_id>/delete")
@login_required
def device_delete(device_id):
    st = load_store()
    st["devices"].pop(device_id, None)
    save_store(st)
    return redirect(url_for("index"))


@app.post("/uninstall-code")
@login_required
def uninstall_code():
    st = load_store()
    st["uninstall_code"] = secrets.token_hex(4).upper()
    save_store(st)
    return redirect(url_for("index"))


def human_ago(secs):
    if secs < 60:
        return f"לפני {secs} שניות"
    if secs < 3600:
        return f"לפני {secs // 60} דקות"
    if secs < 86400:
        return f"לפני {secs // 3600} שעות"
    return f"לפני {secs // 86400} ימים"


# ---------------------------------------------------------------- templates

CSS = """
 body{font-family:system-ui,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}
 .wrap{max-width:820px;margin:0 auto}
 h1{font-size:22px}
 a{color:#93c5fd}
 .bar{display:flex;justify-content:space-between;align-items:center}
 .bar a{color:#94a3b8;font-size:13px}
 .dev{display:block;padding:16px;border-radius:12px;background:#1e293b;margin:10px 0;
   text-decoration:none;color:inherit;border:1px solid #334155}
 .dev:hover{border-color:#3b82f6}
 .dev .top{display:flex;justify-content:space-between;align-items:center}
 .dev b{font-size:16px}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-left:6px}
 .on{background:#22c55e}.off{background:#64748b}
 .pill{font-size:12px;padding:3px 10px;border-radius:20px;background:#334155;color:#cbd5e1}
 .meta{color:#94a3b8;font-size:13px;margin-top:4px}
 .modes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}
 label.mode{display:block;padding:16px;border-radius:12px;border:2px solid #334155;
   background:#1e293b;cursor:pointer}
 label.mode.sel{border-color:#3b82f6;background:#1e3a5f}
 label.mode input{margin-left:8px}
 .mode b{font-size:16px}.mode small{display:block;color:#94a3b8;margin-top:4px}
 textarea{width:100%;height:120px;padding:10px;border-radius:8px;border:1px solid #334155;
   background:#0f172a;color:#e2e8f0;box-sizing:border-box;font-family:monospace;direction:ltr;text-align:left}
 input.txt{padding:10px;border-radius:8px;border:1px solid #334155;background:#0f172a;
   color:#e2e8f0;box-sizing:border-box;width:100%}
 .field{margin:16px 0}.field h3{margin:0 0 6px;font-size:15px}
 .field p{margin:0 0 8px;color:#94a3b8;font-size:13px}
 button{padding:12px 24px;border:0;border-radius:8px;background:#3b82f6;color:#fff;
   font-weight:600;cursor:pointer;font-size:15px}
 button.gray{background:#64748b}button.red{background:#dc2626}
 .box{background:#1e293b;padding:16px;border-radius:12px;margin:16px 0}
 .code{font-family:monospace;font-size:20px;color:#fbbf24}
 .v{color:#64748b;font-size:12px;margin-top:24px;text-align:center}
 .empty{color:#94a3b8;text-align:center;padding:40px;border:1px dashed #334155;border-radius:12px}
"""

LOGIN_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — כניסה</title><style>__CSS__
 body{display:flex;min-height:100vh;align-items:center;justify-content:center}
 .card{background:#1e293b;padding:32px;border-radius:16px;width:320px}
 .card input{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid #334155;
   background:#0f172a;color:#e2e8f0;box-sizing:border-box}
 .card button{width:100%}.err{color:#f87171;font-size:14px;min-height:18px}
</style></head><body>
<form class="card" method="post">
 <h1>🛡️ filter1 — בקרת הורים</h1>
 <div class="err">{{error}}</div>
 <input type="password" name="password" placeholder="סיסמת הורה" autofocus>
 <button type="submit">כניסה</button>
 <div class="v">גרסה {{version}}</div>
</form></body></html>"""

INDEX_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — מחשבים</title><style>__CSS__</style></head><body><div class="wrap">
 <div class="bar"><h1>🛡️ filter1 — מחשבים</h1><a href="/logout">יציאה</a></div>
 {% if not devices %}
  <div class="empty">אין עדיין מחשבים רשומים.<br>התקן את הסוכן על מחשב לקוח והוא יופיע כאן אוטומטית.</div>
 {% endif %}
 {% for dv in devices %}
  <a class="dev" href="/device/{{dv.id}}">
   <div class="top">
    <b><span class="dot {{'on' if dv.online else 'off'}}"></span>{{dv.name}}</b>
    <span class="pill">{{dv.mode_label}}</span>
   </div>
   <div class="meta">{{dv.hostname}} · {{dv.status if (dv.online and dv.status) else ('מחובר' if dv.online else dv.ago)}} · מזהה {{dv.id}}</div>
  </a>
 {% endfor %}
 <div class="box">
  <h3>קוד הסרה חד-פעמי</h3>
  <p style="color:#94a3b8;font-size:13px">חל על כל המחשבים. נדרש כדי להסיר סוכן.</p>
  {% if uninstall_code %}<div class="code">{{uninstall_code}}</div>{% endif %}
  <form method="post" action="/uninstall-code" style="margin-top:8px">
   <button type="submit" class="gray">הפק קוד הסרה חדש</button></form>
 </div>
 <div class="v">גרסה {{version}}</div>
</div></body></html>"""

DEVICE_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — {{d.name}}</title><style>__CSS__</style></head><body><div class="wrap">
 <div class="bar"><h1>🖥️ {{d.name}}</h1><a href="/">← כל המחשבים</a></div>
 <p class="meta">שם מארח: {{d.hostname}} · מזהה: {{did}}</p>
 <form method="post" action="/device/{{did}}/save">
  <div class="field">
   <h3>שם תצוגה</h3>
   <input class="txt" name="name" value="{{d.name}}" placeholder="המחשב של יוסי">
  </div>
  <div class="modes">
   {% for m in modes %}
   <label class="mode {{'sel' if d.mode==m else ''}}">
    <input type="radio" name="mode" value="{{m}}" {{'checked' if d.mode==m else ''}}>
    <b>{{labels[m][0]}}</b><small>{{labels[m][1]}}</small>
   </label>
   {% endfor %}
  </div>
  <div class="field">
   <h3>רשימה לבנה (מותר)</h3>
   <p>דומיין בכל שורה. תמיד מותר במצב "חסימה מלאה" ו"רשימה לבנה".</p>
   <textarea name="whitelist" placeholder="example.com">{{wl}}</textarea>
  </div>
  <div class="field">
   <h3>חסימות ידניות נוספות</h3>
   <p>דומיינים לחסום מעבר לרשימות הציבוריות (למצב "רשימה שחורה").</p>
   <textarea name="blacklist_manual" placeholder="badsite.com">{{bl}}</textarea>
  </div>
  <div class="field">
   <h3>מקורות רשימות ציבוריות (משותף לכל המחשבים)</h3>
   <p>כתובות של קובצי hosts ציבוריים לשימוש במצב "רשימה שחורה".</p>
   <textarea name="blocklists">{{blocklists}}</textarea>
  </div>
  <button type="submit">שמור והחל על מחשב זה</button>
 </form>
 <div class="box">
  <form method="post" action="/device/{{did}}/delete"
        onsubmit="return confirm('למחוק את המחשב מהרשימה? הוא יירשם מחדש בפעם הבאה שהסוכן יתחבר.')">
   <button type="submit" class="red">מחק מחשב מהרשימה</button>
  </form>
 </div>
 <div class="v">גרסה {{version}}</div>
</div></body></html>"""

# Inject the shared CSS. Using str.replace (not %-formatting) so the templates
# can contain Jinja {% ... %} blocks and CSS % units without conflict.
LOGIN_HTML = LOGIN_HTML.replace("__CSS__", CSS)
INDEX_HTML = INDEX_HTML.replace("__CSS__", CSS)
DEVICE_HTML = DEVICE_HTML.replace("__CSS__", CSS)


# One-line PowerShell installer, served by /install.ps1 with __SERVER__ and
# __TOKEN__ filled in. Self-elevates via UAC, downloads the agent, installs it
# as a SYSTEM scheduled task, and starts it.
INSTALLER_PS1 = r'''$ErrorActionPreference = "Stop"
$Server = "__SERVER__"
$Token  = "__TOKEN__"

# self-elevate if not running as administrator
$admin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "Requesting administrator privileges..."
  $cmd = "iex (irm '$Server/install.ps1?token=$Token')"
  Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile","-Command",$cmd
  return
}

$InstallDir = Join-Path $env:ProgramFiles "filter1"

function Get-PyArch($py) {
  try { return (& $py -c "import platform;print(platform.machine())" 2>$null).Trim() } catch { return "" }
}
function All-Pythons {
  $c = @()
  Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { $c += $_.Source }
  $c += @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "C:\Program Files\Python312\python.exe","C:\Program Files\Python311\python.exe",
    "C:\Python312\python.exe","C:\Python311\python.exe")
  return $c | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
}
function Find-X64Python {
  # always prefer a 64-bit (AMD64) Python, even if an ARM64 one is installed
  foreach ($p in (All-Pythons)) { if ((Get-PyArch $p) -eq "AMD64") { return $p } }
  return $null
}

Write-Host "== filter1 client installer =="
$py = Find-X64Python
if (-not $py) {
  Write-Host "Installing 64-bit Python via winget..."
  winget install -e --id Python.Python.3.12 --architecture x64 --scope machine `
    --accept-source-agreements --accept-package-agreements
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine")
  $py = Find-X64Python
}
if (-not $py) { Write-Error "Could not find or install 64-bit Python."; return }
$pyw = $py -replace "python.exe$","pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Write-Host "Using x64 Python: $py"

& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install dnslib | Out-Null

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Invoke-WebRequest -Uri "$Server/agent.py" -OutFile (Join-Path $InstallDir "agent.py")
$cfg = Join-Path $InstallDir "filter1.cfg"
[System.IO.File]::WriteAllText($cfg, "server=$Server`ntoken=$Token")

$agent = Join-Path $InstallDir "agent.py"
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$agent`" --config `"$cfg`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -RestartInterval (New-TimeSpan -Minutes 1) `
  -RestartCount 999 -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "filter1" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "filter1"

# Disable browser DNS-over-HTTPS so browsers cannot bypass the filter.
New-Item -Path "HKLM:\SOFTWARE\Policies\Google\Chrome" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Google\Chrome" -Name DnsOverHttpsMode -Value "off"
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Edge" -Name DnsOverHttpsMode -Value "off"
New-Item -Path "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Name Enabled -Value 0 -Type DWord
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS" -Name Locked -Value 1 -Type DWord

# Firewall: block common VPN tunnel protocols so a VPN cannot bypass the filter.
# (Stealth VPNs over TCP 443 cannot be blocked by port; a standard, non-admin
# user account is what really prevents installing/running a VPN.)
Remove-NetFirewallRule -Group "filter1" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "filter1 block WireGuard" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 51820 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block OpenVPN" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 1194 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block IKEv2" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 500,4500 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block PPTP" -Group "filter1" -Direction Outbound -Action Block -Protocol TCP -RemotePort 1723 -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "filter1 block L2TP" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 1701 -ErrorAction SilentlyContinue | Out-Null
$vpnExes = @(
  "$env:ProgramFiles\Proton\VPN\ProtonVPN.exe",
  "$env:ProgramFiles\Proton Technologies\ProtonVPN\ProtonVPN.exe",
  "${env:ProgramFiles(x86)}\Proton Technologies\ProtonVPN\ProtonVPN.exe",
  "$env:LOCALAPPDATA\Programs\Proton VPN\ProtonVPN.exe")
foreach ($e in $vpnExes) {
  if (Test-Path $e) {
    New-NetFirewallRule -DisplayName "filter1 block ProtonVPN" -Group "filter1" -Direction Outbound -Action Block -Program $e -ErrorAction SilentlyContinue | Out-Null
  }
}

# Block known VPN clients from running at all (Image File Execution Options).
# Blocks by executable name regardless of install path.
$vpnApps = @("ProtonVPN.exe","ProtonVPNService.exe","ProtonVPN.WireGuardService.exe",
  "nordvpn.exe","NordVPN.exe","expressvpn.exe","ExpressVPN.exe","openvpn.exe",
  "openvpn-gui.exe","wireguard.exe","wg.exe","tunnelbear.exe","Windscribe.exe",
  "windscribe.exe","hola.exe","psiphon3.exe","hss.exe","HotspotShield.exe","surfshark.exe")
$ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
foreach ($a in $vpnApps) {
  New-Item -Path "$ifeo\$a" -Force | Out-Null
  Set-ItemProperty -Path "$ifeo\$a" -Name "Debugger" -Value "$env:SystemRoot\System32\cmd.exe /c exit"
}

Write-Host "Done. The machine will appear in your control panel within a minute."
'''


# One-line PowerShell installer for the ADVANCED mitmproxy content filter,
# served by /install-proxy.ps1. Invasive: keep it a separate, deliberate step.
PROXY_INSTALLER_PS1 = r'''$ErrorActionPreference = "Stop"
$Server = "__SERVER__"
$Token  = "__TOKEN__"

$admin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "Requesting administrator privileges..."
  $cmd = "iex (irm '$Server/install-proxy.ps1?token=$Token')"
  Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile","-Command",$cmd
  return
}

$InstallDir = Join-Path $env:ProgramFiles "filter1"
$Conf = Join-Path $env:ProgramData "filter1\mitmproxy"

function Get-PyArch($py) {
  try { return (& $py -c "import platform;print(platform.machine())" 2>$null).Trim() } catch { return "" }
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
  foreach ($p in (All-Pythons)) { if ((Get-PyArch $p) -eq "AMD64") { return $p } }
  return $null
}

Write-Host "== filter1 mitmproxy content filter =="
$py = Find-X64Python
if (-not $py) {
  Write-Host "Installing 64-bit Python via winget..."
  winget install -e --id Python.Python.3.12 --architecture x64 --scope machine `
    --accept-source-agreements --accept-package-agreements
  $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine")
  $py = Find-X64Python
}
if (-not $py) { Write-Error "Could not find or install 64-bit Python."; return }
Write-Host "Using x64 Python: $py"

& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install mitmproxy | Out-Null
$mitm = Join-Path (Split-Path $py) "Scripts\mitmdump.exe"
if (-not (Test-Path $mitm)) { $mitm = Join-Path (Split-Path $py) "mitmdump.exe" }
if (-not (Test-Path $mitm)) { Write-Error "mitmdump.exe not found after install."; return }

New-Item -ItemType Directory -Force -Path $Conf | Out-Null
$p = Start-Process $mitm -ArgumentList "--set","confdir=$Conf","--listen-port","8080","-q" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 6
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
$ca = Join-Path $Conf "mitmproxy-ca-cert.cer"
if (-not (Test-Path $ca)) { $ca = Join-Path $Conf "mitmproxy-ca-cert.pem" }
if (-not (Test-Path $ca)) { Write-Error "CA not generated. Check mitmdump ran."; return }

Import-Certificate -FilePath $ca -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
New-Item -Path "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates" -Name ImportEnterpriseRoots -Value 1 -Type DWord

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$addon = Join-Path $InstallDir "filter_addon.py"
Invoke-WebRequest -Uri "$Server/filter_addon.py" -OutFile $addon

$is = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings"
New-Item $is -Force | Out-Null
Set-ItemProperty $is -Name ProxyEnable -Value 1 -Type DWord
Set-ItemProperty $is -Name ProxyServer -Value "127.0.0.1:8080"
Set-ItemProperty $is -Name ProxyOverride -Value "<local>"
$pol = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\CurrentVersion\Internet Settings"
New-Item $pol -Force | Out-Null
Set-ItemProperty $pol -Name ProxySettingsPerUser -Value 0 -Type DWord
$iepol = "HKLM:\SOFTWARE\Policies\Microsoft\Internet Explorer\Control Panel"
New-Item $iepol -Force | Out-Null
Set-ItemProperty $iepol -Name Proxy -Value 1 -Type DWord

New-NetFirewallRule -DisplayName "filter1 block QUIC" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 443 -ErrorAction SilentlyContinue | Out-Null
foreach ($b in @("HKLM:\SOFTWARE\Policies\Google\Chrome","HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
  New-Item -Path $b -Force | Out-Null
  Set-ItemProperty -Path $b -Name QuicAllowed -Value 0 -Type DWord
}

$pargs = "--set confdir=$Conf -s `"$addon`" --listen-host 127.0.0.1 --listen-port 8080 -q"
$action = New-ScheduledTaskAction -Execute $mitm -Argument $pargs
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -RestartInterval (New-TimeSpan -Minutes 1) `
  -RestartCount 999 -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "filter1-proxy" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "filter1-proxy"

Write-Host "Done. mitmproxy content filter running on 127.0.0.1:8080."
'''


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    app.run(host="0.0.0.0", port=port, debug=True)
