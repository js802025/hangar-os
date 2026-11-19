"""Desktop-control MCP server for a Hangar space.

Runs inside the space's container and drives its private X display. Several
agents can share it: each harness sends `X-Hangar-Agent: <name>`, and that
agent gets its own mouse and keyboard (X multi-pointer, see xinput.py), its own
browser tab, its own reports and messages. Served over streamable HTTP at
:8765/mcp. Run with `--stdio` for stdio transport (single agent).

Every HTTP route except /healthz needs `Authorization: Bearer $HANGAR_AGENT_TOKEN`
(when that's set), so another agent can't drive this desktop through the
host's published port.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anyio
import httpx
import mss
from mcp.server.fastmcp import Context, FastMCP, Image
from PIL import Image as PILImage
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

HOME = Path.home()
STATE = HOME / ".hangar"
REPORTS = STATE / "reports.jsonl"
SPACE = os.environ.get("HANGAR_AGENT", "agent")          # the space's name (and its first agent's)
DEFAULT_AGENT = os.environ.get("HANGAR_AGENT_NAME", SPACE)


def who(ctx: Context | None) -> str:
    """Which agent in this space is calling (X-Hangar-Agent header)."""
    try:
        req = ctx.request_context.request
        name = req.headers.get("x-hangar-agent", "") if req is not None else ""
    except (AttributeError, LookupError, ValueError):
        name = ""
    name = "".join(ch for ch in name.lower() if ch.isalnum() or ch == "-")[:31]
    return name or DEFAULT_AGENT


_input = None


def inp():
    """Per-agent input (created lazily: needs the X display)."""
    global _input
    if _input is None:
        from hangar_desktop.xinput import Input
        _input = Input()
    return _input

mcp = FastMCP(
    "hangar-desktop",
    instructions=(
        "You work for the user inside Hangar. Do the work the cheapest reliable way: (1) normal "
        "work: run_command, read_file, write_file, web_search, web_fetch; (2) browser use for "
        "sites that need input: Hangar's browser with tab_open / tab_read (accessibility tree with "
        "[ref=eN]) / tab_click / tab_type / tab_run (Playwright code), in the background with no "
        "desktop; (3) computer use only for the smallest part nothing else can do: app_snapshot / "
        "app_click / app_type for desktop apps, browser_* for the desktop's browser, and last "
        "screenshot + click/type. "
        "Coordinates are screen pixels from the top-left. To sign in to a site, open "
        "its sign-in page and call request_login; for verification codes call request_code. "
        "Never ask for or type passwords yourself. Call `report` to post progress, "
        "and `report(final=True)` with a summary when you finish or get stuck."
    ),
    host=os.environ.get("HANGAR_MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("HANGAR_MCP_PORT", "8765")),
)


def _xdo(*args: str) -> str:
    out = subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def _paused() -> bool:
    # The daemon drops this file while the user has taken over the desktop.
    return (STATE / "paused").exists()


def _guard() -> None:
    if _paused():
        raise RuntimeError(
            "The user has taken control of this desktop. Wait and try again later."
        )


def _screen_size() -> tuple[int, int]:
    w, h = _xdo("getdisplaygeometry").split()
    return int(w), int(h)


@mcp.tool()
def screenshot() -> Image:
    """Capture the whole desktop as a PNG. Computer use is the last resort: prefer
    run_command / web_fetch, then browser_snapshot or app_snapshot, which read the
    screen as text."""
    return Image(data=_grab_png(), format="png")


def _grab_png(max_width: int = 0) -> bytes:
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])
        img = PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if max_width and img.width > max_width:
        img.thumbnail((max_width, max_width * img.height // img.width))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=not max_width)
    return buf.getvalue()


@mcp.tool()
def screen_info(ctx: Context) -> dict:
    """Screen size, your mouse position, and the window your keyboard is in."""
    w, h = _screen_size()
    me = who(ctx)
    i = inp()
    i.devices(me)
    x, y = i.pointers().get(me, (0, 0))
    wid = i.focus.get(me)
    try:
        active = _xdo("getactivewindow", "getwindowname")
    except RuntimeError:
        active = ""
    return {"width": w, "height": h, "mouse": [x, y], "you": me, "active_window": active,
            "your_window": i.title(wid) if wid else "", "other_agents": sorted(set(i.devs) - {me})}


def _input_call(fn, *a):
    from hangar_desktop.xinput import WindowBusy
    try:
        return fn(*a)
    except WindowBusy as e:
        raise RuntimeError(str(e))


@mcp.tool()
def click(x: int, y: int, ctx: Context, button: str = "left", clicks: int = 1) -> str:
    """Move your mouse to (x, y) and click. button: left | middle | right. Clicking a
    window also puts your keyboard in it. Only when browser_click / app_click can't."""
    _guard()
    btn = {"left": 1, "middle": 2, "right": 3}[button]
    _input_call(inp().click, who(ctx), x, y, btn, clicks)
    return f"clicked {button} x{clicks} at ({x}, {y})"


