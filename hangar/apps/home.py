"""Hangar: the home app. Chat with your pilot; your spaces and apps alongside.

QML (hangar/apps/qml/) draws everything; this module is its bridge to the
daemon: conversations, spaces, approvals and review, kept live by the daemon's
event stream, plus a native live-desktop item and a thumbnail provider.

  hangar-app home [--thread ID | --space NAME | --overview]
"""

from __future__ import annotations

import base64
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPolygonF
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuick import QQuickImageProvider, QQuickPaintedItem

from hangar.apps import common
from hangar.apps.vnc import KEYSYMS, RFB

QML = Path(__file__).parent / "qml"
SOCKET = "hangar-home"


class Thumbs(QQuickImageProvider):
    """image://thumbs/<space>?n: the latest live thumbnail of each space."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image)
        self.cache: dict[str, QImage] = {}
        self.blank = QImage(16, 10, QImage.Format_RGB32)
        self.blank.fill(QColor("#07080b"))

    def requestImage(self, ident: str, size, requested: QSize) -> QImage:
        img = self.cache.get(ident.split("?")[0], self.blank)
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img


class Picture(QQuickPaintedItem):
    """A rounded, aspect-filled image that draws on every scene-graph backend
    (no shaders, so it works without a GPU too). source is a file path or
    "thumb:<space>" for a space's live thumbnail."""

    sourceChanged = Signal()
    radiusChanged = Signal()
    thumbs: "Thumbs | None" = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source, self._radius, self.img = "", 12.0, None
        self.setAntialiasing(True)

    @Property(str, notify=sourceChanged)
    def source(self) -> str:
        return self._source

    @source.setter
    def source(self, v: str) -> None:
        self._source = v
        self.reload()
        self.sourceChanged.emit()

    @Property(float, notify=radiusChanged)
    def radius(self) -> float:
        return self._radius

    @radius.setter
    def radius(self, v: float) -> None:
        self._radius = v
        self.radiusChanged.emit()
        self.update()

    @Slot()
    def reload(self) -> None:
        v = self._source.split("?")[0]
        if v.startswith("thumb:"):
            self.img = Picture.thumbs.cache.get(v[6:]) if Picture.thumbs else None
        elif v:
            img = QImage(v.removeprefix("file://"))
            self.img = None if img.isNull() else img
        else:
            self.img = None
        self.update()

    def paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(0, 0, self.width(), self.height())
        clip = QPainterPath()
        clip.addRoundedRect(r, self._radius, self._radius)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#07080b"))
        if self.img:
            iw, ih = self.img.width(), self.img.height()
            s = max(r.width() / iw, r.height() / ih)          # fill, crop the overflow
            w, h = iw * s, ih * s
            p.drawImage(QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h), self.img)


