"""Hangar: the Spaces app. Every agent as a live thumbnail; open one to watch
its desktop, take over, give it a task, read its reports and logs.

  python -m hangar.apps.spaces      (or: hangar open spaces)
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QScrollArea, QStackedWidget,
                               QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from hangar.apps import common
from hangar.apps.common import C, badge, bg, button, hbox, label, vbox
from hangar.apps.vnc import VncView

THUMB_W = 480


def model_label(m: str) -> str:
    return "no model" if not m else m[6:].split("/")[-1] if m.startswith("local:") else m


def ago(ts: str) -> str:
    try:
        s = (datetime.now().astimezone() - datetime.fromisoformat(ts)).total_seconds()
    except (TypeError, ValueError):
        return ""
    return ("just now" if s < 60 else f"{int(s // 60)}m ago" if s < 3600 else
            f"{int(s // 3600)}h ago" if s < 86400 else datetime.fromisoformat(ts).strftime("%b %d"))


class AgentCard(QFrame):
    def __init__(self, win: "SpacesWindow", name: str) -> None:
        super().__init__()
        self.win, self.name, self.running, self.loading = win, name, False, False
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.thumb = QLabel()
        self.thumb.setFixedHeight(200)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#07080b; border-top-left-radius:12px; border-top-right-radius:12px;"
                                 f"color:{C['dim']}")
        self.badge_slot = hbox(margins=(10, 10, 0, 0))
        overlay = QWidget(self.thumb)
        overlay.setStyleSheet("background: transparent")
        overlay.setLayout(self.badge_slot)
        self.title = label("", "h2")
        self.sub = label("", "dim")
        self.task = label("", wrap=True)
        self.report = label("", "dim", wrap=True)
        self.setLayout(vbox(self.thumb, vbox(self.title, self.sub, self.task, self.report, spacing=3,
                                             margins=(14, 10, 14, 14)), spacing=0))

    def mousePressEvent(self, _e) -> None:
        self.win.open_agent(self.name)

    def update_from(self, a: dict) -> None:
        self.running = a["status"] == "running"
        while self.badge_slot.count():
            w = self.badge_slot.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.badge_slot.addWidget(badge(a["state"]))
        self.badge_slot.parentWidget().adjustSize()
        self.title.setText(a["name"])
        self.sub.setText(f"{a['flavor']} · {model_label(a['model'])}" + (f" · {a['account']}" if a["account"] else ""))
        self.task.setText((a["task"] or "No task")[:160])
        self.report.setText((a["report"] or {}).get("message", "")[:160])
        if not self.running:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("Stopped" if a["status"] == "exited" else a["status"])

    def refresh_thumb(self) -> None:
        if not self.running or self.loading:
            return
        self.loading = True

        def got(data: bytes) -> None:
            self.loading = False
            pm = QPixmap()
            if pm.loadFromData(data):
                self.thumb.setPixmap(pm.scaled(self.thumb.width(), self.thumb.height(),
                                               Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

        def failed(_e) -> None:
            self.loading = False
        bg(lambda: common.api.call("GET", f"/api/agents/{self.name}/screenshot.png?w={THUMB_W}", raw=True),
           got, failed)


class Grid(QWidget):
    def __init__(self, win: "SpacesWindow") -> None:
        super().__init__()
        self.win = win
        self.cards: dict[str, AgentCard] = {}
        self.grid = QGridLayout()
        self.grid.setSpacing(18)
        inner = QWidget()
        inner.setObjectName("scrollbody")
        inner.setLayout(vbox(self.grid, "stretch"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self.count = label("", "dim")
        self.empty = QWidget()
        self.empty.setLayout(vbox("stretch", label("No agents yet", "h1"),
                                  label("Each agent gets its own hidden desktop. Give one a task and watch it work here.", "dim"),
                                  hbox("stretch", button("Launch your first agent", "primary", win.new_agent), "stretch"),
                                  "stretch", spacing=12))
        for w in self.empty.findChildren(QLabel):
            w.setAlignment(Qt.AlignCenter)
        self.setLayout(vbox(hbox(label("Spaces", "h1"), self.count, "stretch"), self.empty, scroll, spacing=14))
        self.scroll = scroll
        self.cols = 0

    def render(self, agents: list[dict]) -> None:
        self.empty.setVisible(not agents)
        self.scroll.setVisible(bool(agents))
        working = sum(a["state"] == "working" for a in agents)
        self.count.setText(f"{len(agents)} agent{'s' * (len(agents) != 1)} · {working} working" if agents else "")
        names = [a["name"] for a in agents]
        for n in list(self.cards):
            if n not in names:
                self.cards.pop(n).deleteLater()
        for a in agents:
            card = self.cards.get(a["name"]) or self.cards.setdefault(a["name"], AgentCard(self.win, a["name"]))
            card.update_from(a)
        self.relayout(force=True)

    def relayout(self, force: bool = False) -> None:
        cols = max(1, self.width() // 360)
        if cols == self.cols and not force:
            return
        self.cols = cols
        for i, name in enumerate(sorted(self.cards)):
            self.grid.addWidget(self.cards[name], i // cols, i % cols)
        for c in range(4):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.relayout()

    def tick(self) -> None:
        for c in self.cards.values():
            c.refresh_thumb()


class AgentPage(QWidget):
    """One agent: live desktop, take over, next task, reports / logs / about."""

    def __init__(self, win: "SpacesWindow", name: str) -> None:
        super().__init__()
        self.win, self.name = win, name
        self.agent: dict | None = None
        self.view = VncView(name)
        self.state_slot = hbox()
        self.actions = hbox()
        self.banner = QFrame()
        self.banner.setStyleSheet("background: rgba(232,121,249,.1); border: 1px solid rgba(232,121,249,.35);"
                                  "border-radius: 10px;")
        self.banner.setLayout(hbox(label("You're in control. The agent's mouse and keyboard tools are paused."),
                                   "stretch", button("Hand back to agent", "primary", self.release),
                                   margins=(12, 8, 12, 8)))
        self.banner.hide()
        self.task = QLineEdit()
        self.task.setPlaceholderText("Give it a new task…")
        self.task.returnPressed.connect(self.run_task)
        self.taskbar = QWidget()
        self.taskbar.setLayout(hbox(self.task, button("Run", "primary", self.run_task)))
        self.reports = QTextEdit()
        self.reports.setReadOnly(True)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setStyleSheet("font-family: Menlo, 'DejaVu Sans Mono', monospace; font-size: 12px;")
        self.about = label("", wrap=True, select=True)
        self.about.setAlignment(Qt.AlignTop)
        tabs = QTabWidget()
        tabs.addTab(self.reports, "Reports")
        tabs.addTab(self.logs, "Logs")
        self.history = QWidget()
        self.history_list = vbox(spacing=6)
        hist_body = QWidget()
        hist_body.setObjectName("scrollbody")
        hist_body.setLayout(vbox(self.history_list, "stretch"))
        hist_scroll = QScrollArea()
        hist_scroll.setWidgetResizable(True)
        hist_scroll.setWidget(hist_body)
        self.history.setLayout(vbox(
            label("A copy of the workspace is saved before every task. Restoring one keeps a copy of the "
                  "current workspace too, so you can undo it.", "dim", wrap=True),
            hbox(button("Save snapshot now", on=self.save_snapshot), "stretch"), hist_scroll, spacing=8,
            margins=(4, 8, 4, 4)))
        tabs.addTab(self.history, "History")
        self.netbox = vbox(spacing=8)
        net_body = QWidget()
        net_body.setObjectName("scrollbody")
        net_body.setLayout(vbox(self.netbox, "stretch", margins=(4, 8, 4, 4)))
        net_scroll = QScrollArea()
        net_scroll.setWidgetResizable(True)
        net_scroll.setWidget(net_body)
        tabs.addTab(net_scroll, "Network")
        self.net_tab = net_scroll
        tabs.addTab(self.about, "About")
        tabs.currentChanged.connect(lambda i: (tabs.widget(i) is self.history and self.load_history(),
                                               tabs.widget(i) is self.net_tab and self.load_network()))
        tabs.setFixedWidth(360)
        self.tabs = tabs
        left = vbox(self.banner, self.view, self.taskbar, spacing=10)
        left.setStretch(1, 1)
        self.setLayout(vbox(hbox(button("← Spaces", "ghost", win.show_grid), label(name, "h1"), self.state_slot,
                                 "stretch", self.actions),
                            hbox(left, tabs, spacing=16), spacing=12))
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.load_side)
        self.poll.start(3000)
        self.load_side()

    def update_from(self, a: dict | None) -> None:
        if a is None:
            self.win.show_grid()
            return
        self.agent = a
        running, taken = a["status"] == "running", a["state"] == "taken over"
        while self.state_slot.count():
            self.state_slot.takeAt(0).widget().deleteLater()
        self.state_slot.addWidget(badge(a["state"]))
        while self.actions.count():
            self.actions.takeAt(0).widget().deleteLater()
        if running:
            self.actions.addWidget(button("Release" if taken else "Take over", on=self.release if taken else self.take_over))
            self.actions.addWidget(button("Stop", on=lambda: self.act("stop")))
        else:
            self.actions.addWidget(button("Start", on=lambda: self.act("start")))
        self.actions.addWidget(button("Delete", "danger", self.delete))
        self.view.set_control(taken)
        self.banner.setVisible(taken)
        self.taskbar.setVisible(bool(a["model"]) and running)
        shares = "<br>".join(a.get("shares") or []) or "nothing"
        self.about.setText(f"<p><b>State</b><br>{a['state']}</p><p><b>Flavor</b><br>{a['flavor']}</p>"
                           f"<p><b>Model</b><br>{a['model'] or 'none'}</p><p><b>Account</b><br>{a['account'] or 'none'}</p>"
                           f"<p><b>Task</b><br>{a['task'] or 'none'}</p><p><b>Shared folders</b><br>{shares}</p>"
                           f"<p><b>Created</b><br>{a['created']}</p><p><b>MCP</b><br>{common.api.base}{a['mcp']}</p>")

    def load_side(self) -> None:
        n = self.name

        def got(res) -> None:
            reps, logs = res
            html = []
            for r in reversed(reps[-100:]):
                col = common.STATE_COLOR.get(r["status"], C["dim"])
                msg = r["message"].replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
                html.append(f"<p style='margin:0 0 10px 0'><span style='color:{col};font-size:12px'>● {r['status']}</span>"
                            f" <span style='color:{C['dim']};font-size:12px'>{ago(r['ts'])}</span><br>{msg}</p>")
            new = "".join(html) or f"<p style='color:{C['dim']}'>No reports yet. Agents post progress here as they work.</p>"
            if new != getattr(self, "_last_reports", None):
                self._last_reports = new
                self.reports.setHtml(new)
            text = logs.strip() or "No agent log yet (it starts with the first task)."
            if text != self.logs.toPlainText():
                bar = self.logs.verticalScrollBar()
                at_end = bar.value() >= bar.maximum() - 10
                self.logs.setPlainText(text)
                if at_end:
                    bar.setValue(bar.maximum())
        bg(lambda: (common.api.get(f"/api/agents/{n}/reports"),
                    common.api.get(f"/api/agents/{n}/logs?service=agent&tail=300")), got, lambda e: None)

    def act(self, what: str) -> None:
        bg(lambda: common.api.post(f"/api/agents/{self.name}/{what}"), lambda _: self.win.refresh())

    def take_over(self) -> None:
        self.act("takeover")

    def release(self) -> None:
        self.act("release")

    def load_history(self) -> None:
        def got(snaps) -> None:
            while self.history_list.count():
                w = self.history_list.takeAt(0).widget()
                if w:
                    w.deleteLater()
            if not snaps:
                self.history_list.addWidget(label("No snapshots yet.", "dim"))
            for sn in snaps:
                when = datetime.fromtimestamp(sn["time"]).strftime("%b %d %H:%M")
                row = QFrame()
                row.setObjectName("panel")
                row.setLayout(hbox(vbox(label(f"<b>{when}</b> · {sn['bytes'] / 1e6:.1f} MB"),
                                        label(sn["label"], "dim", wrap=True), spacing=1), "stretch",
                                   button("Restore", on=lambda _=False, sn=sn: self.restore(sn)),
                                   margins=(10, 8, 10, 8)))
                self.history_list.addWidget(row)
        bg(lambda: common.api.get(f"/api/agents/{self.name}/snapshots"), got)

    def load_network(self) -> None:
        def got(n) -> None:
            while self.netbox.count():
                it = self.netbox.takeAt(0)
                w = it.widget() or (it.layout() and None)
                if w:
                    w.deleteLater()
            if n["mode"] == "open":
                self.netbox.addWidget(label("This agent has open internet access. To limit it to certain sites, "
                                            "create an agent with “Only these sites”.", "dim", wrap=True))
                return
            self.netbox.addWidget(label("<b>Allowed sites</b> (subdomains included). Changes apply right away.",
                                        wrap=True))
            for d in n["allow"]:
                row = QWidget()
                row.setLayout(hbox(label(d), "stretch", button("Remove", "ghost",
                                                                lambda _=False, d=d: self.set_allow(n, remove=d))))
                self.netbox.addWidget(row)
            if n["implied"]:
                self.netbox.addWidget(label("Added for its AI provider: " + ", ".join(n["implied"]), "dim", wrap=True))
            add = QLineEdit()
            add.setPlaceholderText("Add a site, e.g. github.com")
            add.returnPressed.connect(lambda: add.text().strip() and self.set_allow(n, add=add.text().strip()))
            row = QWidget()
            row.setLayout(hbox(add, button("Allow", on=lambda: add.text().strip() and
                                           self.set_allow(n, add=add.text().strip()))))
            self.netbox.addWidget(row)
            self.netbox.addWidget(label("<b>Recent connections</b>", wrap=True))
            for e in n["recent"][:25]:
                color = C["green"] if e["action"] == "ALLOW" else C["red"]
                row = QWidget()
                row.setLayout(hbox(label(f"<span style='color:{color}'>●</span> {e['host']}"), "stretch",
                                   button("Allow", "ghost", lambda _=False, h=e["host"]: self.set_allow(n, add=h))
                                   if e["action"] == "BLOCK" else None))
                self.netbox.addWidget(row)
            if not n["recent"]:
                self.netbox.addWidget(label("Nothing yet.", "dim"))
        bg(lambda: common.api.get(f"/api/agents/{self.name}/network"), got)

    def set_allow(self, n: dict, add: str = "", remove: str = "") -> None:
        allow = [d for d in n["allow"] if d != remove] + ([add] if add else [])
        bg(lambda: common.api.put(f"/api/agents/{self.name}/network", {"allow": allow}),
           lambda _: (self.win.toast(f"Allowed {add}" if add else f"Removed {remove}"), self.load_network()))

    def save_snapshot(self) -> None:
        bg(lambda: common.api.post(f"/api/agents/{self.name}/snapshot", {"label": "saved from Hangar"}),
           lambda _: (self.win.toast("Snapshot saved"), self.load_history()))

    def restore(self, sn: dict) -> None:
        if QMessageBox.question(self, "Restore workspace",
                                f"Replace {self.name}'s workspace with the copy from "
                                f"{datetime.fromtimestamp(sn['time']).strftime('%b %d %H:%M')}?\n\n"
                                "The current workspace is saved first, so you can undo this.") != QMessageBox.Yes:
            return
        bg(lambda: common.api.post(f"/api/agents/{self.name}/rollback", {"id": sn["id"]}),
           lambda _: (self.win.toast("Workspace restored"), self.load_history()))

    def run_task(self) -> None:
        task = self.task.text().strip()
        if not task:
            return
        bg(lambda: common.api.post(f"/api/agents/{self.name}/run", {"task": task}),
           lambda r: (self.task.clear(), self.win.toast("Task started", r.get("warning") or
                                                         "The workspace was saved first (see History)."),
                      self.win.refresh()))

    def delete(self) -> None:
        if QMessageBox.question(self, "Delete agent", f"Delete {self.name} and its files? This can't be undone.") \
                != QMessageBox.Yes:
            return
        bg(lambda: common.api.delete(f"/api/agents/{self.name}"),
           lambda _: (self.win.toast(f"Deleted {self.name}"), self.win.show_grid(), self.win.refresh()))

    def close_page(self) -> None:
        self.poll.stop()
        self.view.close_view()


class NewAgentDialog(QDialog):
    def __init__(self, win: "SpacesWindow", opts: dict, task: str = "") -> None:
        super().__init__(win)
        self.win = win
        self.setWindowTitle("New agent")
        self.setMinimumWidth(560)
        self.name = QLineEdit()
        self.name.setPlaceholderText("scout")
        self.flavor = QComboBox()
        for f in opts.get("flavor_info") or [{"name": n, "description": ""} for n in opts["flavors"]]:
            self.flavor.addItem(f"{f['name']} · {f['description']}" if f["description"] else f["name"], f["name"])
        self.model = QComboBox()
        self.model.addItem("No model (just a desktop)", ("", ""))
        for a in opts["accounts"]:
            self.model.addItem(f"{'Claude Code' if a['harness'] == 'claude' else 'Codex'} · {a['name']}",
                               (a["harness"], a["name"]))
        for m in opts["local_models"]:
            self.model.addItem(f"{m['name']} · {'vision' if m['vision'] else 'text only'} (local)",
                               (f"local:{m['key']}", ""))
        if opts["accounts"]:
            self.model.setCurrentIndex(1)
        self.task = QTextEdit()
        self.task.setPlaceholderText("Find three used road bikes under $500 near Boston and compare them.")
        self.task.setFixedHeight(80)
        self.task.setPlainText(task)
        self.shares = QVBoxLayout()
        self.net = QComboBox()
        self.net.addItem("Open internet", "open")
        self.net.addItem("Only these sites…", "allow")
        self.sites = QLineEdit()
        self.sites.setPlaceholderText("github.com, wikipedia.org (its AI provider is added automatically)")
        self.sites.setVisible(False)
        self.net.currentIndexChanged.connect(lambda _i: self.sites.setVisible(self.net.currentData() == "allow"))
        self.err = label("", wrap=True)
        self.err.setStyleSheet(f"color:{C['red']}")
        self.go = button("Launch", "primary", self.submit)
        form = vbox(
            label("New agent", "h1"),
            hbox(vbox(label("Name", "dim"), self.name, spacing=4), vbox(label("Flavor", "dim"), self.flavor, spacing=4)),
            vbox(label("Model / account", "dim"), self.model, spacing=4),
            label("No accounts or local models yet: add one in the Accounts or Models app.", "dim")
            if not opts["accounts"] and not opts["local_models"] else None,
            vbox(label("Task", "dim"), self.task, spacing=4),
            hbox(label("Shared folders (read-only unless writable)", "dim"), "stretch",
                 button("Add folder…", "ghost", self.add_share)),
            self.shares,
            vbox(label("Internet", "dim"), self.net, self.sites, spacing=4),
            self.err,
            hbox("stretch", button("Cancel", on=self.reject), self.go),
            spacing=12, margins=(22, 22, 22, 22))
        self.setLayout(form)
        self.setStyleSheet(f"QDialog {{ background: {C['panel']}; }}")

    def add_share(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Share a folder with the agent")
        if not path:
            return
        row = QWidget()
        rw = QCheckBox("writable")
        rm = button("✕", "ghost")
        row.setLayout(hbox(label(path), "stretch", rw, rm))
        row.path, row.rw = path, rw
        rm.clicked.connect(row.deleteLater)
        self.shares.addWidget(row)

    def submit(self) -> None:
        model, account = self.model.currentData()
        shares = []
        for i in range(self.shares.count()):
            w = self.shares.itemAt(i).widget()
            if w is not None and hasattr(w, "path"):
                shares.append(w.path + (":rw" if w.rw.isChecked() else ""))
        body = {"name": self.name.text().strip().lower(), "flavor": self.flavor.currentData(), "model": model,
                "account": account, "task": self.task.toPlainText().strip(), "shares": shares}
        if self.net.currentData() == "allow":
            body["allow"] = [d.strip() for d in self.sites.text().replace(" ", ",").split(",") if d.strip()]
        self.go.setEnabled(False)
        self.go.setText("Loading model…" if model.startswith("local:") else "Launching…")
        self.err.setText("")

        def ok(r) -> None:
            for w in r["warnings"]:
                self.win.toast("Heads up", w)
            self.accept()
            self.win.refresh(then=lambda: self.win.open_agent(r["name"]))

        def bad(e) -> None:
            self.err.setText(str(e))
            self.go.setEnabled(True)
            self.go.setText("Launch")
        bg(lambda: common.api.post("/api/agents", body), ok, bad)


class SpacesWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1280, 820)
        self.agents: list[dict] = []
        self.page: AgentPage | None = None
        self.dot = label("●")
        self.approvals = common.ApprovalBar()
        self.approvals.changed.connect(self.approvals.refresh)
        self.grid = Grid(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid)
        root = QWidget()
        root.setObjectName("page")
        root.setLayout(vbox(hbox(label("Hangar", "h1"), "stretch", self.dot,
                                 button("Overview", on=lambda: self.overview and self.overview.toggle(),
                                        tip="All agents at once (F3, or the Spaces key)"),
                                 button("+ New agent", "primary", self.new_agent)),
                            self.approvals, self.stack, spacing=14, margins=(22, 16, 22, 18)))
        self.setCentralWidget(root)
        self.overview = None
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("F3"), self, lambda: self.overview and self.overview.toggle())
        self.toasts = common.Toasts(root)
        self.events = common.Events(self)
        self.events.event.connect(self.on_event)
        self.events.connected.connect(lambda ok: self.dot.setStyleSheet(f"color:{C['green'] if ok else C['dim']}"))
        self.events.start()
        self.debounce = QTimer(self, singleShot=True, interval=300)
        self.debounce.timeout.connect(self.refresh)
        thumbs = QTimer(self)
        thumbs.timeout.connect(lambda: self.stack.currentWidget() is self.grid and self.isVisible() and self.grid.tick())
        thumbs.start(2000)
        slow = QTimer(self)
        slow.timeout.connect(self.refresh)
        slow.start(8000)
        self.refresh()
        self.approvals.refresh()

    def toast(self, title: str, msg: str = "", error: bool = False, on_click=None) -> None:
        self.toasts.show_toast(title, msg, error, on_click)

    def refresh(self, then=None) -> None:
        def got(agents) -> None:
            self.agents = sorted(agents, key=lambda a: a["name"])
            self.grid.render(self.agents)
            if self.page:
                self.page.update_from(next((a for a in self.agents if a["name"] == self.page.name), None))
            self.grid.tick()
            if then:
                then()
        bg(lambda: common.api.get("/api/agents"), got, lambda e: self.toast("Can't load agents", str(e), True))

    def on_event(self, ev: dict) -> None:
        if ev.get("type") == "vault":
            self.approvals.refresh()
            if ev.get("status") == "pending":
                self.toast(f"{ev['agent']} needs you", f"{'Sign in to' if ev['kind'] == 'login' else 'Code for'} "
                                                       f"{ev['site']}: {ev.get('reason', '')}")
            return
        self.debounce.start()
        if ev.get("type") == "report" and (ev.get("final") or ev.get("status") == "waiting_on_user"):
            what = {"done": "finished", "error": "hit a problem", "waiting_on_user": "needs you"}.get(ev["status"], ev["status"])
            self.toast(f"{ev['agent']} {what}", ev.get("message", ""), ev["status"] == "error",
                       lambda: self.open_agent(ev["agent"]))

    def open_agent(self, name: str) -> None:
        self.show_grid()
        self.page = AgentPage(self, name)
        self.stack.addWidget(self.page)
        self.stack.setCurrentWidget(self.page)
        self.page.update_from(next((a for a in self.agents if a["name"] == name), None))

    def show_grid(self) -> None:
        if self.page:
            self.page.close_page()
            self.stack.removeWidget(self.page)
            self.page.deleteLater()
            self.page = None
        self.stack.setCurrentWidget(self.grid)

    def new_agent(self, task: str = "") -> None:
        task = task if isinstance(task, str) else ""
        bg(lambda: common.api.get("/api/options"), lambda opts: NewAgentDialog(self, opts, task).exec())

    def closeEvent(self, e) -> None:
        if self.page:
            self.page.close_page()
        self.events.requestInterruption()
        super().closeEvent(e)


class Overview(QWidget):
    """Mission Control for agents: every agent's desktop, live, full screen.
    Bound to the Spaces key on the Hangar OS; `hangar overview` toggles it."""

    def __init__(self, win: SpacesWindow) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.win = win
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.tiles: dict[str, tuple[QLabel, QLabel, QWidget]] = {}
        self.loading: set[str] = set()
        self.grid = QGridLayout()
        self.grid.setSpacing(28)
        self.empty = label("No agents running. Press Esc.", "dim")
        self.empty.setAlignment(Qt.AlignCenter)
        title = label("Spaces", "h1")
        title.setAlignment(Qt.AlignCenter)
        self.setLayout(vbox(title, "stretch", self.empty, self.grid, "stretch", spacing=24, margins=(60, 40, 60, 40)))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def paintEvent(self, _e) -> None:
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(8, 10, 15, 235))
        p.end()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide_overview()
        else:
            self.show_overview()

    def show_overview(self) -> None:
        screen = (self.win.windowHandle().screen() if self.win.windowHandle() else None) or self.screen()
        self.setGeometry(screen.geometry())
        self.build()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        from PySide6.QtCore import QPropertyAnimation
        self.anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.anim.setDuration(160)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.timer.start(1000)
        self.tick()

    def hide_overview(self) -> None:
        self.timer.stop()
        self.hide()

    def build(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.tiles.clear()
        agents = [a for a in self.win.agents if a["status"] == "running"]
        self.empty.setVisible(not agents)
        n = len(agents)
        cols = 1 if n <= 1 else 2 if n <= 4 else 3 if n <= 9 else 4
        tw = min(720, (self.width() - 120 - 28 * (cols - 1)) // max(cols, 1))
        for i, a in enumerate(agents):
            img = QLabel()
            img.setFixedSize(tw, tw * 10 // 16)
            img.setAlignment(Qt.AlignCenter)
            img.setStyleSheet(f"background:#07080b; border:2px solid {C['line']}; border-radius:10px;")
            cap = hbox("stretch", label(f"<b>{a['name']}</b>"), badge(a["state"]), "stretch")
            tile = QWidget()
            tile.setCursor(Qt.PointingHandCursor)
            tile.setLayout(vbox(img, cap, spacing=8))
            name = a["name"]
            tile.mousePressEvent = lambda _e, n=name: self.pick(n)
            tile.enterEvent = lambda _e, im=img: im.setStyleSheet(
                f"background:#07080b; border:2px solid {C['accent']}; border-radius:10px;")
            tile.leaveEvent = lambda _e, im=img: im.setStyleSheet(
                f"background:#07080b; border:2px solid {C['line']}; border-radius:10px;")
            self.grid.addWidget(tile, i // cols, i % cols, Qt.AlignCenter)
            self.tiles[name] = (img, cap, tile)

    def tick(self) -> None:
        for name, (img, _cap, _t) in list(self.tiles.items()):
            if name in self.loading:
                continue
            self.loading.add(name)

            def got(data, n=name, im=img) -> None:
                self.loading.discard(n)
                pm = QPixmap()
                if self.tiles.get(n, (None,))[0] is im and pm.loadFromData(data):
                    im.setPixmap(pm.scaled(im.width() - 4, im.height() - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            bg(lambda n=name, w=img.width(): common.api.call("GET", f"/api/agents/{n}/screenshot.png?w={w}", raw=True),
               got, lambda _e, n=name: self.loading.discard(n))

    def pick(self, name: str) -> None:
        self.hide_overview()
        self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()
        self.win.open_agent(name)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Escape, Qt.Key_F3):
            self.hide_overview()

    def mousePressEvent(self, _e) -> None:
        self.hide_overview()   # click on empty space closes it, like Mission Control


SOCKET = "hangar-spaces"


def main() -> None:
    """One Spaces per session: launching it again (e.g. from the Spaces key)
    just tells the running one to show itself or toggle the overview."""
    import sys

    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    want = "overview" if "--overview" in sys.argv else "show"
    if "--new-agent" in sys.argv:
        i = sys.argv.index("--new-agent")
        want = "new:" + (sys.argv[i + 1] if i + 1 < len(sys.argv) else "")
    app = common.make_app("spaces")
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if sock.waitForConnected(300):
        sock.write(want.encode())
        sock.flush()
        sock.waitForBytesWritten(1000)
        return
    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)
    win = SpacesWindow()
    win.setWindowTitle("Hangar")
    overview = Overview(win)
    win.overview = overview

    def incoming() -> None:
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(1000)
        msg = bytes(conn.readAll()).decode()
        if msg == "overview":
            win.refresh(then=overview.toggle)
        elif msg.startswith("new:"):
            win.showNormal()
            win.raise_()
            win.new_agent(msg[4:])
        else:
            win.showNormal()
            win.raise_()
            win.activateWindow()
    server.newConnection.connect(incoming)
    if want == "overview":
        win.refresh(then=overview.show_overview)
    elif want.startswith("new:"):
        win.show()
        QTimer.singleShot(300, lambda: win.new_agent(want[4:]))
    else:
        win.show()
    sys.exit(app.exec())
