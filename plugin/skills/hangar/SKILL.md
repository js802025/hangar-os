---
name: hangar
description: Use Hangar for computer use that shouldn't happen on the user's own screen - work in a desktop app, a website that needs real input, or anything that needs a Mac (a macOS VM). Hand Hangar the whole task, or drive a Mac VM step by step. Read before the first hangar tool call.
---

# Using Hangar

Hangar runs computer work in its own spaces: isolated Linux desktops, or
macOS virtual machines. None of it touches the user's screen, mouse or
keyboard. The user watches and takes over in the Hangar app, where every
space you start shows up marked with your client's name.

## Pick the lightest way

1. **Do it yourself without a screen** when you can: shell, files, APIs, web
   fetch. Hangar is for what needs a GUI.
2. **Hand Hangar the job (`do_task`)** for anything with a GUI or a website
   that needs input. Describe the whole job and what "done" looks like. Its
   agent works the efficient way (commands and web first, Hangar's browser for
   sites, the screen only where needed) and returns its final report. It costs
   you one tool call, not dozens of screenshots.
   - `computer="mac"` only for work that needs a Mac app (at most two Mac VMs
     run at once). Default `linux`.
   - Reuse a `space` for follow-ups: its files and sign-ins carry over.
   - If it comes back "still working", call `wait(space)`. Use `look(space)`
     to see its screen and latest reports.
3. **Drive a Mac VM yourself** (`mac_*`) only for short, fiddly steps where you
   need to see each result: `mac_start`, then `mac_screenshot`, then act at that
   screenshot's pixel coordinates with `mac_click` / `mac_type` / `mac_key` /
   `mac_scroll` / `mac_drag`, and screenshot again to check. Use Mac shortcuts:
   `cmd+space` then the app's name and `return` to open an app; `cmd+l` for a
   browser's address bar.

## Rules

- Never type the user's passwords or one-time codes. If a sign-in is needed,
  call `mac_show` (or tell the user to open the space in Hangar) and ask the
  user to sign in themselves.
- Stop what you started: `stop_space(space)` when done (`delete=True` only if
  the user doesn't need its files), `mac_stop` for a VM you booted.
- A Mac VM that's lent to a space can't be driven directly; `look` that space.
- You get the same limits as the user's pilot (set in Hangar's Settings): only
  the models they allowed, a cap on running agents shared with the pilot, their
  internet policy for new spaces (pass `sites` when it's limited), and you can
  give tasks to or stop only spaces an outside agent started. If you hit a
  limit, tell the user; don't work around it.
