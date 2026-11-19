"""Hangar Setup: the first-boot wizard (and "Run setup again" from Settings).

One step per screen, like setting up a new Mac: welcome (language, keyboard,
time zone), network, AI accounts, local models, notifications, done. Every
step can be skipped and changed later. On the Hangar OS it opens full screen
on first login (os/autostart); on a Mac it's a normal window (dev mode).

  python -m hangar.apps.setup [--first-boot] [--windowed]
"""

from __future__ import annotations

import io
import secrets
import subprocess
import sys
from zoneinfo import available_timezones

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QScrollArea, QStackedWidget, QWidget)

from hangar.apps import common
from hangar.apps.common import C, bg, button, hbox, label, vbox
from hangar.apps.manage import JobDialog

EXAMPLE_TASK = ("Find the three most-starred open-source AI agent projects on GitHub this week and "
                "write a short summary of what each one does.")
ACCOUNT_CARDS = [
    ("claude", "claude", "Claude", "Pro or Max subscription. Signs in through Claude Code's official login."),
    ("codex", "chatgpt", "ChatGPT", "Plus or Pro subscription. Signs in through Codex's official login."),
    ("anthropic-key", "anthropic", "Anthropic API key", "Pay-as-you-go key from console.anthropic.com."),
    ("openai-key", "openai", "OpenAI API key", "Pay-as-you-go key from platform.openai.com."),
]


def chip(text: str, color: str) -> QLabel:
    lbl = label(text)
    lbl.setStyleSheet(f"color:{color}; background:{C['panel2']}; border-radius:8px; padding:1px 8px; font-size:11px;"
                      "font-weight:600;")
    return lbl


class Step(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, wiz: "SetupWindow") -> None:
        super().__init__()
        self.wiz = wiz

    def enter(self) -> None:      # called each time the step is shown
        pass

    def commit(self, then) -> None:   # Continue: save this step, then call then()
        then()


class Welcome(Step):
    title = "Welcome to Hangar"
    subtitle = "Let's get your agents ready. This takes a few minutes, and you can skip anything."

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        self.lang, self.kb, self.tz = QComboBox(), QComboBox(), QComboBox()
        self.tz.setEditable(True)
        self.tz.addItems(sorted(available_timezones()))
        self.note = label("", "dim", wrap=True)
        self.setLayout(vbox(label("Language", "dim"), self.lang, label("Keyboard", "dim"), self.kb,
                            label("Time zone", "dim"), self.tz, self.note, "stretch", spacing=6))

    def enter(self) -> None:
        h = self.wiz.state["host"]
        if not self.lang.count():
            for k, v in h["languages"].items():
                self.lang.addItem(v, k)
            for k, v in h["keyboards"].items():
                self.kb.addItem(v, k)
        self.lang.setCurrentIndex(max(self.lang.findData(h["language"]), 0))
        self.kb.setCurrentIndex(max(self.kb.findData(h["keyboard"]), 0))
        self.tz.setCurrentText(h["timezone"] or "UTC")
        self.note.setText("Dev mode: on your Mac these are only shown. On the Hangar OS they're applied."
                          if h["dev"] else "")

    def commit(self, then) -> None:
        h = self.wiz.state["host"]
        body = {k: v for k, v, cur in (("language", self.lang.currentData(), h["language"]),
                                         ("keyboard", self.kb.currentData(), h["keyboard"]),
                                         ("timezone", self.tz.currentText(), h["timezone"])) if v and v != cur}
        if not body:
            then()
            return
        bg(lambda: common.api.post("/api/setup/host", body), lambda _: then(),
           lambda e: self.wiz.toast("Couldn't apply that", str(e), True))


