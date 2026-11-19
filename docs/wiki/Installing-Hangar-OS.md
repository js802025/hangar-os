# Installing Hangar OS

You build your own copy of Hangar OS in GitHub Actions (no build machine
needed), write it to a USB stick, and install it like any Linux distribution.

## What you need

- **An x86_64 PC.** 16 GB of RAM is a workable minimum; 32 GB or more
  lets you run good AI models on the machine itself.
- **Disk:** 100 GB or more. Agent desktops are about 3 GB, and local models are
  4–20 GB each.
- **A GPU is optional.** AMD and Intel graphics work out of the box. NVIDIA
  cards aren't supported yet: local models would run on the CPU, slowly.
- **A USB stick** of 8 GB or more, and a GitHub account.

Installing replaces everything on the PC's disk.

## 1. Build it (once, in GitHub)

1. Fork or push the repository to your GitHub account (it can be private).
2. Open the **Actions** tab and enable workflows. Two run on every push to `main`:
   - **agent images**: the agents' desktops, published as `ghcr.io/<you>/hangar-*`
   - **OS image**: Hangar OS itself, `ghcr.io/<you>/hangar-os`
3. In your GitHub profile → **Packages**, set each `hangar-*` package to
   **Public**. They contain no secrets, and the PC can then download them
   without signing in to GitHub.
4. **Actions → OS image → Run workflow**, and tick **Also build the installer
   ISO**. About 30 minutes later, download `hangar-os-iso` from the run's
   artifacts and unzip it.

## 2. Install it

1. Write `hangar-os.iso` to the USB stick with
   [Fedora Media Writer](https://fedoraproject.org/workstation/download) or
   balenaEtcher.
2. Boot the PC from the stick (usually F12, F11 or Esc at power-on picks the
   boot device).
3. The installer asks for the disk, your user account and password, language
   and time zone. **Tick "Encrypt my data"**: your agents' accounts and your
   password vault live on this disk.
4. Reboot, remove the stick and log in. [First boot](First-boot) takes it from there.

## If the ISO gives you trouble

There's another route to the same system. Install **Fedora Kinoite** from its
official ISO, log in, open a terminal (Konsole) and run:

```bash
sudo bootc switch ghcr.io/<you>/hangar-os:latest
```
```bash
systemctl reboot
```

After the reboot you're on Hangar OS, and updates come from your own registry
from then on.
