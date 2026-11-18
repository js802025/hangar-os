"""M1 smoke test: start one agent, then act as an agent over MCP.

Takes a screenshot, opens the browser, checks it appeared, posts a report.
Usage:  uv run python tests/smoke_m1.py [--keep] [--name smoke]
Screenshots land in out/. With --keep the agent stays up so you can watch it
in noVNC; remove it later with `make clean`.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hangar.engine import AgentSpec, Engine  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out"


def as_json(result) -> dict:
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


def save_image(result, path: Path) -> int:
    img = next(c for c in result.content if c.type == "image")
    data = base64.b64decode(img.data)
    path.write_bytes(data)
    return len(data)


async def drive(mcp_url: str, headers: dict) -> None:
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("tools:", ", ".join(tools))

            info = as_json(await s.call_tool("screen_info", {}))
            print("screen:", info)

            n = save_image(await s.call_tool("screenshot", {}), OUT / "smoke-1-empty.png")
            print(f"screenshot 1: {n} bytes")

            r = await s.call_tool("open_app", {"name": "browser", "args": "https://example.com"})
            print("open_app:", r.content[0].text)
            await s.call_tool("wait", {"seconds": 4})

            info = as_json(await s.call_tool("screen_info", {}))
            title = info["active_window"]
            print("active window:", title)

            n = save_image(await s.call_tool("screenshot", {}), OUT / "smoke-2-browser.png")
            print(f"screenshot 2: {n} bytes")

            r = await s.call_tool("report", {"message": "Opened example.com", "status": "done", "final": True})
            print("report:", r.content[0].text)

            assert "chromium" in title.lower() or "example" in title.lower(), f"browser not focused: {title!r}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="smoke")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    eng = Engine()
    eng.destroy(args.name)
    agent = eng.create(AgentSpec(name=args.name))
    try:
        agent = eng.wait_ready(args.name)
        print(f"agent up: MCP {agent.mcp_url} (token-protected)")
        asyncio.run(drive(agent.mcp_url, agent.auth_headers))
        code, out = eng.exec(args.name, ["cat", "/home/agent/.hangar/reports.jsonl"])
        print("reports.jsonl:", out.strip())
        print("PASS")
    except Exception:
        print(eng.logs(args.name, tail=50))
        raise
    finally:
        if not args.keep:
            eng.destroy(args.name)


if __name__ == "__main__":
    main()
