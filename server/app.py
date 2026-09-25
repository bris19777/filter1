#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""filter1 control server.

Stores the current parental-control mode and the domain lists, exposes a Hebrew
RTL web UI for the parent, and a token-protected JSON API that the on-device
agent polls. Single-file Flask app with a JSON store on disk, in the same spirit
as the bsd-invoices project.
"""

import json
import os
import secrets
import time
from functools import wraps

from flask import (Flask, jsonify, redirect, render_template_string, request,
                   session, url_for)

APP_VERSION = "0.1.0"

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FILTER1_DATA", os.path.join(BASE, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

# Secrets. In production set these as environment variables / platform secrets.
PARENT_PASSWORD = os.environ.get("PARENT_PASSWORD", "changeme")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "DEV")
SECRET = os.environ.get("SECRET", "dev-secret-change-me")

MODES = ("open", "lockdown", "blacklist", "whitelist")

# Public blocklists the agent will download when in blacklist mode. These are
# widely used, community-maintained hosts lists.
DEFAULT_BLOCKLISTS = [
    "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn/hosts",
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/hosts/pro.txt",
]

DEFAULT_CONFIG = {
    "mode": "open",
    "whitelist": [],          # domains always allowed / the only ones allowed
    "blacklist_manual": [],   # extra domains to block on top of public lists
    "blocklists": DEFAULT_BLOCKLISTS,
    "updated_at": 0,
    "uninstall_code": "",     # one-time code that authorises agent uninstall
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # backfill any missing keys
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    cfg["updated_at"] = int(time.time())
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
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
    """Parse a textarea of domains, one per line, into a clean list."""
    out = []
    for line in (text or "").splitlines():
        d = line.strip().lower()
        if not d or d.startswith("#"):
            continue
        # strip scheme / path if the user pasted a full URL
        d = d.replace("https://", "").replace("http://", "").split("/")[0]
        if d and d not in out:
            out.append(d)
    return out


# ---------------------------------------------------------------- agent API

@app.get("/api/config")
def api_config():
    """Polled by the on-device agent. Protected by a shared token."""
    token = request.args.get("token") or request.headers.get("X-Agent-Token")
    if token != AGENT_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    return jsonify({
        "mode": cfg["mode"],
        "whitelist": cfg["whitelist"],
        "blacklist_manual": cfg["blacklist_manual"],
        "blocklists": cfg["blocklists"],
        "updated_at": cfg["updated_at"],
        "uninstall_code": cfg["uninstall_code"],
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


@app.get("/")
@login_required
def index():
    cfg = load_config()
    return render_template_string(
        INDEX_HTML,
        cfg=cfg,
        modes=MODES,
        version=APP_VERSION,
        wl="\n".join(cfg["whitelist"]),
        bl="\n".join(cfg["blacklist_manual"]),
        blocklists="\n".join(cfg["blocklists"]),
    )


@app.post("/save")
@login_required
def save():
    cfg = load_config()
    mode = request.form.get("mode", "open")
    if mode in MODES:
        cfg["mode"] = mode
    cfg["whitelist"] = clean_domains(request.form.get("whitelist"))
    cfg["blacklist_manual"] = clean_domains(request.form.get("blacklist_manual"))
    cfg["blocklists"] = [l.strip() for l in
                         (request.form.get("blocklists") or "").splitlines()
                         if l.strip() and not l.strip().startswith("#")]
    save_config(cfg)
    return redirect(url_for("index"))


@app.post("/uninstall-code")
@login_required
def uninstall_code():
    """Generate a one-time code that authorises the agent to uninstall."""
    cfg = load_config()
    cfg["uninstall_code"] = secrets.token_hex(4).upper()
    save_config(cfg)
    return redirect(url_for("index"))


LOGIN_HTML = """<!doctype html><html dir="rtl" lang="he"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>filter1 — כניסה</title>
<style>
 body{font-family:system-ui,Arial;background:#0f172a;color:#e2e8f0;display:flex;
   min-height:100vh;align-items:center;justify-content:center;margin:0}
 .card{background:#1e293b;padding:32px;border-radius:16px;width:320px;box-shadow:0 10px 40px #0006}
 h1{margin:0 0 16px;font-size:20px}
 input{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid #334155;
   background:#0f172a;color:#e2e8f0;box-sizing:border-box}
 button{width:100%;padding:12px;border:0;border-radius:8px;background:#3b82f6;color:#fff;
   font-weight:600;cursor:pointer;font-size:15px}
 .err{color:#f87171;font-size:14px;min-height:18px}
 .v{color:#64748b;font-size:12px;text-align:center;margin-top:12px}
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
<title>filter1 — לוח בקרה</title>
<style>
 body{font-family:system-ui,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}
 .wrap{max-width:820px;margin:0 auto}
 h1{font-size:22px}
 .modes{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}
 label.mode{display:block;padding:16px;border-radius:12px;border:2px solid #334155;
   background:#1e293b;cursor:pointer}
 label.mode.sel{border-color:#3b82f6;background:#1e3a5f}
 label.mode input{margin-left:8px}
 .mode b{font-size:16px}
 .mode small{display:block;color:#94a3b8;margin-top:4px}
 textarea{width:100%;height:140px;padding:10px;border-radius:8px;border:1px solid #334155;
   background:#0f172a;color:#e2e8f0;box-sizing:border-box;font-family:monospace;direction:ltr;text-align:left}
 .field{margin:16px 0}
 .field h3{margin:0 0 6px;font-size:15px}
 .field p{margin:0 0 8px;color:#94a3b8;font-size:13px}
 button{padding:12px 24px;border:0;border-radius:8px;background:#3b82f6;color:#fff;
   font-weight:600;cursor:pointer;font-size:15px}
 .bar{display:flex;justify-content:space-between;align-items:center}
 .bar a{color:#94a3b8;font-size:13px}
 .box{background:#1e293b;padding:16px;border-radius:12px;margin:16px 0}
 .code{font-family:monospace;font-size:20px;color:#fbbf24}
 .v{color:#64748b;font-size:12px;margin-top:24px;text-align:center}
</style></head><body><div class="wrap">
 <div class="bar"><h1>🛡️ filter1 — לוח בקרה</h1><a href="/logout">יציאה</a></div>
 <form method="post" action="/save">
  <div class="modes">
   {% set labels = {'open':['הכל מותר','גלישה חופשית ללא סינון'],
     'lockdown':['חסימה מלאה','רק הרשימה הלבנה מותרת, כל השאר חסום'],
     'blacklist':['רשימה שחורה','חוסם רשימות ציבוריות של אתרים בעייתיים'],
     'whitelist':['רשימה לבנה','רק אתרים שאישרת מותרים']} %}
   {% for m in modes %}
   <label class="mode {{'sel' if cfg.mode==m else ''}}">
    <input type="radio" name="mode" value="{{m}}" {{'checked' if cfg.mode==m else ''}}>
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
   <h3>מקורות רשימות ציבוריות</h3>
   <p>כתובות של קובצי hosts ציבוריים. הסוכן מוריד ומעדכן אותן במצב "רשימה שחורה".</p>
   <textarea name="blocklists">{{blocklists}}</textarea>
  </div>
  <button type="submit">שמור והחל מרחוק</button>
 </form>

 <div class="box">
  <h3>קוד הסרה חד-פעמי</h3>
  <p style="color:#94a3b8;font-size:13px">כדי להסיר את הסוכן מהמחשב צריך קוד זה.</p>
  {% if cfg.uninstall_code %}<div class="code">{{cfg.uninstall_code}}</div>{% endif %}
  <form method="post" action="/uninstall-code" style="margin-top:8px">
   <button type="submit" style="background:#64748b">הפק קוד הסרה חדש</button>
  </form>
 </div>

 <div class="v">גרסה {{version}} · עודכן לאחרונה: {{cfg.updated_at}}</div>
</div></body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5060))
    app.run(host="0.0.0.0", port=port, debug=True)
