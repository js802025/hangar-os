"""Find or start the daemon (shared by the CLI and the native apps)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

from hangar import auth
from hangar.engine import DAEMON_PORT

URL = f"http://127.0.0.1:{DAEMON_PORT}"


def daemon_up() -> bool:
    try:
        return httpx.get(f"{URL}/healthz", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_daemon() -> bool:
    """Start the daemon in the background if needed. False if it won't come up."""
    if daemon_up():
        return True
    if os.environ.get("HANGAR_NO_AUTODAEMON"):
        return False
    logf = auth.private_dir(auth.STATE_DIR) / "daemon.log"
    with open(logf, "a") as out:
        subprocess.Popen([sys.executable, "-m", "hangar.daemon"], stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        time.sleep(0.25)
        if daemon_up():
            return True
    return False
