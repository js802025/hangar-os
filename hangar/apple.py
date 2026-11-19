"""Apple's `container` as Hangar's container engine (Hangar for Mac only).

Hangar OS runs agents on Podman (Docker on a dev Mac) through the Docker SDK.
Hangar for Mac runs them on Apple's container (github.com/apple/container):
every container is its own lightweight Linux VM, and no Docker Desktop is
needed. This module is a small stand-in for the parts of the Docker SDK that
Hangar uses (containers, volumes, images, networks, events), driven through
the `container` CLI, so the rest of Hangar doesn't change. Engine picks it only
when HANGAR_RUNTIME=apple, which the Mac app sets; nothing on Hangar OS
imports it.

Where Apple's container differs from Docker, and what this does about it:
  - No random host ports: picks free 127.0.0.1 ports itself.
  - No --add-host or container DNS on custom networks: writes /etc/hosts
    entries (hangar-host, and the other containers on a space's network)
    into each container after it starts.
  - Containers can't reach the host's 127.0.0.1: the daemon relays its own
    port and LM Studio's onto the vmnet gateway address (see relay()).
  - Named volumes start empty and owned by root: the first time one is
    mounted, it's seeded from the image's folder, as Docker does.
  - No network connect after create: a created container is only started
    (and so really created) at start(), with every network it was given.
  - No --security-opt: each container is its own VM, with all
    capabilities dropped as on Hangar OS.
  - No event stream: events() polls.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from docker.errors import APIError, ContainerError, ImageNotFound, NotFound

HOSTS_MARK = "# hangar"
_seeded_lock = threading.Lock()


def cli() -> str:
    exe = os.environ.get("HANGAR_CONTAINER_CLI") or shutil.which("container")
    if not exe:
        for p in ("/opt/homebrew/bin/container", "/usr/local/bin/container"):
            if Path(p).exists():
                return p
        raise RuntimeError("Apple's container isn't installed (brew install container)")
    return exe


def _run(args: list[str], input: bytes | None = None, timeout: float = 600, check: bool = True,
         ) -> subprocess.CompletedProcess:
    p = subprocess.run([cli(), *args], input=input, capture_output=True, timeout=timeout)
    if check and p.returncode != 0:
        msg = _clean(p.stderr or p.stdout)
        if "not found" in msg.lower():
            if args[:1] == ["image"] or "image" in msg.lower() and args[:1] == ["run"]:
                raise ImageNotFound(msg)
            raise NotFound(msg)
        raise APIError(f"container {' '.join(args[:2])}: {msg}")
    return p


def _json(args: list[str]):
    return json.loads(_run(args, timeout=60).stdout or b"[]")


def _env_file(env: dict | None) -> tuple[list[str], str | None]:
    """Environment goes in a private file, not on the command line (it holds tokens)."""
    if not env:
        return [], None
    fd, path = tempfile.mkstemp(prefix="hangar-env-")
    with os.fdopen(fd, "w") as f:
        for k, v in env.items():
            if "\n" in str(v):
                raise ValueError(f"environment value for {k} has a newline")
            f.write(f"{k}={v}\n")
    return ["--env-file", path], path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _size(v, minimum_mb: int = 1) -> str:
    """Docker sizes (bytes, "4g", "128m") as Apple's "<n>M"."""
    if isinstance(v, str):
        n, unit = v.strip().lower().rstrip("b"), 1
        if n and n[-1] in "kmgt":
            n, unit = n[:-1], 1024 ** "kmgt".index(n[-1]) * 1024
        v = int(float(n) * unit)
    return f"{max(minimum_mb, v // 2**20)}M"


def _clean(msg: bytes) -> str:
    """The error, without the progress lines."""
    return "\n".join(ln for ln in msg.decode(errors="replace").splitlines()
                     if ln.strip() and not ln.lstrip().startswith("[")).strip()


_gateway: str | None = None


def gateway_ip() -> str:
    """The host's address on the default network (vmnet's gateway)."""
    global _gateway
    if not _gateway:
        for n in _json(["network", "ls", "--format", "json"]):
            if n.get("id") == "default":
                _gateway = n.get("status", {}).get("ipv4Gateway")
        _gateway = _gateway or "192.168.64.1"
    return _gateway


def _state_file() -> Path:
    from hangar import auth
    return auth.STATE_DIR / "apple-seeded.json"


