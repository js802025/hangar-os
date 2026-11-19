# Hangar's in-app browser

Goal: most web work runs in a browser that lives inside Hangar itself, not in a
VM or a container desktop, driven the way Aside drives pages. Computer use
(screenshots and clicks on a desktop) is the last resort.

## How agents work

1. **Normal work:** commands, files, APIs, `web_search` / `web_fetch`.
2. **Browser use:** the in-app browser, through Playwright code and the page's
   accessibility tree (refs), like Aside. For sites that need input: forms,
   sign-ins, carts, JavaScript-only pages.
3. **Computer use:** a desktop (Linux container) or a Mac computer, only for the
   smallest part nothing above can do.

## What we copy from Aside

Aside (aside.com) is a Chromium-based AI browser. Its agent:

- reads pages as a trimmed accessibility tree with ref IDs,
- acts through a JavaScript REPL running Playwright-style code over the
  Chrome DevTools Protocol (CDP), plus bash,
- uses screenshots only as a fallback,
- keeps agent tabs in the background (never taking focus) at a fixed
  1440×900 viewport,
- is built on **pi-mono** (`pi-agent-core`), with autofill the model never sees.

## Design

- **Engine:** Qt's own Chromium (QtWebEngine, `pyside6-webengine`), inside
  Hangar's process. No VM, no extra browser window. Each space gets its own
  profile (cookies, storage), separate from yours and from other spaces.
- **Background tabs:** each tab is a `QWebEngineView` laid out offscreen at
  1440×900 (`WA_DontShowOnScreen`): fully rendered, never on your screen, never
  taking focus.
- **Watching and taking over:** Hangar's Browser view shows any tab live
  (grabbed natively from the view) and passes your mouse and keyboard to it as
  ordinary Qt input when you take over.
- **Agent control:** QtWebEngine speaks CDP (remote debugging on 127.0.0.1).
  Playwright works on its pages through a small relay that answers the few
  browser-wide calls QtWebEngine doesn't support, and asks Hangar to open new
  tabs (`Target.createTarget`). The relay only shows a space its own tabs.
  Agents reach it through the daemon, with their space's token.
- **Tools:** `tab_open` / `tab_read` (Playwright's accessibility snapshot with
  `[ref=eN]` refs), `tab_click` / `tab_type` / `tab_select` by ref, and
  `tab_run` (Playwright code), from a Playwright session in the agent's
  container. Every harness gets them over MCP.
- **Harness:** local models run on **pi** (`@earendil-works/pi-coding-agent`,
  from pi-mono, which Aside's agent is built on), with Hangar's tools exposed
  directly. Claude Code and Codex keep running on their own harnesses with
  your subscriptions; Hangar doesn't route a Claude subscription through pi.
- **Sign-ins:** `request_login` fills the in-app tab the same way it fills the
  container browser; the model never sees the password.

## What's where

| Piece | File |
|---|---|
| Browser host: profiles, background tabs, control endpoint | `hangar/apps/browser_host.py` |
| Browser view (your tabs native, agents' tabs live) | `hangar/apps/qml/BrowserView.qml`, `TabPicture` in `hangar/apps/home.py` |
| Relay: a space sees only its own tabs | `hangar/browser_relay.py`, routes in `hangar/daemon.py` |
| Agent's Playwright session | `images/tab/repl.mjs` |
| `tab_*` tools, vault fill into tabs | `images/mcp-desktop/hangar_desktop/inapp.py`, `vault_tools.py` |
| pi harness for local models | `hangar/harness.py`, `hangar/prepare.py`, `hangar-agent` |
| Work order and skills | `hangar-agent`, `server.py`, `plugins/hangar*/skills/` |

## Native review

- **Your tabs** are Qt's native QML `WebEngineView`s, GPU-rendered, in their
  own persistent profile (`WebEngineProfilePrototype`, Qt 6.9+'s way). They're
  never visible to agents.
- **Agents' tabs** are offscreen `QWebEngineView`s at 1440×900 (rendered,
  never on screen, never focused). The Browser view shows them with a native
  grab of the view, about 10 times a second, only while you're looking at one.
  Taking over sends ordinary Qt mouse, wheel and key events to the page.
- **Threads:** the control endpoint hands all browser work to the Qt main
  thread through a queued slot.
- **Agent control** is Playwright's own API over Chromium's DevTools protocol;
  the relay only fills the gaps QtWebEngine has (browser-wide calls, opening
  tabs) and enforces each space's scope.
- **pi** gets Hangar's MCP tools with `"exposure": "direct"`, so small models
  call them like built-in tools.
- Known limits: QtWebEngine can't open tabs over CDP (Hangar opens them); a
  space's tabs need Hangar running (the daemon starts it in the background).

## Tested

- `tests/smoke_browser.py` (no containers, no models): an agent opens a
  background tab, reads refs, types by ref and runs Playwright code; the tab
  is in its own space's profile; another space can't list, attach to or close
  it, or reach the browser; an approved login fills the tab and reaches the
  site, and is refused on another site.
- On Apple's container runtime: an agent inside its space used the `tab_*`
  tools through the daemon to search Wikipedia in Hangar's browser.
- Not yet run: a full task by a local model on pi (paused while RAM was
  needed elsewhere); `hangar harness install pi` and the image's Node 22 are
  in place for it.
- QtWebEngine 140 (Chromium 140) from `pyside6-webengine` 6.12.0.140.
