"""Agent harnesses (Claude Code, Codex CLI), installed only when needed.

They aren't baked into the agent images (together they're over 1 GB). Each
one is installed once into its own volume, `hangar-harness-<name>-<version>`,
the first time you connect an account that uses it (or create an agent that
needs it), and mounted read-only at /opt/harness/<name> in the agents and
sign-in containers that use it. Local-model agents need neither.

Updating installs the new version into a new volume. New agents use it right
away, while running agents keep theirs until they restart, and unused old
versions are cleaned up.
"""

from __future__ import annotations

import json
import re
import shutil
import time

from docker.errors import APIError, NotFound

from hangar import auth
from hangar.engine import BASE_IMAGE, Engine

CACHE = auth.STATE_DIR / "harness-complete.json"

LABEL = "dev.hangar.harness"
HARNESSES = {
    "claude": {"package": "@anthropic-ai/claude-code", "title": "Claude Code"},
    "codex": {"package": "@openai/codex", "title": "Codex CLI"},
}
MOUNT = "/opt/harness/{}"


class NotInstalled(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{HARNESSES[name]['title']} isn't installed yet. Install it with: "
                         f"hangar harness install {name}")
        self.harness = name


def _check(name: str) -> dict:
    if name not in HARNESSES:
        raise ValueError(f"unknown harness '{name}' (choose from: {', '.join(HARNESSES)})")
    return HARNESSES[name]


def installed(eng: Engine) -> list[dict]:
    """Every installed harness version, newest first (only complete installs)."""
    out = []
    for v in eng.client.volumes.list(filters={"label": LABEL}):
        lb = v.attrs.get("Labels") or {}
        if not _complete(eng, v.name):
            continue
        out.append({"harness": lb[LABEL], "version": lb.get(f"{LABEL}.version", "?"), "volume": v.name,
                    "created": float(lb.get(f"{LABEL}.created", "0"))})
    return sorted(out, key=lambda x: -x["created"])


def _complete(eng: Engine, volume: str) -> bool:
    """A finished install leaves a marker file in the volume (labels can't change
    after creation). Checked once per volume, then remembered."""
    try:
        done = set(json.loads(CACHE.read_text()))
    except (OSError, ValueError):
        done = set()
    if volume in done:
        return True
    try:
        eng.client.containers.run(BASE_IMAGE, ["test", "-e", "/h/.hangar-complete"], entrypoint=[],
                                  remove=True, volumes={volume: {"bind": "/h", "mode": "ro"}})
    except Exception:
        return False
    auth.write_private(CACHE, json.dumps(sorted(done | {volume})))
    return True


def current(eng: Engine, name: str) -> dict | None:
    _check(name)
    return next((h for h in installed(eng) if h["harness"] == name), None)


def mount(eng: Engine, name: str) -> dict:
    """Volume mapping for an agent or sign-in container. Raises NotInstalled."""
    h = current(eng, name)
    if not h:
        raise NotInstalled(name)
    return {h["volume"]: {"bind": MOUNT.format(name), "mode": "ro"}}


def latest_version(eng: Engine, name: str) -> str:
    pkg = _check(name)["package"]
    out = eng.client.containers.run(BASE_IMAGE, ["npm", "view", pkg, "version"], entrypoint=[], remove=True)
    v = out.decode().strip().splitlines()[-1]
    if not re.match(r"^[0-9][0-9A-Za-z.\-+]*$", v):
        raise RuntimeError(f"unexpected version from npm: {v!r}")
    return v


def prepare_install(eng: Engine, name: str, version: str = "") -> tuple[str, list[str]]:
    """Create the volume for `name` and return (volume, argv). Running the argv
    (in a terminal or a jobs.Job) installs into it with npm's progress output."""
    pkg = _check(name)["package"]
    version = version or latest_version(eng, name)
    vol = f"hangar-harness-{name}-{version}".replace("+", "_")
    try:
        eng.client.volumes.get(vol).remove()          # a previous, unfinished attempt
    except NotFound:
        pass
    except APIError:
        pass                                           # in use: reinstalling over it is fine
    try:
        done = set(json.loads(CACHE.read_text())) - {vol}
        auth.write_private(CACHE, json.dumps(sorted(done)))
    except (OSError, ValueError):
        pass
    eng.client.volumes.create(vol, labels={LABEL: name, f"{LABEL}.version": version,
                                           f"{LABEL}.created": str(time.time())})
    cli = shutil.which("podman") or shutil.which("docker")
    if not cli:
        raise RuntimeError("needs the docker or podman CLI on PATH")
    script = (f"npm install -g --no-fund --no-audit --prefix /h {pkg}@{version} && "
              "chmod -R a+rX /h && touch /h/.hangar-complete && echo 'Installed.'")
    argv = [cli, "run", "--rm", "-t", "--user", "root", "-v", f"{vol}:/h", "--entrypoint", "",
            BASE_IMAGE, "bash", "-c", script]
    return vol, argv


def install(eng: Engine, name: str, version: str = "", quiet: bool = False) -> dict:
    """Install now (blocking). Returns the installed harness."""
    import subprocess
    vol, argv = prepare_install(eng, name, version)
    if subprocess.call(argv, stdout=subprocess.DEVNULL if quiet else None) != 0:
        raise RuntimeError(f"installing {HARNESSES[name]['title']} failed")
    prune(eng, name)
    return current(eng, name)


def prune(eng: Engine, name: str | None = None) -> list[str]:
    """Remove older versions that no container uses."""
    removed, seen = [], set()
    for h in installed(eng):
        if name and h["harness"] != name:
            continue
        if h["harness"] not in seen:
            seen.add(h["harness"])           # keep the newest of each
            continue
        try:
            eng.client.volumes.get(h["volume"]).remove()
            removed.append(h["volume"])
        except APIError:
            pass                              # still mounted by an agent
    return removed


def remove(eng: Engine, name: str) -> list[str]:
    _check(name)
    removed = []
    for h in installed(eng):
        if h["harness"] == name:
            try:
                eng.client.volumes.get(h["volume"]).remove()
                removed.append(h["volume"])
            except APIError as e:
                raise RuntimeError(f"{h['volume']} is still used by an agent ({e.explanation})")
    return removed
