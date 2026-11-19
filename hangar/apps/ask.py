"""Ask Hangar: a quick panel for asking the pilot, from anywhere.

Runs in the background with a tray / menu-bar icon, so shortcuts open it
instantly. Modes (each is a shortcut on the Hangar OS, and a tray menu item):
  ask          just the prompt                                 Meta+Space
  screenshot   drag a box (or pick a window / full screen),
               then the prompt with that screenshot attached   Print, Meta+Shift+S
  context      the prompt with the front window / page offered Meta+Shift+Space

Context is captured *before* the panel appears (so the panel isn't in the
screenshot and the front window is still yours). Each piece shows as a chip you
can turn off; only what you explicitly captured starts switched on.

  hangar-app ask [--screenshot [region|window|full]] [--context] [--tray]
"""

from __future__ import annotations

import base64
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QMenu, QPlainTextEdit, QPushButton, QSystemTrayIcon,
                               QWidget)

from hangar import capture, voice
from hangar.apps import common
from hangar.apps.common import C, bg, button, hbox, label, vbox

SOCKET = "hangar-ask"


class Chip(QPushButton):
    def __init__(self, text: str, on: bool) -> None:
        super().__init__(text)
        self.setCheckable(True)
        self.setChecked(on)
        self.setCursor(Qt.PointingHandCursor)
        self.setMaximumWidth(270)
        # Qt draws square corners if the radius is over half the height, so pin both.
        self.setFixedHeight(30)
        self.setStyleSheet(f"""
            QPushButton {{ background: rgba(255,255,255,.035); color: {C['dim']};
                           border: 1px solid rgba(255,255,255,.07); border-radius: 15px;
                           padding: 0 14px; font-size: 12px; text-align: left; }}
            QPushButton:hover {{ background: rgba(255,255,255,.07); color: {C['text']}; }}
            QPushButton:checked {{ background: rgba(124,156,255,.18); color: {C['text']};
                                   border: 1px solid rgba(124,156,255,.45); }}""")
        self.toggled.connect(self._label)
        self._base = text
        self._label(on)

    def _label(self, on: bool) -> None:
        self.setText(("✓  " if on else "+  ") + self._base)
        self.setToolTip("Included: click to leave it out" if on else "Not included: click to include it")


def rounded(pm: QPixmap, r: int) -> QPixmap:
    from PySide6.QtGui import QPainter, QPainterPath
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pm.width(), pm.height(), r, r)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


class Bridge(QObject):
    transcript = Signal(str)
    failed = Signal(str)


