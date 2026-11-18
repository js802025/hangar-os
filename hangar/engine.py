"""Container engine: runs agent containers on Docker (Mac dev) or Podman (target).

Both speak the Docker API, so one code path covers them. Each agent is one
container named `hangar-<name>` with:
  - its own hidden X display, browser profile and home volume (nothing shared),
  - noVNC and MCP published only on 127.0.0.1, on ports the engine picks
    (so many agents never conflict), both locked with the agent's own token,
  - `hangar-host` resolving to the host (for LM Studio and the daemon).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker
import httpx
from docker.errors import NotFound

from hangar import auth

LABEL = "dev.hangar.agent"
# Where agent images come from. Dev: built locally (`make images`). The Hangar OS
# sets HANGAR_IMAGES to its registry, e.g. "ghcr.io/you/hangar-{name}:latest",
# and pulls them on first boot (`hangar images pull`).
IMAGES = os.environ.get("HANGAR_IMAGES", "localhost/hangar-{name}:dev")
BASE_IMAGE = os.environ.get("HANGAR_BASE_IMAGE", IMAGES.format(name="base"))
NOVNC_PORT, MCP_PORT = 6080, 8765
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
DAEMON_PORT = int(os.environ.get("HANGAR_DAEMON_PORT", "7700"))
# How agents reach the daemon to post reports.
DAEMON_AGENT_URL = os.environ.get("HANGAR_DAEMON_AGENT_URL", f"http://hangar-host:{DAEMON_PORT}")


def _socket_url() -> str | None:
    """Find the engine socket: $DOCKER_HOST, Docker Desktop, OrbStack, rootless Podman."""
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    candidates = [
        Path.home() / ".docker/run/docker.sock",                  # Docker Desktop (Mac)
        Path.home() / ".orbstack/run/docker.sock",                # OrbStack
        Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent")) / "podman/podman.sock",
        Path("/run/podman/podman.sock"),
        Path("/var/run/docker.sock"),
    ]
    for p in candidates:
        if p.exists():
            return f"unix://{p}"
    return None


# Never shareable: sharing any of these (or a folder containing one) would put
# keys, keyrings or Hangar's own secrets inside an agent.
HOME = Path.home()
SENSITIVE = [HOME / p for p in (".ssh", ".gnupg", ".aws", ".kube", ".docker", ".netrc",
                                ".password-store", ".local/share/keyrings", ".config/hangar",
                                ".local/state/hangar", "Library/Keychains", ".lmstudio")]


def check_shares(shares: list[str]) -> list[tuple[Path, bool]]:
    """Validate `PATH` / `PATH:rw` shares. Read-only unless :rw. Returns
    (resolved path, writable) pairs; raises ValueError with a reason."""
    out, names = [], set()
    for s in shares:
        raw, rw = (s[:-3], True) if s.endswith(":rw") else (s.removesuffix(":ro"), False)
        p = Path(raw).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"shared folder {raw} doesn't exist (or isn't a folder)")
        if p == Path("/") or p == HOME or p in HOME.parents:
            raise ValueError(f"won't share {p}: share a specific folder, not your whole home or disk")
        for sens in SENSITIVE:
            if p == sens or p in sens.parents or sens in p.parents:
                raise ValueError(f"won't share {p}: it is or contains {sens}, which holds secrets")
        if p.name in names:
            raise ValueError(f"two shared folders are named '{p.name}'")
        names.add(p.name)
        out.append((p, rw))
    return out


def image_for(flavor: str) -> str:
    if flavor == "base":
        return BASE_IMAGE
    return os.environ.get(f"HANGAR_{flavor.upper()}_IMAGE", IMAGES.format(name=flavor))


@dataclass
class AgentSpec:
    name: str
    flavor: str = "base"
    model: str = ""       # "claude", "codex", "local:<model>" (used from M3)
    account: str = ""     # account name (M3)
    task: str = ""
    image: str = ""       # overrides the flavor's image
    env: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, dict] = field(default_factory=dict)   # extra mounts (account)
    shares: list[str] = field(default_factory=list)          # host folders: "PATH" or "PATH:rw"
    allow: list[str] | None = None   # None = open internet; a list = only these domains (via a gateway)
    cpus: float = 2.0
    memory: str = "4g"
    screen: str = "1280x800"


@dataclass
class Agent:
    name: str
    container_id: str
    status: str          # docker status: running, exited, ...
    image: str
    novnc_port: int | None
    mcp_port: int | None
    flavor: str = "base"
    model: str = ""
    account: str = ""
    task: str = ""
    created: str = ""
    shares: list[str] = field(default_factory=list)
    network: str = "open"     # "open" or "allowlist"

    # Direct (host-only) addresses. Both need the agent's token; users go
    # through the daemon instead (hangar view / the Spaces UI).
    @property
    def mcp_url(self) -> str | None:
        return f"http://127.0.0.1:{self.mcp_port}/mcp" if self.mcp_port else None

    @property
    def token(self) -> str:
        return auth.agent_token(self.name)

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def vnc_auth_header(self) -> dict[str, str]:
        return {"Authorization": "Basic " + base64.b64encode(f"hangar:{self.token}".encode()).decode()}


class Engine:
    def __init__(self) -> None:
        url = _socket_url()
        self.client = docker.DockerClient(base_url=url) if url else docker.from_env()

    # -- lifecycle ---------------------------------------------------------
    def create(self, spec: AgentSpec) -> Agent:
        if not NAME_RE.match(spec.name):
            raise ValueError("agent name: lowercase letters, digits and dashes, max 31 chars")
        cname = f"hangar-{spec.name}"
        try:
            self.client.containers.get(cname)
            raise ValueError(f"agent '{spec.name}' already exists")
        except NotFound:
            pass
        shares = check_shares(spec.shares)
        labels = {LABEL: spec.name, f"{LABEL}.flavor": spec.flavor, f"{LABEL}.model": spec.model,
                  f"{LABEL}.account": spec.account, f"{LABEL}.task": spec.task[:2000],
                  f"{LABEL}.shares": json.dumps([f"{p}{':rw' if rw else ''}" for p, rw in shares])}
        share_vols = {str(p): {"bind": f"/home/agent/shared/{p.name}", "mode": "rw" if rw else "ro"}
                      for p, rw in shares}
        env = {"HANGAR_AGENT": spec.name, "HANGAR_SCREEN": spec.screen, "HANGAR_FLAVOR": spec.flavor,
               "HANGAR_MODEL": spec.model, "HANGAR_TASK": spec.task,
               "HANGAR_AGENT_TOKEN": auth.agent_token(spec.name), "HANGAR_DAEMON_URL": DAEMON_AGENT_URL,
               **spec.env}
        if spec.task and spec.model:
            env["HANGAR_AGENT_CMD"] = "hangar-agent"   # start working once the desktop is up
        # Empty host port = engine picks a free one. Bound to localhost only.
        ports = {f"{NOVNC_PORT}/tcp": ("127.0.0.1", None), f"{MCP_PORT}/tcp": ("127.0.0.1", None)}
        net_kw: dict = {"ports": ports, "extra_hosts": {"hangar-host": "host-gateway"}}
        if spec.allow is not None:
            from hangar import network
            allow = list(spec.allow) + network.implied(spec.model)
            extra = network.create(self.client, spec.name, allow, env, ports)
            env.update(extra["environment"])
            labels[f"{LABEL}.network"] = "allowlist"
            net_kw = {"network": extra["network"]}   # no published ports: the gateway forwards them
        try:
            c = self.client.containers.run(
                spec.image or image_for(spec.flavor),
                name=cname,
                hostname=spec.name,
                detach=True,
                labels=labels,
                environment=env,
                volumes={f"hangar-{spec.name}-home": {"bind": "/home/agent", "mode": "rw"}, **spec.volumes,
                         **share_vols},
                shm_size="1g",
                nano_cpus=int(spec.cpus * 1e9),
                mem_limit=spec.memory,
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
                **net_kw,
            )
        except Exception:
            if spec.allow is not None:
                from hangar import network
                network.remove(self.client, spec.name)
            raise
        return self.get(spec.name, container=c)

    def get(self, name: str, container=None) -> Agent:
        c = container or self.client.containers.get(f"hangar-{name}")
        c.reload()
        ports = c.attrs["NetworkSettings"]["Ports"] or {}
        net_mode = c.labels.get(f"{LABEL}.network", "open")
        if net_mode == "allowlist":
            from hangar import network
            gw = network.gateway(self.client, c.labels.get(LABEL, name))
            ports = {}
            if gw is not None:
                gw.reload()
                ports = gw.attrs["NetworkSettings"]["Ports"] or {}

        def host_port(p: int) -> int | None:
            b = ports.get(f"{p}/tcp")
            return int(b[0]["HostPort"]) if b else None

        return Agent(
            name=c.labels.get(LABEL, name),
            container_id=c.id[:12],
            status=c.status,
            image=c.attrs["Config"]["Image"],
            novnc_port=host_port(NOVNC_PORT),
            mcp_port=host_port(MCP_PORT),
            flavor=c.labels.get(f"{LABEL}.flavor", "base"),
            model=c.labels.get(f"{LABEL}.model", ""),
            account=c.labels.get(f"{LABEL}.account", ""),
            task=c.labels.get(f"{LABEL}.task", ""),
            created=c.attrs.get("Created", "")[:19].replace("T", " "),
            shares=json.loads(c.labels.get(f"{LABEL}.shares", "[]")),
            network=net_mode,
        )

    def flavors(self) -> list[dict]:
        """Built flavor images (any image labelled dev.hangar.flavor), base first."""
        out = {}
        for img in self.client.images.list(filters={"label": "dev.hangar.flavor"}):
            name = img.labels.get("dev.hangar.flavor")
            tags = [t for t in img.tags if t == image_for(img.labels.get("dev.hangar.flavor", ""))] or img.tags
            if name and tags and (name not in out or tags[0] == image_for(name)):
                out[name] = {"name": name, "description": img.labels.get("dev.hangar.flavor.description", ""),
                             "image": tags[0]}
        return sorted(out.values(), key=lambda f: (f["name"] != "base", f["name"]))

    def list(self) -> list[Agent]:
        cs = self.client.containers.list(all=True, filters={"label": LABEL})
        return [self.get(c.labels[LABEL], container=c) for c in cs]

    def wait_ready(self, name: str, timeout: float = 60) -> Agent:
        deadline = time.time() + timeout
        while time.time() < deadline:
            a = self.get(name)
            if a.status == "exited":
                raise RuntimeError(f"agent '{name}' exited:\n{self.logs(name, tail=40)}")
            if a.mcp_port:
                try:
                    r = httpx.get(f"http://127.0.0.1:{a.mcp_port}/healthz", timeout=2)
                    if r.status_code == 200:
                        return a
                except httpx.HTTPError:
                    pass
            time.sleep(0.5)
        raise TimeoutError(f"agent '{name}' not ready after {timeout}s")

    def logs(self, name: str, tail: int = 200) -> str:
        c = self.client.containers.get(f"hangar-{name}")
        return c.logs(tail=tail).decode(errors="replace")

    def exec(self, name: str, cmd: list[str]) -> tuple[int, str]:
        c = self.client.containers.get(f"hangar-{name}")
        code, out = c.exec_run(cmd, user="agent")
        return code, out.decode(errors="replace")

    # -- takeover: input tools refuse while the paused flag exists ---------
    def pause(self, name: str) -> None:
        code, out = self.exec(name, ["touch", "/home/agent/.hangar/paused"])
        if code:
            raise RuntimeError(out)

    def resume(self, name: str) -> None:
        self.exec(name, ["rm", "-f", "/home/agent/.hangar/paused"])

    def is_paused(self, name: str) -> bool:
        return self.exec(name, ["test", "-e", "/home/agent/.hangar/paused"])[0] == 0

    def read_file(self, name: str, path: str) -> str:
        code, out = self.exec(name, ["cat", path])
        return out if code == 0 else ""

    def run_task(self, name: str, task: str, snapshot: bool = True) -> dict:
        """Start a task in a running agent (in the background). The workspace is
        snapshotted first so the task can be rolled back."""
        if self.runtime(name)["busy"]:
            raise RuntimeError(f"agent '{name}' is already working on a task")
        result = {"snapshot": None, "warning": ""}
        if snapshot:
            from hangar import snapshots
            try:
                result["snapshot"] = snapshots.take(self, name, f"before: {task}")
            except snapshots.SnapshotError as e:
                result["warning"] = f"No snapshot before this task: {e}"
        c = self.client.containers.get(f"hangar-{name}")
        c.exec_run(["bash", "-c", 'exec hangar-agent "$1" >>/home/agent/.hangar/logs/agent.log 2>&1',
                    "_", task], user="agent", detach=True)
        return result

    def runtime(self, name: str) -> dict:
        """Paused flag, current task, last report and whether a harness is
        running, from one exec."""
        script = (r'cd /home/agent/.hangar; test -e paused && echo P=1; '
                  r'pgrep -f "^bash /opt/hangar/bin/hangar-agent" >/dev/null && echo B=1; '
                  r'echo "T=$(base64 -w0 task.txt 2>/dev/null)"; '
                  r'echo "R=$(tail -n1 reports.jsonl 2>/dev/null)"')
        code, out = self.exec(name, ["bash", "-c", script])
        info = {"paused": False, "busy": False, "task": "", "report": None}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            if k == "P":
                info["paused"] = True
            elif k == "B":
                info["busy"] = True
            elif k == "T" and v:
                info["task"] = base64.b64decode(v).decode(errors="replace")
            elif k == "R" and v:
                try:
                    info["report"] = json.loads(v)
                except ValueError:
                    pass
        return info

    def state(self, a: Agent) -> dict:
        """What the user cares about: working / idle / taken over / done / error /
        waiting_on_user / exited, plus the current task and last report."""
        if a.status != "running":
            return {"state": a.status, "task": a.task, "report": None}
        rt = self.runtime(a.name)
        r = rt["report"]
        if rt["paused"]:
            state = "taken over"
        elif rt["busy"]:
            state = "working"
        else:
            state = r["status"] if r else "idle"
        return {"state": state, "task": rt["task"] or a.task, "report": r}

    def stop(self, name: str) -> None:
        from hangar import network
        self.client.containers.get(f"hangar-{name}").stop(timeout=5)
        gw = network.gateway(self.client, name)
        if gw:
            gw.stop(timeout=2)

    def start(self, name: str) -> None:
        from hangar import network
        gw = network.gateway(self.client, name)
        if gw:
            gw.start()
        self.client.containers.get(f"hangar-{name}").start()

    def destroy(self, name: str, keep_home: bool = False) -> None:
        from hangar import network
        try:
            self.client.containers.get(f"hangar-{name}").remove(force=True)
        except NotFound:
            pass
        network.remove(self.client, name)
        if not keep_home:
            try:
                self.client.volumes.get(f"hangar-{name}-home").remove(force=True)
            except NotFound:
                pass
            from hangar import snapshots
            snapshots.forget(name)
