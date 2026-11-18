"""The vault: website logins and 2FA secrets that agents can ask to use.

Agents never get a password. An agent asks (`request_login("github.com")`),
you approve in Spaces or the CLI, and the daemon types the login into that
agent's browser itself, and only if the page really is that site. The model
only hears "filled". Every request, approval and fill goes to an audit log
(without secrets).

Stored encrypted (Fernet) in ~/.local/state/hangar/vault.enc. The key is
derived from Hangar's 0600 secret, so the vault file alone (in a backup, say)
is useless. On the Hangar OS the disk is encrypted too.

Honest limit: once a password is typed into an agent's browser, a determined
agent could read it back out of the page. Approvals per agent and per site,
filling only on the real site, and the audit log are what keep that in check,
so only grant logins to agents you'd trust with them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet, InvalidToken

from hangar import auth

PATH = auth.STATE_DIR / "vault.enc"
AUDIT = auth.STATE_DIR / "vault-audit.jsonl"
SCOPES = {"once": 0, "hour": 3600, "always": None}
DELETED_DAYS = 30


def weak(pw: str) -> bool:
    """Short, or few kinds of characters (Apple flags these as weak too)."""
    kinds = sum(any(f(c) for c in pw) for f in (str.islower, str.isupper, str.isdigit,
                                                 lambda c: not c.isalnum()))
    return len(pw) < 10 or (len(pw) < 16 and kinds < 3)


def normalize_site(s: str) -> str:
    s = (s or "").strip().lower()
    host = urlsplit(s if "://" in s else f"https://{s}").hostname or ""
    return host.removeprefix("www.")


def host_matches(host: str, site: str) -> bool:
    host, site = normalize_site(host), normalize_site(site)
    return bool(site) and (host == site or host.endswith("." + site))


def totp_secret(s: str) -> str:
    """Accept a base32 secret or an otpauth:// URI (what QR codes contain)."""
    s = (s or "").strip()
    if s.startswith("otpauth://"):
        s = parse_qs(urlsplit(s).query).get("secret", [""])[0]
    s = s.replace(" ", "").upper()
    if s:
        base64.b32decode(s + "=" * (-len(s) % 8))   # raises if it's not valid base32
    return s


