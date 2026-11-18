"""A small native VNC viewer for agents' desktops.

Speaks just enough RFB 3.8 (no auth, Raw + CopyRect, desktop resize) over the
daemon's websocket proxy (/agents/<name>/vnc/websockify), so it gets the same
token checks as the web view. View-only unless control is on (take over).
"""

from __future__ import annotations

import struct

import websockets
from PySide6.QtCore import QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget
from websockets.sync.client import connect

from hangar.apps import common

KEYSYMS = {
    Qt.Key_Return: 0xFF0D, Qt.Key_Enter: 0xFF0D, Qt.Key_Backspace: 0xFF08, Qt.Key_Tab: 0xFF09,
    Qt.Key_Escape: 0xFF1B, Qt.Key_Delete: 0xFFFF, Qt.Key_Home: 0xFF50, Qt.Key_Left: 0xFF51, Qt.Key_Up: 0xFF52,
    Qt.Key_Right: 0xFF53, Qt.Key_Down: 0xFF54, Qt.Key_PageUp: 0xFF55, Qt.Key_PageDown: 0xFF56,
    Qt.Key_End: 0xFF57, Qt.Key_Insert: 0xFF63, Qt.Key_Shift: 0xFFE1, Qt.Key_Control: 0xFFE3,
    Qt.Key_Meta: 0xFFE3, Qt.Key_Alt: 0xFFE9, Qt.Key_Super_L: 0xFFEB, Qt.Key_CapsLock: 0xFFE5,
    **{getattr(Qt, f"Key_F{i}"): 0xFFBD + i for i in range(1, 13)},
}


