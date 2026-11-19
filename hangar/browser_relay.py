"""The daemon's relay between an agent and Hangar's in-app browser.

An agent drives its tabs with Playwright over the Chrome DevTools Protocol
(CDP). Hangar's browser (QtWebEngine, hangar/apps/browser_host.py) serves CDP
on 127.0.0.1 for all its tabs; this relay gives each space a view of only its
own:

  - targets (tabs) of other spaces, and of you, are never listed, attached to
    or driven; messages for their sessions are dropped both ways;
  - new tabs are opened by Hangar (QtWebEngine can't over CDP), in the space's
    own profile; popups a space's page opens belong to that space;
  - browser-wide commands are limited to what Playwright needs: the few that
    QtWebEngine doesn't support are answered here, the rest refused, so an
    agent can't close or reconfigure Hangar's browser.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import websockets

from hangar import auth

STATE = auth.STATE_DIR / "browser.json"      # written by the running Hangar's browser


def control() -> dict | None:
    """Where the running Hangar's browser listens ({cdp, control, key, pid}), if it is."""
    try:
        d = json.loads(STATE.read_text())
        os.kill(int(d["pid"]), 0)
        return d
    except (OSError, ValueError, KeyError):
        return None

BROWSER_OK = {"Browser.getVersion"}
STUB = {"Browser.setDownloadBehavior", "Browser.grantPermissions", "Browser.resetPermissions",
        "Browser.setPermission", "Target.setDiscoverTargets", "Target.setRemoteLocations",
        "Browser.setWindowBounds", "Emulation.setFocusEmulationEnabled"}


class Scope:
    """Which targets and sessions a space may see."""

    def __init__(self, space: str, ctl: dict) -> None:
        self.space, self.ctl = space, ctl
        self.targets: set[str] = set()
        self.tab_ids: set[str] = set()          # Hangar's ids for the space's tabs (known before their targets)
        self.sessions: dict[str, str] = {}      # session id -> target id (the space's)
        self.foreign: set[str] = set()          # sessions of other targets
        self.opening = 0                         # tabs being opened for this space right now
        self.held: list[str] = []               # unknown new tabs seen while opening: decided after

    async def control(self, method: str, path: str, body: dict | None = None):
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.request(method, f"http://127.0.0.1:{self.ctl['control']}{path}", json=body,
                                   headers={"Authorization": f"Bearer {self.ctl['key']}"})
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("error", r.text))
        return r.json()

    async def refresh(self) -> None:
        tabs = await self.control("GET", f"/tabs?space={self.space}")
        self.targets |= {t["target"] for t in tabs if t.get("target")}
        self.tab_ids |= {t["id"] for t in tabs}

    def allowed(self, info: dict | None) -> bool:
        if not info:
            return False
        tid = info.get("targetId", "")
        if tid in self.targets:
            return True
        if info.get("openerId") in self.targets:          # a popup one of our pages opened
            self.targets.add(tid)
            return True
        marker = info.get("url", "").rpartition("#hangar-")[2]
        if marker and marker in self.tab_ids:             # a tab Hangar just opened for this space
            self.targets.add(tid)
            return True
        return False


def _error(mid, session, message: str) -> str:
    out = {"id": mid, "error": {"code": -32000, "message": message}}
    if session:
        out["sessionId"] = session
    return json.dumps(out)


