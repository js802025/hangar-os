"""Shared pieces for Hangar's native apps (Qt / PySide6).

Every app is a thin client of the daemon's API, same as the web pages: this
module has the API client, background calls, the live event stream, the dark
theme, and a few widgets (state badges, site icons, toasts, the approvals bar).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable

import httpx
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from hangar import auth, daemonctl

# -- colours, shared with the web UI ----------------------------------------------------
C = {"bg": "#0b0d12", "bg2": "#12151c", "panel": "#161a23", "panel2": "#1c212c", "line": "#262c3a",
     "text": "#e8eaf0", "dim": "#8a92a6", "accent": "#7c9cff", "accent2": "#a7bbff", "green": "#4ade80",
     "cyan": "#38bdf8", "yellow": "#fbbf24", "red": "#f87171", "magenta": "#e879f9"}
STATE_COLOR = {"working": C["green"], "running": C["green"], "done": C["cyan"], "waiting_on_user": C["yellow"],
               "taken over": C["magenta"], "error": C["red"], "exited": C["red"], "dead": C["red"]}

QSS = f"""
* {{ color: {C['text']}; font-size: 13px; }}
QMainWindow, QDialog, #page {{ background: {C['bg']}; }}
QWidget#sidebar {{ background: {C['bg2']}; border-right: 1px solid {C['line']}; }}
QFrame#card, QFrame#panel {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 12px; }}
QFrame#card:hover {{ border-color: #3a4359; }}
QLabel#dim, QLabel[dim="true"] {{ color: {C['dim']}; }}
QLabel#h1 {{ font-size: 20px; font-weight: 600; }}
QLabel#h2 {{ font-size: 16px; font-weight: 600; }}
QPushButton {{ background: {C['panel2']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 6px 13px; }}
QPushButton:hover {{ background: #242a37; border-color: #343c4f; }}
QPushButton:disabled {{ color: {C['dim']}; }}
QPushButton[kind="primary"] {{ background: {C['accent']}; border-color: {C['accent']}; color: #0b0d12; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {C['accent2']}; }}
QPushButton[kind="danger"] {{ color: {C['red']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {C['dim']}; }}
QPushButton[kind="ghost"]:hover {{ color: {C['text']}; background: {C['panel2']}; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{ background: {C['bg2']}; border: 1px solid {C['line']};
    border-radius: 8px; padding: 6px 8px; selection-background-color: {C['accent']}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {C['accent']}; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox QAbstractItemView {{ background: {C['panel2']}; border: 1px solid {C['line']}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 8px; padding: 4px; }}
QListWidget::item:selected {{ background: rgba(124,156,255,.24); color: {C['text']}; }}
QListWidget::item:selected QLabel {{ color: {C['text']}; }}
QListWidget::item:hover:!selected {{ background: {C['panel2']}; }}
QTableWidget {{ background: transparent; border: none; gridline-color: {C['line']}; }}
QHeaderView::section {{ background: transparent; color: {C['dim']}; border: none; border-bottom: 1px solid {C['line']}; padding: 5px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C['dim']}; padding: 6px 12px; border-radius: 7px; }}
QTabBar::tab:selected {{ background: {C['panel2']}; color: {C['text']}; }}
QScrollArea, QScrollArea > QWidget > QWidget#scrollbody {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; }}
QScrollBar::handle:vertical {{ background: {C['line']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: {C['line']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {C['line']}; border-radius: 4px; background: {C['bg2']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
QCheckBox::indicator:disabled {{ background: {C['panel2']}; }}
QToolTip {{ background: {C['panel2']}; border: 1px solid {C['line']}; }}
"""


class ApiError(Exception):
    pass


class Api:
    """The daemon's API. Native apps run on the Hangar machine, so they read
    the admin token straight from Hangar's state dir."""

    def __init__(self) -> None:
        self.base = daemonctl.URL
        self.token = auth.admin_token()
        self.http = httpx.Client(base_url=self.base, timeout=httpx.Timeout(60, connect=5),
                                 headers={"Authorization": f"Bearer {self.token}"})
        self._lock = threading.Lock()

    def call(self, method: str, path: str, body=None, raw: bool = False):
        try:
            r = self.http.request(method, path, json=body)
        except httpx.HTTPError as e:
            raise ApiError(f"Can't reach the Hangar daemon ({type(e).__name__}).")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error") or r.text
            except ValueError:
                msg = r.text
            raise ApiError(msg or f"HTTP {r.status_code}")
        if raw:
            return r.content
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def put(self, path, body):
        return self.call("PUT", path, body)

    def delete(self, path):
        return self.call("DELETE", path)


api: Api | None = None


# -- run blocking calls off the UI thread -------------------------------------------------
class _Bridge(QObject):
    done = Signal(object, object, object)   # callback, result, error


_bridge: _Bridge | None = None


class _Job(QRunnable):
    def __init__(self, fn, cb, eb) -> None:
        super().__init__()
        self.fn, self.cb, self.eb = fn, cb, eb

    def run(self) -> None:
        try:
            res, err = self.fn(), None
        except Exception as e:   # handed to the UI thread
            res, err = None, e
        try:
            _bridge.done.emit((self.cb, self.eb), res, err)
        except RuntimeError:   # app is shutting down
            pass


def bg(fn: Callable, done: Callable | None = None, fail: Callable | None = None) -> None:
    """Run fn() in a worker thread; call done(result) or fail(error) on the UI thread."""
    QThreadPool.globalInstance().start(_Job(fn, done, fail))


def _deliver(cbs, res, err) -> None:
    cb, eb = cbs
    if err is not None:
        (eb or show_error)(err)
    elif cb:
        cb(res)


def show_error(err) -> None:
    w = QApplication.activeWindow()
    msg = str(err)
    if w and hasattr(w, "toast"):
        w.toast("Something went wrong", msg, error=True)
    else:
        QMessageBox.warning(w, "Hangar", msg)


# -- live events (server-sent events) -----------------------------------------------------
class Events(QObject):
    """Follows /api/events in a background (daemon) thread; emits on the UI thread."""

    event = Signal(dict)
    connected = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True).start()

    def requestInterruption(self) -> None:
        self._stop.set()

    def isInterruptionRequested(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                with httpx.stream("GET", f"{api.base}/api/events", headers={"Authorization": f"Bearer {api.token}"},
                                  timeout=httpx.Timeout(10, read=60)) as r:
                    self.connected.emit(True)
                    for line in r.iter_lines():
                        if self.isInterruptionRequested():
                            return
                        if line.startswith("data: "):
                            self.event.emit(json.loads(line[6:]))
            except (httpx.HTTPError, ValueError):
                pass
            self.connected.emit(False)
            time.sleep(2)


# -- widgets ---------------------------------------------------------------------------
def button(text: str, kind: str = "", on=None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if on:
        b.clicked.connect(on)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str = "", role: str = "", wrap: bool = False, select: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setObjectName(role)
    lbl.setWordWrap(wrap)
    if select:
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lbl


def hbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            continue
        if it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def vbox(*items, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for it in items:
        if it is None:
            continue
        if it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
        else:
            lay.addLayout(it)
    return lay


def badge(state: str) -> QLabel:
    color = STATE_COLOR.get(state, C["dim"])
    text = "needs you" if state == "waiting_on_user" else (state or "idle")
    lbl = QLabel(f"● {text}")
    lbl.setStyleSheet(f"color:{color}; background:{C['panel2']}; border-radius:9px; padding:2px 9px;"
                      "font-size:12px; font-weight:600;")
    return lbl


def site_icon(text: str, size: int = 34) -> QLabel:
    colors = ["#5b8cff", "#a78bfa", "#f472b6", "#fb923c", "#34d399", "#22d3ee", "#facc15", "#f87171"]
    n = 0
    for ch in text:
        n = (n * 31 + ord(ch)) & 0xFFFFFFFF
    lbl = QLabel((text[:1] or "?").upper())
    lbl.setFixedSize(size, size)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet(f"background:{colors[n % len(colors)]}; color:#0b0d12; border-radius:{size // 4}px;"
                      f"font-weight:700; font-size:{size // 2 - 2}px;")
    return lbl


def app_icon(letter: str, color: str) -> QIcon:
    """Simple generated icon per app (real artwork comes with the OS image)."""
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(C["bg2"]))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 120, 120, 28, 28)
    p.setPen(QColor(color))
    f = QFont()
    f.setPixelSize(70)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, letter)
    p.end()
    return QIcon(pm)


class Toasts(QWidget):
    """Little notices in the window's bottom-right corner."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.lay = vbox(spacing=8)
        self.setLayout(self.lay)
        parent.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type().name == "Resize":
            self.place()
        return False

    def place(self) -> None:
        self.adjustSize()
        p = self.parentWidget()
        self.move(p.width() - self.width() - 18, p.height() - self.height() - 18)
        self.raise_()

    def show_toast(self, title: str, msg: str = "", error: bool = False, on_click=None) -> None:
        f = QFrame()
        f.setObjectName("panel")
        f.setStyleSheet(f"QFrame#panel {{ background:{C['panel2']}; border:1px solid "
                        f"{'rgba(248,113,113,.6)' if error else C['line']}; border-radius:12px; }}")
        f.setMaximumWidth(380)
        t = label(f"<b>{title}</b>")
        m = label(msg[:300], "dim", wrap=True)
        f.setLayout(vbox(t, m if msg else None, spacing=2, margins=(12, 10, 12, 10)))
        if on_click:
            f.setCursor(Qt.PointingHandCursor)
            f.mousePressEvent = lambda _e: (on_click(), f.deleteLater())
        self.lay.addWidget(f)
        self.show()
        self.place()
        QTimer.singleShot(9000 if error else 6000, lambda: (f.deleteLater(), QTimer.singleShot(50, self.place)))


class ApprovalBar(QWidget):
    """Agents waiting for a login or a code. Shown in Hangar and Passwords."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.lay = vbox(spacing=8)
        self.setLayout(self.lay)
        self.hide()

    def refresh(self, data: dict | None = None) -> None:
        if data is None:
            bg(lambda: api.get("/api/vault"), self.refresh, lambda e: None)
            return
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        pending = [r for r in data["requests"] if r["status"] == "pending"]
        for r in pending:
            self.lay.addWidget(self._card(r))
        self.setVisible(bool(pending))

    def _card(self, r: dict) -> QFrame:
        f = QFrame()
        f.setStyleSheet("QFrame#req { background: rgba(251,191,36,.08); border: 1px solid rgba(251,191,36,.4);"
                        " border-radius: 12px; }")
        f.setObjectName("req")
        what = f"wants to sign in to {r['site']}" if r["kind"] == "login" else f"needs a verification code for {r['site']}"
        usable = [e for e in r["entries"] if (e["has_password"] if r["kind"] == "login" else e["has_totp"])]
        entry = QComboBox()
        for e in usable:
            entry.addItem(f"{e['label']} · {e['username'] or 'no username'}", e["id"])
        scope = QComboBox()
        for k, t in (("once", "Just this once"), ("hour", "For 1 hour"), ("always", f"Always for {r['agent']}")):
            scope.addItem(t, k)
        scope.setCurrentIndex(1)
        code = QLineEdit()
        code.setPlaceholderText("or type a code" if usable else "Code from text / email")
        code.setFixedWidth(160)
        err = label("", wrap=True)
        err.setStyleSheet(f"color:{C['red']}")

        def approve():
            body = {"entry": "" if code.text().strip() else (entry.currentData() or ""),
                    "scope": scope.currentData(), "code": code.text().strip()}
            go.setEnabled(False)

            def ok(res):
                w = self.window()
                if hasattr(w, "toast"):
                    w.toast(f"Filled in for {r['agent']}" if res["status"] == "filled"
                            else f"Couldn't fill for {r['agent']}", res["message"], error=res["status"] != "filled")
                self.changed.emit()

            def bad(e):
                err.setText(str(e))
                go.setEnabled(True)
            bg(lambda: api.post(f"/api/vault/requests/{r['id']}/approve", body), ok, bad)

        go = button("Sign in" if r["kind"] == "login" else "Send code", "primary", approve)
        deny = button("Deny", on=lambda: bg(lambda: api.post(f"/api/vault/requests/{r['id']}/deny"),
                                             lambda _: self.changed.emit()))
        text = vbox(label(f"<b>{r['agent']}</b> {what}"), label(r["reason"] or "No reason given.", "dim", wrap=True),
                    label(f"No saved password for {r['site']} yet. Add one in Passwords.", "dim")
                    if r["kind"] == "login" and not usable else None, err, spacing=1)
        key = label("🔑")
        key.setStyleSheet("font-size:20px")
        row = hbox(key, text, "stretch", spacing=10, margins=(12, 9, 12, 9))
        if usable:
            row.addWidget(entry)
            row.addWidget(scope)
        if r["kind"] == "code":
            row.addWidget(code)
        if usable or r["kind"] == "code":
            row.addWidget(go)
        row.addWidget(deny)
        f.setLayout(row)
        return f


# -- app bootstrap ----------------------------------------------------------------------
def markdown_html(text: str, link: str = "#7c9cff") -> str:
    """Markdown as rich text for Qt, with links in Hangar's accent colour
    (Qt's Markdown renderer hard-codes blue)."""
    import re
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setMarkdown(text)
    html = re.sub(r"<body[^>]*>", "<body>", doc.toHtml())          # the QML item sets the font
    html = re.sub(r'(<a [^>]*><span style=")[^"]*', rf"\1color:{link}; text-decoration:none;", html)
    # Tables: Qt's ruled grid draws its lines out of step with the text, so use
    # padded rows under a dim bold header instead of borders.
    html = re.sub(r"<table[^>]*>", '<table border="0" cellspacing="0" cellpadding="7">', html)

    def header(m: "re.Match") -> str:
        return re.sub(r"(<p[^>]*>)(.*?)</p>", r'\1<span style="font-weight:600; color:#8b93a7;">\2</span></p>',
                      m.group(0), flags=re.S)
    html = re.sub(r"<tr>.*?</tr>", header, html, count=1, flags=re.S)
    return html


APP_INFO = {
    "spaces": ("Hangar", "H", C["accent"]),
    "passwords": ("Passwords", "P", C["yellow"]),
    "accounts": ("Accounts", "A", C["green"]),
    "models": ("Models", "M", C["magenta"]),
    "settings": ("Hangar Settings", "S", C["cyan"]),
    "setup": ("Hangar Setup", "✦", C["accent2"]),
    "ask": ("Ask Hangar", "✦", C["accent"]),
    "notes": ("Notes", "N", C["green"]),
}


def mac_window(win) -> None:
    """macOS: no title bar, like most Mac apps: the content runs to the top and
    only the three window buttons sit over it (QML leaves Theme.chromeTop free).
    Sets the NSWindow's full-size content view through the Objective-C runtime."""
    if sys.platform != "darwin":
        return
    import ctypes
    import ctypes.util
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]

    def send(obj, sel, *args, ret=ctypes.c_void_p, types=()):
        f = ctypes.CFUNCTYPE(ret, ctypes.c_void_p, ctypes.c_void_p, *types)(("objc_msgSend", objc))
        return f(obj, objc.sel_registerName(sel.encode()), *args)
    view = ctypes.c_void_p(int(win.winId()))                     # Qt's NSView
    nswin = send(view, "window")
    if not nswin:
        return
    mask = send(nswin, "styleMask", ret=ctypes.c_ulong)
    send(nswin, "setStyleMask:", ctypes.c_ulong(mask | (1 << 15)), ret=None, types=(ctypes.c_ulong,))  # FullSizeContentView
    send(nswin, "setTitlebarAppearsTransparent:", ctypes.c_bool(True), ret=None, types=(ctypes.c_bool,))
    send(nswin, "setTitleVisibility:", ctypes.c_long(1), ret=None, types=(ctypes.c_long,))              # hidden
    # Qt lays its view out again once the window's size changes: nudge it.
    w, h = win.width(), win.height()
    win.resize(w, h + 1)
    win.resize(w, h)


def make_app(name: str) -> QApplication:
    """QApplication with Hangar's look. The desktop file name doubles as the
    Wayland app id, so each app gets its own taskbar entry on the OS."""
    global api, _bridge
    title, letter, color = APP_INFO[name]
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(title)
    app.setDesktopFileName(f"dev.hangar.{name}")
    if not (sys.platform == "darwin" and ".app/Contents/MacOS/" in sys.executable):
        app.setWindowIcon(app_icon(letter, color))      # on a Mac, Hangar.app's icon is the Dock icon
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    if not daemonctl.ensure_daemon():
        QMessageBox.critical(None, title, "Couldn't start the Hangar daemon. Is Docker or Podman running?")
        sys.exit(1)
    api = Api()
    _bridge = _Bridge()
    _bridge.done.connect(_deliver)
    return app


def run(name: str, window_cls) -> None:
    app = make_app(name)
    w = window_cls()
    w.setWindowTitle(APP_INFO[name][0])
    w.show()
    sys.exit(app.exec())
