"""The in-app browser: web pages that live inside Hangar, for agents and for you.

Agents browse here instead of on a desktop in a VM or container (see
docs/browser.md). It's Qt's own Chromium (QtWebEngine), in Hangar's process:

  - Each space gets its own profile (cookies, storage), apart from yours and
    from other spaces'. Your own tabs use the "you" profile.
  - A tab is a QWebEngineView laid out offscreen at 1440x900: fully rendered,
    never on your screen, never taking focus. The Browser view shows any tab
    live (a native grab of the view) and passes your input to it when you take
    over.
  - Agents drive tabs with Playwright over the Chrome DevTools Protocol:
    QtWebEngine listens on 127.0.0.1 (a random port), and the daemon relays an
    agent only to its own space's tabs (hangar/browser_relay.py).
  - A small control endpoint on 127.0.0.1 (with a secret key, in
    browser.json in Hangar's state folder) lets the daemon list and open tabs.

Everything here runs on the Qt main thread except the control endpoint, which
hands work over to it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPointF, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QWheelEvent

from hangar import auth

VIEWPORT = QSize(1440, 900)
from hangar.browser_relay import STATE, control  # noqa: E402,F401  (the daemon reads it there)
PROFILES = auth.STATE_DIR / "browser"
YOU = "you"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare() -> int:
    """Turn on the DevTools endpoint; must run before QtWebEngine starts
    (before the QApplication). Returns its port."""
    port = _free_port()
    os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
    # The agents' tabs get Chromium's view of a normal, visible window.
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-renderer-backgrounding"
                                                " --disable-background-timer-throttling"
                                                " --disable-backgrounding-occluded-windows").strip()
    return port


def available() -> bool:
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except ImportError:
        return False


class Tab:
    def __init__(self, tid: str, space: str, view) -> None:
        self.id, self.space, self.view = tid, space, view
        self.target = ""                 # its CDP target id
        self.created = time.time()
        self.driver = ""                 # who's driving: "" (nobody), "agent", "you"

    def info(self) -> dict:
        page = self.view.page()
        return {"id": self.id, "space": self.space, "target": self.target, "url": page.url().toString(),
                "title": page.title(), "loading": self.view.property("hangarLoading") or False,
                "created": self.created, "driver": self.driver}


class BrowserHost(QObject):
    tabsChanged = Signal()
    frame = Signal(str)                  # a tab's picture changed (tab id)
    _call = Signal(object)               # run a function on the Qt thread

    def __init__(self, cdp_port: int) -> None:
        super().__init__()
        from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: F401  (fail early without WebEngine)
        self.cdp_port = cdp_port
        self.tabs: dict[str, Tab] = {}
        self.profiles: dict = {}
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)   # always on the Qt thread
        self.key = secrets.token_hex(24)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        auth.write_private(STATE, json.dumps({"pid": os.getpid(), "cdp": cdp_port, "control": self.server.server_port,
                                              "key": self.key}))
        # Start Chromium (and its DevTools endpoint) now: one blank page of our own,
        # in no space, so no agent ever sees it.
        from PySide6.QtWebEngineCore import QWebEnginePage
        self._idle = QWebEnginePage(self.profile("hangar-internal"), self)
        self._idle.load(QUrl("about:blank"))
        self.ticker = QTimer(self, interval=100)          # live pictures for the Browser view (~10 fps)
        self.ticker.timeout.connect(self._tick)
        self.watching: set[str] = set()

    @Slot(object)
    def _run(self, fn) -> None:
        fn()

    def close(self) -> None:
        self.server.shutdown()
        try:
            if json.loads(STATE.read_text()).get("pid") == os.getpid():
                STATE.unlink()
        except (OSError, ValueError):
            pass

    # -- profiles and tabs (Qt thread) ----------------------------------------------------
    def profile(self, space: str):
        from PySide6.QtWebEngineCore import QWebEngineProfile
        if space not in self.profiles:
            p = QWebEngineProfile(f"hangar-{space}", self)
            d = PROFILES / space
            d.mkdir(parents=True, exist_ok=True)
            p.setPersistentStoragePath(str(d / "storage"))
            p.setCachePath(str(d / "cache"))
            p.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
            p.setDownloadPath(str(d / "downloads"))
            self.profiles[space] = p
        return self.profiles[space]

    def open(self, space: str, url: str = "") -> Tab:
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        tid = secrets.token_hex(4)
        view = QWebEngineView()
        page = QWebEnginePage(self.profile(space), view)
        view.setPage(page)
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)    # rendered, never on your screen
        view.resize(VIEWPORT)
        view.show()
        tab = Tab(tid, space, view)
        self.tabs[tid] = tab
        page.loadStarted.connect(lambda: (view.setProperty("hangarLoading", True), self.tabsChanged.emit()))
        page.loadFinished.connect(lambda _ok: (view.setProperty("hangarLoading", False), self.tabsChanged.emit()))
        page.titleChanged.connect(lambda _t: self.tabsChanged.emit())
        page.urlChanged.connect(lambda _u: self.tabsChanged.emit())
        page.windowCloseRequested.connect(lambda: self.close_tab(tid))
        page.newWindowRequested.connect(lambda req, space=space: self._popup(space, req))
        # A marker URL first, so the tab can be found among the DevTools targets.
        page.load(QUrl(f"about:blank#hangar-{tid}"))
        if url:
            QTimer.singleShot(150, lambda: page.load(QUrl.fromUserInput(url)))
        self.tabsChanged.emit()
        return tab

    def _popup(self, space: str, req) -> None:
        """A page opening a new window gets a new tab in the same space."""
        tab = self.open(space)
        req.openIn(tab.view.page())

    @Slot(str)
    def close_tab(self, tid: str) -> None:
        tab = self.tabs.pop(tid, None)
        if tab:
            self.watching.discard(tid)
            tab.view.close()
            tab.view.deleteLater()
            self.tabsChanged.emit()

    def close_space(self, space: str) -> None:
        for t in [t for t in self.tabs.values() if t.space == space]:
            self.close_tab(t.id)

    def list(self, space: str | None = None) -> list[dict]:
        return [t.info() for t in sorted(self.tabs.values(), key=lambda t: t.created)
                if space is None or t.space == space]

    # -- the picture and your input (for the Browser view) -------------------------------
    def watch(self, tid: str, on: bool) -> None:
        (self.watching.add if on else self.watching.discard)(tid)
        if self.watching and not self.ticker.isActive():
            self.ticker.start()
        elif not self.watching:
            self.ticker.stop()

    def _tick(self) -> None:
        for tid in list(self.watching):
            self.frame.emit(tid)

    def grab(self, tid: str) -> QImage:
        tab = self.tabs.get(tid)
        return tab.view.grab().toImage() if tab else QImage()

    def _target(self, tid: str):
        tab = self.tabs.get(tid)
        return tab.view.focusProxy() or tab.view if tab else None

    def mouse(self, tid: str, kind: str, x: float, y: float, button: int = 1, buttons: int = 0) -> None:
        w = self._target(tid)
        if not w:
            return
        from PySide6.QtWidgets import QApplication
        types = {"press": QMouseEvent.Type.MouseButtonPress, "release": QMouseEvent.Type.MouseButtonRelease,
                 "move": QMouseEvent.Type.MouseMove, "double": QMouseEvent.Type.MouseButtonDblClick}
        b = Qt.MouseButton(button) if kind != "move" else Qt.MouseButton.NoButton
        pos = QPointF(x, y)
        ev = QMouseEvent(types[kind], pos, w.mapToGlobal(pos.toPoint()).toPointF(), b,
                         Qt.MouseButtons(buttons), Qt.KeyboardModifier.NoModifier)
        w.setFocus()
        QApplication.sendEvent(w, ev)

    def wheel(self, tid: str, x: float, y: float, dx: float, dy: float) -> None:
        w = self._target(tid)
        if not w:
            return
        from PySide6.QtWidgets import QApplication
        pos = QPointF(x, y)
        ev = QWheelEvent(pos, w.mapToGlobal(pos.toPoint()).toPointF(), QPoint(0, 0), QPoint(int(dx), int(dy)),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(w, ev)

    def key(self, tid: str, press: bool, key: int, text: str, modifiers: int) -> None:
        w = self._target(tid)
        if not w:
            return
        from PySide6.QtWidgets import QApplication
        ev = QKeyEvent(QKeyEvent.Type.KeyPress if press else QKeyEvent.Type.KeyRelease, key,
                       Qt.KeyboardModifier(modifiers), text)
        QApplication.sendEvent(w, ev)

    def navigate(self, tid: str, what: str) -> None:
        tab = self.tabs.get(tid)
        if not tab:
            return
        v = tab.view
        {"back": v.back, "forward": v.forward, "reload": v.reload, "stop": v.stop}.get(what, lambda: None)()

    def load(self, tid: str, url: str) -> None:
        tab = self.tabs.get(tid)
        if tab:
            tab.view.page().load(QUrl.fromUserInput(url))

    # -- the control endpoint (worker threads) --------------------------------------------
    def on_qt(self, fn, timeout: float = 20):
        """Run fn on the Qt thread and wait for its result."""
        box: dict = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["v"] = fn()
            except Exception as e:  # noqa: BLE001 - reported to the caller
                box["e"] = e
            done.set()
        self._call.emit(run)
        if not done.wait(timeout):
            raise TimeoutError("Hangar's browser is busy")
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def find_target(self, tid: str, timeout: float = 8) -> str:
        """The DevTools target id of a tab (found by its marker URL)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.cdp_port}/json/list", timeout=3) as r:
                    for t in json.load(r):
                        if t.get("type") == "page" and t.get("url", "").endswith(f"#hangar-{tid}"):
                            return t["id"]
            except OSError:
                pass
            time.sleep(0.1)
        raise TimeoutError("the new tab didn't show up")

    def open_for(self, space: str, url: str) -> dict:
        tab = self.on_qt(lambda: self.open(space))          # the marker page first...
        tab.target = self.find_target(tab.id)
        if url:                                             # ...then where it should go
            self.on_qt(lambda: self.load(tab.id, url))
        return self.on_qt(lambda: (self.tabsChanged.emit(), tab.info())[1])

    def _handler(self):
        host = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _ok(self) -> bool:
                import hmac
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + host.key):
                    self._send(401, {"error": "bad key"})
                    return False
                return True

            def _space(self) -> str:
                from urllib.parse import parse_qs, urlsplit
                return (parse_qs(urlsplit(self.path).query).get("space") or [""])[0]

            def do_GET(self):
                if not self._ok():
                    return
                if self.path.startswith("/tabs"):
                    space = self._space() or None
                    self._send(200, host.on_qt(lambda: host.list(space)))
                else:
                    self._send(404, {"error": "no such route"})

            def do_POST(self):
                if not self._ok():
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path.startswith("/tabs"):
                    space = str(body.get("space", ""))
                    if not NAME_RE.match(space):
                        return self._send(400, {"error": "bad space"})
                    try:
                        self._send(200, host.open_for(space, str(body.get("url", ""))))
                    except Exception as e:  # noqa: BLE001
                        self._send(500, {"error": str(e)})
                elif self.path.startswith("/close"):
                    tid, space = str(body.get("id", "")), str(body.get("space", ""))
                    tab = host.tabs.get(tid)
                    if not tab or (space and tab.space != space):
                        return self._send(404, {"error": "no such tab"})
                    host.on_qt(lambda: host.close_tab(tid))
                    self._send(200, {"ok": True})
                elif self.path.startswith("/driver"):
                    tab = host.tabs.get(str(body.get("id", "")))
                    if tab:
                        tab.driver = str(body.get("driver", ""))
                        host.on_qt(lambda: host.tabsChanged.emit())
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "no such route"})
        return H


def profiles_dir(space: str) -> Path:
    return PROFILES / space
