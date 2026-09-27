#!/usr/bin/env python3
# Launches mitmdump with the filter1 addon, using the bundled Python. Invoked by
# the scheduled task so mitmproxy runs from the installer's private venv without
# depending on the Scripts\mitmdump.exe launcher (which bakes an absolute path).

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                    "filter1", "mitmproxy")
ADDON = os.path.join(HERE, "filter_addon.py")
os.makedirs(CONF, exist_ok=True)

from mitmproxy.tools.main import mitmdump  # noqa: E402

sys.argv = ["mitmdump", "--set", "confdir=" + CONF, "-s", ADDON,
            "--listen-host", "127.0.0.1", "--listen-port", "8080", "-q"]
mitmdump()
