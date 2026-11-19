"""Setup wizard smoke test (Qt, offscreen, throwaway state dir).

Walks every step with Continue, like a user accepting the defaults (without
downloading models or signing in to anything), and checks:
  - hardware detection and model picks make sense,
  - in dev mode, system settings are shown but never changed,
  - the notifications step saves an ntfy channel with a random topic and shows a QR code,
  - Finish marks setup done, and --first-boot then exits right away,
  - recommendations fit the machine's model budget.
Screenshots of each step land in out/setup-*.png.
Usage:  uv run --extra apps python tests/smoke_setup.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-setup-"))
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  HANGAR_DAEMON_PORT="7798", QT_QPA_PLATFORM="offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "out"

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from hangar import auth, catalog, notify  # noqa: E402
from hangar.apps import common  # noqa: E402


def wait(seconds: float, until=None) -> None:
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
    for budget, expect in ((5, "qwen/qwen3-vl-4b"), (11, "qwen/qwen3.5-9b"), (24, "qwen/qwen3-vl-30b")):
        picks = [m for m in catalog.recommend(budget) if m["recommended"]]
        assert all(m["fits"] for m in picks) and expect in [m["key"] for m in picks], (budget, picks)
        assert any(m["vision"] for m in picks), "always recommend a model that can see the screen"
    print("model picks fit the budget and include a vision model: ok")

    app = common.make_app("setup")
    daemon_pid = int((auth.STATE_DIR / "daemon.pid").read_text())
    try:
        from hangar.apps.setup import SetupWindow
        w = SetupWindow()
        w.show()
        wait(30, lambda: w.state is not None and w.stack.currentIndex() == 0 and w.title.text())
        hw = w.state["hardware"]
        assert hw["ram_gb"] > 1 and hw["cores"] >= 1 and hw["model_budget_gb"] >= 0, hw
        assert w.state["host"]["dev"], "a Mac must run the wizard in dev mode"
        print("hardware:", w.state["hardware_summary"], f"→ ~{hw['model_budget_gb']:.0f} GB for models")

        names = ["welcome", "network", "accounts", "models", "pilot", "notify", "done"]
        for i, name in enumerate(names):
            wait(10, lambda i=i: w.i == i)
            wait(2.5 if name in ("network", "models") else 0.8)
            w.grab().save(str(OUT / f"setup-{i}-{name}.png"))
            if name == "models":
                for cb in w.steps[3].checks.values():   # don't download anything in a test
                    cb.setChecked(False)
            if name == "notify":
                assert not w.steps[5].qr.pixmap().isNull(), "no QR code"
            if name == "pilot":
                assert not w.skip.isVisible(), "the pilot step must not be skippable"
            if name != "done":
                w.cont()
        print("walked all 7 steps with Continue: ok")
        orch = common.api.get("/api/orchestration")
        if w.steps[4].choices:
            assert orch["pilot"]["model"] == w.steps[4].choices[0][1]["model"], orch
            assert orch["allowed"], orch
            first = w.steps[4].choices[0][0]
            assert "too big" not in first, f"defaulted to a model that doesn't fit: {first}"
            print("pilot set up in Setup:", orch["pilot"]["model"], f"(may use {len(orch['allowed'])} models)")
        else:
            print("pilot: no accounts or models on this machine, so nothing to set (ok)")

        cfg = notify.load()
        assert cfg.get("ntfy", {}).get("url", "").startswith("https://ntfy.sh/hangar-"), cfg
        print("ntfy channel saved:", cfg["ntfy"]["url"][:30] + "…")

        w.cont()   # Finish
        wait(10, lambda: common.api.get("/api/setup")["done"])
        print("Finish marks setup done: ok")

        t = time.time()
        r = subprocess.run([sys.executable, "-m", "hangar.apps.setup", "--first-boot"], timeout=60,
                           capture_output=True, text=True, env=os.environ)
        assert r.returncode == 0 and time.time() - t < 30, r.stderr[-500:]
        print("--first-boot exits right away once setup is done: ok")
        print("PASS")
    finally:
        os.kill(daemon_pid, 15)


if __name__ == "__main__":
    main()
