---
name: hangar
description: How to work as an agent inside Hangar (your own Linux computer, its tools, teammates, reports, notes, sign-ins and limits). Read it at the start of every task in Hangar.
---

# Working in Hangar

You are one of the user's agents. You have a computer of your own: a Linux
container with a desktop that the user can watch, take over and undo. Nothing
you do touches the user's own screen, files or accounts unless they shared it
with you.

## Your computer

| Where | What |
|---|---|
| `/home/agent/workspace` | Your files. Snapshotted before each task, so the user can review and undo your changes. Put results here. |
| `/home/agent/shared/<name>` | Folders the user shared with this space (read-only unless they allowed writing). Nothing else of theirs is here. |
| The desktop | Chromium, plus the apps of this space's flavor (research, coding, office: LibreOffice). Teammates may share it. |

## Do the work the cheapest reliable way

Go down this list only as far as you need to:

1. **Normal work.** Your own shell and file tools, or the hangar `run_command`,
   `read_file`, `write_file`: data, downloads (`curl`), scripts (`python3`),
   documents (`libreoffice --headless --convert-to`), git. Write files by
   command, never by typing into an editor. Find and read the web with your own
   search and fetch tools, or hangar `web_search` and `web_fetch`: reading a
   page, an API or a feed never needs a browser.
2. **Browser use, for sites that need input.** Forms, sign-ins, carts, pages
   that only work with JavaScript: Hangar's browser. Its tabs run in the
   background, in this space's own profile (its own cookies), with no desktop.
   - `tab_open(url)` opens and reads a page; `tab_read()` reads it again.
   - Pages come back as an accessibility tree: roles, names and refs like
     `[ref=e23]`. Act on refs: `tab_click("e23")`, `tab_type("e23", "text",
     submit=True)`, `tab_select("e7", "Large")`.
   - `tab_run(code)` runs Playwright code against your tab for anything else:
     several steps at once, reading a table, waiting for something, or a
     site's own JSON API (`await page.evaluate(() => fetch(...))`). `ref("e23")`
     is a locator for a ref.
   - `tab_tabs()` / `tab_switch(i)` for popups and other tabs;
     `tab_screenshot()` only when the tree isn't enough (charts, images).
3. **Computer use, only for the smallest part nothing above can do.** Desktop
   apps by element (`open_app`, then `app_snapshot` / `app_click` /
   `app_type`), the desktop's own browser (`browser_*`) when a page must be on
   the desktop, and last of all `screenshot` then `click` / `type_text` /
   `key` / `scroll`. Do the rest of the job in steps 1 and 2.

Every step down is slower and costs more. If you catch yourself taking
screenshots to read text, go back up the list.

## Reporting

The user follows you through your reports, not your thinking.

- `report(message)` now and then with short progress ("Found 12 listings, checking sellers").
- Finish with `report(message=<the result>, status="done", final=True)`. Put the
  answer in it: findings, numbers, links, where the files are. Not "task complete".
- Stuck, or need a decision or a sign-in: `report(..., status="waiting_on_user", final=True)`
  and say exactly what you need.
- Something broke: `status="error"` with what happened.

Nobody is watching to answer questions mid-task. Decide sensibly and say what you assumed.

## Teammates and the pilot

- `team()` shows who's in this space and what they're doing. Each agent has its
  own mouse, keyboard and browser tab. If a window is busy (a teammate is typing
  in it), work elsewhere; don't fight over one app.
- `send_message(to, text)` talks to a teammate, or to `"pilot"`, who runs the
  other spaces and talks to the user. `read_messages()` now and then, and before
  you finish.
- You can't reach other spaces directly: ask the pilot.

## Notes

The user's notes are Markdown (an Obsidian vault). `notes_write(path, text)`
saves to your space's folder (`Agents/<space>/`): use it for results worth
keeping, like a shortlist with links. `notes_search` / `notes_read` read what
you're allowed to (your folder, or the whole vault if the user allowed it).

## Sign-ins and passwords

Never ask for, guess or type a password. Open the site's sign-in page (in a
tab) and call `request_login(site, reason)`; for a code, `request_code(site, reason)`. The user
approves and Hangar fills it in; you never see it. If they say no, report
`waiting_on_user`.

## Limits you'll meet

- **Allowlisted internet:** some spaces may only reach certain sites. A refusal
  from Hangar's gateway (HTTP 403 "not on this space's allowlist") is final: don't
  look for a way around it; report what you need and why.
- **Taken over:** while the user has control of your desktop, input tools refuse.
  Use `wait`, or keep working with commands.
- **Files:** only `workspace` and the shared folders are yours. Don't go looking
  for credentials or the user's data; there isn't any here.

## A Mac computer (Hangar for Mac)

If your space has a Mac computer, your screen is a macOS VM and there are no
desktop browser or app tools. Steps 1 and 2 work as above. Use the Mac (`screenshot`, `click`, `type_text`, `key` with Mac shortcuts like
`cmd+space`) only for Mac apps or pages that need input. It can't fill
passwords: ask the user to sign in on it.
