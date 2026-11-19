"""Hangar for Mac smoke test: Mac computers (macOS VMs via terminal-mac).

No VM is booted: a fake `terminal-mac` records what it's asked and answers
like the real one (JSON, a PNG for screenshots). Checks:

  1. macvm turns Hangar's screen actions into terminal-mac commands
     (xdotool-style keys become Mac keys, scroll becomes --dy).
  2. The daemon's /computer routes accept the space's agent token, refuse
     others, and serve screenshots.
  3. The agent's MCP server swaps in Mac screen tools only when
     HANGAR_COMPUTER=mac, and keeps the Linux tool set otherwise.

Usage:  uv run python tests/smoke_mac.py
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-mac-"))
LOG = TMP / "calls.jsonl"
FAKE = TMP / "terminal-mac"
FAKE.write_text(f"""#!/usr/bin/env python3
import json, sys, base64
args = sys.argv[1:]
open({str(LOG)!r}, "a").write(json.dumps(args) + "\\n")
if args[:1] == ["action"] and args[2:3] == ["screenshot"]:
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    open(args[args.index("--output") + 1], "wb").write(png)
    print(json.dumps({{"path": args[args.index("--output") + 1]}}))
elif args[:1] == ["action"] and args[2:3] == ["status"]:
    print(json.dumps({{"ready": True, "width": 1280, "height": 800}}))
else:
    print(json.dumps({{"ok": True}}))
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_TERMINAL_MAC=str(FAKE))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hangar import auth, macvm  # noqa: E402


def calls() -> list[list[str]]:
    out = [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []
    LOG.unlink(missing_ok=True)
    return out


def test_macvm() -> None:
    assert macvm.mac_keys("ctrl+l Return super+space shift+Tab") == ["ctrl+l", "return", "cmd+space", "shift+tab"]
    macvm.act("vm1", "click", {"x": 10, "y": 20, "clicks": 2, "button": "right"})
    macvm.act("vm1", "type", {"text": "--not a flag"})
    macvm.act("vm1", "key", {"keys": "cmd+space Return"})
    macvm.act("vm1", "scroll", {"direction": "down", "amount": 2, "x": 5, "y": 6})
    macvm.act("vm1", "drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4})
    assert calls() == [
        ["action", "vm1", "click", "10", "20", "--double", "--button", "right"],
        ["action", "vm1", "type", "--", "--not a flag"],
        ["action", "vm1", "key", "cmd+space"], ["action", "vm1", "key", "return"],
        ["action", "vm1", "scroll", "--dy", "-200", "--x", "5", "--y", "6"],
        ["action", "vm1", "drag", "1", "2", "3", "4"],
    ]
    assert macvm.screenshot("vm1").startswith(b"\x89PNG")
    try:
        macvm.act("vm1", "click", {"y": 1})
        raise AssertionError("missing x not refused")
    except macvm.MacVMError:
        pass
    calls()
    print("1. macvm: actions become terminal-mac commands: ok")


def test_daemon() -> None:
    from starlette.testclient import TestClient

    from hangar import daemon
    from hangar.engine import Agent

    async def fake_get(name):
        if name == "macspace":
            return Agent(name=name, container_id="x", status="running", image="i", novnc_port=None,
                         mcp_port=None, computer="mac", vm="vm1")
        if name == "linux":
            return Agent(name=name, container_id="y", status="running", image="i", novnc_port=1, mcp_port=1)
        return None
    daemon.get_agent = fake_get
    c = TestClient(daemon.app)
    G = {"Authorization": f"Bearer {auth.agent_token('macspace')}"}
    other = {"Authorization": f"Bearer {auth.agent_token('linux')}"}
    r = c.post("/api/agents/macspace/computer/click", json={"x": 1, "y": 2}, headers=G)
    assert r.status_code == 200, r.text
    assert calls() == [["action", "vm1", "click", "1", "2"]]
    assert c.post("/api/agents/macspace/computer/click", json={"x": 1, "y": 2}, headers=other).status_code == 401
    assert c.post("/api/agents/macspace/computer/rm", json={}, headers=G).status_code == 404
    assert c.post("/api/agents/linux/computer/click", json={"x": 1, "y": 2},
                  headers={"Authorization": f"Bearer {auth.agent_token('linux')}"}).status_code == 404
    r = c.get("/api/agents/macspace/computer/screenshot", headers=G)
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    A = {"Authorization": f"Bearer {auth.admin_token()}"}
    assert c.get("/api/agents/macspace/screenshot.png", headers=A).content.startswith(b"\x89PNG")
    print("2. daemon: /computer routes, agent-token only, screenshots from the VM: ok")


def test_tools() -> None:
    code = ("import asyncio; from hangar_desktop import server; "
            "print(sorted(t.name for t in asyncio.run(server.mcp.list_tools())))")
    cwd = ROOT / "images/mcp-desktop"

    def tools(env: dict) -> list[str]:
        out = subprocess.run(["uv", "run", "--frozen", "python", "-c", code], cwd=cwd, capture_output=True,
                             text=True, env={**os.environ, "HANGAR_AGENT": "t", **env}, timeout=300)
        assert out.returncode == 0, out.stderr[-2000:]
        return eval(out.stdout.strip().splitlines()[-1])
    linux = tools({})
    mac = tools({"HANGAR_COMPUTER": "mac"})
    assert {"browser_navigate", "open_app", "app_snapshot", "request_login"} <= set(linux), linux
    assert not {"browser_navigate", "open_app", "app_snapshot", "request_login"} & set(mac), mac
    assert {"screenshot", "click", "type_text", "key", "scroll", "report", "notes_write"} <= set(mac), mac
    print(f"3. agent tools: Linux set unchanged ({len(linux)}), Mac set swapped in ({len(mac)}): ok")


def main() -> None:
    if sys.platform != "darwin":
        print("SKIP: Mac computers are Hangar for Mac only")
        return
    test_macvm()
    test_daemon()
    test_tools()
    print("PASS")


if __name__ == "__main__":
    main()
