#!/usr/bin/env python3
# Launches mitmdump with the filter1 addon, using the bundled Python. Invoked by
# the scheduled task so mitmproxy runs from the installer's private Python
# without depending on Scripts\mitmdump.exe (which bakes an absolute path).
# Run with python.exe (NOT pythonw): mitmdump needs a real stdout or it exits.

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "filter1")
CONF = os.path.join(DATA, "mitmproxy")
ADDON = os.path.join(HERE, "filter_addon.py")
os.makedirs(CONF, exist_ok=True)


def _log(msg):
    try:
        with open(os.path.join(DATA, "run_proxy.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


try:
    from mitmproxy.tools.main import mitmdump
    sys.argv = ["mitmdump", "--set", "confdir=" + CONF, "-s", ADDON,
                "--listen-host", "127.0.0.1", "--listen-port", "8080", "-q"]
    _log("run_proxy: starting mitmdump")
    mitmdump()
    _log("run_proxy: mitmdump exited")
except Exception:
    _log("run_proxy: crashed\n" + traceback.format_exc())
    raise
