"""M7 smoke test: snapshots, accessibility tools, network allowlists, flavors.

  1. Snapshots: `run` saves the workspace first; rollback restores it.
  2. Accessibility: open a GTK app (Mousepad), read it with app_snapshot, type
     into it with app_type, read it back with app_read.
  3. Network allowlist: an agent allowed only example.com can reach it (curl
     and its browser), is refused elsewhere, can't go around the gateway, and
     an edit to the allowlist applies without a restart.
  4. Flavors (whichever are built): research / coding tools exist; office runs
     LibreOffice Writer, which an agent can read and type into by element.
Usage:  uv run python tests/smoke_m7.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

os.environ["HANGAR_NO_AUTODAEMON"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

from hangar import network, snapshots  # noqa: E402
from hangar.engine import AgentSpec, Engine  # noqa: E402

eng = Engine()
NAMES = ["m7-a", "m7-net", "m7-office", "m7-coding", "m7-research"]


def sh(name: str, cmd: str) -> tuple[int, str]:
    return eng.exec(name, ["bash", "-c", cmd])


async def mcp(name: str, fn):
    a = eng.get(name)
    async with streamablehttp_client(a.mcp_url, headers=a.auth_headers) as (r, w, _), ClientSession(r, w) as s:
        await s.initialize()

        async def call(tool, **kw) -> str:
            res = await s.call_tool(tool, kw)
            text = "\n".join(c.text for c in res.content if c.type == "text")
            if res.isError:
                raise RuntimeError(f"{tool}: {text}")
            return text
        return await fn(call)


def find_ref(snap: str, pattern: str) -> int:
    for line in snap.splitlines():
        m = re.match(r"\s*\[(\d+)\] (.*)", line)
        if m and re.search(pattern, m.group(2)):
            return int(m.group(1))
    raise AssertionError(f"nothing matching {pattern!r} in:\n{snap[:3000]}")


def wait_for(pred, timeout=60, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(1)
    raise TimeoutError(what)


def test_snapshots() -> None:
    sh("m7-a", "mkdir -p ~/workspace/report && echo 'draft 1' > ~/workspace/report/draft.md")
    res = eng.run_task("m7-a", "rewrite the draft")      # no model: the task fails fast, the snapshot stays
    assert res["snapshot"] and res["snapshot"]["label"] == "before: rewrite the draft", res
    sh("m7-a", "echo 'ruined' > ~/workspace/report/draft.md; touch ~/workspace/stray")
    snapshots.rollback(eng, "m7-a", res["snapshot"]["id"])
    _, out = sh("m7-a", "cat ~/workspace/report/draft.md; ls ~/workspace")
    assert "draft 1" in out and "stray" not in out, out
    print("1. snapshot before `run`, rollback restores the workspace: ok")


def test_a11y() -> None:
    async def flow(call):
        await call("open_app", name="editor")
        await call("wait", seconds=2)
        snap = await call("app_snapshot", app="mousepad")
        ref = find_ref(snap, r"^(text|document text|paragraph) ")
        await call("app_type", ref=ref, text="hello from hangar")
        text = await call("app_read", ref=ref)
        assert "hello from hangar" in text, text
        snap = await call("app_snapshot", app="mousepad")
        assert re.search(r'menu "File"', snap), snap[:1500]
        return snap
    snap = asyncio.run(mcp("m7-a", flow))
    print(f"2. Mousepad through the accessibility tree ({len(snap.splitlines())} lines; typed + read back): ok")


def test_network() -> None:
    def code(url: str, extra: str = "") -> str:
        return sh("m7-net", f"curl -s -o /dev/null -m 15 {extra} -w '%{{http_code}}/%{{http_connect}}' {url}")[1].strip()

    assert code("https://example.com").startswith("200"), code("https://example.com")
    blocked = code("https://www.wikipedia.org")
    assert blocked.endswith("/403"), blocked
    bypass = code("https://example.com", "--noproxy '*'")
    assert bypass.startswith("000"), f"reached the internet around the gateway: {bypass}"
    rc, _ = sh("m7-net", "getent hosts example.com")
    assert rc != 0, "agent resolved an internet name directly"
    other_port = code("http://hangar-host:5000/")
    assert other_port.startswith("403"), other_port
    print("3a. allowed site 200, other site 403, no way around the gateway, no direct DNS: ok")

    async def browse(call):
        ok = await call("browser_navigate", url="https://example.com")
        assert "Example Domain" in ok, ok[:300]
        try:
            bad = await call("browser_navigate", url="https://en.wikipedia.org/wiki/Hangar")
        except RuntimeError as e:
            bad = str(e)
        assert "title: Hangar - Wikipedia" not in bad, bad[:300]
    asyncio.run(mcp("m7-net", browse))
    print("3b. the agent's browser goes through the gateway too: ok")

    async def fetch(call):
        ok = await call("web_fetch", url="https://example.com")
        assert "Example Domain" in ok, ok[:300]
        try:
            bad = await call("web_fetch", url="https://en.wikipedia.org/wiki/Hangar")
        except RuntimeError as e:
            bad = str(e)
        assert "Hangar - Wikipedia" not in bad and "aircraft" not in bad, bad[:300]
        out = await call("run_command", command="curl -s -o /dev/null -w '%{http_code}' https://en.wikipedia.org")
        assert "200" not in out, out
    asyncio.run(mcp("m7-net", fetch))
    print("3b'. web_fetch and run_command obey the allowlist too: ok")

    network.write_allow("m7-net", network.read_allow("m7-net") + ["wikipedia.org"])
    assert code("https://www.wikipedia.org").startswith(("200", "301")), code("https://www.wikipedia.org")
    hosts = {e["host"] for e in network.recent(eng.client, "m7-net") if e["action"] == "BLOCK"}
    assert "www.wikipedia.org" in hosts, hosts
    print("3c. allowlist edit applies live; blocked attempts are logged: ok")


def test_flavors() -> None:
    built = {f["name"] for f in eng.flavors()}
    print("   built flavors:", ", ".join(sorted(built)))
    if "research" in built:
        assert sh("m7-research", "pdftotext -v 2>&1; which evince")[0] == 0
        print("4a. research: pdftotext, evince: ok")
    if "coding" in built:
        rc, out = sh("m7-coding", "uv --version && gh --version | head -1 && gcc --version | head -1 && which geany")
        assert rc == 0, out
        print("4b. coding:", " · ".join(out.splitlines()[:3]))
    if "office" in built:
        async def writer(call):
            await call("open_app", name="libreoffice", args="--writer --norestore")
            snap = ""
            for _ in range(40):
                snap = await call("app_snapshot", app="soffice")
                if re.search(r"\] paragraph ", snap):
                    break
                await call("wait", seconds=2)
            para = find_ref(snap, r"^paragraph ")
            doc = find_ref(snap, r"^document text ")
            await call("app_type", ref=para, text="Quarterly report")
            return await call("app_read", ref=doc), snap
        text, snap = asyncio.run(mcp("m7-office", writer))
        assert "Quarterly report" in text, text[:500]
        print(f"4c. office: LibreOffice Writer read + typed by element ({len(snap.splitlines())} lines): ok")


def main() -> None:
    for n in NAMES:
        eng.destroy(n)
    built = {f["name"] for f in eng.flavors()}
    try:
        eng.create(AgentSpec(name="m7-a"))
        eng.create(AgentSpec(name="m7-net", allow=["example.com"]))
        for f in ("office", "coding", "research"):
            if f in built:
                eng.create(AgentSpec(name=f"m7-{f}", flavor=f, memory="3g"))
        for n in NAMES:
            try:
                eng.wait_ready(n, timeout=180)
            except Exception as e:   # not created (flavor not built)
                if "No such container" not in str(e) and "404" not in str(e):
                    raise
        test_snapshots()
        test_a11y()
        test_network()
        test_flavors()
        print("PASS")
    finally:
        for n in NAMES:
            eng.destroy(n)


if __name__ == "__main__":
    main()
