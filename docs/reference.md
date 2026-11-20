# Hangar reference

The technical details behind Hangar: the CLI, how agents, teams, the pilot
and the vault work, the daemon's API and the tools agents get. For using
Hangar day to day, see the [wiki](https://github.com/js802025/hangar-os/wiki);
for working on Hangar itself, [development.md](development.md).

- [CLI](#using-the-cli) · [Running tasks](#running-tasks) · [Accounts](#accounts) · [Local models](#local-models-lm-studio)
- [Teams and the pilot](#teams-and-the-pilot) · [Snapshots](#snapshots-and-rollback) · [Network allowlists](#network-allowlists)
- [Passwords](#passwords-the-vault) · [Notes](#notes) · [Notifications](#notifications) · [The daemon](#the-daemon)
- [Flavors](#flavors) · [Accessibility](#desktop-apps-by-element-accessibility) · [Agent tools](#desktop-control-mcp-tools) · [Web version](#web-version)

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
([local_agent.py](../images/mcp-desktop/hangar_desktop/local_agent.py)) that gives
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

Your main agent. Ask it anything: in Hangar's chat, with Ask Hangar, or

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

**Picking models.** The control tool `models()` lists the allowed models
cheapest first, each with what it can do (sees the screen, reasoning, size,
local or cloud) and what it costs to run, and the pilot is told to give each
agent the cheapest model that can reliably do that agent's task. The chat
records the choice ("Started space bikes: bikes on Qwen3.5 9B (local), wren
on Claude").

What it can't do, enforced by the daemon rather than just asked:
- use a model you haven't allowed,
- run more agents than your limit,
- change spaces you created (it can read and message them),
- widen internet access (with the "only named sites" policy, its spaces get an allowlist),
- approve password requests (those always come to you).

## Snapshots and rollback

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
exit is a small per-agent gateway ([images/gateway](../images/gateway)) that
proxies HTTP(S) to allowed domains (subdomains included) and refuses
everything else. It also refuses LAN and private addresses unless you name
them. The agent's AI provider (Anthropic or OpenAI), LM Studio and the daemon
are always reachable. The allowlist file lives on the host, mounted read-only
into the gateway, so the agent can't edit it. Blocked attempts show up in
`hangar net` and in the agent's **Network** tab, where one click allows them.

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

## Notes

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

## Hangar for your other agents (`hangar mcp`)

`hangar mcp` serves Hangar over MCP (stdio), the way cua-driver does, so an
agent outside Hangar (Claude Code, Codex, Cursor) can get computer work done
without touching your screen:

It's packaged as a plugin (`plugin/`: the server plus a skill on when and how
to use it), so in Claude Code:

```bash
claude plugin marketplace add js802025/hangar-os
claude plugin install hangar@hangar
```

Any other MCP client (Codex, Cursor, ...) runs `plugin/bin/hangar-mcp` (or
`hangar mcp`) as a stdio server; the launcher finds Hangar OS's `hangar` or
Hangar for Mac's checkout. It runs as you, like the CLI, so it needs no token.
Hangar's app is the interface for what these agents do: the spaces they start
show up there ("started by Claude Code"), to watch and take over.

| tool | what it does |
|---|---|
| `do_task(task, computer, space, model, wait_seconds)` | Hangar does the job in a space (a Linux desktop, or `computer="mac"` for a Mac VM) and returns the agent's final report |
| `wait(space)`, `look(space)` | wait for the result; see the space's screen and latest reports |
| `spaces()`, `stop_space(space, delete)` | list; stop or remove |
| `mac_start`, `mac_screenshot`, `mac_click`, `mac_type`, `mac_key`, `mac_scroll`, `mac_drag` | drive a Mac VM yourself, step by step (coordinates are the screenshot's pixels) |
| `mac_show`, `mac_stop` | open the VM's window for you (to watch or sign in); shut it down |

Outside agents get the pilot's limits (Settings, Orchestration) and share its
budget: only the models you allowed (or the pilot's own), at most `max_agents`
running agents across the pilot's spaces and theirs, your internet policy for
new spaces, and they can give tasks to or stop only spaces an outside agent
started (any space can be listed and looked at). `do_task` uses the pilot's
model unless given one. A Mac VM lent to a space
isn't driven directly; `look` that space instead.

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

## Agent tools and skills

Agents work the cheapest reliable way:

1. **Normal work:** commands, files, `web_search` and `web_fetch`.
2. **Browser use:** Hangar's own browser for sites that need input, with the
   `tab_*` tools (accessibility tree with refs, Playwright code). Its tabs run
   in the background, in each space's own profile, with no desktop. See
   [browser.md](browser.md).
3. **Computer use:** a desktop or a Mac computer, only for the smallest part
   nothing above can do.

Local models run on **pi** (the harness Aside's browser agent is built on) once
it's installed: `hangar harness install pi`.

Every agent gets Hangar's **skills**, which explain all this: `hangar` (its
computer, tools, teammates, reports, notes, sign-ins and limits) and, for the
pilot, `hangar-pilot` (when to answer directly, how to split a job, pick
models and write tasks, and how to answer). They live in
`images/base/rootfs/opt/hangar/plugins/`. Claude Code loads them as plugins
for each task (`--plugin-dir`), Codex finds them in `~/.agents/skills`, and
local models get them in their system prompt.

## Desktop-control MCP tools

| tool | what it does |
|---|---|
| `tab_open`, `tab_go`, `tab_read` | Hangar's browser: open or go to a page, read it as an accessibility tree with `[ref=eN]` refs |
| `tab_click`, `tab_type`, `tab_select` | act on a ref |
| `tab_run` | Playwright code against the agent's tab (`page`, `context`, `ref(id)`) |
| `tab_tabs`, `tab_switch`, `tab_close`, `tab_screenshot` | the space's tabs; a picture only as a fallback |
| `run_command` | a bash command in the agent's container (its workspace by default) |
| `read_file`, `write_file` | files, relative to the workspace |
| `web_search`, `web_fetch` | search the web and read pages or APIs without a browser (through the space's network rules) |
| `screenshot` | PNG of the whole desktop |
| `screen_info` | size, mouse position, active window title |
| `click`, `move_mouse`, `drag`, `scroll` | mouse (screen pixels from top-left) |
| `type_text`, `key` | keyboard (`key("ctrl+l Return")`) |
| `open_app` | `browser` (args = URL), `terminal`, or any command |
| `read_clipboard`, `wait` | |
| `report` | post a status update / final summary to the user |
| `notes_search`, `notes_read`, `notes_write` | the user's notes, as far as Settings → Notes allows (see Notes) |

### Browser tools (for sites that need input)

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

## Web version

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

The UI is plain HTML, CSS and JavaScript in [hangar/ui/](../hangar/ui), with no build
step. Edit a file and reload the page.

Each web page can also open on its own at `/app/<name>`
(`uv run hangar open passwords --web` opens it in a Chrome app window).
