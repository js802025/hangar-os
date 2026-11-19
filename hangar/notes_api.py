"""The daemon's notes routes.

You (admin token):
  GET    /api/notes                  ?q=  list, or search
  GET    /api/notes/note             ?path=   a note, its links and backlinks
  PUT    /api/notes/note             {"path", "text"}
  POST   /api/notes/rename           {"path", "to"}
  DELETE /api/notes/note             ?path=   (moves it to the vault's .trash)
  GET    /api/notes/resolve          ?title=  where a [[link]] points
  GET    /api/notes/access     PUT {"agents": "own"|"read"|"off"}

Agents (their own token), limited by the access setting:
  GET    /api/agents/{name}/notes        ?q=      search / list
  GET    /api/agents/{name}/notes/read   ?path=
  POST   /api/agents/{name}/notes/write  {"path", "text", "append", "who"}
"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hangar import auth, notes


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


def is_agent_route(path: str) -> bool:
    p = path.split("/")
    return len(p) in (5, 6) and p[1:3] == ["api", "agents"] and p[4] == "notes"


def routes(hub, log) -> list[Route]:
    def changed(path: str, who: str = "you") -> None:
        hub.publish({"type": "notes", "path": path, "who": who})

    async def listing(request: Request) -> JSONResponse:
        q = request.query_params.get("q", "")
        return JSONResponse(await run_in_threadpool(notes.search, q) if q else await run_in_threadpool(notes.listing))

    async def get_note(request: Request) -> JSONResponse:
        try:
            return JSONResponse(await run_in_threadpool(notes.read, request.query_params.get("path", "")))
        except notes.NotesError as e:
            return _err(str(e), 404)

    async def put_note(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            r = await run_in_threadpool(notes.write, body.get("path", ""), str(body.get("text", "")))
        except notes.NotesError as e:
            return _err(str(e))
        changed(r["path"])
        return JSONResponse(r)

    async def rename(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            r = await run_in_threadpool(notes.rename, body.get("path", ""), body.get("to", ""))
        except notes.NotesError as e:
            return _err(str(e))
        changed(r["path"])
        return JSONResponse(r)

    async def del_note(request: Request) -> JSONResponse:
        path = request.query_params.get("path", "")
        try:
            await run_in_threadpool(notes.delete, path)
        except notes.NotesError as e:
            return _err(str(e), 404)
        changed(path)
        return JSONResponse({"ok": True})

    async def resolve(request: Request) -> JSONResponse:
        return JSONResponse({"path": notes.resolve(request.query_params.get("title", ""))})

    async def get_access(request: Request) -> JSONResponse:
        if request.method == "PUT":
            try:
                notes.set_access((await request.json()).get("agents", ""))
            except notes.NotesError as e:
                return _err(str(e))
        return JSONResponse({"agents": notes.access(), "root": str(notes.root())})

    # -- agents --------------------------------------------------------------------------
    def agent_ok(request: Request) -> bool:
        h = request.headers.get("authorization", "")
        return auth.check(h[7:] if h.startswith("Bearer ") else None, auth.agent_token(request.path_params["name"]))

    async def agent_list(request: Request) -> JSONResponse:
        if not agent_ok(request):
            return _err("bad agent token", 401)
        space = request.path_params["name"]
        if notes.access() == "off":
            return _err("the user has turned notes off for agents", 403)
        scope, q = notes.agent_scope(space), request.query_params.get("q", "")
        r = await run_in_threadpool(notes.search, q, scope) if q else await run_in_threadpool(notes.listing, scope)
        return JSONResponse({"scope": scope or "(whole vault)", "notes": r})

    async def agent_read(request: Request) -> JSONResponse:
        if not agent_ok(request):
            return _err("bad agent token", 401)
        path = request.query_params.get("path", "")
        try:
            if not notes.can_read(request.path_params["name"], path):
                return _err("that note isn't shared with agents", 403)
            return JSONResponse(await run_in_threadpool(notes.read, path))
        except notes.NotesError as e:
            return _err(str(e), 404)

    async def agent_write(request: Request) -> JSONResponse:
        if not agent_ok(request):
            return _err("bad agent token", 401)
        space = request.path_params["name"]
        body = await request.json()
        path = str(body.get("path", ""))
        try:
            if not path.startswith(notes.AGENTS + "/"):
                path = notes.agent_folder(space) + path.lstrip("/")
            if not notes.can_write(space, path):
                return _err(f"agents can only write notes in {notes.agent_folder(space)}", 403)
            r = await run_in_threadpool(notes.write, path, str(body.get("text", "")), bool(body.get("append")))
        except notes.NotesError as e:
            return _err(str(e))
        who = str(body.get("who") or space)[:31]
        changed(r["path"], who)
        log(f"notes: {who} wrote {r['path']}")
        return JSONResponse(r)

    A = "/api/agents/{name}/notes"
    return [
        Route("/api/notes", listing),
        Route("/api/notes/note", get_note),
        Route("/api/notes/note", put_note, methods=["PUT"]),
        Route("/api/notes/note", del_note, methods=["DELETE"]),
        Route("/api/notes/rename", rename, methods=["POST"]),
        Route("/api/notes/resolve", resolve),
        Route("/api/notes/access", get_access, methods=["GET", "PUT"]),
        Route(A, agent_list),
        Route(A + "/read", agent_read),
        Route(A + "/write", agent_write, methods=["POST"]),
    ]
