"""The daemon's management API for the Spaces UI (and the setup wizard):
create/delete agents, accounts and sign-ins, local models, notifications,
settings. Mounted by daemon.py behind the admin token.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading

from docker.errors import APIError, ImageNotFound, NotFound
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from hangar import auth, jobs, notify
from hangar import models as lm
from hangar.accounts import HARNESS, LOGIN_CMD, PROVIDERS, Accounts
from hangar.engine import DAEMON_PORT, AgentSpec, Engine
from hangar import harness
from hangar.prepare import HarnessMissing, SetupError, prepare

SETTINGS = auth.CONFIG_DIR / "settings.json"


def err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=code)


async def body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except ValueError:
        return {}


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def lan_ips() -> list[str]:
    """The machine's LAN address (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return [s.getsockname()[0]]
    except OSError:
        return []


def routes(eng: Engine) -> list[Route]:
    accts = Accounts(eng)

    def harness_job(name: str, then: list[str] | None = None) -> "jobs.Job":
        """One install job per harness at a time; `then` runs after it (e.g. a sign-in)."""
        for j in jobs.jobs.values():
            if j.kind == f"harness:{name}" and not j.done and not then:
                return j
        vol, argv = harness.prepare_install(eng, name)
        title = harness.HARNESSES[name]["title"]
        if then:
            import shlex
            argv = ["bash", "-c", f"{shlex.join(argv)} && {shlex.join(then)}"]
            return jobs.Job(f"Install {title}, then sign in", argv, kind="login")
        return jobs.Job(f"Install {title}", argv, kind=f"harness:{name}")

    # -- agents ------------------------------------------------------------------
    async def create_agent(request: Request):
        b = await body(request)
        spec = AgentSpec(name=str(b.get("name", "")).strip(), flavor=b.get("flavor") or "base",
                         model=b.get("model") or "", account=b.get("account") or "",
                         task=b.get("task") or "", shares=[str(s) for s in b.get("shares") or [] if s])
        if b.get("allow") is not None:
            from hangar.network import clean_domain
            try:
                spec.allow = [clean_domain(d) for d in b["allow"] if str(d).strip()]
            except ValueError as e:
                return err(str(e))
        flavors = {f["name"] for f in await run_in_threadpool(eng.flavors)}
        if spec.flavor not in flavors:
            return err(f"flavor '{spec.flavor}' isn't built. Build it with: make flavor F={spec.flavor}")
        try:
            warnings = await run_in_threadpool(prepare, eng, spec)
            await run_in_threadpool(eng.create, spec)
        except HarnessMissing as e:
            job = await run_in_threadpool(harness_job, e.harness)
            title = harness.HARNESSES[e.harness]["title"]
            return JSONResponse({"error": f"{title} is being installed (one time, a few minutes). "
                                          "Try again when it's done.", "job": job.to_dict()}, status_code=409)
        except (SetupError, ValueError, RuntimeError) as e:
            return err(str(e))
        except ImageNotFound:
            return err(f"The agent desktop for '{spec.flavor}' isn't downloaded yet. It downloads on first boot "
                       f"(or run: hangar images pull --flavor {spec.flavor}).")
        except APIError as e:
            return err(e.explanation or str(e))
        return JSONResponse({"ok": True, "name": spec.name, "warnings": warnings})

    async def delete_agent(request: Request):
        name = request.path_params["name"]
        keep = request.query_params.get("keep_home") == "1"
        try:
            await run_in_threadpool(eng.get, name)
        except NotFound:
            return err(f"no agent named '{name}'", 404)
        await run_in_threadpool(eng.destroy, name, keep)
        return JSONResponse({"ok": True})

    async def options(_: Request):
        """Everything the New agent form needs."""
        def gather():
            studio = lm.LMStudio()
            models = []
            if studio.installed:
                try:
                    models = [m.__dict__ for m in studio.list()]
                except RuntimeError:
                    pass
            return {"flavors": [f["name"] for f in eng.flavors()], "flavor_info": eng.flavors(),
                    "accounts": [{"name": a.name, "provider": a.provider, "harness": a.harness}
                                 for a in accts.list()],
                    "local_models": models, "lmstudio": studio.installed}
        return JSONResponse(await run_in_threadpool(gather))

    # -- accounts ----------------------------------------------------------------
    async def list_accounts(_: Request):
        def gather():
            return [{"name": a.name, "provider": a.provider, "label": PROVIDERS[a.provider],
                     "harness": a.harness, "created": a.created, "agents": accts.users(a.name),
                     "signin": a.provider in LOGIN_CMD} for a in accts.list()]
        return JSONResponse({"accounts": await run_in_threadpool(gather), "providers": PROVIDERS})

    async def add_account(request: Request):
        b = await body(request)
        try:
            a = await run_in_threadpool(accts.create, str(b.get("name", "")).strip(), b.get("provider", ""))
        except (ValueError, KeyError) as e:
            return err(str(e).strip("'"))
        return JSONResponse({"ok": True, "name": a.name, "signin": a.provider in LOGIN_CMD})

    async def remove_account(request: Request):
        try:
            await run_in_threadpool(accts.remove, request.path_params["name"])
        except (KeyError, ValueError) as e:
            return err(str(e).strip("'"))
        return JSONResponse({"ok": True})

    async def set_key(request: Request):
        b = await body(request)
        key = str(b.get("key", "")).strip()
        if not key:
            return err("empty key")
        try:
            a = await run_in_threadpool(accts.get, request.path_params["name"])
            if not a.provider.endswith("-key"):
                return err("this account signs in; it doesn't take a key")
            await run_in_threadpool(accts.set_api_key, a, key)
        except KeyError as e:
            return err(str(e).strip("'"), 404)
        if not await run_in_threadpool(harness.current, eng, a.harness):
            await run_in_threadpool(harness_job, a.harness)   # in the background, ready for its agents
        return JSONResponse({"ok": True})

    async def login(request: Request):
        """Start the vendor's sign-in; the UI shows its link and takes the code."""
        try:
            a = await run_in_threadpool(accts.get, request.path_params["name"])
        except KeyError as e:
            return err(str(e).strip("'"), 404)
        if a.provider not in LOGIN_CMD:
            return err("API-key accounts don't sign in")
        if await run_in_threadpool(harness.current, eng, a.harness):
            job = jobs.Job(f"Sign in: {a.name}", accts.login_argv(a), kind="login")
        else:   # first account for this harness: install it, then sign in, in one window
            def chain():
                vol, _ = harness.prepare_install(eng, a.harness)
                return harness_job(a.harness, then=accts.login_argv(a, harness_volume=vol))
            job = await run_in_threadpool(chain)
        return JSONResponse(job.to_dict())

    async def account_status(request: Request):
        try:
            a = await run_in_threadpool(accts.get, request.path_params["name"])
        except KeyError as e:
            return err(str(e).strip("'"), 404)
        return JSONResponse({"status": await run_in_threadpool(accts.status, a)})

    # -- models ------------------------------------------------------------------
    async def list_models(_: Request):
        def gather():
            s = lm.LMStudio()
            if not s.installed:
                return {"installed": False, "server": False, "models": []}
            return {"installed": True, "server": s.server_up(), "models": [m.__dict__ for m in s.list()],
                    "agent_url": lm.AGENT_URL}
        return JSONResponse(await run_in_threadpool(gather))

    async def download_model(request: Request):
        q = str((await body(request)).get("query", "")).strip()
        studio = lm.LMStudio()
        if not q or q.startswith("-"):
            return err("what should I download? e.g. qwen/qwen2.5-vl-7b")
        if not studio.installed:
            return err("LM Studio isn't installed")
        return JSONResponse(jobs.Job(f"Download {q}", [studio.lms, "get", q, "-y"], kind="download").to_dict())

    async def remove_model(request: Request):
        key = str((await body(request)).get("key", "")).removeprefix("local:")
        try:
            path = await run_in_threadpool(lm.LMStudio().remove, key)
        except (KeyError, RuntimeError) as e:
            return err(str(e).strip("'"))
        return JSONResponse({"ok": True, "removed": str(path)})

    async def serve_models(_: Request):
        try:
            await run_in_threadpool(lm.LMStudio().ensure_server)
        except RuntimeError as e:
            return err(str(e))
        return JSONResponse({"ok": True})

    # -- jobs ----------------------------------------------------------------------
    def job_or_404(request: Request):
        return jobs.get(request.path_params["id"])

    async def get_job(request: Request):
        j = job_or_404(request)
        return JSONResponse(j.to_dict()) if j else err("no such job", 404)

    async def job_input(request: Request):
        j = job_or_404(request)
        if not j:
            return err("no such job", 404)
        try:
            j.send(str((await body(request)).get("text", "")))
        except RuntimeError as e:
            return err(str(e))
        return JSONResponse({"ok": True})

    async def cancel_job(request: Request):
        j = job_or_404(request)
        if j:
            j.cancel()
        return JSONResponse({"ok": True})

    # -- notifications -------------------------------------------------------------
    async def get_notify(_: Request):
        cfg = notify.load()
        masked = {ch: {k: ("•••" if k in notify.SECRET_FIELDS and v else v) for k, v in c.items()}
                  for ch, c in cfg.items()}
        return JSONResponse({"channels": masked, "fields": notify.CHANNELS,
                             "secret_fields": sorted(notify.SECRET_FIELDS)})

    async def put_notify(request: Request):
        ch = request.path_params["channel"]
        if ch not in notify.CHANNELS:
            return err("unknown channel")
        b = await body(request)
        cfg = notify.load()
        old = cfg.get(ch, {})
        new = {}
        for f in notify.CHANNELS[ch]:
            v = str(b.get(f, "")).strip()
            new[f] = old.get(f, "") if v == "•••" else v   # unchanged masked secret
        required = [f for f in notify.CHANNELS[ch] if f != "token"]
        if any(not new[f] for f in required):
            return err(f"{ch} needs: {', '.join(required)}")
        if ch == "ntfy" and not new["url"].startswith(("https://", "http://")):
            return err("the ntfy URL should look like https://ntfy.sh/your-topic")
        cfg[ch] = new
        notify.save(cfg)
        return JSONResponse({"ok": True})

    async def delete_notify(request: Request):
        cfg = notify.load()
        cfg.pop(request.path_params["channel"], None)
        notify.save(cfg)
        return JSONResponse({"ok": True})

    async def test_notify(_: Request):
        res = await run_in_threadpool(notify.send, "Hangar test",
                                      "Notifications work. Agents will ping you here.", "done")
        return JSONResponse({"results": res})

    # -- settings --------------------------------------------------------------------
    async def get_settings(request: Request):
        st = load_settings()
        bound_all = request.app.state.host in ("0.0.0.0", "::")
        urls = [f"http://{ip}:{DAEMON_PORT}" for ip in lan_ips()] if bound_all else []
        qr = ""
        if urls:
            import segno
            link = f"{urls[0]}/login?token={auth.admin_token()}&next=%2F"
            qr = segno.make(link, error="m").svg_inline(scale=4, dark="#e8eaf0", light=None)
        return JSONResponse({"lan": bool(st.get("lan")), "lan_active": bool(urls), "lan_urls": urls,
                             "qr": qr, "local_url": f"http://127.0.0.1:{DAEMON_PORT}",
                             "state_dir": str(auth.STATE_DIR), "config_dir": str(auth.CONFIG_DIR)})

    async def put_settings(request: Request):
        b = await body(request)
        st = load_settings()
        if "lan" in b:
            st["lan"] = bool(b["lan"])
        auth.write_private(SETTINGS, json.dumps(st, indent=2))
        return JSONResponse({"ok": True, "restart_needed": True})

    async def restart(_: Request):
        """Re-exec the daemon so it picks up new settings (e.g. LAN access)."""
        def later():
            os.execv(sys.executable, [sys.executable, "-m", "hangar.daemon"])
        threading.Timer(0.5, later).start()
        return JSONResponse({"ok": True})

    # -- harnesses --------------------------------------------------------------------------
    async def list_harness(_: Request):
        have = await run_in_threadpool(harness.installed, eng)
        return JSONResponse([{"name": n, "title": h["title"],
                              "versions": [x["version"] for x in have if x["harness"] == n]}
                             for n, h in harness.HARNESSES.items()])

    async def install_harness(request: Request):
        name = request.path_params["name"]
        if name not in harness.HARNESSES:
            return err("unknown harness", 404)
        return JSONResponse((await run_in_threadpool(harness_job, name)).to_dict())

    # -- network allowlists ---------------------------------------------------------------
    async def get_network(request: Request):
        from hangar import network
        name = request.path_params["name"]
        try:
            a = await run_in_threadpool(eng.get, name)
        except NotFound:
            return err(f"no agent named '{name}'", 404)
        if a.network != "allowlist":
            return JSONResponse({"mode": "open"})
        return JSONResponse({"mode": "allowlist", "allow": network.read_allow(name),
                             "implied": network.implied(a.model),
                             "recent": await run_in_threadpool(network.recent, eng.client, name)})

    async def set_network(request: Request):
        from hangar import network
        name = request.path_params["name"]
        try:
            a = await run_in_threadpool(eng.get, name)
        except NotFound:
            return err(f"no agent named '{name}'", 404)
        if a.network != "allowlist":
            return err("this agent has open internet access; create it with an allowlist to limit it")
        b = await body(request)
        try:
            allow = network.write_allow(name, [str(d) for d in b.get("allow") or []])
        except ValueError as e:
            return err(str(e))
        return JSONResponse({"ok": True, "allow": allow})

    # -- setup wizard --------------------------------------------------------------------
    async def setup_state(_: Request):
        """Everything the setup wizard shows: hardware, model picks, system settings."""
        def gather():
            from hangar import catalog, hardware, hostsetup
            hw = hardware.detect()
            studio = lm.LMStudio()
            have = []
            if studio.installed:
                try:
                    have = [m.key for m in studio.list()]
                except RuntimeError:
                    pass
            return {"done": bool(load_settings().get("setup_done")), "hardware": hw,
                    "hardware_summary": hardware.summary(hw), "lmstudio": studio.installed,
                    "models": catalog.recommend(hw["model_budget_gb"], have), "host": hostsetup.current(),
                    "accounts": [{"name": a.name, "provider": a.provider} for a in accts.list()],
                    "notify": sorted(notify.load())}
        return JSONResponse(await run_in_threadpool(gather))

    async def setup_host(request: Request):
        from hangar import hostsetup
        b = await body(request)
        try:
            done = await run_in_threadpool(hostsetup.apply, b.get("language"), b.get("keyboard"), b.get("timezone"))
        except (ValueError, RuntimeError) as e:
            return err(str(e))
        return JSONResponse({"ok": True, "done": done})

    async def setup_network(_: Request):
        from hangar import hostsetup
        return JSONResponse(await run_in_threadpool(hostsetup.network))

    async def setup_wifi(request: Request):
        from hangar import hostsetup
        b = await body(request)
        try:
            await run_in_threadpool(hostsetup.connect_wifi, str(b.get("ssid", "")), str(b.get("password", "")))
        except RuntimeError as e:
            return err(str(e))
        return JSONResponse({"ok": True})

    async def setup_lmstudio(_: Request):
        from hangar import hostsetup
        try:
            argv = hostsetup.lmstudio_install_argv()
        except RuntimeError as e:
            return err(str(e))
        return JSONResponse(jobs.Job("Install LM Studio", argv, kind="download").to_dict())

    async def setup_done(request: Request):
        b = await body(request)
        st = load_settings()
        st["setup_done"] = bool(b.get("done", True))
        auth.write_private(SETTINGS, json.dumps(st, indent=2))
        return JSONResponse({"ok": True})

    A = "/api/accounts/{name}"
    return [
        Route("/api/harness", list_harness),
        Route("/api/harness/{name}/install", install_harness, methods=["POST"]),
        Route("/api/agents/{name}/network", get_network),
        Route("/api/agents/{name}/network", set_network, methods=["PUT"]),
        Route("/api/setup", setup_state),
        Route("/api/setup/host", setup_host, methods=["POST"]),
        Route("/api/setup/network", setup_network),
        Route("/api/setup/wifi", setup_wifi, methods=["POST"]),
        Route("/api/setup/done", setup_done, methods=["POST"]),
        Route("/api/setup/lmstudio", setup_lmstudio, methods=["POST"]),
        Route("/api/agents", create_agent, methods=["POST"]),
        Route("/api/agents/{name}", delete_agent, methods=["DELETE"]),
        Route("/api/options", options),
        Route("/api/accounts", list_accounts),
        Route("/api/accounts", add_account, methods=["POST"]),
        Route(A, remove_account, methods=["DELETE"]),
        Route(f"{A}/key", set_key, methods=["POST"]),
        Route(f"{A}/login", login, methods=["POST"]),
        Route(f"{A}/status", account_status),
        Route("/api/models", list_models),
        Route("/api/models/download", download_model, methods=["POST"]),
        Route("/api/models/remove", remove_model, methods=["POST"]),
        Route("/api/models/serve", serve_models, methods=["POST"]),
        Route("/api/jobs/{id}", get_job),
        Route("/api/jobs/{id}", cancel_job, methods=["DELETE"]),
        Route("/api/jobs/{id}/input", job_input, methods=["POST"]),
        Route("/api/notify", get_notify),
        Route("/api/notify/test", test_notify, methods=["POST"]),
        Route("/api/notify/{channel}", put_notify, methods=["PUT"]),
        Route("/api/notify/{channel}", delete_notify, methods=["DELETE"]),
        Route("/api/settings", get_settings),
        Route("/api/settings", put_settings, methods=["PUT"]),
        Route("/api/daemon/restart", restart, methods=["POST"]),
    ]
