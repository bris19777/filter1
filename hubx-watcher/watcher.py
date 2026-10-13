"""HUBX deal watcher.

Logs in to the HUBX buyer portal, pulls the laptop and desktop catalogs,
works out what each computer costs landed in Israel (price + shipping + VAT),
and emails the computers that fit the profit target and are new on the site
or got cheaper since the last check.

Standard library only. Run once (cron) or with --loop (Docker / fly.io).
"""

import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

AUTH_URL = "https://authserver.hubx.com/connect/token"
CLIENT_ID = "186beaeb514347db9b56029c6601bd83"
REALM = "portal-prod"
ITEMS_URL = "https://buyer.hubx.com/api/items/values"
ITEM_LINK = "https://hubx.com/buyer/items/{}"
BOI_RATE_URL = "https://boi.org.il/PublicApi/GetExchangeRate?key=USD&asJson=true"

# Saved-filter ids from the portal's category navigation.
FILTERS = {
    "laptops": "98fe2b03-e341-4662-a9a5-5550420332d3",
    "desktops": "2b157537-8b53-499b-8ef3-55a5f3a0d7da",
    "todays_deals": "19dc4e71-4e14-408f-953e-ba05fe986b6c",
    "price_drop": "0233d36b-3cc9-426c-988b-9ef26cd92ac3",
}

BROWSER_HEADERS = {
    "origin": "https://hubx.com",
    "referer": "https://hubx.com/",
    "user-agent": "Mozilla/5.0 (hubx-watcher)",
}


def env(name, default=None, cast=str):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return cast(value)


