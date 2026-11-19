"""The pilot: the user's main agent, and the tools it orchestrates with.

The pilot is an agent like any other (space "pilot"), running the model
the user picked. Its harness gets a second MCP server, "control", served by the
daemon at /pilot/mcp, to create spaces and agents, hand out tasks, read
reports and pass messages between spaces.

Its limits are enforced here, from the user's orchestration settings:
  - only models on the user's allowed list,
  - at most `max_agents` of its agents running at once,
  - its spaces get the internet policy the user chose (it can't widen it),
  - it can change only spaces it created (it can read and message any),
  - password requests still go to the user (the vault never trusts it).
"""

from __future__ import annotations

import json

from docker.errors import NotFound
from mcp.server.fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool

from hangar import auth, spaces
from hangar.engine import LABEL, AgentSpec, Engine
from hangar.messages import PILOT, Bus

SETTINGS = auth.CONFIG_DIR / "settings.json"
CREATED_BY = f"{LABEL}.created_by"
DEFAULTS = {"pilot": {"model": "", "account": ""}, "allowed": [], "max_agents": 4, "internet": "open"}


def load() -> dict:
    try:
        st = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        st = {}
    orch = dict(st.get("orchestration") or {})
    if "pilot" not in orch and "conductor" in orch:   # saved before the conductor became the pilot
        orch["pilot"] = orch.pop("conductor")
    return {**DEFAULTS, **orch}


def save(orch: dict) -> dict:
    try:
        st = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        st = {}
    clean = {"pilot": {"model": str(orch.get("pilot", {}).get("model", "")),
                           "account": str(orch.get("pilot", {}).get("account", ""))},
             "allowed": [{"model": str(a.get("model", "")), "account": str(a.get("account", ""))}
                         for a in orch.get("allowed", []) if a.get("model")],
             "max_agents": max(1, min(int(orch.get("max_agents", 4)), 32)),
             "internet": orch.get("internet") if orch.get("internet") in ("open", "allowlist") else "open"}
    st["orchestration"] = clean
    auth.write_private(SETTINGS, json.dumps(st, indent=2))
    return clean


def allowed(model: str, account: str = "") -> bool:
    for a in load()["allowed"]:
        if a["model"] == model and (not a["account"] or not account or a["account"] == account):
            return True
    return False


def step(text: str, space: str = "") -> None:
    """Record what the pilot did in its current conversation."""
    from hangar import threads
    tid = threads.active()
    if tid:
        threads.add(tid, "step", text, space=space)
        if space:
            threads.link_space(space, tid)


