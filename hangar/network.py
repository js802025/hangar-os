"""Per-agent network allowlists.

An agent created with an allowlist gets:
  hangar-<name>-net   an internal network (no route anywhere),
  hangar-<name>-gw    its gateway (images/gateway): on that network and the
                      normal one, proxying only allowed hosts and forwarding
                      the agent's view/MCP ports to the host's 127.0.0.1.
The allowlist file lives on the host and is mounted read-only into the
gateway, so edits apply immediately and the agent can't change them.
Agents without an allowlist keep open internet access.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from docker.errors import NotFound

from hangar import auth

from hangar.engine import IMAGES  # noqa: E402

GATEWAY_IMAGE = os.environ.get("HANGAR_GATEWAY_IMAGE", IMAGES.format(name="gateway"))
GW_LABEL = "dev.hangar.gateway"
PROXY_PORT = 3128
# What each harness needs to reach its AI provider (added automatically).
MODEL_DOMAINS = {
    "claude": ["anthropic.com", "claude.ai", "claude.com"],
    "codex": ["openai.com", "chatgpt.com"],
    "local": [],
}
DOMAIN_RE = re.compile(r"^(\*|(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*)$")


def clean_domain(d: str) -> str:
    d = d.strip().lower()
    if "://" in d:
        d = urlsplit(d).hostname or ""
    d = d.split("/")[0].removeprefix("*.")
    if not DOMAIN_RE.match(d):
        raise ValueError(f"'{d}' isn't a domain (like github.com)")
    return d


def allow_dir(name: str):
    return auth.private_dir(auth.STATE_DIR / "network" / name)


def implied(model: str) -> list[str]:
    return MODEL_DOMAINS.get(model.split(":", 1)[0], []) if model else []


def read_allow(name: str) -> list[str]:
    try:
        return [ln.strip() for ln in (allow_dir(name) / "allow").read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def write_allow(name: str, domains: list[str]) -> list[str]:
    clean = sorted({clean_domain(d) for d in domains if d.strip()})
    d = allow_dir(name)
    d.chmod(0o755)   # the gateway (another uid) must read it; it holds no secrets
    tmp = d / "allow.tmp"
    tmp.write_text("# Hangar network allowlist (one domain per line, subdomains included)\n" +
                   "".join(f"{x}\n" for x in clean))
    tmp.chmod(0o644)
    tmp.replace(d / "allow")
    return clean


def always_allow(env: dict) -> str:
    """Host services an agent always needs: LM Studio and the daemon."""
    from hangar.engine import DAEMON_PORT
    hosts = {f"hangar-host:{os.environ.get('HANGAR_LMSTUDIO_PORT', '1234')}", f"hangar-host:{DAEMON_PORT}"}
    llm = urlsplit(env.get("HANGAR_LLM_URL", ""))
    if llm.hostname == "hangar-host" and llm.port:
        hosts.add(f"hangar-host:{llm.port}")
    return ",".join(sorted(hosts))


def create(client, name: str, allow: list[str], env: dict, ports: dict) -> dict:
    """Make the internal network and start the gateway. Returns the extra
    container settings for the agent itself."""
    net_name, gw_name = f"hangar-{name}-net", f"hangar-{name}-gw"
    for old in (gw_name,):
        try:
            client.containers.get(old).remove(force=True)
        except NotFound:
            pass
    try:
        client.networks.get(net_name).remove()
    except NotFound:
        pass
    write_allow(name, allow)
    net = client.networks.create(net_name, driver="bridge", internal=True, labels={GW_LABEL: name})
    gw = client.containers.create(
        GATEWAY_IMAGE, name=gw_name, detach=True, labels={GW_LABEL: name},
        environment={"AGENT_HOST": f"hangar-{name}", "ALWAYS_ALLOW": always_allow(env)},
        ports=ports, extra_hosts={"hangar-host": "host-gateway"},
        volumes={str(allow_dir(name)): {"bind": "/etc/hangar", "mode": "ro"}},
        mem_limit="128m", nano_cpus=int(0.5e9), security_opt=["no-new-privileges"], cap_drop=["ALL"])
    net.connect(gw, aliases=["gateway"])
    gw.start()
    proxy = f"http://{gw_name}:{PROXY_PORT}"
    return {"network": net_name,
            "environment": {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "http_proxy": proxy,
                            "https_proxy": proxy, "NO_PROXY": "localhost,127.0.0.1,::1",
                            "no_proxy": "localhost,127.0.0.1,::1"}}


def gateway(client, name: str):
    try:
        return client.containers.get(f"hangar-{name}-gw")
    except NotFound:
        return None


def remove(client, name: str) -> None:
    gw = gateway(client, name)
    if gw:
        gw.remove(force=True)
    try:
        client.networks.get(f"hangar-{name}-net").remove()
    except NotFound:
        pass


def recent(client, name: str, n: int = 200) -> list[dict]:
    """Recent decisions from the gateway's log, newest first."""
    gw = gateway(client, name)
    if not gw:
        return []
    out = []
    for line in gw.logs(tail=n).decode(errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] in ("ALLOW", "BLOCK", "FAIL"):
            out.append({"ts": parts[0], "action": parts[1], "host": parts[2].rsplit(":", 1)[0],
                        "port": parts[2].rsplit(":", 1)[-1], "why": " ".join(parts[3:])})
    return out[::-1]
