#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""filter1 on-device agent.

Runs a local DNS resolver on 127.0.0.1:53, points the machine's DNS at it, and
filters every query according to the mode fetched from the control server:

  open      -> forward everything upstream
  lockdown  -> resolve only whitelist domains (+ control server), block the rest
  blacklist -> block public blocklists + manual blacklist, forward the rest
  whitelist -> resolve only whitelist domains, block the rest

Blocked answers return 0.0.0.0 so the browser cannot reach the site.

This is transparent parental-control software: it installs under its real name
and is managed by the machine's administrator (the parent).
"""

import argparse
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from urllib.parse import urlparse

try:
    from dnslib import QTYPE, RR, A, DNSRecord
    from dnslib.server import BaseResolver, DNSServer
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r requirements.txt")

UPSTREAM = "1.1.1.1"
POLL_SECONDS = 60          # how often to fetch config
DNS_ASSERT_SECONDS = 30    # how often to re-assert the system DNS setting
BLOCK_IP = "0.0.0.0"
IS_WINDOWS = platform.system() == "Windows"


def id_dir():
    """Directory where the persistent machine id is stored."""
    if IS_WINDOWS:
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return os.path.join(base, "filter1")
    return os.path.join(os.path.expanduser("~"), ".filter1")


def get_device_id():
    """Return a stable machine id, generating and persisting one on first run."""
    d = id_dir()
    path = os.path.join(d, "device_id")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    new_id = uuid.uuid4().hex[:16]
    try:
        os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_id)
    except Exception as e:
        print(f"[id] could not persist device id: {e}")
    return new_id


def device_name():
    try:
        return socket.gethostname()
    except Exception:
        return "pc"


class State:
    """Thread-safe snapshot of the active policy."""

    def __init__(self, server_url):
        self.server_url = server_url.rstrip("/")
        self.lock = threading.Lock()
        self.mode = "open"
        self.whitelist = set()
        self.blocked = set()          # blacklist_manual + downloaded blocklists
        self.blocklist_urls = []
        self._blocklist_sig = None    # to avoid re-downloading unchanged lists
        # the control server's own host is always allowed so the agent can poll
        host = urlparse(self.server_url).hostname
        self.control_host = host.lower() if host else None

    def apply(self, cfg):
        with self.lock:
            self.mode = cfg.get("mode", "open")
            self.whitelist = {d.lower() for d in cfg.get("whitelist", [])}
            manual = {d.lower() for d in cfg.get("blacklist_manual", [])}
            urls = cfg.get("blocklists", [])
            sig = tuple(urls)
            if self.mode == "blacklist" and sig != self._blocklist_sig:
                downloaded = download_blocklists(urls)
                self._blocklist_sig = sig
                self._downloaded = downloaded
            downloaded = getattr(self, "_downloaded", set())
            self.blocked = manual | downloaded
            self.blocklist_urls = urls

    def decision(self, qname):
        """Return True if the query should be allowed (forwarded upstream)."""
        name = qname.rstrip(".").lower()
        with self.lock:
            mode = self.mode
            if self.control_host and self._matches(name, {self.control_host}):
                return True
            if mode == "open":
                return True
            if mode == "lockdown" or mode == "whitelist":
                return self._matches(name, self.whitelist)
            if mode == "blacklist":
                return not self._matches(name, self.blocked)
        return True

    @staticmethod
    def _matches(name, domain_set):
        """Match name against a set, including parent domains (subdomains)."""
        if not domain_set:
            return False
        parts = name.split(".")
        for i in range(len(parts)):
            if ".".join(parts[i:]) in domain_set:
                return True
        return False


def download_blocklists(urls):
    """Download hosts-format lists and return the set of blocked domains."""
    blocked = set()
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "filter1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"[blocklist] failed {url}: {e}")
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # hosts format: "0.0.0.0 domain.com"  or plain "domain.com"
            parts = line.split()
            domain = parts[1] if len(parts) >= 2 else parts[0]
            domain = domain.lower().strip(".")
            if domain and domain not in ("localhost", "0.0.0.0", "127.0.0.1"):
                blocked.add(domain)
    print(f"[blocklist] loaded {len(blocked)} blocked domains")
    return blocked


class Resolver(BaseResolver):
    def __init__(self, state):
        self.state = state

    def resolve(self, request, handler):
        qname = str(request.q.qname)
        reply = request.reply()
        if self.state.decision(qname):
            # forward upstream and pass the answer through
            try:
                proxy = request.send(UPSTREAM, 53, timeout=5)
                return DNSRecord.parse(proxy)
            except Exception:
                return reply  # empty reply on upstream failure
        else:
            # blocked: answer with 0.0.0.0
            if request.q.qtype == QTYPE.A:
                reply.add_answer(RR(qname, QTYPE.A, rdata=A(BLOCK_IP), ttl=60))
            return reply


def config_url(state, token, device_id, name):
    q = urllib.parse.urlencode({
        "token": token, "device_id": device_id, "name": name})
    return f"{state.server_url}/api/config?{q}"


def poll_loop(state, token, device_id, name):
    url = config_url(state, token, device_id, name)
    while True:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "filter1"})
            with urllib.request.urlopen(req, timeout=15) as r:
                import json
                cfg = json.loads(r.read().decode("utf-8"))
            state.apply(cfg)
            print(f"[poll] id={device_id} mode={state.mode} "
                  f"whitelist={len(state.whitelist)} blocked={len(state.blocked)}")
        except Exception as e:
            print(f"[poll] failed: {e}")
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- system DNS

def get_active_interface():
    """Best-effort: find the connected interface name on Windows."""
    try:
        out = subprocess.check_output(
            ["netsh", "interface", "show", "interface"],
            text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "Connected" in line or "מחובר" in line:
                # last column is the interface name
                name = line.split()[-1]
                return name
    except Exception:
        pass
    return "Ethernet"


def set_system_dns(iface):
    """Point the machine's DNS at the local resolver (Windows)."""
    if not IS_WINDOWS:
        return
    try:
        subprocess.run(["netsh", "interface", "ipv4", "set", "dns",
                        f"name={iface}", "static", "127.0.0.1"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[dns] set failed: {e}")


def dns_assert_loop():
    if not IS_WINDOWS:
        return
    iface = get_active_interface()
    while True:
        set_system_dns(iface)
        time.sleep(DNS_ASSERT_SECONDS)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="filter1 agent")
    ap.add_argument("--server", required=True, help="control server URL")
    ap.add_argument("--token", required=True, help="agent token")
    ap.add_argument("--once", action="store_true",
                    help="fetch config once and print the decision map (dev)")
    ap.add_argument("--no-setdns", action="store_true",
                    help="do not touch system DNS (dev / testing)")
    ap.add_argument("--port", type=int, default=53)
    ap.add_argument("--device-id", help="override the machine id (dev)")
    args = ap.parse_args()

    state = State(args.server)
    device_id = args.device_id or get_device_id()
    name = device_name()
    print(f"[id] device_id={device_id} name={name}")

    if args.once:
        import json
        url = config_url(state, args.token, device_id, name)
        with urllib.request.urlopen(url, timeout=15) as r:
            cfg = json.loads(r.read().decode("utf-8"))
        state.apply(cfg)
        print("mode:", state.mode)
        print("whitelist:", sorted(state.whitelist))
        print("blocked count:", len(state.blocked))
        for test in ("youtube.com", "ynet.co.il", "example.com"):
            print(f"  {test} -> {'ALLOW' if state.decision(test) else 'BLOCK'}")
        return

    # start config poller
    threading.Thread(target=poll_loop,
                     args=(state, args.token, device_id, name),
                     daemon=True).start()
    # start DNS re-assert loop
    if not args.no_setdns:
        threading.Thread(target=dns_assert_loop, daemon=True).start()

    resolver = Resolver(state)
    server = DNSServer(resolver, port=args.port, address="127.0.0.1")
    print(f"[dns] listening on 127.0.0.1:{args.port}")
    server.start_thread()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
