"""Capture the frames for the Hangar demo video: one real-ish task, start to finish.

The story: from the Hangar OS desktop, you press the Ask shortcut and ask
Hangar to find one under $500 near Boston, shortlist three
and message the best seller. The pilot starts a crew (a researcher browsing
listings, another reading reviews of the likely picks), other jobs keep running, a
sign-in waits for your OK, you step in for a moment, review what changed,
and get one answer with the shortlist in your notes.

Runs a throwaway demo (its own state dir, notes folder and daemon port) with
real agent containers driven over MCP, and records Hangar's windows offscreen.
Nothing from your own screen is captured. Demo websites are local
(video/site/). Output: out/video/<scene>/NNNN.png

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
SPACES = ["bikes", "inbox", "coder", "trip", "expenses"]
FLAVOR = {"expenses": "office"}
SITE = "http://hangar-host:7811"
DAEMON = "http://127.0.0.1:7810"

ASK = ("Find me a used road bike under $500 near Boston, 56 cm frame. "
       "Shortlist the best three and message the top seller about a test ride.")
PLAN = [
    ("step", "Started a space for it: bikes, with two researchers", {"space": "bikes"}),
    ("step", "bikes is searching PedalSwap for road bikes under $500", {"space": "bikes"}),
    ("step", "wren is reading reviews of the most promising models", {"space": "bikes"}),
    ("progress", "12 listings under $500 within 15 miles; 5 in a 56 cm frame", {}),
]
RESULT = [
    ("report", "Reviews agree: the 2021+ Domane AL 2 is the best all-rounder under $500 used; the Allez is "
               "lighter, the CAAD8 stiffer but older.", {"space": "bikes", "who": "wren", "status": "done"}),
    ("report", "Shortlist saved to your Notes. Messaged the Trek's seller about a test ride on Saturday.",
     {"space": "bikes", "who": "bikes", "status": "done"}),
    ("pilot", "Here's your shortlist, all 56 cm and within 15 miles:\n\n"
              "1. **Trek Domane AL 2** (2021), $450 in Somerville: newest, new chain and bar tape\n"
              "2. **Specialized Allez** (2019), $420 in Cambridge: lightest of the three\n"
              "3. **Cannondale CAAD8** (2016), $380 in Brookline: cheapest, older groupset\n\n"
              "I messaged the Trek's seller asking about a test ride on Saturday. The full comparison "
              "is in your Notes as *Road bike shortlist*.", {"status": "done"}),
]
NOTES = {
    "Agents/bikes/Road bike shortlist.md":
        "# Road bike shortlist\n\nUsed road bikes under $500 near Boston, 56 cm frame. #bikes\n\n"
        "| Bike | Price | Year | Where | Notes |\n|---|---|---|---|---|\n"
        "| **Trek Domane AL 2** | $450 | 2021 | Somerville | new chain and bar tape; seller messaged |\n"
        "| Specialized Allez | $420 | 2019 | Cambridge | lightest, Sora groupset |\n"
        "| Cannondale CAAD8 | $380 | 2016 | Brookline | cheapest, needs new tyres |\n\n"
        "Checks for the test ride come from [[Buying a used bike]].\n",
    "Buying a used bike.md": "# Buying a used bike\n\n- Chain wear, brake pads, wheel wobble\n- Ask for the "
                             "service history\n- See [[Road bike shortlist]]\n",
    "Weekend plans.md": "# Weekend plans\n\n- Saturday: test ride (see [[Road bike shortlist]])\n- Sunday: farmers market\n",
    "Reading list.md": "# Reading list\n\n- *The Rider*\n- *Bike Snob*\n",
    "Agents/inbox/Newsletter digest.md": "# Newsletter digest\n\nThree worth reading this week.\n",
}


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


def tile(space: str, seconds: int = 14) -> None:
    """Keep browser windows side by side as they open (the lead's left, wren's right),
    in the background inside the space."""
    script = ("export DISPLAY=:1; end=$((SECONDS+%d)); while [ $SECONDS -lt $end ]; do i=0; "
              "for w in $(xdotool search --onlyvisible --class chromium | sort -n); do "
              "xdotool windowstate --remove MAXIMIZED_VERT,MAXIMIZED_HORZ $w 2>/dev/null; "
              "xdotool windowsize $w 640 800 windowmove $w $((i*640)) 0; i=$((i+1)); done; sleep 0.2; done") % seconds
    eng.client.containers.get(f"hangar-{space}").exec_run(["bash", "-c", script], user="agent", detach=True)


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        loop = QEventLoop()
        QTimer.singleShot(40, loop.quit)
        loop.exec()


def _dir(scene: str) -> Path:
    d = OUT / scene
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def record(scene: str, grab, seconds: float, every: float = 1 / 12, script=None) -> None:
    """Grab repeatedly for `seconds`; script(t) runs between frames to move the story on."""
    d, n, t0 = _dir(scene), 0, time.time()
    while time.time() - t0 < seconds:
        if script:
            script(time.time() - t0)
        grab().save(str(d / f"{n:04d}.png"))
        n += 1
        pump(every)
    print(f"  {scene}: {n} frames")


def still(scene: str, grab) -> None:
    grab().save(str(_dir(scene) / "0000.png"))
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
    from hangar import notes, threads
    for n in SPACES:
        eng.destroy(n)
    serve_site()
    for n in SPACES:
        eng.create(AgentSpec(name=n, flavor=FLAVOR.get(n, "base")))
    for n in SPACES:
        eng.wait_ready(n, timeout=180)
    spaces.add_agent(eng, "bikes", role="reviews")         # -> wren
    for n, role, model in (("bikes", "researcher", "claude"), ("inbox", "assistant", "codex"),
                           ("coder", "reviewer", "claude"), ("trip", "planner", "claude"),
                           ("expenses", "bookkeeper", "local:qwen/qwen3.5-9b")):
        r = spaces.roster(eng, n)
        r[0]["role"], r[0]["model"] = role, model
        if n == "bikes":
            r[1]["model"] = "local:qwen/qwen3.5-9b"
        spaces.save(n, r)
    set_task("bikes", "bikes", "Find used road bikes under $500 near Boston, 56 cm")
    set_task("bikes", "wren", "Read reviews of the most promising models")
    set_task("inbox", "inbox", "Sort today's newsletters and summarise the three worth reading")
    set_task("coder", "coder", "Review PR #12 and check why the integration tests are pending")
    set_task("trip", "trip", "Find flights and a quiet place to stay in Lisbon, 14-17 March")
    set_task("expenses", "expenses", "Put October's receipts into the expense report")
    eng.exec("expenses", ["bash", "-c", "printf 'Date,Merchant,Category,Amount\\n"
             "Oct 2,Blue Bottle,Meals,14.50\\nOct 3,Delta,Travel,412.00\\nOct 3,Hilton,Lodging,389.20\\n"
             "Oct 9,Uber,Travel,38.75\\nOct 14,Staples,Office,62.10\\nOct 21,Zoom,Software,15.99\\n"
             "Oct 22,Sweetgreen,Meals,18.40\\nOct 28,Amtrak,Travel,96.00\\n,,Total,1036.94\\n' "
             "> ~/workspace/october.csv"])
    v = vault.Vault()
    v.add("pedalswap.com", "jack@example.com", "xocdig-fipQy7-mytbab", label="PedalSwap")
    v.add("github.com", "jack@example.com", "Wq9-plMx-22ab-Kk3r", "JBSWY3DPEHPK3PXP", label="GitHub")
    pilot.save({"pilot": {"model": "claude", "account": ""},
                "allowed": [{"model": "claude", "account": ""}, {"model": "local:qwen/qwen3.5-9b", "account": ""}],
                "max_agents": 6, "internet": "open"})
    for path, text in NOTES.items():
        notes.write(path, text)
    for title, user, answer in (("October expense report", "Do my expense report from the receipts folder",
                                 "Done: 8 receipts, $1,036.94, ready to submit."),
                                ("Lisbon in March", "Plan a long weekend in Lisbon",
                                 "Two flight options and two quiet places to stay."),
                                ("This week's newsletters", "Sort my newsletters", "Three are worth reading this week."),
                                ("Review PR #12", "Review the open pull request",
                                 "The fix looks right; one integration test is still pending.")):
        t = threads.create(title)
        threads.add(t["id"], "user", user)
        threads.add(t["id"], "pilot", answer)
        time.sleep(0.05)
    # The page you're reading when you ask (shown from the coder space's browser, then moved on).
    ts = [background("coder", "coder", [("browser_navigate", {"url": f"{SITE}/review.html"})]),
          background("inbox", "inbox", [("browser_navigate", {"url": f"{SITE}/inbox.html"})]),
          background("trip", "trip", [("browser_navigate", {"url": f"{SITE}/trip.html"})]),
          background("expenses", "expenses", [("open_app", {"name": "libreoffice", "args":
                     "--calc --infilter=CSV:44,34,76,1 /home/agent/workspace/october.csv"}), ("wait", {"seconds": 6})])]
    for t in ts:
        t.join(120)
    for n, who, msg in (("bikes", "bikes", "Searching PedalSwap: 12 road bikes under $500 nearby"),
                        ("bikes", "wren", "Reading reviews of the Domane, Allez and CAAD8"),
                        ("inbox", "inbox", "23 newsletters sorted; summarising the three worth reading"),
                        ("coder", "coder", "The fix looks right; waiting on the integration run"),
                        ("trip", "trip", "Two nonstop flights under $700; checking quiet places to stay"),
                        ("expenses", "expenses", "8 receipts entered; checking totals")):
        asyncio.run(drive(n, who, [("report", {"message": msg, "status": "working"})]))


def os_desktop(w: int, h: int):
    """The Hangar OS desktop as you'd see it after logging in: the wallpaper and a
    Plasma-style panel with Hangar, Notes and Passwords pinned and ✦ in the tray."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    wall = QImage(str(ROOT / "os/files/usr/share/wallpapers/Hangar/contents/images/3840x2160.png"))
    p.drawImage(QRectF(0, 0, w, h), wall.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
    ph = 52
    p.fillRect(QRectF(0, h - ph, w, ph), QColor(16, 19, 26, 235))
    p.fillRect(QRectF(0, h - ph, w, 1), QColor(255, 255, 255, 18))
    icons = ROOT / "os/files/usr/share/icons/hicolor/256x256/apps"
    x = 14
    # app launcher
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 22))
    p.drawRoundedRect(QRectF(x, h - ph + 8, 36, 36), 9, 9)
    path = QPainterPath()
    cx, cy = x + 18, h - ph + 26
    path.moveTo(cx - 10, cy + 5); path.lineTo(cx, cy - 6); path.lineTo(cx + 10, cy + 5)
    from PySide6.QtGui import QPen
    p.setPen(QPen(QColor("#eceef4"), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    x += 52
    for name in ("spaces", "notes", "passwords"):
        icon = QImage(str(icons / f"dev.hangar.{name}.png"))
        p.drawImage(QRectF(x, h - ph + 8, 36, 36), icon)
        x += 46
    # tray and clock
    f = QFont("Inter")
    f.setPixelSize(15)
    p.setFont(f)
    p.setPen(QColor("#7c9cff"))
    p.drawText(QRectF(w - 150, h - ph, 30, ph), Qt.AlignCenter, "✦")
    p.setPen(QColor("#c9cfdb"))
    p.drawText(QRectF(w - 110, h - ph, 96, ph), Qt.AlignCenter, "9:41 AM")
    p.end()
    return img


def main() -> None:
    from PySide6.QtCore import QPoint, QUrl
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PySide6.QtQuickControls2 import QQuickStyle

    import httpx
    from hangar import snapshots, threads
    from hangar.apps import common, home
    common.make_app("spaces")
    pid = int((auth.STATE_DIR / "daemon.pid").read_text())
    admin = {"Authorization": f"Bearer {auth.admin_token()}"}
    try:
        print("setting up the demo…")
        setup_demo()
        eng.exec("bikes", ["bash", "-c", "cd ~/workspace && printf 'Look at: PedalSwap, Craigslist\\n' > todo.txt"])
        snapshots.take(eng, "bikes", "before: find a used road bike")

        # 1. Ask from anywhere: the Hangar OS desktop, then the Ask panel (Meta+Space).
        from hangar.apps.ask import AskPanel
        p = AskPanel()
        p.open(None, {}, set())
        pump(0.3)
        desk = os_desktop(1600, 1000)

        def over_screen() -> QImage:
            img = desk.copy()
            if typed["shown"]:
                panel = p.grab().toImage()
                q = QPainter(img)
                q.drawImage(QPoint((img.width() - panel.width()) // 2, 230), panel)
                q.end()
            return img
        typed = {"i": 0, "shown": False}

        def type_more(t: float) -> None:
            typed["shown"] = t > 1.3
            if t > 1.9:
                typed["i"] = min(len(ASK), typed["i"] + 4)
                p.input.setPlainText(ASK[:typed["i"]])
        record("ask", over_screen, 8.5, every=1 / 12, script=type_more)

        # Your desktop's Hangar: the conversation with the pilot.
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
        panel = None
        pump(5)

        def find_panel():
            from PySide6.QtCore import QObject
            for o in win.findChildren(QObject):
                mo = o.metaObject()
                if mo.indexOfProperty("control") >= 0 and mo.indexOfProperty("tab") >= 0:
                    return o

        # 2. The pilot plans it and puts a crew on it.
        chat = threads.create("Road bike under $500")
        threads.add(chat["id"], "user", ASK)
        threads.set_active(chat["id"])
        threads.link_space("bikes", chat["id"])
        bridge.refresh()
        bridge.openThread(chat["id"])
        pump(1.5)
        shown = {"n": 0}

        def plan(t: float) -> None:
            want = min(len(PLAN), int((t - 0.8) / 1.3) + 1) if t > 0.8 else 0
            while shown["n"] < want:
                kind, text, meta = PLAN[shown["n"]]
                threads.add(chat["id"], kind, text, **meta)
                shown["n"] += 1
                bridge.refresh()
                bridge.openThread(chat["id"])
        record("plan", win.grabWindow, 8, script=plan)

        # 3. Watch them work: each agent gets its own task and starts from a bare desktop.
        bridge.select("bikes")
        pump(4)
        started = {"lead": False, "wren": False}

        def go(t: float) -> None:
            if t > 2.6 and not started["lead"]:
                started["lead"] = True
                tile("bikes")
                background("bikes", "bikes", LEAD)
            if t > 3.6 and not started["wren"]:
                started["wren"] = True
                background("bikes", "wren", WREN)
        LEAD = [
            ("browser_navigate", {"url": f"{SITE}/market.html"}), ("wait", {"seconds": 1.5}),
            ("move_mouse", {"x": 300, "y": 420}), ("wait", {"seconds": 0.8}),
            ("scroll", {"direction": "down", "amount": 3, "x": 380, "y": 420}), ("wait", {"seconds": 1}),
            ("move_mouse", {"x": 150, "y": 330}), ("wait", {"seconds": 0.8}),
            ("browser_navigate", {"url": f"{SITE}/listing.html"}), ("wait", {"seconds": 1.2}),
            ("move_mouse", {"x": 260, "y": 640}), ("wait", {"seconds": 0.6}),
            ("scroll", {"direction": "down", "amount": 2, "x": 380, "y": 500})]
        WREN = [
            ("open_app", {"name": "browser", "args": f"--new-window {SITE}/reviews.html"}), ("wait", {"seconds": 1}),
            ("move_mouse", {"x": 980, "y": 300}), ("wait", {"seconds": 1.2}),
            ("scroll", {"direction": "down", "amount": 2, "x": 960, "y": 450}), ("wait", {"seconds": 1.5}),
            ("move_mouse", {"x": 900, "y": 470}), ("wait", {"seconds": 1.2}),
            ("scroll", {"direction": "down", "amount": 3, "x": 960, "y": 450}), ("wait", {"seconds": 1.2}),
            ("move_mouse", {"x": 1010, "y": 560})]
        record("space", win.grabWindow, 13, script=go)

        # 4. Other jobs keep going: everything at a glance.
        bridge.select("")
        bridge.overviewRequested.emit()
        pump(2.5)
        background("inbox", "inbox", [("scroll", {"direction": "down", "amount": 3, "x": 640, "y": 500})])
        background("coder", "coder", [("wait", {"seconds": 1}),
                                      ("scroll", {"direction": "down", "amount": 3, "x": 640, "y": 500})])
        background("trip", "trip", [("wait", {"seconds": 0.5}),
                                    ("scroll", {"direction": "down", "amount": 3, "x": 640, "y": 500})])
        record("overview", win.grabWindow, 5, every=1 / 6)

        # 5. A sign-in waits for your OK (the agent never sees the password).
        from PySide6.QtCore import QObject
        for o in win.findChildren(QObject):
            if o.metaObject().indexOfProperty("shown") >= 0:
                o.setProperty("shown", False)
        bridge.openThread(chat["id"])
        req = httpx.post(f"{DAEMON}/api/agents/bikes/vault/requests",
                         json={"kind": "login", "site": "pedalswap.com", "reason": "to message the Trek's seller",
                               "who": "bikes"},
                         headers={"Authorization": f"Bearer {auth.agent_token('bikes')}"}, timeout=10).json()
        pump(3)
        record("approve", win.grabWindow, 3.5, every=1 / 6)
        httpx.post(f"{DAEMON}/api/vault/requests/{req['id']}/deny", headers=admin, timeout=10)
        threads.add(chat["id"], "approval", "You let bikes sign in to pedalswap.com (Hangar filled it in)")
        bridge.refreshApprovals()

        # 6. Step in: take the wheel on the agent's desktop, mid-message.
        asyncio.run(drive("bikes", "bikes", [("browser_navigate", {"url": f"{SITE}/message.html"}), ("wait", {"seconds": 1}),
                                             ("click", {"x": 380, "y": 300}),
                                             ("type_text", {"text": "Hi! Is the Trek still available? Could I take "
                                                                    "it for a test ride on Saturday morning?"})]))
        bridge.select("bikes")
        pump(4)
        panel = find_panel()
        panel.setProperty("control", True)
        pump(1.5)
        still("takeover", win.grabWindow)
        panel.setProperty("control", False)

        # 7. Review every change the crew made.
        eng.exec("bikes", ["bash", "-c", "cd ~/workspace && cat > shortlist.md <<'EOF'\n"
                           "# Road bike shortlist\n\n1. Trek Domane AL 2, $450, Somerville (2021)\n"
                           "2. Specialized Allez, $420, Cambridge (2019)\n3. Cannondale CAAD8, $380, Brookline (2016)\n"
                           "\nMessaged the Trek's seller about Saturday.\nEOF\n"
                           "printf 'bike,price,size,year,where\\nTrek Domane AL 2,450,56,2021,Somerville\\n"
                           "Specialized Allez,420,56,2019,Cambridge\\nCannondale CAAD8,380,56,2016,Brookline\\n"
                           "Giant Contend 3,495,54,2022,Quincy\\n' > listings.csv && rm -f todo.txt"])
        panel.setProperty("tab", 1)
        bridge.loadReview("bikes")
        pump(4)
        still("review", win.grabWindow)
        panel.setProperty("tab", 0)

        # 8. One answer, with the work done.
        bridge.select("")
        pump(1)
        done = {"n": 0}

        def finish(t: float) -> None:
            want = min(len(RESULT), int(t / 1.4) + 1)
            while done["n"] < want:
                kind, text, meta = RESULT[done["n"]]
                threads.add(chat["id"], kind, text, **meta)
                if kind == "pilot":
                    threads.clear_active()
                done["n"] += 1
                bridge.refresh()
                bridge.openThread(chat["id"])
        record("answer", win.grabWindow, 7.5, script=finish)
        del engine
        pump(0.5)

        # 9. Findings land in your notes.
        from hangar.apps import notes as notes_app
        engine = QQmlApplicationEngine()
        nb = notes_app.Notes()
        engine.rootContext().setContextProperty("notes", nb)
        engine.addImportPath(str(home.QML))
        engine.load(QUrl.fromLocalFile(str(home.QML / "Notes.qml")))
        nwin = engine.rootObjects()[0]
        nwin.resize(1500, 860)
        pump(2)
        nb.open("Agents/bikes/Road bike shortlist.md")
        pump(1.5)
        nwin.setProperty("editing", False)
        pump(1)
        still("notes", nwin.grabWindow)
        del engine
    finally:
        for n in SPACES:
            eng.destroy(n)
        os.kill(pid, 15)


if __name__ == "__main__":
    main()
