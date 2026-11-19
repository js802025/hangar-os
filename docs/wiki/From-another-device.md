# From another device

Hangar OS is meant to be used at the machine itself. When you're away from
it, you can still keep up.

## Notifications on your phone

Set up **ntfy** in [First boot](First-boot) or Settings → Notifications: scan
the QR code with the free ntfy app, and you'll hear when the pilot finishes,
an agent hits a problem, or something needs you. Slack and Telegram work too.
Setup picks a hard-to-guess topic name for you; public ntfy topics can be
read by anyone who knows the name.

## Checking in from a browser

Hangar has a web version for your phone or another computer on the same
network: every space live, take over, give tasks, approve sign-ins. Turn on
**Settings → Access → open from other devices on my network** and scan the
QR code it shows, which signs your phone in.

> **Known issue:** Hangar OS's firewall closes ports above 1024 to the
> network, which also blocks this. Until that's fixed, open Hangar's port on a
> network you trust (your home Wi-Fi, never public Wi-Fi):
>
> ```bash
> sudo firewall-cmd --permanent --add-port=7700/tcp && sudo firewall-cmd --reload
> ```
