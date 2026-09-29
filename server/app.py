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
import threading
import time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, Response, jsonify, redirect, render_template_string,
                   request, send_file, session, url_for)

APP_VERSION = "1.1.8"

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FILTER1_DATA", os.path.join(BASE, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
# the signed installer, uploaded by the parent and served to agents for auto-update
INSTALLER_PATH = os.path.join(DATA_DIR, "filter1-setup.exe")

PARENT_PASSWORD = os.environ.get("PARENT_PASSWORD", "changeme")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "DEV")
SECRET = os.environ.get("SECRET", "dev-secret-change-me")

MODES = ("open", "lockdown", "blacklist", "whitelist")
ONLINE_WINDOW = 180  # seconds since last_seen to count a device as online
LOG_CAP = 300        # max blocked events and max unique visited hosts per device

DEFAULT_BLOCKLISTS = [
    # StevenBlack unified + porn (~150k domains, includes the major adult sites)
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts",
    # Sinfonietta pornography list, as a second reliable adult-content source
    "https://raw.githubusercontent.com/Sinfonietta/hostfiles/master/pornography-hosts",
]

# Curated blocking categories. Each is a set of the primary domains for that kind
# of service (subdomains are matched automatically by the agent). Enabling a
# category on a device adds its domains to that device's block list. Effective in
# "blacklist" mode, alongside the public blocklists.
CATEGORIES = {
    "streaming": {
        "label": "סטרימינג ווידאו ומוזיקה",
        "domains": [
            "youtube.com", "youtu.be", "googlevideo.com", "ytimg.com",
            "youtubei.googleapis.com", "netflix.com", "nflxvideo.net",
            "nflximg.net", "nflxext.com", "nflxso.net", "disneyplus.com",
            "disney-plus.net", "dssott.com", "hulu.com", "hulustream.com",
            "primevideo.com", "aiv-cdn.net", "aiv-delivery.net", "max.com",
            "hbomax.com", "hbomaxcdn.com", "twitch.tv", "ttvnw.net", "jtvnw.net",
            "vimeo.com", "vimeocdn.com", "dailymotion.com", "dmcdn.net",
            "spotify.com", "scdn.co", "spotifycdn.com",
        ],
    },
    "games": {
        "label": "משחקי אונליין",
        "domains": [
            "roblox.com", "rbxcdn.com", "epicgames.com", "unrealengine.com",
            "fortnite.com", "steampowered.com", "steamcommunity.com",
            "steamstatic.com", "steamcontent.com", "ea.com", "origin.com",
            "minecraft.net", "minecraftservices.com", "mojang.com",
            "activision.com", "callofduty.com", "blizzard.com", "battle.net",
            "riotgames.com", "leagueoflegends.com", "riotcdn.net", "xbox.com",
            "xboxlive.com", "playstation.com", "playstation.net", "supercell.com",
            "king.com", "miniclip.com", "poki.com", "crazygames.com", "y8.com",
            "addictinggames.com", "kongregate.com",
        ],
    },
    "social": {
        "label": "רשתות חברתיות",
        "domains": [
            "facebook.com", "fbcdn.net", "facebook.net", "fb.com",
            "instagram.com", "cdninstagram.com", "twitter.com", "x.com",
            "twimg.com", "t.co", "snapchat.com", "sc-cdn.net", "snap.com",
            "reddit.com", "redd.it", "redditmedia.com", "redditstatic.com",
            "pinterest.com", "pinimg.com", "tumblr.com", "discord.com",
            "discordapp.com", "discord.gg", "discordapp.net", "threads.net",
            "linkedin.com", "licdn.com",
        ],
    },
    "ads": {
        "label": "פרסומות ומעקב",
        "domains": [
            "doubleclick.net", "googleadservices.com", "googlesyndication.com",
            "google-analytics.com", "googletagmanager.com", "googletagservices.com",
            "adservice.google.com", "2mdn.net", "scorecardresearch.com",
            "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "adnxs.com",
            "adsrvr.org", "rubiconproject.com", "pubmatic.com", "openx.net",
            "moatads.com", "quantserve.com", "bidswitch.net",
        ],
    },
    "gambling": {
        "label": "הימורים וקזינו",
        "domains": [
            "bet365.com", "pokerstars.com", "pokerstars.net", "888.com",
            "888casino.com", "888poker.com", "williamhill.com", "betway.com",
            "winner.com", "unibet.com", "bwin.com", "ladbrokes.com",
            "partypoker.com", "draftkings.com", "fanduel.com", "stake.com",
            "betfair.com", "casino.com",
        ],
    },
    "shopping": {
        "label": "קניות אונליין",
        "domains": [
            "amazon.com", "aliexpress.com", "ebay.com", "etsy.com", "walmart.com",
            "shein.com", "temu.com", "wish.com", "asos.com", "terminalx.com",
            "next.co.il", "ksp.co.il", "ivory.co.il", "zap.co.il",
        ],
    },
}

