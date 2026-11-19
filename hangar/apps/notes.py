"""Notes: your Markdown notes (~/Notes, Obsidian-compatible), with your crew's
notes alongside and the pilot one click away.

  hangar-app notes [--note PATH]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine

from hangar.apps import common

QML = Path(__file__).parent / "qml"
WIKI = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")


class Notes(QObject):
    notesChanged = Signal()
    noteChanged = Signal()
    toast = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._notes: list = []
        self._note: dict = {}
        self._query = ""
        self.pending: tuple[str, str] | None = None
        self.saver = QTimer(self, singleShot=True, interval=700)
        self.saver.timeout.connect(self._save_now)
        self.events = common.Events(self)
        self.events.event.connect(self._on_event)
        self.events.start()
        self.refresh()

    @Property("QVariant", notify=notesChanged)
    def notes(self):
        return self._notes

    @Property("QVariant", notify=noteChanged)
    def note(self):
        return self._note

    def _set_notes(self, n) -> None:
        self._notes = n
        self.notesChanged.emit()

    @Slot()
    def refresh(self) -> None:
        q = self._query
        common.bg(lambda: common.api.get("/api/notes" + (f"?q={quote(q)}" if q else "")), self._set_notes,
                  lambda e: self.toast.emit("Couldn't load notes", str(e)))

    @Slot(str)
    def search(self, q: str) -> None:
        self._query = q.strip()
        self.refresh()

    @Slot(str)
    def open(self, path: str) -> None:
        self._flush()

        def got(n) -> None:
            self._note = n
            self.noteChanged.emit()
        common.bg(lambda: common.api.get(f"/api/notes/note?path={quote(path)}"), got,
                  lambda e: self.toast.emit("Couldn't open that note", str(e)))

    @Slot(str)
    def follow(self, title: str) -> None:
        """A [[link]] was clicked: open it, or start that note if it doesn't exist yet."""
        def got(r) -> None:
            if r["path"]:
                self.open(r["path"])
            else:
                self.create(title)
        common.bg(lambda: common.api.get(f"/api/notes/resolve?title={quote(title)}"), got)

    @Slot(str)
    def create(self, title: str = "") -> None:
        self._flush()
        title = (title or "Untitled").strip().replace("/", "-")
        names = {n["path"] for n in self._notes}
        path, i = f"{title}.md", 2
        while path in names:
            path, i = f"{title} {i}.md", i + 1
        common.bg(lambda: common.api.put("/api/notes/note", {"path": path, "text": f"# {title}\n\n"}),
                  lambda n: (self.open(n["path"]), self.refresh()))

    @Slot(str, str)
    def edit(self, path: str, text: str) -> None:
        if self._note.get("path") != path or text == self._note.get("text"):
            return
        self._note["text"] = text
        self.pending = (path, text)
        self.saver.start()

    def _flush(self) -> None:
        if self.saver.isActive():
            self.saver.stop()
            self._save_now()

    def _save_now(self) -> None:
        if not self.pending:
            return
        path, text = self.pending
        self.pending = None
        common.bg(lambda: common.api.put("/api/notes/note", {"path": path, "text": text}), lambda _: None,
                  lambda e: self.toast.emit("Couldn't save", str(e)))

    @Slot(str, str)
    def rename(self, path: str, to: str) -> None:
        self._flush()
        to = to.strip()
        if not to or to + ".md" == path:
            return
        common.bg(lambda: common.api.post("/api/notes/rename", {"path": path, "to": str(Path(path).parent / to)
                                                                 if "/" in path else to}),
                  lambda n: (self.open(n["path"]), self.refresh()),
                  lambda e: self.toast.emit("Couldn't rename", str(e)))

    @Slot(str)
    def remove(self, path: str) -> None:
        self.saver.stop()
        self.pending = None
        common.bg(lambda: common.api.delete(f"/api/notes/note?path={quote(path)}"),
                  lambda _: (self._clear(), self.refresh(), self.toast.emit("Moved to the trash", "Notes/.trash")))

    def _clear(self) -> None:
        self._note = {}
        self.noteChanged.emit()

    @Slot(str, result=str)
    def preview(self, text: str) -> str:
        """Markdown for display: [[links]] become clickable."""
        body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
        return common.markdown_html(WIKI.sub(lambda m: f"[{m.group(2) or m.group(1)}](note:{quote(m.group(1))})", body))

    @Slot(str)
    def askPilot(self, path: str) -> None:
        """Hand this note to the pilot in a new chat."""
        self._flush()
        n = self._note
        q = (f"About my note \"{n.get('title', path)}\" (Notes/{path}):\n\n{n.get('text', '')[:6000]}\n\n"
             "Read it and ask me what I'd like done with it.")
        common.bg(lambda: common.api.post("/api/ask", {"question": q, "new": True}),
                  lambda r: _open_home(r.get("thread", "")),
                  lambda e: self.toast.emit("The pilot couldn't take that", str(e)))

    def _on_event(self, ev: dict) -> None:
        if ev.get("type") != "notes":
            return
        self.refresh()
        if ev.get("path") == self._note.get("path") and ev.get("who") != "you" and not self.pending:
            self.open(ev["path"])                    # an agent changed the note you're reading
            self.toast.emit(f"{ev['who']} updated this note", "")


def _open_home(thread: str) -> None:
    import subprocess
    subprocess.Popen([sys.executable, "-m", "hangar.apps.home", *(["--thread", thread] if thread else [])],
                     start_new_session=True)


def main() -> None:
    app = common.make_app("notes")
    from PySide6.QtQuickControls2 import QQuickStyle
    QQuickStyle.setStyle("Basic")          # Hangar draws its own controls
    engine = QQmlApplicationEngine()
    bridge = Notes()
    engine.rootContext().setContextProperty("notes", bridge)
    engine.addImportPath(str(QML))
    engine.load(QUrl.fromLocalFile(str(QML / "Notes.qml")))
    if not engine.rootObjects():
        sys.exit("couldn't load Notes")
    if "--note" in sys.argv:
        bridge.open(sys.argv[sys.argv.index("--note") + 1])
    code = app.exec()
    bridge._flush()
    del engine
    sys.exit(code)


if __name__ == "__main__":
    main()
