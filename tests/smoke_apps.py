"""Native apps smoke test (Qt, offscreen): every app opens against a test
daemon and shows real data, the native VNC viewer decodes the agent's desktop
and, with control on, moves the agent's real mouse; the overview shows tiles.
Screenshots of each app land in out/apps-*.png.
Usage:  uv run --extra apps python tests/smoke_apps.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-apps-"))
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT="7797", QT_QPA_PLATFORM="offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out"

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from hangar import auth  # noqa: E402
from hangar.apps import common  # noqa: E402
from hangar.engine import AgentSpec, Engine  # noqa: E402
from hangar.vault import Vault  # noqa: E402


def wait(app, seconds: float, until=None) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
        if until and until():
            return
    if until:
        raise TimeoutError("condition not met")


def main() -> None:
    Vault().add("github.com", "me@example.com", "xocdig-fipQy7-mytbab", "JBSWY3DPEHPK3PXP", label="GitHub")
    eng = Engine()
    eng.destroy("apps-a")
    eng.create(AgentSpec(name="apps-a"))
    eng.wait_ready("apps-a", timeout=120)
    app = common.make_app("spaces")
    daemon_pid = (auth.STATE_DIR / "daemon.pid").read_text().strip()
    try:
        from hangar.apps.manage import AccountsWindow, ModelsWindow, SettingsWindow
        from hangar.apps.passwords import PasswordsWindow
        from hangar.apps.spaces import Overview, SpacesWindow

        w = SpacesWindow()
        w.resize(1280, 800)
        w.show()
        wait(app, 20, lambda: "apps-a" in w.grid.cards)
        wait(app, 20, lambda: not w.grid.cards["apps-a"].thumb.pixmap().isNull())
        w.grab().save(str(OUT / "apps-spaces.png"))
        print("Spaces: agent card with a live thumbnail: ok")

        w.open_agent("apps-a")
        view = w.page.view
        wait(app, 30, lambda: view.img is not None)
        assert view.img.width() == 1280, view.img.size()
        w.grab().save(str(OUT / "apps-agent.png"))
        print("agent page: native VNC frame", view.img.width(), "x", view.img.height(), ": ok")

        # Take over through the app, then move the agent's real mouse.
        w.page.take_over()
        wait(app, 15, lambda: view.control)
        import struct
        view.rfb.send(struct.pack(">BBHH", 5, 0, 321, 234))
        wait(app, 10, lambda: "x:321 y:234" in eng.exec("apps-a", ["xdotool", "getmouselocation"])[1])
        w.page.release()
        wait(app, 15, lambda: not view.control)
        print("take over: native viewer drives the agent's mouse, release hands it back: ok")

        ov = Overview(w)
        ov.show_overview()
        ov.setGeometry(0, 0, 1440, 900)
        ov.build()
        ov.tick()
        wait(app, 15, lambda: not ov.tiles["apps-a"][0].pixmap().isNull())
        ov.grab().save(str(OUT / "apps-overview.png"))
        ov.hide_overview()
        print("overview (Spaces key): live tile per agent: ok")

        pw = PasswordsWindow()
        pw.resize(1180, 760)
        pw.show()
        wait(app, 15, lambda: pw.data is not None and pw.items.count() == 1)
        wait(app, 5, lambda: any(lbl.text().replace(" ", "").isdigit() for k, ls in pw.code_labels.items()
                                 if k != "_ring" for lbl in ls))
        pw.grab().save(str(OUT / "apps-passwords.png"))
        print("Passwords: entry listed, live verification code shown: ok")

        for cls, name, ready in ((AccountsWindow, "accounts", "Your accounts"),
                                 (ModelsWindow, "models", "LM Studio"),
                                 (SettingsWindow, "settings", "Notifications")):
            win = cls()
            win.resize(980, 720)
            win.show()
            from PySide6.QtWidgets import QLabel
            wait(app, 30, lambda win=win: any(ready in lbl.text() for lbl in win.findChildren(QLabel)))
            win.grab().save(str(OUT / f"apps-{name}.png"))
            print(f"{name.capitalize()}: loaded: ok")
        w.close()
        print("PASS")
    finally:
        eng.destroy("apps-a")
        os.kill(int(daemon_pid), 15)


if __name__ == "__main__":
    main()
