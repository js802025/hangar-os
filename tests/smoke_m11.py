"""M11 smoke test: the chat-first Hangar home, review, notes.

  1. Notes: Markdown vault with [[links]], backlinks, rename that rewrites
     links, search, trash; agents only reach their own folder (or the whole
     vault read-only when you allow it) and can't escape it.
  2. Review: a space's changes since its last snapshot, as per-file diffs,
     and reverting one file (a fake container, so no Docker needed here).
  3. Home (QML, offscreen) against a real daemon: chats in the sidebar, a
     conversation opens, and a space's report lands in it live.
  4. Notes app (QML) lists the vault, opens a note, renders [[links]].

Usage:  uv run --extra apps python tests/smoke_m11.py
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-m11-"))
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_NOTES_DIR=str(TMP / "Notes"), XDG_DATA_HOME=str(TMP / "data"),
                  HANGAR_DAEMON_PORT="7803", QT_QPA_PLATFORM="offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

from hangar import auth, notes, snapshots, threads  # noqa: E402


def wait(seconds: float, cond, app=None) -> None:
    from PySide6.QtCore import QCoreApplication, QEventLoop
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 50)
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_notes() -> None:
    notes.write("Trip", "Plan in [[Itinerary]] and [[Agents/lisbon/Hotels|hotels]]. #travel\n")
    notes.write("Itinerary", "Day 1: Alfama\n")
    notes.write("Agents/lisbon/Hotels", "Memmo Príncipe Real\n")
    assert [b["path"] for b in notes.read("Itinerary")["backlinks"]] == ["Trip.md"]
    assert notes.read("Agents/lisbon/Hotels")["backlinks"][0]["path"] == "Trip.md"
    assert notes.read("Trip")["tags"] == ["travel"]
    notes.rename("Itinerary", "Plan")
    assert "[[Plan]]" in notes.read("Trip")["text"]
    assert [h["path"] for h in notes.search("memmo")] == ["Agents/lisbon/Hotels.md"]
    for bad in ("../x", "/etc/passwd", "a/../../x", ".hidden/x"):
        try:
            notes.read(bad)
        except notes.NotesError:
            continue
        raise AssertionError(f"{bad} wasn't refused")

    from starlette.testclient import TestClient
    from hangar import daemon
    c = TestClient(daemon.app)
    A = {"Authorization": f"Bearer {auth.admin_token()}"}
    G = {"Authorization": f"Bearer {auth.agent_token('lisbon')}"}
    assert c.get("/api/notes").status_code == 401
    assert c.get("/api/agents/lisbon/notes", headers={"Authorization": "Bearer x"}).status_code == 401
    r = c.post("/api/agents/lisbon/notes/write", json={"path": "Shortlist", "text": "1. Memmo", "who": "wren"}, headers=G)
    assert r.json()["path"] == "Agents/lisbon/Shortlist.md", r.text
    assert c.post("/api/agents/lisbon/notes/write", json={"path": "Agents/site/x", "text": "x"}, headers=G).status_code == 403
    assert c.post("/api/agents/lisbon/notes/write", json={"path": "../../Trip", "text": "x"}, headers=G).status_code == 400
    assert c.get("/api/agents/lisbon/notes/read?path=Trip", headers=G).status_code == 403      # default: own folder
    seen = [n["path"] for n in c.get("/api/agents/lisbon/notes", headers=G).json()["notes"]]
    assert set(seen) == {"Agents/lisbon/Hotels.md", "Agents/lisbon/Shortlist.md"}, seen
    c.put("/api/notes/access", json={"agents": "read"}, headers=A)
    assert "Plan" in c.get("/api/agents/lisbon/notes/read?path=Trip", headers=G).json()["text"]
    assert c.post("/api/agents/lisbon/notes/write", json={"path": "Agents/site/x", "text": "x"}, headers=G).status_code == 403
    c.put("/api/notes/access", json={"agents": "off"}, headers=A)
    assert c.get("/api/agents/lisbon/notes", headers=G).status_code == 403
    c.put("/api/notes/access", json={"agents": "own"}, headers=A)
    notes.delete("Plan")
    assert (notes.root() / ".trash").exists() and not (notes.root() / "Plan.md").exists()
    print("1. notes: links, backlinks, rename, search, trash; agents limited to their folder: ok")


class FakeContainer:
    """Just enough of a container for snapshots.changes / revert_file."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def get_archive(self, _path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for p, data in self.files.items():
                ti = tarfile.TarInfo(f"workspace/{p}")
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
        return iter([buf.getvalue()]), {}

    def put_archive(self, _where, data) -> bool:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for m in tar.getmembers():
                self.files[m.name.split("/", 1)[1]] = tar.extractfile(m).read()
        return True

    def exec_run(self, cmd, user=None):
        if cmd[:2] == ["rm", "-f"]:
            self.files.pop(cmd[2].split("/workspace/", 1)[1], None)
        return 0, b""