# The per-device policy. New devices are created from these defaults.
DEVICE_DEFAULTS = {
    "mode": "open",
    "whitelist": [],
    "blacklist_manual": [],
    "categories": [],   # enabled blocking-category keys (see CATEGORIES)
    "layers": "both",   # which layer enforces: dns | proxy | both
    # per-device daily automatic lockdown at `hour` (Israel time); the device is
    # switched to "lockdown" (full block) and stays that way until the parent opens it.
    "auto_lockdown": {"enabled": True, "hour": 23, "last_applied": ""},
}

LAYERS = ("dns", "proxy", "both")

DEFAULT_STORE = {
    "defaults": dict(DEVICE_DEFAULTS),
    "blocklists": DEFAULT_BLOCKLISTS,   # shared across all devices
    "devices": {},                      # device_id -> device record
    "uninstall_code": "",
    # remote auto-update target advertised to agents. version: the latest agent
    # version; url: where the signed installer is hosted; signer: the pinned
    # code-signing certificate thumbprint the agent verifies before running it.
    "update": {"version": "", "url": "", "signer": ""},
    "updated_at": 0,
}

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


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


def auto_lockdown_loop():
    """Every minute, check Israel local time and lock each device whose own daily
    auto-lockdown hour has arrived. Per device, once per day (idempotent via its
    last_applied). zoneinfo handles Israel DST. A locked device stays in lockdown
    until the parent opens it in the panel."""
    while True:
        try:
            st = load_store()
            now = datetime.now(ISRAEL_TZ)
            today = now.strftime("%Y-%m-%d")
            changed = 0
            for dev in st["devices"].values():
                cfg = dev.get("auto_lockdown") or {}
                if not cfg.get("enabled", True):
                    continue
                try:
                    hour = int(cfg.get("hour", 23))
                except (TypeError, ValueError):
                    hour = 23
                if now.hour >= hour and cfg.get("last_applied") != today:
                    dev["mode"] = "lockdown"
                    cfg["hour"] = hour
                    cfg["enabled"] = True
                    cfg["last_applied"] = today
                    dev["auto_lockdown"] = cfg
                    changed += 1
            if changed:
                save_store(st)
                print(f"[auto-lockdown] locked {changed} device(s)")
        except Exception as e:
            print(f"[auto-lockdown] error: {e}")
        time.sleep(60)


app = Flask(__name__)
app.secret_key = SECRET

