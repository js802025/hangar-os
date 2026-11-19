"""Messages between agents.

Rules (enforced here, not just suggested to the agents):
  - agents in the same space can message each other,
  - any agent can message the pilot,
  - only the pilot can message agents in other spaces (so related work in
    different spaces is coordinated by it, and a stray agent can't reach
    across).
Unread messages wait in memory per recipient; every message is also logged per
space (~/.local/state/hangar/messages/<space>.jsonl) for the Hangar app.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from hangar import auth

ROOT = auth.STATE_DIR / "messages"
PILOT = "pilot"


class Bus:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.unread: dict[str, list[dict]] = {}
        self.events: dict[str, asyncio.Event] = {}

    def _event(self, key: str) -> asyncio.Event:
        return self.events.setdefault(key, asyncio.Event())

    def send(self, frm_space: str, frm: str, to_space: str, to: str, text: str) -> dict:
        m = {"ts": datetime.now(timezone.utc).isoformat(), "from": frm if frm_space == to_space else f"{frm_space}/{frm}",
             "from_space": frm_space, "from_agent": frm, "to_space": to_space, "to": to, "text": text[:8000]}
        key = f"{to_space}/{to}"
        self.unread.setdefault(key, []).append(m)
        self._event(key).set()
        auth.private_dir(ROOT)
        for space in {frm_space, to_space}:
            with (ROOT / f"{space}.jsonl").open("a") as f:
                f.write(json.dumps(m) + "\n")
        if self.hub:
            self.hub.publish({"type": "message", **m})
        return m

    async def take(self, space: str, agent: str, wait: float = 0) -> list[dict]:
        key = f"{space}/{agent}"
        if not self.unread.get(key) and wait > 0:
            ev = self._event(key)
            ev.clear()
            try:
                await asyncio.wait_for(ev.wait(), wait)
            except asyncio.TimeoutError:
                pass
        return self.unread.pop(key, [])

    @staticmethod
    def history(space: str, n: int = 200) -> list[dict]:
        try:
            return [json.loads(x) for x in (ROOT / f"{space}.jsonl").read_text().splitlines()[-n:]]
        except (OSError, ValueError):
            return []


def route(frm_space: str, to: str, roster_names: list[str]) -> tuple[str, str]:
    """Where a message from an agent in `frm_space` addressed to `to` goes:
    (space, agent). Raises PermissionError for anything the rules forbid."""
    to = to.strip().lower()
    if to in (PILOT, f"{PILOT}/{PILOT}"):
        return PILOT, PILOT
    if "/" in to:
        space, agent = to.split("/", 1)
        if frm_space != PILOT and space != frm_space:
            raise PermissionError("you can only message your own space's agents or the pilot; ask the "
                                  "pilot to pass it on")
        return space, agent
    if to not in roster_names:
        raise PermissionError(f"no teammate called '{to}' here (teammates: {', '.join(roster_names) or 'none'}; "
                              "or message the pilot)")
    return frm_space, to
