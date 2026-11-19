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
from hangar.prepare import HarnessMissing, SetupError, prepare

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
    flavor: str = typer.Option("base", help="base | research | coding | office (see `hangar flavors`)"),
    model: str = typer.Option("", help="claude[:model] | codex[:model] | local:<lm-studio-model>"),
    account: str = typer.Option("", help="Account for claude/codex (default: the only matching one)"),
    task: str = typer.Option("", help="What the agent should do"),
    share: list[str] = typer.Option([], "--share", help="Host folder to share, read-only (PATH) or writable (PATH:rw); repeatable"),
    allow: list[str] = typer.Option([], "--allow", help="Only let it reach these domains (repeatable); "
                                                       "its AI provider is added automatically"),
    offline: bool = typer.Option(False, "--offline", help="No internet except its AI provider and local models"),
    cpus: float = typer.Option(2.0, help="CPU cap"),
    memory: str = typer.Option("4g", help="Memory cap, e.g. 4g"),
    screen: str = typer.Option("1280x800", help="Desktop size WxH"),
    view: bool = typer.Option(False, "--view", help="Open the live view when ready"),
):
    """Create and start an agent."""
    try:
        from hangar.network import clean_domain
        allow_list = [clean_domain(d) for d in allow] if (allow or offline) else None
    except ValueError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    spec = AgentSpec(name=name, flavor=flavor, model=model, account=account, task=task, shares=share, allow=allow_list,
                     cpus=cpus, memory=memory, screen=screen)
    configure_model(spec)
    try:
        with con.status(f"Starting {name}…"):
            eng().create(spec)
            a = eng().wait_ready(name)
    except ImageNotFound as e:
        missing = "the gateway image" if "gateway" in str(e) else f"image {image_for(flavor)}"
        con.print(f"[red]Can't find {missing}.[/] Build it first (`make images`).")
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
        try:
            with con.status("Preparing…"):
                warnings = prepare(eng(), spec)
        except HarnessMissing as e:
            ensure_harness(e.harness)
            with con.status("Preparing…"):
                warnings = prepare(eng(), spec)
    except SetupError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    for w in warnings:
        con.print(f"[yellow]Warning:[/] {w}")


@app.command()
def net(name: str,
        allow: list[str] = typer.Option([], "--allow", help="Add a domain (repeatable)"),
        remove: list[str] = typer.Option([], "--remove", help="Remove a domain (repeatable)"),
        n: int = typer.Option(15, "-n", help="How many recent connections to show")):
    """Show or change an agent's network allowlist."""
    from hangar import network
    a = agent_or_exit(name)
    if a.network != "allowlist":
        con.print(f"{name} has [bold]open[/] internet access. To limit it, create it with --allow/--offline "
                  "(an allowlist can't be added to a running agent).")
        return
    current = network.read_allow(name)
    if allow or remove:
        try:
            drop = {network.clean_domain(d) for d in remove}
            current = network.write_allow(name, [d for d in current if d not in drop] + list(allow))
        except ValueError as e:
            con.print(f"[red]{e}[/]")
            raise typer.Exit(1)
        con.print("[green]✓[/] Updated (applies right away).")
    con.print(f"[bold]{name}[/] may reach: {', '.join(current) or '[dim]nothing but its AI provider and local models[/]'}")
    events = network.recent(eng().client, name)[:n]
    if events:
        t = Table(box=None, header_style="bold dim")
        for col in ("WHEN", "", "HOST"):
            t.add_column(col)
        for e in events:
            t.add_row(e["ts"][11:19], f"[{'green' if e['action'] == 'ALLOW' else 'red'}]{e['action'].lower()}[/]",
                      f"{e['host']}:{e['port']}" + (f"  [dim]{e['why']}[/]" if e["why"] else ""))
        con.print(t)
        blocked = sorted({e["host"] for e in events if e["action"] == "BLOCK"})
        if blocked:
            con.print(f"[dim]Allow one with: hangar net {name} --allow {blocked[0]}[/]")


images_app = typer.Typer(help="Agent images (on the Hangar OS they come from a registry).", no_args_is_help=True)
app.add_typer(images_app, name="images")