def _seeded() -> set[str]:
    try:
        return set(json.loads(_state_file().read_text()))
    except (OSError, ValueError):
        return set()


def _mark_seeded(vol: str) -> None:
    from hangar import auth
    auth.write_private(_state_file(), json.dumps(sorted(_seeded() | {vol})))


def _forget_seeded(vol: str) -> None:
    from hangar import auth
    if vol in (s := _seeded()):
        auth.write_private(_state_file(), json.dumps(sorted(s - {vol})))


# -- containers ---------------------------------------------------------------------------

def _attrs(d: dict) -> dict:
    """Apple's inspect JSON, in the Docker shape Hangar reads."""
    c, st = d["configuration"], d.get("status", {})
    ports = {}
    for p in c.get("publishedPorts") or []:
        ports[f"{p['containerPort']}/{p.get('proto', 'tcp')}"] = [
            {"HostIp": p.get("hostAddress", ""), "HostPort": str(p["hostPort"])}]
    mounts = []
    for m in c.get("mounts") or []:
        kind = next(iter(m.get("type") or {"bind": {}}))
        rw = "ro" not in (m.get("options") or [])
        if kind == "volume":
            mounts.append({"Type": "volume", "Name": m["type"]["volume"].get("name"), "Source": m.get("source"),
                           "Destination": m["destination"], "RW": rw})
        else:
            mounts.append({"Type": "bind", "Source": m.get("source"), "Destination": m["destination"], "RW": rw})
    labels = c.get("labels") or {}
    nets = [n.get("network") for n in c.get("networks") or [] if isinstance(n, dict)]
    res = c.get("resources") or {}
    hosts = json.loads(labels.get("dev.hangar.apple.hosts", "{}"))
    return {
        "Id": d["id"], "Name": d["id"], "Created": c.get("creationDate", ""),
        "Config": {"Image": (c.get("image") or {}).get("reference", ""), "Labels": labels,
                   "Env": (c.get("initProcess") or {}).get("environment") or [], "Hostname": d["id"]},
        "HostConfig": {"PortBindings": ports or None, "ExtraHosts": [f"{k}:{v}" for k, v in hosts.items()],
                       "NetworkMode": next((n for n in nets if n and n != "default"), "default"),
                       "ShmSize": c.get("shmSize"), "NanoCpus": int(res.get("cpus", 2) * 1e9),
                       "Memory": res.get("memoryInBytes"), "SecurityOpt": [], "CapDrop": c.get("capDrop") or []},
        "NetworkSettings": {"Ports": ports,
                            "Networks": {n.get("network"): {"IPAddress": (n.get("ipv4Address") or "").split("/")[0]}
                                         for n in st.get("networks") or []}},
        "Mounts": mounts,
        "State": {"Status": _status(st.get("state", ""))},
    }


def _status(state: str) -> str:
    return {"running": "running", "stopped": "exited", "stopping": "exited"}.get(state, state or "unknown")


