"""`hangar mcp`: Hangar as an MCP server for your other agents (Claude Code,
Codex, Cursor, ...), over stdio, like cua-driver.

It runs on your machine as you (the same trust as the `hangar` CLI), so it
needs no token. It offers two ways to get computer work done without touching
your own screen:

  - Hand Hangar the job: `do_task` starts (or reuses) a space, with a Linux
    desktop or a Mac VM, and one of its agents does the task the Hangar way
    (normal work, then Hangar's browser, then the screen only where needed).
    You get its final report back; `look` shows its screen meanwhile.
  - Drive a Mac VM yourself, cua-style: `mac_screenshot`, `mac_click`,
    `mac_type`, `mac_key`, `mac_scroll`, `mac_drag`, on a macOS VM run by
    terminal-mac (see hangar/macvm.py). A VM lent to a space is left alone.

Add it to Claude Code:  claude mcp add hangar -- hangar mcp
(on Hangar for Mac use mac/bin/hangar).
"""

from __future__ import annotations

import json
import re
import secrets
import time

import anyio
import httpx
from mcp.server.fastmcp import Context, FastMCP, Image

from hangar import auth, daemonctl, macvm, pilot
from hangar.engine import DAEMON_PORT, LABEL, AgentSpec, Engine

REPORTS = "/home/agent/.hangar/reports.jsonl"
SHOT_WIDTH = 1280                       # screenshots are shrunk to this; clicks are scaled back
INSTRUCTIONS = (
    "Hangar runs computer work in its own spaces: isolated Linux desktops, or macOS virtual "
    "machines, never on the user's screen. Prefer do_task: describe the job and Hangar's agent "
    "does it (commands and web first, Hangar's browser for sites, the screen only where "
    "needed) and returns its report. Use computer='mac' only for work that needs a Mac app. "
    "To drive a Mac VM yourself, step by step like computer use: mac_screenshot, then mac_click / "
    "mac_type / mac_key / mac_scroll at that screenshot's pixel coordinates, and screenshot again "
    "to check. Never type the user's passwords: if a sign-in is needed, call mac_show and ask "
    "the user to sign in in the VM's window."
)

CLIENTS = {"claude-code": "Claude Code", "codex-mcp-client": "Codex", "cursor-vscode": "Cursor"}
_engine: Engine | None = None
_scale: dict[str, float] = {}           # vm -> real pixels per screenshot pixel


def eng() -> Engine:
    global _engine
    if _engine is None:
        try:
            e = Engine()
            e.client.ping()
        except Exception as ex:  # noqa: BLE001
            raise RuntimeError(f"Hangar can't reach its container engine: {ex}") from None
        _engine = e
    return _engine


def _thread(fn, *a):
    return anyio.to_thread.run_sync(lambda: fn(*a))


def _reports(space: str) -> list[dict]:
    out = []
    for line in eng().read_file(space, REPORTS).splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _fmt(r: dict, space: str) -> str:
    return f"[{r.get('status')}{', final' if r.get('final') else ''}] {r.get('who') or space}: {r.get('message', '')}"


def _default_model() -> str:
    o = pilot.load()
    return o["pilot"].get("model") or next((a["model"] for a in o["allowed"]), "")


def _wait(space: str, agent: str, seen: int, seconds: float) -> str:
    """Wait for the agent's final report (or a question for the user) after report number `seen`."""
    end = time.time() + max(0, seconds)
    started = time.time()
    while True:
        new = [r for r in _reports(space)[seen:] if (r.get("who") or space) == agent]
        done = next((r for r in new if r.get("final") or r.get("status") in ("waiting_on_user", "error")), None)
        if done:
            more = "" if done.get("final") else (
                "\nIt needs the user: call look() to see why; the user can take over with `hangar takeover "
                f"{space}`." if done.get("status") == "waiting_on_user" else "")
            return "\n".join(_fmt(r, space) for r in new) + more
        busy = eng().runtime(space)["agents"].get(agent, {}).get("busy", False)
        if not busy and time.time() - started > 20:
            tail = "\n".join(_fmt(r, space) for r in new)
            return (tail + "\n" if tail else "") + f"{agent} stopped without a final report (see `hangar logs {space}`)."
        if time.time() >= end:
            tail = "\n".join(_fmt(r, space) for r in new[-5:])
            return (tail + "\n" if tail else "") + f"Still working. Call wait('{space}') for the result."
        time.sleep(3)


