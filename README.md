# Hangar

An OS for a spare desktop whose whole job is running AI agents. Each agent gets
its own hidden desktop (browser, apps, computer use) that never touches your
screen, mouse or keyboard. You watch them all as live thumbnails, take over
any one, and hand it back.

> Status: **M6**: a first-boot setup wizard (accounts, local models picked
> for your hardware, phone notifications), plus native desktop apps (Qt): **Hangar** shows every agent as a
> live thumbnail, with a Mission Control-style overview, and lets you watch,
> take over and give tasks. Alongside it are **Passwords**, **Accounts**,
> **Models** and **Settings**. The same things work in a browser for your
> phone. Agents run tasks with Claude Code, Codex CLI or
> a local model (LM Studio), each on its own hidden desktop. A local daemon
> collects their reports and notifies you (ntfy, Slack, Telegram).

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
make base        # build the agent image for this machine (arm64 on a Mac)
make smoke       # M1: one agent: screenshot, open a browser, report
uv run python tests/smoke_m2.py   # M2: three agents at once, browser tools, takeover
uv run python tests/smoke_m3.py   # M3: local harness (fake model), API-key account
uv run python tests/smoke_m4.py   # M4: daemon, tokens, reports → ntfy, proxies, events
uv run python tests/smoke_m5.py   # M5: Spaces API: create with shares, accounts, sign-in job, settings
uv run python tests/smoke_vault.py  # Passwords: an agent signs in to a fake site without seeing the password
uv run --extra apps python tests/smoke_apps.py  # native apps, offscreen; screenshots in out/apps-*.png
uv run --extra apps python tests/smoke_setup.py # setup wizard, every step; screenshots in out/setup-*.png
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

## Setup wizard

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
   recommends models from a [catalog](hangar/catalog.py) that will run well,
   always including one that can see the screen. Each shows its memory needs,
   and models that won't fit are marked too big. Downloads run in the
   background while you finish.
5. **Notifications:** turns on ntfy with a random private topic and shows a
   QR code to scan in the ntfy phone app, with a "Send a test" button.
   Slack and Telegram are optional.
6. **Done:** a short tour (Spaces key, Take over, Passwords) and **Launch
   your first agent**, which opens Hangar's New agent form with an example task.

The user account is created by the OS installer, so the wizard doesn't ask.
On the OS, [os/autostart](os/autostart) opens the wizard full screen on the
first login, and it exits straight away once setup is done.

## The apps

Hangar's interface is a set of native desktop apps written in Qt (PySide6),
each with its own window and taskbar icon, on top of the daemon's API:

| app | what it's for |
|---|---|
| **Hangar** | every agent as a live thumbnail; open one to watch its desktop, take over, give it a task, read reports and logs; New agent (with a real folder picker for shared folders) |
| **Passwords** | the vault, modelled on Apple's Passwords app (see below) |
| **Accounts** | subscriptions and API keys; sign-ins run in a panel with the official link |
| **Models** | LM Studio models, downloads, server |
| **Hangar Settings** | notifications, network access (with a QR code for your phone), run setup again |
| **Hangar Setup** | the first-boot wizard (above) |

```bash
uv sync --extra apps          # PySide6 (Qt), once
uv run hangar open            # Hangar (spaces); also: passwords | accounts | models | settings
uv run hangar overview        # every agent at once, full screen (Esc closes)
```

**The Spaces key.** Like Mission Control, one key shows every agent working,
live: click one to jump to it. In the Hangar app it's F3 or the Overview
button. On the Hangar OS it's bound system-wide to the Mission Control key
(Apple keyboards) or Meta+S ([os/kde/kglobalshortcutsrc](os/kde/kglobalshortcutsrc)).
Only one Hangar runs at a time: launching it again (or pressing the key)
signals the running one.

Agents' desktops are shown by a small native VNC client
([hangar/apps/vnc.py](hangar/apps/vnc.py)) that goes through the daemon's
token-checked proxy. While you've taken over, your mouse and keyboard go to
the agent's desktop (Cmd/Ctrl shortcuts included).

