# Passwords

Agents often need to sign in to a website to finish a job. Hangar lets them,
without ever giving them your password.

![An agent asking to sign in](images/approve.png)

## How it works

1. An agent reaches a sign-in page and asks to sign in, saying why.
2. You see the request at the top of Hangar (and Passwords), and get a
   notification: *"bikes wants to sign in to pedalswap.com: to message the seller"*.
3. Choose **Sign in** (just this once, for an hour, or always for that
   agent) or **Deny**.
4. Hangar types the username and password into the agent's browser itself,
   only if the page really is that site. A lookalike address is refused.
   The agent is only told it worked.

Verification codes work the same way: if you saved the site's 2FA setup key,
Hangar fills the current code; otherwise you type the code from your text or
email into the request.

## The Passwords app

Modelled on Apple's Passwords: all your logins, live verification codes,
weak or reused passwords, which agents may use what (and their activity), and
Recently Deleted (kept 30 days). It has a strong-password generator too.

Everything is stored encrypted on the PC.

## The honest limit

Once a password is typed into an agent's browser, a determined agent could
read it back out of the page. So only approve sign-ins for agents you'd trust
with that account, and prefer "just this once" or "for an hour".
