"""The daemon's side of the vault: agents' requests, your approvals, the fill.

Agent routes (agent token):
  POST /api/agents/{name}/vault/requests        {"kind": "login"|"code", "site", "reason"}
  GET  /api/agents/{name}/vault/requests/{id}   ?wait=20 long-polls for a decision
Admin routes (admin token):
  GET    /api/vault                              entries (no secrets), grants, requests, audit
  POST   /api/vault/entries                      {"site", "username", "password", "totp", "label"}
  DELETE /api/vault/entries/{id}
  DELETE /api/vault/grants/{agent}/{entry}
  POST   /api/vault/requests/{id}/approve        {"entry", "scope": once|hour|always, "code"}
                                                 or {"login": {"username", "password"}}: a login
                                                 you supply once (Hangar for Mac fills it from
                                                 Apple Passwords); used for this fill, never stored
  POST   /api/vault/requests/{id}/deny
"""

from __future__ import annotations

import asyncio
import binascii
import secrets
import time

import httpx
from docker.errors import NotFound
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hangar import auth, notify
from hangar.engine import Engine
from hangar.vault import SCOPES, Vault, audit, normalize_site, recent_audit, totp

EXPIRE = 15 * 60   # unanswered requests lapse after 15 minutes


class Req:
    def __init__(self, agent: str, kind: str, site: str, reason: str, who: str = "") -> None:
        self.id = secrets.token_hex(5)
        self.agent, self.kind, self.site, self.reason = agent, kind, site, reason
        self.who = who or agent   # which agent in the space asked (its tab gets the fill)
        self.status = "pending"        # pending | approved | filled | denied | failed | expired
        self.message = ""
        self.created = time.time()
        self.changed = asyncio.Event()

    def set(self, status: str, message: str) -> None:
        self.status, self.message = status, message
        self.changed.set()
        self.changed = asyncio.Event()

    def public(self, vault: Vault | None = None) -> dict:
        d = {"id": self.id, "agent": self.agent, "who": self.who, "kind": self.kind, "site": self.site, "reason": self.reason,
             "status": self.status, "message": self.message, "created": self.created}
        if vault is not None:
            d["entries"] = vault.match(self.site)
        return d


