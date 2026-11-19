# Notes

**Notes** is a simple notes app for you and your crew. Notes are plain
Markdown files in `~/Notes`, the same format as Obsidian, so you can open the
folder in Obsidian or any editor too.

![Notes](images/notes.png)

- Link notes with `[[Note title]]`. Click a link to follow it, or to create
  the note if it doesn't exist yet.
- `#tags` are picked up, and each note shows what links to it.
- **Edit** and **Read** switch between writing and reading (Ctrl+E).
- Search with Ctrl+F; new note with Ctrl+N.
- Deleting moves a note to `Notes/.trash`.
- **Ask pilot** hands the note to your pilot in a new chat.

## What your crew writes

Agents keep what they find (shortlists, research, summaries) in their own
folder, `Notes/Agents/<space>/`. Those notes appear in the list marked with the
space's name. If an agent changes the note you have open, it reloads and
tells you who changed it.

## What agents can read

You decide, in Settings → Notes:

| setting | agents can |
|---|---|
| **Only their own folder** (default) | read and write only their space's folder; the pilot can read all of `Notes/Agents/` |
| **Read all my notes** | also search and read your notes, but still write only to their own folder |
| **No notes** | nothing |
