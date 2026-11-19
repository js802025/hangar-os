"""Screen tools for a space whose computer is a Mac (HANGAR_COMPUTER=mac).

Only used on Hangar for Mac. The container still runs the agent, but its
screen is a macOS virtual machine on the host, so screenshot / click / type /
key / scroll / drag / move_mouse go through the daemon to that VM (see
hangar/macvm.py). Desktop-only tools that don't apply to a Mac are removed.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import Context, FastMCP, Image

SPACE = os.environ.get("HANGAR_AGENT", "agent")
LINUX_ONLY = ("open_app", "app_snapshot", "app_click", "app_type", "app_read", "read_clipboard")
REPLACED = ("screenshot", "screen_info", "click", "move_mouse", "drag", "type_text", "key", "scroll")
INSTRUCTIONS = (
    "You have a Linux container for the work itself and a Mac (a macOS VM) as your screen. "
    "Do everything you can without the Mac: run_command, read_file and write_file in your "
    "container for files, data and scripts, and web_search / web_fetch to find and read "
    "pages. Use the Mac (screenshot, click, type_text, key, scroll) only for what needs a "
    "Mac app or a page that needs real input. Coordinates are screenshot pixels from the "
    "top-left; use Mac shortcuts (cmd+space then an app's name and return to open it, cmd+l "
    "for a browser's address bar). Call `report` to post progress, and `report(final=True)` "
    "with a summary when you finish or get stuck. Never type passwords: if a site needs a "
    "sign-in, report waiting_on_user and ask the user to sign in on the Mac."
)


def _call(method: str, path: str, **kw) -> httpx.Response:
    daemon = os.environ.get("HANGAR_DAEMON_URL")
    if not daemon:
        raise RuntimeError("no Hangar daemon configured, so the Mac computer can't be reached")
    r = httpx.request(method, f"{daemon}/api/agents/{SPACE}{path}", timeout=kw.pop("timeout", 60),
                      headers={"Authorization": f"Bearer {os.environ.get('HANGAR_AGENT_TOKEN', '')}"}, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", r.text)
        except ValueError:
            msg = r.text
        raise RuntimeError(f"Mac computer: {msg}")
    return r


def grab_png(max_width: int = 0) -> bytes:
    return _call("GET", "/computer/screenshot", params={"w": max_width}).content


def _act(action: str, **body) -> dict:
    return _call("POST", f"/computer/{action}", json=body).json()


def register(mcp: FastMCP, guard, who) -> None:
    # The browser_* tools and the vault's fill drive the container's own Chromium,
    # which a Mac space doesn't show, so they go too (sign-ins: ask the user).
    browser = [t.name for t in mcp._tool_manager.list_tools() if t.name.startswith("browser_")]
    for name in (*REPLACED, *LINUX_ONLY, "request_login", "request_code", *browser):
        try:
            mcp.remove_tool(name)
        except Exception:  # noqa: BLE001 - not registered in this build
            pass
    mcp._mcp_server.instructions = INSTRUCTIONS

    @mcp.tool()
    def screenshot() -> Image:
        """Capture the Mac's screen as a PNG. Only for Mac apps or pages that need input:
        do everything else with run_command and web_fetch."""
        return Image(data=grab_png(), format="png")

    @mcp.tool()
    def screen_info(ctx: Context) -> dict:
        """What kind of computer this is, and whether it's ready."""
        return {"computer": "mac", "you": who(ctx), **_act("status")}

    @mcp.tool()
    def click(x: int, y: int, ctx: Context, button: str = "left", clicks: int = 1) -> str:
        """Click at (x, y). button: left | right. clicks: 1 or 2."""
        guard()
        _act("click", x=x, y=y, button=button, clicks=clicks)
        return f"clicked {button} x{clicks} at ({x}, {y})"

    @mcp.tool()
    def move_mouse(x: int, y: int, ctx: Context) -> str:
        """Move the mouse to (x, y) without clicking (hover)."""
        guard()
        _act("move", x=x, y=y)
        return f"mouse at ({x}, {y})"

    @mcp.tool()
    def drag(x1: int, y1: int, x2: int, y2: int, ctx: Context) -> str:
        """Press at (x1, y1), drag to (x2, y2) and release."""
        guard()
        _act("drag", x1=x1, y1=y1, x2=x2, y2=y2)
        return f"dragged ({x1}, {y1}) -> ({x2}, {y2})"

    @mcp.tool()
    def type_text(text: str, ctx: Context) -> str:
        """Type text into the focused field (click it first)."""
        guard()
        _act("type", text=text)
        return f"typed {len(text)} characters"

    @mcp.tool()
    def key(keys: str, ctx: Context) -> str:
        """Press keys, space-separated: 'cmd+space', 'return', 'cmd+shift+t tab'."""
        guard()
        _act("key", keys=keys)
        return f"pressed {keys}"

    @mcp.tool()
    def scroll(ctx: Context, direction: str = "down", amount: int = 3, x: int | None = None,
               y: int | None = None) -> str:
        """Scroll up or down by `amount` steps, optionally at (x, y)."""
        guard()
        _act("scroll", direction=direction, amount=amount, x=x, y=y)
        return f"scrolled {direction} {amount}"
