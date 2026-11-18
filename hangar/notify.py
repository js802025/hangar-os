"""Notifications: tell the user when an agent finishes, fails or needs them.

Channels live in ~/.config/hangar/notify.json (mode 0600, since webhook URLs
and bot tokens are secrets):
  {"ntfy":     {"url": "https://ntfy.sh/my-topic", "token": ""},
   "slack":    {"webhook": "https://hooks.slack.com/services/..."},
   "telegram": {"bot_token": "123:abc", "chat_id": "42"}}
With nothing configured, notifications only go to the daemon's log.
"""

from __future__ import annotations

import json

import httpx

from hangar import auth

PATH = auth.CONFIG_DIR / "notify.json"
CHANNELS = {
    "ntfy": ["url", "token"],
    "slack": ["webhook"],
    "telegram": ["bot_token", "chat_id"],
}
SECRET_FIELDS = {"token", "webhook", "bot_token"}
TITLES = {"done": "finished", "error": "hit a problem", "waiting_on_user": "needs you"}


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {}


def save(cfg: dict) -> None:
    auth.write_private(PATH, json.dumps(cfg, indent=2))


def should_notify(report: dict) -> bool:
    return bool(report.get("final")) or report.get("status") == "waiting_on_user"


def title_for(agent: str, status: str) -> str:
    return f"{agent} {TITLES.get(status, status)}"


def desktop(title: str, message: str, status: str) -> None:
    """A notification on this computer's own desktop (the Hangar OS), via notify-send."""
    import shutil
    import subprocess
    import sys
    if sys.platform != "linux" or not shutil.which("notify-send"):
        return
    urgency = "critical" if status in ("error", "waiting_on_user") else "normal"
    subprocess.Popen(["notify-send", "--app-name=Hangar", "--icon=dev.hangar.spaces", f"--urgency={urgency}",
                      title, message[:500]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def send(title: str, message: str, status: str = "done", cfg: dict | None = None) -> dict[str, str]:
    """Send to every configured channel. Returns {channel: "ok" | error}."""
    cfg = load() if cfg is None else cfg
    results: dict[str, str] = {}
    desktop(title, message, status)
    with httpx.Client(timeout=10) as http:
        for name, c in cfg.items():
            try:
                if name == "ntfy":
                    headers = {"Title": title, "Tags": {"done": "white_check_mark", "error": "warning",
                                                        "waiting_on_user": "raising_hand"}.get(status, "robot"),
                               "Priority": "high" if status in ("error", "waiting_on_user") else "default"}
                    if c.get("token"):
                        headers["Authorization"] = f"Bearer {c['token']}"
                    r = http.post(c["url"], content=message.encode(), headers=headers)
                elif name == "slack":
                    r = http.post(c["webhook"], json={"text": f"*{title}*\n{message}"})
                elif name == "telegram":
                    r = http.post(f"https://api.telegram.org/bot{c['bot_token']}/sendMessage",
                                  json={"chat_id": c["chat_id"], "text": f"{title}\n{message}"})
                else:
                    continue
                r.raise_for_status()
                results[name] = "ok"
            except (httpx.HTTPError, KeyError) as e:
                # Never echo URLs: they can contain the webhook secret.
                results[name] = f"failed ({type(e).__name__}"
                results[name] += f" {e.response.status_code})" if isinstance(e, httpx.HTTPStatusError) else ")"
    return results
