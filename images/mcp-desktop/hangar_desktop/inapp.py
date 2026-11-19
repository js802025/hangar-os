"""tab_*: Hangar's in-app browser, the way Aside drives pages.

Most web work needs no desktop: agents browse in tabs that live inside Hangar
(QtWebEngine, in each space's own profile, in the background; see
docs/browser.md). A page is read as an accessibility tree with refs
(`[ref=e23]`), and acted on by ref or with Playwright code. Screenshots are only
a fallback. The Playwright session runs in this container (images/tab/repl.mjs)
and reaches the browser through the Hangar daemon, which shows this space only
its own tabs.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import subprocess
import threading

import anyio
from mcp.server.fastmcp import Context, FastMCP, Image

REPL = os.environ.get("HANGAR_TAB_REPL", "/opt/hangar/tab/repl.mjs")


class Session:
    """The Node Playwright session (started on first use, restarted if it dies)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.ids = itertools.count(1)
        self.waiting: dict[int, tuple[threading.Event, dict]] = {}
        self.lock = threading.Lock()

    def _start(self) -> None:
        self.proc = subprocess.Popen(["node", REPL], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            ev, box = self.waiting.pop(msg.get("id"), (None, None))
            if ev:
                box.update(msg)
                ev.set()
        for ev, box in list(self.waiting.values()):         # it died: fail what's waiting
            box.update(ok=False, error="the browser session stopped; try again")
            ev.set()

    def call(self, op: str, timeout: float = 120, **kw) -> str:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start()
            mid = next(self.ids)
            ev, box = threading.Event(), {}
            self.waiting[mid] = (ev, box)
            self.proc.stdin.write(json.dumps({"id": mid, "op": op, **kw}) + "\n")
            self.proc.stdin.flush()
        if not ev.wait(timeout):
            self.waiting.pop(mid, None)
            raise RuntimeError(f"tab_{op} took longer than {int(timeout)}s")
        if not box.get("ok"):
            raise RuntimeError(box.get("error", "failed"))
        return box["result"]


_session = Session()
_used: dict[str, float] = {}          # agent -> when it last used a tab


def recently_used(agent: str, within: float = 1800) -> bool:
    import time
    return time.time() - _used.get(agent, 0) < within


def fill(agent: str, site: str, username: str, password: str, code: str) -> dict:
    """Fill a login the user approved into the agent's in-app tab (see vault_tools)."""
    return json.loads(_session.call("fill", agent=agent, site=site, username=username, password=password, code=code))


def available() -> bool:
    return os.path.exists(REPL) and bool(os.environ.get("HANGAR_DAEMON_URL"))


def register(mcp: FastMCP, who) -> None:
    async def call(op: str, ctx: Context | None, **kw) -> str:
        import time
        agent = who(ctx)
        _used[agent] = time.time()
        return await anyio.to_thread.run_sync(lambda: _session.call(op, agent=agent, **kw))

    @mcp.tool()
    async def tab_open(url: str, ctx: Context = None) -> str:
        """Open a page in a new tab of Hangar's browser (in the background, in this space's
        own profile) and read it. Use this for websites that need input: forms, sign-ins,
        carts, pages that only work with JavaScript. To just read a page, web_fetch is cheaper."""
        return await call("open", ctx, url=url)

    @mcp.tool()
    async def tab_go(url: str, ctx: Context = None) -> str:
        """Go to a URL in your current tab, and read it."""
        return await call("go", ctx, url=url)

    @mcp.tool()
    async def tab_read(ctx: Context = None) -> str:
        """Read your current tab as an accessibility tree: roles, names and [ref=eN] refs to act on."""
        return await call("snapshot", ctx)

    @mcp.tool()
    async def tab_click(ref: str, ctx: Context = None) -> str:
        """Click element ref (e.g. "e23") from tab_read. Returns the page afterwards."""
        return await call("click", ctx, ref=ref)

    @mcp.tool()
    async def tab_type(ref: str, text: str, submit: bool = False, ctx: Context = None) -> str:
        """Fill the field ref with text (replacing what's there); submit=True presses Enter."""
        return await call("type", ctx, ref=ref, text=text, submit=submit)

    @mcp.tool()
    async def tab_select(ref: str, option: str, ctx: Context = None) -> str:
        """Choose option (its label or value) in the dropdown ref."""
        return await call("select", ctx, ref=ref, option=option)

    @mcp.tool()
    async def tab_run(code: str, ctx: Context = None) -> str:
        """Run Playwright code against your current tab, for anything the other tab_ tools don't
        cover: several steps at once, reading a table, waiting for something, a site's own API.
        In scope: page, context, browser, console, ref(id) (a locator for a [ref=…]). Use await;
        `return` a value or console.log it. Example:
          await ref("e23").fill("road bike"); await page.keyboard.press("Enter");
          await page.waitForSelector(".results"); return await page.locator(".price").allTextContents()"""
        return await call("run", ctx, code=code, timeout=180)

    @mcp.tool()
    async def tab_tabs(ctx: Context = None) -> str:
        """This space's tabs (yours marked *)."""
        return await call("tabs", ctx)

    @mcp.tool()
    async def tab_switch(index: int, ctx: Context = None) -> str:
        """Make tab `index` (from tab_tabs) your current tab, and read it."""
        return await call("switch", ctx, index=index)

    @mcp.tool()
    async def tab_close(ctx: Context = None) -> str:
        """Close your current tab."""
        return await call("close", ctx)

    @mcp.tool()
    async def tab_screenshot(ctx: Context = None) -> Image:
        """A picture of your current tab: only when its accessibility tree isn't enough
        (charts, canvases, images that matter)."""
        return Image(data=base64.b64decode(await call("screenshot", ctx)), format="png")