class Container:
    def __init__(self, client: Client, data: dict | None = None, pending: dict | None = None) -> None:
        self.client = client
        self.pending = pending           # create()d but not started yet
        if data:
            self._set(data)
        else:
            self.id = self.name = pending["name"]
            self.labels = pending.get("labels") or {}
            self.status = "created"
            self.attrs = {"Config": {"Labels": self.labels, "Image": pending["image"], "Env": []}, "Mounts": [],
                          "HostConfig": {}, "NetworkSettings": {"Ports": {}}}

    def _set(self, d: dict) -> None:
        self.data = d
        self.id = self.name = d["id"]
        self.attrs = _attrs(d)
        self.labels = self.attrs["Config"]["Labels"]
        self.status = self.attrs["State"]["Status"]

    def reload(self) -> None:
        if self.pending is None:
            self._set(self.client.containers._inspect(self.id))

    def ip(self, network: str = "") -> str:
        self.reload()
        nets = self.attrs["NetworkSettings"].get("Networks") or {}
        if network:
            return (nets.get(network) or {}).get("IPAddress", "")
        return next((n["IPAddress"] for n in nets.values() if n.get("IPAddress")), "")

    # lifecycle
    def start(self) -> None:
        if self.pending is not None:
            spec, self.pending = self.pending, None
            self.client.containers._pending.pop(self.id, None)
            new = self.client.containers.run(**{**spec, "detach": True})
            self._set(new.data)
            return
        _run(["start", self.id], timeout=180)
        self.client.containers._after_start(self)

    def stop(self, timeout: int = 10) -> None:
        if self.pending is None:
            _run(["stop", "-t", str(timeout), self.id], timeout=timeout + 60)

    def remove(self, force: bool = False) -> None:
        if self.pending is not None:
            self.client.containers._pending.pop(self.id, None)
            return
        _run(["rm", *(["-f"] if force else []), self.id], timeout=120)

    def logs(self, tail: int | str = "all", **_) -> bytes:
        args = ["logs"] + (["-n", str(tail)] if tail != "all" else []) + [self.id]
        p = _run(args, timeout=60, check=False)
        return p.stdout + p.stderr

    # inside it
    def exec_run(self, cmd, user: str | None = None, detach: bool = False, environment: dict | None = None,
                 stdin: bytes | None = None, **_):
        if isinstance(cmd, str):
            cmd = ["sh", "-c", cmd]
        envargs, path = _env_file(environment)
        args = ["exec", *(["-d"] if detach else []), *(["-i"] if stdin is not None else []),
                *(["-u", user] if user else []), *(["-w", "/"] if user == "root" else []), *envargs, self.id, *cmd]
        try:
            p = _run(args, input=stdin, timeout=600, check=False)
        finally:
            if path:
                os.unlink(path)
        if detach:
            return (None, b"")
        return (p.returncode, p.stdout + p.stderr)

    def get_archive(self, path: str):
        """A tar of `path` (its own name at the top), like Docker's."""
        path = path.rstrip("/") or "/"
        parent, base = os.path.dirname(path) or "/", os.path.basename(path) or "."
        p = _run(["exec", self.id, "tar", "-c", "-C", parent, base], timeout=600, check=False)
        if p.returncode != 0:
            raise NotFound(p.stderr.decode(errors="replace").strip() or f"can't read {path}")
        return iter([p.stdout]), {"name": base}

    def put_archive(self, path: str, data: bytes) -> bool:
        """Unpack a tar into `path` (keeping its owners), in this container or,
        if it isn't running, a throwaway one with the same mounts."""
        self.reload()
        if self.status == "running":
            # As the folder's owner: with every capability dropped (as on Hangar OS),
            # root can't write into the agent's folders, and owners carry over anyway.
            code, out = self.exec_run(["stat", "-c", "%u:%g", path], user="root")
            owner = out.decode().strip() if code == 0 else "agent"
            p = _run(["exec", "-i", "-u", owner, "-w", "/", self.id, "tar", "-x", "-m", "--no-same-owner",
                      "-C", path], input=data, timeout=600, check=False)
            return p.returncode == 0
        spec = self.pending or {}
        vols = spec.get("volumes") or {m.get("Name") or m["Source"]: {"bind": m["Destination"],
                                                                    "mode": "rw" if m["RW"] else "ro"}
                                       for m in self.attrs.get("Mounts", [])}
        image = spec.get("image") or self.attrs["Config"]["Image"]
        args = ["run", "--rm", "-i", "-u", "root", "-w", "/", *self.client.containers._volume_args(image, vols),
                "--entrypoint", "tar", image, "-x", "-C", path]
        return _run(args, input=data, timeout=600, check=False).returncode == 0


