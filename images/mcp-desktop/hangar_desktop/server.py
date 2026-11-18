"""Desktop-control MCP server for a Hangar agent.

Runs inside the agent container and drives its private X display ($DISPLAY)
with xdotool. Served over streamable HTTP at :8765/mcp so both an in-container
harness and the host can connect. Run with `--stdio` for stdio transport.

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
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

HOME = Path.home()
STATE = HOME / ".hangar"
REPORTS = STATE / "reports.jsonl"

mcp = FastMCP(
    "hangar-desktop",
    instructions=(
        "You control a private Linux desktop (X11, Openbox). Take a screenshot "
        "to see it; coordinates are screen pixels from the top-left. "
        "For web pages prefer the browser_* tools: browser_navigate, then "
        "browser_snapshot gives numbered elements to browser_click/browser_type; "
        "fall back to screenshot + click for canvases and odd widgets. For desktop apps use "
        "app_snapshot / app_click / app_type the same way. To sign in to a site, open "
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
    """Capture the whole desktop as a PNG."""
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
def screen_info() -> dict:
    """Screen size, current mouse position and the active window's title."""
    w, h = _screen_size()
    loc = dict(kv.split(":") for kv in _xdo("getmouselocation").split()[:2])
    try:
        title = _xdo("getactivewindow", "getwindowname")
    except RuntimeError:
        title = ""
    return {"width": w, "height": h, "mouse": [int(loc["x"]), int(loc["y"])], "active_window": title}


@mcp.tool()
def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """Move the mouse to (x, y) and click. button: left | middle | right."""
    _guard()
    btn = {"left": "1", "middle": "2", "right": "3"}[button]
    _xdo("mousemove", "--sync", str(x), str(y), "click", "--repeat", str(clicks), "--delay", "80", btn)
    return f"clicked {button} x{clicks} at ({x}, {y})"


@mcp.tool()
def move_mouse(x: int, y: int) -> str:
    """Move the mouse to (x, y) without clicking (hover)."""
    _guard()
    _xdo("mousemove", "--sync", str(x), str(y))
    return f"mouse at ({x}, {y})"


@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int) -> str:
    """Press the left button at (x1, y1), drag to (x2, y2) and release."""
    _guard()
    _xdo("mousemove", "--sync", str(x1), str(y1), "mousedown", "1",
         "mousemove", "--sync", str(x2), str(y2), "mouseup", "1")
    return f"dragged ({x1}, {y1}) -> ({x2}, {y2})"


@mcp.tool()
def type_text(text: str) -> str:
    """Type text into the focused window."""
    _guard()
    _xdo("type", "--delay", "12", "--", text)
    return f"typed {len(text)} characters"


@mcp.tool()
def key(keys: str) -> str:
    """Press keys, xdotool style, space-separated: 'Return', 'ctrl+l', 'ctrl+shift+t Tab'."""
    _guard()
    _xdo("key", "--delay", "40", *keys.split())
    return f"pressed {keys}"


@mcp.tool()
def scroll(direction: str = "down", amount: int = 3, x: int | None = None, y: int | None = None) -> str:
    """Scroll up/down/left/right by `amount` wheel clicks, optionally at (x, y)."""
    _guard()
    btn = {"up": "4", "down": "5", "left": "6", "right": "7"}[direction]
    if x is not None and y is not None:
        _xdo("mousemove", "--sync", str(x), str(y))
    _xdo("click", "--repeat", str(amount), "--delay", "30", btn)
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
def report(message: str, status: str = "working", final: bool = False) -> str:
    """Post a status update to your user. status: working | waiting_on_user | done | error.
    Use final=True with a summary when the task is finished or you're stuck."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": os.environ.get("HANGAR_AGENT", "agent"),
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


from hangar_desktop import browser as _browser  # noqa: E402
from hangar_desktop import vault_tools as _vault  # noqa: E402

_browser.register(mcp, _guard)
_vault.register(mcp)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "paused": _paused()})


@mcp.custom_route("/screenshot.png", methods=["GET"])
async def screenshot_png(request: Request) -> Response:
    """For the daemon's live thumbnails. ?w=<max width> scales it down."""
    w = int(request.query_params.get("w", "0") or 0)
    return Response(await anyio.to_thread.run_sync(_grab_png, w), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@mcp.custom_route("/vault/fill", methods=["POST"])
async def vault_fill(request: Request) -> JSONResponse:
    """Called by the daemon (never by the agent) after the user approves."""
    b = await request.json()
    try:
        res = await _vault.fill(str(b.get("site", "")), str(b.get("username", "")),
                                str(b.get("password", "")), str(b.get("code", "")))
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
    print(report(message, status=status, final=status != "working"))


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
