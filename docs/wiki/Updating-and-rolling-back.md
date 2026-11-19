# Updating and rolling back

Hangar OS is **image-based**: the system is one read-only image, replaced as a
whole on update. An update can't half-apply, and the previous version is kept
so you can go back.

## The system

```bash
sudo bootc upgrade && systemctl reboot
```

This fetches the newest Hangar OS image built by your GitHub Actions. If
something's wrong after an update, go back to the previous one:

```bash
sudo bootc rollback && systemctl reboot
```

Your home folder, agents, AI accounts, vault, notes and settings live outside
the system image and are untouched by both.

## Agent tools and desktops

These update separately, without a new system image:

```bash
hangar harness update claude
```
```bash
hangar images pull --all
```

Running agents keep their current version until they restart.

## Apps

Zed and other Flatpak apps update through Discover, like on any KDE desktop.
