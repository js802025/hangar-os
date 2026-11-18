"""Local models through LM Studio.

LM Studio runs on the host (the Mac in dev, the Hangar machine in the OS) and
serves an OpenAI-compatible API on port 1234, so every agent shares the same
downloaded models. This wraps LM Studio's `lms` CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

PORT = int(os.environ.get("HANGAR_LMSTUDIO_PORT", "1234"))
HOST_URL = f"http://127.0.0.1:{PORT}"
# How agents reach it from inside their containers (engine maps hangar-host).
AGENT_URL = os.environ.get("HANGAR_LMSTUDIO_AGENT_URL", f"http://hangar-host:{PORT}/v1")


def lms_path() -> str | None:
    return shutil.which("lms") or next(
        (str(p) for p in [Path.home() / ".lmstudio/bin/lms"] if p.exists()), None)


@dataclass
class Model:
    key: str
    name: str
    size_gb: float
    params: str
    vision: bool
    tools: bool
    path: str
    loaded: bool = False


class LMStudio:
    def __init__(self) -> None:
        self.lms = lms_path()

    @property
    def installed(self) -> bool:
        return self.lms is not None

    def _run(self, *args: str, check: bool = True) -> str:
        if not self.lms:
            raise RuntimeError("LM Studio's `lms` CLI not found. Install LM Studio and run it once.")
        r = subprocess.run([self.lms, *args], capture_output=True, text=True)
        if check and r.returncode:
            raise RuntimeError(f"lms {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    # -- server ---------------------------------------------------------------
    def server_up(self) -> bool:
        try:
            return httpx.get(f"{HOST_URL}/v1/models", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure_server(self) -> None:
        """Start LM Studio's API server if it isn't running. On Linux it binds
        to all interfaces so rootless containers can reach it; containers get
        no other way in (the OS firewall keeps 1234 off the LAN)."""
        if self.server_up():
            return
        args = ["server", "start", "--port", str(PORT)]
        if os.uname().sysname == "Linux":
            args += ["--bind", "0.0.0.0"]
        self._run(*args)

    # -- models ---------------------------------------------------------------
    def list(self) -> list[Model]:
        items = json.loads(self._run("ls", "--json", "--llm") or "[]")
        loaded = {m.get("modelKey") for m in json.loads(self._run("ps", "--json", check=False) or "[]")}
        return [Model(key=m["modelKey"], name=m.get("displayName", m["modelKey"]),
                      size_gb=round(m.get("sizeBytes", 0) / 1e9, 1), params=m.get("paramsString", ""),
                      vision=bool(m.get("vision")), tools=bool(m.get("trainedForToolUse")),
                      path=m.get("path", ""), loaded=m["modelKey"] in loaded) for m in items]

    def get(self, key: str) -> Model | None:
        return next((m for m in self.list() if m.key == key or m.path == key), None)

    def download(self, query: str) -> int:
        """Search LM Studio's catalog and download (streams progress to the terminal)."""
        if not self.lms:
            raise RuntimeError("LM Studio's `lms` CLI not found.")
        return subprocess.call([self.lms, "get", query, "-y"])

    def ensure_loaded(self, key: str, context: int = 16384) -> None:
        """Load a model with enough context for agent work (tool schemas and page
        snapshots don't fit LM Studio's small default). Unloads after an idle hour."""
        if any(m.get("modelKey") == key for m in json.loads(self._run("ps", "--json", check=False) or "[]")):
            return
        self._run("load", key, "-c", str(context), "--ttl", "3600", "-y")

    def models_dir(self) -> Path:
        try:
            s = json.loads((Path.home() / ".lmstudio/settings.json").read_text())
            return Path(s.get("downloadsFolder") or Path.home() / ".lmstudio/models")
        except (OSError, ValueError):
            return Path.home() / ".lmstudio/models"

    def remove(self, key: str) -> Path:
        """lms has no remove command, so delete the model's folder."""
        m = self.get(key)
        if not m:
            raise KeyError(f"no downloaded model '{key}'")
        self._run("unload", m.key, check=False)
        root = self.models_dir().resolve()
        target = (root / m.path).resolve()
        if root not in target.parents:
            raise RuntimeError(f"refusing to delete {target}: outside {root}")
        if target.is_file():
            target.unlink()          # one quant file; leave sibling files alone
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            raise RuntimeError(f"can't find {m.key}'s files; remove it in LM Studio's My Models tab")
        return target
