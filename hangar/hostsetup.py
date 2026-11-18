"""System settings the setup wizard touches: language, keyboard, time zone,
Wi-Fi. On the Hangar OS (Linux) these go through localectl, timedatectl and
NetworkManager. In dev on a Mac they're only shown, never changed.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time

LINUX = platform.system() == "Linux"
DEV = not LINUX or bool(os.environ.get("HANGAR_DEV"))

LANGUAGES = {"en_US.UTF-8": "English (US)", "en_GB.UTF-8": "English (UK)", "de_DE.UTF-8": "Deutsch",
             "fr_FR.UTF-8": "Français", "es_ES.UTF-8": "Español", "it_IT.UTF-8": "Italiano",
             "pt_BR.UTF-8": "Português (Brasil)", "nl_NL.UTF-8": "Nederlands", "sv_SE.UTF-8": "Svenska",
             "pl_PL.UTF-8": "Polski", "ja_JP.UTF-8": "日本語", "ko_KR.UTF-8": "한국어", "zh_CN.UTF-8": "中文 (简体)"}
KEYBOARDS = {"us": "US", "gb": "UK", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
             "br": "Brazilian", "nl": "Dutch", "se": "Swedish", "pl": "Polish", "jp": "Japanese",
             "kr": "Korean", "us(dvorak)": "US Dvorak", "us(colemak)": "US Colemak"}


def _run(*cmd: str, stdin: str | None = None, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def current() -> dict:
    tz = ""
    try:
        tz = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
    except OSError:
        pass
    lang = (os.environ.get("LANG") or "en_US.UTF-8")
    keymap = "us"
    if LINUX and shutil.which("localectl"):
        _, out = _run("localectl", "status")
        for line in out.splitlines():
            k, _, v = line.strip().partition(": ")
            if k == "System Locale" and "LANG=" in v:
                lang = v.split("LANG=")[1].split()[0]
            if k == "X11 Layout":
                keymap = v.strip()
    return {"language": lang, "keyboard": keymap, "timezone": tz, "dev": DEV,
            "languages": LANGUAGES, "keyboards": KEYBOARDS}


def apply(language: str | None = None, keyboard: str | None = None, timezone: str | None = None) -> list[str]:
    """Change system settings. Returns what was done (or would be, in dev)."""
    if language and language not in LANGUAGES:
        raise ValueError("unknown language")
    if keyboard and keyboard not in KEYBOARDS:
        raise ValueError("unknown keyboard layout")
    if timezone and not os.path.exists(f"/usr/share/zoneinfo/{timezone}"):
        raise ValueError("unknown time zone")
    cmds = []
    if language:
        cmds.append(["localectl", "set-locale", f"LANG={language}"])
    if keyboard:
        layout, _, variant = keyboard.rstrip(")").partition("(")
        cmds.append(["localectl", "set-x11-keymap", layout, "", variant])
    if timezone:
        cmds.append(["timedatectl", "set-timezone", timezone])
    done = []
    for c in cmds:
        if DEV:
            done.append("dev mode, not run: " + " ".join(x for x in c if x))
            continue
        code, out = _run(*c)
        if code:
            raise RuntimeError(f"{c[0]}: {out}")
        done.append(" ".join(x for x in c if x))
    return done


LMSTUDIO_INSTALLER = "https://lmstudio.ai/install.sh"   # LM Studio's official headless install (llmster + lms)


def lmstudio_install_argv() -> list[str]:
    """Install LM Studio's headless server into ~/.lmstudio (no root needed)."""
    if not LINUX:
        raise RuntimeError("on a Mac, install LM Studio from lmstudio.ai")
    return ["sh", "-c", f"curl -fsSL {LMSTUDIO_INSTALLER} | sh && ~/.lmstudio/bin/lms server start "
                        "--port 1234 --bind 0.0.0.0 && echo 'LM Studio is ready.'"]


def online() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return True
    except OSError:
        return False


def network() -> dict:
    info = {"online": online(), "wifi": False, "networks": [], "dev": DEV}
    if DEV or not shutil.which("nmcli"):
        return info
    code, out = _run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "auto")
    if code:
        return info
    info["wifi"] = True
    seen = {}
    for line in out.splitlines():
        parts = line.replace("\\:", "\0").split(":")
        if len(parts) < 4 or not parts[1]:
            continue
        ssid = parts[1].replace("\0", ":")
        net = {"ssid": ssid, "active": parts[0] == "yes", "signal": int(parts[2] or 0),
               "secure": parts[3] not in ("", "--")}
        if ssid not in seen or net["signal"] > seen[ssid]["signal"]:
            seen[ssid] = net
    info["networks"] = sorted(seen.values(), key=lambda n: (not n["active"], -n["signal"]))
    return info


def connect_wifi(ssid: str, password: str = "") -> str:
    if DEV:
        raise RuntimeError("Wi-Fi is managed by your Mac in dev mode")
    # The password goes on stdin (nmcli --ask), never on a command line.
    code, out = _run("nmcli", "--ask", "dev", "wifi", "connect", ssid, stdin=(password + "\n") if password else "",
                     timeout=60)
    if code:
        raise RuntimeError(out.splitlines()[-1] if out else "couldn't connect")
    for _ in range(10):
        if online():
            break
        time.sleep(1)
    return out
