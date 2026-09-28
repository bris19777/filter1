#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""filter1 local status / diagnostics.

Prints a plain-language report (Hebrew) of what is and isn't working on this PC,
and why, so a parent can tell e.g. that the mitmproxy certificate is installed
but the device never registered because the agent can't reach the control server.

Stdlib only, so it runs on the bundled Python. No admin needed (all checks read
state). Double-click filter1-status.bat, or run:  py\\python.exe status.py
"""

import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

IS_WINDOWS = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "filter1") \
    if IS_WINDOWS else os.path.join(os.path.expanduser("~"), ".filter1")
CONF = os.path.join(DATA, "mitmproxy")

OK, BAD, WARN = "[ תקין ]", "[ תקלה ]", "[ אזהרה ]"


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def find_cfg():
    """filter1.cfg sits next to the install; search here, then the usual dirs."""
    dirs = [HERE, DATA]
    if IS_WINDOWS:
        for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                   os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            dirs.append(os.path.join(pf, "filter1"))
    for d in dirs:
        p = os.path.join(d, "filter1.cfg")
        if os.path.exists(p):
            return p
    return None


def parse_servers(raw):
    out = []
    for p in re.split(r"[,;\s]+", (raw or "").strip()):
        p = p.strip().strip("<>\"' \t\r\n").rstrip("/")
        if p and p not in out:
            out.append(p)
    return out


def read_cfg():
    servers, token = [], ""
    p = find_cfg()
    if p:
        try:
            with open(p, encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k, v = k.strip().lower(), v.strip().strip("<>\"' ")
                        if k == "server":
                            servers = parse_servers(v)
                        elif k == "token":
                            token = v
        except Exception:
            pass
    return servers, token, p


def read_device_id():
    p = os.path.join(DATA, "device_id")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def read_status_json():
    p = os.path.join(DATA, "status.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - os.path.getmtime(p)
        return data, age
    except Exception:
        return None, None


def check_server(servers, token, device_id):
    """Live re-check: does any control server answer the config request? A 200
    means this device is registered/known to that server. Returns
    (ok, working_url, detail) — trying each server so a blocked primary can fail
    over to a whitelisted alternate."""
    if not servers or not token:
        return False, "", "אין server/token בקובץ ההגדרות"
    last = ""
    for server in servers:
        q = urllib.parse.urlencode({"token": token,
                                    "device_id": device_id or "status",
                                    "name": socket.gethostname()})
        url = f"{server.rstrip('/')}/api/config?{q}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "filter1-status"})
            with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
                cfg = json.loads(r.read().decode("utf-8"))
            return True, server, f"מצב מהשרת: {cfg.get('mode', '?')}"
        except Exception as e:
            last = f"{server}: {e}"
    return False, "", last


def ca_file():
    for n in ("mitmproxy-ca-cert.cer", "mitmproxy-ca-cert.pem"):
        p = os.path.join(CONF, n)
        if os.path.exists(p):
            return p
    return None


def ca_trusted():
    if not IS_WINDOWS:
        return None
    try:
        out = subprocess.run(["certutil", "-store", "Root"],
                             capture_output=True, text=True, timeout=30,
                             creationflags=0x08000000).stdout
        return "mitmproxy" in out.lower()
    except Exception:
        return None


def browser_proxy():
    if not IS_WINDOWS:
        return {}
    import winreg
    res = {}
    for name, base in (("Chrome", r"SOFTWARE\Policies\Google\Chrome"),
                       ("Edge", r"SOFTWARE\Policies\Microsoft\Edge")):
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                                 winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            try:
                res[name] = winreg.QueryValueEx(key, "ProxyServer")[0]
            except FileNotFoundError:
                res[name] = None
            finally:
                winreg.CloseKey(key)
        except FileNotFoundError:
            res[name] = None
        except Exception:
            res[name] = None
    return res


def tail(path, n=6):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-n:]).rstrip()
    except Exception:
        return ""


def build_report():
    lines = []
    def add(s=""):
        lines.append(s)

    servers, token, cfg_path = read_cfg()
    device_id = read_device_id()
    st, age = read_status_json()
    # if the cfg has no servers, fall back to what the agent recorded
    if not servers and st:
        servers = st.get("servers") or ([st.get("server")] if st.get("server") else [])

    add("=" * 56)
    add("            filter1 — מצב מקומי ואבחון")
    add(time.strftime("            %Y-%m-%d %H:%M:%S"))
    add("=" * 56)
    add()

    # --- config ---
    if servers and token:
        add(f"{OK} הגדרות נמצאו: {cfg_path or '(מתוך status.json)'}")
        if len(servers) == 1:
            add(f"       שרת: {servers[0]}")
        else:
            add(f"       שרתים (לפי סדר ניסיון): {', '.join(servers)}")
        add(f"       מזהה מחשב: {device_id or '(עדיין לא נוצר)'}")
    else:
        add(f"{BAD} קובץ ההגדרות (filter1.cfg) חסר או ללא server/token")
    add()

    # --- agent process (via status.json freshness) ---
    if st is None:
        add(f"{BAD} הסוכן לא כתב מצב (status.json חסר) — כנראה אינו רץ")
        add("       בדוק את המשימה 'filter1' ב-Task Scheduler ואת selftest.log")
    elif age is not None and age > 180:
        add(f"{WARN} הסוכן לא עודכן {int(age)} שניות — ייתכן שנעצר")
    else:
        add(f"{OK} הסוכן פעיל (עדכון אחרון לפני {int(age or 0)} שניות)")
    add()

    # --- server registration (live) ---
    reachable, working, detail = check_server(servers, token, device_id)
    if reachable:
        add(f"{OK} רישום בשרת: המכשיר מוכר לשרת ({detail})")
        if len(servers) > 1:
            add(f"       שרת פעיל: {working}")
    else:
        add(f"{BAD} רישום בשרת נכשל — לכן המחשב לא מופיע בלוח הבקרה")
        add(f"       סיבה: {detail}")
        add("       בדוק חיבור אינטרנט, כתובת השרת, והטוקן")
        add("       אם רשת מסננת (כמו רימון) חוסמת את הדומיין — בקש לאשר אותו,")
        add("       או הוסף כתובת שרת חלופית מאושרת ל-filter1.cfg (מופרדת בפסיק)")
    if st and st.get("last_error"):
        add(f"       שגיאת poll אחרונה של הסוכן: {st['last_error']}")
    add()

    # --- DNS layer ---
    v4 = port_open("127.0.0.1", 53)
    add(f"{OK if v4 else BAD} שרת DNS מקומי על 127.0.0.1:53 "
        f"{'מאזין' if v4 else 'לא מאזין (ייתכן שפורט 53 תפוס)'}")
    if st is not None:
        add(f"       IPv6 (::1): {'מאזין' if st.get('dns_listen_ipv6') else 'כבוי/לא זמין'}"
            f"   |   שולט על DNS המערכת: {'כן' if st.get('dns_taken_over') else 'לא (fail-open)'}")
        ups = st.get("upstreams") or []
        if ups:
            add(f"       שרתי DNS במעלה: {', '.join(ups)}")
    add()

    # --- proxy layer ---
    p8080 = port_open("127.0.0.1", 8080)
    add(f"{OK if p8080 else BAD} פרוקסי על 127.0.0.1:8080 "
        f"{'מאזין' if p8080 else 'לא מאזין'}")
    ca = ca_file()
    add(f"{OK if ca else BAD} תעודת mitmproxy: {'קיימת' if ca else 'חסרה'}"
        + (f" ({ca})" if ca else ""))
    trusted = ca_trusted()
    if trusted is True:
        add(f"{OK} התעודה מותקנת ב-Root של המחשב")
    elif trusted is False:
        add(f"{BAD} התעודה לא מותקנת ב-Root — HTTPS יישבר אם הדפדפן מנותב לפרוקסי")
    else:
        add(f"{WARN} לא ניתן לבדוק את התקנת התעודה")
    bp = browser_proxy()
    for name, val in bp.items():
        if val:
            add(f"{OK} {name} מנותב לפרוקסי: {val}")
        else:
            add(f"{WARN} {name} לא מנותב לפרוקסי (סינון HTTPS לא פעיל לדפדפן זה)")
    add()

    # --- summary hint ---
    add("-" * 56)
    if ca and not reachable:
        add("סיכום: התעודה מותקנת אך אין רישום בשרת. הבעיה היא בקשר לשרת")
        add("        (אינטרנט/כתובת/טוקן), לא בפרוקסי. ראה 'רישום בשרת' למעלה.")
    elif reachable and v4:
        add("סיכום: הרכיבים המרכזיים תקינים.")
    else:
        add("סיכום: ראה את השורות המסומנות [ תקלה ] למעלה.")
    add()

    # --- recent logs ---
    add("-" * 56)
    add("שורות אחרונות מהיומנים:")
    for label, fn in (("selftest.log", os.path.join(DATA, "selftest.log")),
                      ("run_proxy.log", os.path.join(DATA, "run_proxy.log")),
                      ("proxy.log", os.path.join(DATA, "proxy.log"))):
        t = tail(fn)
        if t:
            add(f"  --- {label} ---")
            for l in t.splitlines():
                add(f"    {l}")
    return "\n".join(lines)


def main():
    report = build_report()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(report)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "status.txt"), "w", encoding="utf-8") as f:
            f.write(report + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
