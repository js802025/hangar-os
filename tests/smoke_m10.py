"""M10 smoke test: Ask Hangar (Qt, offscreen) with context, and local voice.

  1. Voice: a spoken question (macOS `say`) is transcribed locally with the
     tiny Whisper model. Skipped if the `voice` extra isn't installed.
  2. The Ask panel sends a question with a screenshot and the page URL
     switched on and the selected text switched off; the pilot (a scripted
     fake model with vision) must receive the screenshot as an image and the
     URL in its task, and must NOT receive the selection; its answer shows up
     in the panel.
Usage:  uv run --extra apps --extra voice python tests/smoke_m10.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="hangar-m10-"))
LLM_PORT = 1297
os.environ.update(HANGAR_STATE_DIR=str(TMP / "state"), HANGAR_CONFIG_DIR=str(TMP / "config"),
                  XDG_DATA_HOME=str(TMP / "data"), HANGAR_DAEMON_PORT="7802", QT_QPA_PLATFORM="offscreen",
                  HANGAR_LLM_URL=f"http://hangar-host:{LLM_PORT}/v1", HANGAR_LLM_VISION="1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from hangar import auth, pilot, voice  # noqa: E402

seen: dict = {}


class FakeModel(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = body["messages"]
        task = next(m["content"] for m in msgs if m["role"] == "user" and isinstance(m["content"], str))
        images = [p for m in msgs if isinstance(m["content"], list) for p in m["content"] if p.get("type") == "image_url"]
        seen.update(task=task, images=len(images))
        answer = (f"I can see your screenshot ({len(images)} image) and the page "
                  f"{'github.com/acme/api/pull/12' if 'github.com/acme/api/pull/12' in task else '(no URL)'}.")
        msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "report", "arguments": json.dumps({"message": answer, "status": "done", "final": True})}}]}
        out = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def wait(seconds: float, until=None) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)
        loop.exec()
        if until and until():
            return
    if until:
        raise TimeoutError("condition not met")


def test_voice() -> None:
    if not voice.available() or not shutil.which("say"):
        print("1. voice: skipped (needs the voice extra and macOS `say`)")
        return
    voice.download("tiny")
    aiff, wav = TMP / "q.aiff", TMP / "q.wav"
    subprocess.run(["say", "-o", str(aiff), "Summarize the open pull requests for me"], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
    text = voice.transcribe(wav, "tiny")
    assert "summarize" in text.lower() and "request" in text.lower(), text   # tiny is rough on names
    print(f"1. voice, transcribed locally: “{text}”")


def main() -> None:
    test_voice()
    llm = ThreadingHTTPServer(("0.0.0.0", LLM_PORT), FakeModel)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    pilot.save({"pilot": {"model": "local:fake"}, "allowed": [{"model": "local:fake"}], "max_agents": 2})
    from hangar.apps import common
    app = common.make_app("ask")
    pid = int((auth.STATE_DIR / "daemon.pid").read_text())
    from hangar.engine import Engine
    eng = Engine()
    eng.destroy("pilot")
    try:
        from hangar.apps.ask import AskPanel
        panel = AskPanel()
        shot = ROOT / "os/files/usr/share/wallpapers/Hangar/contents/screenshot.png"
        ctx = {"app": "Google Chrome", "title": "Fix flaky auth test · Pull Request #12",
               "url": "https://github.com/acme/api/pull/12", "selection": "SECRET-SELECTION-NOT-INCLUDED"}
        panel.open(shot, ctx, {"screenshot", "url"})
        assert panel.chip_for["screenshot"].isChecked() and panel.chip_for["url"].isChecked()
        assert not panel.chip_for["selection"].isChecked() and not panel.chip_for["window"].isChecked()
        panel.input.setPlainText("Why is CI failing here?")
        panel.send()
        wait(300, lambda: panel.answer.isVisible())
        panel.grab().save(str(ROOT / "out/m10-answer.png"))
        print("2. answer in the panel:", panel.answer.text())
        assert seen["images"] == 1, seen
        assert "github.com/acme/api/pull/12" in seen["task"], seen["task"]
        assert "SECRET-SELECTION" not in seen["task"] and "Fix flaky auth" not in seen["task"], seen["task"]
        assert "github.com/acme/api/pull/12" in panel.answer.text()
        print("   the pilot got the screenshot as an image and the URL; switched-off chips weren't sent: ok")
        print("PASS")
    finally:
        eng.destroy("pilot")
        llm.shutdown()
        os.kill(pid, 15)


if __name__ == "__main__":
    main()
