# Reviewing changes

Before every task, Hangar takes a snapshot of the space's workspace and keeps
it outside the agent's reach. So you can always see exactly what changed, and
undo it.

![Reviewing changes](images/review.png)

## In Hangar

Open a space and pick **Changes**. You'll see every file the crew added,
changed or deleted since the task started, with the lines that changed.

- **Revert** puts one file back as it was (or removes it, if it was new).
- **Open in editor** opens the work in **Zed**, a fast code editor that
  Hangar OS installs on first boot. If the space works on a folder you shared
  with it, that's your real folder; otherwise Hangar opens a copy of the
  workspace in `~/Hangar/<space>`.

## Rolling back everything

To undo a whole task at once, use the terminal:

```bash
hangar snapshots <space>
```
```bash
hangar rollback <space>
```

A rollback snapshots the current state first, so it can be undone too. The
last 20 snapshots are kept.

**Note:** folders you share with write access are your own files on your own
disk, so they aren't snapshotted. Keep them in Git, or share read-only.
