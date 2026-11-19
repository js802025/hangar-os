"""M9 smoke test: teams in one space, messaging rules, and the pilot.

  1. Two agents share one desktop, each with its own mouse and keyboard: they
     type into their own windows at the same time without mixing.
  2. The per-window lock stops one agent typing into a window the other is using.
  3. Each agent gets its own browser tab.
  4. Messages: teammates can talk; reaching another space directly is refused;
     the pilot can reach anyone.
  5. The pilot (a scripted fake model) creates a space with a team of two
     using its control tools; the user's limits (allowed models, max agents) hold.
Usage:  uv run python tests/smoke_m9.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-m9-"))
PORT, LLM_PORT = 7799, 1298
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT=str(PORT), HANGAR_LLM_URL=f"http://hangar-host:{LLM_PORT}/v1",
                  HANGAR_LLM_VISION="0")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from hangar import auth, pilot, spaces  # noqa: E402
from hangar.daemon import app, eng  # noqa: E402
from hangar.engine import AgentSpec  # noqa: E402

D = f"http://127.0.0.1:{PORT}"
SPACES = ["m9-team", "pilot", "m9-research"]


def tool_call(name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{time.time_ns()}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class FakeModel(BaseHTTPRequestHandler):
    """Pilot: models() → create_space(team of two) → final report.
    Any other agent: reports straight away."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tools = {t["function"]["name"] for t in body.get("tools", [])}
        turns = sum(m["role"] == "assistant" for m in body["messages"])
        if "create_space" in tools:
            if turns == 0:
                msg = tool_call("models", {})
            elif turns == 1:
                msg = tool_call("create_space", {"name": "m9-research", "agents": [
                    {"model": "local:fake", "role": "finder", "task": "find three sources"},
                    {"model": "local:fake", "role": "checker"}]})
            else:
                result = [m["content"] for m in body["messages"] if m["role"] == "tool"][-1]
                msg = tool_call("report", {"message": f"Pilot: {result}", "status": "done", "final": True})
        else:
            msg = tool_call("report", {"message": "found 3 sources", "status": "done", "final": True})
        out = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


async def session(url: str, headers: dict):
    cm = streamablehttp_client(url, headers=headers)
    r, w, _ = await cm.__aenter__()
    s = ClientSession(r, w)
    await s.__aenter__()
    await s.initialize()

    async def call(tool, **kw):
        res = await s.call_tool(tool, kw)
        text = "\n".join(c.text for c in res.content if c.type == "text")
        if res.isError:
            raise RuntimeError(text)
        return text

    async def close():
        await s.__aexit__(None, None, None)
        await cm.__aexit__(None, None, None)
    return call, close


