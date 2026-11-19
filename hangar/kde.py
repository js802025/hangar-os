"""KDE Plasma integration: give a space its own virtual desktop.

On the Hangar OS, "pop out" puts a space's live desktop full-screen on a KWin
virtual desktop of its own (named "✦ <space>"), so Meta+Ctrl+arrows, KWin's
Overview and the pager all show your agents next to your own desktops.

Talks to KWin over D-Bus (Plasma 6): the VirtualDesktopManager to create,
switch and remove desktops, and a tiny KWin script to move the space's window
onto its desktop. Everything is a no-op elsewhere (macOS, other desktops).
Needs a running Qt application (QtDBus).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PREFIX = "✦ "
SERVICE = "org.kde.KWin"


def _bus():
    from PySide6.QtDBus import QDBusConnection
    return QDBusConnection.sessionBus()


def available() -> bool:
    if sys.platform != "linux" or not os.environ.get("DBUS_SESSION_BUS_ADDRESS", "unix:"):
        return False
    try:
        bus = _bus()
        return bus.isConnected() and bus.interface().isServiceRegistered(SERVICE).value()
    except Exception:  # noqa: BLE001 - no Qt D-Bus, no session bus: just not KDE
        return False


def _vdm():
    from PySide6.QtDBus import QDBusInterface
    return QDBusInterface(SERVICE, "/VirtualDesktopManager", "org.kde.KWin.VirtualDesktopManager", _bus())


def desktops() -> list[dict]:
    """[{position, id, name}] of KWin's virtual desktops."""
    from PySide6.QtDBus import QDBusInterface, QDBusMessage
    props = QDBusInterface(SERVICE, "/VirtualDesktopManager", "org.freedesktop.DBus.Properties", _bus())
    reply = props.call("Get", "org.kde.KWin.VirtualDesktopManager", "desktops")
    if reply.type() == QDBusMessage.ErrorMessage:
        return []
    out = []
    try:
        for i, d in enumerate(reply.arguments()[0]):
            pos, ident, name = (list(d) + [None, None, None])[:3]
            out.append({"position": pos if pos is not None else i, "id": str(ident), "name": str(name)})
    except (TypeError, IndexError):
        pass
    return out


def find(space: str) -> dict | None:
    return next((d for d in desktops() if d["name"] == PREFIX + space), None)


def ensure_desktop(space: str) -> str | None:
    """The id of the space's virtual desktop, creating it at the end if needed."""
    if not available():
        return None
    d = find(space)
    if d:
        return d["id"]
    _vdm().call("createDesktop", len(desktops()), PREFIX + space)
    d = find(space)
    return d["id"] if d else None


def switch(space: str) -> None:
    d = find(space)
    if d:
        from PySide6.QtDBus import QDBusInterface, QDBusVariant
        props = QDBusInterface(SERVICE, "/VirtualDesktopManager", "org.freedesktop.DBus.Properties", _bus())
        props.call("Set", "org.kde.KWin.VirtualDesktopManager", "current", QDBusVariant(d["id"]))


def remove_desktop(space: str) -> None:
    d = find(space)
    if d:
        _vdm().call("removeDesktop", d["id"])


SCRIPT = """
const want = %(caption)s, target = %(desktop)s;
const d = workspace.desktops.find(x => x.id === target);
for (const w of workspace.windowList()) {
    if (d && w.caption === want) { w.desktops = [d]; w.fullScreen = true; workspace.currentDesktop = d; }
}
"""


def place(caption: str, space: str) -> bool:
    """Move the window titled `caption` to the space's desktop, full screen."""
    import json
    d = find(space)
    if not d:
        return False
    from PySide6.QtDBus import QDBusInterface
    f = Path(tempfile.gettempdir()) / f"hangar-place-{os.getpid()}.js"
    f.write_text(SCRIPT % {"caption": json.dumps(caption), "desktop": json.dumps(d["id"])})
    scripting = QDBusInterface(SERVICE, "/Scripting", "org.kde.kwin.Scripting", _bus())
    name = f"hangar-place-{os.getpid()}"
    scripting.call("unloadScript", name)
    reply = scripting.call("loadScript", str(f), name)
    try:
        sid = int(reply.arguments()[0])
    except (IndexError, TypeError, ValueError):
        return False
    for path in (f"/Scripting/Script{sid}", f"/{sid}"):          # Plasma 6, then 5
        QDBusInterface(SERVICE, path, "org.kde.kwin.Script", _bus()).call("run")
    scripting.call("unloadScript", name)
    return True
