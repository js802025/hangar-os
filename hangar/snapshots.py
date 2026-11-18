"""Workspace snapshots: a copy of an agent's ~/workspace before every task,
so you can roll back whatever the agent did.

Snapshots live on the host (~/.local/state/hangar/snapshots/<agent>/), not in
the agent's container, so an agent can't edit or delete its own history.
Writable shared folders (--share PATH:rw) are not included: they're your
files, on your disk.
"""

from __future__ import annotations

import gzip
import json
import os
import secrets
import shutil
import time
from pathlib import Path

from hangar import auth
from hangar.engine import Engine

ROOT = auth.STATE_DIR / "snapshots"
WORKSPACE = "/home/agent/workspace"
KEEP = int(os.environ.get("HANGAR_SNAPSHOT_KEEP", "20"))
MAX_BYTES = int(float(os.environ.get("HANGAR_SNAPSHOT_MAX_GB", "2")) * 2**30)


class SnapshotError(RuntimeError):
    pass


def _dir(agent: str) -> Path:
    return auth.private_dir(ROOT / agent)


def list_snapshots(agent: str) -> list[dict]:
    out = []
    for meta in sorted((ROOT / agent).glob("*.json"), reverse=True):
        try:
            out.append(json.loads(meta.read_text()))
        except (OSError, ValueError):
            pass
    return out


def take(eng: Engine, agent: str, label: str = "") -> dict:
    """Copy the agent's workspace to the host. Returns the snapshot's info."""
    c = eng.client.containers.get(f"hangar-{agent}")
    code, out = c.exec_run(["du", "-sb", WORKSPACE], user="agent")
    size = int(out.split()[0]) if code == 0 and out.split() else 0
    if size > MAX_BYTES:
        raise SnapshotError(f"workspace is {size / 2**30:.1f} GB, over the {MAX_BYTES / 2**30:.0f} GB snapshot limit "
                            "(set HANGAR_SNAPSHOT_MAX_GB to raise it)")
    sid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    d = _dir(agent)
    path = d / f"{sid}.tar.gz"
    stream, _ = c.get_archive(WORKSPACE)
    with gzip.open(path, "wb", compresslevel=3) as f:
        for chunk in stream:
            f.write(chunk)
    path.chmod(0o600)
    info = {"id": sid, "agent": agent, "label": label[:200], "time": time.time(),
            "bytes": size, "file_bytes": path.stat().st_size}
    auth.write_private(d / f"{sid}.json", json.dumps(info))
    for old in list_snapshots(agent)[KEEP:]:
        _delete(agent, old["id"])
    return info


def rollback(eng: Engine, agent: str, sid: str | None = None) -> dict:
    """Put the workspace back as it was in a snapshot (default: the latest).
    The current workspace is snapshotted first, so a rollback can be undone."""
    snaps = list_snapshots(agent)
    if not snaps:
        raise SnapshotError(f"{agent} has no snapshots")
    target = snaps[0] if sid is None else next((s for s in snaps if s["id"] == sid), None)
    if target is None:
        raise SnapshotError(f"no snapshot {sid} for {agent}")
    path = ROOT / agent / f"{target['id']}.tar.gz"
    c = eng.client.containers.get(f"hangar-{agent}")
    safety = take(eng, agent, f"before rolling back to {target['id']}")
    code, out = c.exec_run(["find", WORKSPACE, "-mindepth", "1", "-delete"], user="agent")
    if code:
        raise SnapshotError(f"couldn't clear the workspace: {out.decode(errors='replace')[-300:]}")
    with gzip.open(path, "rb") as f:
        ok = c.put_archive("/home/agent", f.read())
    if not ok:
        raise SnapshotError("the engine refused the restore")
    return {"restored": target, "undo": safety}


def _delete(agent: str, sid: str) -> None:
    for ext in (".tar.gz", ".json"):
        (ROOT / agent / f"{sid}{ext}").unlink(missing_ok=True)


def forget(agent: str) -> None:
    """Delete all of an agent's snapshots (when the agent is destroyed)."""
    shutil.rmtree(ROOT / agent, ignore_errors=True)