def routes(eng: Engine, hub, log) -> list[Route]:
    vault = Vault()
    reqs: dict[str, Req] = {}

    def err(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code)

    def publish(r: Req) -> None:
        hub.publish({"type": "vault", **r.public()})

    def expire_old() -> None:
        for r in reqs.values():
            if r.status == "pending" and time.time() - r.created > EXPIRE:
                r.set("expired", "Nobody answered the request in time. Report waiting_on_user if you still need it.")

    async def do_fill(r: Req, entry_id: str, code: str = "", how: str = "approved",
                      given: dict | None = None) -> None:
        """Type the secret into the agent's browser via its own (token-locked) port.
        `given` is a login the user supplied for this one fill; it isn't kept."""
        try:
            e = await run_in_threadpool(vault.get, entry_id) if entry_id else None
            if e and normalize_site(r.site) != e["site"] and not normalize_site(r.site).endswith("." + e["site"]):
                raise RuntimeError("that login is for a different site")
            payload = {"site": e["site"] if e else r.site, "agent": r.who}
            if r.kind == "login" and given:
                payload |= {"username": given.get("username", ""), "password": given.get("password", "")}
            elif r.kind == "login":
                if not e:
                    raise RuntimeError("no login chosen")
                payload |= {"username": e.get("username", ""), "password": e.get("password", "")}
            else:
                payload["code"] = code or (totp(e["totp"]) if e and e.get("totp") else "")
                if not payload["code"]:
                    raise RuntimeError("no code given")
            a = await run_in_threadpool(eng.get, r.agent)
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.post(f"http://127.0.0.1:{a.mcp_port}/vault/fill", json=payload,
                                       headers=a.auth_headers)
            res = resp.json()
            if not res.get("ok"):
                raise RuntimeError(res.get("error", f"fill failed ({resp.status_code})"))
            what = " and ".join(res["filled"])
            more = (" The password field wasn't on this page: continue to the next step and call "
                    "request_login again.") if r.kind == "login" and "password" not in res["filled"] else ""
            src = e["label"] if e else ("a login from the user" if given else "code from the user")
            r.set("filled", f"Filled the {what} on {res['page']} ({src}). "
                            f"Now continue (e.g. click the sign-in or verify button).{more}")
            audit("filled", agent=r.agent, kind=r.kind, site=r.site, entry=e["label"] if e else "", how=how,
                  fields=res["filled"])
        except (RuntimeError, KeyError, NotFound, httpx.HTTPError, ValueError) as ex:
            r.set("failed", f"Couldn't fill it: {ex}")
            audit("fill_failed", agent=r.agent, kind=r.kind, site=r.site, error=str(ex)[:200])
        publish(r)

    # -- agent side ------------------------------------------------------------------------
    def agent_ok(request: Request) -> bool:
        h = request.headers.get("authorization", "")
        return auth.check(h[7:] if h.startswith("Bearer ") else None, auth.agent_token(request.path_params["name"]))

    async def new_request(request: Request):
        name = request.path_params["name"]
        if not agent_ok(request):
            return err("bad agent token", 401)
        try:
            b = await request.json()
        except ValueError:
            b = {}
        kind, site = b.get("kind"), normalize_site(str(b.get("site", "")))
        if kind not in ("login", "code") or not site:
            return err("kind must be login or code, and site a domain")
        expire_old()
        who = "".join(ch for ch in str(b.get("who", "")).lower() if ch.isalnum() or ch == "-")[:31]
        r = Req(name, kind, site, str(b.get("reason", ""))[:300], who)
        reqs[r.id] = r
        audit("requested", agent=name, kind=kind, site=site, reason=r.reason)
        # Standing approval? Fill right away.
        entries = await run_in_threadpool(vault.match, site)
        for e in entries:
            if await run_in_threadpool(vault.granted, name, e["id"]) and (kind == "login" or e["has_totp"]):
                r.status = "approved"
                asyncio.get_running_loop().create_task(do_fill(r, e["id"], how="standing approval"))
                return JSONResponse(r.public())
        r.message = ("Waiting for the user to approve. They've been notified. If this times out, "
                     "call the tool again or report waiting_on_user.")
        publish(r)
        from hangar import threads
        tid = threads.thread_for_space(name)
        if tid:
            threads.add(tid, "approval", f"{r.who} wants to {'sign in to' if kind == 'login' else 'get a code for'} "
                                         f"{site}", request=r.id, space=name, reason=r.reason)
        what = "sign in to" if kind == "login" else "get a verification code for"
        log(f"vault: {name} wants to {what} {site}")
        asyncio.get_running_loop().run_in_executor(
            None, notify.send, f"{name} wants to {what} {site}",
            (r.reason or "No reason given.") + "\nApprove or deny in Hangar.", "waiting_on_user")
        return JSONResponse(r.public())

    async def poll_request(request: Request):
        if not agent_ok(request):
            return err("bad agent token", 401)
        r = reqs.get(request.path_params["id"])
        if not r or r.agent != request.path_params["name"]:
            return err("no such request", 404)
        wait = min(float(request.query_params.get("wait", "0") or 0), 30)
        if r.status in ("pending", "approved") and wait:
            try:
                await asyncio.wait_for(r.changed.wait(), wait)
            except asyncio.TimeoutError:
                pass
        expire_old()
        return JSONResponse(r.public())

    # -- your side -------------------------------------------------------------------------------
    async def overview(_: Request):
        expire_old()
        def gather():
            entries = {e["id"]: e for e in vault.entries()}
            return {"entries": list(entries.values()),
                    "grants": [{**g, "label": entries.get(g["entry"], {}).get("label", "?")} for g in vault.grants()],
                    "requests": sorted((r.public(vault) for r in reqs.values()
                                        if r.status == "pending" or time.time() - r.created < 3600),
                                       key=lambda d: (d["status"] != "pending", -d["created"])),
                    "audit": recent_audit(40)}
        return JSONResponse(await run_in_threadpool(gather))

    async def add_entry(request: Request):
        b = await request.json()
        try:
            e = await run_in_threadpool(vault.add, str(b.get("site", "")), str(b.get("username", "")),
                                        str(b.get("password", "")), str(b.get("totp", "")),
                                        str(b.get("label", "")), str(b.get("notes", "")))
        except (ValueError, binascii.Error) as ex:
            return err("that verification code setup key isn't valid" if isinstance(ex, binascii.Error) else str(ex))
        hub.publish({"type": "vault"})
        return JSONResponse(e)

    async def edit_entry(request: Request):
        b = await request.json()
        fields = {k: (str(b[k]) if b.get(k) is not None else None)
                  for k in ("site", "label", "username", "password", "totp", "notes")}
        try:
            e = await run_in_threadpool(lambda: vault.update(request.path_params["id"], **fields))
        except KeyError as ex:
            return err(str(ex).strip("'"), 404)
        except (ValueError, binascii.Error) as ex:
            return err("that verification code setup key isn't valid" if isinstance(ex, binascii.Error) else str(ex))
        hub.publish({"type": "vault"})
        return JSONResponse(e)

    def entry_action(fn, what):
        async def endpoint(request: Request):
            try:
                await run_in_threadpool(fn, request.path_params["id"])
            except KeyError as ex:
                return err(str(ex).strip("'"), 404)
            hub.publish({"type": "vault"})
            return JSONResponse({"ok": True, "did": what})
        return endpoint

    async def reveal(request: Request):
        try:
            sec = await run_in_threadpool(vault.secret, request.path_params["id"])
        except KeyError as ex:
            return err(str(ex).strip("'"), 404)
        if request.query_params.get("audit") == "1":
            audit("revealed", entry=request.path_params["id"])
        return JSONResponse(sec, headers={"Cache-Control": "no-store"})

    async def codes(_: Request):
        return JSONResponse(await run_in_threadpool(vault.codes), headers={"Cache-Control": "no-store"})

    async def revoke(request: Request):
        await run_in_threadpool(vault.revoke, request.path_params["agent"], request.path_params["entry"])
        hub.publish({"type": "vault"})
        return JSONResponse({"ok": True})

    async def approve(request: Request):
        r = reqs.get(request.path_params["id"])
        if not r or r.status != "pending":
            return err("that request isn't waiting any more", 409)
        b = await request.json()
        entry, scope, code = str(b.get("entry", "")), b.get("scope", "once"), str(b.get("code", "")).strip()
        if scope not in SCOPES:
            return err(f"scope: {' | '.join(SCOPES)}")
        given = b.get("login")
        if given is not None:          # a login the user supplied once (e.g. from Apple Passwords)
            if r.kind != "login":
                return err("a login only answers a sign-in request; send a code for this one")
            if not isinstance(given, dict) or not str(given.get("password", "")):
                return err("login needs a username and a password")
            given = {"username": str(given.get("username", ""))[:500], "password": str(given["password"])[:1000]}
            audit("approved", agent=r.agent, kind=r.kind, site=r.site, scope="once", entry="one-time login from the user")
            r.status = "approved"
            publish(r)
            await do_fill(r, "", how="one-time login", given=given)
            return JSONResponse(r.public())
        candidates = {e["id"]: e for e in await run_in_threadpool(vault.match, r.site)}
        if entry and entry not in candidates:
            return err("that login isn't for this site")
        if r.kind == "login" and not entry:
            return err(f"pick a saved login for {r.site} (add one in the Vault first)")
        if r.kind == "code" and not code and not (entry and candidates[entry]["has_totp"]):
            return err("type the code, or pick a login that has a 2FA secret")
        if entry:
            await run_in_threadpool(vault.grant, r.agent, entry, scope)
        audit("approved", agent=r.agent, kind=r.kind, site=r.site, scope=scope,
              entry=candidates[entry]["label"] if entry else "typed code")
        r.status = "approved"
        publish(r)
        await do_fill(r, entry, code)
        return JSONResponse(r.public())

    async def deny(request: Request):
        r = reqs.get(request.path_params["id"])
        if not r or r.status != "pending":
            return err("that request isn't waiting any more", 409)
        r.set("denied", "The user said no. Don't try to sign in another way; continue without it or "
                        "report waiting_on_user.")
        audit("denied", agent=r.agent, kind=r.kind, site=r.site)
        publish(r)
        return JSONResponse(r.public())

    A = "/api/agents/{name}/vault/requests"
    return [
        Route(A, new_request, methods=["POST"]),
        Route(A + "/{id}", poll_request),
        Route("/api/vault", overview),
        Route("/api/vault/entries", add_entry, methods=["POST"]),
        Route("/api/vault/entries/{id}", edit_entry, methods=["PUT"]),
        Route("/api/vault/entries/{id}", entry_action(vault.remove, "deleted"), methods=["DELETE"]),
        Route("/api/vault/entries/{id}/restore", entry_action(vault.restore, "restored"), methods=["POST"]),
        Route("/api/vault/entries/{id}/purge", entry_action(vault.purge, "purged"), methods=["POST"]),
        Route("/api/vault/entries/{id}/secret", reveal),
        Route("/api/vault/codes", codes),
        Route("/api/vault/grants/{agent}/{entry}", revoke, methods=["DELETE"]),
        Route("/api/vault/requests/{id}/approve", approve, methods=["POST"]),
        Route("/api/vault/requests/{id}/deny", deny, methods=["POST"]),
    ]


def is_agent_route(path: str) -> bool:
    p = path.split("/")
    return len(p) >= 6 and p[1:3] == ["api", "agents"] and p[4:6] == ["vault", "requests"]