def wait_for(pred, timeout=120, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            v = pred()
            if v:
                return v
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError(what)


async def team_tests() -> None:
    a = eng.get("m9-team")
    first, _c1 = await session(a.mcp_url, {**a.auth_headers, "X-Hangar-Agent": "m9-team"})
    second, _c2 = await session(a.mcp_url, {**a.auth_headers, "X-Hangar-Agent": "wren"})

    # 1. own windows, typed into at the same time
    for call, f, x in ((first, "a", 20), (second, "b", 660)):
        await call("open_app", name="terminal", args=f"-T win-{f} -geometry 70x12+{x}+120 -e sh -c 'cat > /tmp/{f}.txt'")
    await first("wait", seconds=1.5)
    await first("click", x=220, y=220)
    await second("click", x=860, y=220)
    await asyncio.gather(first("type_text", text="alpha from the first agent\n"),
                         second("type_text", text="bravo from wren\n"))
    await first("wait", seconds=1)
    _, out = eng.exec("m9-team", ["bash", "-c", "cat /tmp/a.txt; echo ---; cat /tmp/b.txt"])
    assert out.split("---")[0].strip() == "alpha from the first agent", out
    assert out.split("---")[1].strip() == "bravo from wren", out
    info = json.loads(await second("screen_info"))
    assert info["you"] == "wren" and "m9-team" in info["other_agents"], info
    print("1. two agents typed into their own windows at the same time, nothing mixed: ok")

    # 2. the per-window lock
    try:
        await second("click", x=220, y=220)
        raise AssertionError("wren was allowed into the first agent's window")
    except RuntimeError as e:
        lock_msg = str(e)
    assert "m9-team is using the window" in lock_msg, lock_msg
    print("2. per-window lock:", lock_msg[:70] + "…")

    # 3. own browser tabs
    await first("browser_navigate", url="https://example.com")
    await second("browser_navigate", url="data:text/html,<title>Wren's tab</title><h1>hi</h1>")
    assert "Example Domain" in await first("browser_snapshot")
    assert "Wren's tab" in await second("browser_snapshot")
    print("3. each agent keeps its own browser tab: ok")

    # 4. messages
    await first("send_message", to="wren", text="can you check the second source?")
    got = await second("read_messages")
    assert "can you check the second source?" in got and "m9-team" in got, got
    try:
        await second("send_message", to="pilot-x/agent", text="sneaky")
        raise AssertionError("cross-space message was allowed")
    except RuntimeError as e:
        assert "only message your own space" in str(e), e
    await second("send_message", to="pilot", text="wren here: done with sources")
    team = await first("team")
    assert "wren" in team and "(you)" in team, team
    ptr = httpx.get(f"{D}/api/agents/m9-team/pointers", headers={"Authorization": f"Bearer {auth.admin_token()}"}).json()
    assert {"m9-team", "wren"} <= set(ptr), ptr
    print("4. teammates message each other; other spaces only via the pilot; cursors:", ptr)


async def pilot_limits() -> None:
    call, _ = await session(f"{D}/pilot/mcp", {"Authorization": f"Bearer {auth.agent_token('pilot')}"})
    try:
        await call("create_space", name="m9-sneaky", agents=[{"model": "claude", "task": "x"}])
        raise AssertionError("pilot used a model the user didn't allow")
    except RuntimeError as e:
        assert "isn't one you may use" in str(e), e
    try:
        await call("create_space", name="m9-big", agents=[{"model": "local:fake"}] * 3)
        raise AssertionError("pilot went over the agent limit")
    except RuntimeError as e:
        assert "limit" in str(e), e
    try:
        await call("give_task", space="m9-team", agent="wren", task="hijack")
        raise AssertionError("pilot changed a space it didn't create")
    except RuntimeError as e:
        assert "isn't one of your spaces" in str(e), e
    await call("message", space="m9-team", agent="wren", text="pilot says hi")
    print("5b. pilot limits hold (models, max agents, user's spaces); it can message any space: ok")
    assert httpx.post(f"{D}/pilot/mcp", json={}).status_code == 401


def main() -> None:
    llm = ThreadingHTTPServer(("0.0.0.0", LLM_PORT), FakeModel)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    wait_for(lambda: httpx.get(f"{D}/healthz").status_code == 200, 20, "daemon")
    admin = {"Authorization": f"Bearer {auth.admin_token()}"}
    for n in SPACES:
        eng.destroy(n)
    try:
        eng.create(AgentSpec(name="m9-team"))
        eng.wait_ready("m9-team", timeout=120)
        e = spaces.add_agent(eng, "m9-team")
        assert e["name"] == "wren" and e["color"] != spaces.PALETTE[0], e
        asyncio.run(team_tests())

        pilot.save({"pilot": {"model": "local:fake"}, "allowed": [{"model": "local:fake"}],
                        "max_agents": 2, "internet": "open"})
        r = httpx.post(f"{D}/api/ask", json={"question": "research three sources on hangars"}, headers=admin,
                       timeout=240)
        assert r.status_code == 200, r.text
        wait_for(lambda: eng.get("m9-research") and len(spaces.roster(eng, "m9-research")) == 2, 240,
                 "pilot's space")
        roster = spaces.roster(eng, "m9-research")
        assert [m["role"] for m in roster] == ["finder", "checker"], roster
        final = wait_for(lambda: (r := eng.runtime("pilot")["agents"].get("pilot", {}).get("report"))
                         and r.get("final") and r, 180, "pilot's answer")
        assert "Created space m9-research" in final["message"], final
        finder = wait_for(lambda: (r := eng.runtime("m9-research")["agents"].get("m9-research", {}).get("report"))
                          and r.get("final") and r, 180, "finder's report")
        print("5a. pilot created m9-research with", [f"{m['name']} ({m['role']})" for m in roster],
              "| pilot:", final["message"][:60], "| finder:", finder["message"])
        asyncio.run(pilot_limits())
        print("PASS")
    finally:
        for n in SPACES:
            eng.destroy(n)
        server.should_exit = True
        llm.shutdown()


if __name__ == "__main__":
    main()
