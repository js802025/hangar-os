"""hangar daemon: one local server that the CLI, the Spaces UI and agents talk to.

  GET  /api/agents                        agents with live state and last report
  GET  /api/agents/{name}                 one agent
  GET  /api/agents/{name}/screenshot.png  live thumbnail (?w=640)
  GET  /api/agents/{name}/logs            ?service=agent|mcp|... &tail=200
  GET  /api/agents/{name}/reports         every report the daemon received
  POST /api/agents/{name}/reports         agents post here, with their own token
  POST /api/agents/{name}/run             {"task": "..."}
  POST /api/agents/{name}/takeover | release | stop | start
  GET  /api/events                        server-sent events: reports, agent start/stop
  GET  /agents/{name}/view[?control=1]    noVNC page, proxied (websocket too)
  *    /agents/{name}/mcp                 the agent's MCP server, proxied

Everything except /healthz, /login and agents' report posts needs the admin
token, as `Authorization: Bearer` or the cookie /login sets. Agents' ports
need their own tokens, which only the daemon (and each agent itself) holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx
import websockets
from docker.errors import NotFound
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from pathlib import Path

from starlette.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                                 RedirectResponse, Response, StreamingResponse)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from hangar import auth, manage_api, notify, vault_api
from hangar.engine import DAEMON_PORT, LABEL, Agent, Engine

HOST = os.environ.get("HANGAR_DAEMON_HOST", "127.0.0.1")
COOKIE = "hangar_token"
STATUSES = {"working", "waiting_on_user", "done", "error"}
REPORTS_DIR = auth.STATE_DIR / "reports"
UI_DIR = Path(__file__).parent / "ui"


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Hub:
    """Fans events out to every /api/events subscriber."""

    def __init__(self) -> None:
        self.subs: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event: dict) -> None:
        for q in list(self.subs):
            if q.qsize() < 500:
                q.put_nowait(event)

    def publish_threadsafe(self, event: dict) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.publish, event)


eng = Engine()
hub = Hub()
_shots: dict[tuple[str, int], tuple[float, bytes]] = {}


# -- helpers ---------------------------------------------------------------------

def agent_dict(a: Agent) -> dict:
    st = eng.state(a)
    return {"name": a.name, "state": st["state"], "status": a.status, "flavor": a.flavor,
            "model": a.model, "account": a.account, "task": st["task"], "report": st["report"],
            "created": a.created, "shares": a.shares, "view": f"/agents/{a.name}/view",
            "mcp": f"/agents/{a.name}/mcp"}


async def get_agent(name: str) -> Agent | None:
    try:
        return await run_in_threadpool(eng.get, name)
    except NotFound:
        return None


def not_found(name: str) -> JSONResponse:
    return JSONResponse({"error": f"no agent named '{name}'"}, status_code=404)


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:] if h.startswith("Bearer ") else None


class AdminAuth:
    """ASGI middleware: the admin token, except on the few open routes."""

    OPEN = {"/healthz", "/login", "/favicon.svg"}

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope.get("method", "GET")
        if (path in self.OPEN or path.startswith("/ui/") or vault_api.is_agent_route(path)
                or (method == "POST" and path.endswith("/reports"))):
            return await self.app(scope, receive, send)   # agent routes check agent tokens themselves
        headers = dict(scope["headers"])
        token = None
        if headers.get(b"authorization", b"").startswith(b"Bearer "):
            token = headers[b"authorization"][7:].decode()
        else:
            for part in headers.get(b"cookie", b"").decode().split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    token = v
        if not auth.check(token, auth.admin_token()):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401})
            if path == "/" or path.startswith("/app/") or path.startswith("/agents/") and path.endswith("/view"):
                return await RedirectResponse(f"/login?next={quote(path, safe='')}",
                                              status_code=303)(scope, receive, send)
            return await PlainTextResponse("unauthorized: run `hangar view` or pass the admin token",
                                           status_code=401)(scope, receive, send)
        return await self.app(scope, receive, send)


# -- API ---------------------------------------------------------------------------

async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def login(request: Request) -> Response:
    """`hangar view` opens /login?token=…&next=… once; the cookie does the rest."""
    token = request.query_params.get("token", "").strip()
    nxt = request.query_params.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    if not auth.check(token, auth.admin_token()):
        page = (UI_DIR / "login.html").read_text().replace("{{next}}", html.escape(nxt, quote=True))
        page = page.replace("{{error}}", "That token didn't match." if token else "")
        return HTMLResponse(page, status_code=401 if token else 200)
    r = RedirectResponse(nxt, status_code=303)
    r.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=30 * 86400)
    return r


async def list_agents(_: Request) -> JSONResponse:
    agents = await run_in_threadpool(eng.list)
    return JSONResponse(await asyncio.gather(*(run_in_threadpool(agent_dict, a) for a in agents)))


async def show_agent(request: Request) -> JSONResponse:
    a = await get_agent(request.path_params["name"])
    return JSONResponse(await run_in_threadpool(agent_dict, a)) if a else not_found(request.path_params["name"])


async def screenshot(request: Request) -> Response:
    name = request.path_params["name"]
    w = min(int(request.query_params.get("w", "0") or 0), 4000)
    hit = _shots.get((name, w))
    if hit and time.time() - hit[0] < 1.5:      # many viewers, one grab
        return Response(hit[1], media_type="image/png")
    a = await get_agent(name)
    if not a or not a.mcp_port or a.status != "running":
        return not_found(name)
    async with httpx.AsyncClient() as http:
        r = await http.get(f"http://127.0.0.1:{a.mcp_port}/screenshot.png", params={"w": w},
                           headers=a.auth_headers, timeout=15)
    if r.status_code != 200:
        return PlainTextResponse(f"screenshot failed: {r.status_code}", status_code=502)
    _shots[(name, w)] = (time.time(), r.content)
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "no-store"})


async def logs(request: Request) -> Response:
    name = request.path_params["name"]
    if not await get_agent(name):
        return not_found(name)
    service = request.query_params.get("service", "")
    tail = str(min(int(request.query_params.get("tail", "200")), 5000))
    if service:
        if not service.isidentifier():
            return PlainTextResponse("bad service", status_code=400)
        _, out = await run_in_threadpool(eng.exec, name, ["tail", "-n", tail,
                                                          f"/home/agent/.hangar/logs/{service}.log"])
    else:
        out = await run_in_threadpool(eng.logs, name, int(tail))
    return PlainTextResponse(out)


def _reports_file(name: str):
    return REPORTS_DIR / f"{name}.jsonl"


async def get_reports(request: Request) -> JSONResponse:
    """The agent's own log is the full record (it includes reports made while
    the daemon was down); fall back to what the daemon received."""
    name = request.path_params["name"]
    a = await get_agent(name)
    text = ""
    if a and a.status == "running":
        text = await run_in_threadpool(eng.read_file, name, "/home/agent/.hangar/reports.jsonl")
    if not text:
        try:
            text = _reports_file(name).read_text()
        except OSError:
            text = ""
    out = []
    for line in text.splitlines()[-500:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return JSONResponse(out)


async def post_report(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    if not auth.check(bearer(request), auth.agent_token(name)):
        return JSONResponse({"error": "bad agent token"}, status_code=401)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "expected JSON"}, status_code=400)
    status = body.get("status") if body.get("status") in STATUSES else "working"
    report = {"ts": datetime.now(timezone.utc).isoformat(), "agent": name, "status": status,
              "final": bool(body.get("final")), "message": str(body.get("message", ""))[:4000]}
    auth.private_dir(REPORTS_DIR)
    with _reports_file(name).open("a") as f:
        f.write(json.dumps(report) + "\n")
    hub.publish({"type": "report", **report})
    log(f"report {name} [{status}{' final' if report['final'] else ''}] {report['message'][:200]}")
    if notify.should_notify(report):
        asyncio.get_running_loop().run_in_executor(None, _notify, report)
    return JSONResponse({"ok": True})


def _notify(report: dict) -> None:
    title = notify.title_for(report["agent"], report["status"])
    res = notify.send(title, report["message"], report["status"])
    log(f"notify: {title}" + (f" -> {res}" if res else " (no channels configured)"))


def action(fn):
    async def endpoint(request: Request) -> JSONResponse:
        name = request.path_params["name"]
        if not await get_agent(name):
            return not_found(name)
        try:
            body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
            await run_in_threadpool(fn, name, body)
        except (RuntimeError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        hub.publish({"type": "agent", "agent": name, "action": fn.__name__})
        return JSONResponse({"ok": True})
    return endpoint


def takeover(name, _):
    eng.pause(name)


def release(name, _):
    eng.resume(name)


def run(name, body):
    if not body.get("task"):
        raise ValueError("missing task")
    eng.run_task(name, body["task"])


def stop(name, _):
    eng.stop(name)


def start(name, _):
    eng.start(name)


async def events(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue()
    hub.subs.add(q)

    async def stream():
        try:
            yield ": hello\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hub.subs.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# -- proxies -----------------------------------------------------------------------

async def view(request: Request) -> Response:
    name = request.path_params["name"]
    control = request.query_params.get("control") == "1"
    # noVNC resolves its websocket `path` (default "websockify") relative to the
    # page, so this lands on /agents/{name}/vnc/websockify.
    return RedirectResponse(f"/agents/{quote(name)}/vnc/vnc.html?autoconnect=1&resize=scale&reconnect=1"
                            f"&view_only={0 if control else 1}")


async def vnc_static(request: Request) -> Response:
    """noVNC's own HTML/JS, fetched from the agent's websockify."""
    a = await get_agent(request.path_params["name"])
    if not a or not a.novnc_port:
        return not_found(request.path_params["name"])
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(f"http://127.0.0.1:{a.novnc_port}/{request.path_params['path']}",
                               headers=a.vnc_auth_header, timeout=15)
    except httpx.HTTPError:
        # The desktop is still starting; this page retries until it's up.
        return HTMLResponse('<meta http-equiv="refresh" content="1"><body style="background:#07080b;color:#8a92a6;'
                            'font:14px system-ui;display:grid;place-items:center;height:90vh">Starting the desktop…',
                            status_code=503)
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/octet-stream"))


async def vnc_ws(ws: WebSocket) -> None:
    """Pipe the browser's noVNC websocket to the agent's websockify."""
    origin = ws.headers.get("origin")
    if origin and urlsplit(origin).netloc != ws.headers.get("host"):
        await ws.close(code=4403)            # another site trying to open our socket
        return
    a = await get_agent(ws.path_params["name"])
    if not a or not a.novnc_port:
        await ws.close(code=4404)
        return
    asked = ws.scope.get("subprotocols") or []
    try:
        up = await websockets.connect(f"ws://127.0.0.1:{a.novnc_port}/websockify",
                                      additional_headers=a.vnc_auth_header,
                                      subprotocols=asked or None, max_size=None, open_timeout=10)
    except (OSError, websockets.WebSocketException):
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=up.subprotocol)

    async def browser_to_agent():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            await up.send(m["bytes"] if m.get("bytes") is not None else m.get("text", ""))

    async def agent_to_browser():
        async for m in up:
            await (ws.send_bytes(m) if isinstance(m, bytes) else ws.send_text(m))

    tasks = [asyncio.create_task(browser_to_agent()), asyncio.create_task(agent_to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await up.close()
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


MCP_FORWARD = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
_mcp_http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))


async def mcp_proxy(request: Request) -> Response:
    """Streamable-HTTP MCP, passed through (SSE responses stream as they come)."""
    a = await get_agent(request.path_params["name"])
    if not a or not a.mcp_port:
        return not_found(request.path_params["name"])
    headers = {k: v for k, v in request.headers.items() if k in MCP_FORWARD} | a.auth_headers
    req = _mcp_http.build_request(request.method, f"http://127.0.0.1:{a.mcp_port}/mcp",
                                  headers=headers, content=await request.body())
    up = await _mcp_http.send(req, stream=True)
    keep = {k: v for k, v in up.headers.items() if k in ("content-type", "mcp-session-id", "cache-control")}

    async def body():
        try:
            async for chunk in up.aiter_raw():
                yield chunk
        finally:
            await up.aclose()

    return StreamingResponse(body(), status_code=up.status_code, headers=keep)


# -- container events -> /api/events ----------------------------------------------------

def watch_engine() -> None:
    """Forward agent container start/stop/destroy from the engine's event stream."""
    while True:
        try:
            for ev in Engine().client.events(decode=True, filters={"type": "container", "label": LABEL}):
                act = ev.get("Action") or ev.get("status")
                if act in ("start", "die", "destroy", "create"):
                    name = ev.get("Actor", {}).get("Attributes", {}).get(LABEL)
                    hub.publish_threadsafe({"type": "agent", "agent": name, "action": act})
        except Exception as e:   # engine restarted, socket hiccup: retry
            log(f"engine events: {e}; retrying")
            time.sleep(3)


