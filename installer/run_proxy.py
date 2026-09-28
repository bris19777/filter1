#!/usr/bin/env python3
# Launches mitmdump with the filter1 addon, using the bundled Python. Invoked by
# the scheduled task so mitmproxy runs from the installer's private Python
# without depending on Scripts\mitmdump.exe (which bakes an absolute path).
# Run with python.exe (NOT pythonw): mitmdump needs a real stdout or it exits.
#
# It also self-heals the browser routing on every boot: once mitmdump is actually
# listening and the CA exists, it (re)trusts the CA and points Chrome/Edge at the
# proxy. That way the proxy layer converges even if the first install deferred the
# routing (e.g. the proxy was slow to come up), instead of being applied only
# once by the installer.

import os
import socket
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "filter1")
CONF = os.path.join(DATA, "mitmproxy")
ADDON = os.path.join(HERE, "filter_addon.py")
os.makedirs(CONF, exist_ok=True)

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8080
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _log(msg):
    try:
        with open(os.path.join(DATA, "run_proxy.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def _find_ca():
    for n in ("mitmproxy-ca-cert.cer", "mitmproxy-ca-cert.pem"):
        p = os.path.join(CONF, n)
        if os.path.exists(p):
            return p
    return None


def _trust_ca(ca):
    """(Re)add the mitmproxy CA to the machine Root store. Idempotent; needs the
    SYSTEM/admin privileges the scheduled task already runs with."""
    try:
        subprocess.run(["certutil", "-f", "-addstore", "Root", ca],
                       creationflags=CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except Exception:
        _log("route: certutil trust failed\n" + traceback.format_exc())


def _route_browsers():
    """Point Chrome/Edge at the proxy and disable QUIC, via HKLM policy keys."""
    import winreg
    server = f"{PROXY_HOST}:{PROXY_PORT}"
    for base in (r"SOFTWARE\Policies\Google\Chrome",
                 r"SOFTWARE\Policies\Microsoft\Edge"):
        key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, base, 0,
                                 winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
        try:
            winreg.SetValueEx(key, "QuicAllowed", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "ProxyMode", 0, winreg.REG_SZ, "fixed_servers")
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        finally:
            winreg.CloseKey(key)


def _wait_and_route():
    """Once the proxy is listening and the CA exists, trust the CA and route the
    browser. Only routes when BOTH hold, so we never point a browser at a dead
    port or break HTTPS with an untrusted CA."""
    if os.name != "nt":
        return
    ca = None
    for _ in range(180):
        time.sleep(1)
        if _port_open(PROXY_HOST, PROXY_PORT):
            ca = _find_ca()
            if ca:
                break
    if not ca:
        _log("route: proxy not listening or CA missing; browser routing skipped")
        return
    _trust_ca(ca)
    try:
        _route_browsers()
        _log(f"route: browser routed to {PROXY_HOST}:{PROXY_PORT}")
    except Exception:
        _log("route: failed to set browser policy\n" + traceback.format_exc())


try:
    if os.name == "nt":
        threading.Thread(target=_wait_and_route, daemon=True).start()
    from mitmproxy.tools.main import mitmdump
    sys.argv = ["mitmdump", "--set", "confdir=" + CONF, "-s", ADDON,
                "--listen-host", PROXY_HOST, "--listen-port", str(PROXY_PORT), "-q"]
    _log("run_proxy: starting mitmdump")
    mitmdump()
    _log("run_proxy: mitmdump exited")
except Exception:
    _log("run_proxy: crashed\n" + traceback.format_exc())
    raise
