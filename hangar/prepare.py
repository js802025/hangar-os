"""Turn "what the user asked for" into a ready AgentSpec, for the CLI and the UI.

Resolves --model/--account into account mounts and env, checks and loads
local models, and validates shared folders. Problems raise SetupError with a
message meant for the user; soft problems come back as warnings.
"""

from __future__ import annotations

from hangar import harness as harness_mod
from hangar import models as lm
from hangar.accounts import Accounts
from hangar.engine import AgentSpec, Engine, check_shares


class SetupError(Exception):
    pass


class HarnessMissing(SetupError):
    """The agent's harness (Claude Code / Codex) isn't installed yet."""

    def __init__(self, msg: str, harness: str) -> None:
        super().__init__(msg)
        self.harness = harness


def prepare(eng: Engine, spec: AgentSpec) -> list[str]:
    """Check shares, then resolve the first agent's model and account into mounts and env."""
    try:
        check_shares(spec.shares)
    except ValueError as e:
        raise SetupError(str(e))
    if not spec.model:
        return []
    r = resolve_model(eng, spec.model, spec.account)
    spec.model, spec.account = r["model"], r["account"]
    spec.volumes.update(r["volumes"])
    spec.env.update(r["env"])
    return r["warnings"]


def resolve_model(eng: Engine, model: str, account: str = "", account_base: str = "") -> dict:
    """What an agent with this model needs: {model, account, volumes, env, warnings}.
    account_base: where its account is mounted (the first agent of a space uses
    /hangar/account; others /hangar/accounts/<name>)."""
    from hangar.accounts import MOUNT, env_for
    warnings: list[str] = []
    harness = model.split(":", 1)[0]
    out = {"model": model, "account": "", "volumes": {}, "env": {}, "warnings": warnings}
    if harness in ("claude", "codex"):
        accts = Accounts(eng)
        if account:
            try:
                acct = accts.get(account)
            except KeyError as e:
                raise SetupError(f"{e}. See `hangar accounts list`.")
            if acct.harness != harness:
                raise SetupError(f"Account '{acct.name}' is {acct.provider}; it can't run {harness}.")
        else:
            matches = [a for a in accts.list() if a.harness == harness]
            if len(matches) != 1:
                raise SetupError(f"{'No' if not matches else 'Several'} {harness} accounts; pick one with "
                                 f"--account. Add one: hangar accounts add <name> --provider {harness}")
            acct = matches[0]
        base = account_base or MOUNT
        out["account"] = acct.name
        out["volumes"] = {acct.volume: {"bind": base, "mode": "rw"}}
        out["env"] = env_for(acct, base)
        try:
            out["volumes"].update(harness_mod.mount(eng, harness))
        except harness_mod.NotInstalled as e:
            raise HarnessMissing(str(e), harness) from e
    elif harness == "local":
        key = model.split(":", 1)[1] if ":" in model else ""
        import os
        if os.environ.get("HANGAR_LLM_URL"):
            # Another OpenAI-compatible server instead of LM Studio (Ollama, vLLM, …): trust the name.
            out["env"] = {"HANGAR_LLM_URL": os.environ["HANGAR_LLM_URL"],
                          "HANGAR_MODEL_VISION": os.environ.get("HANGAR_LLM_VISION", "1")}
            _with_pi(eng, out)
            return out
        studio = lm.LMStudio()
        if not studio.installed:
            raise SetupError("LM Studio isn't installed (no `lms` CLI). Install it from lmstudio.ai and open it once.")
        m = studio.get(key)
        if not m:
            raise SetupError(f"Model '{key}' isn't downloaded. See `hangar models list`, or "
                             f"`hangar models download {key or '<name>'}`.")
        if not m.vision:
            warnings.append(f"{m.name} can't see images, so this agent can't use screenshots. It can "
                            "still use the browser's text tools. Pick a vision model for desktop work.")
        if not m.tools:
            warnings.append(f"{m.name} wasn't trained for tool use; it may struggle.")
        studio.ensure_server()
        studio.ensure_loaded(m.key)
        out["model"] = f"local:{m.key}"
        out["env"] = {"HANGAR_LLM_URL": lm.AGENT_URL, "HANGAR_MODEL_VISION": "1" if m.vision else "0"}
        _with_pi(eng, out)
    else:
        raise SetupError(f"Unknown model '{model}'. Use claude, codex or local:<model>.")
    return out


def _with_pi(eng, out: dict) -> None:
    """Local models run on pi (the harness Aside's browser agent is built on) when it's
    installed (`hangar harness install pi`); otherwise on Hangar's built-in loop."""
    try:
        out.setdefault("volumes", {}).update(harness_mod.mount(eng, "pi"))
    except harness_mod.NotInstalled:
        pass
