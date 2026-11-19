# Hangar

An OS for a spare desktop whose whole job is running AI agents. Each agent gets
its own hidden desktop (browser, apps, computer use) that never touches your
screen, mouse or keyboard. You watch them all as live thumbnails, take over
any one, and hand it back.

> Status: **M11**: Hangar is chat-first. The main window is a conversation
> with your pilot (like the Claude app): it shows every step, what each space
> reported and sign-ins waiting on you, with your spaces live alongside. A
> notification opens the conversation it belongs to. Review what agents
> changed file by file (revert any file, or open it in Zed), keep notes with
> your crew in an Obsidian-compatible **Notes** app, and give any space its
> own KDE virtual desktop. Rebuilt in Qt Quick, so it feels native. Before that
> (M10): Ask Hangar, the main agent from anywhere. Press a shortcut,
> optionally with a screenshot or the window you're in, type or speak your
> question, and the pilot answers. Before that (M9): teams and the pilot. A space can hold several agents,
> each with its own mouse, keyboard and browser tab on the shared desktop.
> Ask the pilot (your main agent) anything, and it answers or runs teams
> across spaces, passing messages between them. Before that (M8): an installable OS for the spare PC (Fedora Kinoite + Hangar,
> built in GitHub Actions). Agent images are pulled from your GitHub registry
> and harnesses are installed on demand. Before that (M7): workspace snapshots with rollback, per-agent network
> allowlists, and research / coding / office flavors whose apps agents can
> read through the accessibility tree. Before that (M6): a first-boot setup wizard (accounts, local models picked
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

Hangar's interface is a set of native desktop apps (Qt: Qt Quick for Hangar
and Notes, widgets for the rest), each with its own window and taskbar icon,
on top of the daemon's API:

| app | what it's for |
|---|---|
| **Hangar** | your conversations with the pilot, your spaces live alongside, review, the overview |
| **Notes** | your Markdown notes and your agents' findings (Obsidian-compatible, below) |
| **Passwords** | the vault, modelled on Apple's Passwords app (see below) |
| **Accounts** | subscriptions and API keys; sign-ins run in a panel with the official link |
| **Models** | LM Studio models, downloads, server |
| **Hangar Settings** | the pilot, notes access, voice, notifications, network access (QR code for your phone) |
| **Hangar Setup** | the first-boot wizard (above) |

```bash
uv sync --extra apps          # PySide6 (Qt), once
uv run hangar open            # Hangar; also: notes | passwords | accounts | models | settings
uv run hangar overview        # every space at once, full window (Esc closes)
```

### Hangar: chat first

The window has three parts:

- **Chats** (left): every conversation with your pilot, newest first, with a
  dot on the one it's working on. Below them, your **spaces** as live
  thumbnails with each agent's coloured avatar.
- **The conversation** (middle): what you asked, every step the pilot takes
  ("Started a research space: lisbon", "wren is comparing hotels"), each
  space's report as a card, and the pilot's answers in Markdown. Sign-in
  requests from any agent wait at the top with **Sign in** / **Deny**. Ask a
  follow-up in the same chat and the pilot has the whole history. Attach a
  screenshot with the camera button.