def _lent(vm: str) -> str:
    try:
        return next((a.name for a in eng().list() if a.vm == vm and a.status == "running"), "")
    except RuntimeError:
        return ""                       # no container engine: no spaces to lend it to


def _free_vm(vm: str) -> str:
    space = _lent(vm)
    if space:
        raise RuntimeError(f"the Mac VM {vm} is lent to space {space}; use look('{space}'), "
                           "or stop that space first")
    return vm


def _png(data: bytes, max_width: int) -> tuple[bytes, float]:
    """Shrink a screenshot to max_width (Qt's QImage, with the apps extra); returns the scale."""
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage
    except ImportError:
        return data, 1.0
    img = QImage.fromData(data)
    if img.isNull() or img.width() <= max_width:
        return data, 1.0
    scale = img.width() / max_width
    small = img.scaledToWidth(max_width, Qt.SmoothTransformation)
    out = QByteArray()
    buf = QBuffer(out)
    buf.open(QIODevice.WriteOnly)
    small.save(buf, "PNG")
    return bytes(out), scale


def build() -> FastMCP:
    mcp = FastMCP("hangar", instructions=INSTRUCTIONS, log_level="WARNING")

    # -- hand Hangar the job ----------------------------------------------------------------

    @mcp.tool()
    async def spaces() -> str:
        """Hangar's spaces: their state, computer (linux or a Mac VM), model and latest report."""
        def go():
            rows = []
            for a in eng().list():
                st = eng().state(a)
                rep = st["report"]["message"][:160] if st["report"] else ""
                rows.append(f"{a.name}: {st['state']}, {('mac ' + a.vm) if a.vm else 'linux'}, "
                            f"{a.model or 'no model'}" + (f". Latest: {rep}" if rep else ""))
            return "\n".join(rows) or "No spaces yet."
        return await _thread(go)

    @mcp.tool()
    async def do_task(task: str, ctx: Context, computer: str = "linux", space: str = "", model: str = "",
                      wait_seconds: int = 600) -> str:
        """Have Hangar do a task in one of its spaces and return the agent's final report.
        task: the whole job, in plain words, with what "done" looks like.
        computer: "linux" (a desktop in a container, right for nearly everything) or "mac" /
        "mac:VM" (a macOS VM, only for Mac apps; at most 2 run at once).
        space: reuse this space (its files and sign-ins carry over); default: a new one.
        model: e.g. "claude", "codex", "local:<model>"; default: the user's pilot model.
        wait_seconds: how long to wait here; past that, call wait(space)."""
        info = ctx.session.client_params.clientInfo if ctx.session.client_params else None
        via = CLIENTS.get(info.name, info.name) if info else "another agent"

        def start() -> tuple[str, str, int]:
            e = eng()
            daemonctl.ensure_daemon()           # screen tools, reports and notifications go through it
            if space:
                a = e.get(space)
                if a.status != "running":
                    e.start(space)
                    e.wait_ready(space, timeout=180)
                seen = len(_reports(space))
                e.run_task(space, task, snapshot=True)
                return space, space, seen
            name = "task-" + (re.sub(r"[^a-z0-9]+", "-", task.lower())[:18].strip("-") or "job") \
                   + "-" + secrets.token_hex(2)
            m = model or _default_model()
            if not m:
                raise RuntimeError("no model: pass model=, or set the pilot's model in Hangar's Settings")
            from hangar.prepare import prepare
            spec = AgentSpec(name=name, model=m, task=task, computer=computer or "linux",
                             labels={f"{LABEL}.via": via[:40]})
            prepare(e, spec)
            e.create(spec)
            e.wait_ready(name, timeout=240)
            return name, name, 0
        name, agent, seen = await _thread(start)
        result = await _thread(_wait, name, agent, seen, wait_seconds)
        return f"Space {name}:\n{result}"

    @mcp.tool()
    async def wait(space: str, seconds: int = 300) -> str:
        """Wait for a space's current task to finish and return its report."""
        def go():
            reps = _reports(space)
            last = max((i for i, r in enumerate(reps) if r.get("final")), default=-1)
            return _wait(space, space, last + 1, seconds)
        return await _thread(go)

    @mcp.tool()
    async def look(space: str) -> list:
        """A space's screen right now (Linux desktop or Mac VM), and its latest reports."""
        def go():
            a = eng().get(space)
            if a.vm:
                png = _png(macvm.screenshot(a.vm), SHOT_WIDTH)[0]
            else:
                r = httpx.get(f"http://127.0.0.1:{DAEMON_PORT}/api/agents/{space}/screenshot.png",
                              params={"w": SHOT_WIDTH}, timeout=30,
                              headers={"Authorization": f"Bearer {auth.admin_token()}"})
                r.raise_for_status()
                png = r.content
            tail = "\n".join(_fmt(r, space) for r in _reports(space)[-5:])
            return [tail or "No reports yet.", Image(data=png, format="png")]
        return await _thread(go)

    @mcp.tool()
    async def stop_space(space: str, delete: bool = False) -> str:
        """Stop a space (and its Mac VM). delete=True also removes it and its files."""
        await _thread(eng().destroy if delete else eng().stop, space)
        return f"{'Deleted' if delete else 'Stopped'} {space}."

    # -- drive a Mac VM yourself ------------------------------------------------------------

    @mcp.tool()
    async def mac_start(vm: str = macvm.DEFAULT_VM) -> str:
        """Boot a Mac VM in the background (prepared once with `hangar mac setup`)."""
        await _thread(macvm.start, _free_vm(vm))
        return json.dumps(await _thread(macvm.status, vm))

    @mcp.tool()
    async def mac_screenshot(vm: str = macvm.DEFAULT_VM) -> Image:
        """The Mac VM's screen. Click and scroll at this image's pixel coordinates."""
        def go():
            raw = macvm.screenshot(_free_vm(vm))
            png, _scale[vm] = _png(raw, SHOT_WIDTH)
            return png
        return Image(data=await _thread(go), format="png")

    def _act(vm: str, action: str, body: dict) -> str:
        s = _scale.get(vm, 1.0)
        for k in ("x", "y", "x1", "y1", "x2", "y2"):
            if body.get(k) is not None:
                body[k] = round(body[k] * s)
        res = macvm.act(_free_vm(vm), action, body)
        return json.dumps(res) if res else "ok"

    @mcp.tool()
    async def mac_click(x: int, y: int, vm: str = macvm.DEFAULT_VM, button: str = "left",
                        clicks: int = 1) -> str:
        """Click at (x, y) in the last mac_screenshot. button: left | right; clicks: 1 or 2."""
        return await _thread(_act, vm, "click", {"x": x, "y": y, "button": button, "clicks": clicks})

    @mcp.tool()
    async def mac_type(text: str, vm: str = macvm.DEFAULT_VM) -> str:
        """Type text into the focused field. Never the user's passwords."""
        return await _thread(_act, vm, "type", {"text": text})

    @mcp.tool()
    async def mac_key(keys: str, vm: str = macvm.DEFAULT_VM) -> str:
        """Press keys, space-separated: e.g. "cmd+space", "return", "cmd+l", "escape"."""
        return await _thread(_act, vm, "key", {"keys": keys})

    @mcp.tool()
    async def mac_scroll(direction: str = "down", amount: int = 3, x: int | None = None,
                         y: int | None = None, vm: str = macvm.DEFAULT_VM) -> str:
        """Scroll up or down, optionally over (x, y)."""
        return await _thread(_act, vm, "scroll", {"direction": direction, "amount": amount, "x": x, "y": y})

    @mcp.tool()
    async def mac_drag(x1: int, y1: int, x2: int, y2: int, vm: str = macvm.DEFAULT_VM) -> str:
        """Drag from (x1, y1) to (x2, y2)."""
        return await _thread(_act, vm, "drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    @mcp.tool()
    async def mac_show(vm: str = macvm.DEFAULT_VM) -> str:
        """Open the VM's window on the user's screen, so they can watch, take over or sign in."""
        await _thread(macvm.show, vm)
        return f"{vm}'s window is open for the user."

    @mcp.tool()
    async def mac_stop(vm: str = macvm.DEFAULT_VM) -> str:
        """Shut a Mac VM down (frees one of the two macOS VMs a Mac may run)."""
        await _thread(macvm.stop, _free_vm(vm))
        return f"Stopped {vm}."

    return mcp


def serve() -> None:
    build().run("stdio")