class Containers:
    def __init__(self, client: Client) -> None:
        self.client = client
        self._pending: dict[str, Container] = {}

    def _inspect(self, name: str) -> dict:
        p = _run(["inspect", name], timeout=60, check=False)
        data = json.loads(p.stdout or b"[]") if p.returncode == 0 else []
        if not data:
            raise NotFound(f"No such container: {name}")   # Docker's wording
        return data[0]

    def get(self, name: str) -> Container:
        if name in self._pending:
            return self._pending[name]
        return Container(self.client, self._inspect(name))

    def list(self, all: bool = False, filters: dict | None = None) -> list[Container]:
        rows = _json(["ls", *(["-a"] if all else []), "--format", "json"])
        out = []
        want = (filters or {}).get("label")
        for d in rows:
            labels = (d.get("configuration") or {}).get("labels") or {}
            if want:
                k, eq, v = want.partition("=")
                if k not in labels or (eq and labels[k] != v):
                    continue
            out.append(Container(self.client, d))
        return out

    def create(self, image: str, command=None, *, name: str | None = None, **kw) -> Container:
        """Kept in memory until start(), so networks can still be added."""
        name = name or f"hangar-tmp-{os.getpid()}-{int(time.time() * 1000) % 10**8}"
        try:
            self.get(name)
            raise APIError(f"container {name} already exists")
        except NotFound:
            pass
        c = Container(self.client, pending={"image": image, "command": command, "name": name, **kw})
        self._pending[name] = c
        return c

    def _volume_args(self, image: str, volumes: dict | None) -> list[str]:
        args = []
        for src, m in (volumes or {}).items():
            ro = ":ro" if m.get("mode") == "ro" else ""
            if src.startswith("/"):
                args += ["-v", f"{src}:{m['bind']}{ro}"]
            else:
                self.client.volumes._ensure(src, image, m["bind"])
                args += ["-v", f"{src}:{m['bind']}{ro}"]
        return args

    def run(self, image: str, command=None, *, name: str | None = None, detach: bool = False,
            labels: dict | None = None, environment: dict | None = None, volumes: dict | None = None,
            shm_size=None, nano_cpus: int | None = None, mem_limit=None, cap_drop: list | None = None,
            ports: dict | None = None, extra_hosts: dict | None = None, network: str | None = None,
            networks: list | None = None, entrypoint=None, remove: bool = False, user: str | None = None,
            stderr: bool = False, hostname: str | None = None, security_opt=None, **_):
        labels = dict(labels or {})
        if extra_hosts:
            labels["dev.hangar.apple.hosts"] = json.dumps(extra_hosts)
        args = ["run"]
        if detach:
            args.append("-d")
        if remove:
            args.append("--rm")
        if name:
            args += ["--name", name]
        for k, v in labels.items():
            args += ["-l", f"{k}={v}"]
        envargs, envpath = _env_file(environment)
        args += envargs
        args += self._volume_args(image, volumes)
        if shm_size:
            args += ["--shm-size", _size(shm_size)]
        if nano_cpus:
            args += ["-c", str(max(1, math.ceil(nano_cpus / 1e9)))]
        if mem_limit:
            args += ["-m", _size(mem_limit, minimum_mb=256)]      # Apple's VMs need at least 200 MiB
        for cap in cap_drop or []:
            args += ["--cap-drop", cap]
        for spec, bind in (ports or {}).items():
            host = bind[0] if isinstance(bind, tuple) else "127.0.0.1"
            hp = (bind[1] if isinstance(bind, tuple) else bind) or free_port()
            args += ["-p", f"{host}:{hp}:{spec.split('/')[0]}"]
        for net in ([network] if network else []) + list(networks or []):
            args += ["--network", net]
        if user:
            args += ["-u", user]
        cmd = list(command) if isinstance(command, (list, tuple)) else (command.split() if command else [])
        if entrypoint is not None:
            ep = list(entrypoint) if isinstance(entrypoint, (list, tuple)) else ([entrypoint] if entrypoint else [])
            if not ep and cmd:            # Docker's entrypoint=[]: run the command itself
                ep, cmd = cmd[:1], cmd[1:]
            if ep:
                args += ["--entrypoint", ep[0]]
                cmd = ep[1:] + cmd
        args += [image, *cmd]
        try:
            p = _run(args, timeout=600, check=False)
        finally:
            if envpath:
                os.unlink(envpath)
        if p.returncode != 0 and detach:
            msg = _clean(p.stderr)
            if "not found" in msg.lower() and "image" in msg.lower():
                raise ImageNotFound(msg)
            raise APIError(f"container run: {msg}")
        if not detach:
            if p.returncode != 0:
                raise ContainerError(name or image, p.returncode, cmd, image, p.stderr)
            return p.stdout + (p.stderr if stderr else b"")
        c = self.get(name or p.stdout.decode().strip().splitlines()[-1])
        self._after_start(c)
        return c

    def _after_start(self, c: Container) -> None:
        """What Docker's DNS and --add-host would do: /etc/hosts entries."""
        c.reload()
        nets = [n for n in (c.attrs["NetworkSettings"].get("Networks") or {}) if n and n != "default"]
        for net in nets:
            self.sync_hosts(net)
        if not nets:
            self._write_hosts(c, self._extra(c))

    def _extra(self, c: Container) -> dict[str, str]:
        hosts = json.loads(c.labels.get("dev.hangar.apple.hosts", "{}"))
        gw = gateway_ip()
        return {k: (gw if v == "host-gateway" else v) for k, v in hosts.items()}

    def sync_hosts(self, network: str) -> None:
        """Every running container on `network` can find the others by name."""
        members = [c for c in self.list() if network in (c.attrs["NetworkSettings"].get("Networks") or {})]
        names = {c.name: (c.attrs["NetworkSettings"]["Networks"][network] or {}).get("IPAddress", "")
                 for c in members}
        for c in members:
            self._write_hosts(c, {**{n: ip for n, ip in names.items() if ip and n != c.name}, **self._extra(c)})

    def _write_hosts(self, c: Container, entries: dict[str, str]) -> None:
        if not entries:
            return
        lines = "".join(f"{ip} {name} {HOSTS_MARK}\n" for name, ip in entries.items())
        script = f'grep -v "{HOSTS_MARK}$" /etc/hosts > /tmp/.hosts; cat /tmp/.hosts - > /etc/hosts; rm -f /tmp/.hosts'
        c.exec_run(["sh", "-c", script], user="root", stdin=lines.encode())


