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
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from urllib.parse import urlparse


def _ssl_context():
    """A verifying SSL context. A bundled/relocated Python may not read the
    Windows root store, so prefer certifi's CA bundle when it is available."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()

try:
    from dnslib import QTYPE, RR, A, DNSRecord
    from dnslib.server import BaseResolver, DNSServer
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r requirements.txt")

# Bump on every release together with the installer's AppVersion. The control
# server advertises the latest version; the agent self-updates when it is behind.
AGENT_VERSION = "1.1.8"

FALLBACK_UPSTREAMS = ["1.1.1.1", "8.8.8.8"]  # used only if we can't detect any
POLL_SECONDS = 60          # how often to fetch config
DNS_ASSERT_SECONDS = 30    # how often to re-assert / health-check the DNS setting
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
        # server_url may list several control-server URLs (comma/space/semicolon
        # separated). The agent registers via whichever the network allows — so a
        # blocked primary domain (e.g. an upstream "clean internet" filter such as
        # Rimon blocking the host) can fail over to an alternate/whitelisted one.
        self.server_urls = parse_servers(server_url)
        self.server_url = self.server_urls[0] if self.server_urls else ""
        self.lock = threading.Lock()
        self.mode = "open"
        self.whitelist = set()
        self.blocked = set()          # blacklist_manual + downloaded blocklists
        self.blocklist_urls = []
        self._blocklist_sig = None    # to avoid re-downloading unchanged lists
        self.have_config = False      # True once we successfully fetched config
        self.layers = "both"          # dns | proxy | both (which layer enforces)
        self.upstreams = list(FALLBACK_UPSTREAMS)  # real DNS to forward to
        # last-poll health, surfaced in status.json for local diagnostics
        self.last_poll_ok = False
        self.last_error = ""
        self.last_poll_ts = 0.0
        # remote auto-update fields advertised by the server
        self.latest_version = ""
        self.update_url = ""
        self.update_signer = ""     # pinned code-signing cert thumbprint (required)
        self.update_state = ""
        # every control server's host is always allowed so the agent can poll
        self.control_hosts = set()
        for u in self.server_urls:
            h = urlparse(u).hostname
            if h:
                self.control_hosts.add(h.lower())

    def apply(self, cfg):
        mode = cfg.get("mode", "open")
        whitelist = {d.lower() for d in cfg.get("whitelist", [])}
        manual = {d.lower() for d in cfg.get("blacklist_manual", [])}
        urls = cfg.get("blocklists", [])
        sig = tuple(urls)
        # download OUTSIDE the lock so DNS resolution is not stalled; only cache
        # a non-empty result, so a transient failure retries on the next poll
        if mode == "blacklist" and sig != self._blocklist_sig:
            dl = download_blocklists(urls)
            if dl:
                self._downloaded = dl
                self._blocklist_sig = sig
        downloaded = getattr(self, "_downloaded", set())
        with self.lock:
            self.have_config = True
            self.mode = mode
            self.layers = cfg.get("layers", "both")
            self.whitelist = whitelist
            self.blocked = manual | downloaded
            self.blocklist_urls = urls
            self.latest_version = (cfg.get("latest_version") or "").strip()
            self.update_url = (cfg.get("update_url") or "").strip()
            self.update_signer = (cfg.get("update_signer") or "").replace(":", "").replace(" ", "").strip().lower()

    def decision(self, qname):
        """Return True if the query should be allowed (forwarded upstream)."""
        name = qname.rstrip(".").lower()
        with self.lock:
            mode = self.mode
            # fail-open: never block until we have a real config from the server
            if not self.have_config:
                return True
            if self.control_hosts and self._matches(name, self.control_hosts):
                return True
            # DNS layer disabled (proxy enforces): forward everything
            if self.layers == "proxy":
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
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
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
            # forward to the first upstream that answers
            for up in list(self.state.upstreams):
                try:
                    proxy = request.send(up, 53, timeout=4)
                    return DNSRecord.parse(proxy)
                except Exception:
                    continue
            return reply  # empty reply if every upstream failed
        else:
            # blocked: answer with 0.0.0.0
            if request.q.qtype == QTYPE.A:
                reply.add_answer(RR(qname, QTYPE.A, rdata=A(BLOCK_IP), ttl=60))
            return reply


def start_ipv6_resolver(resolver, port):
    """Best-effort: also listen on [::1] so the system's IPv6 DNS can point at us
    and IPv6 queries can't bypass the filter. Returns True if it started.

    dnslib's default server is IPv4-only, so we hand it a small AF_INET6 UDP
    server subclass. If the IPv6 stack is disabled the bind fails and we simply
    stay IPv4-only (and won't touch the machine's IPv6 DNS)."""
    if not IS_WINDOWS:
        return False
    try:
        import socketserver
        from dnslib.server import DNSServer

        class _UDPServerV6(socketserver.ThreadingUDPServer):
            allow_reuse_address = True
            daemon_threads = True
            address_family = socket.AF_INET6

        s6 = DNSServer(resolver, port=port, address="::1", server=_UDPServerV6)
        s6.start_thread()
        return True
    except Exception as e:
        print(f"[dns] IPv6 listener not started ({e}); staying IPv4-only")
        return False


def parse_servers(raw):
    """Split a raw server value into an ordered, de-duplicated list of base URLs.
    Accepts comma / semicolon / whitespace separators."""
    out = []
    for p in re.split(r"[,;\s]+", (raw or "").strip()):
        p = p.strip().strip("<>\"' \t\r\n").rstrip("/")
        if p and p not in out:
            out.append(p)
    return out


def config_url(base_url, token, device_id, name, count=None, amode=None):
    d = {"token": token, "device_id": device_id, "name": name}
    if count is not None:
        d["count"] = count
    if amode is not None:
        d["amode"] = amode
    q = urllib.parse.urlencode(d)
    return f"{base_url}/api/config?{q}"


def version_tuple(v):
    """'1.2.10' -> (1, 2, 10); non-numeric parts ignored. Empty -> ()."""
    return tuple(int(p) for p in re.findall(r"\d+", v or ""))


# guards against re-downloading the same version in a tight loop
_last_update_attempt = {"version": "", "ts": 0.0}


def _set_update_state(state, s):
    with state.lock:
        state.update_state = s


def verify_installer_signature(path, expected_thumbprint):
    """Return (ok, detail). Requires a VALID Authenticode signature whose signing
    certificate thumbprint matches the pinned value. Fails closed on any doubt."""
    lp = path.replace("'", "''")
    ps = (f"$s = Get-AuthenticodeSignature -LiteralPath '{lp}'; "
          "if ($s.Status -ne 'Valid') { Write-Output ('status=' + $s.Status); exit 0 }; "
          "Write-Output ('thumb=' + $s.SignerCertificate.Thumbprint)")
    out = (_ps(ps) or "").strip().lower()
    if "thumb=" not in out:
        return False, (out or "no signature")
    thumb = out.split("thumb=", 1)[1].strip().replace(":", "").replace(" ", "")
    if thumb == expected_thumbprint:
        return True, thumb
    return False, f"thumbprint mismatch ({thumb})"


def maybe_self_update(state):
    """Remote auto-update: if the server advertises a newer version and a pinned
    signer, download the installer, verify its Authenticode signature against the
    pinned thumbprint, and run it silently. Fails closed: any missing pin or failed
    verification skips the update and leaves the running agent untouched."""
    if not IS_WINDOWS:
        return
    with state.lock:
        latest = state.latest_version
        url = state.update_url
        signer = state.update_signer
    if not url or not latest:
        _set_update_state(state, f"no update configured (running {AGENT_VERSION})")
        return
    if version_tuple(latest) <= version_tuple(AGENT_VERSION):
        _set_update_state(state, f"up-to-date ({AGENT_VERSION})")
        return
    if not signer:
        _set_update_state(state, f"update {latest} available but no pinned signer; refusing")
        print("[update] refusing: update_signer (cert thumbprint) is not set")
        return
    now = time.time()
    if _last_update_attempt["version"] == latest and now - _last_update_attempt["ts"] < 1800:
        return
    _last_update_attempt["version"] = latest
    _last_update_attempt["ts"] = now
    safe_ver = re.sub(r"[^0-9.]", "", latest) or "new"
    tmp = os.path.join(id_dir(), f"filter1-update-{safe_ver}.exe")
    try:
        _set_update_state(state, f"downloading {latest}")
        req = urllib.request.Request(url, headers={"User-Agent": "filter1"})
        with urllib.request.urlopen(req, timeout=180, context=SSL_CTX) as r, \
                open(tmp, "wb") as f:
            f.write(r.read())
        ok, detail = verify_installer_signature(tmp, signer)
        if not ok:
            _set_update_state(state, f"update {latest} rejected: {detail}")
            print(f"[update] signature check failed: {detail}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
        _set_update_state(state, f"installing {latest}")
        print(f"[update] verified {latest} (signer {detail}); launching silent installer")
        # /VERYSILENT so the installer's existing in-place upgrade runs unattended;
        # it stops our task, replaces files, and restarts the (new) agent.
        subprocess.Popen([tmp, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
                         creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        _set_update_state(state, f"update error: {e}")
        print(f"[update] error: {e}")


def write_status(state, device_id, name):
    """Persist a local snapshot of why the agent is or isn't working, so the
    status tool (and the parent) can see, e.g., that the mitmproxy cert exists
    but the device never registered because the server poll is failing."""
    try:
        import json
        with state.lock:
            data = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "agent_version": AGENT_VERSION,
                "latest_version": state.latest_version,
                "update_state": state.update_state,
                "server": state.server_url,
                "servers": list(state.server_urls),
                "device_id": device_id,
                "name": name,
                "server_reachable": state.last_poll_ok,
                "registered": bool(state.last_poll_ok and state.have_config),
                "last_error": state.last_error,
                "mode": state.mode if state.have_config else None,
                "whitelist_count": len(state.whitelist),
                "blocked_count": len(state.blocked),
                "dns_listen_ipv4": True,
                "dns_listen_ipv6": LISTEN_V6,
                "dns_taken_over": DNS_TAKEN_OVER,
                "upstreams": list(state.upstreams),
            }
        d = id_dir()
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, "status.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(d, "status.json"))
    except Exception as e:
        print(f"[status] could not write status.json: {e}")


def poll_loop(state, token, device_id, name):
    while True:
        # report the currently applied status so the panel can show readiness
        with state.lock:
            cnt, amode = len(state.blocked), state.mode
            servers = list(state.server_urls)
        ok = False
        last_err = "no control server configured"
        for base in servers:
            url = config_url(base, token, device_id, name, count=cnt, amode=amode)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "filter1"})
                with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
                    import json
                    cfg = json.loads(r.read().decode("utf-8"))
                state.apply(cfg)
                with state.lock:
                    state.server_url = base
                    state.last_poll_ok = True
                    state.last_error = ""
                    state.last_poll_ts = time.time()
                print(f"[poll] server={base} id={device_id} mode={state.mode} "
                      f"whitelist={len(state.whitelist)} blocked={len(state.blocked)}")
                ok = True
                break
            except Exception as e:
                last_err = f"{base}: {e}"
                print(f"[poll] {last_err}")
        if not ok:
            with state.lock:
                state.last_poll_ok = False
                state.last_error = last_err
                state.last_poll_ts = time.time()
            print(f"[poll] all control servers failed: {last_err}")
        if ok:
            maybe_self_update(state)
        write_status(state, device_id, name)
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- system DNS

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _ps(cmd):
    """Run a PowerShell command, return stdout text (or '')."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            text=True, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        return out
    except Exception:
        return ""


