# Developing Hangar

## How it fits together

```
 host (your Mac in dev / Hangar OS on the target)
 ├── hangar CLI + engine (Python, Docker API → Docker Desktop or rootless Podman)
 ├── hangar daemon :7700   API, reports, notifications, proxies noVNC + MCP
 └── agent container  hangar-<name>          (one per agent)
     ├── Xvnc :1          hidden X display + VNC on 127.0.0.1:5901 (in-container only)
     ├── openbox          window manager
     ├── chromium         own profile in ~/.config/hangar-chromium
     ├── websockify :6080 noVNC   ─┐  published on 127.0.0.1:<random>,
     └── MCP server :8765/mcp    ─┘  both locked with the agent's own token
```

Agents post reports to the daemon at `hangar-host:7700`. Only the daemon
holds every agent's token, so agents can't watch, drive or impersonate each
other, even though their ports are on the same host.

## Dev setup on a Mac (Apple Silicon)

Needs Docker Desktop (OrbStack or Podman Desktop also work: the engine finds
their socket, or set `DOCKER_HOST`) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync          # Python deps for the engine and tests
make images      # base agent image + network gateway + flavors (arm64 on a Mac); `make base` for just the base
make smoke       # M1: one agent: screenshot, open a browser, report
uv run python tests/smoke_m2.py   # M2: three agents at once, browser tools, takeover
uv run python tests/smoke_m3.py   # M3: local harness (fake model), API-key account
uv run python tests/smoke_m4.py   # M4: daemon, tokens, reports → ntfy, proxies, events
uv run python tests/smoke_m5.py   # M5: Spaces API: create with shares, accounts, sign-in job, settings
uv run python tests/smoke_vault.py  # Passwords: an agent signs in to a fake site without seeing the password
uv run --extra apps python tests/smoke_apps.py  # native apps, offscreen; screenshots in out/apps-*.png
uv run --extra apps python tests/smoke_setup.py # setup wizard, every step; screenshots in out/setup-*.png
uv run python tests/smoke_m7.py   # M7: snapshots, accessibility tools, network allowlists, flavors
uv run python tests/smoke_m9.py   # M9: two agents on one desktop, window lock, messaging rules, pilot
uv run --extra apps --extra voice python tests/smoke_m10.py  # M10: Ask panel with context, local voice
uv run --extra apps python tests/smoke_m11.py  # M11: notes + agent access, review diffs, Home and Notes apps
```

Screenshots from the smoke test land in `out/`. `make clean` removes all
agents.

### Drive an agent from your own MCP client (optional)

Harnesses normally run *inside* the agent, but the daemon also proxies each
agent's MCP server, so Claude Code on your Mac can drive an agent's desktop:

```bash
claude mcp add --transport http hangar-scout http://127.0.0.1:7700/agents/scout/mcp \
  --header "Authorization: Bearer $(uv run hangar daemon token)"
```

### Multi-arch images

`make base-multiarch` builds amd64 + arm64 with buildx (`PUSH=1` to push).
Everything in the image comes from Debian packages available on both.

## The setup wizard

```bash
uv run hangar-app setup        # on a Mac: a normal window, nothing on your Mac is changed
```

Like setting up a new Mac: one step per screen, progress dots, Back / Skip /
Continue. Every step can be skipped and redone later (Settings → Run setup again).

1. **Welcome:** language, keyboard and time zone. These are applied with
   `localectl`/`timedatectl` on the Hangar OS, and only shown in dev mode on a Mac.
2. **Network:** shows whether you're online. On the OS it lists Wi-Fi networks
   (NetworkManager) and joins one, with the password passed on stdin.
3. **AI accounts:** Claude or ChatGPT subscriptions through each company's
   official sign-in, or API keys. Connect several, or none.
4. **Local models:** detects CPU, RAM and GPU/VRAM, works out how much memory
   a model can use (after the OS, your apps, Docker and the agents), and
   recommends models from a [catalog](../hangar/catalog.py) that will run well,
   always including one that can see the screen. Each shows its memory needs,
   and models that won't fit are marked too big. Downloads run in the
   background while you finish.
5. **Notifications:** turns on ntfy with a random private topic and shows a
   QR code to scan in the ntfy phone app, with a "Send a test" button.
   Slack and Telegram are optional.
6. **Done:** a short tour (the pilot, spaces, review, notes, passwords) and
   **Say hello to your pilot**, which opens Hangar on a new chat.

The user account is created by the OS installer, so the wizard doesn't ask.
On the OS, [os/autostart](../os/files/etc/xdg/autostart) opens the wizard full screen on the
first login, and it exits straight away once setup is done.

## The wiki

The [wiki](https://github.com/js802025/hangar-os/wiki) is written in
[docs/wiki/](wiki) (one Markdown file per page, GitHub wiki style) and
published with `docs/wiki/publish.sh`, which copies the pages and
[docs/images/](images) into the wiki's own repository.

## Screenshots and the demo video

```bash
QT_QPA_PLATFORM=offscreen uv run --extra apps python video/capture.py   # real agents, records out/video/
QT_QPA_PLATFORM=offscreen uv run --extra apps python video/render.py    # out/hangar-walkthrough.mp4
QT_QPA_PLATFORM=offscreen uv run --extra apps python docs/make_images.py  # docs/images/ for the README and wiki
```

The capture runs a throwaway demo (its own state, notes folder and daemon
port) against local demo websites in [video/site/](../video/site), so nothing
from your own screen or accounts is recorded.

## Repo layout

```
hangar/apps/       native Qt apps: home.py (Hangar: chat, spaces, review, overview), notes.py,
                   space.py (one space, own desktop), passwords.py, setup.py (wizard),
                   manage.py (accounts, models, settings), vnc.py (native viewer), common.py,
                   spaces.py (the M5-M10 widget version, kept for now)
hangar/apps/qml/   Qt Quick UI: Theme (design system), Home, Sidebar, ChatView, SpacePanel,
                   ReviewView, Overview, Notes, SpaceWindow, and small controls
hangar/threads.py, notes.py, kde.py   conversations; the notes vault; KDE virtual desktops
hangar/hardware.py, catalog.py, hostsetup.py   what the wizard detects, suggests and sets
hangar/snapshots.py, network.py   workspace snapshots; allowlist networks + gateways
hangar/spaces.py, messages.py, pilot.py   teams in a space, the message bus, the pilot's tools
hangar/apps/ask.py, capture.py, voice.py      Ask Hangar: panel + tray, screen/window capture, local voice
images/flavors/    research, coding, office (add your own)
images/gateway/    the per-agent egress gateway
os/                Hangar OS: Containerfile, files layered into it, branding, ISO config
.github/workflows/ CI: agent images (multi-arch) and the OS image + ISO
hangar/            Python package: engine.py (containers), cli.py (`hangar`),
                   daemon.py (API + proxies), manage_api.py (UI's API),
                   vault.py + vault_api.py (passwords), prepare.py (checks), jobs.py (sign-ins,
                   downloads), auth.py (tokens), notify.py, ui/ (Spaces),
                   accounts.py (account volumes, logins), models.py (LM Studio)
images/base/       base agent image (Containerfile + rootfs)
images/mcp-desktop/ desktop-control MCP server + local-model agent loop
tests/             smoke tests
```