# -- volumes, images, networks -----------------------------------------------------------

class Volume:
    def __init__(self, d: dict) -> None:
        c = d.get("configuration") or {}
        self.name = d.get("id") or c.get("name")
        self.attrs = {"Name": self.name, "Labels": c.get("labels") or {}, "CreatedAt": c.get("creationDate", "")}

    def remove(self, force: bool = False) -> None:
        _run(["volume", "rm", self.name], timeout=120)
        _forget_seeded(self.name)


class Volumes:
    def __init__(self, client: Client) -> None:
        self.client = client

    def list(self, filters: dict | None = None) -> list[Volume]:
        want = (filters or {}).get("label")
        out = []
        for d in _json(["volume", "ls", "--format", "json"]):
            v = Volume(d)
            if want:
                k, eq, val = want.partition("=")
                if k not in v.attrs["Labels"] or (eq and v.attrs["Labels"][k] != val):
                    continue
            out.append(v)
        return out

    def get(self, name: str) -> Volume:
        p = _run(["volume", "inspect", name], timeout=60, check=False)
        data = json.loads(p.stdout or b"[]") if p.returncode == 0 else []
        if not data:
            raise NotFound(f"no volume {name}")
        return Volume(data[0])

    def create(self, name: str, labels: dict | None = None, **_) -> Volume:
        args = ["volume", "create"]
        for k, v in (labels or {}).items():
            args += ["--label", f"{k}={v}"]
        _run([*args, name], timeout=60)
        _forget_seeded(name)
        return self.get(name)

    def _ensure(self, name: str, image: str, dest: str) -> None:
        """Docker fills a new named volume from the image's folder (and its owner);
        Apple's start empty and owned by root, so do that once, ourselves."""
        with _seeded_lock:
            if name in _seeded():
                return
            try:
                self.get(name)
            except NotFound:
                _run(["volume", "create", name], timeout=60)
            script = (f'[ -d "{dest}" ] && cp -a "{dest}/." /v/ 2>/dev/null; '
                      f'chown "$(stat -c %u:%g "{dest}" 2>/dev/null || echo 0:0)" /v; '
                      'rm -rf /v/lost+found; true')
            _run(["run", "--rm", "-u", "root", "-w", "/", "-v", f"{name}:/v", "--entrypoint", "sh", image,
                  "-c", script], timeout=300)
            _mark_seeded(name)


class Image:
    def __init__(self, d: dict) -> None:
        cfg = d.get("configuration") or {}
        self.id = d.get("id", "")
        self.tags = [cfg.get("name")] if cfg.get("name") else []
        self.labels = {}
        for v in d.get("variants") or []:
            self.labels = (((v.get("config") or {}).get("config") or {}).get("Labels")) or self.labels
            if self.labels:
                break


