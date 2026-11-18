"""Vault smoke test: an agent signs in to a (fake, local) website without ever
seeing the password.

A tiny login site runs on the host (reached as hangar-host:7794). The test
plays both sides: the agent (over its MCP server) and you (approving through
the daemon's API). Checks:
  - request_login waits for approval, then Hangar fills the form; the site
    receives the right username and password,
  - nothing the agent sees (tool results, snapshots) contains the password,
  - a standing "for 1 hour" approval fills the next time without asking,
  - on a different host (a lookalike), Hangar refuses to fill,
  - request_code fills a valid 2FA code,
  - denying works, another agent can't read the request, the audit log has no secrets.
Usage:  uv run python tests/smoke_vault.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

TMP = Path(tempfile.mkdtemp(prefix="hangar-vault-"))
PORT, SITE_PORT = 7793, 7794
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT=str(PORT))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from hangar import auth  # noqa: E402
from hangar.daemon import app, eng  # noqa: E402
from hangar.engine import AgentSpec  # noqa: E402
from hangar.vault import AUDIT, Vault, totp  # noqa: E402

D = f"http://127.0.0.1:{PORT}"
PASSWORD = "Tr0ub4dor-horse-STAPLE"
TOTP_KEY = "JBSWY3DPEHPK3PXP"
posts: list[dict] = []

LOGIN = """<!doctype html><title>Fake Login</title><h1>Sign in</h1>
<form method=post action=/login><label>Email <input name=email type=email></label>
<label>Password <input name=pass type=password></label><button>Sign in</button></form>"""
VERIFY = """<!doctype html><title>Verify</title><h1>Two-factor</h1>
<form method=post action=/verify><label>Code <input name=otp inputmode=numeric autocomplete=one-time-code></label>
<button>Verify</button></form>"""


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send(VERIFY if self.path.startswith("/verify") else LOGIN)

    def do_POST(self):
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        posts.append({"path": self.path, **form})
        self._send("<!doctype html><title>Welcome</title><h1>Signed in</h1>")


async def agent_session(url: str, headers: dict, fn):
    async with streamablehttp_client(url, headers=headers) as (r, w, _), ClientSession(r, w) as s:
        await s.initialize()

        async def call(tool, **kw) -> str:
            res = await s.call_tool(tool, kw)
            return "\n".join(c.text for c in res.content if c.type == "text")
        return await fn(call)


def main() -> None:
    site = ThreadingHTTPServer(("0.0.0.0", SITE_PORT), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"{D}/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    admin = httpx.Client(base_url=D, headers={"Authorization": f"Bearer {auth.admin_token()}"}, timeout=60)

    entry = Vault().add("hangar-host", "alice@example.com", PASSWORD, TOTP_KEY, label="Fake Site")
    for n in ("v-agent", "v-other"):
        eng.destroy(n)
    try:
        for n in ("v-agent", "v-other"):
            eng.create(AgentSpec(name=n))
        a = eng.wait_ready("v-agent", timeout=120)
        eng.wait_ready("v-other", timeout=120)
        seen: list[str] = []   # everything the agent's model would see

        def approve_when_pending(kind: str, body: dict, deny: bool = False) -> dict:
            for _ in range(60):
                reqs = [r for r in admin.get("/api/vault").json()["requests"]
                        if r["status"] == "pending" and r["kind"] == kind]
                if reqs:
                    r = reqs[0]
                    if deny:
                        return admin.post(f"/api/vault/requests/{r['id']}/deny").json()
                    return admin.post(f"/api/vault/requests/{r['id']}/approve", json=body).json()
                time.sleep(0.5)
            raise TimeoutError("no pending request")

        async def flow(call):
            loop = asyncio.get_running_loop()
            url = f"http://hangar-host:{SITE_PORT}"
            seen.append(await call("browser_navigate", url=f"{url}/login"))

            # 1. first login: needs approval
            approval = loop.run_in_executor(None, approve_when_pending, "login",
                                            {"entry": entry["id"], "scope": "hour"})
            msg = await call("request_login", site="hangar-host", reason="to read your inbox")
            seen.append(msg)
            res = await approval
            assert res["status"] == "filled", res
            assert "Filled the password and username" in msg, msg
            snap = await call("browser_snapshot")
            seen.append(snap)
            assert "(filled)" in snap and "alice@example.com" in snap, snap
            ref = next(line.split("]")[0][1:] for line in snap.splitlines() if 'button "Sign in"' in line)
            seen.append(await call("browser_click", ref=int(ref)))
            assert posts[-1] == {"path": "/login", "email": "alice@example.com", "pass": PASSWORD}, posts
            print("1. approved login filled; the site got the right credentials: ok")

            # 2. standing approval: fills without asking
            seen.append(await call("browser_navigate", url=f"{url}/login"))
            t = time.time()
            msg = await call("request_login", site="hangar-host", reason="again")
            seen.append(msg)
            assert "Filled" in msg and time.time() - t < 15, msg
            print("2. 'for 1 hour' approval fills again without asking: ok")

            # 3. lookalike host: refuse to fill
            seen.append(await call("browser_navigate", url=f"http://host.docker.internal:{SITE_PORT}/login"))
            msg = await call("request_login", site="hangar-host", reason="phish test")
            seen.append(msg)
            assert "Couldn't fill" in msg and "not hangar-host" in msg, msg
            print("3. wrong host (lookalike page): refused to fill:", msg[:90])

            # 4. verification code from the saved 2FA key
            seen.append(await call("browser_navigate", url=f"{url}/verify"))
            msg = await call("request_code", site="hangar-host", reason="2FA prompt")
            seen.append(msg)
            assert "Filled the code" in msg, msg
            snap = await call("browser_snapshot")
            ref = next(line.split("]")[0][1:] for line in snap.splitlines() if 'button "Verify"' in line)
            seen.append(await call("browser_click", ref=int(ref)))
            assert posts[-1]["otp"] in {totp(TOTP_KEY, time.time() - d) for d in (0, 30)}, posts[-1]
            print("4. request_code filled a valid 2FA code: ok")

            # 5. no saved login + user says no
            denial = loop.run_in_executor(None, approve_when_pending, "login", {}, True)
            msg = await call("request_login", site="example.org", reason="no such login")
            await denial
            seen.append(msg)
            assert "said no" in msg, msg
            print("5. denied request: ok")

        asyncio.run(agent_session(a.mcp_url, a.auth_headers, flow))

        everything = "\n".join(seen)
        assert PASSWORD not in everything and TOTP_KEY not in everything
        print("the agent never saw the password or 2FA key: ok")

        # Another agent can't read this agent's requests (or make them as it).
        rid = admin.get("/api/vault").json()["requests"][0]["id"]
        other = eng.get("v-other")
        r = httpx.get(f"{D}/api/agents/v-agent/vault/requests/{rid}", headers=other.auth_headers)
        assert r.status_code == 401, r.status_code
        assert httpx.get(f"{D}/api/vault", headers=other.auth_headers).status_code == 401
        print("other agents can't see requests or the vault: ok")

        log = AUDIT.read_text()
        assert PASSWORD not in log and TOTP_KEY not in log
        assert b"Tr0ub4dor" not in (TMP / "state/vault.enc").read_bytes()
        print("audit log and vault file contain no secrets: ok")
        print("PASS")
    finally:
        for n in ("v-agent", "v-other"):
            eng.destroy(n)
        server.should_exit = True
        site.shutdown()


if __name__ == "__main__":
    main()