class LiveDesktop(QQuickPaintedItem):
    """A space's live desktop (native VNC through the daemon), with every agent's
    cursor in its colour. Sends your mouse and keyboard while you have control."""

    spaceChanged = Signal()
    controlChanged = Signal()
    statusChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._space, self._control, self._status = "", False, "Connecting…"
        self.img: QImage | None = None
        self.cursors: dict = {}
        self.rfb: RFB | None = None
        self.buttons = 0
        self.setAcceptedMouseButtons(Qt.AllButtons)
        self.setAcceptHoverEvents(True)
        self.setFlag(QQuickPaintedItem.ItemAcceptsInputMethod, True)
        self.setAntialiasing(True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_cursors)
        self.timer.start(350)
        self._busy = False

    @Property(str, notify=spaceChanged)
    def space(self) -> str:
        return self._space

    @space.setter
    def space(self, v: str) -> None:
        if v == self._space:
            return
        self._space = v
        if self.rfb:
            self.rfb.stop()
            self.rfb = None
        self.img, self.cursors = None, {}
        if v:
            self.rfb = RFB(v)
            self.rfb.frame.connect(self._frame)
            self.rfb.status.connect(self._set_status)
            self.rfb.start()
        self.spaceChanged.emit()
        self.update()

    @Property(bool, notify=controlChanged)
    def control(self) -> bool:
        return self._control

    @control.setter
    def control(self, v: bool) -> None:
        self._control = v
        if v:
            self.forceActiveFocus()
        self.controlChanged.emit()
        self.update()

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    def _set_status(self, s: str) -> None:
        self._status = s
        self.statusChanged.emit()
        self.update()

    def _frame(self, img: QImage) -> None:
        self.img = img
        if self._status:
            self._set_status("")
        self.update()

    def _poll_cursors(self) -> None:
        if not self._space or self._busy or not self.isVisible():
            return
        self._busy = True
        space = self._space

        def got(res) -> None:
            self._busy = False
            ptrs, colors = res
            if space == self._space:
                self.cursors = {n: (xy[0], xy[1], colors.get(n, "#7c9cff")) for n, xy in ptrs.items()}
                self.update()

        def fail(_e) -> None:
            self._busy = False

        def fetch():
            ptrs = common.api.get(f"/api/agents/{space}/pointers")
            team = common.api.get(f"/api/agents/{space}")
            return ptrs, {m["name"]: m["color"] for m in team.get("agents", [])}
        common.bg(fetch, got, fail)

    def target(self) -> QRectF:
        w, h = self.width(), self.height()
        if not self.img:
            return QRectF(0, 0, w, h)
        s = min(w / self.img.width(), h / self.img.height())
        return QRectF((w - self.img.width() * s) / 2, (h - self.img.height() * s) / 2,
                      self.img.width() * s, self.img.height() * s)

    def paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self.target()
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#07080b"))
        if self.img:
            p.drawImage(r, self.img)
            sx, sy = r.width() / self.img.width(), r.height() / self.img.height()
            f = QFont()
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            for name, (x, y, color) in self.cursors.items():
                cx, cy = r.x() + x * sx, r.y() + y * sy
                from PySide6.QtCore import QPointF
                arrow = QPolygonF([QPointF(cx, cy), QPointF(cx, cy + 17), QPointF(cx + 4.5, cy + 13),
                                   QPointF(cx + 8, cy + 20), QPointF(cx + 10.5, cy + 19), QPointF(cx + 7, cy + 12),
                                   QPointF(cx + 12.5, cy + 12)])
                p.setPen(QColor("#0b0d12"))
                p.setBrush(QColor(color))
                p.drawPolygon(arrow)
                tw = p.fontMetrics().horizontalAdvance(name) + 12
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(cx + 12, cy + 18, tw, 17), 8.5, 8.5)
                p.setPen(QColor("#0b0d12"))
                p.drawText(QRectF(cx + 12, cy + 18, tw, 17), Qt.AlignCenter, name)
        if self._status:
            p.setPen(QColor(common.C["dim"]))
            p.drawText(r, Qt.AlignCenter, self._status)

    # -- your input, while in control ------------------------------------------------------
    def _pos(self, e):
        r = self.target()
        x = (e.position().x() - r.x()) * self.img.width() / max(r.width(), 1)
        y = (e.position().y() - r.y()) * self.img.height() / max(r.height(), 1)
        return max(0, min(int(x), self.img.width() - 1)), max(0, min(int(y), self.img.height() - 1))

    def _pointer(self, e, mask=None) -> None:
        if self._control and self.img and self.rfb:
            x, y = self._pos(e)
            self.rfb.send(struct.pack(">BBHH", 5, self.buttons if mask is None else mask, x, y))

    @staticmethod
    def _mask(b) -> int:
        return {Qt.LeftButton: 1, Qt.MiddleButton: 2, Qt.RightButton: 4}.get(b, 0)

    def mousePressEvent(self, e) -> None:
        self.forceActiveFocus()
        self.buttons |= self._mask(e.button())
        self._pointer(e)

    def mouseReleaseEvent(self, e) -> None:
        self.buttons &= ~self._mask(e.button())
        self._pointer(e)

    def mouseMoveEvent(self, e) -> None:
        self._pointer(e)

    def hoverMoveEvent(self, e) -> None:
        self._pointer(e)

    def wheelEvent(self, e) -> None:
        bit = 8 if e.angleDelta().y() > 0 else 16
        self._pointer(e, self.buttons | bit)
        self._pointer(e, self.buttons)

    def _key(self, e, down: bool) -> None:
        if not (self._control and self.rfb):
            return
        k = e.key()
        sym = KEYSYMS.get(k)
        if sym is None:
            if Qt.Key_A <= k <= Qt.Key_Z and e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
                sym = ord(chr(k).lower())
            elif e.text() and e.text().isprintable():
                c = e.text()[0]
                sym = ord(c) if ord(c) < 256 else 0x01000000 | ord(c)
        if sym is not None:
            self.rfb.send(struct.pack(">BBxxI", 4, 1 if down else 0, sym))

    def keyPressEvent(self, e) -> None:
        self._key(e, True)

    def keyReleaseEvent(self, e) -> None:
        self._key(e, False)

    def releaseResources(self) -> None:
        if self.rfb:
            self.rfb.stop()