@mcp.tool()
def move_mouse(x: int, y: int, ctx: Context) -> str:
    """Move your mouse to (x, y) without clicking (hover)."""
    _guard()
    inp().move(who(ctx), x, y)
    return f"mouse at ({x}, {y})"


@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int, ctx: Context) -> str:
    """Press the left button at (x1, y1), drag to (x2, y2) and release."""
    _guard()
    _input_call(inp().drag, who(ctx), x1, y1, x2, y2)
    return f"dragged ({x1}, {y1}) -> ({x2}, {y2})"


@mcp.tool()
def type_text(text: str, ctx: Context) -> str:
    """Type text into the window your keyboard is in (click it first). To write a
    file, use write_file instead."""
    _guard()
    _input_call(inp().type_text, who(ctx), text)
    return f"typed {len(text)} characters"


@mcp.tool()
def key(keys: str, ctx: Context) -> str:
    """Press keys, space-separated, xdotool style: 'Return', 'ctrl+l', 'ctrl+shift+t Tab'."""
    _guard()
    _input_call(inp().key, who(ctx), keys)
    return f"pressed {keys}"


@mcp.tool()
def scroll(ctx: Context, direction: str = "down", amount: int = 3, x: int | None = None, y: int | None = None) -> str:
    """Scroll up/down/left/right by `amount` wheel clicks, optionally at (x, y)."""
    _guard()
    btn = {"up": 4, "down": 5, "left": 6, "right": 7}[direction]
    _input_call(inp().scroll, who(ctx), btn, amount, x, y)
    return f"scrolled {direction} {amount}"


APPS = {
    "browser": ["hangar-browser"],
    "terminal": ["xterm"],
    "editor": ["mousepad"],
}


@mcp.tool()
def open_app(name: str, args: str = "") -> str:
    """Open an app on the desktop. name: 'browser' (args = URL), 'terminal', 'editor'
    (a text editor; args = file), or any command on PATH (e.g. 'libreoffice --writer').
    Returns once its window appears or after ~10s. Then use app_snapshot to read it."""
    _guard()
    cmd = APPS.get(name, [name]) + shlex.split(args)
    before = set(_windows())
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        if set(_windows()) - before:
            time.sleep(0.8)  # let it paint
            return f"opened {name}"
    return f"started {name} (no new window seen yet; take a screenshot)"


def _windows() -> list[str]:
    out = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "."],
                         capture_output=True, text=True)
    return out.stdout.split()


def _a11y(*args: str, timeout: int = 30) -> str:
    r = subprocess.run(["hangar-a11y", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else "accessibility call failed")
    return r.stdout.strip()


@mcp.tool()
def app_snapshot(app: str = "") -> str:
    """Read desktop apps (not web pages) as numbered elements: buttons, menus, text
    fields, with their values. Like browser_snapshot, for GTK/Qt apps and LibreOffice.
    `app` filters by app name (e.g. "soffice", "mousepad"). Act on numbers with app_click /
    app_type. Much more reliable than screenshot + click when it works."""
    return _a11y("snapshot", app)


@mcp.tool()
def app_click(ref: int) -> str:
    """Click / activate element [ref] from the latest app_snapshot."""
    _guard()
    return _a11y("click", str(ref))


@mcp.tool()
def app_type(ref: int, text: str, replace: bool = False) -> str:
    """Type text into element [ref] (focuses it first). replace=True swaps the field's
    whole text instead of typing at the cursor."""
    _guard()
    return _a11y("set" if replace else "type", str(ref), text, timeout=120)


@mcp.tool()
def app_read(ref: int) -> str:
    """The full text of element [ref] (e.g. a document or a long field)."""
    return _a11y("read", str(ref))


@mcp.tool()
def read_clipboard() -> str:
    """Return the current clipboard text."""
    out = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, text=True)
    return out.stdout


