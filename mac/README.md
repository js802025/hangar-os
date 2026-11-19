# Hangar for Mac

A native Mac app that brings the Hangar OS idea to people who aren't ready to
install a new operating system: your pilot and a crew of agents, each on a
computer of its own, living in **macOS Spaces** next to your own desktops.

This folder holds the design and the Mac app (`HangarMac/`). The engine work
lives with the rest of Hangar (`hangar/apple.py`, `hangar/macvm.py`) and is
only switched on for the Mac. It's early: nothing here ships yet.

## The idea in one picture

```
 Your Mac                                         Mission Control
 ┌───────────────┬──────────────┬──────────────┬──────────────┐
 │ Desktop 1     │ Desktop 2    │ ✦ bikes      │ ✦ inbox      │
 │ (yours)       │ (yours)      │ agent's      │ agent's      │
 │               │              │ desktop,     │ desktop,     │
 │ menu bar: ✦   │              │ full screen  │ full screen  │
 └───────────────┴──────────────┴──────────────┴──────────────┘
        Ctrl+→ / three-finger swipe moves between them, like any Space
```

- A **menu bar app** (✦) with Ask Hangar (a global shortcut, like ⌘Space),
  your conversations with the pilot, and a list of spaces.
- Each **space** pops out into a native full-screen window. macOS gives every
  full-screen window **its own Space**, so agents show up in Mission Control,
  the Ctrl+arrow keys and trackpad swipes exactly like your own desktops. This
  is the Mac version of `hangar/kde.py`, which puts a space on a KWin virtual
  desktop of its own.
- Notifications, Notes (plain Markdown, also an Obsidian vault), Passwords and
  review all come over from Hangar OS unchanged, because they live in the
  daemon.

## Where the agents' computers come from

Two kinds of agent computer, chosen per space:

| | Linux computer (default) | Mac computer (opt-in) |
|---|---|---|
| What it is | Hangar's agent image: a Linux desktop with Chromium, LibreOffice and the agent's MCP tools | A real macOS virtual machine |
| Runs on | **Apple's `container`**: each space is its own lightweight Linux VM (Virtualization.framework). No Docker Desktop. | [terminal-mac](https://github.com/govijr/terminal-mac) (MIT), also on Virtualization.framework |
| How many | As many as memory allows | **Two at most**: Apple caps running macOS VMs at two per Mac |
| Good for | Browsing, documents, code: nearly everything | Work that needs Mac apps |
| Ask for it | (default) | `hangar new NAME --computer mac`, or the pilot picks `computer: "mac"` |

### Apple's container (`hangar/apple.py`)

Hangar OS drives Podman through the Docker SDK. On a Mac, `HANGAR_RUNTIME=apple`
swaps in `hangar/apple.py`, a small stand-in for the parts of that SDK Hangar
uses, built on the `container` CLI. Nothing else in Hangar changes, and Hangar
OS never loads it. What it smooths over:

| Docker does | Apple's container | So Hangar for Mac |
|---|---|---|
| Picks free host ports | Needs a host port | Picks free `127.0.0.1` ports itself |
| `--add-host`, DNS between containers | Neither (custom networks) | Writes `/etc/hosts` entries into each container after it starts |
| `host-gateway` reaches the host's localhost | Can't reach `127.0.0.1` | The daemon relays its port and LM Studio's onto the vmnet gateway (`192.168.64.1`), reachable only from this Mac's VMs |
| Fills a new named volume from the image | Empty, root-owned ext4 | Seeds each volume from the image once |
| `network connect` after create | Networks fixed at start | A created container is only started (really created) at `start()` |
| `--security-opt no-new-privileges` | Not available | Each container is its own VM, with all capabilities dropped, as on the OS |
| An event stream | None | Polls |

Allowlisted spaces work the same way: an internal (host-only) network, and a
gateway container on both networks that only proxies allowed sites.

Tested on macOS 26.6 (Apple silicon) with `container` 1.7.1: the M1, M2, M4,
M7 and M9 smoke tests pass on it unchanged (desktops and browsers over MCP,
several agents at once, the daemon, tokens, noVNC and events, snapshots and
rollback, accessibility tools, network allowlists with live edits, teams and
the pilot). A space starts in about 3 seconds.

### Mac computers (`hangar/macvm.py`)