@images_app.command("pull")
def images_pull(all_flavors: bool = typer.Option(False, "--all", help="Also every flavor, not just base + gateway"),
                flavor: list[str] = typer.Option([], "--flavor", help="A flavor to pull (repeatable)")):
    """Download agent images from the registry in HANGAR_IMAGES (first boot does this)."""
    from hangar.engine import IMAGES
    if IMAGES.startswith("localhost/"):
        con.print("[yellow]HANGAR_IMAGES points at local images (dev mode): build them with `make images`.[/]")
        raise typer.Exit(1)
    names = ["base", "gateway", *flavor] + (["research", "coding", "office"] if all_flavors else [])
    for n in dict.fromkeys(names):
        ref = image_for(n) if n != "gateway" else IMAGES.format(name="gateway")
        con.print(f"Pulling [bold]{ref}[/]…")
        repo, _, tag = ref.rpartition(":")
        try:
            eng().client.images.pull(repo, tag=tag or "latest")
        except Exception as e:   # one missing flavor shouldn't stop the rest
            con.print(f"[red]  failed:[/] {e}")
            if n in ("base", "gateway"):
                raise typer.Exit(1)
    con.print("[green]✓[/] Agent images ready.")


@app.command()
def flavors():
    """Agent flavors that are built and ready."""
    t = Table(box=None, header_style="bold dim")
    for col in ("FLAVOR", "WHAT'S IN IT", "IMAGE"):
        t.add_column(col)
    for f in eng().flavors():
        t.add_row(f["name"], f["description"], f["image"])
    con.print(t)
    con.print("[dim]Build more with `make flavors`; add your own in images/flavors/<name>/ (see README).[/]")


@app.command()
def run(name: str, task: str, agent: str = typer.Option("", "--agent", "-a", help="Which agent in the space")):
    """Give a running agent (or one agent of a space) a new task."""
    a = agent_or_exit(name)
    if not a.model:
        con.print(f"[red]{name} has no model.[/] Create it with --model to run tasks.")
        raise typer.Exit(1)
    ensure_daemon(quiet=True)
    try:
        res = eng().run_task(name, task, agent=agent)
    except RuntimeError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"[green]{agent or name} is working on:[/] {task}")
    if res["snapshot"]:
        con.print(f"[dim]Workspace saved first (snapshot {res['snapshot']['id']}); "
                  f"undo with: hangar rollback {name}[/]")
    if res["warning"]:
        con.print(f"[yellow]{res['warning']}[/]")


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


APP_NAMES = ["home", "notes", "passwords", "accounts", "models", "settings"]


@app.command("open")
def open_app(name: str = typer.Argument("home", help=" | ".join(APP_NAMES)),
             web: bool = typer.Option(False, "--web", help="Open the web version in a browser instead")):
    """Open one of Hangar's apps (native Qt windows; --web for the browser version)."""
    name = "home" if name == "spaces" else name
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
    subprocess.Popen([sys.executable, "-m", "hangar.apps.home", "--overview"], start_new_session=True,
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
def snapshot(name: str, message: str = typer.Option("", "-m", help="A note to remember it by")):
    """Save a copy of the agent's workspace now."""
    from hangar import snapshots
    agent_or_exit(name)
    try:
        info = snapshots.take(eng(), name, message or "manual snapshot")
    except snapshots.SnapshotError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] Snapshot {info['id']} ({info['bytes'] / 1e6:.1f} MB of workspace).")


@app.command()
def snapshots(name: str):
    """List an agent's workspace snapshots (one is taken before every task)."""
    from hangar import snapshots as snaps
    items = snaps.list_snapshots(name)
    if not items:
        con.print(f"No snapshots for {name} yet. One is taken before every `hangar run`.")
        return
    t = Table(box=None, header_style="bold dim")
    for col in ("ID", "WHEN", "SIZE", "NOTE"):
        t.add_column(col)
    for s in items:
        t.add_row(s["id"], time.strftime("%b %d %H:%M", time.localtime(s["time"])), f"{s['bytes'] / 1e6:.1f} MB",
                  s["label"][:70])
    con.print(t)


