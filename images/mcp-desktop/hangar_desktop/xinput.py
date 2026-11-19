"""Per-agent mouse and keyboard on one desktop (X Multi-Pointer).

Each agent in a space gets its own master pointer + keyboard pair
("hangar-<agent>"), created on first use. Input is injected into that pair
only (XTest device events), so several agents can click and type at the same
time without moving each other's cursor or stealing keyboard focus. The core
pointer is left for the person (VNC / take over).

Keyboard focus is set per agent with XISetFocus, because Openbox doesn't know
about multiple pointers. A light per-window lock stops two agents typing into
the same window at once (most apps have a single text cursor).
"""

from __future__ import annotations

import ctypes as C
import re
import subprocess
import threading
import time

from Xlib import X as XC
from Xlib import display as xdisplay

LOCK_SECONDS = 20
ALIASES = {"ctrl": "Control_L", "control": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L",
           "meta": "Super_L", "win": "Super_L", "cmd": "Super_L", "enter": "Return", "esc": "Escape",
           "del": "Delete", "backspace": "BackSpace", "pgup": "Prior", "pgdn": "Next", "space": "space",
           "tab": "Tab", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}


class WindowBusy(RuntimeError):
    pass


class Input:
    def __init__(self) -> None:
        self.X = C.cdll.LoadLibrary("libX11.so.6")
        self.Xi = C.cdll.LoadLibrary("libXi.so.6")
        self.Xt = C.cdll.LoadLibrary("libXtst.so.6")
        X, Xi, Xt = self.X, self.Xi, self.Xt
        X.XOpenDisplay.restype = C.c_void_p
        X.XFlush.argtypes = [C.c_void_p]
        X.XStringToKeysym.restype = C.c_ulong
        X.XStringToKeysym.argtypes = [C.c_char_p]
        X.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        X.XKeysymToKeycode.restype = C.c_ubyte
        X.XKeycodeToKeysym.argtypes = [C.c_void_p, C.c_ubyte, C.c_int]
        X.XKeycodeToKeysym.restype = C.c_ulong
        Xi.XOpenDevice.restype = C.c_void_p
        Xi.XOpenDevice.argtypes = [C.c_void_p, C.c_ulong]
        Xi.XISetFocus.argtypes = [C.c_void_p, C.c_int, C.c_ulong, C.c_ulong]
        Xi.XIQueryPointer.argtypes = [C.c_void_p, C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
                                      C.POINTER(C.c_double), C.POINTER(C.c_double), C.POINTER(C.c_double),
                                      C.POINTER(C.c_double), C.c_void_p, C.c_void_p, C.c_void_p]
        for fn in ("XTestFakeDeviceMotionEvent",):
            getattr(Xt, fn).argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.POINTER(C.c_int), C.c_int,
                                        C.c_ulong]
        for fn in ("XTestFakeDeviceButtonEvent", "XTestFakeDeviceKeyEvent"):
            getattr(Xt, fn).argtypes = [C.c_void_p, C.c_void_p, C.c_uint, C.c_int, C.POINTER(C.c_int), C.c_int,
                                        C.c_ulong]
        self.dpy = X.XOpenDisplay(None)
        self.xd = xdisplay.Display()
        self.root = self.xd.screen().root
        self.devs: dict[str, dict] = {}
        self.focus: dict[str, int] = {}              # agent -> window its keyboard is focused on
        self.locks: dict[int, tuple[str, float]] = {}  # window -> (agent, expires)
        self.mu = threading.RLock()
        self._none = (C.c_int * 2)()

    # -- devices -----------------------------------------------------------------------
    @staticmethod
    def _list() -> dict[str, int]:
        out = subprocess.run(["xinput", "list", "--short"], capture_output=True, text=True).stdout
        found = {}
        for line in out.splitlines():
            m = re.search(r"([^\t⎡⎜⎣↳]+?)\s+id=(\d+)", line)
            if m:
                found[m.group(1).strip()] = int(m.group(2))
        return found

    def devices(self, agent: str) -> dict:
        with self.mu:
            if agent in self.devs:
                return self.devs[agent]
            name = "hangar-" + re.sub(r"[^a-z0-9-]", "-", agent.lower())[:24]
            ids = self._list()
            if f"{name} pointer" not in ids:
                subprocess.run(["xinput", "create-master", name], check=True)
                ids = self._list()
            d = {"name": name, "pointer": ids[f"{name} pointer"], "keyboard": ids[f"{name} keyboard"],
                 "ptr": self.Xi.XOpenDevice(self.dpy, ids[f"{name} XTEST pointer"]),
                 "kbd": self.Xi.XOpenDevice(self.dpy, ids[f"{name} XTEST keyboard"])}
            self.devs[agent] = d
            return d

    def pointers(self) -> dict[str, tuple[int, int]]:
        """Where each agent's cursor is (for drawing them in the viewer)."""
        out = {}
        for agent, d in list(self.devs.items()):
            root, child = C.c_ulong(), C.c_ulong()
            rx, ry, wx, wy = C.c_double(), C.c_double(), C.c_double(), C.c_double()
            buttons = (C.c_byte * 16)()
            mods = (C.c_byte * 16)()
            grp = (C.c_byte * 16)()
            self.Xi.XIQueryPointer(self.dpy, d["pointer"], self.root.id, C.byref(root), C.byref(child), C.byref(rx),
                                   C.byref(ry), C.byref(wx), C.byref(wy), buttons, mods, grp)
            out[agent] = (int(rx.value), int(ry.value))
        return out

    # -- windows and locks ------------------------------------------------------------------
    def window_at(self, x: int, y: int) -> int | None:
        """Topmost managed window containing (x, y)."""
        atom = self.xd.intern_atom("_NET_CLIENT_LIST_STACKING")
        prop = self.root.get_full_property(atom, XC.AnyPropertyType)
        for wid in reversed(list(prop.value) if prop else []):
            try:
                w = self.xd.create_resource_object("window", wid)
                if w.get_attributes().map_state != XC.IsViewable:
                    continue
                g = w.get_geometry()
                t = w.translate_coords(self.root, 0, 0)
                ox, oy = -t.x, -t.y
                if ox <= x < ox + g.width and oy <= y < oy + g.height:
                    return wid
            except Exception:
                continue
        return None

    def title(self, wid: int) -> str:
        try:
            w = self.xd.create_resource_object("window", wid)
            name = w.get_full_property(self.xd.intern_atom("_NET_WM_NAME"), XC.AnyPropertyType)
            return name.value.decode(errors="replace") if name else (w.get_wm_name() or "")
        except Exception:
            return ""

    def claim(self, agent: str, wid: int | None) -> None:
        if not wid:
            return
        with self.mu:
            now = time.time()
            holder = self.locks.get(wid)
            if holder and holder[0] != agent and holder[1] > now:
                raise WindowBusy(f'{holder[0]} is using the window "{self.title(wid)}" right now. Work in another '
                                 f"window, or wait about {int(holder[1] - now)}s and try again.")
            self.locks[wid] = (agent, now + LOCK_SECONDS)

    # -- input ----------------------------------------------------------------------------------
    def _flush(self) -> None:
        self.X.XFlush(self.dpy)

    def move(self, agent: str, x: int, y: int) -> None:
        d = self.devices(agent)
        axes = (C.c_int * 2)(x, y)
        self.Xt.XTestFakeDeviceMotionEvent(self.dpy, d["ptr"], 0, 0, axes, 2, 0)
        self._flush()

    def button(self, agent: str, button: int, press: bool) -> None:
        d = self.devices(agent)
        self.Xt.XTestFakeDeviceButtonEvent(self.dpy, d["ptr"], button, 1 if press else 0, self._none, 0, 0)
        self._flush()

    def click(self, agent: str, x: int, y: int, button: int = 1, clicks: int = 1) -> None:
        wid = self.window_at(x, y)
        self.claim(agent, wid)
        self.move(agent, x, y)
        time.sleep(0.03)
        for _ in range(clicks):
            self.button(agent, button, True)
            self.button(agent, button, False)
            time.sleep(0.08)
        if wid and button == 1:
            self.set_focus(agent, wid)

    def drag(self, agent: str, x1: int, y1: int, x2: int, y2: int) -> None:
        self.claim(agent, self.window_at(x1, y1))
        self.move(agent, x1, y1)
        self.button(agent, 1, True)
        for i in range(1, 11):
            self.move(agent, x1 + (x2 - x1) * i // 10, y1 + (y2 - y1) * i // 10)
            time.sleep(0.02)
        self.button(agent, 1, False)

    def scroll(self, agent: str, button: int, amount: int, x: int | None, y: int | None) -> None:
        if x is not None and y is not None:
            self.claim(agent, self.window_at(x, y))
            self.move(agent, x, y)
        for _ in range(amount):
            self.button(agent, button, True)
            self.button(agent, button, False)
            time.sleep(0.03)

    def set_focus(self, agent: str, wid: int) -> None:
        d = self.devices(agent)
        self.Xi.XISetFocus(self.dpy, d["keyboard"], wid, 0)
        self.focus[agent] = wid
        self._flush()

    def _target(self, agent: str) -> None:
        wid = self.focus.get(agent)
        if wid is None:
            raise RuntimeError("Click the window or field to type into first (your keyboard isn't in any window yet).")
        self.claim(agent, wid)
        self.set_focus(agent, wid)

    def _keysym(self, name: str) -> int:
        sym = self.X.XStringToKeysym(ALIASES.get(name.lower(), name).encode())
        if not sym:
            raise ValueError(f"unknown key '{name}'")
        return sym

    def _key(self, agent: str, keycode: int, press: bool) -> None:
        d = self.devices(agent)
        self.Xt.XTestFakeDeviceKeyEvent(self.dpy, d["kbd"], keycode, 1 if press else 0, self._none, 0, 0)

    def key(self, agent: str, combos: str) -> None:
        """xdotool-style: "ctrl+l Return", "ctrl+shift+t"."""
        self._target(agent)
        for combo in combos.split():
            codes = [self.X.XKeysymToKeycode(self.dpy, self._keysym(k)) for k in combo.split("+")]
            for c in codes:
                self._key(agent, c, True)
            for c in reversed(codes):
                self._key(agent, c, False)
            self._flush()
            time.sleep(0.04)

    def type_text(self, agent: str, text: str) -> None:
        self._target(agent)
        shift = self.X.XKeysymToKeycode(self.dpy, self._keysym("Shift_L"))
        missing = []
        for ch in text:
            if ch == "\n":
                sym = self._keysym("Return")
            elif ch == "\t":
                sym = self._keysym("Tab")
            else:
                cp = ord(ch)
                sym = cp if (0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF) else 0x01000000 | cp
            kc = self.X.XKeysymToKeycode(self.dpy, sym)
            if not kc:
                missing.append(ch)
                continue
            shifted = self.X.XKeycodeToKeysym(self.dpy, kc, 0) != sym and self.X.XKeycodeToKeysym(self.dpy, kc, 1) == sym
            if shifted:
                self._key(agent, shift, True)
            self._key(agent, kc, True)
            self._key(agent, kc, False)
            if shifted:
                self._key(agent, shift, False)
            self._flush()
            time.sleep(0.012)
        if missing:
            raise RuntimeError(f"couldn't type {''.join(sorted(set(missing)))!r} (not on the keyboard layout); "
                               "paste those instead")