@mcp.tool()
def wait(seconds: float = 1.0) -> str:
    """Wait for the screen to settle (max 30s)."""
    time.sleep(min(max(seconds, 0), 30))
    return f"waited {seconds}s"


@mcp.tool()
def report(message: str, ctx: Context, status: str = "working", final: bool = False) -> str:
    """Post a status update to your user. status: working | waiting_on_user | done | error.
    Use final=True with a summary when the task is finished or you're stuck."""
    return _report(message, status, final, who(ctx))


def _report(message: str, status: str, final: bool, by: str) -> str:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": SPACE,
        "who": by,
        "status": status,
        "final": final,
        "message": message,
    }
    STATE.mkdir(parents=True, exist_ok=True)
    with REPORTS.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    daemon = os.environ.get("HANGAR_DAEMON_URL")
    if daemon:
        try:
            httpx.post(f"{daemon}/api/agents/{entry['agent']}/reports", json=entry,
                       headers={"Authorization": f"Bearer {os.environ.get('HANGAR_AGENT_TOKEN', '')}"},
                       timeout=5)
        except httpx.HTTPError as e:
            return f"report saved locally (daemon unreachable: {e})"
    return "report sent"


def _daemon(method: str, path: str, **kw):
    daemon = os.environ.get("HANGAR_DAEMON_URL")
    if not daemon:
        raise RuntimeError("no Hangar daemon configured, so messaging isn't available")
    r = httpx.request(method, f"{daemon}/api/agents/{SPACE}{path}", timeout=kw.pop("timeout", 15),
                      headers={"Authorization": f"Bearer {os.environ.get('HANGAR_AGENT_TOKEN', '')}"}, **kw)
    if r.status_code >= 400:
        raise RuntimeError(r.json().get("error", r.text) if "json" in r.headers.get("content-type", "") else r.text)
    return r.json()


@mcp.tool()
def send_message(to: str, text: str, ctx: Context) -> str:
    """Message a teammate in this space (by name), or "pilot" (the main agent,
    who coordinates all spaces and can pass things to other spaces' agents)."""
    _daemon("POST", "/messages", json={"from": who(ctx), "to": to, "text": text})
    return f"sent to {to}"


@mcp.tool()
def read_messages(ctx: Context, wait_seconds: int = 0) -> str:
    """Your new messages (from teammates, the pilot or the user). wait_seconds > 0
    waits up to that long (max 50) for one to arrive."""
    msgs = _daemon("GET", f"/messages?for={who(ctx)}&wait={min(max(wait_seconds, 0), 50)}", timeout=60)
    if not msgs:
        return "No new messages."
    return "\n".join(f"[{m['ts'][11:19]}] {m['from']}: {m['text']}" for m in msgs)


@mcp.tool()
def team(ctx: Context) -> str:
    """Who else is in this space, what they're doing, and their latest report."""
    info = _daemon("GET", "/team")
    me = who(ctx)
    lines = []
    for a in info["agents"]:
        tag = " (you)" if a["name"] == me else ""
        lines.append(f"- {a['name']}{tag}: {a.get('role') or 'agent'}, {a['state']}"
                     + (f" | task: {a['task'][:120]}" if a.get("task") else "")
                     + (f" | latest: {a['report'][:160]}" if a.get("report") else ""))
    return "\n".join(lines) or "You're the only agent here."