def build(eng: Engine, bus: Bus) -> FastMCP:
    # host="0.0.0.0": the pilot connects as hangar-host; the daemon checks its token.
    mcp = FastMCP("hangar-control", host="0.0.0.0", streamable_http_path="/mcp", log_level="WARNING",
                  instructions="Run Hangar's other agents for the user. See models() first.")

    def mine(space: str) -> bool:
        try:
            return eng.get(space) and eng.client.containers.get(f"hangar-{space}").labels.get(CREATED_BY) == PILOT
        except NotFound:
            return False

    def running_agents() -> int:
        n = 0
        for a in eng.list():
            c = eng.client.containers.get(f"hangar-{a.name}")
            if c.labels.get(CREATED_BY) == PILOT and a.status == "running":
                n += len(spaces.roster(eng, a.name))
        return n

    def check_model(model: str, account: str) -> None:
        if not allowed(model, account):
            ok = ", ".join(f"{a['model']}{' (' + a['account'] + ')' if a['account'] else ''}" for a in load()["allowed"])
            raise ValueError(f"model {model!r} isn't one you may use. Allowed: {ok or 'none (the user must allow some)'}")

    @mcp.tool()
    async def models() -> str:
        """Which models you may give agents, your limits, and how many of your agents run now."""
        o = load()
        n = await run_in_threadpool(running_agents)
        lines = [f"- {a['model']}" + (f" (account {a['account']})" if a["account"] else "") for a in o["allowed"]]
        return ("Models you may use:\n" + ("\n".join(lines) or "- none: ask the user to allow some in Settings")
                + f"\nMax agents running at once: {o['max_agents']} (now: {n})"
                + f"\nNew spaces' internet: {o['internet']}")

    @mcp.tool()
    async def spaces_overview() -> str:
        """Every space, its agents, what they're doing and their latest report."""
        def gather():
            out = []
            for a in eng.list():
                if a.name == PILOT:
                    continue
                st = eng.state(a)
                owner = "yours" if mine(a.name) else "the user's"
                out.append(f"## {a.name} ({a.flavor}, {st['state']}, {owner})")
                for m in st["agents"]:
                    out.append(f"- {m['name']} [{m['model'] or 'no model'}] {m['state']}"
                               + (f" | role: {m['role']}" if m.get("role") else "")
                               + (f" | task: {m['task'][:140]}" if m.get("task") else "")
                               + (f" | latest: {m['report']['message'][:200]}" if m.get("report") else ""))
            return "\n".join(out) or "No spaces yet."
        return await run_in_threadpool(gather)

    @mcp.tool()
    async def create_space(name: str, agents: list[dict], flavor: str = "base",
                           sites: list[str] | None = None) -> str:
        """Create a space (one shared desktop) with a team of agents and start their tasks.
        agents: [{"name": optional, "model": "...", "account": optional, "role": "...", "task": "..."}].
        flavor: base | research | coding | office. sites: domains the space may reach (used when
        the user's policy limits new spaces' internet; ignored when it's open)."""
        if not agents:
            raise ValueError("give the space at least one agent")
        o = load()
        for ag in agents:
            check_model(ag.get("model", ""), ag.get("account", ""))
        n = await run_in_threadpool(running_agents)
        if n + len(agents) > o["max_agents"]:
            raise ValueError(f"that would run {n + len(agents)} agents; the user's limit is {o['max_agents']}. "
                             "Reuse or stop a space, or use fewer agents.")
        from hangar.prepare import prepare
        first = agents[0]
        spec = AgentSpec(name=name, flavor=flavor, model=first["model"], account=first.get("account", ""),
                         allow=list(sites or []) if o["internet"] == "allowlist" else None,
                         labels={CREATED_BY: PILOT})

        def make():
            prepare(eng, spec)
            eng.create(spec)
            eng.wait_ready(name, timeout=180)
            roster = spaces.roster(eng, name)
            roster[0]["role"] = first.get("role", "")
            spaces.save(name, roster)
            names = [name]
            for ag in agents[1:]:
                e = spaces.add_agent(eng, name, ag.get("name", ""), ag["model"], ag.get("account", ""),
                                     ag.get("role", ""))
                names.append(e["name"])
            for nm, ag in zip(names, agents):
                if ag.get("task"):
                    eng.run_task(name, ag["task"], agent=nm, snapshot=False)
            return names
        names = await run_in_threadpool(make)
        step(f"Started space {name} with {', '.join(names)}", name)
        return f"Created space {name} with {', '.join(names)}."

    @mcp.tool()
    async def add_agent(space: str, model: str, role: str = "", task: str = "", name: str = "",
                        account: str = "") -> str:
        """Add an agent to one of your spaces (it shares that desktop, with its own mouse and tab)."""
        if not await run_in_threadpool(mine, space):
            raise ValueError(f"{space} isn't one of your spaces")
        check_model(model, account)
        if await run_in_threadpool(running_agents) + 1 > load()["max_agents"]:
            raise ValueError(f"the user's limit is {load()['max_agents']} running agents")

        def go():
            e = spaces.add_agent(eng, space, name, model, account, role)
            if task:
                eng.run_task(space, task, agent=e["name"], snapshot=False)
            return e["name"]
        added = await run_in_threadpool(go)
        step(f"Added {added} to {space}" + (f": {task[:120]}" if task else ""), space)
        return f"Added {added} to {space}."

    @mcp.tool()
    async def give_task(space: str, agent: str, task: str) -> str:
        """Give an idle agent in one of your spaces its next task."""
        if not await run_in_threadpool(mine, space):
            raise ValueError(f"{space} isn't one of your spaces; message its agents instead")
        await run_in_threadpool(lambda: eng.run_task(space, task, agent=agent))
        step(f"Asked {agent} in {space}: {task[:160]}", space)
        return f"{agent} in {space} is working on it."

    @mcp.tool()
    async def message(space: str, agent: str, text: str) -> str:
        """Send a message to an agent in any space (e.g. pass on what another space found)."""
        names = [m["name"] for m in await run_in_threadpool(spaces.roster, eng, space)]
        if agent not in names:
            raise ValueError(f"no agent {agent} in {space} (agents: {', '.join(names)})")
        bus.send(PILOT, PILOT, space, agent, text)
        step(f"Passed a note to {agent} in {space}", space)
        return f"sent to {space}/{agent}"

    @mcp.tool()
    async def read_messages(wait_seconds: int = 0) -> str:
        """Messages agents (or the user) sent you. wait_seconds waits up to 50s for one."""
        msgs = await bus.take(PILOT, PILOT, min(max(wait_seconds, 0), 50))
        return "\n".join(f"[{m['ts'][11:19]}] {m['from']}: {m['text']}" for m in msgs) or "No new messages."

    @mcp.tool()
    async def reports(space: str, agent: str = "", n: int = 8) -> str:
        """Recent reports from a space (optionally one agent)."""
        def gather():
            text = eng.read_file(space, "/home/agent/.hangar/reports.jsonl")
            out = []
            for line in text.splitlines()[-300:]:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if agent and (r.get("who") or space) != agent:
                    continue
                out.append(f"[{r['ts'][11:19]}] {r.get('who') or space} {r['status']}"
                           f"{' (final)' if r.get('final') else ''}: {r['message'][:600]}")
            return "\n".join(out[-max(1, min(n, 50)):]) or "No reports yet."
        return await run_in_threadpool(gather)

    @mcp.tool()
    async def stop_space(space: str, delete: bool = False) -> str:
        """Stop one of your spaces (delete=True also removes it and its files)."""
        if not await run_in_threadpool(mine, space):
            raise ValueError(f"{space} isn't one of your spaces")
        await run_in_threadpool(eng.destroy if delete else eng.stop, space)
        step(f"{'Deleted' if delete else 'Stopped'} space {space}", space)
        return f"{'Deleted' if delete else 'Stopped'} {space}."

    return mcp