def read_current_dns():
    """The machine's current IPv4 DNS servers, excluding our own loopback.

    Scoped to physical, connected adapters so we don't pick up (or fight) the
    DNS of virtual adapters (Hyper-V/VMware/VirtualBox/WSL/VPN)."""
    if not IS_WINDOWS:
        return []
    out = _ps("(Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Up'} | "
              "Get-DnsClientServerAddress -AddressFamily IPv4)."
              "ServerAddresses -join ','")
    servers = [s.strip() for s in out.replace("\n", ",").split(",") if s.strip()]
    seen = []
    for s in servers:
        if not s.startswith("127.") and s not in seen:
            seen.append(s)
    return seen


def get_upstreams():
    """Detect the real DNS to forward to, and persist it so we keep it even
    after we have overridden the system DNS with our own loopback."""
    path = os.path.join(id_dir(), "upstreams.txt")
    cur = read_current_dns()
    if cur:
        try:
            os.makedirs(id_dir(), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(cur))
        except Exception:
            pass
        base = cur
    else:
        base = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    base = [l.strip() for l in f if l.strip()]
            except Exception:
                base = []
    ups = base + [u for u in FALLBACK_UPSTREAMS if u not in base]
    return ups or list(FALLBACK_UPSTREAMS)


def upstream_ok(state):
    """True if at least one upstream answers a test query."""
    for up in list(state.upstreams):
        try:
            q = DNSRecord.question("cloudflare.com")
            q.send(up, 53, timeout=3)
            return True
        except Exception:
            continue
    return False


