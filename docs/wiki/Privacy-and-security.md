# Privacy and security

Agents act on your behalf, so Hangar is built to keep each one in its lane.

## Each agent has its own computer

Every space is a separate container with its own hidden desktop. It never
touches your screen, mouse or keyboard, and spaces can't watch, drive or
message each other (only the pilot passes messages between spaces).

## Your files

A space sees none of your files unless you share a folder with it, read-only
unless you say otherwise. Hangar refuses to share your whole home folder, `/`,
or anything containing SSH keys, GPG keys, cloud credentials, keyrings or
Hangar's own settings. Your computer's keyring, passwords and SSH keys never
enter an agent.

## Your accounts

- **AI subscriptions** sign in through each company's official login;
  Hangar never sees those passwords. Each account's login is mounted only into
  the agents assigned to it.
- **Website passwords** stay in Hangar's encrypted vault; agents ask and you
  approve (see [Passwords](Passwords)).
- Nothing secret is ever built into the system image.

## The internet

By default spaces have open internet access. You can limit a space to named
websites: it then sits on a private network whose only way out is a small
gateway that allows those sites and nothing else (including your home
network), and blocked attempts are shown to you. In Settings you can make
every space the pilot creates work this way.

## What leaves the PC

- If an agent uses a **cloud AI** (Claude, ChatGPT), what it sees and does is
  sent to that company, as with using their apps directly.
- With **local models**, the thinking happens on the PC.
- Voice transcription always happens on the PC.
- Hangar's own settings, vault, notes and snapshots stay on the PC's disk
  (encrypt it during [installation](Installing-Hangar-OS)).

## Limits the pilot can't talk its way around

The daemon, not the AI, enforces: which models may be used, how many agents
run, which spaces the pilot may change, internet limits, and that sign-ins
always need you.

## Undo

Every task starts with a workspace snapshot, so changes can be reviewed and
undone (see [Reviewing changes](Reviewing-changes)).
