"""Notes: a folder of Markdown files (~/Notes), the same format Obsidian uses,
so you can open the folder in Obsidian or any editor too.

Agents get notes tools, but they only see what you allow (Settings → Notes):
  own   (default) each space reads and writes only Notes/Agents/<space>/
  read  agents can also search and read your whole vault (still write only their folder)
  off   no notes for agents
The pilot can always read every space's folder under Notes/Agents/.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from hangar import auth

ROOT = Path(os.environ.get("HANGAR_NOTES_DIR", Path.home() / "Notes"))
AGENTS = "Agents"
CONFIG = auth.STATE_DIR / "notes.json"
ACCESS = ("own", "read", "off")
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
TAG = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")
MAX_BYTES = 2 * 2**20


class NotesError(ValueError):
    pass


def root() -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT.resolve()         # one spelling, even if ~/Notes is a symlink


def access() -> str:
    try:
        v = json.loads(CONFIG.read_text()).get("agents", "own")
    except (OSError, ValueError):
        v = "own"
    return v if v in ACCESS else "own"


def set_access(v: str) -> None:
    if v not in ACCESS:
        raise NotesError(f"access is one of {', '.join(ACCESS)}")
    auth.write_private(CONFIG, json.dumps({"agents": v}))


def _clean(path: str) -> str:
    """A vault-relative path to a .md file, or NotesError."""
    p = path.strip().strip("/").replace("\\", "/")
    if not p or any(part in ("", ".", "..") or part.startswith(".") for part in p.split("/")):
        raise NotesError("bad note path")
    return p if p.endswith(".md") else p + ".md"


def _file(path: str) -> Path:
    f = (root() / _clean(path)).resolve()
    if root() not in f.parents:
        raise NotesError("bad note path")
    return f


def title_of(path: str) -> str:
    return Path(path).stem


def _all() -> list[Path]:
    r = root()
    return sorted((f for f in r.rglob("*.md") if not any(p.startswith(".") for p in f.relative_to(r).parts)),
                  key=lambda f: f.stat().st_mtime, reverse=True)


def info(f: Path, text: str | None = None) -> dict:
    rel = f.relative_to(root()).as_posix()
    if text is None:
        text = f.read_text(errors="replace")
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")), "")
    return {"path": rel, "title": title_of(rel), "modified": f.stat().st_mtime, "preview": first[:160],
            "tags": sorted(set(TAG.findall(body))), "agent": rel.startswith(AGENTS + "/")}


def listing(prefix: str = "") -> list[dict]:
    out = []
    for f in _all():
        rel = f.relative_to(root()).as_posix()
        if not prefix or rel.startswith(prefix):
            out.append(info(f))
    return out


def read(path: str) -> dict:
    f = _file(path)
    if not f.exists():
        raise NotesError(f"no note {path}")
    text = f.read_text(errors="replace")
    return {**info(f, text), "text": text, "links": sorted(set(LINK.findall(text))), "backlinks": backlinks(path)}


def write(path: str, text: str, append: bool = False) -> dict:
    if len(text.encode()) > MAX_BYTES:
        raise NotesError("that note is too big")
    f = _file(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    if append and f.exists():
        old = f.read_text(errors="replace")
        text = old + ("" if old.endswith("\n") else "\n") + text
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(f)
    return info(f, text)


def delete(path: str) -> None:
    """Move a note to .trash in the vault (like Obsidian), never a hard delete."""
    f = _file(path)
    if not f.exists():
        raise NotesError(f"no note {path}")
    dest = root() / ".trash" / f"{time.strftime('%Y%m%d-%H%M%S')}-{f.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    f.replace(dest)


def rename(path: str, new: str) -> dict:
    """Rename a note and rewrite [[links]] to it across the vault."""
    f, g = _file(path), _file(new)
    if not f.exists():
        raise NotesError(f"no note {path}")
    if g.exists():
        raise NotesError(f"{new} already exists")
    g.parent.mkdir(parents=True, exist_ok=True)
    f.replace(g)
    old_t, new_t = title_of(_clean(path)), title_of(_clean(new))
    pat = re.compile(r"\[\[" + re.escape(old_t) + r"(?=[\]|#])")
    for other in _all():
        t = other.read_text(errors="replace")
        if pat.search(t):
            other.write_text(pat.sub("[[" + new_t, t))
    return info(g)


def backlinks(path: str) -> list[dict]:
    t = title_of(_clean(path)).lower()
    out = []
    for f in _all():
        text = f.read_text(errors="replace")
        if any(x.strip().lower() == t or x.strip().lower().endswith("/" + t) for x in LINK.findall(text)):
            out.append(info(f, text))
    return out


def resolve(title: str) -> str | None:
    """The path of the note a [[title]] link points at."""
    t = title.strip().lower()
    for f in _all():
        rel = f.relative_to(root()).as_posix()
        if rel[:-3].lower() == t or title_of(rel).lower() == t:
            return rel
    return None


def search(q: str, prefix: str = "", limit: int = 30) -> list[dict]:
    """Notes whose title or text has every word of q, best (title hits, recent) first."""
    words = [w.lower() for w in q.split() if w.strip()]
    hits = []
    for f in _all():
        rel = f.relative_to(root()).as_posix()
        if prefix and not rel.startswith(prefix):
            continue
        text = f.read_text(errors="replace")
        low, tl = text.lower(), title_of(rel).lower()
        if not all(w in low or w in tl for w in words):
            continue
        i = low.find(words[0]) if words else 0
        snippet = text[max(0, i - 60): i + 140].replace("\n", " ").strip() if i >= 0 else ""
        score = sum(w in tl for w in words) * 10 + sum(low.count(w) for w in words)
        hits.append({**info(f, text), "snippet": snippet, "score": score})
    hits.sort(key=lambda h: (-h["score"], -h["modified"]))
    return hits[:limit]


# -- what an agent may touch ------------------------------------------------------------
def agent_folder(space: str) -> str:
    return f"{AGENTS}/{space}/"


def can_read(space: str, path: str) -> bool:
    a = access()
    if a == "off":
        return False
    p = _clean(path)
    if a == "read" or p.startswith(agent_folder(space)):
        return True
    return space == "pilot" and p.startswith(AGENTS + "/")


def can_write(space: str, path: str) -> bool:
    return access() != "off" and _clean(path).startswith(agent_folder(space))


def agent_scope(space: str) -> str:
    """The prefix an agent's listing and search are limited to ("" = everything)."""
    a = access()
    if a == "read":
        return ""
    return AGENTS + "/" if space == "pilot" else agent_folder(space)