class AskPanel(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(720)
        self.ctx: dict = {}
        self.shot: Path | None = None
        self.asked_at = ""
        card = QFrame()
        card.setObjectName("askcard")
        card.setStyleSheet("QFrame#askcard { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                           "stop:0 rgba(30,35,48,.98), stop:1 rgba(20,23,32,.98)); "
                           "border: 1px solid rgba(255,255,255,.09); border-radius: 26px; }")
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask Hangar anything… (Enter to send, Shift+Enter for a new line)")
        self.input.setFixedHeight(76)
        self.input.setStyleSheet("QPlainTextEdit { background: transparent; border: none; font-size: 16px; }")
        self.input.installEventFilter(self)
        self.chips = vbox(spacing=6)   # rows of two chips
        self.thumb = label()
        self.thumb.setStyleSheet("border-radius: 12px; border: 1px solid rgba(255,255,255,.1);")
        self.thumb.hide()
        self.mic = button("🎙", "ghost", self.toggle_mic, "Click to talk, click again to stop")
        self.mic.setFixedSize(40, 40)
        self.mic.setStyleSheet("QPushButton { border-radius: 20px; font-size: 17px; background: rgba(255,255,255,.05); }"
                               "QPushButton:hover { background: rgba(255,255,255,.1); }")
        self.mic.setVisible(bool(voice.available() and voice.chosen()))
        self.status = label("", "dim")
        self.answer = label("", wrap=True, select=True)
        self.answer.setStyleSheet("background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.06);"
                                  "border-radius: 16px; padding: 12px 14px; font-size: 14px;")
        self.answer.hide()
        self.open_btn = button("Open in Hangar", on=self.open_hangar)
        self.open_btn.hide()
        self.send_btn = button("Ask  ↵", "primary", self.send)
        self.send_btn.setFixedHeight(36)
        self.send_btn.setStyleSheet(f"QPushButton {{ border-radius: 18px; padding: 0 20px; background: {C['accent']};"
                                    f" color: #0b0d12; font-weight: 650; border: none; }}"
                                    f"QPushButton:hover {{ background: {C['accent2']}; }}"
                                    "QPushButton:disabled { background: rgba(124,156,255,.35); }")
        self.model = label("", "dim")
        spark = label("✦")
        spark.setAlignment(Qt.AlignTop)
        spark.setStyleSheet(f"color:{C['accent']}; font-size:18px; padding-top:4px")
        card.setLayout(vbox(hbox(spark, self.input, self.mic, spacing=10),
                            hbox(self.thumb, self.chips, "stretch", spacing=12),
                            self.answer,
                            hbox(self.model, "stretch", self.status, self.open_btn, self.send_btn),
                            spacing=12, margins=(22, 18, 22, 18)))
        self.setLayout(vbox(card, margins=(30, 20, 30, 40)))   # room for the shadow
        QShortcut(QKeySequence("Escape"), self, self.hide)
        self.recorder = None
        self.bridge = Bridge()
        self.bridge.transcript.connect(self._got_transcript)
        self.bridge.failed.connect(lambda e: self.status.setText(f"Voice: {e}"))
        self.events = common.Events(self)
        self.events.event.connect(self.on_event)
        self.events.start()

    # -- showing -------------------------------------------------------------------------
    def open(self, shot: Path | None = None, ctx: dict | None = None, pre: set[str] = frozenset()) -> None:
        self.shot, self.ctx = shot, ctx or {}
        self.answer.hide()
        self.open_btn.hide()
        self.status.setText("")
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.clear()
        while self.chips.count():
            item = self.chips.takeAt(0)
            row = item.layout()
            while row is not None and row.count():
                w = row.takeAt(0).widget()
                if w:
                    w.deleteLater()
        self.chip_for: dict[str, Chip] = {}
        if shot:
            self._chip("screenshot", "📷 Screenshot", "screenshot" in pre)
            pm = QPixmap(str(shot))
            self.thumb.setPixmap(rounded(pm.scaled(132, 82, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                                     .copy(0, 0, 132, 82), 12))
            self.thumb.show()
        else:
            self.thumb.hide()
        def short(t: str, n: int = 30) -> str:
            return t if len(t) <= n else t[:n - 1] + "…"
        if self.ctx.get("title") or self.ctx.get("app"):
            self._chip("window", f"🪟 {short(self.ctx.get('title') or self.ctx.get('app'))}", "window" in pre)
        if self.ctx.get("url"):
            u = self.ctx["url"].split("://", 1)[-1]
            self._chip("url", f"🔗 {short(u)}", "url" in pre)
        if self.ctx.get("selection"):
            first = self.ctx["selection"].strip().splitlines()[0] if self.ctx["selection"].strip() else ""
            self._chip("selection", f"✂ “{short(first, 26)}”", "selection" in pre)
        self.mic.setVisible(bool(voice.available() and voice.chosen()))
        bg(lambda: common.api.get("/api/orchestration"),
           lambda o: self.model.setText(f"Pilot: {o['pilot']['model'] or 'not set up yet'}"), lambda _e: None)
        self.adjustSize()
        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen().geometry().center()) or \
            QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + int(g.height() * 0.16))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _chip(self, key: str, text: str, on: bool) -> None:
        c = Chip(text, on)
        c.setToolTip(c.toolTip() + "\n" + {"window": self.ctx.get("title", ""), "url": self.ctx.get("url", ""),
                                             "selection": self.ctx.get("selection", "")[:500]}.get(key, ""))
        self.chip_for[key] = c
        row = self.chips.itemAt(self.chips.count() - 1).layout() if self.chips.count() else None
        if row is None or row.count() >= 3:     # two chips + a stretch per row
            row = hbox(spacing=6)
            self.chips.addLayout(row)
            row.addStretch(1)
        row.insertWidget(row.count() - 1, c)

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type().name == "KeyPress" and ev.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not ev.modifiers() & Qt.ShiftModifier:
            self.send()
            return True
        return False

    # -- sending ---------------------------------------------------------------------------------
    def send(self) -> None:
        q = self.input.toPlainText().strip()
        if not q:
            return
        on = {k for k, c in self.chip_for.items() if c.isChecked()}
        body = {"question": q, "context": {k: v for k, v in self.ctx.items()
                                           if (k == "title" and "window" in on) or (k == "app" and "window" in on)
                                           or (k in ("url", "selection") and k in on)}}
        if self.shot and "screenshot" in on:
            body["screenshot"] = base64.b64encode(self.shot.read_bytes()).decode()
        self.send_btn.setEnabled(False)
        self.input.setEnabled(False)
        self.status.setText("Sending…")

        def ok(r) -> None:
            from datetime import datetime, timezone
            self.asked_at = datetime.now(timezone.utc).isoformat()
            self.status.setText("Queued: the pilot is finishing something else." if r.get("queued")
                                else "The pilot is on it… (you can close this; the answer comes as a notification)")
            self.open_btn.show()

        def bad(e) -> None:
            self.status.setText("")
            self.answer.setText(str(e))
            self.answer.show()
            self.send_btn.setEnabled(True)
            self.input.setEnabled(True)
            self.adjustSize()
        bg(lambda: common.api.post("/api/ask", body), ok, bad)

    def on_event(self, ev: dict) -> None:
        if (ev.get("type") == "report" and ev.get("agent") == "pilot" and ev.get("final")
                and self.asked_at and ev.get("ts", "") >= self.asked_at):
            self.asked_at = ""
            self.status.setText("")
            self.answer.setText(ev.get("message", ""))
            self.answer.show()
            self.input.setEnabled(True)
            self.input.clear()
            self.input.setPlaceholderText("Ask a follow-up…")
            self.send_btn.setEnabled(True)
            self.adjustSize()

    def open_hangar(self) -> None:
        import subprocess
        args = ["--thread", self.thread] if getattr(self, "thread", "") else []
        subprocess.Popen([sys.executable, "-m", "hangar.apps.home", *args], start_new_session=True)
        self.hide()

    # -- voice --------------------------------------------------------------------------------------
    def toggle_mic(self) -> None:
        if self.recorder is None:
            try:
                self.recorder = voice.Recorder()
                self.recorder.start()
            except Exception as e:   # no microphone / permission
                self.recorder = None
                self.status.setText(f"Microphone: {e}")
                return
            self.mic.setText("⏹")
            self.status.setText("Listening… click ⏹ when you're done")
            return
        audio = self.recorder.stop()
        self.recorder = None
        self.mic.setText("🎙")
        self.status.setText("Transcribing (on this computer)…")

        def work():
            try:
                self.bridge.transcript.emit(voice.transcribe(audio))
            except Exception as e:
                self.bridge.failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _got_transcript(self, text: str) -> None:
        self.status.setText("")
        if text:
            cur = self.input.toPlainText().strip()
            self.input.setPlainText((cur + " " + text).strip())
            self.input.setFocus()