# True once we also manage to listen on [::1]; only then do we point the
# system's IPv6 DNS at ourselves (otherwise we'd break IPv6 DNS resolution).
LISTEN_V6 = False

# Whether we currently hold the system DNS (False when we've failed open because
# no upstream was reachable). Surfaced in status.json.
DNS_TAKEN_OVER = False


def set_system_dns_all():
    """Point the system DNS at our loopback resolver, on physical adapters only.

    Virtual adapters (Hyper-V/VMware/VirtualBox/WSL/VPN) are left alone so we
    don't break host-only networking or fight other DNS managers. When an IPv6
    listener is up we also set the IPv6 DNS to ::1 so IPv6 queries can't bypass
    the filter."""
    if not IS_WINDOWS:
        return
    addrs = "'127.0.0.1'"
    if LISTEN_V6:
        addrs += ",'::1'"
    _ps("Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Up'} | "
        f"Set-DnsClientServerAddress -ServerAddresses ({addrs})")


def reset_system_dns_all():
    if not IS_WINDOWS:
        return
    _ps("Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Up'} | "
        "Set-DnsClientServerAddress -ResetServerAddresses")


def dns_guard_loop(state):
    """Keep the system DNS pointed at us WHILE we can still reach a real
    upstream. If every upstream becomes unreachable, revert to automatic DNS so
    the machine is never cut off (fail-open)."""
    global DNS_TAKEN_OVER
    if not IS_WINDOWS:
        return
    fails = 0
    taken_over = False
    while True:
        if upstream_ok(state):
            fails = 0
            set_system_dns_all()
            taken_over = True
        else:
            fails += 1
            if taken_over and fails >= 2:
                print("[dns] no upstream reachable; reverting to automatic DNS "
                      "(fail-open)")
                reset_system_dns_all()
                taken_over = False
        DNS_TAKEN_OVER = taken_over
        time.sleep(DNS_ASSERT_SECONDS)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="filter1 agent")
    ap.add_argument("--server", help="control server URL")
    ap.add_argument("--token", help="agent token")
    ap.add_argument("--config", help="path to a key=value config file "
                    "(server=..., token=...); CLI flags override it")
    ap.add_argument("--once", action="store_true",
                    help="fetch config once and print the decision map (dev)")
    ap.add_argument("--no-setdns", action="store_true",
                    help="do not touch system DNS (dev / testing)")
    ap.add_argument("--port", type=int, default=53)
    ap.add_argument("--device-id", help="override the machine id (dev)")
    args = ap.parse_args()

    # merge config file (CLI flags win)
    server = args.server
    token = args.token
    if args.config and os.path.exists(args.config):
        try:
            # utf-8-sig transparently strips a BOM that PowerShell may have added
            with open(args.config, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "server" and not server:
                        server = v
                    elif k == "token" and not token:
                        token = v
        except Exception as e:
            print(f"[config] failed to read {args.config}: {e}")

    # tolerate a token/server pasted with surrounding <>, quotes or spaces
    def _clean(x):
        return x.strip().strip("<>\"' \t\r\n") if x else x
    server, token = _clean(server), _clean(token)
    if not server or not token:
        sys.exit("Missing --server/--token (or a --config file providing them).")
    args.server, args.token = server, token

    state = State(args.server)
    device_id = args.device_id or get_device_id()
    name = device_name()
    print(f"[id] device_id={device_id} name={name}")

    if args.once:
        import json
        cfg = None
        for base in state.server_urls:
            try:
                url = config_url(base, args.token, device_id, name)
                with urllib.request.urlopen(url, timeout=15, context=SSL_CTX) as r:
                    cfg = json.loads(r.read().decode("utf-8"))
                state.server_url = base
                print("server:", base)
                break
            except Exception as e:
                print(f"server {base} failed: {e}")
        if cfg is None:
            sys.exit("could not reach any control server")
        state.apply(cfg)
        print("mode:", state.mode)
        print("whitelist:", sorted(state.whitelist))
        print("blocked count:", len(state.blocked))
        for test in ("youtube.com", "ynet.co.il", "example.com"):
            print(f"  {test} -> {'ALLOW' if state.decision(test) else 'BLOCK'}")
        return

    # detect the real DNS to forward to BEFORE we override the system setting
    state.upstreams = get_upstreams()
    print(f"[dns] upstreams: {state.upstreams}")

    # start the local resolver first, so we can forward before taking over DNS
    resolver = Resolver(state)
    server = DNSServer(resolver, port=args.port, address="127.0.0.1")
    print(f"[dns] listening on 127.0.0.1:{args.port}")
    server.start_thread()

    # also listen on [::1] so IPv6 DNS can be pointed at us (closes the IPv6
    # bypass on dual-stack machines); best-effort, IPv4 keeps working regardless
    global LISTEN_V6
    LISTEN_V6 = start_ipv6_resolver(resolver, args.port)
    if LISTEN_V6:
        print(f"[dns] also listening on [::1]:{args.port}")

    # start config poller
    threading.Thread(target=poll_loop,
                     args=(state, args.token, device_id, name),
                     daemon=True).start()
    # take over the system DNS only while a real upstream stays reachable
    if not args.no_setdns:
        threading.Thread(target=dns_guard_loop, args=(state,), daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
