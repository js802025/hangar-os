"""Per-agent egress gateway: the only way out for an agent with an allowlist.

The agent sits on an internal network with no route out. This gateway sits
on that network and the normal one, and runs:
  - an HTTP(S) proxy on :3128 that only lets through hosts on the allowlist
    (and never to private / LAN addresses unless a pattern names them),
  - plain TCP forwards of the agent's noVNC (6080) and MCP (8765) ports, which
    the engine publishes on the host's 127.0.0.1 instead of the agent's.

Allowlist: /etc/hangar/allow, one pattern per line ("github.com" also covers
its subdomains; "*" allows everything but still logs). Re-read on change, so
edits apply right away. Every decision is logged: ALLOW / BLOCK host:port.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import sys
import time

ALLOW_FILE = "/etc/hangar/allow"
AGENT = os.environ["AGENT_HOST"]
ALWAYS = {h.strip().lower() for h in os.environ.get("ALWAYS_ALLOW", "").split(",") if h.strip()}  # host:port
_cache = {"mtime": -1.0, "patterns": []}


def log(*a) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), *a, flush=True)


def patterns() -> list[str]:
    try:
        m = os.stat(ALLOW_FILE).st_mtime
    except OSError:
        return []
    if m != _cache["mtime"]:
        # Just after the host replaces the file, a shared folder (Apple's virtiofs)
        # can briefly fail to open it: retry a moment, else keep the last list.
        for _ in range(10):
            try:
                with open(ALLOW_FILE) as f:
                    _cache["patterns"] = [ln.strip().lower().lstrip("*.") if ln.strip() != "*" else "*"
                                          for ln in f if ln.strip() and not ln.startswith("#")]
                _cache["mtime"] = m
                break
            except OSError:
                time.sleep(0.15)
    return _cache["patterns"]


def allowed(host: str, port: int) -> tuple[bool, bool]:
    """(allowed, named explicitly). Explicit names may reach private addresses."""
    host = host.lower().rstrip(".")
    if f"{host}:{port}" in ALWAYS:
        return True, True
    for fresh in (False, True):
        if fresh:                  # before refusing, read the file again: a shared folder's
            _cache["mtime"] = -1.0   # timestamp can lag a just-made edit (Apple's virtiofs)
        for p in patterns():
            if p == "*":
                return True, False
            if host == p or host.endswith("." + p):
                return True, True
    return False, False


async def private(host: str, port: int) -> bool:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for *_, addr in infos:
        ip = ipaddress.ip_address(addr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def splice(r1, w1, r2, w2) -> None:
    await asyncio.gather(pipe(r1, w2), pipe(r2, w1))


async def refuse(w: asyncio.StreamWriter, host: str, why: str) -> None:
    body = (f"Blocked by Hangar: {host} is not on this agent's network allowlist ({why}). "
            f"Ask the user to allow it.\n").encode()
    w.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nX-Hangar-Blocked: " + host.encode() +
            b"\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    try:
        await w.drain()
    finally:
        w.close()


async def check(host: str, port: int) -> str | None:
    ok, explicit = allowed(host, port)
    if not ok:
        return "not allowed"
    if not explicit and await private(host, port):
        return "private address"
    return None


async def proxy(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
        cw.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        cw.close()
        return
    if method.upper() == "CONNECT":
        host, _, port = target.rpartition(":")
        host, port = host.strip("[]"), int(port or 443)
        rest = b""
    else:
        if not target.startswith("http://"):
            await refuse(cw, target[:60], "not a proxy request")
            return
        hostport, _, path = target[7:].partition("/")
        host, _, port = hostport.partition(":")
        port = int(port or 80)
        hdrs = [ln for ln in lines[1:] if ln and not ln.lower().startswith(("proxy-", "connection:"))]
        rest = (f"{method} /{path} {version}\r\n" + "\r\n".join(hdrs) + "\r\nConnection: close\r\n\r\n").encode("latin-1")
    why = await check(host, port)
    if why:
        log("BLOCK", f"{host}:{port}", why)
        await refuse(cw, host, why)
        return
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
    except (OSError, asyncio.TimeoutError) as e:
        log("FAIL", f"{host}:{port}", type(e).__name__)
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        cw.close()
        return
    log("ALLOW", f"{host}:{port}")
    if method.upper() == "CONNECT":
        cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await cw.drain()
    else:
        uw.write(rest)
        await uw.drain()
    await splice(cr, cw, ur, uw)


def forward(port: int):
    async def handle(cr, cw):
        try:
            ur, uw = await asyncio.open_connection(AGENT, port)
        except OSError:
            cw.close()
            return
        await splice(cr, cw, ur, uw)
    return handle


async def main() -> None:
    servers = [await asyncio.start_server(proxy, "0.0.0.0", 3128)]
    for p in (6080, 8765):
        servers.append(await asyncio.start_server(forward(p), "0.0.0.0", p))
    log(f"gateway for {AGENT}: proxy :3128, forwarding 6080 and 8765; allow={patterns()} always={sorted(ALWAYS)}")
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
