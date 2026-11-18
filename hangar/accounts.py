"""AI accounts.

Each account is a named container volume `hangar-acct-<name>`, labelled with
its provider. It holds that account's CLI login state (or an API key) and is
mounted at /hangar/account only into agents assigned to it. Credentials never
go into images, environment variables or command lines, and Hangar never sees
a password: sign-in happens in the vendor's own CLI login flow.

Providers:
  claude         Claude Pro/Max subscription via `claude auth login`
  codex          ChatGPT subscription via `codex login --device-auth`
  anthropic-key  Anthropic API key (used by the Claude Code harness)
  openai-key     OpenAI API key (used by the Codex harness)
"""

from __future__ import annotations

import io
import re
import shutil
import tarfile
import time
from dataclasses import dataclass

from docker.errors import NotFound

from hangar.engine import BASE_IMAGE, LABEL, Engine

ACCT_LABEL = "dev.hangar.account"
MOUNT = "/hangar/account"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

PROVIDERS = {
    "claude": "Claude subscription (Pro/Max), signs in with Claude Code",
    "codex": "ChatGPT subscription (Plus/Pro), signs in with Codex CLI",
    "anthropic-key": "Anthropic API key",
    "openai-key": "OpenAI API key",
}
HARNESS = {"claude": "claude", "anthropic-key": "claude", "codex": "codex", "openai-key": "codex"}

# Where each CLI keeps its state inside the account volume.
ENV = {
    "claude": {"CLAUDE_CONFIG_DIR": f"{MOUNT}/claude"},
    "codex": {"CODEX_HOME": f"{MOUNT}/codex"},
    "anthropic-key": {},
    "openai-key": {},
}

LOGIN_CMD = {
    "claude": ["bash", "-c", f"mkdir -p {MOUNT}/claude && claude auth login --claudeai"],
    "codex": ["bash", "-c", f"mkdir -p {MOUNT}/codex && codex login --device-auth"],
}
STATUS_CMD = {
    "claude": ["claude", "auth", "status", "--text"],
    "codex": ["codex", "login", "status"],
    "anthropic-key": ["bash", "-c", f"test -s {MOUNT}/api_key && echo 'API key stored' || echo 'no key'"],
    "openai-key": ["bash", "-c", f"test -s {MOUNT}/api_key && echo 'API key stored' || echo 'no key'"],
}


@dataclass
class Account:
    name: str
    provider: str
    created: str

    @property
    def volume(self) -> str:
        return f"hangar-acct-{self.name}"

    @property
    def harness(self) -> str:
        return HARNESS[self.provider]


class Accounts:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or Engine()
        self.client = self.engine.client

    def list(self) -> list[Account]:
        vols = self.client.volumes.list(filters={"label": ACCT_LABEL})
        out = [Account(v.attrs["Labels"][ACCT_LABEL], v.attrs["Labels"].get(f"{ACCT_LABEL}.provider", "?"),
                       (v.attrs.get("CreatedAt") or "")[:19].replace("T", " ")) for v in vols]
        return sorted(out, key=lambda a: a.name)

    def get(self, name: str) -> Account:
        for a in self.list():
            if a.name == name:
                return a
        raise KeyError(f"no account named '{name}'")

    def create(self, name: str, provider: str) -> Account:
        if not NAME_RE.match(name):
            raise ValueError("account name: lowercase letters, digits and dashes, max 31 chars")
        if provider not in PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(PROVIDERS)}")
        if any(a.name == name for a in self.list()):
            raise ValueError(f"account '{name}' already exists")
        self.client.volumes.create(f"hangar-acct-{name}",
                                   labels={ACCT_LABEL: name, f"{ACCT_LABEL}.provider": provider})
        return self.get(name)

    def users(self, name: str) -> list[str]:
        cs = self.client.containers.list(all=True, filters={"label": f"{LABEL}.account={name}"})
        return [c.labels[LABEL] for c in cs]

    def remove(self, name: str) -> None:
        if used := self.users(name):
            raise ValueError(f"account '{name}' is used by: {', '.join(used)}. Destroy those agents first.")
        try:
            self.client.volumes.get(f"hangar-acct-{name}").remove(force=True)
        except NotFound:
            raise KeyError(f"no account named '{name}'")

    # -- sign-in --------------------------------------------------------------
    def mounts_and_env(self, acct: Account) -> tuple[dict, dict]:
        vols = {acct.volume: {"bind": MOUNT, "mode": "rw"}}
        env = {"HANGAR_ACCOUNT": acct.name, "HANGAR_ACCOUNT_PROVIDER": acct.provider, **ENV[acct.provider]}
        return vols, env

    def login_argv(self, acct: Account, harness_volume: str = "") -> list[str]:
        """argv for an interactive, throwaway login container (needs a real TTY,
        so it goes through the docker/podman CLI rather than the API)."""
        if acct.provider not in LOGIN_CMD:
            raise ValueError(f"{acct.provider} accounts use an API key, not a login")
        cli = shutil.which("podman") or shutil.which("docker")
        if not cli:
            raise RuntimeError("needs the docker or podman CLI on PATH")
        from hangar import harness
        env = sum((["-e", f"{k}={v}"] for k, v in ENV[acct.provider].items()), [])
        mounts = ({harness_volume: {"bind": harness.MOUNT.format(acct.harness)}} if harness_volume
                  else harness.mount(self.engine, acct.harness))
        hv = sum((["-v", f"{vol}:{m['bind']}:ro"] for vol, m in mounts.items()), [])
        return [cli, "run", "--rm", "-it", "--name", f"hangar-login-{acct.name}-{int(time.time())}",
                "-v", f"{acct.volume}:{MOUNT}", *hv, *env, "--entrypoint", "", BASE_IMAGE,
                *LOGIN_CMD[acct.provider]]

    def set_api_key(self, acct: Account, key: str) -> None:
        """Write the key into the account volume without it ever appearing in a
        command line or environment: copy it into a stopped helper container."""
        if not acct.provider.endswith("-key"):
            raise ValueError(f"{acct.provider} accounts sign in with a login, not a key")
        data = key.strip().encode()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("api_key")
            info.size, info.mode, info.uid, info.gid = len(data), 0o600, 1000, 1000
            tar.addfile(info, io.BytesIO(data))
        c = self.client.containers.create(BASE_IMAGE, command=["true"], entrypoint=[],
                                          volumes={acct.volume: {"bind": MOUNT, "mode": "rw"}})
        try:
            c.put_archive(MOUNT, buf.getvalue())
        finally:
            c.remove(force=True)

    def status(self, acct: Account) -> str:
        """Ask the vendor CLI whether this account is signed in."""
        from hangar import harness
        vols = {acct.volume: {"bind": MOUNT, "mode": "ro"}}
        if acct.provider in LOGIN_CMD:
            try:
                vols.update(harness.mount(self.engine, acct.harness))
            except harness.NotInstalled as e:
                return str(e)
        out = self.client.containers.run(
            BASE_IMAGE, command=STATUS_CMD[acct.provider], entrypoint=[], remove=True,
            volumes=vols, environment=ENV[acct.provider], stdout=True, stderr=True)
        return out.decode(errors="replace").strip()