class Bridge(QObject):
    threadsChanged = Signal()
    threadChanged = Signal()
    spacesChanged = Signal()
    approvalsChanged = Signal()
    reviewChanged = Signal()
    selectedChanged = Signal()
    thumbsTick = Signal()
    toast = Signal(str, str)
    pilotChanged = Signal()
    overviewRequested = Signal()

    def __init__(self, thumbs: Thumbs) -> None:
        super().__init__()
        self._threads: list = []
        self._thread: dict = {}
        self._spaces: list = []
        self._approvals: list = []
        self._review: dict = {}
        self._selected = ""
        self._pilot = ""
        self.thumbs = thumbs
        self._thumb_busy: set[str] = set()
        self.events = common.Events(self)
        self.events.event.connect(self._on_event)
        self.events.start()
        self.debounce = QTimer(self, singleShot=True, interval=350)
        self.debounce.timeout.connect(self.refreshSpaces)
        t = QTimer(self)
        t.timeout.connect(self._tick_thumbs)
        t.start(1500)
        slow = QTimer(self)
        slow.timeout.connect(self.refreshSpaces)
        slow.start(8000)
        self.refresh()

    # -- properties QML reads -------------------------------------------------------------
    @Property("QVariant", notify=threadsChanged)
    def threads(self):
        return self._threads

    @Property("QVariant", notify=threadChanged)
    def thread(self):
        return self._thread

    @Property("QVariant", notify=spacesChanged)
    def spaces(self):
        return self._spaces

    @Property("QVariant", notify=approvalsChanged)
    def approvals(self):
        return self._approvals

    @Property("QVariant", notify=reviewChanged)
    def review(self):
        return self._review

    @Property(str, notify=selectedChanged)
    def selected(self):
        return self._selected

    @Property(str, notify=pilotChanged)
    def pilot(self):
        return self._pilot

    @Property(bool, constant=True)
    def linux(self):
        return sys.platform == "linux"

    # -- loading ------------------------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        common.bg(lambda: common.api.get("/api/threads"), self._set_threads, lambda e: None)
        self.refreshSpaces()
        self.refreshApprovals()
        common.bg(lambda: common.api.get("/api/orchestration"),
                  lambda o: self._set_pilot(o["pilot"]["model"]), lambda e: None)

    def _set_pilot(self, m: str) -> None:
        self._pilot = m
        self.pilotChanged.emit()

    def _set_threads(self, ts) -> None:
        self._threads = ts
        self.threadsChanged.emit()

    @Slot()
    def refreshSpaces(self) -> None:
        def got(agents) -> None:
            self._spaces = sorted(agents, key=lambda a: (a["name"] != "pilot", a["name"]))
            self.spacesChanged.emit()
        common.bg(lambda: common.api.get("/api/agents"), got, lambda e: None)

    @Slot()
    def refreshApprovals(self) -> None:
        def got(v) -> None:
            self._approvals = [r for r in v["requests"] if r["status"] == "pending"]
            self.approvalsChanged.emit()
        common.bg(lambda: common.api.get("/api/vault"), got, lambda e: None)

    def _tick_thumbs(self) -> None:
        for s in self._spaces:
            n = s["name"]
            if s["status"] != "running" or n in self._thumb_busy:
                continue
            self._thumb_busy.add(n)

            def got(data, n=n) -> None:
                self._thumb_busy.discard(n)
                img = QImage()
                if img.loadFromData(data):
                    self.thumbs.cache[n] = img
                    self.thumbsTick.emit()

            common.bg(lambda n=n: common.api.call("GET", f"/api/agents/{n}/screenshot.png?w=640", raw=True), got,
                      lambda e, n=n: self._thumb_busy.discard(n))

    def _on_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "thread":
            if self._thread.get("id") == ev.get("thread"):
                self.openThread(ev["thread"])
            common.bg(lambda: common.api.get("/api/threads"), self._set_threads, lambda e: None)
        elif kind == "vault":
            self.refreshApprovals()
        elif kind in ("report", "agent"):
            self.debounce.start()

    # -- chat ----------------------------------------------------------------------------------
    @Slot(str)
    def openThread(self, tid: str) -> None:
        def got(t) -> None:
            self._thread = t
            self.threadChanged.emit()
        common.bg(lambda: common.api.get(f"/api/threads/{tid}"), got,
                  lambda e: self.toast.emit("Couldn't open that chat", str(e)))

    @Slot()
    def newChat(self) -> None:
        self._thread = {}
        self.threadChanged.emit()

    @Slot(str)
    def deleteThread(self, tid: str) -> None:
        common.bg(lambda: common.api.delete(f"/api/threads/{tid}"), lambda _: (self.newChat(), self.refresh()))

    @Slot(str, str)
    def send(self, text: str, screenshot: str) -> None:
        text = text.strip()
        if not text:
            return
        body = {"question": text, "thread": self._thread.get("id", "")}
        if screenshot:
            body["screenshot"] = base64.b64encode(Path(screenshot).read_bytes()).decode()

        def ok(r) -> None:
            self.openThread(r["thread"])
            common.bg(lambda: common.api.get("/api/threads"), self._set_threads, lambda e: None)

        common.bg(lambda: common.api.post("/api/ask", body), ok,
                  lambda e: self.toast.emit("The pilot couldn't take that", str(e)))

    @Slot(str, result=str)
    def md(self, text: str) -> str:
        return common.markdown_html(text)

    @Slot(result=str)
    def captureScreenshot(self) -> str:
        from hangar import capture
        shot = capture.screenshot("region")
        return str(shot) if shot else ""

    # -- spaces ----------------------------------------------------------------------------------
    @Slot(str)
    def select(self, name: str) -> None:
        self._selected = name
        self._review = {}
        self.selectedChanged.emit()
        self.reviewChanged.emit()

    @Slot(str, str)
    def act(self, name: str, what: str) -> None:
        common.bg(lambda: common.api.post(f"/api/agents/{name}/{what}"), lambda _: self.refreshSpaces(),
                  lambda e: self.toast.emit("Couldn't do that", str(e)))

    @Slot(str, str, str)
    def runTask(self, name: str, agent: str, task: str) -> None:
        if task.strip():
            common.bg(lambda: common.api.post(f"/api/agents/{name}/run", {"task": task, "agent": agent}),
                      lambda r: (self.toast.emit("On it", r.get("warning") or "The workspace was saved first."),
                                 self.refreshSpaces()),
                      lambda e: self.toast.emit("Couldn't start that", str(e)))

    @Slot(str)
    def loadReview(self, name: str) -> None:
        self._review = {"loading": True}
        self.reviewChanged.emit()

        def got(r) -> None:
            self._review = r
            self.reviewChanged.emit()

        def bad(e) -> None:
            self._review = {"error": str(e)}
            self.reviewChanged.emit()
        common.bg(lambda: common.api.get(f"/api/agents/{name}/changes"), got, bad)

    @Slot(str, str)
    def revert(self, name: str, path: str) -> None:
        snap = (self._review.get("snapshot") or {}).get("id")
        common.bg(lambda: common.api.post(f"/api/agents/{name}/changes", {"path": path, "snapshot": snap}),
                  lambda r: (self.toast.emit(f"{'Restored' if r['did'] == 'restored' else 'Removed'} {path}", ""),
                             self.loadReview(name)),
                  lambda e: self.toast.emit("Couldn't revert", str(e)))

    @Slot(str)
    def openInEditor(self, name: str) -> None:
        """Open the agent's work in an editor: its writable shared folder if it has one
        (your real files), otherwise a copy of its workspace in ~/Hangar/<space>."""
        def go():
            a = common.api.get(f"/api/agents/{name}")
            rw = [s[:-3] for s in a.get("shares", []) if s.endswith(":rw")]
            if rw:
                path = rw[0]
            else:
                from hangar import snapshots
                from hangar.engine import Engine
                path = str(Path.home() / "Hangar" / name)
                snapshots.export(Engine(), name, path)
            open_editor(path)
            return path
        common.bg(go, lambda p: self.toast.emit("Opened in your editor", p),
                  lambda e: self.toast.emit("Couldn't open an editor", str(e)))

    # -- approvals -----------------------------------------------------------------------------------
    @Slot(str, str)
    def approve(self, rid: str, code: str) -> None:
        r = next((x for x in self._approvals if x["id"] == rid), None)
        if not r:
            return
        usable = [e for e in r.get("entries", []) if (e["has_password"] if r["kind"] == "login" else e["has_totp"])]
        body = {"entry": "" if code else (usable[0]["id"] if usable else ""), "scope": "hour", "code": code}
        common.bg(lambda: common.api.post(f"/api/vault/requests/{rid}/approve", body),
                  lambda res: (self.toast.emit("Signed in" if res["status"] == "filled" else "Couldn't fill it",
                                               res["message"]), self.refreshApprovals()),
                  lambda e: self.toast.emit("Couldn't approve", str(e)))

    @Slot(str)
    def deny(self, rid: str) -> None:
        common.bg(lambda: common.api.post(f"/api/vault/requests/{rid}/deny"), lambda _: self.refreshApprovals())

    @Slot(str)
    def popOut(self, name: str) -> None:
        """The space on its own window (on the Hangar OS: its own virtual desktop)."""
        subprocess.Popen([sys.executable, "-m", "hangar.apps.space", name], start_new_session=True)

    @Slot(str)
    def openHome(self, space: str) -> None:
        subprocess.Popen([sys.executable, "-m", "hangar.apps.home", *(["--space", space] if space else [])],
                         start_new_session=True)

    @Slot(str)
    def openApp(self, name: str) -> None:
        subprocess.Popen([sys.executable, "-m", f"hangar.apps.{name}"], start_new_session=True)


