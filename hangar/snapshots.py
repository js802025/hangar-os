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


# -- review: what changed since a snapshot ------------------------------------------------
def _files_from_tar(data: bytes) -> dict[str, bytes]:
    import io
    import tarfile
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            if m.isfile():
                path = m.name.split("/", 1)[1] if "/" in m.name else ""
                if path and not path.startswith(".") and "/." not in path:
                    out[path] = tar.extractfile(m).read()
    return out


def _current(eng: Engine, agent: str) -> dict[str, bytes]:
    stream, _ = eng.client.containers.get(f"hangar-{agent}").get_archive(WORKSPACE)
    return _files_from_tar(b"".join(stream))


def _base(agent: str, sid: str | None) -> tuple[dict, dict[str, bytes]]:
    snaps = list_snapshots(agent)
    if not snaps:
        raise SnapshotError(f"{agent} has no snapshots yet (one is taken before every task)")
    if sid:
        info = next((s for s in snaps if s["id"] == sid), None)
        if not info:
            raise SnapshotError(f"no snapshot {sid}")
    else:   # the workspace as it was before the latest task
        info = next((s for s in snaps if s["label"].startswith("before:")), snaps[0])
    with gzip.open(ROOT / agent / f"{info['id']}.tar.gz", "rb") as f:
        return info, _files_from_tar(f.read())


def changes(eng: Engine, agent: str, sid: str | None = None) -> dict:
    """Files the agent added, changed or deleted since a snapshot, with diffs."""
    import difflib
    info, before = _base(agent, sid)
    after = _current(eng, agent)
    files = []
    for path in sorted(set(before) | set(after)):
        a, b = before.get(path), after.get(path)
        if a == b:
            continue
        status = "added" if a is None else "deleted" if b is None else "modified"
        entry = {"path": path, "status": status, "binary": False, "added": 0, "removed": 0, "diff": []}
        try:
            ta = (a or b"").decode("utf-8").splitlines()
            tb = (b or b"").decode("utf-8").splitlines()
        except UnicodeDecodeError:
            entry["binary"] = True
            files.append(entry)
            continue
        if len(ta) + len(tb) > 20000:
            entry["binary"] = True        # too big to show; still revertable
            files.append(entry)
            continue
        for line in list(difflib.unified_diff(ta, tb, lineterm="", n=3))[2:]:
            kind = "hunk" if line.startswith("@@") else "add" if line.startswith("+") else \
                "del" if line.startswith("-") else "ctx"
            entry["added"] += kind == "add"
            entry["removed"] += kind == "del"
            entry["diff"].append({"kind": kind, "text": line})
        files.append(entry)
    return {"snapshot": info, "files": files}


def revert_file(eng: Engine, agent: str, path: str, sid: str | None = None) -> str:
    """Put one file back as it was in the snapshot (or remove it if it was new)."""
    import io
    import tarfile
    if ".." in path.split("/") or path.startswith("/"):
        raise SnapshotError("bad path")
    _info, before = _base(agent, sid)
    c = eng.client.containers.get(f"hangar-{agent}")
    if path not in before:
        c.exec_run(["rm", "-f", f"{WORKSPACE}/{path}"], user="agent")
        return "removed"
    data = before[path]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        ti = tarfile.TarInfo(f"workspace/{path}")
        ti.size, ti.mode, ti.uid, ti.gid = len(data), 0o644, 1000, 1000
        tar.addfile(ti, io.BytesIO(data))
    c.put_archive("/home/agent", buf.getvalue())
    return "restored"


def export(eng: Engine, agent: str, dest) -> None:
    """Copy the workspace to a folder on this computer (for opening in an editor)."""
    import io
    import tarfile
    from pathlib import Path
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    stream, _ = eng.client.containers.get(f"hangar-{agent}").get_archive(WORKSPACE)
    with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
        for m in tar.getmembers():
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if not rel or ".." in rel.split("/") or not (m.isfile() or m.isdir()):
                continue
            target = dest / rel
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(m).read())
