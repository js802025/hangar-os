"""Tokens and local state.

One random secret lives in Hangar's state dir (mode 0600). Every token is an
HMAC of it, so nothing else needs storing:
  - the admin token, for the CLI and the Spaces UI to talk to the daemon,
  - one token per agent, which the agent uses to post its own reports and
    which guards its noVNC and MCP ports (so agents can't reach each other).
Agents only ever get their own token; the secret never leaves the host.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

STATE_DIR = Path(os.environ.get("HANGAR_STATE_DIR", Path.home() / ".local/state/hangar"))
CONFIG_DIR = Path(os.environ.get("HANGAR_CONFIG_DIR", Path.home() / ".config/hangar"))


def private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def write_private(path: Path, data: str) -> None:
    private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)


def _secret() -> bytes:
    path = STATE_DIR / "secret"
    if not path.exists():
        write_private(path, secrets.token_hex(32))
    return path.read_text().strip().encode()


def _derive(label: str) -> str:
    return hmac.new(_secret(), label.encode(), hashlib.sha256).hexdigest()[:48]


def derive_key(label: str) -> bytes:
    """32 bytes for encryption keys (e.g. the vault's)."""
    return hmac.new(_secret(), f"key:{label}".encode(), hashlib.sha256).digest()


def admin_token() -> str:
    return _derive("admin")


def agent_token(name: str) -> str:
    return _derive(f"agent:{name}")


def check(given: str | None, expected: str) -> bool:
    return bool(given) and hmac.compare_digest(given, expected)