class Images:
    def list(self, filters: dict | None = None) -> list[Image]:
        want = (filters or {}).get("label")
        out = []
        for d in _json(["image", "ls", "--format", "json"]):
            img = Image(d)
            if want and want.partition("=")[0] not in img.labels:
                continue
            out.append(img)
        return out

    def pull(self, repo: str, tag: str = "latest", **_) -> None:
        _run(["image", "pull", f"{repo}:{tag}"], timeout=3600)


class Network:
    def __init__(self, client: Client, name: str) -> None:
        self.client, self.name = client, name

    def connect(self, container: Container, aliases: list | None = None) -> None:
        if container.pending is None:
            raise APIError("Apple's container can only join networks before it starts")
        container.pending.setdefault("networks", ["default"]).append(self.name)

    def remove(self) -> None:
        _run(["network", "rm", self.name], timeout=60)


class Networks:
    def __init__(self, client: Client) -> None:
        self.client = client

    def get(self, name: str) -> Network:
        if not any(n.get("id") == name for n in _json(["network", "ls", "--format", "json"])):
            raise NotFound(f"no network {name}")
        return Network(self.client, name)

    def create(self, name: str, internal: bool = False, labels: dict | None = None, **_) -> Network:
        args = ["network", "create", *(["--internal"] if internal else [])]
        for k, v in (labels or {}).items():
            args += ["--label", f"{k}={v}"]
        _run([*args, name], timeout=60)
        return Network(self.client, name)


class Client:
    """The bits of docker.DockerClient that Hangar uses."""

    def __init__(self) -> None:
        cli()
        self.containers = Containers(self)
        self.volumes = Volumes(self)
        self.images = Images()
        self.networks = Networks(self)

    def ping(self) -> bool:
        p = _run(["system", "status"], timeout=20, check=False)
        if p.returncode != 0:
            raise APIError("Apple's container isn't running (container system start)")
        return True

    def address(self, container: Container, network: str) -> str:
        """A container's address on a network (Docker resolves names; here, use IPs)."""
        return container.ip(network)

    def events(self, decode: bool = True, filters: dict | None = None, interval: float = 2.0):
        """Container create/start/die/destroy, by polling (Apple has no event stream)."""
        label = (filters or {}).get("label", "")
        seen: dict[str, tuple[str, str]] = {}
        first = True
        while True:
            now = {}
            for c in self.containers.list(all=True, filters={"label": label} if label else None):
                now[c.id] = (c.status, c.labels.get(label, "") if label else "")
            if not first:
                for cid, (st, lb) in now.items():
                    old = seen.get(cid)
                    acts = (["create"] if old is None else []) + (
                        ["start"] if st == "running" and (old is None or old[0] != "running") else
                        ["die"] if st != "running" and old and old[0] == "running" else [])
                    for a in acts:
                        yield {"Action": a, "Actor": {"ID": cid, "Attributes": {label: lb} if label else {}}}
                for cid, (_st, lb) in seen.items():
                    if cid not in now:
                        yield {"Action": "destroy", "Actor": {"ID": cid, "Attributes": {label: lb} if label else {}}}
            seen, first = now, False
            time.sleep(interval)


# -- reaching the host --------------------------------------------------------------------

def relay(ports: list[int]) -> list[threading.Thread]:
    """Forward gateway_ip():port to 127.0.0.1:port, so containers reach the
    daemon (and LM Studio) the way hangar-host does on Docker. Only VMs on this
    Mac can reach the gateway address."""
    threads = []
    for port in ports:
        t = threading.Thread(target=_serve_relay, args=(gateway_ip(), port), daemon=True,
                             name=f"hangar-relay-{port}")
        t.start()
        threads.append(t)
    return threads


def _serve_relay(host: str, port: int) -> None:
    while True:
        try:
            srv = socket.create_server((host, port), reuse_port=False)
            break
        except OSError:
            time.sleep(5)            # the vmnet bridge isn't up yet
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_pipe_pair, args=(conn, port), daemon=True).start()


def _pipe_pair(conn: socket.socket, port: int) -> None:
    try:
        up = socket.create_connection(("127.0.0.1", port), timeout=10)
    except OSError:
        conn.close()
        return
    up.settimeout(None)

    def pump(a: socket.socket, b: socket.socket) -> None:
        try:
            while data := a.recv(65536):
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    t = threading.Thread(target=pump, args=(up, conn), daemon=True)
    t.start()
    pump(conn, up)
    t.join(timeout=5)
    conn.close()
    up.close()