class RFB(QThread):
    """Connection + decoder; emits whole frames to the UI thread."""

    frame = Signal(QImage)
    status = Signal(str)

    def __init__(self, agent: str) -> None:
        super().__init__()
        self.agent = agent
        self.ws = None
        self.buf = bytearray()
        self.fb: QImage | None = None

    # -- byte stream over websocket frames --------------------------------------------
    def read(self, n: int) -> bytes:
        while len(self.buf) < n:
            m = self.ws.recv()
            self.buf += m if isinstance(m, bytes) else m.encode()
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def send(self, data: bytes) -> None:
        if self.ws is not None:
            try:
                self.ws.send(data)
            except websockets.WebSocketException:
                pass

    # -- protocol ----------------------------------------------------------------------
    def run(self) -> None:
        url = common.api.base.replace("http", "ws", 1) + f"/agents/{self.agent}/vnc/websockify"
        while not self.isInterruptionRequested():
            try:
                self.status.emit("Connecting…")
                with connect(url, additional_headers={"Authorization": f"Bearer {common.api.token}"},
                             subprotocols=["binary"], max_size=None, open_timeout=10) as ws:
                    self.ws, self.buf = ws, bytearray()
                    self.handshake()
                    self.status.emit("")
                    self.loop()
            except Exception as e:   # agent stopped, daemon restarted…: retry
                self.ws = None
                if self.isInterruptionRequested():
                    return
                self.status.emit(f"Not connected ({type(e).__name__}). Retrying…")
                self.msleep(2000)

    def handshake(self) -> None:
        ver = self.read(12)
        if not ver.startswith(b"RFB "):
            raise ConnectionError("not a VNC server")
        self.send(b"RFB 003.008\n")
        types = self.read(self.read(1)[0])
        if 1 not in types:
            raise ConnectionError("server wants a password")
        self.send(b"\x01")
        if struct.unpack(">I", self.read(4))[0] != 0:
            raise ConnectionError("VNC security handshake failed")
        self.send(b"\x01")                                   # ClientInit: shared
        w, h = struct.unpack(">HH", self.read(4))
        self.read(16)
        self.read(struct.unpack(">I", self.read(4))[0])      # desktop name
        self.fb = QImage(w, h, QImage.Format_RGB32)
        self.fb.fill(QColor("#07080b"))
        # 32bpp little-endian BGRX == QImage.Format_RGB32 in memory.
        self.send(struct.pack(">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        encs = [0, 1, -223]                                   # Raw, CopyRect, DesktopSize
        self.send(struct.pack(">BxH", 2, len(encs)) + b"".join(struct.pack(">i", e) for e in encs))
        self.request(incremental=False)

    def request(self, incremental: bool = True) -> None:
        self.send(struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, self.fb.width(), self.fb.height()))

    def loop(self) -> None:
        while not self.isInterruptionRequested():
            t = self.read(1)[0]
            if t == 0:
                self.read(1)
                for _ in range(struct.unpack(">H", self.read(2))[0]):
                    x, y, w, h, enc = struct.unpack(">HHHHi", self.read(12))
                    if enc == 0:
                        data = self.read(w * h * 4)
                        img = QImage(data, w, h, w * 4, QImage.Format_RGB32)
                        p = QPainter(self.fb)
                        p.drawImage(x, y, img)
                        p.end()
                    elif enc == 1:
                        sx, sy = struct.unpack(">HH", self.read(4))
                        piece = self.fb.copy(sx, sy, w, h)
                        p = QPainter(self.fb)
                        p.drawImage(x, y, piece)
                        p.end()
                    elif enc == -223:
                        self.fb = self.fb.copy(0, 0, w, h)
                    else:
                        raise ConnectionError(f"unexpected encoding {enc}")
                self.frame.emit(self.fb.copy())
                self.request()
            elif t == 1:
                self.read(3)
                self.read(6 * struct.unpack(">H", self.read(2))[0])
            elif t == 2:
                pass
            elif t == 3:
                self.read(3)
                self.read(struct.unpack(">I", self.read(4))[0])
            else:
                raise ConnectionError(f"unknown message {t}")

    def stop(self) -> None:
        self.requestInterruption()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.wait(3000)


class VncView(QWidget):
    """Scaled live view of an agent's desktop. set_control(True) sends input."""

    def __init__(self, agent: str) -> None:
        super().__init__()
        self.img: QImage | None = None
        self.msg = "Connecting…"
        self.control = False
        self.buttons = 0
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.rfb = RFB(agent)
        self.rfb.frame.connect(self._frame)
        self.rfb.status.connect(self._status)
        self.rfb.start()

    def _frame(self, img: QImage) -> None:
        self.img = img
        self.update()

    def _status(self, s: str) -> None:
        self.msg = s
        self.update()

    def set_control(self, on: bool) -> None:
        self.control = on
        self.setCursor(Qt.ArrowCursor if on else Qt.ForbiddenCursor if False else Qt.ArrowCursor)
        if on:
            self.setFocus()
        self.update()

    def target(self) -> QRect:
        if not self.img:
            return self.rect()
        s = self.img.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRect(QPoint((self.width() - s.width()) // 2, (self.height() - s.height()) // 2), s)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#07080b"))
        if self.img:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self.target(), self.img)
        if self.msg:
            p.setPen(QColor(common.C["dim"]))
            p.drawText(self.rect(), Qt.AlignCenter, self.msg)
        if self.control:
            p.setPen(QColor(common.C["magenta"]))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # -- input (only while you have control) ---------------------------------------------
    def _pos(self, e) -> tuple[int, int]:
        r = self.target()
        x = (e.position().x() - r.x()) * self.img.width() / max(r.width(), 1)
        y = (e.position().y() - r.y()) * self.img.height() / max(r.height(), 1)
        return max(0, min(int(x), self.img.width() - 1)), max(0, min(int(y), self.img.height() - 1))

    def _pointer(self, e, mask: int | None = None) -> None:
        if not (self.control and self.img and self.rfb.fb):
            return
        x, y = self._pos(e)
        self.rfb.send(struct.pack(">BBHH", 5, self.buttons if mask is None else mask, x, y))

    @staticmethod
    def _mask(b) -> int:
        return {Qt.LeftButton: 1, Qt.MiddleButton: 2, Qt.RightButton: 4}.get(b, 0)

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        self.buttons |= self._mask(e.button())
        self._pointer(e)

    def mouseReleaseEvent(self, e) -> None:
        self.buttons &= ~self._mask(e.button())
        self._pointer(e)

    def mouseMoveEvent(self, e) -> None:
        self._pointer(e)

    def wheelEvent(self, e) -> None:
        bit = 8 if e.angleDelta().y() > 0 else 16
        self._pointer(e, self.buttons | bit)
        self._pointer(e, self.buttons)

    def _key(self, e, down: bool) -> None:
        if not self.control:
            return
        k = e.key()
        sym = KEYSYMS.get(k)
        if sym is None:
            if Qt.Key_A <= k <= Qt.Key_Z and (e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
                sym = ord(chr(k).lower())
            elif e.text() and e.text().isprintable():
                c = e.text()[0]
                sym = ord(c) if ord(c) < 256 else 0x01000000 | ord(c)
            elif 0x20 <= k < 0x7F:
                sym = ord(chr(k).lower())
        if sym is not None:
            self.rfb.send(struct.pack(">BBxxI", 4, 1 if down else 0, sym))

    def keyPressEvent(self, e) -> None:
        self._key(e, True)

    def keyReleaseEvent(self, e) -> None:
        self._key(e, False)

    def focusNextPrevChild(self, _next: bool) -> bool:
        return not self.control   # keep Tab inside the desktop while in control

    def close_view(self) -> None:
        self.rfb.stop()