A space with a Mac computer still has its container: the agent, its files,
reports and notes live there. Only its screen is the Mac. The agent's screen
tools (screenshot, click, type, key, scroll, drag) go to the daemon
(`/api/agents/NAME/computer/...`, with the space's own token), which drives
the VM with `terminal-mac action`. The Linux-only tools (browser_*, app_*,
open_app, and the vault's fill) are removed for that space, and the agent is
told it's on a Mac.

- **Setting one up:** `hangar mac setup` (or "Set Up a Mac Computer…" in the
  menu) downloads macOS and opens the VM's window, where you finish setup like
  a new Mac. Turn on automatic login so agents never wait at the login screen.
- **Lending it:** one space at a time. It starts with the space and stops
  with it, freeing one of Apple's two slots.
- **Sign-ins:** the vault can't fill a password into a Mac, so the agent asks
  you to sign in on it ("Take over the Mac" in the menu).
- **Not yet tested against a real VM:** the terminal-mac commands follow its
  documentation and are covered by a test with a fake `terminal-mac`
  (`tests/smoke_mac.py`).

## Apple Passwords

macOS has no API for an app to read Apple Passwords (iCloud Keychain): an
app only sees its own keychain items. That's the right rule for Hangar too,
so sign-ins go through **AutoFill**, with you in the loop:

1. An agent calls `request_login(site)` as on Hangar OS.
2. A notification appears in the corner ("bikes wants to sign in to
   pedalswap.com"), with **Sign in…** and **Deny**. Nothing takes your focus or
   pops up over your work. Deny answers from the notification.
3. Sign in… (or clicking the notification, the ✦ menu, or "Sign in" in
   Hangar's window) opens Hangar's sign-in prompt, in Hangar's own look: the
   agent, the site and why, with a username and a password field marked for
   AutoFill. macOS offers your saved logins there ("Passwords…", Touch ID).
4. **Sign in** sends that login to the daemon once
   (`POST /api/vault/requests/ID/approve {"login": {...}}`), which types it
   into the agent's browser through the usual fill path, and only if the
   browser is on that site. It isn't stored or logged, and the agent never
   sees it (`tests/smoke_vault.py`, step 6).
5. Verification codes use a one-time-code field, which Passwords fills from
   its own 2FA codes. Logins saved in Hangar's vault can still be picked.

Passkeys can't be lent: they stay on your devices, so sites that only offer
passkeys need you to sign in yourself ("Take over").

## Mapping Hangar OS to the Mac

| Hangar OS | Hangar for Mac |
|---|---|
| KDE panel icon, Plasma notifications | Menu bar extra, `UserNotifications` |
| Meta+Space: Ask Hangar | A global hotkey (Carbon `RegisterEventHotKey`, no Accessibility permission needed) |
| "Attach this window" / screenshot | ScreenCaptureKit (needs the Screen Recording permission, asked only when used) |
| Space on its own KWin virtual desktop | Space in a full-screen window, so its own macOS Space |
| Hangar app (QML) | SwiftUI shell; the daemon API stays the same |
| Passwords vault | Apple Passwords through AutoFill (above), or the same vault |
| systemd user service for the daemon | A `launchd` agent (`SMAppService`) running the bundled daemon |
| Rollback of the whole OS | Not applicable; snapshots of each space still work |

## How macOS Spaces can be used

| Approach | Works? | Notes |
|---|---|---|
| Full-screen window (`toggleFullScreen`) | **Yes, public API** | macOS creates a Space for it, named after the window, and removes it when the window leaves full screen. This is what the spike does. |
| `collectionBehavior` (`.fullScreenPrimary`, `.canJoinAllSpaces`) | Yes | Controls whether a window may go full screen, or follows you to every Space (useful for an "agent at work" HUD). |
| Creating a normal (non-full-screen) Space, moving windows between Spaces | **Private only** (`CGSSpaceCreate`, `CGSAddWindowsToSpaces` in SkyLight) | yabai does this, but needs SIP partly disabled. Not for a product. |
| Two spaces side by side in one Space | Yes | Split View of two full-screen windows works with no extra code. |

## Trust model on a Mac

The Hangar OS promises carry over, because they are enforced by the daemon
and the containers, not by the desktop:

- Agents never see your screen, mouse or keyboard: their desktops are inside
  containers or VMs. The Mac app shows them; it does not give them your
  display.
- Only folders you share with a space are mounted into it. The daemon already
  refuses your home folder, SSH keys, keyrings and cloud credentials.
- Sign-ins go through the vault with your approval. On a Mac that approval
  can be Touch ID.
- The Mac app needs no Accessibility permission. Screen Recording is asked
  for only when you attach a screenshot or a window to a question.

## Distribution

- A signed, notarized `.app` with the daemon bundled (a frozen Python build,
  or the daemon run with `uv` on first launch).
- First launch: the same setup wizard (accounts, models), then "Install the
  agent runtime": Apple's `container` (macOS 26, Apple silicon).
- The agent images are pulled from GitHub Container Registry (arm64 builds,
  which the roadmap already needs).

## Open questions

- **Images:** `make mac-images` builds them with `container build`. A real
  release would pull arm64 images from GitHub Container Registry instead.
- **Accounts across runtimes:** AI account sign-ins live in volumes, so an
  account signed in under Docker isn't there under Apple's container. Sign in
  again (`hangar accounts login`) after switching.
- **Local models:** Ollama or MLX on the Mac itself, so they get the GPU.
- **Live view:** the app polls screenshots. A real build should speak VNC
  (RFB) to Linux spaces, as the Qt app does (`hangar/apps/vnc.py`).

## Trying it

```bash
brew install container                       # Apple's container (macOS 26, Apple silicon)
make mac-images                              # build the agent images with it
mac/build-app.sh                             # builds mac/build/Hangar.app
open mac/build/Hangar.app
```

**Hangar.app** starts Hangar on Apple's container and opens Hangar's own
window, with Hangar's browser built in (agents browse there, in the
background, instead of on a desktop; see [docs/browser.md](../docs/browser.md)): the same app as on Hangar OS (chats with the pilot, spaces, review,
overview), without the title bar on a Mac, just the window buttons. Popping a
space out puts it full screen on its own macOS Space. A ✦ in the menu bar
opens Hangar, lists spaces, shows sign-in requests and sets up Mac computers.

`mac/bin/hangar` is the CLI with the same setup (`HANGAR_RUNTIME=apple`). For
Mac computers, install [terminal-mac](https://github.com/govijr/terminal-mac)
and run `mac/bin/hangar mac setup`.

Hangar.app runs Hangar from this checkout, so rebuild it if you move the folder.
