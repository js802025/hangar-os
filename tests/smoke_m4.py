"""M4 smoke test: the daemon, agent tokens and notifications.

Runs a daemon on a test port with a throwaway state/config dir, a fake ntfy
server, and two agents, then checks:
  - the API and proxies refuse requests without the admin token,
  - an agent's MCP and noVNC ports refuse requests without its token,
  - an agent can't post reports as another agent,
  - a final report reaches the daemon, the event stream and ntfy,
  - screenshots, logs, takeover/release and the MCP proxy work,
  - the noVNC websocket proxy speaks VNC and refuses other sites' pages.
Usage:  uv run python tests/smoke_m4.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-m4-"))
PORT, NTFY_PORT = 7790, 7791
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT=str(PORT))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from hangar import auth, notify  # noqa: E402
from hangar.daemon import app  # noqa: E402
from hangar.engine import AgentSpec, Engine  # noqa: E402

D = f"http://127.0.0.1:{PORT}"
NAMES = ("m4-a", "m4-b")
pushes: list[dict] = []


class FakeNtfy(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        pushes.append({"path": self.path, "title": self.headers["Title"], "body": body})
        self.send_response(200)
        self.end_headers()


def until(pred, timeout=20, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if v := pred():
                return v
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise TimeoutError(what)


async def mcp_call(url: str, headers: dict, tool: str, args: dict) -> str:
    async with streamablehttp_client(url, headers=headers) as (r, w, _), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool(tool, args)
        return res.content[0].text


async def vnc_checks(name: str, admin: str) -> None:
    url = f"ws://127.0.0.1:{PORT}/agents/{name}/vnc/websockify"
    cookie = {"Cookie": f"hangar_token={admin}"}
    async with websockets.connect(url, additional_headers=cookie, subprotocols=["binary"]) as ws:
        banner = await asyncio.wait_for(ws.recv(), 10)
        assert bytes(banner).startswith(b"RFB 003."), banner
        print("noVNC websocket through the daemon: got", bytes(banner).strip().decode())
    for headers, why in (({}, "no token"), ({**cookie, "Origin": "https://evil.example"}, "foreign origin")):
        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                await asyncio.wait_for(ws.recv(), 5)
            raise AssertionError(f"websocket allowed with {why}")
        except (websockets.InvalidStatus, websockets.ConnectionClosed):
            print(f"websocket refused ({why}): ok")


def main() -> None:
    ntfy = ThreadingHTTPServer(("127.0.0.1", NTFY_PORT), FakeNtfy)
    threading.Thread(target=ntfy.serve_forever, daemon=True).start()
    notify.save({"ntfy": {"url": f"http://127.0.0.1:{NTFY_PORT}/hangar-test", "token": ""}})

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    until(lambda: httpx.get(f"{D}/healthz").status_code == 200, what="daemon up")

    admin = auth.admin_token()
    H = {"Authorization": f"Bearer {admin}"}
    eng = Engine()
    for n in NAMES:
        eng.destroy(n)
    events: list[dict] = []

    def follow_events():
        with httpx.stream("GET", f"{D}/api/events", headers=H, timeout=None) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    threading.Thread(target=follow_events, daemon=True).start()
    try:
        for n in NAMES:
            eng.create(AgentSpec(name=n))
        a, b = (eng.wait_ready(n, timeout=120) for n in NAMES)

        # -- auth -----------------------------------------------------------------
        assert httpx.get(f"{D}/api/agents").status_code == 401
        assert httpx.get(f"{D}/api/agents", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert httpx.post(f"{D}/agents/m4-a/mcp", json={}).status_code == 401
        listed = httpx.get(f"{D}/api/agents", headers=H).json()
        assert {x["name"] for x in listed} >= set(NAMES), listed
        print("daemon API needs the admin token: ok;", [(x["name"], x["state"]) for x in listed])

        assert httpx.post(a.mcp_url, json={}).status_code == 401
        assert httpx.get(f"http://127.0.0.1:{a.mcp_port}/screenshot.png").status_code == 401
        assert httpx.get(f"http://127.0.0.1:{a.novnc_port}/vnc.html").status_code == 401
        assert httpx.get(f"http://127.0.0.1:{a.novnc_port}/vnc.html", headers=b.vnc_auth_header).status_code == 401
        print("agent ports refuse requests without that agent's token: ok")

        # From inside agent B: B's token can't post as A (and B never sees A's).
        code, out = eng.exec("m4-b", ["bash", "-c",
            'curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $HANGAR_AGENT_TOKEN" '
            '-H "Content-Type: application/json" -d \'{"message":"spoof","status":"done","final":true}\' '
            '"$HANGAR_DAEMON_URL/api/agents/m4-a/reports"'])
        assert out.strip() == "401", f"spoofed report got {out!r}"
        print("agent B can't post reports as agent A: ok (from inside the container:", out.strip() + ")")

        # -- report: agent -> daemon -> events + ntfy ---------------------------------------
        out = asyncio.run(mcp_call(f"{D}/agents/m4-a/mcp", H, "report",
                                   {"message": "All done: found 3 results", "status": "done", "final": True}))
        assert out == "report sent", f"report tool says {out!r} (can the container reach the daemon?)"
        reports = httpx.get(f"{D}/api/agents/m4-a/reports", headers=H).json()
        assert reports[-1]["message"] == "All done: found 3 results", reports
        until(lambda: pushes, what="ntfy push")
        assert pushes[0]["title"] == "m4-a finished" and "3 results" in pushes[0]["body"], pushes
        until(lambda: any(e["type"] == "report" for e in events), what="report event")
        print("report via MCP proxy -> daemon -> event stream + ntfy:", pushes[0])
        assert not any("spoof" in json.dumps(r) for r in reports)

        # -- views, logs, takeover -----------------------------------------------------------
        png = httpx.get(f"{D}/api/agents/m4-a/screenshot.png?w=320", headers=H)
        assert png.status_code == 200 and png.content[:4] == b"\x89PNG", png.status_code
        print(f"thumbnail: {len(png.content)} bytes")
        assert httpx.get(f"{D}/api/agents/m4-a/logs?service=xvnc", headers=H).text.strip()
        r = httpx.get(f"{D}/login", params={"token": admin, "next": "/agents/m4-a/view"}, follow_redirects=True)
        assert r.status_code == 200 and "noVNC" in r.text, (r.status_code, r.text[:200])
        assert str(r.url).startswith(f"{D}/agents/m4-a/vnc/vnc.html?"), r.url
        print("login -> view -> noVNC page through the proxy: ok")
        asyncio.run(vnc_checks("m4-a", admin))

        assert httpx.post(f"{D}/api/agents/m4-a/takeover", headers=H).status_code == 200
        assert httpx.get(f"{D}/api/agents/m4-a", headers=H).json()["state"] == "taken over"
        out = asyncio.run(mcp_call(f"{D}/agents/m4-a/mcp", H, "click", {"x": 5, "y": 5}))
        assert "taken control" in out, out
        httpx.post(f"{D}/api/agents/m4-a/release", headers=H)
        print("takeover/release through the API: ok")

        eng.stop("m4-b")
        until(lambda: any(e.get("action") == "die" and e.get("agent") == "m4-b" for e in events),
              what="die event")
        print("container events reach the event stream: ok")
        print("PASS")
    finally:
        for n in NAMES:
            eng.destroy(n)
        server.should_exit = True
        ntfy.shutdown()


if __name__ == "__main__":
    main()