Launchers for the OS (one per app, so each gets its own app-menu entry and
taskbar icon) are in [os/applications/](os/applications).

### Web version

The same features also run in a browser, for checking in from a phone or
another computer (Settings → network access):

```bash
uv run hangar ui        # opens http://127.0.0.1:7700 in your browser, signed in
```

- **Spaces:** every agent as a live thumbnail with its state, flavor, model,
  task and latest report. Toasts pop up when one finishes or needs you.
- **Agent view:** click a tile for its live desktop, with Take over / Hand
  back, a box to give it the next task, and Reports / Logs / About tabs.
- **New agent:** name, flavor, model or account, task and shared folders.
- **Accounts:** add subscriptions (the official sign-in runs in a panel that
  shows its link and takes the code) or API keys.
- **Models:** LM Studio's models with vision / tool-use support, downloads
  with progress, server start.
- **Settings:** notification channels, and "open from other devices on my
  network", which shows a QR code that signs your phone in.

The UI is plain HTML, CSS and JavaScript in [hangar/ui/](hangar/ui), with no build
step. Edit a file and reload the page.

Each web page can also open on its own at `/app/<name>`
(`uv run hangar open passwords --web` opens it in a Chrome app window).

## Passwords (the vault)

Agents can sign in to websites without ever seeing your password, much like
Apple's Passwords app filling a login for you:

1. The agent opens a site's sign-in page and calls `request_login("github.com", reason)`.
2. You get a notification, and a bar appears at the top of Hangar and Passwords:
   *"scout wants to sign in to github.com: to check your PRs"*. Pick the login
   and how long to allow it (just this once, for 1 hour, or always for that
   agent), or Deny.
3. Hangar types the username and password into that agent's browser itself,
   only if the page really is on that site (a lookalike domain is refused, and
   so is plain http). The agent's tools only report "Filled the username and password".
4. Verification codes work the same way with `request_code`. If you saved the
   site's 2FA setup key, Hangar fills the current code. Otherwise you type the
   code from your text or email into the bar.

The **Passwords** app has All, Codes (live 6-digit codes with a countdown),
Security (weak and reused passwords), Agent access (requests, standing
permissions, activity log) and Recently Deleted (kept 30 days). It also has
show/copy, a strong-password generator and notes.

```bash
uv run hangar vault add github.com -u me@example.com   # asks for the password (hidden)
uv run hangar vault list | requests | approve <id> [--scope hour] | deny <id> | remove <id>
```

The vault is encrypted at `~/.local/state/hangar/vault.enc` with a key derived
from Hangar's secret. The audit log has no secrets, agent snapshots never show
password fields' contents, and other agents can't see each other's requests.
**The honest limit:** once a password is typed into an agent's browser, a
determined agent could read it back out of the page. So approve logins only
for agents you'd trust with that account, and prefer "just this once" or
"for 1 hour".

## Using the CLI

```bash
uv run hangar new scout --task "find me a used road bike under $500"
uv run hangar list                 # all agents, their state and task
uv run hangar status scout         # details + latest report (--json too)
uv run hangar view scout           # watch live in your browser (view-only)
uv run hangar takeover scout       # pause the agent, open with your mouse/keyboard
uv run hangar release scout        # hand it back
uv run hangar logs scout           # session log + reports (-s mcp for one service)
uv run hangar shell scout          # bash inside the agent
uv run hangar stop|start scout
uv run hangar destroy scout        # removes it and its files (--keep-home to keep)
uv run hangar new reader --share ~/Documents/papers           # read-only at ~/shared/papers
uv run hangar new writer --share ~/Projects/site:rw           # writable
```

**Shared folders** are the only host files an agent can see. They're mounted
at `/home/agent/shared/<folder>`, read-only unless you add `:rw`. Hangar
refuses to share your whole home folder, `/`, or anything that is or contains
`~/.ssh`, `~/.gnupg`, cloud credentials, keyrings or Hangar's own config.

`--flavor` is stored now and gets real images in M7.

## The daemon