def http(url, data=None, headers=None, method=None, timeout=90):
    base = BROWSER_HEADERS if "hubx.com" in url else {"user-agent": BROWSER_HEADERS["user-agent"]}
    req = urllib.request.Request(url, data=data, headers={**base, **(headers or {})}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# --- HUBX -------------------------------------------------------------------

def login(user, password):
    form = urllib.parse.urlencode({
        "audience": "undefined",
        "client_id": CLIENT_ID,
        "username": f"{REALM}\\{user}",
        "grant_type": "password",
        "password": password,
    }).encode()
    body = http(AUTH_URL, form, {"content-type": "application/x-www-form-urlencoded"}, "POST")
    return body["access_token"]


def fetch_filter(token, filter_id):
    payload = json.dumps({
        "savedFilter": filter_id, "categoryId": None, "attributes": [],
        "pagination": {"pageSize": 9999, "pageNumber": 1}, "searchQuery": None,
        "orderBy": None, "exwPoint": None, "inStock": False, "price": None, "sourcePage": 0,
    }).encode()
    body = http(ITEMS_URL, payload, {"content-type": "application/json", "authorization": f"Bearer {token}"}, "POST")
    return body["values"]


def usd_ils_rate(fallback):
    try:
        return float(http(BOI_RATE_URL, timeout=30)["currentExchangeRate"])
    except Exception as exc:  # noqa: BLE001 - rate is best effort
        print(f"exchange rate lookup failed ({exc}); using {fallback}", file=sys.stderr)
        return fallback


# --- spec parsing -------------------------------------------------------------

LOW_END = re.compile(r"Celeron|Pentium|Athlon|MediaTek|Snapdragon|\bN\d{3}\b|\bN\d{4}\b|\bA4-|\bA6-|J\d{4}", re.I)


def parse_specs(desc):
    s = desc.replace("™", "").replace("®", "")
    ram = ssd = 0
    for num, unit, kind in re.findall(r"(\d+)\s*(GB|TB)\s*(SSD|eMMC|HDD|UFS)?", s):
        size = int(num) * (1024 if unit == "TB" else 1)
        if kind and not ssd:
            ssd = size if kind == "SSD" else 0
        elif not kind and not ram and size <= 128:
            ram = size

    tier, gen = "", 0
    m = re.search(r"\bi([3579])[- ]?(\d{4,5})([A-Z0-9]*)", s, re.I)
    if m:
        tier, num = "i" + m.group(1), m.group(2)
        gen = int(num[:2]) if len(num) == 5 or num[0] == "1" else int(num[0])
    m = re.search(r"Core\s*(Ultra\s*)?([3579])\s*\d{3}", s)
    if not tier and m:
        tier, gen = "i" + m.group(2), 14 if m.group(1) else 13
    m = re.search(r"Ryzen\s*([3579])\s*(?:PRO\s*)?(\d)\d(\d)\d", s, re.I)
    if m:
        # Rough Intel-generation equivalent. In the 7000 series the third digit is
        # the architecture: 7x20 is Zen 2, 7x30 Zen 3, 7x40/7x45 Zen 4.
        tier, series, arch = "r" + m.group(1), int(m.group(2)), int(m.group(3))
        gen = {3: 9, 4: 10, 5: 11, 6: 12, 7: {2: 10, 3: 11}.get(arch, 13), 8: 13, 9: 14}.get(series, 8)
    if LOW_END.search(s):
        tier, gen = "low", 0

    return {
        "ram": ram,
        "ssd": ssd,
        "tier": tier,
        "gen": gen,
        "touch": bool(re.search(r"touch", s, re.I)) and "Touch Bar" not in s,
        "chromebook": bool(re.search(r"chrome", s, re.I)),
        "us_only": bool(re.search(r"US Sales Only", s, re.I)),
        "graded": bool(re.search(r"GRADE [BC]|[BC]-GRADE|SCRATCH|DAMAGED", s, re.I)),
    }


# --- rules ----------------------------------------------------------------------

def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def landed_cost(price_usd, cfg, rate):
    """Cost in ILS including shipping and VAT."""
    return round((price_usd + cfg["shipping_usd"]) * rate * (1 + cfg["vat"]))


def matches(item, rule, cost):
    specs = item["specs"]
    keywords = rule.get("keywords")
    if keywords and not any(k.lower() in item["desc"].lower() for k in keywords):
        return False
    if rule.get("category") and item["category"] != rule["category"]:
        return False
    if cost > rule["sale_price_ils"] - rule["min_profit_ils"]:
        return False
    if specs["chromebook"] and not rule.get("allow_chromebook"):
        return False
    if specs["tier"] in ("", "low"):
        return False
    if specs["tier"] in ("i3", "r3") and not rule.get("allow_i3"):
        return False
    if specs["ram"] < rule.get("min_ram_gb", 8):
        return False
    if specs["gen"] < rule.get("min_cpu_gen", 10):
        return False
    if rule.get("require_touch") and not specs["touch"]:
        return False
    if specs["us_only"] and not rule.get("allow_us_only"):
        return False
    if specs["graded"] and not rule.get("allow_graded", True):
        return False
    if item["available"] < rule.get("min_stock", 1):
        return False
    return True


# --- state ----------------------------------------------------------------------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"items": {}, "alerted": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


# --- email ----------------------------------------------------------------------

def render_email(sections, rate, cfg):
    rows_html = []
    for title, rows in sections:
        if not rows:
            continue
        rows_html.append(f'<h3 style="margin:24px 0 8px">{html.escape(title)} ({len(rows)})</h3>')
        rows_html.append(
            '<table cellpadding="6" style="border-collapse:collapse;font-size:14px;width:100%">'
            '<tr style="background:#f0f0f0;text-align:right">'
            "<th>דגם</th><th>מחיר</th><th>עלות בארץ</th><th>רווח</th><th>מלאי</th><th>הערה</th></tr>"
        )
        for r in rows:
            link = ITEM_LINK.format(urllib.parse.quote(r["mpn"]))
            rows_html.append(
                '<tr style="border-bottom:1px solid #ddd">'
                f'<td dir="ltr" style="text-align:left"><a href="{link}">{html.escape(r["desc"])}</a>'
                f'<br><small style="color:#777">{html.escape(r["condition"])} · {html.escape(r["mpn"])}</small></td>'
                f'<td dir="ltr">${r["price"]:.0f}</td>'
                f'<td>{r["cost"]:,} ₪</td>'
                f'<td><b>{r["profit"]:,} ₪</b></td>'
                f'<td>{r["available"]}</td>'
                f'<td>{html.escape(r["note"])}</td></tr>'
            )
        rows_html.append("</table>")
    return (
        '<div dir="rtl" style="font-family:Arial,sans-serif">'
        f"<p>שער דולר: {rate:.3f} ₪ · משלוח {cfg['shipping_usd']}$ · מע\"מ {cfg['vat'] * 100:.0f}%. "
        "העלות כוללת משלוח ומע\"מ; הרווח מחושב מול מחיר המכירה של הכלל.</p>"
        + "".join(rows_html)
        + '<p style="color:#777;font-size:12px">הקישורים נפתחים כשמחוברים ל-HUBX.</p></div>'
    )


def send_email(subject, body_html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = env("MAIL_FROM", env("SMTP_USER"))
    msg["To"] = env("MAIL_TO")
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    host, port = env("SMTP_HOST", "smtp.gmail.com"), env("SMTP_PORT", 587, int)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=60)
    else:
        server = smtplib.SMTP(host, port, timeout=60)
        server.starttls()
    with server:
        server.login(env("SMTP_USER"), env("SMTP_PASS"))
        server.sendmail(msg["From"], [a.strip() for a in msg["To"].split(",")], msg.as_string())


# --- main check -----------------------------------------------------------------

def check(cfg, state_path, dry_run=False):
    rate = usd_ils_rate(cfg.get("fallback_usd_ils", 3.1))
    token = login(env("HUBX_USER"), env("HUBX_PASS"))

    flagged = {}
    for name in ("todays_deals", "price_drop"):
        for raw in fetch_filter(token, FILTERS[name]):
            flagged.setdefault(raw["id"], []).append(name)

    items = []
    for category in ("laptops", "desktops"):
        for raw in fetch_filter(token, FILTERS[category]):
            attrs = {a["label"]: a["value"] for a in raw.get("attributes", [])}
            items.append({
                "id": raw["id"],
                "category": category,
                "desc": raw["description"],
                "mpn": raw["mpn"],
                "price": float(raw["unitPrice"]),
                "available": raw["inventory"]["availableToSell"],
                "condition": attrs.get("Condition", ""),
                "flags": flagged.get(raw["id"], []),
                "specs": parse_specs(raw["description"]),
            })

    state = load_state(state_path)
    first_run = not state["items"]
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    drop_pct = cfg.get("price_drop_alert_pct", 5) / 100
    realert_days = cfg.get("realert_after_days", 7)

    new_rows, drop_rows = [], []
    for item in items:
        prev = state["items"].get(item["id"])
        cost = landed_cost(item["price"], cfg, rate)
        for rule in cfg["rules"]:
            if not matches(item, rule, cost):
                continue
            row = {**item, "cost": cost, "profit": rule["sale_price_ils"] - cost}
            hubx_flags = " · ".join({"todays_deals": "Today's Deal", "price_drop": "Price Drop"}[f] for f in item["flags"])
            if prev and item["price"] <= prev["price"] * (1 - drop_pct):
                row["note"] = f"{rule['name']} · ירד מ-${prev['price']:.0f}" + (f" · {hubx_flags}" if hubx_flags else "")
                drop_rows.append(row)
            elif not prev and not first_run:
                row["note"] = f"{rule['name']} · חדש באתר" + (f" · {hubx_flags}" if hubx_flags else "")
                new_rows.append(row)
            elif first_run:
                row["note"] = rule["name"] + (f" · {hubx_flags}" if hubx_flags else "")
                new_rows.append(row)
            break  # first matching rule wins

    # Don't repeat the same alert for the same price within realert_after_days.
    def fresh(row):
        key = f"{row['id']}@{row['price']:.0f}"
        last = state["alerted"].get(key)
        if last and (time.time() - last) < realert_days * 86400:
            return False
        state["alerted"][key] = time.time()
        return True

    drop_rows = sorted(filter(fresh, drop_rows), key=lambda r: -r["profit"])
    new_rows = sorted(filter(fresh, new_rows), key=lambda r: -r["profit"])
    if first_run:
        new_rows = new_rows[: cfg.get("first_run_max_rows", 40)]

    state["items"] = {i["id"]: {"price": i["price"], "seen": now} for i in items}
    cutoff = time.time() - 60 * 86400
    state["alerted"] = {k: v for k, v in state["alerted"].items() if v > cutoff}

    total = len(new_rows) + len(drop_rows)
    print(f"{now} rate={rate:.3f} items={len(items)} new_matches={len(new_rows)} price_drops={len(drop_rows)}")

    if total:
        sections = [("ירידות מחיר", drop_rows), ("סיכום ראשון: מחשבים שעומדים ביעד" if first_run else "חדשים באתר", new_rows)]
        subject = f"HUBX: {total} מציאות" + (" (סיכום ראשון)" if first_run else "")
        body = render_email(sections, rate, cfg)
        if dry_run:
            out = os.path.join(os.path.dirname(os.path.abspath(state_path)), "last_email.html")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                f.write(body)
            print(f"dry run: email written to {out}")
        else:
            send_email(subject, body)
            print(f"email sent to {env('MAIL_TO')}")

    if not dry_run:
        save_state(state_path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=env("WATCHER_CONFIG", os.path.join(os.path.dirname(__file__), "config.json")))
    parser.add_argument("--state", default=env("WATCHER_STATE", os.path.join(os.path.dirname(__file__), "data", "state.json")))
    parser.add_argument("--loop", action="store_true", help="keep running, checking every INTERVAL_HOURS")
    parser.add_argument("--dry-run", action="store_true", help="write the email to a file instead of sending; don't save state")
    args = parser.parse_args()

    missing = [n for n in ("HUBX_USER", "HUBX_PASS") if not env(n)]
    if not args.dry_run:
        missing += [n for n in ("SMTP_USER", "SMTP_PASS", "MAIL_TO") if not env(n)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)}")

    interval = env("INTERVAL_HOURS", 3, float) * 3600
    while True:
        try:
            check(load_config(args.config), args.state, args.dry_run)
        except (urllib.error.URLError, smtplib.SMTPException, KeyError, ValueError) as exc:
            print(f"check failed: {exc!r}", file=sys.stderr)
            if not args.loop:
                sys.exit(1)
        if not args.loop:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