@app.command()
def rollback(name: str, snapshot_id: str = typer.Argument(None, help="Default: the latest"),
             yes: bool = typer.Option(False, "-y", "--yes")):
    """Put the agent's workspace back as it was in a snapshot."""
    from hangar import snapshots as snaps
    agent_or_exit(name)
    if eng().runtime(name)["busy"] and not typer.confirm(f"{name} is working on a task. Roll back anyway?"):
        raise typer.Exit()
    items = snaps.list_snapshots(name)
    target = items[0] if items and not snapshot_id else next((s for s in items if s["id"] == snapshot_id), None)
    if not target:
        con.print(f"[red]No such snapshot.[/] See `hangar snapshots {name}`.")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Replace {name}'s workspace with snapshot {target['id']} ({target['label']})?"):
        raise typer.Exit()
    try:
        res = snaps.rollback(eng(), name, target["id"])
    except snaps.SnapshotError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] Restored {res['restored']['id']}. The workspace you replaced is snapshot "
              f"{res['undo']['id']} (roll back to it to undo).")


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


def ensure_harness(name: str) -> None:
    """Install Claude Code / Codex the first time something needs it."""
    from hangar import harness
    if harness.current(eng(), name):
        return
    con.print(f"[bold]Installing {harness.HARNESSES[name]['title']}[/] (one time; agents share it)…")
    try:
        h = harness.install(eng(), name)
    except RuntimeError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] {harness.HARNESSES[name]['title']} {h['version']} installed.")


def _login(accts: Accounts, acct) -> None:
    ensure_harness(acct.harness)
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


# -- teams and the pilot ---------------------------------------------------------

agent_app = typer.Typer(help="Agents inside a space (a space can hold a team).", no_args_is_help=True)
app.add_typer(agent_app, name="agent")


@agent_app.command("add")
def agent_add(space: str, name: str = typer.Option("", "--name", help="Default: a friendly name"),
              model: str = typer.Option("", help="claude | codex | local:<model>"),
              account: str = typer.Option(""), role: str = typer.Option("", help="e.g. 'reviewer'"),
              task: str = typer.Option("")):
    """Add an agent to a space. It shares the desktop with its own mouse, keyboard and tab."""
    from hangar import spaces
    agent_or_exit(space)
    try:
        with con.status("Adding…"):
            try:
                e = spaces.add_agent(eng(), space, name, model, account, role)
            except HarnessMissing as hm:
                ensure_harness(hm.harness)
                e = spaces.add_agent(eng(), space, name, model, account, role)
        if task:
            eng().run_task(space, task, agent=e["name"])
    except (SetupError, ValueError, RuntimeError) as ex:
        con.print(f"[red]{ex}[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] [bold {e['color']}]{e['name']}[/] joined {space}"
              + (f" and is working on: {task}" if task else "."))


@agent_app.command("list")
def agent_list(space: str):
    """Who's in a space and what each is doing."""
    a = agent_or_exit(space)
    t = Table(box=None, header_style="bold dim")
    for col in ("AGENT", "STATE", "MODEL", "ROLE", "TASK"):
        t.add_column(col, overflow="ellipsis", no_wrap=True, max_width=40 if col == "TASK" else None)
    for m in eng().state(a)["agents"]:
        t.add_row(f"[{m['color']}]●[/] {m['name']}", f"[{STATE_STYLE.get(m['state'], 'white')}]{m['state']}[/]",
                  m["model"] or "-", m.get("role") or "-", m.get("task") or "-")
    con.print(t)


@agent_app.command("remove")
def agent_remove(space: str, name: str):
    """Take an agent out of a space (its work in the workspace stays)."""
    from hangar import spaces
    try:
        spaces.remove_agent(eng(), space, name)
    except (KeyError, ValueError) as ex:
        con.print(f"[red]{str(ex).strip(chr(39))}[/]")
        raise typer.Exit(1)
    con.print(f"{name} left {space}.")


@app.command()
def messages(space: str, n: int = typer.Option(30, "-n")):
    """Messages to and from a space's agents (including via the pilot)."""
    d = _daemon_api("GET", f"/api/agents/{space}/messages/history")
    for m in d[-n:]:
        con.print(f"[dim]{m['ts'][11:19]}[/] [bold]{m['from']}[/] → {m['to'] if m['to_space'] == space else m['to_space'] + '/' + m['to']}: {m['text']}")
    if not d:
        con.print("No messages yet.")


