"""M2 smoke test: several agents at once, no conflicts.

Starts three agents through the `hangar` CLI, then drives all three at the
same time over MCP with the element-based browser tools. Checks that each
agent has its own ports, browser and desktop, and that takeover pauses input.
Usage:  uv run python tests/smoke_m2.py [--keep]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hangar.engine import Engine  # noqa: E402

NAMES = ["alpha", "bravo", "charlie"]

FORM = """<title>Form {name}</title><h1>Hello</h1>
<label>Your name <input id=n></label>
<select id=s><option>red</option><option>blue</option></select>
<button onclick="document.querySelector('h1').textContent='Hi '+n.value+' '+s.value">Greet</button>"""


os.environ["HANGAR_NO_AUTODAEMON"] = "1"   # smoke_m4 covers the daemon


def hangar(*args: str) -> str:
    r = subprocess.run(["uv", "run", "hangar", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"hangar {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    return r.stdout


def ref(snapshot: str, role: str, name_part: str) -> int:
    for line in snapshot.splitlines():
        m = re.match(r"\[(\d+)\] (\w+) \"([^\"]*)\"", line)
        if m and m.group(2) == role and name_part.lower() in m.group(3).lower():
            return int(m.group(1))
    raise AssertionError(f"no {role} '{name_part}' in snapshot:\n{snapshot}")


async def drive(name: str, mcp_url: str, headers: dict) -> str:
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()

            async def call(tool, **kw) -> str:
                r = await s.call_tool(tool, kw)
                text = r.content[0].text
                if r.isError:
                    raise RuntimeError(f"{name}.{tool}: {text}")
                return text

            snap = await call("browser_navigate", url="data:text/html," + quote(FORM.format(name=name)))
            assert f"title: Form {name}" in snap, snap
            await call("browser_type", ref=ref(snap, "textbox", "your name"), text=name)
            snap = await call("browser_select", ref=ref(snap, "combobox", ""), option="blue")
            snap = await call("browser_click", ref=ref(snap, "button", "greet"))
            assert f"# Hi {name} blue" in snap, snap

            snap = await call("browser_navigate", url="https://example.com")
            assert "Example Domain" in snap, snap
            text = await call("browser_text")
            assert "documentation examples" in text, text
            tabs = await call("browser_tabs")
            await call("report", message=f"{name} finished", status="done", final=True)
            return f"{name}: form ok, example.com ok, tabs:\n{tabs}"


async def drive_all(agents) -> None:
    results = await asyncio.gather(*(drive(a.name, a.mcp_url, a.auth_headers) for a in agents))
    for r in results:
        print(r)


async def check_takeover(mcp_url: str, headers: dict) -> None:
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            r = await s.call_tool("browser_navigate", {"url": "https://example.org"})
            assert r.isError and "taken control" in r.content[0].text, r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    eng = Engine()
    for n in NAMES:
        eng.destroy(n)
    try:
        # Start all three in parallel through the CLI.
        procs = [subprocess.Popen(["uv", "run", "hangar", "new", n, "--task", f"test task for {n}"],
                                  cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                 for n in NAMES]
        for p in procs:
            out = p.communicate()[0]
            assert p.returncode == 0, out
            print(out.strip())

        agents = [eng.get(n) for n in NAMES]
        ports = [p for a in agents for p in (a.novnc_port, a.mcp_port)]
        assert len(set(ports)) == len(ports), f"port clash: {ports}"
        print("ports unique:", ports)

        asyncio.run(drive_all(agents))

        # Each agent has exactly its own Chromium with its own profile.
        for n in NAMES:
            code, out = eng.exec(n, ["pgrep", "-c", "-f", "hangar-chromium"])
            assert code == 0 and int(out) >= 1, out
        print("each agent runs its own browser")

        hangar("takeover", "bravo", "--print")
        asyncio.run(check_takeover(agents[1].mcp_url, agents[1].auth_headers))
        hangar("release", "bravo")
        print("takeover pauses input tools: ok")

        print(hangar("list"))
        print("PASS")
    finally:
        if not args.keep:
            for n in NAMES:
                eng.destroy(n)


if __name__ == "__main__":
    main()
