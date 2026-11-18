"""M5 smoke test: the Spaces UI's API, end to end, with a throwaway state dir.

  - the UI is served (and unsigned browsers go to the sign-in page),
  - creating an agent with a shared folder (mounted read-only), refusing ~/.ssh,
  - API-key accounts, and the official sign-in shown as a job with its link
    (cancelled before signing in: this test never logs in to anything),
  - notification settings keep secrets masked,
  - deleting the agent.
Usage:  uv run python tests/smoke_m5.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-m5-"))
PORT = 7792
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT=str(PORT))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402
import uvicorn  # noqa: E402

from hangar import auth  # noqa: E402
from hangar.daemon import app, eng  # noqa: E402

D = f"http://127.0.0.1:{PORT}"
SHARE = TMP / "shared-notes"


def until(pred, timeout=30, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if v := pred():
                return v
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(what)


def main() -> None:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    until(lambda: httpx.get(f"{D}/healthz").status_code == 200, what="daemon up")
    c = httpx.Client(base_url=D, headers={"Authorization": f"Bearer {auth.admin_token()}"}, timeout=60)
    SHARE.mkdir()
    (SHARE / "hello.txt").write_text("hi from the host\n")
    eng.destroy("m5-ui")
    try:
        # UI
        r = httpx.get(f"{D}/")
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), r
        assert "Admin token" in httpx.get(f"{D}/login").text
        assert "app.js" in c.get("/").text and c.get("/ui/app.js").status_code == 200
        print("UI served; signed-out browsers get the sign-in page: ok")

        # agents
        opts = c.get("/api/options").json()
        assert "base" in opts["flavors"], opts
        bad = c.post("/api/agents", json={"name": "m5-ui", "shares": ["~/.ssh"]})
        assert bad.status_code == 400 and "secrets" in bad.json()["error"], bad.text
        r = c.post("/api/agents", json={"name": "m5-ui", "task": "look around", "shares": [str(SHARE)]})
        assert r.status_code == 200, r.text
        eng.wait_ready("m5-ui", timeout=120)
        _, out = eng.exec("m5-ui", ["cat", "/home/agent/shared/shared-notes/hello.txt"])
        assert out == "hi from the host\n", out
        code, out = eng.exec("m5-ui", ["touch", "/home/agent/shared/shared-notes/x"])
        assert code != 0 and "Read-only" in out, out
        a = c.get("/api/agents/m5-ui").json()
        assert a["shares"] == [str(SHARE.resolve())], a
        print("create via API, shared folder read-only, ~/.ssh refused: ok")

        # accounts: API key
        assert c.post("/api/accounts", json={"name": "m5-key", "provider": "openai-key"}).status_code == 200
        assert c.post("/api/accounts/m5-key/key", json={"key": "sk-FAKE-M5"}).status_code == 200
        st = c.get("/api/accounts/m5-key/status").json()["status"]
        assert "API key stored" in st, st
        accts = c.get("/api/accounts").json()["accounts"]
        assert not any("FAKE" in str(x) for x in accts), accts
        print("API-key account via API (key never returned): ok")

        # accounts: official sign-in as a job; wait for its link, then cancel
        assert c.post("/api/accounts", json={"name": "m5-sub", "provider": "claude"}).status_code == 200
        job = c.post("/api/accounts/m5-sub/login").json()
        j = until(lambda: (x := c.get(f"/api/jobs/{job['id']}").json())["urls"] and x, timeout=900,
                  what="sign-in link")
        assert any(u.startswith("https://") for u in j["urls"]), j
        print("sign-in job shows the official link:", j["urls"][-1][:60] + "…")
        c.delete(f"/api/jobs/{job['id']}")
        until(lambda: c.get(f"/api/jobs/{job['id']}").json()["done"], what="job cancelled")
        print("sign-in cancelled without logging in: ok")

        # notifications: secrets stay masked, and a masked value isn't overwritten
        assert c.put("/api/notify/slack", json={"webhook": "https://hooks.slack.com/services/SECRET"}).status_code == 200
        n = c.get("/api/notify").json()
        assert n["channels"]["slack"]["webhook"] == "•••", n
        assert c.put("/api/notify/slack", json={"webhook": "•••"}).status_code == 200
        from hangar import notify
        assert notify.load()["slack"]["webhook"].endswith("SECRET")
        assert c.put("/api/notify/ntfy", json={"url": "not a url"}).status_code == 400
        print("notification settings (masked secrets): ok")

        s = c.get("/api/settings").json()
        assert s["local_url"].endswith(str(PORT)) and not s["lan_active"], s

        assert c.delete("/api/agents/m5-ui").status_code == 200
        assert c.get("/api/agents/m5-ui").status_code == 404
        print("delete via API: ok")
        print("PASS")
    finally:
        eng.destroy("m5-ui")
        for name in ("m5-key", "m5-sub"):
            c.delete(f"/api/accounts/{name}")
        server.should_exit = True


if __name__ == "__main__":
    main()
