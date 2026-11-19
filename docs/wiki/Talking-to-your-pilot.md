# Talking to your pilot

The **pilot** is your main agent. You tell it what you need; it answers
simple things itself, and for bigger jobs it plans the work, starts a crew of
agents, checks on them, and gives you one answer.

![A conversation with the pilot](images/plan.png)

## In Hangar

Open **Hangar** from the taskbar. On the left are your chats; the middle is
the conversation. Ask in plain words, the way you'd ask a capable assistant:
what you want, any constraints, and what "done" looks like.

As it works, the conversation shows:
- each **step** the pilot takes ("Started a space for it…"). Click one to open
  that space;
- **reports** from the agents as cards;
- **sign-in requests** waiting for you at the top (see [Passwords](Passwords));
- the pilot's **answer** at the end.

Reply in the same chat to follow up: the pilot remembers the conversation.
**New chat** (Ctrl+N) starts fresh. Attach a screenshot with the 📷 button.

![The pilot's answer](images/answer.png)

## From anywhere

You don't need Hangar open to ask. From any app:

| shortcut | what it does |
|---|---|
| **Meta+Space** | the Ask panel |
| **Print** or **Meta+Shift+S** | take a screenshot (drag a box or click a window), then ask about it |
| **Meta+Shift+Space** | ask about the window you're in (its title and web page are offered) |

![Asking from anywhere](images/ask.png)

Everything you could send is shown as a chip; click one to include or leave
it out. Only what you deliberately captured starts switched on, and nothing
is sent until you press **Ask**. The ✦ icon in the system tray does the same
things.

**Voice:** in Settings → Ask Hangar, download a speech model and a 🎙 button
appears. Speech is turned into text on the PC itself, never sent anywhere, and
you can edit the text before asking.

## Notifications

When the pilot finishes, you get a notification; **click it to open that
conversation**. If you set up ntfy, Slack or Telegram, the same reaches your
phone. See [From another device](From-another-device).

## Choosing models

For each agent, the pilot picks **the most efficient model that can do that
part of the job**, from the models you've allowed. Routine browsing,
reading, searching and filling in forms go to small models running on the PC
(free and private); work that needs to see the screen gets a model with
vision; judgement, writing, code and long multi-step work get a bigger cloud
model like Claude. The conversation shows which model each agent got and why.

## Good to know

- The pilot can only use the models you've allowed and run as many agents as
  your limit, set in [Settings](Settings). These are enforced, not suggested.
- It never approves sign-ins; those always come to you.
- You can also give a task to a space directly, without the pilot. See
  [Spaces and agents](Spaces-and-agents).
