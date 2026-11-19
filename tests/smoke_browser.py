"""In-app browser smoke test: Hangar's browser, the daemon's relay, the agent's session.

No containers and no models: the browser host (QtWebEngine) runs here, the
daemon runs in a thread with two stand-in spaces (bb-a, bb-b), and the agent's
Playwright session (images/tab/repl.mjs) runs as it would in a space.

  1. Space bb-a's agent opens a page in a background tab, reads it as an
     accessibility tree with refs, clicks by ref and runs Playwright code.
  2. The tab lives in bb-a's own profile, and the Browser view lists it.
  3. Space bb-b sees none of bb-a's tabs, can't attach to or close them, and
     can't use browser-wide commands; a wrong token is refused.
  4. A login the user approved fills into bb-a's tab and reaches the site;
     on another site it's refused.

The pages come from a small local site, so no internet is needed.

Needs the browser extra and Node:  uv run --extra apps --extra browser python tests/smoke_browser.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TMP = Path(tempfile.mkdtemp(prefix="hangar-browser-"))
PORT, SITE = 7806, 7807
posts: list[dict] = []


class Site(BaseHTTPRequestHandler):
    """A search form and a sign-in form, so the test needs no internet."""
    PAGES = {
        "/": "<title>Shop</title><form action=/results><label>Search <input name=q></label>"
             "<button>Go</button></form>",
        "/login": "<title>Sign in</title><form method=post action=/login><input name=email type=email "
                  "autocomplete=username><input name=pass type=password><button>Sign in</button></form>",
    }

    def log_message(self, *a):
        pass

    def _html(self, body: str) -> None:
        data = f"<!doctype html><meta charset=utf-8>{body}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/results":
            q = parse_qs(u.query).get("q", [""])[0]
            return self._html(f"<title>Results</title><h1>Results for {q}</h1><ul><li>Road bike</li><li>Gravel bike</li></ul>")
        self._html(self.PAGES.get(u.path, "<title>404</title>"))

    def do_POST(self):
        body = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        posts.append({k: v[0] for k, v in body.items()})
        self._html("<title>Welcome</title><h1>Signed in</h1>")
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT=str(PORT), QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPL = ROOT / "images/tab/repl.mjs"

from hangar.apps import browser_host  # noqa: E402

CDP = browser_host.prepare()

from PySide6.QtCore import QCoreApplication, QEventLoop, Qt  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
host = browser_host.BrowserHost(CDP)

import uvicorn  # noqa: E402
import websockets  # noqa: E402

from hangar import auth, daemon  # noqa: E402
from hangar.engine import Agent  # noqa: E402

SPACES = ("bb-a", "bb-b")


async def fake_get(name):
    if name in SPACES:
        return Agent(name=name, container_id=name, status="running", image="i", novnc_port=None, mcp_port=None)
    return None
daemon.get_agent = fake_get


def pump(seconds: float, until=None) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 50)
        if until and until():
            return
        time.sleep(0.01)
    if until:
        raise TimeoutError("condition not met")


def run_in_thread(fn):
    box = {}

    def go():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    pump(240, lambda: not t.is_alive())
    if "e" in box:
        raise box["e"]
    return box.get("v")


def session(space: str, ops: list[dict]) -> list[dict]:
    """Run the agent's Playwright session (as in a space) for a list of operations."""
    env = {**os.environ, "HANGAR_AGENT": space, "HANGAR_DAEMON_URL": f"http://127.0.0.1:{PORT}",
           "HANGAR_AGENT_TOKEN": auth.agent_token(space)}
    lines = "".join(json.dumps({"id": i, **op}) + "\n" for i, op in enumerate(ops))
    out = run_in_thread(lambda: subprocess.run(["node", str(REPL)], input=lines, capture_output=True, text=True,
                                               env=env, timeout=200))
    return [json.loads(x) for x in out.stdout.splitlines() if x.startswith("{")]