class Resident(QObject):
    """The background part: tray icon, shortcuts' entry point, captures."""

    captured = Signal(object, object, object)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.panel = AskPanel()
        self.captured.connect(self._show)
        self.tray = QSystemTrayIcon(app.windowIcon())
        self.tray.setToolTip("Ask Hangar")
        menu = QMenu()
        for text, mode in (("Ask Hangar…", "ask"), ("Screenshot && ask…", "screenshot:region"),
                           ("Ask about this window…", "context")):
            act = QAction(text, menu)
            act.triggered.connect(lambda _=False, m=mode: self.handle(m))
            menu.addAction(act)
        menu.addSeparator()
        for text, mod in (("Open Hangar", "home"), ("Notes", "notes"), ("Passwords", "passwords"), ("Settings", "settings")):
            act = QAction(text, menu)
            act.triggered.connect(lambda _=False, m=mod: self._launch(m))
            menu.addAction(act)
        menu.addSeparator()
        quit_ = QAction("Quit Ask Hangar", menu)
        quit_.triggered.connect(app.quit)
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and self.handle("ask"))
        self.tray.show()
        self.menu = menu
        self.last_thread = ""
        self.tray.messageClicked.connect(self.open_thread)
        self.events = common.Events(self)
        self.events.event.connect(self._on_event)
        self.events.start()

    def _on_event(self, ev: dict) -> None:
        """When the pilot answers (and you're not looking at the answer), say so from
        the tray; clicking the notification opens the whole conversation in Hangar."""
        if ev.get("type") != "report" or ev.get("agent") != "pilot" or not ev.get("final"):
            return
        if self.panel.isVisible() and self.panel.asked_at:
            return            # the panel shows it
        self.last_thread = ev.get("thread", "")
        icon = QSystemTrayIcon.Warning if ev.get("status") in ("error", "waiting_on_user") else QSystemTrayIcon.Information
        self.tray.showMessage("Pilot", ev.get("message", "")[:400], icon, 12000)

    def open_thread(self) -> None:
        import subprocess
        args = ["--thread", self.last_thread] if self.last_thread else []
        subprocess.Popen([sys.executable, "-m", "hangar.apps.home", *args], start_new_session=True)

    def _show(self, shot, ctx, pre) -> None:
        if shot:   # like any screenshot tool, it's on the clipboard too
            from PySide6.QtGui import QImage
            QGuiApplication.clipboard().setImage(QImage(str(shot)))
        self.panel.open(shot, ctx, pre)

    @staticmethod
    def _launch(mod: str) -> None:
        import subprocess
        subprocess.Popen([sys.executable, "-m", f"hangar.apps.{mod}"], start_new_session=True)

    def handle(self, mode: str) -> None:
        """Capture first (in the background: region selection waits for the user), then open the panel."""
        if mode == "ask":
            self.panel.open()
            return
        self.panel.hide()

        def work():
            if mode.startswith("screenshot"):
                shot = capture.screenshot(mode.split(":", 1)[1] if ":" in mode else "region")
                if shot is None:
                    return            # cancelled
                capture.keep(shot)    # still a normal screenshot: saved to Pictures/Screenshots
                self.captured.emit(shot, {}, {"screenshot"})
            else:
                self.captured.emit(None, capture.context(), {"window", "url"})
        QTimer.singleShot(150, lambda: threading.Thread(target=work, daemon=True).start())


def mode_from_argv(argv: list[str]) -> str:
    if "--screenshot" in argv:
        i = argv.index("--screenshot")
        kind = argv[i + 1] if i + 1 < len(argv) and argv[i + 1] in ("region", "window", "full") else "region"
        return f"screenshot:{kind}"
    if "--context" in argv:
        return "context"
    return "tray" if "--tray" in argv else "ask"


def main() -> None:
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    mode = mode_from_argv(sys.argv)
    app = common.make_app("ask")
    app.setQuitOnLastWindowClosed(False)
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if sock.waitForConnected(300):
        sock.write(mode.encode())
        sock.flush()
        sock.waitForBytesWritten(1000)
        return
    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)
    res = Resident(app)

    def incoming() -> None:
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(1000)
        m = bytes(conn.readAll()).decode()
        if m != "tray":
            res.handle(m)
    server.newConnection.connect(incoming)
    if mode != "tray":
        res.handle(mode)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