def ensure(eng: Engine) -> None:
    """Create the pilot's own space from the user's settings, if needed."""
    try:
        eng.get(PILOT)
        return
    except NotFound:
        pass
    o = load()["pilot"]
    if not o["model"]:
        raise ValueError("Pick a model for the pilot first: Hangar Settings → Orchestration "
                         "(or `hangar pilot set <model>`).")
    from hangar.prepare import prepare
    spec = AgentSpec(name=PILOT, model=o["model"], account=o["account"], env={"HANGAR_PILOT": "1"},
                     labels={CREATED_BY: "hangar"}, memory="3g")
    prepare(eng, spec)
    eng.create(spec)
    eng.wait_ready(PILOT, timeout=180)


def attach(eng: Engine, data: bytes) -> str:
    """Copy a screenshot into the pilot's workspace; returns its path there."""
    import io
    import tarfile
    import time
    name = f"ask-{time.strftime('%Y%m%d-%H%M%S')}.png"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        d = tarfile.TarInfo("asks")
        d.type, d.mode, d.uid, d.gid = tarfile.DIRTYPE, 0o755, 1000, 1000
        tar.addfile(d)
        f = tarfile.TarInfo(f"asks/{name}")
        f.size, f.mode, f.uid, f.gid = len(data), 0o644, 1000, 1000
        tar.addfile(f, io.BytesIO(data))
    eng.client.containers.get(f"hangar-{PILOT}").put_archive("/home/agent/workspace", buf.getvalue())
    return f"/home/agent/workspace/asks/{name}"


def ask(eng: Engine, bus: Bus, question: str, new: bool = False, context: str = "",
        screenshot: bytes | None = None, thread: str = "") -> dict:
    """Hand the user's message (plus any context they chose to include) to the
    pilot, as part of a conversation (a new one unless `thread` is given), or
    queue it as a message if the pilot is busy."""
    from hangar import threads
    ensure(eng)
    t = threads.get(thread) if thread else threads.create()
    earlier = threads.history_prompt(t) if t["items"] else ""
    threads.add(t["id"], "user", question, context=context, screenshot=bool(screenshot))
    text = question + (f"\n\nWhat I'm looking at:\n{context}" if context else "")
    if earlier:   # the conversation so far, so any harness picks up where it left off
        text = f"Our conversation so far:\n{earlier}\n\nThe user now says:\n{text}"
    env = {}
    if screenshot:
        path = attach(eng, screenshot)
        text += f"\n\nI attached a screenshot: {path}"
        env["HANGAR_ATTACH"] = path
    rt = eng.runtime(PILOT)
    if rt["agents"].get(PILOT, {}).get("busy"):
        bus.send("user", "user", PILOT, PILOT, text)
        return {"queued": True, "thread": t["id"]}
    threads.set_active(t["id"])
    eng.run_task(PILOT, text, snapshot=False, extra_env=env)
    return {"queued": False, "thread": t["id"]}