def totp(secret: str, at: float | None = None, digits: int = 6, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", int((at or time.time()) // step)), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(auth.derive_key("vault")))


class Vault:
    def __init__(self) -> None:
        self.f = _fernet()

    # -- storage ---------------------------------------------------------------
    def load(self) -> dict:
        if not PATH.exists():
            return {"entries": [], "grants": []}
        try:
            return json.loads(self.f.decrypt(PATH.read_bytes()))
        except InvalidToken:
            raise RuntimeError(f"can't decrypt {PATH} (was Hangar's secret replaced?)")

    def save(self, data: dict) -> None:
        auth.private_dir(PATH.parent)
        tmp = PATH.with_suffix(".tmp")
        auth.write_private(tmp, "")
        tmp.write_bytes(self.f.encrypt(json.dumps(data).encode()))
        tmp.replace(PATH)

    # -- entries ---------------------------------------------------------------
    # Like Apple's Passwords: entries have a site, username, password, optional
    # verification-code (TOTP) secret and notes; deleting moves them to
    # Recently Deleted for 30 days.
    @staticmethod
    def public(e: dict, warnings: list[str] | None = None) -> dict:
        return {"id": e["id"], "site": e["site"], "label": e.get("label") or e["site"],
                "username": e.get("username", ""), "has_password": bool(e.get("password")),
                "has_totp": bool(e.get("totp")), "notes": e.get("notes", ""),
                "created": e.get("created", 0), "updated": e.get("updated", 0),
                "deleted": e.get("deleted"), "warnings": warnings or []}

    def _live(self, data: dict) -> list[dict]:
        cutoff = time.time() - DELETED_DAYS * 86400
        data["entries"] = [e for e in data["entries"] if not e.get("deleted") or e["deleted"] > cutoff]
        return [e for e in data["entries"] if not e.get("deleted")]

    def entries(self) -> list[dict]:
        data = self.load()
        live = self._live(data)
        counts: dict[str, int] = {}
        for e in live:
            if e.get("password"):
                counts[e["password"]] = counts.get(e["password"], 0) + 1
        out = []
        for e in data["entries"]:
            w = []
            if not e.get("deleted") and e.get("password"):
                if weak(e["password"]):
                    w.append("weak")
                if counts.get(e["password"], 0) > 1:
                    w.append("reused")
            out.append(self.public(e, w))
        return out

    def get(self, entry_id: str, deleted: bool = False) -> dict:
        for e in self.load()["entries"]:
            if e["id"] == entry_id and (deleted or not e.get("deleted")):
                return e
        raise KeyError(f"no vault entry {entry_id}")

    def add(self, site: str, username: str = "", password: str = "", totp_secret_: str = "",
            label: str = "", notes: str = "") -> dict:
        site = normalize_site(site)
        if not site or "." not in site and site not in ("localhost", "hangar-host"):
            raise ValueError("website should be a domain like github.com")
        if not (password or totp_secret_):
            raise ValueError("add a password, a verification code setup key, or both")
        now = time.time()
        e = {"id": secrets.token_hex(5), "site": site, "label": label.strip() or site,
             "username": username.strip(), "password": password, "totp": totp_secret(totp_secret_),
             "notes": notes, "created": now, "updated": now}
        data = self.load()
        data["entries"].append(e)
        self.save(data)
        audit("added", site=site, label=e["label"])
        return self.public(e)

    def update(self, entry_id: str, **fields) -> dict:
        """Change some fields. None means "leave as is"."""
        data = self.load()
        e = next((x for x in data["entries"] if x["id"] == entry_id and not x.get("deleted")), None)
        if not e:
            raise KeyError(f"no vault entry {entry_id}")
        if fields.get("site") is not None:
            site = normalize_site(fields["site"])
            if not site:
                raise ValueError("website should be a domain like github.com")
            e["site"] = site
        for k in ("label", "username", "password", "notes"):
            if fields.get(k) is not None:
                e[k] = fields[k].strip() if k in ("label", "username") else fields[k]
        if fields.get("totp") is not None:
            e["totp"] = totp_secret(fields["totp"])
        e["label"] = e.get("label") or e["site"]
        e["updated"] = time.time()
        self.save(data)
        audit("edited", site=e["site"], label=e["label"], fields=sorted(k for k, v in fields.items() if v is not None))
        return self.public(e)

    def _set_deleted(self, entry_id: str, when: float | None) -> dict:
        data = self.load()
        e = next((x for x in data["entries"] if x["id"] == entry_id), None)
        if not e:
            raise KeyError(f"no vault entry {entry_id}")
        e["deleted"] = when
        if when:
            data["grants"] = [g for g in data["grants"] if g["entry"] != entry_id]
        self.save(data)
        return e

    def remove(self, entry_id: str) -> None:
        """Move to Recently Deleted (agents lose access right away)."""
        e = self._set_deleted(entry_id, time.time())
        audit("deleted", site=e["site"], label=e["label"])

    def restore(self, entry_id: str) -> None:
        e = self._set_deleted(entry_id, None)
        audit("restored", site=e["site"], label=e["label"])

    def purge(self, entry_id: str) -> None:
        data = self.load()
        e = next((x for x in data["entries"] if x["id"] == entry_id and x.get("deleted")), None)
        if not e:
            raise KeyError("only entries in Recently Deleted can be deleted for good")
        data["entries"].remove(e)
        self.save(data)
        audit("purged", site=e["site"], label=e["label"])

    def secret(self, entry_id: str) -> dict:
        """For you in Spaces (reveal / copy). Never sent to agents."""
        e = self.get(entry_id)
        now = time.time()
        return {"password": e.get("password", ""), "code": totp(e["totp"], now) if e.get("totp") else "",
                "seconds_left": 30 - int(now) % 30}

    def codes(self) -> list[dict]:
        now = time.time()
        return [{"id": e["id"], "label": e["label"], "site": e["site"], "username": e.get("username", ""),
                 "code": totp(e["totp"], now), "seconds_left": 30 - int(now) % 30}
                for e in self._live(self.load()) if e.get("totp")]

    def match(self, site: str) -> list[dict]:
        """Entries usable on `site` (an entry for google.com covers accounts.google.com)."""
        return [self.public(e) for e in self._live(self.load()) if host_matches(site, e["site"])]

    # -- grants: which agent may use which entry without asking ----------------------
    def grants(self) -> list[dict]:
        now = time.time()
        return [g for g in self.load()["grants"] if g["until"] is None or g["until"] > now]

    def grant(self, agent: str, entry_id: str, scope: str) -> None:
        if scope not in SCOPES:
            raise ValueError(f"scope: {' | '.join(SCOPES)}")
        if scope == "once":
            return
        data = self.load()
        data["grants"] = [g for g in data["grants"]
                          if not (g["agent"] == agent and g["entry"] == entry_id)
                          and (g["until"] is None or g["until"] > time.time())]
        data["grants"].append({"agent": agent, "entry": entry_id,
                               "until": None if SCOPES[scope] is None else time.time() + SCOPES[scope]})
        self.save(data)

    def granted(self, agent: str, entry_id: str) -> bool:
        return any(g["agent"] == agent and g["entry"] == entry_id for g in self.grants())

    def revoke(self, agent: str, entry_id: str) -> None:
        data = self.load()
        data["grants"] = [g for g in data["grants"] if not (g["agent"] == agent and g["entry"] == entry_id)]
        self.save(data)
        audit("revoked", agent=agent, entry=entry_id)


def audit(action: str, **kw) -> None:
    auth.private_dir(AUDIT.parent)
    with AUDIT.open("a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "action": action, **kw}) + "\n")
    AUDIT.chmod(0o600)


def recent_audit(n: int = 50) -> list[dict]:
    try:
        return [json.loads(x) for x in AUDIT.read_text().splitlines()[-n:]][::-1]
    except (OSError, ValueError):
        return []
