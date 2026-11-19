"""Asking the user's vault for logins and verification codes.

The agent calls request_login / request_code. The daemon asks the user, and
on approval calls this container's /vault/fill route (protected by the
agent's token) to type the login into the browser. Secrets go straight into
the page; they never appear in tool results.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import Context

from hangar_desktop.browser import link

LOCAL_HOSTS = {"localhost", "127.0.0.1", "hangar-host"}   # http allowed only here (dev and tests)

USER_SEL = ('input[autocomplete="username"], input[type="email"], input[name*="user" i], '
            'input[name*="email" i], input[name*="login" i], input[id*="user" i], input[id*="email" i], '
            'input[id*="login" i]')
PASS_SEL = 'input[type="password"]'
CODE_SEL = ('input[autocomplete="one-time-code"], input[name*="otp" i], input[name*="code" i], '
            'input[id*="otp" i], input[id*="code" i], input[inputmode="numeric"]')


def _matches(host: str, site: str) -> bool:
    host = (host or "").lower().removeprefix("www.")
    return host == site or host.endswith("." + site)


async def _first_visible(frame, selector):
    loc = frame.locator(selector)
    for i in range(min(await loc.count(), 10)):
        el = loc.nth(i)
        if await el.is_visible() and await el.is_editable():
            return el
    return None


async def fill(site: str, username: str = "", password: str = "", code: str = "", agent: str = "") -> dict:
    """Fill a login or code into the agent's current page, only if it's on `site`: its tab in
    Hangar's browser if it's working there, else the desktop's browser."""
    from hangar_desktop import inapp
    import anyio
    if inapp.available() and inapp.recently_used(agent or os.environ.get("HANGAR_AGENT_NAME", "")):
        try:
            return await anyio.to_thread.run_sync(lambda: inapp.fill(
                agent or os.environ.get("HANGAR_AGENT_NAME", ""), site, username, password, code))
        except RuntimeError as e:
            if "no tab open" not in str(e):
                raise
    page = await link.page(agent)
    u = urlsplit(page.url)
    if not _matches(u.hostname or "", site):
        raise RuntimeError(f"the browser is on {u.hostname or page.url}, not {site}. "
                           f"Open the {site} sign-in page first.")
    if u.scheme != "https" and (u.hostname or "") not in LOCAL_HOSTS:
        raise RuntimeError("refusing to fill a login over plain http")
    filled: list[str] = []
    # The login form may sit in a frame; only use frames on the same site.
    frames = [f for f in page.frames if _matches(urlsplit(f.url).hostname or "", site)]
    for frame in frames:
        if code and "code" not in filled and (el := await _first_visible(frame, CODE_SEL)):
            await el.fill(code)
            filled.append("code")
        if password and "password" not in filled and (el := await _first_visible(frame, PASS_SEL)):
            await el.fill(password)
            filled.append("password")
        if username and "username" not in filled:
            el = await _first_visible(frame, USER_SEL)
            if el is None and "password" in filled:
                # Plain text box right before the password box.
                el = await _first_visible(frame, 'input[type="text"]')
            if el is not None:
                await el.fill(username)
                filled.append("username")
    if not filled:
        raise RuntimeError("no login or code field found on this page")
    return {"filled": filled, "page": f"{u.hostname}{u.path}"}


async def _ask(kind: str, site: str, reason: str, by: str = "") -> str:
    daemon, agent = os.environ.get("HANGAR_DAEMON_URL"), os.environ.get("HANGAR_AGENT", "agent")
    if not daemon:
        return "No Hangar daemon is configured, so the vault isn't available. Report waiting_on_user."
    headers = {"Authorization": f"Bearer {os.environ.get('HANGAR_AGENT_TOKEN', '')}"}
    base = f"{daemon}/api/agents/{agent}/vault/requests"
    async with httpx.AsyncClient(timeout=70) as http:
        try:
            r = await http.post(base, json={"kind": kind, "site": site, "reason": reason, "who": by}, headers=headers)
            r.raise_for_status()
            req = r.json()
            # Wait up to ~50s for the user (tool calls time out after a minute in some harnesses).
            deadline = asyncio.get_running_loop().time() + 50
            while req["status"] in ("pending", "approved") and asyncio.get_running_loop().time() < deadline:
                r = await http.get(f"{base}/{req['id']}", params={"wait": 20}, headers=headers)
                r.raise_for_status()
                req = r.json()
        except httpx.HTTPError as e:
            return f"Couldn't reach the Hangar vault ({type(e).__name__}). Report waiting_on_user."
    return req["message"]


def register(mcp, who) -> None:

    @mcp.tool()
    async def request_login(site: str, reason: str, ctx: Context = None) -> str:
        """Ask the user's vault to sign you in to `site` (e.g. "github.com"). First open
        the site's sign-in page in the browser. If approved, Hangar types the username
        and password into the page for you (you never see them); then click the sign-in
        button. `reason`: one short sentence the user sees, e.g. "to check your PRs".
        Never ask the user for passwords any other way."""
        return await _ask("login", site, reason, who(ctx))

    @mcp.tool()
    async def request_code(site: str, reason: str, ctx: Context = None) -> str:
        """Ask for a verification / 2FA code for `site` when its page asks for one
        (authenticator, SMS or email code). Hangar fills it into the code field once
        the user approves or types it in. `reason`: one short sentence for the user."""
        return await _ask("code", site, reason, who(ctx))
