# First boot

The first time you log in, **Hangar Setup** opens full screen. Each step can
be skipped and done later (Settings → Run setup again).

![Setup welcome](images/setup-welcome.png)

1. **Welcome:** language, keyboard and time zone.
2. **Network:** pick a Wi-Fi network if you're not on a cable.
3. **AI accounts:** connect the AI you already pay for. Claude and ChatGPT
   subscriptions sign in through each company's own login page (Hangar never
   sees that password); API keys work too. Connect several, or none if you'll
   only use local models. See [AI accounts and models](AI-accounts-and-models).

   ![AI accounts](images/setup-accounts.png)
4. **Local models:** Hangar looks at your CPU, memory and graphics card and
   recommends models that will run well on this machine, always including one
   that can see the screen. Downloads carry on in the background.

   ![Local models](images/setup-models.png)
5. **Your pilot:** the main agent you'll talk to. Pick its model, and which
   models it may give the agents it starts.

   ![Your pilot](images/setup-pilot.png)
6. **Notifications:** scan a QR code with the free ntfy app on your phone to
   hear when work is done, even away from the PC. Slack and Telegram work too.
7. **Done:** a short tour, and **Say hello to your pilot** opens Hangar.

After that you're at a normal KDE desktop; see [Your desktop](Your-desktop) for what's where.

![Done](images/setup-done.png)

While you go through setup, the agents' desktops (about 3 GB) download in the
background. The first task may wait for them to finish.

Next: [Talking to your pilot](Talking-to-your-pilot).