`hangar new`, `run`, `view` and `takeover` start the daemon in the
background if it isn't running (log in `~/.local/state/hangar/daemon.log`).
Run it in the foreground with `uv run hangar daemon`; `hangar daemon status`,
`stop` and `token` do what they say. It listens on `127.0.0.1:7700` only.

Agents keep working without it, but their reports then stay in their own
containers (`hangar logs`) and you get no notifications.

| route | |
|---|---|
| `GET /api/agents`, `/api/agents/{name}` | agents with state, task and last report |
| `GET /api/agents/{name}/screenshot.png?w=640` | live thumbnail |
| `GET /api/agents/{name}/logs?service=agent` | logs |
| `GET` / `POST /api/agents/{name}/reports` | report history / agents post here |
| `POST /api/agents/{name}/run` `{"task": …}`, `takeover`, `release`, `stop`, `start` | actions |
| `GET /api/events` | server-sent events: reports, agents starting and stopping |
| `/agents/{name}/view[?control=1]` | noVNC, proxied (including its websocket) |
| `/agents/{name}/mcp` | the agent's MCP server, proxied |

Everything needs the admin token (`Authorization: Bearer …`), except agents
posting their own reports with their own token. `hangar view` opens
`/login?token=…` once, which sets an HttpOnly, SameSite=Strict cookie for the
browser. The tokens are HMACs of one random secret in
`~/.local/state/hangar/secret` (mode 0600), which never enters a container.

## Notifications

You get a notification when an agent finishes, fails or needs you.

```bash
uv run hangar notify add ntfy        # topic URL, e.g. https://ntfy.sh/my-hangar-7f3k (+ optional token)
uv run hangar notify add slack       # incoming-webhook URL
uv run hangar notify add telegram    # bot token + chat ID
uv run hangar notify list | test | remove <channel>
```

Channels are stored in `~/.config/hangar/notify.json` (mode 0600), and
secrets are read without echoing. With no channels set, notifications only go
to the daemon's log. With ntfy, pick a hard-to-guess topic name or use a
token, because public ntfy topics are readable by anyone who knows the name.

## Accounts

Each account is a container volume (`hangar-acct-<name>`) holding that
account's login state or API key. It's mounted at `/hangar/account` only into
agents assigned to it, so credentials never go into images, environment
variables or command lines.

```bash
uv run hangar accounts add personal --provider claude   # Claude Pro/Max
uv run hangar accounts add work --provider codex        # ChatGPT Plus/Pro
uv run hangar accounts add api --provider anthropic-key # or openai-key (asks for the key, hidden)
uv run hangar accounts list | status <name> | login <name> | remove <name>
```

Subscription sign-in runs the vendor's own CLI login (`claude auth login`,
`codex login --device-auth`) in a throwaway container: open the link it
prints, sign in in your browser, paste back the code. Hangar never sees your
password.

Agents using the same account share its login. That's convenient, but they
also share its rate limits, so for many busy agents use separate accounts.

## Running tasks

```bash
uv run hangar new scout --model claude --task "find 3 used road bikes under $500 near Boston"
uv run hangar new coder --model codex:gpt-5-codex --account work --task "…"
uv run hangar new local1 --model local:qwen/qwen2.5-vl-7b --task "…"
uv run hangar run scout "now compare their frame sizes"   # next task, same desktop
```

`--model` picks the harness: `claude[:model]`, `codex[:model]` or
`local:<lm-studio-model>`. All three get the same desktop and browser tools
through the agent's MCP server, and finish by calling `report`. If a harness
exits without a final report, Hangar posts one (done, or the error reason).

## Local models (LM Studio)

LM Studio runs on the host, so models download once and every agent shares
them. Agents reach it at `http://hangar-host:1234/v1`.

```bash
uv run hangar models list                  # downloaded models, with VISION / TOOLS columns
uv run hangar models download qwen/qwen2.5-vl-7b
uv run hangar models serve                 # start LM Studio's API server
uv run hangar models remove <model>
```

