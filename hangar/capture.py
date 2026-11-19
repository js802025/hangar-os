"""What the user is looking at, for "Ask Hangar": a screenshot, and the
frontmost window's title, URL (for browsers) and selected text.

Everything here is best effort and nothing leaves the machine by itself: the
Ask panel shows each piece as a chip the user can turn off before sending.

  Hangar OS (KDE Plasma, Wayland): Spectacle for screenshots, wl-clipboard for
    the selection. Window title / URL aren't readable on Wayland without
    extra plumbing, so they're left out there.
  macOS (dev): screencapture, AppleScript for the app, window and browser URL.
  X11: xdotool and xclip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAC = sys.platform == "darwin"
WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))


def _run(*cmd: str, timeout: float = 5, stdin: str | None = None) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def screenshot(mode: str = "region") -> Path | None:
    """mode: region | window | full. Returns a PNG path, or None if cancelled.
    Interactive modes let the user drag a box or click a window."""
    out = Path(tempfile.mkdtemp(prefix="hangar-ask-")) / f"screenshot-{int(time.time())}.png"
    if MAC:
        flag = {"region": ["-i", "-s"], "window": ["-i", "-w"], "full": []}[mode]
        subprocess.run(["screencapture", "-x", *flag, str(out)], timeout=300)
    elif shutil.which("spectacle"):
        flag = {"region": "-r", "window": "-a", "full": "-f"}[mode]
        subprocess.run(["spectacle", "-b", "-n", flag, "-o", str(out)], timeout=300)
    elif shutil.which("grim"):
        geom = _run("slurp", timeout=300) if mode == "region" and shutil.which("slurp") else ""
        subprocess.run(["grim", *(["-g", geom] if geom else []), str(out)], timeout=60)
    elif shutil.which("import"):   # ImageMagick on X11
        subprocess.run(["import", *(["-window", "root"] if mode == "full" else []), str(out)], timeout=300)
    return out if out.exists() and out.stat().st_size > 0 else None


def keep(shot: Path) -> Path:
    """Save a copy where screenshots normally go (Pictures/Screenshots)."""
    import shutil as sh
    d = Path.home() / ("Desktop" if MAC else "Pictures/Screenshots")
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"Screenshot {time.strftime('%Y-%m-%d at %H.%M.%S')}.png"
    sh.copy(shot, dest)
    return dest


def _mac_context() -> dict:
    script = '''
tell application "System Events"
  set p to first application process whose frontmost is true
  set appName to name of p
  set t to ""
  try
    set t to name of front window of p
  end try
end tell
set u to ""
try
  if appName is in {"Google Chrome", "Brave Browser", "Microsoft Edge", "Arc", "Chromium"} then
    tell application appName to set u to URL of active tab of front window
  else if appName is "Safari" then
    tell application "Safari" to set u to URL of front document
  end if
end try
return appName & linefeed & t & linefeed & u'''
    lines = (_run("osascript", "-e", script, timeout=6) + "\n\n\n").split("\n")
    return {"app": lines[0], "title": lines[1], "url": lines[2]}


def context() -> dict:
    """{app, title, url, selection}. Empty strings where unknown."""
    ctx = {"app": "", "title": "", "url": "", "selection": ""}
    if MAC:
        ctx.update(_mac_context())
        # macOS has no "primary selection"; offer what's on the clipboard instead.
        ctx["selection"] = _run("pbpaste")[:4000]
    elif WAYLAND:
        ctx["selection"] = _run("wl-paste", "--primary", "--no-newline")[:4000]
    else:
        wid = _run("xdotool", "getactivewindow")
        if wid:
            ctx["title"] = _run("xdotool", "getwindowname", wid)
        ctx["selection"] = _run("xclip", "-o", "-selection", "primary")[:4000]
    return ctx


def describe(ctx: dict) -> str:
    """The context as text to put in front of the pilot."""
    parts = []
    if ctx.get("app") or ctx.get("title"):
        parts.append(f"Window: {ctx.get('title') or '?'} ({ctx.get('app') or 'unknown app'})")
    if ctx.get("url"):
        parts.append(f"Page: {ctx['url']}")
    if ctx.get("selection"):
        parts.append(f"Selected text:\n{ctx['selection']}")
    return "\n".join(parts)
