"""`hangar` command line.

Agent commands talk to the container engine directly. The daemon (started
automatically when needed) receives agents' reports, sends notifications and
serves the live views, so `view` and `takeover` open pages through it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import typer
from docker.errors import ImageNotFound, NotFound
from rich.console import Console
from rich.table import Table

import httpx

from hangar import auth, daemonctl, notify
from hangar import models as lm
from hangar.accounts import HARNESS, PROVIDERS, Accounts
from hangar.engine import DAEMON_PORT, Agent, AgentSpec, Engine, image_for
from hangar.prepare import SetupError, prepare

app = typer.Typer(help="Hangar: run AI agents on their own hidden desktops.", no_args_is_help=True,
                  add_completion=False)
accounts_app = typer.Typer(help="AI accounts: subscriptions and API keys.", no_args_is_help=True)
models_app = typer.Typer(help="Local models (LM Studio).", no_args_is_help=True)
app.add_typer(accounts_app, name="accounts")
app.add_typer(models_app, name="models")
notify_app = typer.Typer(help="Notifications when agents finish or need you.", no_args_is_help=True)
app.add_typer(notify_app, name="notify")
DAEMON = f"http://127.0.0.1:{DAEMON_PORT}"
con = Console()
_engine: Engine | None = None


def eng() -> Engine:
    global _engine
    if _engine is None:
        try:
            _engine = Engine()
            _engine.client.ping()
        except Exception as e:
            con.print(f"[red]Can't reach the container engine:[/] {e}\n"
                      "Is Docker Desktop / Podman running? (`open -a Docker` on a Mac)")
            raise typer.Exit(1)
    return _engine


def agent_or_exit(name: str) -> Agent:
    try:
        return eng().get(name)
    except NotFound:
        con.print(f"[red]No agent named '{name}'.[/] See `hangar list`.")
        raise typer.Exit(1)


def runtime(a: Agent) -> dict:
    return eng().state(a)


def daemon_up() -> bool:
    return daemonctl.daemon_up()


def ensure_daemon(quiet: bool = False) -> bool:
    """Start the daemon in the background if it isn't running. Agents still
    work without it; their reports just stay in their own containers."""
    was_up = daemonctl.daemon_up()
    ok = daemonctl.ensure_daemon()
    logf = auth.STATE_DIR / "daemon.log"
    if ok and not was_up and not quiet:
        con.print(f"[dim]Started the hangar daemon on {DAEMON} (log: {logf})[/]")
    if not ok and not os.environ.get("HANGAR_NO_AUTODAEMON"):
        con.print(f"[yellow]Couldn't start the daemon; see {logf}[/]")
    return ok


def page_url(path: str) -> str:
    """A link that signs the browser in to the daemon, then opens `path`."""
    return f"{DAEMON}/login?token={auth.admin_token()}&next={quote(path, safe='')}"


STATE_STYLE = {"running": "green", "working": "green", "idle": "dim", "done": "cyan",
               "waiting_on_user": "yellow", "taken over": "magenta", "error": "red", "exited": "red"}


@app.command()
def new(
    name: str,
    flavor: str = typer.Option("base", help="base | research | coding | office"),
    model: str = typer.Option("", help="claude[:model] | codex[:model] | local:<lm-studio-model>"),
    account: str = typer.Option("", help="Account for claude/codex (default: the only matching one)"),
    task: str = typer.Option("", help="What the agent should do"),
    share: list[str] = typer.Option([], "--share", help="Host folder to share, read-only (PATH) or writable (PATH:rw); repeatable"),
    cpus: float = typer.Option(2.0, help="CPU cap"),
    memory: str = typer.Option("4g", help="Memory cap, e.g. 4g"),
    screen: str = typer.Option("1280x800", help="Desktop size WxH"),
    view: bool = typer.Option(False, "--view", help="Open the live view when ready"),
):
    """Create and start an agent."""
    spec = AgentSpec(name=name, flavor=flavor, model=model, account=account, task=task, shares=share,
                     cpus=cpus, memory=memory, screen=screen)
    configure_model(spec)
    try:
        with con.status(f"Starting {name}…"):
            eng().create(spec)
            a = eng().wait_ready(name)
    except ImageNotFound:
        con.print(f"[red]Image {image_for(flavor)} not found.[/] Build it first (`make base`).")
        raise typer.Exit(1)
    except ValueError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    ensure_daemon(quiet=True)   # so its reports and notifications reach you
    con.print(f"[green]✓[/] {name} is up. Watch it: hangar view {name}")
    if task and model:
        con.print(f"[green]Working on:[/] {task}\n[dim]Follow along: hangar view {name} · hangar logs {name} -s agent[/]")
    elif task:
        con.print("[yellow]Task saved, but no --model is set, so the agent won't start it.[/]")
    if view and ensure_daemon():
        webbrowser.open(page_url(f"/agents/{name}/view"))


def configure_model(spec: AgentSpec) -> None:
    """Resolve --model/--account/--share, or exit with a helpful message."""
    try:
        with con.status("Preparing…"):
            warnings = prepare(eng(), spec)
    except SetupError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    for w in warnings:
        con.print(f"[yellow]Warning:[/] {w}")


@app.command()
def run(name: str, task: str):
    """Give a running agent a new task."""
    a = agent_or_exit(name)
    if not a.model:
        con.print(f"[red]{name} has no model.[/] Create it with --model to run tasks.")
        raise typer.Exit(1)
    ensure_daemon(quiet=True)
    try:
        eng().run_task(name, task)
    except RuntimeError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"[green]{name} is working on:[/] {task}")


@app.command("list")
def list_():
    """Show all agents."""
    agents = eng().list()
    if not agents:
        con.print("No agents yet. Try: hangar new scout --task \"…\"")
        return
    with ThreadPoolExecutor(8) as ex:
        rts = list(ex.map(runtime, agents))
    t = Table(box=None, header_style="bold dim")
    for col in ("NAME", "STATE", "FLAVOR", "MODEL", "TASK", "LAST REPORT"):
        t.add_column(col, overflow="ellipsis", no_wrap=True, max_width=40 if col in ("TASK", "LAST REPORT") else None)
    for a, rt in zip(agents, rts):
        st, r = rt["state"], rt["report"]
        t.add_row(a.name, f"[{STATE_STYLE.get(st, 'white')}]{st}[/]", a.flavor, a.model or "-",
                  rt["task"] or "-", r["message"] if r else "-")
    con.print(t)


@app.command()
def status(name: str, as_json: bool = typer.Option(False, "--json")):
    """Details and latest report for one agent."""
    a = agent_or_exit(name)
    rt = runtime(a)
    info = {**a.__dict__, "state": rt["state"], "task": rt["task"],
            "view_url": f"{DAEMON}/agents/{name}/view", "mcp_url": f"{DAEMON}/agents/{name}/mcp",
            "last_report": rt["report"]}
    if as_json:
        print(json.dumps(info, indent=2))
        return
    for k in ("name", "state", "flavor", "model", "account", "task", "shares", "image", "created",
              "view_url", "mcp_url"):
        v = ", ".join(info[k]) if isinstance(info[k], list) else info[k]
        con.print(f"[dim]{k:>10}[/]  {v or '-'}")
    r = info["last_report"]
    if r:
        con.print(f"[dim]{'report':>10}[/]  [{r['status']}] {r['message']}  [dim]{r['ts'][:19]}[/]")


@app.command()
def ui(print_only: bool = typer.Option(False, "--print", help="Only print the URL")):
    """Open the web version of Hangar in your browser (for phones and other computers)."""
    _open("/", print_only)


APP_NAMES = ["spaces", "passwords", "accounts", "models", "settings"]


@app.command("open")
def open_app(name: str = typer.Argument("spaces", help=" | ".join(APP_NAMES)),
             web: bool = typer.Option(False, "--web", help="Open the web version in a browser instead")):
    """Open one of Hangar's apps (native Qt windows; --web for the browser version)."""
    if name not in APP_NAMES:
        con.print(f"[red]Unknown app.[/] Pick one of: {', '.join(APP_NAMES)}")
        raise typer.Exit(1)
    if not ensure_daemon():
        raise typer.Exit(1)
    if not web:
        try:
            import PySide6  # noqa: F401
        except ImportError:
            con.print("[yellow]Native apps need PySide6 (uv sync --extra apps); opening the web version.[/]")
        else:
            subprocess.Popen([sys.executable, "-m", f"hangar.apps.{name}"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    _open_web_app(name)


@app.command()
def overview():
    """Show every agent at once (what the Spaces key does on the Hangar OS)."""
    if not ensure_daemon():
        raise typer.Exit(1)
    subprocess.Popen([sys.executable, "-m", "hangar.apps.spaces", "--overview"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _open_web_app(name: str) -> None:
    url = page_url(f"/app/{name}")
    # A Chromium-family browser in app mode gives a window with no tabs or address bar.
    mac = ["Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser"]
    linux = ["chromium", "chromium-browser", "google-chrome", "microsoft-edge", "brave-browser"]
    if sys.platform == "darwin":
        for b in mac:
            if Path(f"/Applications/{b}.app").exists():
                subprocess.Popen(["open", "-na", b, "--args", f"--app={url}"])
                return
    else:
        for b in linux:
            if shutil.which(b):
                subprocess.Popen([b, f"--app={url}", f"--class=hangar-{name}"], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    webbrowser.open(url)


@app.command()
def view(name: str, print_only: bool = typer.Option(False, "--print", help="Only print the URL")):
    """Watch an agent's desktop (view-only), through the daemon."""
    agent_or_exit(name)
    _open(f"/agents/{name}/view", print_only)


def _open(path: str, print_only: bool) -> None:
    if print_only:
        con.print(f"{DAEMON}{path}  [dim](needs the daemon and a signed-in browser; "
                  "`hangar daemon token` prints the token)[/]")
        return
    if not ensure_daemon():
        raise typer.Exit(1)
    webbrowser.open(page_url(path))


@app.command()
def takeover(name: str, print_only: bool = typer.Option(False, "--print")):
    """Pause the agent's input tools and open its desktop with your mouse and keyboard."""
    agent_or_exit(name)
    eng().pause(name)
    con.print(f"[magenta]You have control of {name}.[/] Its input tools are paused. "
              f"Run `hangar release {name}` to hand it back.")
    _open(f"/agents/{name}/view?control=1", print_only)


@app.command()
def release(name: str):
    """Hand control back to the agent."""
    agent_or_exit(name)
    eng().resume(name)
    con.print(f"[green]✓[/] {name} has control again.")


@app.command()
def logs(
    name: str,
    service: str = typer.Option("", "-s", "--service",
                                help="mcp | agent | xvnc | websockify | openbox (default: session + reports)"),
    tail: int = typer.Option(100, "-n"),
):
    """Show an agent's logs and reports."""
    agent_or_exit(name)
    if service:
        print(eng().exec(name, ["tail", "-n", str(tail), f"/home/agent/.hangar/logs/{service}.log"])[1])
        return
    print(eng().logs(name, tail=tail))
    reports = eng().read_file(name, "/home/agent/.hangar/reports.jsonl").strip().splitlines()
    for line in reports[-tail:]:
        r = json.loads(line)
        con.print(f"[dim]{r['ts'][:19]}[/] [{STATE_STYLE.get(r['status'], 'white')}]{r['status']}[/] {r['message']}")


@app.command()
def shell(name: str):
    """Open a shell inside an agent's container."""
    agent_or_exit(name)
    cli = shutil.which("podman") or shutil.which("docker")
    if not cli:
        con.print("[red]Neither podman nor docker CLI found.[/]")
        raise typer.Exit(1)
    subprocess.run([cli, "exec", "-it", f"hangar-{name}", "bash"])


@app.command()
def stop(name: str):
    """Stop an agent (its desktop and files are kept)."""
    agent_or_exit(name)
    eng().stop(name)
    con.print(f"Stopped {name}. `hangar start {name}` brings it back.")


@app.command()
def start(name: str):
    """Start a stopped agent."""
    agent_or_exit(name)
    with con.status(f"Starting {name}…"):
        eng().start(name)
        a = eng().wait_ready(name)
    con.print(f"[green]✓[/] {name} is up. Watch it: hangar view {name}")


@app.command()
def destroy(name: str, yes: bool = typer.Option(False, "-y", "--yes"),
            keep_home: bool = typer.Option(False, help="Keep the agent's home volume")):
    """Delete an agent and (unless --keep-home) its files."""
    agent_or_exit(name)
    if not yes and not typer.confirm(f"Delete {name}{'' if keep_home else ' and its files'}?"):
        raise typer.Exit()
    eng().destroy(name, keep_home=keep_home)
    con.print(f"Deleted {name}.")


# -- accounts ------------------------------------------------------------------

@accounts_app.command("list")
def accounts_list():
    """Show accounts and which agents use them."""
    accts = Accounts(eng())
    items = accts.list()
    if not items:
        con.print("No accounts yet. Add one: hangar accounts add personal --provider claude")
        return
    t = Table(box=None, header_style="bold dim")
    for col in ("NAME", "PROVIDER", "HARNESS", "AGENTS", "CREATED"):
        t.add_column(col)
    for a in items:
        t.add_row(a.name, a.provider, a.harness, ", ".join(accts.users(a.name)) or "-", a.created)
    con.print(t)


@accounts_app.command("add")
def accounts_add(
    name: str,
    provider: str = typer.Option(..., prompt=f"Provider ({' | '.join(PROVIDERS)})",
                                 help=" | ".join(PROVIDERS)),
):
    """Add an account and sign in through the vendor's official login."""
    accts = Accounts(eng())
    try:
        acct = accts.create(name, provider)
    except ValueError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"Created account [bold]{name}[/] ({PROVIDERS[provider]}).")
    if provider.endswith("-key"):
        key = typer.prompt("Paste the API key (hidden)", hide_input=True)
        accts.set_api_key(acct, key)
        con.print("[green]✓[/] Key stored in the account's volume.")
    else:
        _login(accts, acct)


def _login(accts: Accounts, acct) -> None:
    con.print("[dim]Starting the official sign-in in a temporary container. Open the link it shows, "
              "sign in in your browser, and paste back any code it asks for. Hangar never sees "
              "your password.[/]")
    code = subprocess.call(accts.login_argv(acct))
    status = accts.status(acct)
    con.print(f"[{'green' if code == 0 else 'yellow'}]Sign-in finished (exit {code}).[/] Status:\n{status}")


@accounts_app.command("login")
def accounts_login(name: str):
    """Sign in again (e.g. after a logout or an expired session)."""
    accts = Accounts(eng())
    try:
        acct = accts.get(name)
        if acct.provider.endswith("-key"):
            accts.set_api_key(acct, typer.prompt("Paste the new API key (hidden)", hide_input=True))
            con.print("[green]✓[/] Key replaced.")
        else:
            _login(accts, acct)
    except (KeyError, ValueError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)


@accounts_app.command("status")
def accounts_status(name: str):
    """Ask the vendor CLI whether an account is signed in."""
    accts = Accounts(eng())
    try:
        con.print(accts.status(accts.get(name)))
    except KeyError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)


@accounts_app.command("remove")
def accounts_remove(name: str, yes: bool = typer.Option(False, "-y", "--yes")):
    """Delete an account and its stored sign-in."""
    if not yes and not typer.confirm(f"Delete account {name} and its stored sign-in?"):
        raise typer.Exit()
    try:
        Accounts(eng()).remove(name)
    except (KeyError, ValueError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"Deleted account {name}.")


# -- daemon --------------------------------------------------------------------

daemon_app = typer.Typer(help="The local daemon (reports, notifications, live views).",
                         invoke_without_command=True)
app.add_typer(daemon_app, name="daemon")


@daemon_app.callback()
def daemon(ctx: typer.Context, port: int = typer.Option(DAEMON_PORT, help="Port on 127.0.0.1")):
    """Run the daemon in the foreground (other commands start it in the background)."""
    if ctx.invoked_subcommand:
        return
    if daemon_up():
        con.print(f"[yellow]The daemon is already running on {DAEMON}.[/] Stop it with `hangar daemon stop`.")
        raise typer.Exit(1)
    from hangar.daemon import serve
    serve(port=port)


@daemon_app.command("status")
def daemon_status():
    """Is the daemon running?"""
    con.print(f"[green]running[/] on {DAEMON}" if daemon_up() else "[dim]not running[/]")


@daemon_app.command("stop")
def daemon_stop():
    """Stop the daemon."""
    pidf = auth.STATE_DIR / "daemon.pid"
    try:
        os.kill(int(pidf.read_text()), 15)
        con.print("Stopped.")
    except (OSError, ValueError):
        con.print("[dim]No daemon running.[/]")


@daemon_app.command("token")
def daemon_token():
    """Print the admin token (for scripts and `curl -H "Authorization: Bearer …"`)."""
    print(auth.admin_token())


# -- vault ---------------------------------------------------------------------

vault_app = typer.Typer(help="Passwords and verification codes agents can ask to use.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")


def _daemon_api(method: str, path: str, **kw):
    if not ensure_daemon(quiet=True):
        raise typer.Exit(1)
    r = httpx.request(method, f"{DAEMON}{path}", headers={"Authorization": f"Bearer {auth.admin_token()}"},
                      timeout=60, **kw)
    if r.status_code >= 400:
        con.print(f"[red]{r.json().get('error', r.text) if 'json' in r.headers.get('content-type', '') else r.text}[/]")
        raise typer.Exit(1)
    return r.json()


@vault_app.command("list")
def vault_list():
    """Saved passwords (secrets hidden) and which agents may use them."""
    from hangar.vault import Vault
    v = Vault()
    entries = [e for e in v.entries() if not e["deleted"]]
    if not entries:
        con.print("No passwords yet. Add one: hangar vault add github.com")
        return
    grants = v.grants()
    t = Table(box=None, header_style="bold dim")
    for col in ("ID", "WEBSITE", "USER NAME", "CODES", "AGENTS", "WARNINGS"):
        t.add_column(col)
    for e in sorted(entries, key=lambda x: x["label"]):
        t.add_row(e["id"], e["label"], e["username"] or "-", "yes" if e["has_totp"] else "",
                  ", ".join(g["agent"] for g in grants if g["entry"] == e["id"]) or "-",
                  f"[yellow]{', '.join(e['warnings'])}[/]" if e["warnings"] else "")
    con.print(t)


@vault_app.command("add")
def vault_add(site: str, username: str = typer.Option("", "--username", "-u")):
    """Save a password (asked for hidden) and optionally a 2FA setup key."""
    from hangar.vault import Vault
    password = typer.prompt("Password (hidden, Enter for none)", default="", hide_input=True, show_default=False)
    key = typer.prompt("Verification code setup key (hidden, Enter for none)", default="", hide_input=True,
                       show_default=False)
    try:
        e = Vault().add(site, username, password, key)
    except Exception as ex:   # ValueError, or bad base32
        con.print(f"[red]{ex}[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] Saved {e['label']} ({e['id']}).")


@vault_app.command("remove")
def vault_remove(entry_id: str):
    """Move a password to Recently Deleted (recoverable for 30 days in Spaces)."""
    from hangar.vault import Vault
    try:
        Vault().remove(entry_id)
    except KeyError as ex:
        con.print(f"[red]{ex}[/]")
        raise typer.Exit(1)
    con.print("Moved to Recently Deleted.")


@vault_app.command("requests")
def vault_requests():
    """Agents waiting for a login or code."""
    d = _daemon_api("GET", "/api/vault")
    pending = [r for r in d["requests"] if r["status"] == "pending"]
    if not pending:
        con.print("No agents are waiting.")
    for r in pending:
        opts = ", ".join(f"{e['id']} ({e['label']}, {e['username'] or 'no username'})" for e in r["entries"])
        con.print(f"[bold]{r['id']}[/]  {r['agent']} wants {'to sign in to' if r['kind'] == 'login' else 'a code for'} "
                  f"{r['site']}: {r['reason'] or '-'}\n      saved logins: {opts or 'none'}")


@vault_app.command("approve")
def vault_approve(request_id: str, entry: str = typer.Option("", help="Saved login ID (default: the only match)"),
                  scope: str = typer.Option("once", help="once | hour | always"),
                  code: bool = typer.Option(False, "--code", help="Type a code from a text or email")):
    """Approve a request: Hangar fills it into the agent's browser."""
    d = _daemon_api("GET", "/api/vault")
    r = next((x for x in d["requests"] if x["id"] == request_id), None)
    if not r:
        con.print("[red]No such request.[/] See `hangar vault requests`.")
        raise typer.Exit(1)
    typed = typer.prompt("Code", hide_input=False) if code else ""
    if not entry and not typed:
        usable = [e for e in r["entries"] if (e["has_password"] if r["kind"] == "login" else e["has_totp"])]
        if len(usable) != 1:
            con.print("[red]Pick a login with --entry[/] (see `hangar vault requests`).")
            raise typer.Exit(1)
        entry = usable[0]["id"]
    res = _daemon_api("POST", f"/api/vault/requests/{request_id}/approve",
                      json={"entry": entry, "scope": scope, "code": typed})
    con.print(f"[{'green' if res['status'] == 'filled' else 'red'}]{res['status']}[/]: {res['message']}")


@vault_app.command("deny")
def vault_deny(request_id: str):
    """Say no to a request."""
    _daemon_api("POST", f"/api/vault/requests/{request_id}/deny")
    con.print("Denied.")


# -- notifications -------------------------------------------------------------

@notify_app.command("list")
def notify_list():
    """Show configured channels (secrets hidden)."""
    cfg = notify.load()
    if not cfg:
        con.print("No channels yet; notifications only go to the daemon's log.\n"
                  "Add one: hangar notify add ntfy | slack | telegram")
    for name, c in cfg.items():
        shown = {k: ("•••" if k in notify.SECRET_FIELDS and v else v) for k, v in c.items()}
        con.print(f"[bold]{name}[/]  {shown}")


@notify_app.command("add")
def notify_add(channel: str = typer.Argument(..., help="ntfy | slack | telegram")):
    """Add or replace a channel. Secrets are read hidden and stored 0600."""
    if channel not in notify.CHANNELS:
        con.print(f"[red]Unknown channel.[/] Pick one of: {', '.join(notify.CHANNELS)}")
        raise typer.Exit(1)
    prompts = {"url": "ntfy topic URL (e.g. https://ntfy.sh/my-hangar-7f3k)",
               "token": "ntfy access token (Enter for none)", "webhook": "Slack incoming-webhook URL",
               "bot_token": "Telegram bot token (from @BotFather)", "chat_id": "Telegram chat ID"}
    c = {}
    for field in notify.CHANNELS[channel]:
        c[field] = typer.prompt(prompts[field], default="" if field == "token" else None,
                                hide_input=field in notify.SECRET_FIELDS, show_default=False)
    cfg = notify.load()
    cfg[channel] = c
    notify.save(cfg)
    con.print(f"[green]✓[/] Saved {channel}. Try it: hangar notify test")


@notify_app.command("remove")
def notify_remove(channel: str):
    """Remove a channel."""
    cfg = notify.load()
    if cfg.pop(channel, None) is None:
        con.print(f"[dim]{channel} wasn't configured.[/]")
        return
    notify.save(cfg)
    con.print(f"Removed {channel}.")


@notify_app.command("test")
def notify_test():
    """Send a test notification to every channel."""
    res = notify.send("Hangar test", "Notifications work. Agents will ping you here.", "done")
    if not res:
        con.print("No channels configured. Add one: hangar notify add ntfy")
    for ch, r in res.items():
        con.print(f"{ch}: {'[green]ok[/]' if r == 'ok' else f'[red]{r}[/]'}")


# -- models --------------------------------------------------------------------

def studio_or_exit() -> lm.LMStudio:
    s = lm.LMStudio()
    if not s.installed:
        con.print("[red]LM Studio isn't installed[/] (no `lms` CLI). Get it at lmstudio.ai and open it once.")
        raise typer.Exit(1)
    return s


@models_app.command("list")
def models_list():
    """Downloaded models. Vision models can be used for desktop (screenshot) work."""
    s = studio_or_exit()
    t = Table(box=None, header_style="bold dim")
    for col in ("MODEL", "NAME", "SIZE", "VISION", "TOOLS", "LOADED"):
        t.add_column(col, no_wrap=col in ("MODEL", "SIZE"))
    for m in s.list():
        t.add_row(f"local:{m.key}", m.name, f"{m.size_gb} GB", "[green]yes[/]" if m.vision else "[dim]no[/]",
                  "yes" if m.tools else "[dim]no[/]", "●" if m.loaded else "")
    con.print(t)
    con.print(f"[dim]Server: {'running' if s.server_up() else 'stopped'} on {lm.HOST_URL}[/]")


@models_app.command("download")
def models_download(query: str):
    """Search LM Studio's catalog and download, e.g. `qwen/qwen2.5-vl-7b`."""
    raise typer.Exit(studio_or_exit().download(query))


@models_app.command("remove")
def models_remove(key: str, yes: bool = typer.Option(False, "-y", "--yes")):
    """Delete a downloaded model's files."""
    s = studio_or_exit()
    key = key.removeprefix("local:")
    if not yes and not typer.confirm(f"Delete {key} from disk?"):
        raise typer.Exit()
    try:
        con.print(f"Deleted {s.remove(key)}")
    except (KeyError, RuntimeError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)


@models_app.command("serve")
def models_serve():
    """Start LM Studio's API server (agents reach it as hangar-host:1234)."""
    s = studio_or_exit()
    s.ensure_server()
    con.print(f"[green]✓[/] LM Studio server on {lm.HOST_URL} · agents use {lm.AGENT_URL}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