When you create a local agent, Hangar starts the server and loads the model
with a 16k context (LM Studio's default is too small for agent work). It warns
if the model can't see images (no screenshots, browser text tools only) or
wasn't trained for tool use.

The local harness is a small loop
([local_agent.py](images/mcp-desktop/hangar_desktop/local_agent.py)) that gives
the model the agent's MCP tools as OpenAI function tools. To make local models
more usable it:
- also accepts tool calls written as text (`browser_navigate("…")`,
  `<tool_call>{…}</tool_call>`),
- tells the model when it's repeating itself,
- keeps only the latest two screenshots and trims old tool output.

**Pick a model your machine can hold.** On a 24 GB Mac running Docker, 7–12 GB
models are comfortable. A 7B model works the tools but is weak at multi-step
tasks; 20B+ models do much better if they fit.

**No conflicts by design:** every agent is its own container, so each has
display `:1`, its own Chromium profile and its own home volume. Its noVNC
and MCP ports are picked by the engine, bound to 127.0.0.1 only, and need the
agent's token.

## Desktop-control MCP tools

| tool | what it does |
|---|---|
| `screenshot` | PNG of the whole desktop |
| `screen_info` | size, mouse position, active window title |
| `click`, `move_mouse`, `drag`, `scroll` | mouse (screen pixels from top-left) |
| `type_text`, `key` | keyboard (`key("ctrl+l Return")`) |
| `open_app` | `browser` (args = URL), `terminal`, or any command |
| `read_clipboard`, `wait` | |
| `report` | post a status update / final summary to the user |

### Browser tools (preferred for the web)

Agents drive their Chromium through the DevTools protocol (port 9222, inside
the container only). `browser_snapshot` returns the page as a short numbered
list, and the agent acts by number, which is far more reliable and cheaper
than screenshots:

```
url: https://en.wikipedia.org/wiki/Hangar
title: Hangar - Wikipedia
[3] textbox "Search Wikipedia"
[4] button "Search"
## Contents
[10] link "History"
… 70 more offscreen elements (scroll, then snapshot again)
```

| tool | what it does |
|---|---|
| `browser_navigate(url)` | open a URL (starts the browser if needed) |
| `browser_snapshot()` | numbered elements + headings; on-screen first when long |
| `browser_click(ref)`, `browser_type(ref, text, submit)`, `browser_select(ref, option)` | act on an element |
| `browser_press(key)`, `browser_back()` | keyboard, history |
| `browser_text()` | the page's readable text |
| `browser_tabs()`, `browser_switch_tab(i)` | tabs (`-1` opens a new one) |

Actions return a fresh snapshot, so the agent sees the result immediately.
Screenshots + pixel clicks stay available for canvases and odd widgets.

While the user has taken over a desktop, input tools refuse with a "user has
control" message, so the agent waits instead of fighting for the mouse.

## Repo layout

```
hangar/apps/       native Qt apps: spaces.py (+ overview), passwords.py, setup.py (wizard),
                   manage.py (accounts, models, settings), vnc.py (native viewer), common.py
hangar/hardware.py, catalog.py, hostsetup.py   what the wizard detects, suggests and sets
os/                OS integration: app launchers, the Spaces-key shortcut
hangar/            Python package: engine.py (containers), cli.py (`hangar`),
                   daemon.py (API + proxies), manage_api.py (UI's API),
                   vault.py + vault_api.py (passwords), prepare.py (checks), jobs.py (sign-ins,
                   downloads), auth.py (tokens), notify.py, ui/ (Spaces),
                   accounts.py (account volumes, logins), models.py (LM Studio)
images/base/       base agent image (Containerfile + rootfs)
images/mcp-desktop/ desktop-control MCP server + local-model agent loop
tests/             smoke tests
```

## Roadmap

M1 hidden desktop + MCP · M2 `hangar` CLI, many agents · M3 accounts, LM Studio,
local harness · M4 daemon, reports, notifications · M5 Spaces UI · M6 setup
wizard · M7 snapshots, allowlists, flavors · M8 bootc OS image + ISO
