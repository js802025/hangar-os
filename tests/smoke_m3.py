"""M3 smoke test: the local-model harness, without needing a real model.

Runs a fake OpenAI-compatible server on the host that scripts a model:
  1. writes its first tool call as text (exercises the text-call parser),
  2. keeps repeating browser_navigate (exercises loop detection),
  3. after the nudge, answers with the page title it saw in the tool results.
Checks the agent's final report contains that heading. Also checks the
API-key account path (key stored 0600, never in the container's env).
Usage:  uv run python tests/smoke_m3.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hangar.accounts import Accounts  # noqa: E402
from hangar.engine import AgentSpec, Engine  # noqa: E402

PORT = 1299


def tool_call(name: str, args: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{time.time_ns()}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


class FakeModel(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        tool_text = "\n".join(m["content"] for m in msgs if m["role"] == "tool")
        nudged = any(m["role"] == "user" and "repeating" in str(m["content"]) for m in msgs)
        if len(msgs) == 2:   # first turn: a tool call written as plain text
            msg = {"role": "assistant", "content": 'browser_navigate("https://example.com")'}
        elif nudged and "title: Example Domain" in tool_text:
            msg = tool_call("report", {"message": "The page title is: Example Domain", "status": "done", "final": True})
        else:
            msg = tool_call("browser_navigate", {"url": "https://example.com"})
        out = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def wait_final(eng: Engine, name: str, timeout: float = 240) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rt = eng.runtime(name)
        r = rt["report"]
        if r and r.get("final"):
            return r
        time.sleep(2)
    raise TimeoutError(f"{name}: no final report\n{eng.read_file(name, '/home/agent/.hangar/logs/agent.log')[-3000:]}")


def main() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), FakeModel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    eng = Engine()
    accts = Accounts(eng)
    for n in ("m3-local", "m3-key"):
        eng.destroy(n)
    try:
        accts.remove("m3-fake")
    except KeyError:
        pass
    try:
        # 1. local harness against the fake model
        eng.create(AgentSpec(name="m3-local", model="local:fake", task="What is the heading on example.com?",
                             env={"HANGAR_LLM_URL": f"http://hangar-host:{PORT}/v1", "HANGAR_MAX_STEPS": "12"}))
        eng.wait_ready("m3-local", timeout=120)
        r = wait_final(eng, "m3-local")
        log = eng.read_file("m3-local", "/home/agent/.hangar/logs/agent.log")
        print("local report:", r["status"], "|", r["message"])
        assert r["status"] == "done" and "Example Domain" in r["message"], log[-2000:]
        assert "loop detected" in log, "loop detection didn't fire"
        print("local harness: text-call parsing, loop nudge, final report: ok")

        # 2. API-key account: stored safely, mounted, harness fails cleanly
        acct = accts.create("m3-fake", "anthropic-key")
        accts.set_api_key(acct, "sk-ant-api03-FAKE-TEST-KEY")
        vols, env = accts.mounts_and_env(acct)
        eng.create(AgentSpec(name="m3-key", model="claude", account="m3-fake", task="Say hi",
                             volumes=vols, env=env))
        eng.wait_ready("m3-key", timeout=120)
        _, ls = eng.exec("m3-key", ["stat", "-c", "%a %U", "/hangar/account/api_key"])
        assert ls.strip() == "600 agent", ls
        env_dump = eng.client.containers.get("hangar-m3-key").attrs["Config"]["Env"]
        assert not any("FAKE-TEST-KEY" in e for e in env_dump), "key leaked into container env"
        r = wait_final(eng, "m3-key")
        print("claude w/ fake key report:", r["status"], "|", r["message"][:100])
        assert r["status"] == "error", r
        print("API-key account: stored 0600, not in env, bad key reported as error: ok")
        print("PASS")
    finally:
        srv.shutdown()
        for n in ("m3-local", "m3-key"):
            eng.destroy(n)
        try:
            accts.remove("m3-fake")
        except (KeyError, ValueError):
            pass


if __name__ == "__main__":
    main()