class Network(Step):
    title = "Connect to the internet"
    subtitle = "Agents need the internet for their AI accounts and the web."

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        self.status = label("Checking…")
        self.list = QListWidget()
        self.list.setMinimumHeight(180)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Wi-Fi password")
        self.join = button("Join", "primary", self.connect)
        self.wifi = QWidget()
        self.wifi.setLayout(vbox(self.list, hbox(self.pw, self.join), spacing=8))
        self.wifi.hide()
        self.setLayout(vbox(self.status, self.wifi, "stretch", spacing=10))

    def enter(self) -> None:
        self.status.setText("Checking…")
        bg(lambda: common.api.get("/api/setup/network"), self.show_net)

    def show_net(self, n: dict) -> None:
        on = f"<span style='color:{C['green']}'>●</span> You're online." if n["online"] else \
             f"<span style='color:{C['yellow']}'>●</span> Not connected yet."
        extra = " Wi-Fi is managed by your Mac in dev mode." if n["dev"] else ""
        self.status.setText(on + extra)
        self.wifi.setVisible(n["wifi"])
        self.list.clear()
        for net in n["networks"]:
            it = QListWidgetItem(f"{'✓ ' if net['active'] else ''}{net['ssid']}    "
                                 f"{'🔒' if net['secure'] else ''} {net['signal']}%")
            it.setData(Qt.UserRole, net["ssid"])
            self.list.addItem(it)

    def connect(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        ssid, pw = it.data(Qt.UserRole), self.pw.text()
        self.join.setEnabled(False)
        self.join.setText("Joining…")

        def done(_):
            self.pw.clear()
            self.join.setEnabled(True)
            self.join.setText("Join")
            self.enter()

        def failed(e):
            self.wiz.toast("Couldn't join", str(e), True)
            done(None)
        bg(lambda: common.api.post("/api/setup/wifi", {"ssid": ssid, "password": pw}), done, failed)


class Accounts(Step):
    title = "Connect your AI accounts"
    subtitle = ("Use a subscription you already have, or an API key. Sign-in happens in each company's "
                "official login: Hangar never sees your password. Connect several, or none.")

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        self.cards = vbox(spacing=10)
        self.setLayout(vbox(self.cards, "stretch"))

    def enter(self) -> None:
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.deleteLater()
        have = self.wiz.state["accounts"]
        for provider, default, name, blurb in ACCOUNT_CARDS:
            mine = [a["name"] for a in have if a["provider"] == provider]
            f = QFrame()
            f.setObjectName("panel")
            status = (label(f"<span style='color:{C['green']}'>✓ Connected</span> ({', '.join(mine)})")
                      if mine else None)
            act = button("Add another" if mine else ("Sign in" if not provider.endswith("-key") else "Add key"),
                         "" if mine else "primary", lambda _=False, p=provider, d=default: self.add(p, d))
            f.setLayout(hbox(vbox(label(f"<b>{name}</b>"), label(blurb, "dim", wrap=True), status, spacing=2),
                             "stretch", act, margins=(16, 12, 16, 12)))
            self.cards.addWidget(f)

    def add(self, provider: str, default: str) -> None:
        taken = {a["name"] for a in self.wiz.state["accounts"]}
        name = default
        n = 2
        while name in taken:
            name, n = f"{default}-{n}", n + 1
        key = ""
        if provider.endswith("-key"):
            key, ok = QInputDialog.getText(self, "API key", "Paste the API key (it's stored in its own volume):",
                                           QLineEdit.Password)
            if not ok or not key.strip():
                return

        def go():
            r = common.api.post("/api/accounts", {"name": name, "provider": provider})
            if r["signin"]:
                return common.api.post(f"/api/accounts/{name}/login")
            common.api.post(f"/api/accounts/{name}/key", {"key": key.strip()})
            return None

        def ok(job):
            if job:
                JobDialog(self, job, lambda _j: self.wiz.reload(then=self.enter)).show()
            self.wiz.reload(then=self.enter)
        bg(go, ok)


class Models(Step):
    title = "Local models"
    subtitle = "Models that run on this computer: private, free to use, and shared by every agent."

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        self.hw = label("", wrap=True)
        self.list = vbox(spacing=8)
        inner = QWidget()
        inner.setObjectName("scrollbody")
        inner.setLayout(vbox(self.list, "stretch"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(300)
        self.go = button("Download selected", "primary", self.download)
        self.setLayout(vbox(self.hw, scroll, hbox(label("Downloads keep going while you finish setup.", "dim"),
                                                    "stretch", self.go), spacing=10))
        self.checks: dict[str, QCheckBox] = {}

    def enter(self) -> None:
        s = self.wiz.state
        hw = s["hardware"]
        self.hw.setText(f"<b>This computer:</b> {s['hardware_summary']}<br><span style='color:{C['dim']}'>"
                        f"About {hw['model_budget_gb']:.0f} GB is free for a model. {hw['note']}</span>")
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.checks.clear()
        if not s["lmstudio"]:
            if s["host"]["dev"]:
                self.list.addWidget(label("LM Studio isn't installed. On a Mac, get it from lmstudio.ai, open it "
                                          "once, then come back to this step.", "dim", wrap=True))
            else:
                self.list.addWidget(label("Local models run in LM Studio. Install its background server "
                                          "(about 500 MB), then pick models.", wrap=True))
                self.list.addWidget(button("Install LM Studio", "primary", lambda: bg(
                    lambda: common.api.post("/api/setup/lmstudio"),
                    lambda j: JobDialog(self, j, lambda _j: self.wiz.reload(then=self.enter)).show())))
            self.go.hide()
            return
        self.go.show()
        for m in s["models"]:
            f = QFrame()
            f.setObjectName("panel")
            cb = QCheckBox()
            cb.setChecked(m["recommended"] and not m["downloaded"])
            cb.setEnabled(m["fits"] and not m["downloaded"])
            self.checks[m["key"]] = cb
            chips = [chip("Recommended", C["accent2"]) if m["recommended"] else None,
                     chip("Sees the screen", C["green"]) if m["vision"] else chip("Text only", C["dim"]),
                     chip("Reasons", C["cyan"]) if m["reasoning"] else None,
                     chip("Downloaded", C["green"]) if m["downloaded"] else None,
                     chip("Too big for this computer", C["red"]) if not m["fits"] else
                     chip("Tight fit", C["yellow"]) if m["tight"] else None]
            f.setLayout(hbox(cb, vbox(hbox(label(f"<b>{m['name']}</b>"), *chips, "stretch", spacing=6),
                                      label(f"{m['blurb']} Needs {m['min_gb']} GB.", "dim", wrap=True), spacing=2),
                             margins=(12, 10, 14, 10), spacing=12))
            if not m["fits"]:
                f.setStyleSheet("QFrame#panel { background: transparent; }")
            self.list.addWidget(f)

    def download(self) -> None:
        keys = [k for k, cb in self.checks.items() if cb.isChecked() and cb.isEnabled()]
        for k in keys:
            bg(lambda k=k: common.api.post("/api/models/download", {"query": k}),
               lambda j: self.wiz.track(j), lambda e: self.wiz.toast("Download failed", str(e), True))
            self.checks[k].setEnabled(False)
        if keys:
            self.wiz.toast(f"Downloading {len(keys)} model{'s' * (len(keys) > 1)}", "You can keep going.")

    def commit(self, then) -> None:
        if any(cb.isChecked() and cb.isEnabled() for cb in self.checks.values()):
            self.download()
        then()


HARNESS_OF = {"claude": "claude", "anthropic-key": "claude", "codex": "codex", "openai-key": "codex"}


def pilot_choices(opts: dict, downloading: list[str], budget_gb: float = 1e9) -> list[tuple[str, dict]]:
    """Everything the pilot could use: accounts first (Claude, then Codex),
    then local models that fit this machine (vision first, biggest first), then
    ones that don't fit, then ones still downloading."""
    out = []
    for harness, title in (("claude", "Claude Code"), ("codex", "Codex")):
        for a in opts["accounts"]:
            if a["harness"] == harness:
                out.append((f"{title} · {a['name']}", {"model": harness, "account": a["name"]}))
    local = sorted(opts["local_models"], key=lambda m: (m["size_gb"] > budget_gb, not m["vision"], -m["size_gb"]))
    for m in local:
        big = m["size_gb"] > budget_gb
        out.append((f"{m['name']} (local{', sees the screen' if m['vision'] else ''}"
                    f"{', too big for this computer' if big else ''})", {"model": f"local:{m['key']}", "account": ""}))
    have = {v["model"] for _, v in out}
    for key in downloading:
        if f"local:{key}" not in have:
            out.append((f"{key} (local, downloading…)", {"model": f"local:{key}", "account": ""}))
    return out


class Pilot(Step):
    title = "Your main agent"
    subtitle = ("The pilot is who you talk to: press Meta+Space (or the Print key after a screenshot) and ask. "
                "It answers, or puts agents to work for you.")
    required = True

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        from PySide6.QtWidgets import QSpinBox
        self.model = QComboBox()
        self.boxes = vbox(spacing=4)
        self.cap = QSpinBox()
        self.cap.setRange(1, 16)
        self.cap.setValue(4)
        self.empty = label("Connect an AI account or pick a local model first (go Back), and the pilot will use "
                           "it. You can also set it later in Hangar Settings → Orchestration.", "dim", wrap=True)
        self.checks: list = []
        self.setLayout(vbox(self.empty, label("The pilot uses", "dim"), self.model,
                            label("Models it may give the agents it starts", "dim"), self.boxes,
                            hbox(label("Most agents running at once", "dim"), self.cap, "stretch"),
                            label("You can change all of this later in Hangar Settings → Orchestration.", "dim"),
                            "stretch", spacing=8))

    def enter(self) -> None:
        def got(opts) -> None:
            self.choices = pilot_choices(opts, self.wiz.downloading(), self.wiz.budget())
            self.model.clear()
            for lbl, v in self.choices:
                self.model.addItem(lbl, v)
            while self.boxes.count():
                w = self.boxes.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self.checks = []
            for lbl, v in self.choices:
                cb = QCheckBox(lbl)
                cb.setChecked("too big" not in lbl and "downloading" not in lbl or v == self.model.itemData(0))
                self.boxes.addWidget(cb)
                self.checks.append((cb, v))
            self.empty.setVisible(not self.choices)
            self.model.setVisible(bool(self.choices))
        bg(lambda: common.api.get("/api/options"), got)

    def settings(self) -> dict | None:
        if not getattr(self, "choices", None):
            return None
        allowed = [v for cb, v in self.checks if cb.isChecked()] or [self.model.currentData()]
        return {"pilot": self.model.currentData(), "allowed": allowed, "max_agents": self.cap.value(),
                "internet": "open"}

    def commit(self, then) -> None:
        st = self.settings()
        if st is None:
            then()
            return
        bg(lambda: common.api.put("/api/orchestration", st), lambda _: then(),
           lambda e: self.wiz.toast("Couldn't save", str(e), True))


class Notifications(Step):
    title = "Get notified"
    subtitle = "Hangar tells you when an agent finishes, hits a problem, or needs you."

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        self.topic = f"hangar-{secrets.token_hex(5)}"
        self.on = QCheckBox("Send notifications to my phone with ntfy (free, no account)")
        self.on.setChecked(True)
        self.qr = label()
        self.how = label("", "dim", wrap=True)
        self.slack = QLineEdit()
        self.slack.setPlaceholderText("Slack incoming-webhook URL (optional)")
        self.slack.setEchoMode(QLineEdit.Password)
        self.tg_token = QLineEdit()
        self.tg_token.setPlaceholderText("Telegram bot token (optional)")
        self.tg_token.setEchoMode(QLineEdit.Password)
        self.tg_chat = QLineEdit()
        self.tg_chat.setPlaceholderText("Telegram chat ID")
        self.test = button("Send a test", on=self.send_test)
        self.setLayout(vbox(self.on, hbox(self.qr, vbox(self.how, self.test, "stretch", spacing=8), spacing=18),
                            label("Also, if you like:", "dim"), self.slack, hbox(self.tg_token, self.tg_chat),
                            "stretch", spacing=10))
        self.on.toggled.connect(lambda v: (self.qr.setVisible(v), self.how.setVisible(v), self.test.setVisible(v)))

    def enter(self) -> None:
        import segno
        url = f"https://ntfy.sh/{self.topic}"
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="png", scale=5, dark="#e8eaf0", light="#161a23", border=2)
        pm = QPixmap()
        pm.loadFromData(buf.getvalue())
        self.qr.setPixmap(pm)
        self.how.setText(f"1. Install the <b>ntfy</b> app on your phone.<br>2. Tap <b>+</b>, then scan this code "
                         f"or type the topic <b>{self.topic}</b>.<br><br>The topic name works like a password, "
                         "so only share it with people who should see your agents' updates.")

    def save(self) -> None:
        if self.on.isChecked():
            common.api.put("/api/notify/ntfy", {"url": f"https://ntfy.sh/{self.topic}", "token": ""})
        if self.slack.text().strip():
            common.api.put("/api/notify/slack", {"webhook": self.slack.text().strip()})
        if self.tg_token.text().strip():
            common.api.put("/api/notify/telegram", {"bot_token": self.tg_token.text().strip(),
                                                    "chat_id": self.tg_chat.text().strip()})

    def send_test(self) -> None:
        def go():
            self.save()
            return common.api.post("/api/notify/test")
        bg(go, lambda r: self.wiz.toast("Test sent", " · ".join(f"{k}: {v}" for k, v in r["results"].items())),
           lambda e: self.wiz.toast("Couldn't send", str(e), True))

    def commit(self, then) -> None:
        bg(self.save, lambda _: then(), lambda e: self.wiz.toast("Couldn't save", str(e), True))


class Done(Step):
    title = "You're all set"
    subtitle = "Here's how Hangar works."

    def __init__(self, wiz) -> None:
        super().__init__(wiz)
        tips = [("✦", "Your pilot", "Ask it anything in Hangar, or press Meta+Space anywhere (after a "
                                     "screenshot too). It answers, or puts a crew of agents on the job and "
                                     "keeps you posted in the conversation."),
                ("🛰", "Spaces", "Every agent works on a desktop of its own. Watch them live, take over any "
                                  "time, and press Meta+S to see them all at once."),
                ("🔍", "Review", "See exactly what agents changed, undo any file, or open it in Zed."),
                ("📝", "Notes", "Agents keep what they find in your Notes (plain Markdown, Obsidian-friendly)."),
                ("🔑", "Passwords", "When an agent needs to sign in, it asks. You approve, and Hangar fills it "
                                     "in without the agent ever seeing the password.")]
        rows = []
        for icon, t, d in tips:
            ic = label(icon)
            ic.setStyleSheet("font-size: 26px")
            ic.setFixedWidth(40)
            rows.append(hbox(ic, vbox(label(f"<b>{t}</b>"), label(d, "dim", wrap=True), spacing=2), spacing=12))
        self.launch = button("Say hello to your pilot", "primary", self.launch_first)
        self.launch.setMinimumHeight(40)
        self.setLayout(vbox(*rows, "stretch", hbox("stretch", self.launch, "stretch"), spacing=16))

    def launch_first(self) -> None:
        def go():
            self.wiz.ensure_pilot()
            common.api.post("/api/setup/done", {"done": True})
            subprocess.Popen([sys.executable, "-m", "hangar.apps.home"], start_new_session=True)
        bg(go, lambda _: self.wiz.close())


STEPS = [Welcome, Network, Accounts, Models, Pilot, Notifications, Done]


class Backdrop(QWidget):
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#141a33"))
        g.setColorAt(0.55, QColor(C["bg"]))
        g.setColorAt(1, QColor("#1a1230"))
        p.fillRect(self.rect(), g)
        p.end()


class SetupWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1000, 760)
        self.state: dict | None = None
        self.jobs: dict[str, dict] = {}
        self.steps = [cls(self) for cls in STEPS]
        self.stack = QStackedWidget()
        for s in self.steps:
            self.stack.addWidget(s)
        self.title = label("", "h1")
        self.title.setStyleSheet("font-size: 26px; font-weight: 650;")
        self.title.setAlignment(Qt.AlignCenter)
        self.sub = label("", "dim", wrap=True)
        self.sub.setAlignment(Qt.AlignCenter)
        self.back = button("Back", "ghost", lambda: self.go(self.i - 1))
        self.skip = button("Skip", "ghost", lambda: self.go(self.i + 1))
        self.next = button("Continue", "primary", self.cont)
        self.next.setMinimumWidth(120)
        self.dots = label()
        self.downloads = label("", "dim")
        # Equal-width sides keep the progress dots centred whichever buttons show.
        left, right = QWidget(), QWidget()
        left.setLayout(hbox(self.back, "stretch"))
        right.setLayout(hbox("stretch", self.skip, self.next))
        left.setFixedWidth(220)
        right.setFixedWidth(220)
        card = QFrame()
        card.setObjectName("panel")
        card.setStyleSheet(f"QFrame#panel {{ background: rgba(22,26,35,.94); border: 1px solid {C['line']};"
                           "border-radius: 18px; }")
        card.setMaximumWidth(760)
        card.setMinimumWidth(560)
        card.setLayout(vbox(self.title, self.sub, self.stack,
                            hbox(left, "stretch", self.dots, "stretch", right),
                            spacing=14, margins=(36, 30, 36, 24)))
        root = Backdrop()
        root.setLayout(vbox("stretch", hbox("stretch", card, "stretch"), hbox("stretch", self.downloads, "stretch"),
                            "stretch", margins=(24, 24, 24, 24), spacing=10))
        self.setCentralWidget(root)
        self.toasts = common.Toasts(root)
        self.i = 0
        t = QTimer(self)
        t.timeout.connect(self.poll_jobs)
        t.start(1500)
        self.reload(then=lambda: self.go(0))

    def toast(self, title: str, msg: str = "", error: bool = False, on_click=None) -> None:
        self.toasts.show_toast(title, msg, error, on_click)

    def reload(self, then=None) -> None:
        def got(s):
            self.state = s
            if then:
                then()
        bg(lambda: common.api.get("/api/setup"), got)

    def go(self, i: int) -> None:
        if self.state is None:
            return
        self.i = max(0, min(i, len(self.steps) - 1))
        step = self.steps[self.i]
        self.stack.setCurrentWidget(step)
        self.title.setText(step.title)
        self.sub.setText(step.subtitle)
        self.back.setVisible(self.i > 0)
        last = self.i == len(self.steps) - 1
        self.skip.setVisible(not last and self.i > 0 and not getattr(step, "required", False))
        self.next.setText("Finish" if last else "Continue")
        self.dots.setText(" ".join(
            f"<span style='color:{C['accent'] if n == self.i else C['line']}; font-size:16px'>●</span>"
            for n in range(len(self.steps))))
        step.enter()

    def cont(self) -> None:
        step = self.steps[self.i]
        if self.i == len(self.steps) - 1:
            bg(lambda: (self.ensure_pilot(), common.api.post("/api/setup/done", {"done": True})),
               lambda _: self.close())
            return
        self.next.setEnabled(False)

        def then():
            self.next.setEnabled(True)
            self.reload(then=lambda: self.go(self.i + 1))
        step.commit(then)
        QTimer.singleShot(8000, lambda: self.next.setEnabled(True))

    def budget(self) -> float:
        return float(((self.state or {}).get("hardware") or {}).get("model_budget_gb", 1e9))

    def downloading(self) -> list[str]:
        return [j["title"].removeprefix("Download ") for j in self.jobs.values()
                if j.get("kind") == "download" and j["title"].startswith("Download ") and "/" in j["title"]]

    def ensure_pilot(self) -> None:
        """Runs in a worker: if the pilot was never set, use the best available
        model (Claude, then Codex, then the strongest local model) and allow all."""
        cur = common.api.get("/api/orchestration")
        if cur["pilot"]["model"]:
            return
        choices = pilot_choices(common.api.get("/api/options"), self.downloading(), self.budget())
        if choices:
            common.api.put("/api/orchestration", {"pilot": choices[0][1], "allowed": [v for _, v in choices],
                                                  "max_agents": 4, "internet": "open"})

    def track(self, job: dict) -> None:
        self.jobs[job["id"]] = job
        self.poll_jobs()

    def poll_jobs(self) -> None:
        if not self.jobs:
            self.downloads.setText("")
            return

        def got(js):
            for j in js:
                self.jobs[j["id"]] = j
            running = [j for j in self.jobs.values() if not j["done"]]
            failed = [j for j in self.jobs.values() if j["done"] and j["code"]]
            parts = []
            for j in running:
                pct = next((w for line in reversed(j["lines"]) for w in line.split() if w.endswith("%")), "")
                parts.append(f"{j['title'].removeprefix('Download ')} {pct}".strip())
            text = ("Downloading: " + " · ".join(parts)) if parts else "Downloads finished."
            if failed:
                text += f"  ({len(failed)} failed: see Models)"
            self.downloads.setText(text)
        ids = list(self.jobs)
        bg(lambda: [common.api.get(f"/api/jobs/{i}") for i in ids], got, lambda _e: None)


def main() -> None:
    first = "--first-boot" in sys.argv
    app = common.make_app("setup")
    if first and common.api.get("/api/setup")["done"]:
        return   # first-boot autostart: nothing to do once setup has run
    w = SetupWindow()
    w.setWindowTitle("Hangar Setup")
    if first and "--windowed" not in sys.argv:
        w.showFullScreen()
    else:
        w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
