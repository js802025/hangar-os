# AI accounts and models

Agents need an AI to think with. Hangar works with what you already have.

## Subscriptions and API keys

Open **Accounts** to add:
- a **Claude** subscription (Pro or Max), or an Anthropic API key,
- a **ChatGPT** subscription (Plus or Pro, through Codex), or an OpenAI API key.

Subscriptions sign in through the company's official login: Hangar opens the
link, you sign in in your browser, and paste back the code. Hangar never sees
your password. You can connect several accounts and give different agents
different ones; agents sharing an account also share its usage limits.

The tools agents use with these (Claude Code, Codex) are downloaded only when
you first connect an account that needs them.

## Local models

**Models** manages AI models that run on the PC itself, through LM Studio:
nothing leaves the machine. Setup recommends models that fit your memory and
graphics card; you can download more later. Models that can see images can
use screenshots; others work from page text.

Local models are free to run and private, but smaller than the big cloud
models: great for focused tasks, weaker at long multi-step jobs. Larger
models (20B+) do much better if your PC can hold them.

## Who uses what

In [Settings](Settings) → Orchestration, choose the pilot's model and the
models it may give agents. The pilot can't use anything you haven't allowed.

Within what you allow, the pilot gives each agent the most efficient model
that can do its task: small local models for routine work, a model that sees
the screen for visual work, and cloud models only for judgement, writing,
code or long multi-step jobs. Allowing a couple of local models alongside a
cloud account keeps most work free and on the PC.