- **A space** (right, when you pick one from the sidebar, a step or a report
  card): its live desktop with every agent's named cursor, **Take over** /
  **Hand back**, its **crew** (each agent's role, state and latest report),
  **Changes** (below), and a box to give it a task directly.

**Notifications open the conversation.** When the pilot answers, the tray
says so; click it and Hangar opens on that chat. On the Hangar OS a space's
desktop notification ("lisbon finished") opens the chat that started it, or
the space itself. `hangar-app home --thread ID` / `--space NAME` do the same
from a script.

**Changes (code review).** Every task starts with a workspace snapshot, so
Hangar can show exactly what the crew changed since: added, modified and
deleted files with line diffs. **Revert** puts one file back; **Open in
editor** opens the agent's shared folder (your real files) or a copy of its
workspace in [Zed](https://zed.dev), which the Hangar OS installs on first
boot (any editor on a Mac).

**The overview (the Spaces key).** Every space at once, live and large;
click one to open it. In Hangar it's the ⌗ button or Meta+S / Ctrl+↑; on the
Hangar OS, Meta+S (or the Mission Control key on Apple keyboards) works from
anywhere ([os/files/etc/xdg/kglobalshortcutsrc](os/files/etc/xdg/kglobalshortcutsrc)).
Launching Hangar again (or pressing the key) signals the running one.

**A space on its own desktop.** **Own desktop** pops a space out full screen
onto a KDE virtual desktop named "✦ lisbon", so it sits next to your own
desktops in the pager, KWin's overview and Meta+Ctrl+arrows. Close it and the
desktop goes away. (On a Mac it's just a separate window.)

Agents' desktops are drawn by a small native VNC client
([hangar/apps/vnc.py](hangar/apps/vnc.py)) through the daemon's token-checked
proxy. While you've taken over, your mouse and keyboard go to the agent's
desktop (Cmd/Ctrl shortcuts included).

### Notes

Notes is a folder of Markdown files, `~/Notes`, the same format as
Obsidian: open the folder in Obsidian (or any editor) and it just works.
`[[Title]]` links another note (click it to follow, or to create it), and
`#tags` are picked up. Each note shows what links to it. Deleting moves a
note to `Notes/.trash`. **Ask pilot** hands the note to the pilot in a new chat.

Agents get three tools: `notes_search`, `notes_read` and `notes_write`. They
keep what they find in their own folder, `Notes/Agents/<space>/`, and those
notes appear in the app marked with the space. What they can see is up to
you (Settings → Notes):

| setting | agents can |
|---|---|
| **own** (default) | read and write only `Notes/Agents/<their space>/`; the pilot can read all of `Notes/Agents/` |
| **read** | also search and read the rest of your notes (still write only their folder) |
| **off** | no notes at all |

If an agent changes the note you have open, it reloads and tells you who did it.

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
uv run hangar destroy scout        # removes it, its files and snapshots (--keep-home to keep)
uv run hangar new reader --share ~/Documents/papers           # read-only at ~/shared/papers
uv run hangar new writer --share ~/Projects/site:rw           # writable
```

**Shared folders** are the only host files an agent can see. They're mounted
at `/home/agent/shared/<folder>`, read-only unless you add `:rw`. Hangar
refuses to share your whole home folder, `/`, or anything that is or contains
`~/.ssh`, `~/.gnupg`, cloud credentials, keyrings or Hangar's own config.

## Flavors

Every agent starts from the base image (Chromium, a terminal, the Mousepad
text editor, the desktop-control MCP server). Flavors add apps on top:

| flavor | adds |
|---|---|
| `base` | browser, terminal, text editor |
| `research` | PDF and image viewers (Evince, Ristretto), `pdftotext` |
| `coding` | compilers, Python dev headers + `uv`, GitHub CLI, Geany, neovim, sqlite |
| `office` | LibreOffice Writer, Calc and Impress (GTK front end), document fonts, PDF viewer |

```bash
make flavors                       # build them all (or: make flavor F=office)
uv run hangar flavors              # what's built
uv run hangar new writer --flavor office --model claude --task "draft a cover letter in Writer"
```

### Adding a flavor

1. Create `images/flavors/<name>/Containerfile`:

   ```dockerfile
   ARG BASE=localhost/hangar-base:dev
   FROM ${BASE}
   USER root
   RUN apt-get update && apt-get install -y --no-install-recommends gimp inkscape \
       && rm -rf /var/lib/apt/lists/*
   USER agent
   LABEL dev.hangar.flavor="design" \
         dev.hangar.flavor.description="Graphics: GIMP and Inkscape"
   ```

2. `make flavor F=design`, then `hangar new artist --flavor design`.

The `dev.hangar.flavor` label is how Hangar finds it: it shows up in
`hangar flavors` and the New agent form, with no code changes. Prefer
GTK or Qt apps. They expose their UI to the accessibility tools below, so
agents can use them by element instead of by pixel. Keep to Debian
packages (or anything multi-arch) so the flavor builds on both arm64 and amd64.

## Desktop apps by element (accessibility)

Like the browser snapshot, but for desktop apps. Each agent's session runs
the AT-SPI accessibility bus, and `app_snapshot` lists an app's buttons,
menus and text fields as numbered lines that `app_click` / `app_type` act on:

```
window "Untitled 1 - LibreOffice Writer" (app soffice)
  [12] push button "Bold"
  [40] paragraph "" (editable, focused)
```

It works for GTK and Qt apps and LibreOffice. For anything without an
accessibility tree, agents fall back to screenshot + click.

## Teams and the pilot

### Several agents in one space

A **space** is one desktop: one container, browser and workspace. It can
hold several **agents** working together:

```bash
uv run hangar new pr-12 --model claude --task "review PR #12"
uv run hangar agent add pr-12 --role tester --model local:qwen/qwen3.5-9b --task "run the tests"
uv run hangar agent list pr-12
uv run hangar run pr-12 --agent wren "now check the CI logs"
```

- **Their own mouse and keyboard.** The desktop's X server supports multiple
  pointers, so each agent gets its own mouse+keyboard pair and they can click
  and type at the same time without moving each other's cursor. The original
  pointer stays yours, so you can work alongside them.
- **A colour and a name.** Agents added without a name get a friendly one (wren,
  juno, milo…). The live view draws every agent's cursor in its colour with
  its name.
- **One window at a time.** Most apps have a single text cursor, so a light lock
  stops two agents typing into the same window; the second is told who's
  using it and works elsewhere. The pilot is told not to split work that way.
- **Their own browser tab.** Teammates share the browser's sign-ins but never
  each other's page.
- **Desktop-app typing goes through the accessibility API**, not a keyboard, so
  it can't collide either.

### Messages

Agents have `send_message`, `read_messages` and `team` tools. The rules are
enforced by the daemon:
- teammates in a space can message each other,
- anyone can message the pilot,
- only the pilot can message another space's agents. So when work in two
  spaces is related, the pilot passes along what matters, and a stray
  agent can't reach into another space.

See them with `hangar messages <space>` or the space's **Messages** tab.

### Ask Hangar (shortcuts, screenshots, voice)

The quickest way to reach the pilot, from anywhere:

| shortcut (Hangar OS) | what happens |
|---|---|
| **Meta+Space** | the Ask panel, nothing attached |
| **Print** or **Meta+Shift+S** | drag a box (or click a window), then the Ask panel with that screenshot. It's also saved to Pictures/Screenshots and copied, like any screenshot, so Esc just keeps the screenshot |
| **Meta+Shift+Space** | the Ask panel about the window you're in: its title and page are offered |
| tray icon ✦ | the same three, plus Open Hangar / Passwords / Settings |

Each piece of context is a chip you click to include or leave out. Only what
you explicitly captured starts switched on: the screenshot, or the window and
page for "ask about this". Selected text is offered but off. Nothing is sent
until you press Ask. The answer appears in the panel, and as a notification if
you've closed it. Spectacle, KDE's own screenshot app, moves to Meta+Print.
All of these shortcuts can be changed in System Settings → Shortcuts.

On your Mac (dev): `uv run --extra apps hangar-app ask --tray` puts ✦ in the
menu bar. macOS asks once for Screen Recording (screenshots) and
Accessibility (window titles and browser URLs) permission.

**Voice (optional).** Hangar Settings → Ask Hangar → download a speech model
(tiny 75 MB … medium 1.5 GB; small is a good default). A 🎙 button then
appears in the panel. Speech is transcribed on the computer with Whisper:
audio stays in memory, is never saved or sent, and you can edit the text
before asking. Nothing is downloaded unless you choose to.

How context reaches the pilot: the screenshot is copied into its
workspace. Claude Code is told to look at it, Codex gets it as an image
(`-i`), and the local loop sends it to vision models. The window, page and
selection go in as text under "What I'm looking at".

### The pilot

Your main agent. Ask it anything: the **Ask Hangar…** box in the Hangar
app, or

```bash
uv run hangar ask "review PR #12 and figure out why CI fails" --wait
```

It answers simple things itself. For bigger jobs it plans the work, creates
spaces with teams (each agent with a role and a task), checks their reports,
relays messages between spaces, and gives you one answer, also as a notification.
It runs in its own space and remembers the conversation (`--new` starts over).

You choose its model, and what it may use, in **Hangar Settings →
Orchestration** (or `hangar pilot set`):

```bash
uv run hangar pilot set claude --allow local:qwen/qwen3.5-9b --allow claude@personal --max-agents 4
```

What it can't do, enforced by the daemon rather than just asked:
- use a model you haven't allowed,
- run more agents than your limit,
- change spaces you created (it can read and message them),
- widen internet access (with the "only named sites" policy, its spaces get an allowlist),
- approve password requests (those always come to you).



Before every task (`hangar run`, or Run in the Hangar app), Hangar copies the
agent's `~/workspace` to the host (`~/.local/state/hangar/snapshots/`,
outside the agent's reach). Undo whatever the agent did:

```bash
uv run hangar snapshots scout              # list (the last 20 are kept)
uv run hangar rollback scout [<id>]        # restore (default: the latest)
uv run hangar snapshot scout -m "before the big refactor"
```

A rollback snapshots the current workspace first, so it can be undone too. The
Hangar app has the same under the agent's **History** tab. Writable shared
folders aren't included, since they're your files on your disk. Workspaces
over 2 GB are skipped (set `HANGAR_SNAPSHOT_MAX_GB` to change that).

## Network allowlists

By default agents have open internet access. To limit one to certain sites:

```bash
uv run hangar new scout --allow github.com --allow wikipedia.org --model claude --task "…"
uv run hangar new hermit --offline          # nothing but its AI provider and local models
uv run hangar net scout                     # allowed sites + recent allowed/blocked connections
uv run hangar net scout --allow arxiv.org   # applies right away, no restart
```

Such an agent sits on its own internal network with no route out. Its only
exit is a small per-agent gateway ([images/gateway](images/gateway)) that
proxies HTTP(S) to allowed domains (subdomains included) and refuses
everything else. It also refuses LAN and private addresses unless you name
them. The agent's AI provider (Anthropic or OpenAI), LM Studio and the daemon
are always reachable. The allowlist file lives on the host, mounted read-only
into the gateway, so the agent can't edit it. Blocked attempts show up in
`hangar net` and in the agent's **Network** tab, where one click allows them.


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

**Harnesses are installed only when you need them.** Claude Code and Codex
aren't in the agent images (together they're about 1.1 GB). The first time you
connect an account for one (or create an agent that needs it), Hangar installs
it once into a shared volume, mounted read-only into the agents that use it.
Local-model agents need neither.

```bash
uv run hangar harness list                 # installed versions
uv run hangar harness update claude        # newest version; running agents keep theirs until restarted
uv run hangar harness install codex | remove codex
```

Updating a harness doesn't need a new OS image.

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
| `notes_search`, `notes_read`, `notes_write` | the user's notes, as far as Settings → Notes allows (see Notes) |

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

## Installing on the spare desktop

The Hangar machine runs **Hangar OS**: Fedora Kinoite (KDE Plasma,
image-based: read-only system, atomic updates, one-command rollback) with
Hangar built in. You boot to a normal desktop with Hangar and Passwords in
the taskbar. Agents run in rootless Podman, and LM Studio runs headless on
the host. Everything is built in GitHub Actions, never on your laptop.

### 1. Build it (once, in GitHub)

1. Push this repo to GitHub (it can be private).
2. **Actions** tab → enable workflows. Two run on every push to `main`:
   - **agent images**: the agent desktops (base, gateway, research, coding,
     office) for amd64 and arm64 → `ghcr.io/<you>/hangar-*:latest`
   - **OS image**: Hangar OS for x86_64 → `ghcr.io/<you>/hangar-os:latest`
3. In your GitHub profile → **Packages**, set each `hangar-*` package to
   **Public**. They contain no secrets, and the PC can then download them without
   a GitHub login.
4. **Actions → OS image → Run workflow**, tick **Also build the installer ISO**.
   About 30 minutes later, download `hangar-os-iso` from the run's artifacts.

### 2. Install (on the PC)

1. Write `hangar-os.iso` to a USB stick (Fedora Media Writer, balenaEtcher, or
   `dd`), boot the PC from it.
2. The installer asks for the disk (tick **Encrypt my data**: agents' accounts
   and your vault live on it), your user account and password, language and time zone.
3. Reboot and log in. **Hangar Setup** opens full screen: network, AI accounts,
   local models (it installs LM Studio's headless server and recommends models
   for your GPU/RAM), phone notifications. Meanwhile the agent desktops download
   in the background (about 3 GB).
4. Done: Hangar is in the taskbar, and the Spaces key (Meta+S, or Mission Control
   on an Apple keyboard) shows every agent.

**If the ISO gives you trouble**, there's another route to the same result:
install **Fedora Kinoite 45** from its official ISO, log in, then

```bash
sudo bootc switch ghcr.io/<you>/hangar-os:latest
```
```bash
systemctl reboot
```

### Updating and rolling back

```bash
sudo bootc upgrade && systemctl reboot     # new OS image from your registry (built by CI)
sudo bootc rollback && systemctl reboot    # back to the previous OS image
hangar harness update claude               # harnesses update on their own schedule
hangar images pull --all                   # newest agent desktops
```

Updates swap the whole system image atomically. Your home folder, agents,
accounts, vault and settings live outside it and are untouched.

### On the Hangar OS vs. on your Mac

| | Mac (dev) | Hangar OS |
|---|---|---|
| agents | Docker Desktop | rootless Podman (same Docker API) |
| agent images | built locally (`make images`) | pulled from `ghcr.io/<you>` |
| daemon | started on demand | systemd user service, from login |
| LM Studio | the Mac app | headless server, installed by Setup |
| setup wizard | a window; changes nothing on your Mac | full screen on first login; applies language, keyboard, time zone, Wi-Fi |
| notifications | ntfy / Slack / Telegram | the same, plus desktop notifications |

### Not yet verified on real hardware

These can only be checked by installing on the PC, so expect a round of fixes:
- **Agents reaching the daemon and LM Studio** through rootless Podman's
  `host-gateway` (the daemon listens on all interfaces, with the firewall
  closing ports above 1024 to the LAN).
- **The installer asking the questions** (disk, encryption, user), which
  depends on bootc-image-builder's interactive-module support.
- **Branding on screen:** the boot splash (needs the initramfs rebuild CI
  does) and the login screen background.
- **NVIDIA GPUs:** the image has no NVIDIA driver yet, so LM Studio would run
  on the CPU there. AMD and Intel GPUs work with the built-in drivers.
- **Spaces as KDE virtual desktops** (KWin's D-Bus and scripting APIs, in
  [hangar/kde.py](hangar/kde.py)), clickable desktop notifications
  (`notify-send --action`), and Zed installing from Flathub on first boot.

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

## Roadmap

M1 hidden desktop + MCP · M2 `hangar` CLI, many agents · M3 accounts, LM Studio,
local harness · M4 daemon, reports, notifications · M5 Spaces UI · M6 setup
wizard · M7 snapshots, allowlists, flavors · M8 bootc OS image + ISO

M9 teams + the pilot ·

M10 Ask Hangar ·

M11 chat-first Hangar (Qt Quick), review + Zed, Notes, spaces on KDE desktops ·

Next: install on the real PC and fix what it finds; an NVIDIA variant of the
OS image; the Hangar Browser (an agent-first browser shell) as a stretch goal.
