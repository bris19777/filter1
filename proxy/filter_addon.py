# -*- coding: utf-8 -*-
"""filter1 mitmproxy addon.

HTTPS content filtering by hostname, driven by the same filter1 control server
as the DNS agent. Because mitmproxy terminates TLS, this enforces policy even
when the client uses DoH or ECH, which defeat DNS-level filtering.

It reuses the DNS agent's config so both layers share one identity:
  server + token from   <ProgramData>\\filter1\\filter1.cfg
  device id from        <ProgramData>\\filter1\\device_id

Run (see install-proxy.ps1):
  mitmdump --set confdir=<dir> -s filter_addon.py --listen-host 127.0.0.1 --listen-port 8080

This is transparent, administrator-installed parental-control software. It must
only be deployed on machines the installer's operator administers, with the
filter1 root certificate installed on those machines.
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from mitmproxy import http

POLL_SECONDS = 60
BLOCKLIST_REFRESH = 3600  # re-download public blocklists at most this often
FLUSH_SECONDS = 20        # how often to send batched activity to the server

# talk to the control server and blocklists DIRECTLY, never through the system
# proxy (which is this very process) — otherwise the addon's own requests loop
# back through mitmproxy and can fail, leaving the policy unloaded
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def cfg_dir():
    if os.name == "nt":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return os.path.join(base, "filter1")
    return os.path.join(os.path.expanduser("~"), ".filter1")


def data_dirs():
    """Where the DNS agent may have written filter1.cfg / device_id. The
    installer writes the cfg under Program Files; the agent writes device_id
    under ProgramData, so search both."""
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pd = os.environ.get("ProgramData", r"C:\ProgramData")
        return [os.path.join(pf, "filter1"), os.path.join(pd, "filter1")]
    return [os.path.join(os.path.expanduser("~"), ".filter1")]


def find_file(name):
    for d in data_dirs():
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def log(msg):
    """Append a diagnostic line to <ProgramData>\\filter1\\proxy.log."""
    try:
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n"
        with open(os.path.join(cfg_dir(), "proxy.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class Policy:
    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "open"
        self.layers = "both"           # dns | proxy | both (which layer enforces)
        self.whitelist = set()
        self.manual = set()
        self.downloaded = set()
        self.blocklist_urls = []
        self._sig = None
        self._last_dl = 0
        self.have = False
        self.server = ""
        self.token = ""
        self.device_id = "proxy"
        self.control_host = ""
        # batched activity (deduped within each flush window)
        self.ev_lock = threading.Lock()
        self.ev_blocked = set()
        self.ev_visited = set()

    def load_local(self):
        cfg_path = find_file("filter1.cfg")
        if cfg_path:
            try:
                with open(cfg_path, encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip()
                        if "=" in line and not line.startswith("#"):
                            k, v = line.split("=", 1)
                            k = k.strip().lower()
                            v = v.strip().strip("<>\"' ")
                            if k == "server":
                                self.server = v
                            elif k == "token":
                                self.token = v
            except Exception as e:
                print("[filter1] cannot read filter1.cfg:", e)
                log(f"cannot read {cfg_path}: {e}")
        else:
            log("filter1.cfg not found in " + " ; ".join(data_dirs()))
        id_path = find_file("device_id")
        if id_path:
            try:
                with open(id_path, encoding="utf-8") as f:
                    self.device_id = f.read().strip() or "proxy"
            except Exception:
                self.device_id = "proxy"
        self.control_host = (urlparse(self.server).hostname or "").lower()

    def _download_blocklists(self, urls):
        blocked = set()
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "filter1"})
                with DIRECT.open(req, timeout=60) as r:
                    text = r.read().decode("utf-8", "ignore")
            except Exception as e:
                print("[filter1] blocklist failed", url, e)
                log(f"blocklist failed {url}: {e}")
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                domain = parts[1] if len(parts) >= 2 else parts[0]
                domain = domain.lower().strip(".")
                if domain and domain not in ("localhost", "0.0.0.0", "127.0.0.1"):
                    blocked.add(domain)
        print("[filter1] blocklist loaded", len(blocked), "domains")
        log(f"blocklist loaded {len(blocked)} domains")
        return blocked

    def poll_loop(self):
        while True:
            try:
                q = urllib.parse.urlencode({
                    "token": self.token, "device_id": self.device_id,
                    "name": "proxy"})
                url = f"{self.server}/api/config?{q}"
                with DIRECT.open(url, timeout=15) as r:
                    cfg = json.loads(r.read().decode("utf-8"))
                mode = cfg.get("mode", "open")
                whitelist = {d.lower() for d in cfg.get("whitelist", [])}
                manual = {d.lower() for d in cfg.get("blacklist_manual", [])}
                urls = cfg.get("blocklists", [])
                now = time.time()
                sig = tuple(urls)
                if mode == "blacklist" and (sig != self._sig or
                                            now - self._last_dl > BLOCKLIST_REFRESH):
                    self.downloaded = self._download_blocklists(urls)
                    self._sig = sig
                    self._last_dl = now
                with self.lock:
                    self.mode = mode
                    self.layers = cfg.get("layers", "both")
                    self.whitelist = whitelist
                    self.manual = manual
                    self.blocklist_urls = urls
                    self.have = True
                msg = (f"poll ok: mode={mode} layers={self.layers} "
                       f"wl={len(whitelist)} blocked={len(manual) + len(self.downloaded)}")
                print("[filter1]", msg)
                log(msg)
            except Exception as e:
                print("[filter1] poll failed:", e)
                log(f"poll failed: {e}")
            time.sleep(POLL_SECONDS)

    @staticmethod
    def _match(name, domain_set):
        if not domain_set:
            return False
        parts = name.split(".")
        return any(".".join(parts[i:]) in domain_set for i in range(len(parts)))

    def record(self, host, blocked):
        host = (host or "").lower().rstrip(".")
        if not host:
            return
        with self.ev_lock:
            (self.ev_blocked if blocked else self.ev_visited).add(host)

    def report_loop(self):
        while True:
            time.sleep(FLUSH_SECONDS)
            with self.ev_lock:
                blocked = list(self.ev_blocked)
                visited = list(self.ev_visited)
                self.ev_blocked.clear()
                self.ev_visited.clear()
            if (not blocked and not visited) or not self.server or not self.token:
                continue
            try:
                payload = json.dumps({
                    "device_id": self.device_id,
                    "blocked": blocked, "visited": visited}).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.server}/api/log?token={self.token}", data=payload,
                    headers={"Content-Type": "application/json",
                             "User-Agent": "filter1"})
                DIRECT.open(req, timeout=15).read()
            except Exception as e:
                log(f"report failed: {e}")

    def allowed(self, host):
        host = (host or "").lower().rstrip(".")
        with self.lock:
            if not self.have:          # fail-open until we have a real policy
                return True
            if self.control_host and self._match(host, {self.control_host}):
                return True
            # proxy layer disabled (DNS enforces): pass everything through
            if self.layers == "dns":
                return True
            if self.mode == "open":
                return True
            if self.mode in ("lockdown", "whitelist"):
                return self._match(host, self.whitelist)
            if self.mode == "blacklist":
                return not self._match(host, self.manual | self.downloaded)
        return True


POLICY = Policy()

BLOCK_HTML = (
    "<!doctype html><html dir='rtl' lang='he'><head><meta charset='utf-8'>"
    "<title>\u05d7\u05e1\u05d5\u05dd</title></head>"
    "<body style='font-family:system-ui,Arial;background:#0f172a;color:#e2e8f0;"
    "display:flex;min-height:100vh;align-items:center;justify-content:center'>"
    "<div style='text-align:center'><div style='font-size:52px'>\U0001f6e1\ufe0f</div>"
    "<h2>\u05d4\u05d0\u05ea\u05e8 \u05d7\u05e1\u05d5\u05dd</h2>"
    "<p style='color:#94a3b8'>\u05e0\u05d7\u05e1\u05dd \u05e2\u05dc \u05d9\u05d3\u05d9 filter1</p>"
    "</div></body></html>"
).encode("utf-8")


def load(loader):
    POLICY.load_local()
    log(f"addon started: server={POLICY.server} device={POLICY.device_id} "
        f"control_host={POLICY.control_host}")
    threading.Thread(target=POLICY.poll_loop, daemon=True).start()
    threading.Thread(target=POLICY.report_loop, daemon=True).start()


def request(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    if not POLICY.allowed(host):
        POLICY.record(host, True)
        flow.response = http.Response.make(
            403, BLOCK_HTML, {"Content-Type": "text/html; charset=utf-8"})
    else:
        POLICY.record(host, False)