async def raw_cdp(space: str, token: str, msgs: list[dict]) -> list[dict]:
    url = f"ws://127.0.0.1:{PORT}/api/agents/{space}/browser/cdp"
    out = []
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {token}"}) as ws:
        for m in msgs:
            await ws.send(json.dumps(m))
            while True:
                r = json.loads(await asyncio.wait_for(ws.recv(), 20))
                if r.get("id") == m["id"]:
                    out.append(r)
                    break
    return out


def main() -> None:
    site = ThreadingHTTPServer(("127.0.0.1", SITE), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(daemon.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    pump(20, lambda: server.started)
    try:
        # 1. browse as bb-a's agent
        res = session("bb-a", [{"op": "open", "url": f"http://127.0.0.1:{SITE}/"}])
        assert res[0]["ok"], res
        box = next(line.split("[ref=")[1].split("]")[0] for line in res[0]["result"].splitlines() if "textbox" in line)
        res = session("bb-a", [{"op": "switch", "index": 0},
                               {"op": "type", "ref": box, "text": "bikes", "submit": True},
                               {"op": "run", "code": "return await page.locator('li').allTextContents()"}])
        assert all(r["ok"] for r in res), res
        assert "Results for bikes" in res[1]["result"], res[1]
        assert "Road bike" in res[2]["result"] and "Gravel bike" in res[2]["result"], res[2]
        print("1. bb-a's agent: opened a tab, read refs, typed by ref, ran Playwright code: ok")

        # 2. it's bb-a's, in bb-a's profile, and the Browser view sees it
        tabs = host.list()
        assert [t["space"] for t in tabs] == ["bb-a"], tabs
        assert host.tabs[tabs[0]["id"]].view.page().profile().storageName() == "hangar-bb-a"
        target = tabs[0]["target"]
        print("2. the tab is in bb-a's own profile, listed for the Browser view: ok")

        # 3. bb-b can't see or touch it
        res = session("bb-b", [{"op": "tabs"}])
        assert res[0]["ok"] and res[0]["result"] == "no tabs", res
        b_tok = auth.agent_token("bb-b")
        got = run_in_thread(lambda: asyncio.run(raw_cdp("bb-b", b_tok, [
            {"id": 1, "method": "Target.getTargets"},
            {"id": 2, "method": "Target.attachToTarget", "params": {"targetId": target, "flatten": True}},
            {"id": 3, "method": "Target.closeTarget", "params": {"targetId": target}},
            {"id": 4, "method": "Browser.close"},
            {"id": 5, "method": "Target.createBrowserContext"}])))
        assert not got[0]["result"]["targetInfos"], got[0]
        assert all("error" in g for g in got[1:]), got
        assert len(host.list()) == 1
        try:
            run_in_thread(lambda: asyncio.run(raw_cdp("bb-a", b_tok, [{"id": 1, "method": "Browser.getVersion"}])))
            raise AssertionError("bb-b's token opened bb-a's browser")
        except websockets.InvalidStatus:
            pass
        except websockets.ConnectionClosed:
            pass
        print("3. bb-b sees none of bb-a's tabs, can't attach, close or reach the browser; wrong token refused: ok")

        # 4. an approved login fills into bb-a's tab, only on its own site
        res = session("bb-a", [{"op": "switch", "index": 0},
                               {"op": "go", "url": f"http://localhost:{SITE}/login"},
                               {"op": "fill", "site": "example.com", "username": "u", "password": "p"},
                               {"op": "fill", "site": "localhost", "username": "ann@example.com", "password": "s3cret"},
                               {"op": "run", "code": "await page.getByRole('button', {name: 'Sign in'}).click(); "
                                                     "return await page.title()"}])
        assert not res[2]["ok"] and "not example.com" in res[2]["error"], res[2]
        assert res[3]["ok"] and json.loads(res[3]["result"])["filled"] == ["password", "username"], res[3]
        assert posts[-1] == {"email": "ann@example.com", "pass": "s3cret"}, posts
        print("4. an approved login fills bb-a's tab and reaches the site; refused on another site: ok")
        print("PASS")
    finally:
        server.should_exit = True
        host.close()


if __name__ == "__main__":
    main()