def open_editor(path: str) -> None:
    """Zed if it's here (the Hangar OS ships it), else whatever the system has."""
    for cmd in (["zed", path], ["flatpak", "run", "dev.zed.Zed", path]):
        if shutil.which(cmd[0]) and (cmd[0] != "flatpak" or
                                     subprocess.run(["flatpak", "info", "dev.zed.Zed"], capture_output=True).returncode == 0):
            subprocess.Popen(cmd, start_new_session=True)
            return
    if sys.platform == "darwin":
        app = "Zed" if Path("/Applications/Zed.app").exists() else "TextEdit"
        subprocess.Popen(["open", "-a", app, path])
    else:
        subprocess.Popen(["xdg-open", path], start_new_session=True)


def main() -> None:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    want = "show"
    if "--thread" in sys.argv:
        want = "thread:" + sys.argv[sys.argv.index("--thread") + 1]
    elif "--space" in sys.argv:
        want = "space:" + sys.argv[sys.argv.index("--space") + 1]
    elif "--overview" in sys.argv:
        want = "overview"
    app = common.make_app("spaces")
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if sock.waitForConnected(300):
        sock.write(want.encode())
        sock.flush()
        sock.waitForBytesWritten(1000)
        return
    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)
    qmlRegisterType(LiveDesktop, "Hangar", 1, 0, "LiveDesktop")
    qmlRegisterType(Picture, "Hangar", 1, 0, "Picture")
    from PySide6.QtQuickControls2 import QQuickStyle
    QQuickStyle.setStyle("Basic")          # Hangar draws its own controls
    engine = QQmlApplicationEngine()
    thumbs = Thumbs()
    Picture.thumbs = thumbs
    engine.addImageProvider("thumbs", thumbs)
    bridge = Bridge(thumbs)
    engine.rootContext().setContextProperty("hangar", bridge)
    engine.addImportPath(str(QML))
    engine.load(QUrl.fromLocalFile(str(QML / "Home.qml")))
    if not engine.rootObjects():
        sys.exit("couldn't load the Hangar interface")
    win = engine.rootObjects()[0]

    def handle(msg: str) -> None:
        win.show()
        win.raise_()
        win.requestActivate()
        if msg.startswith("thread:"):
            bridge.openThread(msg[7:])
        elif msg.startswith("space:"):
            bridge.select(msg[6:])
        elif msg == "overview":
            bridge.overviewRequested.emit()

    def incoming() -> None:
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(1000)
        handle(bytes(conn.readAll()).decode())
    server.newConnection.connect(incoming)
    QTimer.singleShot(200, lambda: handle(want) if want != "show" else None)
    code = app.exec()
    del engine          # before the bridge it reads from goes away
    sys.exit(code)


if __name__ == "__main__":
    main()