@contextlib.asynccontextmanager
async def lifespan(app):
    hub.loop = asyncio.get_running_loop()
    threading.Thread(target=watch_engine, daemon=True).start()
    log(f"hangar daemon on http://{web.state.host}:{DAEMON_PORT}  (state: {auth.STATE_DIR})")
    yield
    await _mcp_http.aclose()


class UIFiles(StaticFiles):
    """The UI's files, always revalidated, so edits show up on reload."""

    def file_response(self, *a, **kw):
        r = super().file_response(*a, **kw)
        r.headers["Cache-Control"] = "no-cache"
        return r


async def index(_: Request) -> FileResponse:
    return FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})


A = "/api/agents/{name}"
web = Starlette(routes=[
    Route("/", index),
    Route("/app/{name}", index),
    Route("/favicon.svg", lambda r: FileResponse(UI_DIR / "favicon.svg")),
    Mount("/ui", UIFiles(directory=UI_DIR)),
    *manage_api.routes(eng),
    *vault_api.routes(eng, hub, log),
    Route("/healthz", healthz),
    Route("/login", login),
    Route("/api/agents", list_agents),
    Route(A, show_agent),
    Route(f"{A}/screenshot.png", screenshot),
    Route(f"{A}/logs", logs),
    Route(f"{A}/reports", get_reports, methods=["GET"]),
    Route(f"{A}/reports", post_report, methods=["POST"]),
    *(Route(f"{A}/{fn.__name__}", action(fn), methods=["POST"]) for fn in (takeover, release, run, stop, start)),
    Route("/api/events", events),
    Route("/agents/{name}/view", view),
    WebSocketRoute("/agents/{name}/vnc/websockify", vnc_ws),
    Route("/agents/{name}/vnc/{path:path}", vnc_static),
    Route("/agents/{name}/mcp", mcp_proxy, methods=["GET", "POST", "DELETE"]),
], lifespan=lifespan)
web.state.host = HOST
app = AdminAuth(web)


def serve(host: str | None = None, port: int = DAEMON_PORT) -> None:
    """LAN access (Settings → "Open from other devices") binds all interfaces;
    everything still needs the admin token."""
    import uvicorn
    host = host or ("0.0.0.0" if manage_api.load_settings().get("lan") else HOST)
    web.state.host = host
    auth.admin_token()   # create the secret up front
    pidf = auth.STATE_DIR / "daemon.pid"
    pidf.write_text(str(os.getpid()))
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning", ws_max_size=64 * 1024 * 1024)
    finally:
        pidf.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
