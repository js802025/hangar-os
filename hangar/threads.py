"""Conversations with the pilot.

A thread is one conversation: your messages and the pilot's replies, plus what
happened along the way (its progress, the spaces and agents it started, their
reports, password requests). The Hangar app shows a thread as a chat; a
notification about it opens that chat.

Stored as JSON per thread in ~/.local/state/hangar/threads/. The pilot works on
one thread at a time ("active"); spaces it starts are linked to that thread, so
their reports land in the right conversation later.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone

from hangar import auth

ROOT = auth.STATE_DIR / "threads"
_lock = threading.RLock()
_hub = None
KINDS = {"user", "pilot", "progress", "step", "report", "approval", "error"}


def attach_hub(hub) -> None:
    global _hub
    _hub = hub


def _path(tid: str):
    return auth.private_dir(ROOT) / f"{tid}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(title: str = "") -> dict:
    t = {"id": secrets.token_hex(5), "title": title[:80] or "New chat", "created": _now(), "updated": _now(),
         "items": []}
    save(t)
    return t


def save(t: dict) -> None:
    with _lock:
        auth.write_private(_path(t["id"]), json.dumps(t))


def get(tid: str) -> dict:
    try:
        return json.loads(_path(tid).read_text())
    except (OSError, ValueError):
        raise KeyError(f"no conversation {tid}")


def listing() -> list[dict]:
    out = []
    for p in auth.private_dir(ROOT).glob("*.json"):
        try:
            t = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(t, dict) or "items" not in t:     # spaces.json lives here too
            continue
        last = next((i for i in reversed(t["items"]) if i["kind"] in ("user", "pilot")), None)
        out.append({"id": t["id"], "title": t["title"], "updated": t["updated"],
                    "preview": " ".join((last or {}).get("text", "").replace("**", "").split())[:120], "busy": t["id"] == active()})
    return sorted(out, key=lambda x: x["updated"], reverse=True)


def delete(tid: str) -> None:
    _path(tid).unlink(missing_ok=True)


def add(tid: str, kind: str, text: str, **meta) -> dict | None:
    """Append to a thread and tell the apps."""
    if not tid or kind not in KINDS:
        return None
    with _lock:
        try:
            t = get(tid)
        except KeyError:
            return None
        item = {"id": secrets.token_hex(4), "ts": _now(), "kind": kind, "text": text[:20000], **meta}
        t["items"].append(item)
        t["updated"] = item["ts"]
        if kind == "user" and t["title"] == "New chat":
            t["title"] = text.strip().splitlines()[0][:60] if text.strip() else "New chat"
        save(t)
    if _hub:
        _hub.publish({"type": "thread", "thread": tid, "item": item})
    return item


# -- which thread the pilot is on, and which spaces belong to which thread ----------------
def active() -> str:
    try:
        return (ROOT / "active").read_text().strip()
    except OSError:
        return ""


def set_active(tid: str) -> None:
    auth.write_private(auth.private_dir(ROOT) / "active", tid)


def clear_active() -> None:
    (ROOT / "active").unlink(missing_ok=True)


def _links() -> dict:
    try:
        return json.loads((ROOT / "spaces.json").read_text())
    except (OSError, ValueError):
        return {}


def link_space(space: str, tid: str) -> None:
    with _lock:
        links = _links()
        links[space] = tid
        auth.write_private(auth.private_dir(ROOT) / "spaces.json", json.dumps(links))


def thread_for_space(space: str) -> str:
    return _links().get(space, "")


def history_prompt(t: dict, limit: int = 14) -> str:
    """The conversation so far, for the pilot (harness-independent memory)."""
    lines = []
    for i in t["items"][-limit:]:
        if i["kind"] == "user":
            lines.append(f"User: {i['text']}")
        elif i["kind"] == "pilot":
            lines.append(f"You (pilot): {i['text']}")
        elif i["kind"] in ("step", "report"):
            lines.append(f"[{i['kind']}] {i['text'][:400]}")
    return "\n".join(lines)


def wait_idle(timeout: float = 0) -> None:
    end = time.time() + timeout
    while active() and time.time() < end:
        time.sleep(0.5)
