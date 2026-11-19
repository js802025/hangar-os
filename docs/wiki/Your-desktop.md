# Your desktop

Hangar OS is a full KDE Plasma desktop. Hangar is woven into it rather than
being an app you have to open first.

## The taskbar

**Hangar**, **Notes** and **Passwords** are pinned to the taskbar. The app
menu also has **Accounts**, **Models** and **Hangar Settings**, alongside the
usual KDE apps: the browser, Dolphin (files), Konsole (terminal), Discover
(more apps) and **Zed**, the code editor Hangar uses for
[reviewing changes](Reviewing-changes).

## The tray

The ✦ icon in the system tray is the quickest way to the pilot: click it to
ask, or right-click for a screenshot-and-ask, asking about the current window,
and opening Hangar, Notes, Passwords or Settings.

## Shortcuts

**Meta+Space** asks the pilot from any app, **Print** asks about a screenshot,
and **Meta+S** shows every space at once. All of them are in
[Keyboard shortcuts](Keyboard-shortcuts).

## Notifications

When the pilot finishes, a space needs you, or an agent wants to sign in
somewhere, you get a desktop notification. Click it to open the conversation
it belongs to.

## Virtual desktops

KDE's virtual desktops are yours, but a space can join them too: **Own
desktop** puts a space full screen on a desktop of its own, so you can switch
to your agents' work with Meta+Ctrl+arrows like any other desktop. See
[Spaces and agents](Spaces-and-agents).

## In the background

Two background services make up Hangar: the **daemon** (it runs the agents,
collects their reports and sends notifications) and a downloader that keeps
the agents' desktops up to date. Both start when you log in. The agents
themselves run in rootless Podman containers, and local AI models run in LM
Studio's background server.

## Your files

Your home folder is yours: agents only see folders you
[share with a space](Privacy-and-security). Your notes live in `~/Notes`,
copies of agents' work opened in an editor go to `~/Hangar/`, and screenshots
to Pictures/Screenshots.
