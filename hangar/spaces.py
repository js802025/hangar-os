"""Who's in each space.

A space is one container: one desktop, browser, workspace. It can hold several
agents, each with its own name, model, account and role, and each with its own
mouse, keyboard and browser tab inside. The first agent is named after the
space, so a space created the classic way (`hangar new scout`) is a team of one.

The roster lives on the host (~/.local/state/hangar/spaces/<space>.json); it
holds no secrets (accounts are referenced by name).
"""

from __future__ import annotations

import json
import time

from hangar import auth
from hangar.engine import NAME_RE, Engine

ROOT = auth.STATE_DIR / "spaces"
EXTRA_ACCOUNTS = "/hangar/accounts/{}"
# Each agent in a space gets its own cursor colour (shown in the live view with
# its name), and agents added without a name get a friendly one.
PALETTE = ["#7c9cff", "#f472b6", "#4ade80", "#fbbf24", "#38bdf8", "#a78bfa", "#fb923c", "#2dd4bf"]
NAMES = ["wren", "juno", "milo", "nova", "otto", "iris", "finn", "cleo", "dex", "pip", "sol", "tess",
         "bram", "lux", "kit", "rhea", "gus", "ada", "zed", "maya"]


def next_color(agents: list[dict]) -> str:
    used = {a.get("color") for a in agents}
    return next((c for c in PALETTE if c not in used), PALETTE[len(agents) % len(PALETTE)])


def pick_name(agents: list[dict], space: str = "") -> str:
    taken = {a["name"] for a in agents} | {space}
    name = next((n for n in NAMES if n not in taken), "")
    if not name:
        i = 2
        while f"agent-{i}" in taken:
            i += 1
        name = f"agent-{i}"
    return name


def _path(space: str):
    return auth.private_dir(ROOT) / f"{space}.json"


def roster(eng: Engine, space: str) -> list[dict]:
    try:
        return json.loads(_path(space).read_text())["agents"]
    except (OSError, ValueError, KeyError):
        a = eng.get(space)     # older space: a team of one
        return [{"name": space, "model": a.model, "account": a.account, "role": "", "env": {}, "created": 0,
                 "color": PALETTE[0]}]


def save(space: str, agents: list[dict]) -> None:
    auth.write_private(_path(space), json.dumps({"agents": agents}, indent=2))


def forget(space: str) -> None:
    _path(space).unlink(missing_ok=True)


def record_first(space: str, model: str, account: str) -> None:
    save(space, [{"name": space, "model": model, "account": account, "role": "", "env": {},
                  "created": time.time(), "color": PALETTE[0]}])


def agent_env(eng: Engine, space: str, name: str) -> dict:
    """Environment for running one agent's harness (no secrets: accounts are files)."""
    a = next((x for x in roster(eng, space) if x["name"] == name), None)
    if a is None:
        raise KeyError(f"no agent '{name}' in space '{space}'")
    return {"HANGAR_AGENT_NAME": name, "HANGAR_MODEL": a["model"], "HANGAR_ROLE": a.get("role", ""),
            **(a.get("env") or {})}


def add_agent(eng: Engine, space: str, name: str = "", model: str = "", account: str = "", role: str = "") -> dict:
    """Add an agent to a running space. If its account or harness isn't mounted in
    the space yet, the container is recreated with it (only when nobody's mid-task)."""
    from hangar.accounts import MOUNT, Accounts, env_for
    from hangar.prepare import resolve_model
    agents = roster(eng, space)
    name = (name or pick_name(agents, space)).strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("agent name: lowercase letters, digits and dashes, max 31 chars")
    if any(x["name"] == name for x in agents):
        raise ValueError(f"'{space}' already has an agent called {name}")
    entry = {"name": name, "model": model, "account": "", "role": role[:300], "env": {}, "created": time.time(),
             "color": next_color(agents)}
    need: dict = {}
    if model:
        r = resolve_model(eng, model, account)
        entry["model"] = r["model"]
        env = {k: v for k, v in r["env"].items() if not k.startswith("HANGAR_ACCOUNT")}
        if r["account"]:
            acct = Accounts(eng).get(r["account"])
            primary = agents[0].get("account") if agents else ""
            base = MOUNT if acct.name == primary else EXTRA_ACCOUNTS.format(acct.name)
            entry["account"] = acct.name
            env = {k: v for k, v in r["env"].items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
                   and not k.startswith("HANGAR_ACCOUNT")}
            env.update(env_for(acct, base))
            r["volumes"] = {**{v: m for v, m in r["volumes"].items() if v != acct.volume},
                            acct.volume: {"bind": base, "mode": "rw"}}
        entry["env"] = env
        need = r["volumes"]
    mounted = {m.get("Name") or m.get("Source"): m["Destination"]
               for m in eng.client.containers.get(f"hangar-{space}").attrs.get("Mounts", [])}
    missing = {v: m for v, m in need.items() if mounted.get(v) != m["bind"]}
    if missing:
        rt = eng.runtime(space)
        if any(x["busy"] for x in rt.get("agents", {}).values()) or rt["busy"]:
            raise RuntimeError(f"{name} needs {', '.join(sorted(missing))} in the space, which means restarting "
                               "its desktop, but an agent there is mid-task. Try again when it's done.")
        eng.recreate(space, missing)
    agents.append(entry)
    save(space, agents)
    return entry


def remove_agent(eng: Engine, space: str, name: str) -> None:
    agents = roster(eng, space)
    if agents and agents[0]["name"] == name:
        raise ValueError(f"{name} is the space's first agent; delete the space instead")
    if not any(x["name"] == name for x in agents):
        raise KeyError(f"no agent '{name}' in space '{space}'")
    eng.exec(space, ["pkill", "-f", f"hangar-agent --agent {name}( |$)"])
    save(space, [x for x in agents if x["name"] != name])
