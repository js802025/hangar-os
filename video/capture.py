"""Capture the frames for the Hangar walkthrough video.

Runs a throwaway demo (its own state dir and daemon port), starts real agents
that browse and type, and records Hangar's windows offscreen while they work.
Nothing from your own screen is captured. Output: out/video/<scene>/NNNN.png

  QT_QPA_PLATFORM=offscreen uv run --extra apps python video/capture.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-video-"))
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT="7810", QT_QPA_PLATFORM="offscreen", XDG_DATA_HOME=str(TMP / "data"),
                  HANGAR_NOTES_DIR=str(TMP / "Notes"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out" / "video"

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from hangar import auth, pilot, spaces, vault  # noqa: E402
from hangar.engine import AgentSpec, Engine  # noqa: E402

eng = Engine()
SPACES = ["research", "studio", "inbox", "coder"]
SITE = "http://hangar-host:7811"
TASKS = {"research": ("research", "Compare the surviving airship hangars and their floor areas"),
         "inbox": ("inbox", "Sort today's newsletters and summarise the three worth reading"),
         "studio": ("studio", "Research airship hangars for the one-page brief"),
         "studio/wren": ("wren", "Write the brief as the research comes in"),
         "coder": ("coder", "Review PR #12 and check why the integration tests are pending")}


def serve_site() -> None:
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("0.0.0.0", 7811), functools.partial(Quiet, directory=str(ROOT / "video/site")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def set_task(space: str, agent: str, task: str) -> None:
    import shlex
    q = shlex.quote(task)
    eng.exec(space, ["bash", "-c", f"mkdir -p ~/.hangar/agents/{agent} && printf %s {q} > ~/.hangar/agents/{agent}/task.txt"
                     + (f" && printf %s {q} > ~/.hangar/task.txt" if agent == space else "")])


def tile_studio() -> None:
    """Browser on the left, the editor on the right, so both agents are visible."""
    eng.exec("studio", ["bash", "-c",
             "export DISPLAY=:1; for w in $(xdotool search --onlyvisible --class chromium); do "
             "xdotool windowsize $w 760 800 windowmove $w 0 0; done; "
             "for w in $(xdotool search --onlyvisible --class mousepad); do "
             "xdotool windowsize $w 520 800 windowmove $w 760 0 windowactivate $w; done"])


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        loop = QEventLoop()
        QTimer.singleShot(40, loop.quit)
        loop.exec()


def record(scene: str, widget, seconds: float, every: float = 1 / 12, until=None) -> None:
    """Grab `widget` repeatedly for `seconds` (12 fps is plenty: the renderer holds frames)."""
    d = OUT / scene
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    n, end = 0, time.time() + seconds
    while time.time() < end:
        widget.grab().save(str(d / f"{n:04d}.png"))
        n += 1
        pump(every)
    print(f"  {scene}: {n} frames")


def still(scene: str, widget) -> None:
    d = OUT / scene
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    widget.grab().save(str(d / "0000.png"))
    print(f"  {scene}: 1 frame")


# -- the agents' "work" (real MCP calls against their desktops) ----------------------------
async def drive(space: str, who: str, steps) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    a = eng.get(space)
    async with streamablehttp_client(a.mcp_url, headers={**a.auth_headers, "X-Hangar-Agent": who}) as (r, w, _), \
            ClientSession(r, w) as s:
        await s.initialize()
        for tool, args in steps:
            try:
                await s.call_tool(tool, args)
            except Exception:
                pass


def background(space: str, who: str, steps) -> threading.Thread:
    t = threading.Thread(target=lambda: asyncio.run(drive(space, who, steps)), daemon=True)
    t.start()
    return t


def setup_demo() -> None:
    for n in SPACES:
        eng.destroy(n)
    serve_site()
    for n in SPACES:
        eng.create(AgentSpec(name=n))
    for n in SPACES:
        eng.wait_ready(n, timeout=180)
    spaces.add_agent(eng, "studio", role="writer")          # -> wren
    models = {"research": "claude", "studio": "local:qwen/qwen3.5-9b", "inbox": "codex", "coder": "claude"}
    for n, role in (("research", "researcher"), ("studio", "researcher"), ("inbox", "assistant"), ("coder", "reviewer")):
        r = spaces.roster(eng, n)
        r[0]["role"] = role
        r[0]["model"] = models[n]
        if n == "studio":
            r[1]["model"] = "local:qwen/qwen3.5-9b"
        spaces.save(n, r)
    for key, (agent, task) in TASKS.items():
        set_task(key.split("/")[0], agent, task)
    v = vault.Vault()
    v.add("github.com", "jack@example.com", "xocdig-fipQy7-mytbab", "JBSWY3DPEHPK3PXP", label="GitHub")
    v.add("amazon.com", "jack@example.com", "password1", label="Amazon")
    v.add("accounts.google.com", "jack.demo@gmail.com", "Wq9-plMx-22ab-Kk3r-uuVz", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
          label="Google")
    v.add("notion.so", "jack@example.com", "Rv7-kqT2-mm9x-Pp4e", label="Notion")
    pilot.save({"pilot": {"model": "claude", "account": ""},
                    "allowed": [{"model": "claude", "account": ""}, {"model": "local:qwen/qwen3.5-9b", "account": ""}],
                    "max_agents": 6, "internet": "open"})
    # Opening pages, so every desktop has something on it.
    ts = [background("research", "research", [("browser_navigate", {"url": f"{SITE}/hangars.html"})]),
          background("inbox", "inbox", [("browser_navigate", {"url": f"{SITE}/inbox.html"})]),
          background("coder", "coder", [("browser_navigate", {"url": f"{SITE}/review.html"})]),
          background("studio", "studio", [("browser_navigate", {"url": f"{SITE}/hangars.html"})]),
          background("studio", "wren", [("open_app", {"name": "editor"}), ("wait", {"seconds": 2})])]
    for t in ts:
        t.join(90)
    tile_studio()
    asyncio.run(drive("studio", "wren", [("wait", {"seconds": 1}), ("click", {"x": 1000, "y": 400})]))
    for n, who, msg in (("research", "research", "Found four hangars still standing; comparing floor areas"),
                        ("inbox", "inbox", "12 newsletters sorted; 3 worth reading"),
                        ("studio", "studio", "Sources gathered for Hangar One, Akron and Cardington"),
                        ("studio", "wren", "Drafting the brief"),
                        ("coder", "coder", "The fix looks right; waiting on the integration run")):
        asyncio.run(drive(n, who, [("report", {"message": msg, "status": "working"})]))


CHAT = [
    ("user", "I need a one-page brief on the great airship hangars that are still standing.", {}),
    ("step", "Started a space for it: studio, with a researcher and a writer", {"space": "studio"}),
    ("step", "studio is gathering sources", {"space": "studio"}),
    ("step", "wren is drafting the brief", {"space": "studio"}),
    ("progress", "Found four hangars still standing; comparing floor areas", {}),
    ("report", "Brief drafted: three hangars, their size and history, with sources. Saved to your Notes.",
     {"space": "studio", "who": "wren", "status": "done"}),
    ("pilot", "Your brief is ready.\n\n"
              "- **Hangar One**, California (1933)\n"
              "- **Goodyear Airdock**, Akron (1929)\n"
              "- **Cardington Sheds**, England (1917)\n\n"
              "It's in your Notes as *Airship hangars brief*. The studio crew's changes are ready to review.",
     {"status": "done"}),
]

NOTES = {
    "Agents/studio/Airship hangars brief.md":
        "# Airship hangars brief\n\nThe great airship sheds were built for zeppelins in the 1910s-30s. "
        "A few still stand, and they are still among the largest single rooms ever built. #research\n\n"
        "## Still standing\n"
        "- **Hangar One**, Moffett Field, California (1933): 8 acres of floor\n"
        "- **Goodyear Airdock**, Akron, Ohio (1929): so big it once had its own weather\n"
        "- **Cardington Sheds**, Bedfordshire (1917 and 1928): now film studios\n\n"
        "## Why they matter\nSee [[Lighter than air]] for the background, and [[Trip ideas]] for visiting.\n",
    "Lighter than air.md": "# Lighter than air\n\nNotes on airships, from the Hindenburg to today's cargo designs.\n",
    "Trip ideas.md": "# Trip ideas\n\n- Akron in the autumn; ask about Airdock tours\n- [[Airship hangars brief]]\n",
    "Reading list.md": "# Reading list\n\n- *The Airmen Who Would Not Die*\n- *Skyfaring*\n",
    "Agents/inbox/Newsletter digest.md": "# Newsletter digest\n\nThree worth reading this week.\n",
}


def record_qml(scene: str, win, seconds: float, every: float = 1 / 12, script=None) -> None:
    """Grab a Qt Quick window repeatedly; script(t) runs between frames to move the story on."""
    d = OUT / scene
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    n, t0 = 0, time.time()
    while time.time() - t0 < seconds:
        if script:
            script(time.time() - t0)
        win.grabWindow().save(str(d / f"{n:04d}.png"))
        n += 1
        pump(every)
    print(f"  {scene}: {n} frames")


def still_qml(scene: str, win) -> None:
    d = OUT / scene
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    win.grabWindow().save(str(d / "0000.png"))
    print(f"  {scene}: 1 frame")


def find_obj(root, prop: str):
    from PySide6.QtCore import QObject
    for o in root.findChildren(QObject):
        if o.metaObject().indexOfProperty(prop) >= 0 and o.metaObject().indexOfProperty("tab") >= 0:
            return o
    return None


def main() -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PySide6.QtQuickControls2 import QQuickStyle

    from hangar import notes, snapshots, threads
    from hangar.apps import common, home
    app = common.make_app("spaces")
    pid = int((auth.STATE_DIR / "daemon.pid").read_text())
    try:
        print("setting up the demo…")
        setup_demo()
        for path, text in NOTES.items():
            notes.write(path, text)
        # The studio crew's workspace before the task, so Review has something to show.
        eng.exec("studio", ["bash", "-c", "cd ~/workspace && printf '# Brief\\n\\nTODO\\n' > brief.md && "
                                          "printf 'Sources\\n- wikipedia\\n' > sources.md && printf 'old\\n' > notes.txt"])
        snapshots.take(eng, "studio", "before: the airship hangars brief")
        eng.exec("studio", ["bash", "-c", "cd ~/workspace && cat > brief.md <<'EOF'\n"
                            "# The great airship hangars\n\nBuilt for zeppelins, a few still stand:\n\n"
                            "- Hangar One, Moffett Field, California (1933)\n"
                            "- Goodyear Airdock, Akron, Ohio (1929)\n"
                            "- Cardington Sheds, Bedfordshire (1917, 1928)\n\n"
                            "## Size\nHangar One covers 8 acres; the Airdock is 1,175 ft long.\nEOF\n"
                            "printf 'Sources\\n- NASA Ames history office\\n- Akron Beacon Journal archive\\n"
                            "- Historic England listing\\n' > sources.md && rm notes.txt && "
                            "printf 'Hangar,Year,Length (ft)\\nHangar One,1933,1133\\nAirdock,1929,1175\\n' > hangars.csv"])

        # Older chats, so the sidebar looks lived in.
        for title, user, answer in (("Newsletter digest", "Sort my newsletters", "Three are worth reading this week."),
                                    ("Review PR #12", "Review the open pull request",
                                     "The fix looks right; one test is still pending.")):
            t = threads.create(title)
            threads.add(t["id"], "user", user)
            threads.add(t["id"], "pilot", answer)
            time.sleep(0.05)

        QQuickStyle.setStyle("Basic")
        qmlRegisterType(home.LiveDesktop, "Hangar", 1, 0, "LiveDesktop")
        qmlRegisterType(home.Picture, "Hangar", 1, 0, "Picture")
        engine = QQmlApplicationEngine()
        thumbs = home.Thumbs()
        home.Picture.thumbs = thumbs
        engine.addImageProvider("thumbs", thumbs)
        bridge = home.Bridge(thumbs)
        engine.rootContext().setContextProperty("hangar", bridge)
        engine.addImportPath(str(home.QML))
        engine.load(QUrl.fromLocalFile(str(home.QML / "Home.qml")))
        win = engine.rootObjects()[0]
        win.resize(1600, 900)
        pump(6)

        # 1. Just ask: the conversation builds up as the crew works.
        brief = threads.create("Airship hangars brief")
        threads.add(brief["id"], *CHAT[0][:2], **CHAT[0][2])
        threads.set_active(brief["id"])
        bridge.refresh()
        bridge.openThread(brief["id"])
        pump(2)
        shown = {"n": 1}

        def story(t: float) -> None:
            want = min(len(CHAT), 1 + int(t / 1.25))
            while shown["n"] < want:
                kind, text, meta = CHAT[shown["n"]]
                threads.add(brief["id"], kind, text, **meta)
                if kind == "pilot":
                    threads.clear_active()
                shown["n"] += 1
                bridge.refresh()
                bridge.openThread(brief["id"])
        record_qml("chat", win, 11, script=story)

        # 2. Watch them work: the studio space, two agents at once.
        bridge.select("studio")
        pump(5)
        text = ("# The great airship hangars\n\n"
                "Built for zeppelins, a few still stand:\n"
                "- Hangar One, California (1933)\n"
                "- Goodyear Airdock, Akron (1929)\n"
                "- Cardington Sheds, England\n")
        background("studio", "wren", [("type_text", {"text": text})])
        background("studio", "studio", [("move_mouse", {"x": 260, "y": 330}), ("wait", {"seconds": 1}),
                                        ("scroll", {"direction": "down", "amount": 3, "x": 380, "y": 420}),
                                        ("move_mouse", {"x": 470, "y": 380}), ("wait", {"seconds": 1.2}),
                                        ("scroll", {"direction": "down", "amount": 3, "x": 470, "y": 420}),
                                        ("move_mouse", {"x": 330, "y": 470}), ("wait", {"seconds": 1}),
                                        ("move_mouse", {"x": 520, "y": 520})])
        record_qml("space", win, 10)

        # 3. Step in: take over the desktop.
        panel = find_obj(win, "control")
        panel.setProperty("control", True)
        pump(1.5)
        still_qml("takeover", win)
        panel.setProperty("control", False)

        # 4. Review the crew's changes.
        panel.setProperty("tab", 1)
        bridge.loadReview("studio")
        pump(4)
        still_qml("review", win)
        panel.setProperty("tab", 0)

        # 5. Everything at a glance.
        bridge.select("")
        bridge.overviewRequested.emit()
        pump(2)
        background("research", "research", [("scroll", {"direction": "down", "amount": 5, "x": 640, "y": 500})])
        background("inbox", "inbox", [("wait", {"seconds": 1.5}),
                                       ("scroll", {"direction": "down", "amount": 3, "x": 640, "y": 500})])
        record_qml("overview", win, 4, every=1 / 6)
        del engine
        pump(0.5)

        # 6. Notes, shared with the crew.
        from hangar.apps import notes as notes_app
        engine = QQmlApplicationEngine()
        nb = notes_app.Notes()
        engine.rootContext().setContextProperty("notes", nb)
        engine.addImportPath(str(home.QML))
        engine.load(QUrl.fromLocalFile(str(home.QML / "Notes.qml")))
        nwin = engine.rootObjects()[0]
        nwin.resize(1440, 860)
        pump(2)
        nb.open("Agents/studio/Airship hangars brief.md")
        pump(1.5)
        nwin.setProperty("editing", False)
        pump(1)
        still_qml("notes", nwin)
        del engine

        # 7. Sign-ins stay yours: an agent asks, the user approves in Passwords.
        import httpx
        httpx.post("http://127.0.0.1:7810/api/agents/research/vault/requests",
                   json={"kind": "login", "site": "github.com", "reason": "to open the pull requests you mentioned"},
                   headers={"Authorization": f"Bearer {auth.agent_token('research')}"}, timeout=10)
        from hangar.apps.passwords import PasswordsWindow
        pw = PasswordsWindow()
        pw.resize(1280, 760)
        pw.show()
        pump(4)
        pw.pick_entry(next(e["id"] for e in pw.data["entries"] if e["label"] == "GitHub"))
        pump(2)
        record("passwords", pw, 3, every=0.5)

        # 8. Ask from anywhere: the quick panel over whatever you're doing.
        from hangar.apps.ask import AskPanel
        p = AskPanel()
        shot = OUT / "space" / "0060.png"
        ctx = {"app": "Firefox", "title": "Hangar One · Moffett Field history", "url": "https://example.org/hangar-one",
               "selection": ""}
        p.open(shot if shot.exists() else None, ctx, {"screenshot", "url"})
        p.input.setPlainText("How big is this compared to the others?")
        pump(0.3)
        d = OUT / "ask"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        n = 0
        for _ in range(14):
            p.grab().save(str(d / f"{n:04d}.png"))
            n += 1
        p.answer.setText("Hangar One is the second largest of the three: about 8 acres, a little smaller than "
                         "Akron's Airdock. Both dwarf the Cardington sheds.")
        p.answer.show()
        p.adjustSize()
        pump(0.3)
        for _ in range(40):
            p.grab().save(str(d / f"{n:04d}.png"))
            n += 1
        print(f"  ask: {n} frames")
    finally:
        for n in SPACES:
            eng.destroy(n)
        os.kill(pid, 15)


if __name__ == "__main__":
    main()