def test_review() -> None:
    before = {"index.html": b"<h1>Hello</h1>\n<p>old</p>\n", "notes.md": b"keep\n", "old.txt": b"bye\n"}
    c = FakeContainer(dict(before))
    eng = type("E", (), {"client": type("C", (), {"containers": type("Cs", (), {"get": lambda self, n: c})()})()})()
    sid = "20260101-000000-abcd"
    d = auth.private_dir(snapshots.ROOT / "site")
    with gzip.open(d / f"{sid}.tar.gz", "wb") as f:
        f.write(b"".join(c.get_archive("")[0]))
    (d / f"{sid}.json").write_text(json.dumps({"id": sid, "agent": "site", "label": "before: landing page",
                                               "time": time.time()}))
    c.files.update({"index.html": b"<h1>Fly your agents.</h1>\n<p>old</p>\n", "style.css": b"body{}\n"})
    del c.files["old.txt"]
    ch = snapshots.changes(eng, "site")
    got = {f["path"]: (f["status"], f["added"], f["removed"]) for f in ch["files"]}
    assert got == {"index.html": ("modified", 1, 1), "old.txt": ("deleted", 0, 1), "style.css": ("added", 1, 0)}, got
    assert any(x["kind"] == "add" and "Fly your agents" in x["text"] for x in ch["files"][0]["diff"])
    assert snapshots.revert_file(eng, "site", "index.html") == "restored" and c.files["index.html"] == before["index.html"]
    assert snapshots.revert_file(eng, "site", "style.css") == "removed" and "style.css" not in c.files
    try:
        snapshots.revert_file(eng, "site", "../etc/passwd")
        raise AssertionError("path escape not refused")
    except snapshots.SnapshotError:
        pass
    print("2. review: added/modified/deleted with diffs; per-file revert: ok")


def test_apps() -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PySide6.QtQuickControls2 import QQuickStyle

    import httpx
    from hangar.apps import common, home
    from hangar.apps import notes as notes_app
    app = common.make_app("spaces")
    pid = int((auth.STATE_DIR / "daemon.pid").read_text())
    try:
        t = threads.create()
        threads.add(t["id"], "user", "Plan a weekend in Lisbon")
        threads.add(t["id"], "step", "Started a research space: lisbon", space="lisbon")
        threads.add(t["id"], "pilot", "Here's the plan so far:\n\n- **Stay** in Príncipe Real\n- [[x]] see [docs](https://example.com)")
        threads.link_space("lisbon", t["id"])

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
        warnings = []
        engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
        engine.load(QUrl.fromLocalFile(str(home.QML / "Home.qml")))
        assert engine.rootObjects(), warnings
        win = engine.rootObjects()[0]
        wait(10, lambda: any(x["id"] == t["id"] for x in bridge.threads))
        bridge.openThread(t["id"])
        wait(10, lambda: len(bridge.thread.get("items", [])) == 3)

        # a space the pilot started reports back: it shows up in the conversation, live
        r = httpx.post("http://127.0.0.1:7803/api/agents/lisbon/reports",
                       json={"status": "done", "final": True, "message": "Itinerary drafted.", "who": "juno"},
                       headers={"Authorization": f"Bearer {auth.agent_token('lisbon')}"})
        assert r.status_code == 200, r.text
        wait(10, lambda: len(bridge.thread.get("items", [])) == 4)
        assert bridge.thread["items"][-1]["kind"] == "report" and bridge.thread["items"][-1]["who"] == "juno"
        assert "#7c9cff" in bridge.md("[a](https://x.y)")
        win.grabWindow().save(str(OUT / "m11-home.png"))
        bad = [w for w in warnings if "TypeError" in w or "ReferenceError" in w or "is not a type" in w]
        assert not bad, bad
        print("3. home: chats listed, conversation opened, a space's report arrived live: ok (out/m11-home.png)")
        del engine

        engine = QQmlApplicationEngine()
        nb = notes_app.Notes()
        engine.rootContext().setContextProperty("notes", nb)
        engine.addImportPath(str(home.QML))
        warnings.clear()
        engine.load(QUrl.fromLocalFile(str(home.QML / "Notes.qml")))
        assert engine.rootObjects(), warnings
        wait(10, lambda: len(nb.notes) >= 3)
        nb.open("Trip.md")
        wait(10, lambda: nb.note.get("path") == "Trip.md")
        html = nb.preview(nb.note["text"])
        assert 'href="note:Plan"' in html and "#7c9cff" in html, html[:400]
        engine.rootObjects()[0].grabWindow().save(str(OUT / "m11-notes.png"))
        bad = [w for w in warnings if "TypeError" in w or "ReferenceError" in w]
        assert not bad, bad
        print("4. notes app: vault listed, note opened, [[links]] clickable: ok (out/m11-notes.png)")
        del engine
    finally:
        os.kill(pid, 15)


def main() -> None:
    test_notes()
    test_review()
    test_apps()
    print("PASS")


if __name__ == "__main__":
    main()
