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

from flask import (Flask, jsonify, redirect, render_template_string, request,
                   session, url_for)

APP_VERSION = "0.2.0"

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
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/hosts/pro.txt",
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
        devices.append({
            "id": did,
            "name": d.get("name") or did,
            "hostname": d.get("hostname", ""),
            "mode": d.get("mode", "open"),
            "mode_label": MODE_LABELS.get(d.get("mode", "open"))[0],
            "online": (now - last) <= ONLINE_WINDOW,
            "last_seen": last,
            "ago": human_ago(now - last) if last else "מעולם לא",
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
<title>filter1 — כניסה</title><style>%s
 body{display:flex;min-height:100vh;align-items:center;justify-content:center}
 .card{background:#1e293b;padding:32px;border-radius:16px;width:320px}
 .card input{width:100%%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid #334155;
   background:#0f172a;color:#e2e8f0;box-sizing:border-box}
 .card button{width:100%%}.err{color:#f87171;font-size:14px;min-height:18px}
</style></head><body>
<form class="card" method="post">
 <h1>🛡️ filter1 — בקרת הורים</h1>
 <div class="err">{{error}}</div>
 <input type="password" name="password" placeholder="סיסמת הורה" autofocus>
 <button type="submit">כניסה</button>
 <div class="v">גרסה {{version}}</div>
</form></body></html>""" % CSS

INDEX_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — מחשבים</title><style>%s</style></head><body><div class="wrap">
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
   <div class="meta">{{dv.hostname}} · {{'מחובר' if dv.online else dv.ago}} · מזהה {{dv.id}}</div>
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
</div></body></html>""" % CSS

DEVICE_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — {{d.name}}</title><style>%s</style></head><body><div class="wrap">
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
</div></body></html>""" % CSS


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    app.run(host="0.0.0.0", port=port, debug=True)