@mcp.tool()
def notes_search(query: str = "") -> str:
    """Search the user's notes (Markdown, Obsidian-style [[links]]) that are shared
    with agents; an empty query lists the most recent. Your own space's folder is
    Agents/<space>/; keep findings, plans and summaries there for the user."""
    r = _daemon("GET", "/notes", params={"q": query} if query else None)
    if not r["notes"]:
        return f"No notes found (you can see: {r['scope']})."
    return "\n".join(f"- {n['path']}: {n.get('snippet') or n['preview']}"[:240] for n in r["notes"][:25])


@mcp.tool()
def notes_read(path: str) -> str:
    """Read one note (path from notes_search), with what links to it."""
    n = _daemon("GET", "/notes/read", params={"path": path})
    back = ", ".join(b["path"] for b in n["backlinks"]) or "none"
    return f"{n['text']}\n\n---\nlinks: {', '.join(n['links']) or 'none'} | linked from: {back}"


@mcp.tool()
def notes_write(path: str, text: str, ctx: Context, append: bool = False) -> str:
    """Write a Markdown note in this space's folder (Agents/<space>/<path>); the user
    sees it in their Notes app. Link other notes with [[Title]]. append=True adds
    to the end instead of replacing."""
    n = _daemon("POST", "/notes/write", json={"path": path, "text": text, "append": append, "who": who(ctx)})
    return f"saved {n['path']}"


from hangar_desktop import browser as _browser  # noqa: E402
from hangar_desktop import vault_tools as _vault  # noqa: E402

from hangar_desktop import inapp as _inapp  # noqa: E402
from hangar_desktop import work_tools as _work  # noqa: E402

_work.register(mcp)
if _inapp.available():                 # Hangar's in-app browser (no desktop needed)
    _inapp.register(mcp, who)
_browser.register(mcp, _guard, who)
_vault.register(mcp, who)

if os.environ.get("HANGAR_COMPUTER") == "mac":    # Hangar for Mac: the screen is a macOS VM
    from hangar_desktop import mac_screen
    mac_screen.register(mcp, _guard, who)
    _grab_png = mac_screen.grab_png               # noqa: F811 - thumbnails come from the VM too


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "paused": _paused()})


@mcp.custom_route("/screenshot.png", methods=["GET"])
async def screenshot_png(request: Request) -> Response:
    """For the daemon's live thumbnails. ?w=<max width> scales it down."""
    w = int(request.query_params.get("w", "0") or 0)
    return Response(await anyio.to_thread.run_sync(_grab_png, w), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@mcp.custom_route("/pointers", methods=["GET"])
async def pointers(_: Request) -> JSONResponse:
    """Each agent's cursor, so the viewer can label them."""
    if _input is None:
        return JSONResponse({})
    return JSONResponse({a: list(p) for a, p in (await anyio.to_thread.run_sync(_input.pointers)).items()})


@mcp.custom_route("/vault/fill", methods=["POST"])
async def vault_fill(request: Request) -> JSONResponse:
    """Called by the daemon (never by the agent) after the user approves."""
    b = await request.json()
    try:
        res = await _vault.fill(str(b.get("site", "")), str(b.get("username", "")),
                                str(b.get("password", "")), str(b.get("code", "")), str(b.get("agent", "")))
        return JSONResponse({"ok": True, **res})
    except Exception as e:   # report the reason, never the values
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)


class TokenAuth:
    """ASGI middleware: Bearer token on everything but /healthz."""

    def __init__(self, app, token: str) -> None:
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(auth, b"Bearer " + self.token):
                await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def report_cli() -> None:
    """`hangar-report <status> <message> [--final]` for scripts (final unless status is working)."""
    if len(sys.argv) < 3:
        sys.exit("usage: hangar-report <working|done|error|waiting_on_user> <message>")
    status, message = sys.argv[1], " ".join(sys.argv[2:])
    print(_report(message, status, status != "working", DEFAULT_AGENT))


def main() -> None:
    if "--stdio" in sys.argv:
        mcp.run("stdio")
        return
    import uvicorn
    app = mcp.streamable_http_app()
    token = os.environ.get("HANGAR_AGENT_TOKEN", "")
    if token:
        app = TokenAuth(app, token)
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port, log_level="warning")


if __name__ == "__main__":
    main()
