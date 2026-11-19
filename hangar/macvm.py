"""Mac computers for agents (Hangar for Mac only).

Most spaces get a Linux desktop in their container. A space created with
`--computer mac` instead gets a real macOS virtual machine as its screen, for
work that needs Mac apps. The agent itself (its harness, files, reports, notes)
still runs in the space's container; its screen tools (screenshot, click,
type, key, scroll, drag) are forwarded through the daemon to the VM.

The VMs are run by terminal-mac (github.com/govijr/terminal-mac, MIT), which
boots macOS with Apple's Virtualization framework and drives it with a helper
inside the guest: nothing touches your own screen, mouse or keyboard.

Apple allows at most two macOS VMs running at once on a Mac, so Mac computers
are the exception, not how the crew normally works. A VM is prepared once
(`hangar mac setup`: you finish macOS setup in its window, as on a new Mac) and
then lent to one space at a time.

On anything but a Mac with terminal-mac installed, every function here raises
MacVMError, and nothing else in Hangar changes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_VM = "hangar-mac"
MAX_RUNNING = 2          # Apple's limit on running macOS guests per Mac
KEYS = {"Return": "return", "Enter": "return", "Tab": "tab", "Escape": "escape", "BackSpace": "delete",
        "Delete": "forwarddelete", "space": "space", "Up": "up", "Down": "down", "Left": "left", "Right": "right",
        "Home": "home", "End": "end", "Page_Up": "pageup", "Page_Down": "pagedown",
        "ctrl": "ctrl", "control": "ctrl", "alt": "option", "super": "cmd", "meta": "cmd", "shift": "shift"}


class MacVMError(RuntimeError):
    pass


def binary() -> str:
    exe = os.environ.get("HANGAR_TERMINAL_MAC") or shutil.which("terminal-mac")
    if sys.platform != "darwin" or not exe:
        raise MacVMError("Mac computers need Hangar for Mac with terminal-mac installed "
                         "(github.com/govijr/terminal-mac)")
    return exe


def available() -> bool:
    try:
        binary()
        return True
    except MacVMError:
        return False


def _run(*args: str, timeout: float = 60) -> str:
    try:
        out = subprocess.run([binary(), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MacVMError(f"terminal-mac {args[0]} timed out") from None
    if out.returncode != 0:
        msg = (out.stderr or out.stdout).strip()
        try:
            msg = json.loads(msg).get("error", msg)
        except (ValueError, AttributeError):
            pass
        raise MacVMError(f"terminal-mac {' '.join(args[:3])}: {msg or 'failed'}")
    return out.stdout


def _json(text: str) -> dict:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {"result": v}
    except ValueError:
        return {"output": text.strip()}


def status(vm: str) -> dict:
    return _json(_run("action", vm, "status", timeout=20))


def start(vm: str) -> None:
    """Boot the VM headless if it isn't running (it must have been set up)."""
    _run("start", vm, timeout=180)


def stop(vm: str) -> None:
    _run("stop", vm, timeout=120)


def show(vm: str) -> None:
    """Open the VM's own window (to take over, or finish setup)."""
    _run("show", vm, timeout=30)


def setup_command(vm: str = DEFAULT_VM) -> list[str]:
    """First-time setup runs in the foreground: it downloads macOS and opens
    the VM's window, where you finish Setup Assistant and create its user."""
    return [binary(), "up", vm]


# -- the screen ------------------------------------------------------------------------

def screenshot(vm: str, max_width: int = 0) -> bytes:
    with tempfile.TemporaryDirectory(prefix="hangar-mac-") as d:
        path = Path(d) / "shot.png"
        _run("action", vm, "screenshot", "--output", str(path), timeout=30)
        data = path.read_bytes()
    if max_width:
        data = _shrink(data, max_width)
    return data


def _shrink(png: bytes, max_width: int) -> bytes:
    try:
        import io

        from PIL import Image
    except ImportError:
        return png
    img = Image.open(io.BytesIO(png))
    if img.width <= max_width:
        return png
    img.thumbnail((max_width, max_width * img.height // img.width))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def mac_keys(keys: str) -> list[str]:
    """xdotool-style 'ctrl+l Return' -> terminal-mac's ['ctrl+l', 'return'].
    Agents on a Mac should use cmd+…; ctrl stays ctrl."""
    out = []
    for combo in keys.split():
        parts = [KEYS.get(p, KEYS.get(p.lower(), p.lower())) for p in combo.split("+") if p]
        if parts:
            out.append("+".join(parts))
    return out


def act(vm: str, action: str, body: dict) -> dict:
    """One input action on the VM's screen. Coordinates are screenshot pixels
    (terminal-mac's guest points)."""
    def num(k: str, default: int | None = None) -> str:
        v = body.get(k, default)
        if v is None:
            raise MacVMError(f"{action} needs {k}")
        return str(int(v))

    if action == "click":
        args = ["click", num("x"), num("y")]
        if int(body.get("clicks", 1)) >= 2:
            args.append("--double")
        if body.get("button", "left") == "right":
            args += ["--button", "right"]
        return _json(_run("action", vm, *args))
    if action == "move":
        return _json(_run("action", vm, "move", num("x"), num("y")))
    if action == "drag":
        return _json(_run("action", vm, "drag", num("x1"), num("y1"), num("x2"), num("y2")))
    if action == "type":
        return _json(_run("action", vm, "type", "--", str(body.get("text", ""))))
    if action == "key":
        res: dict = {}
        for k in mac_keys(str(body.get("keys", ""))):
            res = _json(_run("action", vm, "key", k))
        return res
    if action == "scroll":
        d = str(body.get("direction", "down"))
        if d not in ("up", "down"):
            raise MacVMError("on a Mac computer, scroll up or down")
        dy = int(body.get("amount", 3)) * 100 * (-1 if d == "down" else 1)
        args = ["scroll", "--dy", str(dy)]
        if body.get("x") is not None and body.get("y") is not None:
            args += ["--x", num("x"), "--y", num("y")]
        return _json(_run("action", vm, *args))
    if action == "status":
        return status(vm)
    raise MacVMError(f"unknown action {action}")


ACTIONS = ("click", "move", "drag", "type", "key", "scroll", "status")