@app.command()
def ask(question: str, new: bool = typer.Option(False, "--new", help="Start a fresh conversation"),
        wait: bool = typer.Option(False, "--wait", help="Wait here for the pilot's answer")):
    """Ask the pilot (your main agent). It answers, or runs agents to get it done."""
    res = _daemon_api("POST", "/api/ask", json={"question": question, "new": new})
    con.print("[green]Asked.[/] " + ("The pilot is busy; your message is queued for it." if res.get("queued")
                                     else "You'll get a notification with the answer."))
    if wait and not res.get("queued"):
        start = time.time()
        with con.status("The pilot is working…"):
            while time.time() - start < 3600:
                time.sleep(3)
                rt = eng().runtime("pilot")
                r = rt["agents"].get("pilot", {}).get("report")
                if r and r.get("final") and r["ts"] > time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(start - 2)):
                    con.print(f"\n[bold]Pilot[/] ({r['status']}): {r['message']}")
                    return


pilot_app = typer.Typer(help="The pilot's model and what it may use.", no_args_is_help=True)
app.add_typer(pilot_app, name="pilot")


@pilot_app.command("show")
def pilot_show():
    """Current orchestration settings."""
    from hangar import pilot
    o = pilot.load()
    con.print(f"Pilot model: [bold]{o['pilot']['model'] or 'not set'}[/]"
              + (f" (account {o['pilot']['account']})" if o["pilot"]["account"] else ""))
    con.print("May give agents: " + (", ".join(a["model"] + (f" ({a['account']})" if a["account"] else "")
                                             for a in o["allowed"]) or "[yellow]nothing yet[/]"))
    con.print(f"Max agents at once: {o['max_agents']} · new spaces' internet: {o['internet']}")


@pilot_app.command("set")
def pilot_set(model: str = typer.Argument(..., help="claude | codex | local:<model>"),
                  account: str = typer.Option(""),
                  allow: list[str] = typer.Option([], "--allow", help="A model it may give agents (repeatable), "
                                                                     "e.g. local:qwen/qwen3.5-9b or claude@personal"),
                  max_agents: int = typer.Option(4), internet: str = typer.Option("open", help="open | allowlist")):
    """Choose the pilot's model and what it may use for agents."""
    from hangar import pilot
    allowed = [{"model": a.split("@")[0], "account": a.split("@")[1] if "@" in a else ""} for a in allow]
    o = pilot.save({"pilot": {"model": model, "account": account}, "allowed": allowed,
                        "max_agents": max_agents, "internet": internet})
    con.print("[green]✓[/] Saved.")
    pilot_show()


# -- harnesses ------------------------------------------------------------------

harness_app = typer.Typer(help="Agent harnesses (Claude Code, Codex CLI), installed when needed.",
                          no_args_is_help=True)
app.add_typer(harness_app, name="harness")


@harness_app.command("list")
def harness_list():
    """Which harnesses are installed (agents share them)."""
    from hangar import harness
    have = harness.installed(eng())
    t = Table(box=None, header_style="bold dim")
    for col in ("HARNESS", "VERSION", "VOLUME"):
        t.add_column(col)
    for name, info in harness.HARNESSES.items():
        mine = [h for h in have if h["harness"] == name]
        t.add_row(info["title"], ", ".join(h["version"] for h in mine) or "[dim]not installed[/]",
                  ", ".join(h["volume"] for h in mine) or "-")
    con.print(t)


@harness_app.command("install")
def harness_install(name: str = typer.Argument(..., help="claude | codex"), version: str = typer.Option("")):
    """Install a harness (latest version unless --version)."""
    from hangar import harness
    try:
        h = harness.install(eng(), name, version)
    except (ValueError, RuntimeError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] {harness.HARNESSES[name]['title']} {h['version']} installed.")


@harness_app.command("update")
def harness_update(name: str = typer.Argument(..., help="claude | codex")):
    """Install the newest version. New agents use it; running ones keep theirs until restarted."""
    from hangar import harness
    try:
        cur, latest = harness.current(eng(), name), harness.latest_version(eng(), name)
    except ValueError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    if cur and cur["version"] == latest:
        con.print(f"{harness.HARNESSES[name]['title']} {latest} is already the newest.")
        return
    harness_install(name, latest)


@harness_app.command("remove")
def harness_remove(name: str = typer.Argument(..., help="claude | codex")):
    """Uninstall a harness (only if no agent uses it)."""
    from hangar import harness
    try:
        gone = harness.remove(eng(), name)
    except (ValueError, RuntimeError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"Removed {', '.join(gone) or 'nothing (not installed)'}.")


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