# start the daily auto-lockdown scheduler once per process
_scheduler_started = False
if not _scheduler_started:
    _scheduler_started = True
    threading.Thread(target=auto_lockdown_loop, daemon=True).start()


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

    # expand enabled categories into the manual block list the agent enforces, so
    # no agent change is needed — categories ride along as extra blocked domains.
    manual = list(dev.get("blacklist_manual", []))
    for key in dev.get("categories", []):
        cat = CATEGORIES.get(key)
        if cat:
            manual.extend(cat["domains"])
    manual = list(dict.fromkeys(manual))  # de-dup, keep order

    upd = st.get("update", {})
    return jsonify({
        "device_id": device_id,
        "mode": dev["mode"],
        "layers": dev.get("layers", "both"),
        "whitelist": dev["whitelist"],
        "blacklist_manual": manual,
        "blocklists": st["blocklists"],
        "uninstall_code": st["uninstall_code"],
        "latest_version": upd.get("version", ""),
        "update_url": upd.get("url", ""),
        "update_signer": upd.get("signer", ""),
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


@app.post("/api/log")
def api_log():
    """Receive batched activity from the proxy addon: blocked hosts and a
    summary of visited (unique) hosts. Token-protected; keyed by device_id."""
    token = request.args.get("token") or request.headers.get("X-Agent-Token")
    if token != AGENT_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    device_id = (data.get("device_id") or "").strip()
    if not device_id or not SAFE_ID.match(device_id):
        return jsonify({"error": "bad device_id"}), 400
    st = load_store()
    dev = st["devices"].get(device_id)
    if not dev:
        return jsonify({"error": "unknown device"}), 404
    now = int(time.time())

    blocks = dev.setdefault("log_blocks", [])
    for h in (data.get("blocked") or [])[:300]:
        h = str(h).lower()[:120].strip()
        if h:
            blocks.append([now, h])
    if len(blocks) > LOG_CAP:
        del blocks[:len(blocks) - LOG_CAP]

    visited = dev.setdefault("log_visited", {})
    for h in (data.get("visited") or [])[:1000]:
        h = str(h).lower()[:120].strip()
        if not h:
            continue
        e = visited.get(h)
        if e:
            e[0], e[1] = now, e[1] + 1
        else:
            visited[h] = [now, 1]
    if len(visited) > LOG_CAP:
        oldest = sorted(visited.items(), key=lambda kv: kv[1][0])
        for h, _ in oldest[:len(visited) - LOG_CAP]:
            visited.pop(h, None)

    save_store(st)
    return jsonify({"ok": True})


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
    base = "https://" + request.host   # Fly terminates TLS; force https for POSTs
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
    base = "https://" + request.host   # Fly terminates TLS; force https for POSTs
    script = PROXY_INSTALLER_PS1.replace("__SERVER__", base).replace("__TOKEN__", token)
    return Response(script, mimetype="text/plain")


@app.post("/api/upload-installer")
def api_upload_installer():
    """Parent uploads the signed installer here (stored on the /data volume):
       curl -X POST "https://<server>/api/upload-installer?token=<TOKEN>" \\
            --data-binary @filter1-setup.exe
    Token-gated. The agent still verifies the Authenticode signature + pinned
    thumbprint before running it, so a bad upload without the key is never run."""
    token = request.args.get("token") or request.headers.get("X-Agent-Token")
    if token != AGENT_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    # stream to disk in chunks instead of buffering the whole (tens-of-MB) file in
    # memory, which can OOM a small machine and return 502.
    tmp = INSTALLER_PATH + ".tmp"
    total = 0
    with open(tmp, "wb") as f:
        while True:
            chunk = request.stream.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    if total == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return jsonify({"error": "empty body"}), 400
    os.replace(tmp, INSTALLER_PATH)
    return jsonify({"ok": True, "bytes": total})


@app.get("/download/filter1-setup.exe")
def download_installer():
    """Serve the uploaded installer to agents. Token-gated because the installer
    embeds the agent token. Point the panel's update URL at this with ?token=."""
    if request.args.get("token") != AGENT_TOKEN:
        return "unauthorized", 401
    if not os.path.exists(INSTALLER_PATH):
        return "installer not uploaded yet", 404
    return send_file(INSTALLER_PATH, mimetype="application/octet-stream",
                     as_attachment=True, download_name="filter1-setup.exe")


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
        uninstall_code=st["uninstall_code"], update=st.get("update", {}),
        agent_version=APP_VERSION)


@app.get("/device/<device_id>")
@login_required
def device(device_id):
    st = load_store()
    d = st["devices"].get(device_id)
    if not d:
        return redirect(url_for("index"))
    return render_template_string(
        DEVICE_HTML, did=device_id, d=d, modes=MODES, labels=MODE_LABELS,
        version=APP_VERSION, layers=d.get("layers", "both"),
        wl="\n".join(d.get("whitelist", [])),
        bl="\n".join(d.get("blacklist_manual", [])),
        blocklists="\n".join(st["blocklists"]),
        categories=CATEGORIES, enabled=set(d.get("categories", [])),
        auto_lockdown=(d.get("auto_lockdown") or DEFAULT_STORE["auto_lockdown"]),
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
    layer = request.form.get("layers")
    if layer in LAYERS:
        d["layers"] = layer
    d["whitelist"] = clean_domains(request.form.get("whitelist"))
    d["blacklist_manual"] = clean_domains(request.form.get("blacklist_manual"))
    d["categories"] = [k for k in CATEGORIES
                       if request.form.get("cat_" + k) == "on"]
    cur_al = d.get("auto_lockdown") or dict(DEFAULT_STORE["auto_lockdown"])
    try:
        al_hour = max(0, min(23, int(request.form.get("auto_lockdown_hour",
                                                      cur_al.get("hour", 23)))))
    except (TypeError, ValueError):
        al_hour = cur_al.get("hour", 23)
    d["auto_lockdown"] = {
        "enabled": request.form.get("auto_lockdown_enabled") == "on",
        "hour": al_hour,
        "last_applied": "",   # reset so a change can take effect today
    }
    # blocklists are shared across devices
    st["blocklists"] = [l.strip() for l in
                        (request.form.get("blocklists") or "").splitlines()
                        if l.strip() and not l.strip().startswith("#")]
    save_store(st)
    return redirect(url_for("device", device_id=device_id))


@app.get("/device/<device_id>/log")
@login_required
def device_log(device_id):
    st = load_store()
    d = st["devices"].get(device_id)
    if not d:
        return redirect(url_for("index"))
    now = int(time.time())
    blocks = [{"host": h, "ago": human_ago(now - ts)}
              for ts, h in reversed(d.get("log_blocks", []))]
    visited = sorted(d.get("log_visited", {}).items(),
                     key=lambda kv: kv[1][0], reverse=True)
    visited = [{"host": h, "count": v[1], "ago": human_ago(now - v[0])}
               for h, v in visited]
    return render_template_string(
        LOG_HTML, did=device_id, name=d.get("name") or device_id,
        blocks=blocks, visited=visited, version=APP_VERSION)


@app.post("/device/<device_id>/log/clear")
@login_required
def device_log_clear(device_id):
    st = load_store()
    d = st["devices"].get(device_id)
    if d:
        d["log_blocks"] = []
        d["log_visited"] = {}
        save_store(st)
    return redirect(url_for("device_log", device_id=device_id))


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


@app.post("/update-target")
@login_required
def update_target():
    """Set the remote auto-update target that agents poll. Empty version or url
    disables auto-update. signer is the pinned code-signing cert thumbprint the
    agent verifies before running the installer."""
    st = load_store()
    st["update"] = {
        "version": (request.form.get("version") or "").strip(),
        "url": (request.form.get("url") or "").strip(),
        "signer": (request.form.get("signer") or "").strip().replace(":", "").replace(" ", ""),
    }
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
 .cat{display:block;margin:6px 0;font-size:15px}
 .cat input{margin-left:8px;transform:scale(1.2)}
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
 <div class="box">
  <h3>עדכון אוטומטי מרחוק</h3>
  <p style="color:#94a3b8;font-size:13px">
   הסוכנים בודקים גרסה בכל דקה. אם הגרסה כאן חדשה יותר, הם מורידים את המתקין,
   מאמתים את חתימתו מול טביעת האצבע, ומעדכנים בשקט. השאר ריק כדי לכבות.
   ללא טביעת אצבע העדכון לא ירוץ (הגנה).</p>
  <form method="post" action="/update-target" style="margin-top:8px">
   <input class="txt" name="version" value="{{update.get('version','')}}"
     placeholder="גרסה אחרונה, למשל 1.1.8" style="margin-bottom:8px">
   <input class="txt" name="url" value="{{update.get('url','')}}"
     placeholder="כתובת המתקין החתום (https://.../filter1-setup.exe)" style="margin-bottom:8px">
   <input class="txt" name="signer" value="{{update.get('signer','')}}"
     placeholder="טביעת אצבע של תעודת החתימה (Thumbprint)" style="margin-bottom:8px">
   <button type="submit" class="gray">שמור יעד עדכון</button></form>
 </div>
 <div class="v">גרסה {{version}}</div>
</div></body></html>"""

DEVICE_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — {{d.name}}</title><style>__CSS__</style></head><body><div class="wrap">
 <div class="bar"><h1>🖥️ {{d.name}}</h1>
  <span><a href="/device/{{did}}/log">📄 יומן פעילות</a> · <a href="/">← כל המחשבים</a></span></div>
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
   <h3>שכבת אכיפה</h3>
   <p>איך המחשב חוסם: DNS מהיר, פרוקסי לפי תוכן (גם מול DoH), או שניהם. הפרוקסי חייב להיות מותקן.</p>
   <select name="layers" class="txt">
    <option value="both" {{'selected' if layers=='both' else ''}}>שניהם — DNS + פרוקסי</option>
    <option value="dns" {{'selected' if layers=='dns' else ''}}>DNS בלבד</option>
    <option value="proxy" {{'selected' if layers=='proxy' else ''}}>פרוקסי בלבד</option>
   </select>
  </div>
  <div class="field">
   <h3>רשימה לבנה (מותר)</h3>
   <p>דומיין בכל שורה. תמיד מותר במצב "חסימה מלאה" ו"רשימה לבנה".</p>
   <textarea name="whitelist" placeholder="example.com">{{wl}}</textarea>
  </div>
  <div class="field">
   <h3>קטגוריות חסימה</h3>
   <p>סמן קטגוריות לחסימה, בנוסף לרשימות הציבוריות. חל במצב "רשימה שחורה".</p>
   {% for key, c in categories.items() %}
   <label class="cat">
    <input type="checkbox" name="cat_{{key}}" {{'checked' if key in enabled else ''}}>
    {{c.label}}
   </label>
   {% endfor %}
  </div>
  <div class="field">
   <h3>חסימה אוטומטית יומית</h3>
   <p>בשעה שנקבעת (שעון ישראל) המחשב הזה עובר אוטומטית ל"חסימה מלאה", ונשאר כך
      עד שתפתח אותו ידנית.</p>
   <label class="cat">
    <input type="checkbox" name="auto_lockdown_enabled" {{'checked' if auto_lockdown.get('enabled') else ''}}>
    הפעל למחשב זה
   </label>
   <label>שעה (0–23):
    <input class="txt" name="auto_lockdown_hour" value="{{auto_lockdown.get('hour', 23)}}"
      style="width:80px;display:inline-block" inputmode="numeric">
   </label>
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

LOG_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — יומן {{name}}</title><style>__CSS__
 table{width:100%;border-collapse:collapse;margin:8px 0}
 th,td{text-align:right;padding:8px 10px;border-bottom:1px solid #26324a;font-size:14px}
 th{color:#94a3b8;font-weight:600}
 td.host{font-family:monospace;direction:ltr;text-align:left}
 .cnt{color:#94a3b8}
 .tag{color:#f87171}
 .cols{display:grid;grid-template-columns:1fr;gap:20px}
 .scroll{max-height:60vh;overflow:auto;border:1px solid #26324a;border-radius:10px}
</style></head><body><div class="wrap">
 <div class="bar"><h1>📄 יומן — {{name}}</h1>
  <span><a href="/device/{{did}}">← חזרה למחשב</a></span></div>
 <div class="cols">
  <div>
   <h3>🛡️ אתרים שנחסמו ({{blocks|length}})</h3>
   {% if not blocks %}<p class="meta">אין חסימות עדיין.</p>{% else %}
   <div class="scroll"><table><tr><th>אתר</th><th>מתי</th></tr>
    {% for b in blocks %}<tr><td class="host tag">{{b.host}}</td><td>{{b.ago}}</td></tr>{% endfor %}
   </table></div>{% endif %}
  </div>
  <div>
   <h3>🌐 אתרים שנגלשו — תמצית ({{visited|length}})</h3>
   {% if not visited %}<p class="meta">אין עדיין.</p>{% else %}
   <div class="scroll"><table><tr><th>אתר</th><th>פעמים</th><th>לאחרונה</th></tr>
    {% for v in visited %}<tr><td class="host">{{v.host}}</td><td class="cnt">{{v.count}}</td><td>{{v.ago}}</td></tr>{% endfor %}
   </table></div>{% endif %}
  </div>
 </div>
 <form method="post" action="/device/{{did}}/log/clear" style="margin-top:16px"
       onsubmit="return confirm('לנקות את היומן?')">
  <button type="submit" class="gray">נקה יומן</button></form>
 <div class="v">גרסה {{version}} · היומן מתעדכן ממחשבים במצב פרוקסי</div>
</div></body></html>"""

# Inject the shared CSS. Using str.replace (not %-formatting) so the templates
# can contain Jinja {% ... %} blocks and CSS % units without conflict.
LOGIN_HTML = LOGIN_HTML.replace("__CSS__", CSS)
INDEX_HTML = INDEX_HTML.replace("__CSS__", CSS)
DEVICE_HTML = DEVICE_HTML.replace("__CSS__", CSS)
LOG_HTML = LOG_HTML.replace("__CSS__", CSS)


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
  # use the interpreter's build tag ((AMD64)/(ARM64)); platform.machine() is
  # unreliable on ARM64 Windows, where emulated x64 still reports ARM64
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
# stop any previous agent so the new code takes over port 53
Stop-ScheduledTask -TaskName "filter1" -ErrorAction SilentlyContinue | Out-Null
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*filter1\agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
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

# create a registry key only if missing (New-Item -Force fails on existing keys)
function NK($p) { if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null } }

function Get-PyArch($py) {
  # use the interpreter's build tag ((AMD64)/(ARM64)); platform.machine() is
  # unreliable on ARM64 Windows, where emulated x64 still reports ARM64
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
NK "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates"
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates" -Name ImportEnterpriseRoots -Value 1 -Type DWord

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$addon = Join-Path $InstallDir "filter_addon.py"
Invoke-WebRequest -Uri "$Server/filter_addon.py" -OutFile $addon

# route browsers via BROWSER POLICY only (below); the system-wide WinINET proxy
# makes Windows block app launches during its security-zone checks.
New-NetFirewallRule -DisplayName "filter1 block QUIC" -Group "filter1" -Direction Outbound -Action Block -Protocol UDP -RemotePort 443 -ErrorAction SilentlyContinue | Out-Null
foreach ($b in @("HKLM:\SOFTWARE\Policies\Google\Chrome","HKLM:\SOFTWARE\Policies\Microsoft\Edge")) {
  NK $b
  Set-ItemProperty -Path $b -Name QuicAllowed -Value 0 -Type DWord
  # force the browser through the local proxy via policy (reliable, machine-wide)
  Set-ItemProperty -Path $b -Name ProxyMode -Value "fixed_servers"
  Set-ItemProperty -Path $b -Name ProxyServer -Value "127.0.0.1:8080"
}

# stop any previous proxy so the new one takes over port 8080
Stop-ScheduledTask -TaskName "filter1-proxy" -ErrorAction SilentlyContinue | Out-Null
Get-Process mitmdump -ErrorAction SilentlyContinue | Stop-Process -Force
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
