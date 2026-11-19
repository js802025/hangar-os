# Troubleshooting

## Hangar says it can't reach the daemon

The daemon is Hangar's background service. Check and restart it:

```bash
systemctl --user status hangar-daemon
```
```bash
systemctl --user restart hangar-daemon
```

Its log is in `~/.local/state/hangar/daemon.log`.

## A space is stuck or doing the wrong thing

Open it and **Take over** to see what's happening, then **Hand back** or give
it a clearer task. To start over, undo its changes (see
[Reviewing changes](Reviewing-changes)). From a terminal:

```bash
hangar logs <space>
```

## The first task takes ages

The agents' desktops (about 3 GB) download in the background after setup.
Check with:

```bash
systemctl --user status hangar-images
```

## Local models are slow

They're running on the CPU, or the model is too big for your graphics
memory. Pick a smaller one in **Models**. NVIDIA graphics cards aren't
supported yet.

## A shortcut doesn't work

Check System Settings → Keyboard → Shortcuts for a clash with another app.
See [Keyboard shortcuts](Keyboard-shortcuts).

## Not yet verified on real hardware

Hangar OS hasn't been installed on a real PC yet, so these are the likeliest
to need fixes:

- agents reaching the daemon and local models through rootless Podman's
  host networking,
- the installer asking for disk, encryption and user,
- the boot splash and login screen branding,
- NVIDIA graphics (no driver in the image yet),
- KDE shortcuts, and screenshots of a region or window under Wayland,
- a space on its own virtual desktop, and clicking a notification to open
  Hangar,
- Zed installing from Flathub on first boot,
- opening Hangar from your phone (the firewall blocks it; see
  [From another device](From-another-device)).

If you hit one of these, please open an issue with what you saw.