async def relay(ws, space: str, ctl: dict) -> None:
    """Relay one agent connection (a Starlette WebSocket, already accepted)."""
    scope = Scope(space, ctl)
    await scope.refresh()
    async with httpx.AsyncClient(timeout=10) as http:
        ver = (await http.get(f"http://127.0.0.1:{ctl['cdp']}/json/version")).json()
    up = await websockets.connect(ver["webSocketDebuggerUrl"], max_size=256 * 2**20)
    pending: dict[int, str] = {}                 # our ids whose results need filtering -> method

    async def down_to_up() -> None:
        while True:
            m = json.loads(await ws.receive_text())
            mid, method, session, params = m.get("id"), m.get("method", ""), m.get("sessionId"), m.get("params") or {}
            if session:
                if session in scope.sessions:
                    await up.send(json.dumps(m))
                else:
                    await ws.send_text(_error(mid, session, "not one of your tabs"))
                continue
            if method in STUB:
                await ws.send_text(json.dumps({"id": mid, "result": {}}))
            elif method == "Target.getTargetInfo" and not params.get("targetId"):     # the browser itself
                await up.send(json.dumps(m))
            elif method in BROWSER_OK or method in ("Target.setAutoAttach", "Target.getTargets"):
                if method == "Target.getTargets":
                    await scope.refresh()
                    pending[mid] = method
                await up.send(json.dumps(m))
            elif method == "Target.createTarget":
                scope.opening += 1
                try:
                    tab = await scope.control("POST", "/tabs", {"space": space, "url": params.get("url", "")})
                    scope.targets.add(tab["target"])
                    scope.tab_ids.add(tab["id"])
                    held, scope.held = scope.held, []
                    for raw in held:
                        await attached(raw, final=True)
                    # Like Chromium: the tab is attached before the answer arrives.
                    for _ in range(100):
                        if tab["target"] in scope.sessions.values():
                            break
                        await asyncio.sleep(0.05)
                    await ws.send_text(json.dumps({"id": mid, "result": {"targetId": tab["target"]}}))
                except Exception as e:  # noqa: BLE001
                    await ws.send_text(_error(mid, None, f"couldn't open a tab: {e}"))
                finally:
                    scope.opening -= 1
                    held, scope.held = scope.held, []
                    for raw in held:
                        await attached(raw, final=True)
            elif method in ("Target.attachToTarget", "Target.activateTarget", "Target.getTargetInfo",
                            "Target.closeTarget", "Target.detachFromTarget"):
                tid = params.get("targetId") or scope.sessions.get(params.get("sessionId", ""), "")
                if tid not in scope.targets:
                    await scope.refresh()
                if tid not in scope.targets:
                    await ws.send_text(_error(mid, None, "not one of your tabs"))
                elif method == "Target.closeTarget":
                    tabs = await scope.control("GET", f"/tabs?space={space}")
                    hit = next((t for t in tabs if t["target"] == tid), None)
                    if hit:
                        await scope.control("POST", "/close", {"id": hit["id"], "space": space})
                    await ws.send_text(json.dumps({"id": mid, "result": {"success": bool(hit)}}))
                else:
                    await up.send(json.dumps(m))
            else:
                await ws.send_text(_error(mid, None, f"{method} isn't available in Hangar's browser"))

    async def attached(raw: str, final: bool = False) -> None:
        """A tab was attached (by auto-attach): pass it on if it's this space's."""
        params = json.loads(raw).get("params") or {}
        info = params.get("targetInfo")
        if info and info.get("targetId") not in scope.targets:
            await scope.refresh()
        if scope.allowed(info):
            scope.sessions[params["sessionId"]] = info["targetId"]
            await ws.send_text(raw)
        elif scope.opening and not final:          # maybe the tab being opened: decide when we know
            scope.held.append(raw)
        else:                                      # not ours: let it run on, and let go of it
            sid = params.get("sessionId", "")
            scope.foreign.add(sid)
            if params.get("waitingForDebugger"):
                await up.send(json.dumps({"id": -1, "sessionId": sid, "method": "Runtime.runIfWaitingForDebugger"}))
            await up.send(json.dumps({"id": -2, "method": "Target.detachFromTarget", "params": {"sessionId": sid}}))

    async def up_to_down() -> None:
        async for raw in up:
            m = json.loads(raw)
            session, method, params = m.get("sessionId"), m.get("method", ""), m.get("params") or {}
            if session:
                if session in scope.sessions:
                    await ws.send_text(raw if isinstance(raw, str) else raw.decode())
                continue                                           # another space's session: drop
            if method == "Target.attachedToTarget":
                await attached(raw if isinstance(raw, str) else raw.decode())
                continue
            if method == "Target.detachedFromTarget":
                if scope.sessions.pop(params.get("sessionId", ""), None):
                    await ws.send_text(raw if isinstance(raw, str) else raw.decode())
                continue
            if method in ("Target.targetCreated", "Target.targetInfoChanged", "Target.targetDestroyed"):
                info = params.get("targetInfo") or {"targetId": params.get("targetId")}
                if scope.allowed(info):
                    await ws.send_text(raw if isinstance(raw, str) else raw.decode())
                continue
            if method:                                             # other browser-wide events: not for agents
                continue
            if m.get("id") in (-1, -2):                            # our own housekeeping
                continue
            if pending.pop(m.get("id"), None) == "Target.getTargets" and "result" in m:
                m["result"]["targetInfos"] = [t for t in m["result"].get("targetInfos", []) if scope.allowed(t)]
                await ws.send_text(json.dumps(m))
                continue
            await ws.send_text(raw if isinstance(raw, str) else raw.decode())

    tasks = [asyncio.create_task(down_to_up()), asyncio.create_task(up_to_down())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t.exception() and not isinstance(t.exception(), websockets.ConnectionClosed):
                raise t.exception()
    finally:
        for t in tasks:
            t.cancel()
        await up.close()
