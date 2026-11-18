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
    warnings: list[str] = []
    try:
        check_shares(spec.shares)
    except ValueError as e:
        raise SetupError(str(e))
    m = spec.model
    if not m:
        return warnings
    harness = m.split(":", 1)[0]
    if harness in ("claude", "codex"):
        accts = Accounts(eng)
        if spec.account:
            try:
                acct = accts.get(spec.account)
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
        spec.account = acct.name
        vols, env = accts.mounts_and_env(acct)
        try:
            vols.update(harness_mod.mount(eng, harness))
        except harness_mod.NotInstalled as e:
            raise HarnessMissing(str(e), harness) from e
        spec.volumes.update(vols)
        spec.env.update(env)
    elif harness == "local":
        key = m.split(":", 1)[1] if ":" in m else ""
        studio = lm.LMStudio()
        if not studio.installed:
            raise SetupError("LM Studio isn't installed (no `lms` CLI). Install it from lmstudio.ai and open it once.")
        model = studio.get(key)
        if not model:
            raise SetupError(f"Model '{key}' isn't downloaded. See `hangar models list`, or "
                             f"`hangar models download {key or '<name>'}`.")
        if not model.vision:
            warnings.append(f"{model.name} can't see images, so this agent can't use screenshots. It can "
                            "still use the browser's text tools. Pick a vision model for desktop work.")
        if not model.tools:
            warnings.append(f"{model.name} wasn't trained for tool use; it may struggle.")
        studio.ensure_server()
        studio.ensure_loaded(model.key)
        spec.model = f"local:{model.key}"
        spec.env.update({"HANGAR_LLM_URL": lm.AGENT_URL, "HANGAR_MODEL_VISION": "1" if model.vision else "0"})
    else:
        raise SetupError(f"Unknown model '{m}'. Use claude, codex or local:<model>.")
    return warnings
