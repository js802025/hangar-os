"""The smaller Hangar apps: Accounts, Models and Settings.

Each is its own app (own window and taskbar entry); they share this module.
  python -m hangar.apps.accounts | models | settings
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QInputDialog, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QScrollArea, QWidget)

from hangar.apps import common
from hangar.apps.common import C, bg, button, hbox, label, vbox


def panel(title: str, *rows, sub: str = "") -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    f.setLayout(vbox(hbox(label(title, "h2"), "stretch"), label(sub, "dim", wrap=True) if sub else None, *rows,
                     spacing=10, margins=(18, 16, 18, 16)))
    return f


def table(headers: list[str], rows: list[list]) -> QWidget:
    w = QWidget()
    g = QGridLayout()
    g.setHorizontalSpacing(16)
    g.setVerticalSpacing(10)
    for c, h in enumerate(headers):
        g.addWidget(label(h, "dim"), 0, c)
    for r, row in enumerate(rows, 1):
        for c, cell in enumerate(row):
            if cell is None:
                continue
            g.addWidget(cell if isinstance(cell, QWidget) else label(str(cell), wrap=True), r, c)
    g.setColumnStretch(len(headers), 1)   # spare space goes to an empty last column
    w.setLayout(g)
    return w


class Page(QMainWindow):
    """Window with a scrolling column of panels, toasts and a reload()."""

    def __init__(self, title: str, sub: str = "") -> None:
        super().__init__()
        self.resize(980, 720)
        self.body = vbox(spacing=16)
        holder = QWidget()
        holder.setObjectName("scrollbody")
        holder.setLayout(vbox(label(title, "h1"), label(sub, "dim", wrap=True) if sub else None, self.body,
                              "stretch", spacing=8, margins=(24, 20, 24, 24)))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        root = QWidget()
        root.setObjectName("page")
        root.setLayout(vbox(scroll))
        self.setCentralWidget(root)
        self.toasts = common.Toasts(root)
        QTimer.singleShot(0, self.reload)

    def toast(self, title: str, msg: str = "", error: bool = False, on_click=None) -> None:
        self.toasts.show_toast(title, msg, error, on_click)

    def set_panels(self, *panels: QWidget) -> None:
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w:
                w.deleteLater()
        for p in panels:
            if p is not None:
                self.body.addWidget(p)

    def reload(self) -> None:
        raise NotImplementedError


class JobDialog(QDialog):
    """Watches a sign-in or download running in the daemon."""

    def __init__(self, parent: QWidget, job: dict, on_done=None) -> None:
        super().__init__(parent)
        self.job, self.on_done = job, on_done
        self.setWindowTitle(job["title"])
        self.setMinimumSize(640, 460)
        self.setStyleSheet(f"QDialog {{ background: {C['panel']}; }}")
        self.links = hbox()
        self.term = QPlainTextEdit()
        self.term.setReadOnly(True)
        self.term.setStyleSheet("font-family: Menlo, 'DejaVu Sans Mono', monospace; font-size: 12px; background:#07080b;")
        self.code = QLineEdit()
        self.code.setPlaceholderText("Paste the code from the sign-in page")
        self.code.returnPressed.connect(self.send)
        self.input = QWidget()
        self.input.setLayout(hbox(self.code, button("Send", "primary", self.send)))
        self.input.setVisible(job["kind"] == "login")
        self.status = label("Starting…", "dim")
        self.cancel = button("Cancel", "danger", lambda: bg(lambda: common.api.delete(f"/api/jobs/{job['id']}")))
        self.setLayout(vbox(
            label(job["title"], "h2"),
            label("This is the official sign-in. Open the link, sign in in your browser, then paste back any "
                  "code it shows. Hangar never sees your password.", "dim", wrap=True) if job["kind"] == "login" else None,
            self.links, self.term, self.input,
            hbox(self.status, "stretch", self.cancel, button("Close", on=self.close)),
            spacing=10, margins=(20, 18, 20, 18)))
        self.urls: list[str] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(1000)
        self.poll()

    def send(self) -> None:
        text = self.code.text().strip()
        if text:
            bg(lambda: common.api.post(f"/api/jobs/{self.job['id']}/input", {"text": text}),
               lambda _: self.code.clear())

    def poll(self) -> None:
        bg(lambda: common.api.get(f"/api/jobs/{self.job['id']}"), self.show_job, lambda _e: None)

    def show_job(self, j: dict) -> None:
        text = "\n".join(j["lines"]) or "Starting…"
        if text != self.term.toPlainText():
            self.term.setPlainText(text)
            self.term.verticalScrollBar().setValue(self.term.verticalScrollBar().maximum())
        if j["urls"] != self.urls:
            self.urls = j["urls"]
            while self.links.count():
                self.links.takeAt(0).widget().deleteLater()
            for u in j["urls"]:
                self.links.addWidget(button("Open sign-in page ↗" if j["kind"] == "login" else "Open link ↗", "primary",
                                            lambda _=False, u=u: QDesktopServices.openUrl(QUrl(u))))
            self.links.addStretch(1)
        if j["done"]:
            self.timer.stop()
            self.status.setText("Finished." if j["code"] == 0 else f"Exited with code {j['code']}.")
            self.input.hide()
            self.cancel.hide()
            if self.on_done:
                self.on_done(j)
        else:
            self.status.setText("Running…")

    def closeEvent(self, e) -> None:
        self.timer.stop()
        super().closeEvent(e)


# -- Accounts ------------------------------------------------------------------------------
class AccountsWindow(Page):
    def __init__(self) -> None:
        super().__init__("Accounts", "Subscriptions and API keys. Each lives in its own volume, mounted only into "
                                     "the agents that use it.")

    def reload(self) -> None:
        bg(lambda: common.api.get("/api/accounts"), self.render)

    def render(self, d: dict) -> None:
        name = QLineEdit()
        name.setPlaceholderText("personal")
        provider = QComboBox()
        for k, v in d["providers"].items():
            provider.addItem(v, k)
        key = QLineEdit()
        key.setEchoMode(QLineEdit.Password)
        key.setPlaceholderText("API key")
        key.hide()
        provider.currentIndexChanged.connect(lambda _i: key.setVisible(provider.currentData().endswith("-key")))
        err = label("", wrap=True)
        err.setStyleSheet(f"color:{C['red']}")

        def add() -> None:
            n, p, k = name.text().strip().lower(), provider.currentData(), key.text()
            err.setText("")

            def go():
                r = common.api.post("/api/accounts", {"name": n, "provider": p})
                if r["signin"]:
                    return ("login", common.api.post(f"/api/accounts/{n}/login"))
                common.api.post(f"/api/accounts/{n}/key", {"key": k})
                return ("key", None)

            def ok(res):
                kind, job = res
                if kind == "login":
                    JobDialog(self, job, lambda _j: self.reload()).show()
                else:
                    self.toast(f"Saved {n}", "The key is stored in the account's volume.")
                self.reload()
            bg(go, ok, lambda e: err.setText(str(e)))

        rows = []
        for a in d["accounts"]:
            status = label("", "dim", wrap=True)

            def check(_=False, a=a, status=status):
                status.setText("checking…")
                bg(lambda: common.api.get(f"/api/accounts/{a['name']}/status"), lambda r: status.setText(r["status"]))

            def signin(_=False, a=a):
                bg(lambda: common.api.post(f"/api/accounts/{a['name']}/login"), lambda j: JobDialog(self, j).show())

            def replace(_=False, a=a):
                k, ok = QInputDialog.getText(self, "Replace key", f"New API key for {a['name']}", QLineEdit.Password)
                if ok and k:
                    bg(lambda: common.api.post(f"/api/accounts/{a['name']}/key", {"key": k}),
                       lambda _: self.toast("Key replaced"))

            def delete(_=False, a=a):
                if QMessageBox.question(self, "Delete account", f"Delete {a['name']} and its stored sign-in?") \
                        == QMessageBox.Yes:
                    bg(lambda: common.api.delete(f"/api/accounts/{a['name']}"),
                       lambda _: (self.toast("Deleted"), self.reload()))
            acts = QWidget()
            acts.setLayout(hbox(button("Status", "ghost", check),
                                button("Sign in", on=signin) if a["signin"] else button("Replace key", on=replace),
                                button("Delete", "danger", delete)))
            rows.append([label(f"<b>{a['name']}</b>"), a["label"], ", ".join(a["agents"]) or "none", status, acts])
        self.set_panels(
            panel("Add an account", hbox(name, provider, key, button("Add account", "primary", add)), err),
            panel("Your accounts", table(["Name", "Type", "Used by", "Status", ""], rows) if rows else
                  label("No accounts yet. Add a Claude or ChatGPT subscription, or an API key.", "dim")))


# -- Models ---------------------------------------------------------------------------------
class ModelsWindow(Page):
    def __init__(self) -> None:
        super().__init__("Models", "Local models run in LM Studio on this machine and are shared by every agent.")

    def reload(self) -> None:
        bg(lambda: common.api.get("/api/models"), self.render)

    def render(self, d: dict) -> None:
        if not d["installed"]:
            self.set_panels(panel("LM Studio isn't installed",
                                  label("Install LM Studio from lmstudio.ai and open it once, then reopen Models.", "dim")))
            return
        q = QLineEdit()
        q.setPlaceholderText("e.g. qwen/qwen2.5-vl-7b")

        def download():
            text = q.text().strip()
            bg(lambda: common.api.post("/api/models/download", {"query": text}),
               lambda j: (q.clear(), JobDialog(self, j, lambda _j: self.reload()).show()))
        q.returnPressed.connect(download)
        state = label("● server running" if d["server"] else "● server stopped")
        state.setStyleSheet(f"color:{C['green'] if d['server'] else C['dim']}")
        start = None if d["server"] else button("Start server", on=lambda: bg(
            lambda: common.api.post("/api/models/serve"), lambda _: (self.toast("Server started"), self.reload())))
        rows = []
        for m in d["models"]:
            def delete(_=False, m=m):
                if QMessageBox.question(self, "Delete model", f"Delete {m['name']} from disk?") == QMessageBox.Yes:
                    bg(lambda: common.api.post("/api/models/remove", {"key": m["key"]}),
                       lambda _: (self.toast("Deleted"), self.reload()))
            yes = lambda b: label("yes") if not b else label(f"<span style='color:{C['green']}'>yes</span>")
            rows.append([label(f"<b>{m['name']}</b><br><span style='color:{C['dim']}'>{m['key']}</span>"),
                         f"{m['size_gb']} GB", yes(m["vision"]) if m["vision"] else label("no", "dim"),
                         yes(m["tools"]) if m["tools"] else label("no", "dim"), "●" if m["loaded"] else "",
                         button("Delete", "danger", delete)])
        self.set_panels(
            panel("LM Studio", hbox(state, "stretch", start), hbox(q, button("Download", "primary", download)),
                  sub=f"Agents reach it at {d['agent_url']}. Vision models can use screenshots; text-only models "
                      "use the browser's text tools."),
            panel("Downloaded", table(["Model", "Size", "Vision", "Tools", "Loaded", ""], rows) if rows else
                  label("No models downloaded yet.", "dim")))


# -- Settings ----------------------------------------------------------------------------------
LABELS = {"url": "Topic URL", "token": "Access token (optional)", "webhook": "Incoming webhook URL",
          "bot_token": "Bot token", "chat_id": "Chat ID"}
HELP = {"ntfy": "Easiest for phones: install the ntfy app and subscribe to a hard-to-guess topic.",
        "slack": "Create an incoming webhook for a channel.",
        "telegram": "Make a bot with @BotFather, then message it once."}


class SettingsWindow(Page):
    def __init__(self) -> None:
        super().__init__("Hangar Settings")

    def reload(self) -> None:
        bg(lambda: (common.api.get("/api/notify"), common.api.get("/api/settings")), self.render)

    def render(self, res) -> None:
        n, s = res
        channels = []
        for ch, fields in n["fields"].items():
            cur = n["channels"].get(ch, {})
            inputs = {}
            rows = []
            for f in fields:
                e = QLineEdit(cur.get(f, ""))
                if f in n["secret_fields"]:
                    e.setEchoMode(QLineEdit.Password)
                if f == "url":
                    e.setPlaceholderText("https://ntfy.sh/hangar-7f3k2q")
                inputs[f] = e
                rows += [label(LABELS[f], "dim"), e]

            def save(_=False, ch=ch, inputs=inputs):
                body = {k: v.text() for k, v in inputs.items()}
                bg(lambda: common.api.put(f"/api/notify/{ch}", body), lambda _: (self.toast(f"Saved {ch}"), self.reload()))

            def remove(_=False, ch=ch):
                bg(lambda: common.api.delete(f"/api/notify/{ch}"), lambda _: (self.toast(f"Removed {ch}"), self.reload()))
            on = label("● on" if ch in n["channels"] else "○ off")
            on.setStyleSheet(f"color:{C['green'] if ch in n['channels'] else C['dim']}")
            box = QFrame()
            box.setLayout(vbox(hbox(label(f"<b>{ch.capitalize()}</b>"), "stretch", on), label(HELP[ch], "dim", wrap=True),
                               *rows, hbox(button("Save", "primary", save),
                                           button("Remove", "ghost", remove) if ch in n["channels"] else None, "stretch"),
                               spacing=6, margins=(0, 12, 0, 4)))
            channels.append(box)
        results = label("", "dim", wrap=True)

        def test():
            results.setText("sending…")
            bg(lambda: common.api.post("/api/notify/test"),
               lambda r: results.setText(" · ".join(f"{k}: {v}" for k, v in r["results"].items()) or "No channels set"))

        lan = QCheckBox("Open Hangar from other devices on my network")
        lan.setChecked(s["lan"])
        restart = button("Restart the daemon to apply", "primary")
        restart.hide()

        def toggle(on):
            bg(lambda: common.api.put("/api/settings", {"lan": bool(on)}), lambda _: restart.show())

        def do_restart():
            restart.setEnabled(False)
            restart.setText("Restarting…")
            bg(lambda: common.api.post("/api/daemon/restart"), lambda _: QTimer.singleShot(2500, self.reload),
               lambda _e: QTimer.singleShot(2500, self.reload))
        lan.toggled.connect(toggle)
        restart.clicked.connect(do_restart)
        qr = None
        if s["qr"]:
            from PySide6.QtSvg import QSvgRenderer
            r = QSvgRenderer(QByteArray(s["qr"].encode()))
            pm = QPixmap(220, 220)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            r.render(p)
            p.end()
            qr = label()
            qr.setPixmap(pm)
        urls = "<br>".join(s["lan_urls"])
        self.set_panels(
            panel("Notifications", hbox(button("Send test", on=test), results, "stretch"), *channels,
                  sub="You get a notification when an agent finishes, hits a problem or needs you."),
            panel("Access", label(f"This machine: <code>{s['local_url']}</code>" +
                                  (f"<br>On your network: <code>{urls}</code>" if urls else ""), select=True),
                  lan, label("Everything still needs the admin token. Traffic on your network isn't encrypted, so "
                             "only turn this on for a network you trust.", "dim", wrap=True), restart,
                  label("Scan to open Hangar on your phone (signs it in):", "dim") if qr else None, qr),
            panel("Setup", hbox(button("Run setup again", on=lambda: subprocess.Popen(
                [sys.executable, "-m", "hangar.apps.setup"], start_new_session=True)), "stretch"),
                  sub="Walk through accounts, local models and notifications again."),
            panel("About", label(f"State: <code>{s['state_dir']}</code><br>Config: <code>{s['config_dir']}</code><br>"
                                 "Admin token: <code>hangar daemon token</code>", select=True)))
