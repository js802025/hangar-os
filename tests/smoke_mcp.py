"""`hangar mcp` smoke test: Hangar as an MCP server for other agents.

No VM, no containers, no models: a fake `terminal-mac` stands in for the
Mac VM (a 2560x1600 Retina screen), and a real MCP client talks to
`hangar mcp` over stdio, as Claude Code would.

  1. The server lists Hangar's tools (hand a task over, or drive a Mac).
  2. mac_screenshot comes back shrunk to 1280 wide.
  3. Clicks at the screenshot's pixels land at the VM's real pixels; keys and
     text reach the VM.

  uv run --extra apps python tests/smoke_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PySide6.QtGui import QColor, QImage

TMP = Path(tempfile.mkdtemp(prefix="hangar-mcp-"))
LOG, SHOT, FAKE = TMP / "calls.jsonl", TMP / "screen.png", TMP / "terminal-mac"
_img = QImage(2560, 1600, QImage.Format_RGB32)
_img.fill(QColor("#334"))
_img.save(str(SHOT), "PNG")
FAKE.write_text(f"""#!/usr/bin/env python3
import json, shutil, sys
args = sys.argv[1:]
open({str(LOG)!r}, "a").write(json.dumps(args) + "\\n")
if args[2:3] == ["screenshot"]:
    shutil.copy({str(SHOT)!r}, args[args.index("--output") + 1])
print(json.dumps({{"ok": True}}))
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
ROOT = Path(__file__).resolve().parents[1]


def calls() -> list[list[str]]:
    return [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []


async def main() -> None:
    env = {**os.environ, "HANGAR_STATE_DIR": str(TMP / "state"), "HANGAR_CONFIG_DIR": str(TMP / "config"),
           "HANGAR_TERMINAL_MAC": str(FAKE)}
    server = StdioServerParameters(command=sys.executable, args=["-m", "hangar.cli", "mcp"], env=env,
                                   cwd=str(ROOT))
    async with stdio_client(server) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        assert "do_task" in (init.instructions or ""), init.instructions
        names = {t.name for t in (await s.list_tools()).tools}
        want = {"do_task", "wait", "look", "spaces", "stop_space", "mac_start", "mac_screenshot", "mac_click",
                "mac_type", "mac_key", "mac_scroll", "mac_drag", "mac_show", "mac_stop"}
        assert want <= names, want - names
        print("1. hangar mcp lists its tools: hand a task over, or drive a Mac: ok")

        shot = await s.call_tool("mac_screenshot", {})
        assert not shot.isError, shot
        import base64
        img = QImage.fromData(base64.b64decode(shot.content[0].data))
        assert (img.width(), img.height()) == (1280, 800), (img.width(), img.height())
        print("2. mac_screenshot: shrunk from 2560x1600 to 1280x800: ok")

        for tool, args in (("mac_click", {"x": 100, "y": 50}), ("mac_key", {"keys": "cmd+space"}),
                           ("mac_type", {"text": "Notes"}), ("mac_click", {"x": 10, "y": 20, "clicks": 2})):
            res = await s.call_tool(tool, args)
            assert not res.isError, (tool, res)
        acts = [c[2:] for c in calls() if c[:1] == ["action"] and c[2] != "screenshot"]
        assert acts == [["click", "200", "100"], ["key", "cmd+space"], ["type", "--", "Notes"],
                        ["click", "20", "40", "--double"]], acts
        print("3. clicks land at the VM's real pixels; keys and text reach the VM: ok")
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
