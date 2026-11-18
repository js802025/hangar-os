"""Passwords: the vault, as its own app (modelled on Apple's Passwords).

Sidebar: All, Codes, Security, Agent access, Recently Deleted. A searchable
list, and a detail pane with show/copy, live verification codes, notes, edit,
and which agents may use each password. Agents' requests show at the top.

  python -m hangar.apps.passwords      (or: hangar open passwords)
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QScrollArea, QWidget)

from hangar.apps import common
from hangar.apps.common import C, bg, button, hbox, label, site_icon, vbox

SECTIONS = [("all", "🔑  All"), ("codes", "⏱  Codes"), ("security", "⚠️  Security"),
            ("access", "🤖  Agent access"), ("deleted", "🗑  Recently Deleted")]


def strong_password() -> str:
    """Apple-style: three groups of six with one capital and one digit."""
    letters = "abcdefghijkmnopqrstuvwxyz"
    chars = [secrets.choice(letters) for _ in range(18)]
    up, dg = secrets.randbelow(18), secrets.randbelow(18)
    if dg == up:
        dg = (dg + 1) % 18
    chars[up] = chars[up].upper()
    chars[dg] = str(secrets.randbelow(10))
    return "-".join("".join(chars[i:i + 6]) for i in (0, 6, 12))


def fmt_code(c: str) -> str:
    return f"{c[:3]} {c[3:]}" if len(c) == 6 else c


class Ring(QWidget):
    """Countdown ring next to a verification code."""

    def __init__(self) -> None:
        super().__init__()
        self.secs = 30
        self.setFixedSize(QSize(22, 22))

    def set(self, secs: int) -> None:
        self.secs = secs
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, 16, 16)
        p.setPen(QPen(QColor(C["line"]), 3))
        p.drawEllipse(r)
        pen = QPen(QColor(C["red"] if self.secs <= 5 else C["accent"]), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 90 * 16, int(-360 * 16 * self.secs / 30))
        p.end()


def field(title: str, value: QWidget | str, *extra: QWidget) -> QFrame:
    f = QFrame()
    f.setStyleSheet(f"QFrame#field {{ border-bottom: 1px solid {C['line']}; }}")
    f.setObjectName("field")
    v = value if isinstance(value, QWidget) else label(value, wrap=True, select=True)
    f.setLayout(vbox(label(title, "dim"), hbox(v, "stretch", *extra), spacing=2, margins=(0, 7, 0, 7)))
    return f


def copy(text: str, what: str, win) -> None:
    QGuiApplication.clipboard().setText(text)
    win.toast(f"{what} copied")
    # Like a password manager: clear the clipboard again after a while.
    QTimer.singleShot(60_000, lambda: QGuiApplication.clipboard().text() == text and
                      QGuiApplication.clipboard().clear())


class PasswordsWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1040, 660)
        self.data: dict | None = None
        self.section, self.selected, self.editing, self.prefill = "all", None, None, None
        self.codes: list[dict] = []
        self.codes_at = 0.0
        self.code_labels: dict[str, list] = {}

        self.side = QListWidget()
        self.side.setFixedWidth(176)
        self.side.currentRowChanged.connect(self.pick_section)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.textChanged.connect(lambda _t: self.render_list())
        self.items = QListWidget()
        self.items.itemClicked.connect(lambda it: self.pick_entry(it.data(Qt.UserRole)))
        self.items.setSpacing(1)
        self.add_btn = button("+", "primary", self.new_entry, "New password")
        listcol = QWidget()
        listcol.setFixedWidth(290)
        listcol.setLayout(vbox(hbox(self.search, self.add_btn), self.items, spacing=8, margins=(12, 12, 12, 12)))
        self.listcol = listcol
        self.detail = QScrollArea()
        self.detail.setWidgetResizable(True)
        self.approvals = common.ApprovalBar()
        self.approvals.changed.connect(self.refresh)

        sidew = QWidget()
        sidew.setObjectName("sidebar")
        sidew.setLayout(vbox(self.side, margins=(8, 12, 8, 12)))
        sidew.setFixedWidth(190)
        body = QFrame()
        body.setObjectName("panel")
        lay = hbox(sidew, listcol, self.detail, spacing=0)
        body.setLayout(lay)
        root = QWidget()
        root.setObjectName("page")
        root.setLayout(vbox(self.approvals, body, spacing=12, margins=(16, 14, 16, 16)))
        self.setCentralWidget(root)
        self.toasts = common.Toasts(root)

        self.events = common.Events(self)
        self.events.event.connect(lambda ev: ev.get("type") == "vault" and self.refresh())
        self.events.start()
        t = QTimer(self)
        t.timeout.connect(self.tick)
        t.start(1000)
        self.refresh()

    def toast(self, title: str, msg: str = "", error: bool = False, on_click=None) -> None:
        self.toasts.show_toast(title, msg, error, on_click)

    # -- data ------------------------------------------------------------------------------
    def refresh(self) -> None:
        bg(lambda: common.api.get("/api/vault"), self.loaded)

    def loaded(self, d: dict) -> None:
        self.data = d
        self.approvals.refresh(d)
        self.render_side()
        self.render_main()

    def entries(self, section: str) -> list[dict]:
        live = [e for e in self.data["entries"] if not e["deleted"]]
        return {"codes": [e for e in live if e["has_totp"]],
                "security": [e for e in live if e["warnings"]],
                "deleted": [e for e in self.data["entries"] if e["deleted"]]}.get(section, live)

    # -- sidebar & list ------------------------------------------------------------------------
    def render_side(self) -> None:
        self.side.blockSignals(True)
        row = self.side.currentRow()
        self.side.clear()
        pending = sum(r["status"] == "pending" for r in self.data["requests"])
        for key, text in SECTIONS:
            n = len(self.data["grants"]) + pending if key == "access" else len(self.entries(key))
            it = QListWidgetItem(f"{text}    {n or ''}")
            it.setSizeHint(QSize(160, 32))
            self.side.addItem(it)
        self.side.setCurrentRow(max(row, 0) if row >= 0 else 0)
        self.side.blockSignals(False)

    def pick_section(self, row: int) -> None:
        if row < 0:
            return
        self.section = SECTIONS[row][0]
        self.selected, self.editing = None, None
        self.render_main()

    def render_main(self) -> None:
        access = self.section == "access"
        self.listcol.setVisible(not access)
        self.add_btn.setVisible(self.section != "deleted")
        if access:
            self.show_detail(self.access_view())
            return
        self.render_list()
        if self.editing:
            self.show_detail(self.edit_form())
            return
        items = self.visible_items()
        sel = next((e for e in items if e["id"] == self.selected), items[0] if items else None)
        self.selected = sel["id"] if sel else None
        self.render_list()
        self.show_detail(self.detail_view(sel) if sel else label(self.empty_text(), "dim"))

    def visible_items(self) -> list[dict]:
        q = self.search.text().lower()
        return sorted((e for e in self.entries(self.section)
                       if not q or q in f"{e['label']} {e['site']} {e['username']}".lower()),
                      key=lambda e: e["label"].lower())

    def empty_text(self) -> str:
        return {"deleted": "Nothing here. Deleted passwords stay for 30 days.",
                "security": "No weak or reused passwords. 👍",
                "codes": "No verification codes. Add a setup key to a password to get codes here."}.get(
            self.section, "No passwords yet. Click + to add one.")

    def render_list(self) -> None:
        if not self.data:
            return
        self.items.clear()
        self.code_labels.clear()
        for e in self.visible_items():
            it = QListWidgetItem()
            it.setData(Qt.UserRole, e["id"])
            it.setSizeHint(QSize(260, 46))
            w = QWidget()
            w.setStyleSheet("background: transparent")
            code = label("", "dim")
            code.setStyleSheet(f"color:{C['accent2']}; font-family: Menlo, monospace; font-weight:600")
            if self.section == "codes":
                self.code_labels.setdefault(e["id"], []).append(code)
            warn = label("●" if e["warnings"] and self.section != "deleted" else "")
            warn.setStyleSheet(f"color:{C['yellow']}")
            w.setLayout(hbox(site_icon(e["label"]), vbox(label(f"<b>{e['label']}</b>"),
                                                         label(e["username"] or e["site"], "dim"), spacing=0),
                             "stretch", code if self.section == "codes" else None, warn, margins=(6, 4, 6, 4)))
            self.items.addItem(it)
            self.items.setItemWidget(it, w)
            if e["id"] == self.selected:
                it.setSelected(True)
        self.tick(force_labels=True)

    def pick_entry(self, entry_id: str) -> None:
        self.selected, self.editing = entry_id, None
        self.render_main()

    def show_detail(self, w: QWidget) -> None:
        holder = QWidget()
        holder.setObjectName("scrollbody")
        holder.setLayout(vbox(w, "stretch", margins=(22, 18, 22, 18)))
        w.setMaximumWidth(560)
        self.detail.setWidget(holder)

    # -- detail ------------------------------------------------------------------------------------
    def detail_view(self, e: dict) -> QWidget:
        eid = e["id"]
        title = hbox(site_icon(e["label"], 54), vbox(label(e["label"], "h1"), label(e["site"], "dim"), spacing=0),
                     "stretch", None if e["deleted"] else button("Edit", on=lambda: self.edit(eid)), spacing=14)
        w = QWidget()
        if e["deleted"]:
            left = max(0, 30 - int((time.time() - e["deleted"]) // 86400))
            w.setLayout(vbox(title, label(f"Deleted · removed for good in {left} days", "dim"),
                             field("User Name", e["username"] or "—"), field("Website", e["site"]),
                             hbox(button("Recover", "primary", lambda: self.entry_action(eid, "restore", "Recovered")),
                                  button("Delete now", "danger", lambda: self.purge(e)), "stretch"),
                             spacing=6))
            return w
        rows = [title]
        if e["warnings"]:
            msg = ("This password is weak and used on other sites." if len(e["warnings"]) == 2 else
                   "This password is used on other sites. Change it on the website, then here."
                   if "reused" in e["warnings"] else "This password is weak. Change it on the website, then here.")
            warn = label(f"⚠️ {msg}", wrap=True)
            warn.setStyleSheet("background: rgba(251,191,36,.1); border: 1px solid rgba(251,191,36,.35);"
                               "color: #fde68a; padding: 8px 10px; border-radius: 9px;")
            rows.append(warn)
        rows.append(field("User Name", e["username"] or "—",
                          button("Copy", "ghost", lambda: copy(e["username"], "User name", self)) if e["username"] else None))
        if e["has_password"]:
            pw = label("••••••••••••", select=True)
            pw.setStyleSheet("font-family: Menlo, monospace; letter-spacing: 1px;")
            show = button("Show", "ghost")

            def toggle():
                if show.text() == "Show":
                    bg(lambda: common.api.get(f"/api/vault/entries/{eid}/secret?audit=1"),
                       lambda s: (pw.setText(s["password"]), pw.setStyleSheet(
                           f"font-family: Menlo, monospace; color:{C['accent2']}"), show.setText("Hide")))
                else:
                    pw.setText("••••••••••••")
                    show.setText("Show")
            show.clicked.connect(toggle)
            rows.append(field("Password", pw, show, button("Copy", "ghost", lambda: bg(
                lambda: common.api.get(f"/api/vault/entries/{eid}/secret?audit=1"),
                lambda s: copy(s["password"], "Password", self)))))
        if e["has_totp"]:
            code = label("··· ···")
            code.setStyleSheet(f"font-family: Menlo, monospace; font-size: 22px; font-weight: 600; color:{C['accent2']}")
            ring = Ring()
            self.code_labels.setdefault(eid, []).append(code)
            self.code_labels.setdefault("_ring", []).append(ring)
            rows.append(field("Verification Code", code, ring, button("Copy", "ghost", lambda: bg(
                lambda: common.api.get(f"/api/vault/entries/{eid}/secret"), lambda s: copy(s["code"], "Code", self)))))
        rows.append(field("Website", e["site"]))
        if e["notes"]:
            rows.append(field("Notes", e["notes"]))
        grants = [g for g in self.data["grants"] if g["entry"] == eid]
        access = [hbox(label(f"🤖 {g['agent']}"), "stretch",
                       label(datetime.fromtimestamp(g["until"]).strftime("until %H:%M") if g["until"] else "always", "dim"),
                       button("Remove", "ghost", lambda _=False, g=g: self.revoke(g))) for g in grants] or \
                 [label("No agent can use this without asking you first.", "dim")]
        box = QFrame()
        box.setLayout(vbox(label("Agent access", "dim"), *access, spacing=4, margins=(0, 10, 0, 10)))
        rows.append(box)
        rows.append(hbox(button("Delete", "danger", lambda: self.entry_action(eid, None, "Moved to Recently Deleted")),
                         "stretch", margins=(0, 12, 0, 0)))
        w.setLayout(vbox(*rows, spacing=2))
        self.tick(force_labels=True)
        return w

    def edit(self, eid: str) -> None:
        self.editing = eid
        self.render_main()

    def new_entry(self) -> None:
        self.editing, self.prefill = "new", None
        if self.section in ("deleted", "access"):
            self.side.setCurrentRow(0)
        self.render_main()

    def edit_form(self) -> QWidget:
        new = self.editing == "new"
        e = {"site": "", "label": "", "username": "", "notes": "", **(self.prefill or {})} if new else \
            next(x for x in self.data["entries"] if x["id"] == self.editing)
        site, title, user = QLineEdit(e["site"]), QLineEdit("" if new else e["label"]), QLineEdit(e["username"])
        site.setPlaceholderText("github.com")
        title.setPlaceholderText("GitHub")
        pw = QLineEdit()
        pw.setPlaceholderText("" if new else "Leave empty to keep the current password")
        totp = QLineEdit()
        totp.setPlaceholderText("Base32 key or otpauth:// link from the site's 2FA setup")
        notes = QPlainTextEdit(e.get("notes", ""))
        notes.setFixedHeight(80)
        err = label("", wrap=True)
        err.setStyleSheet(f"color:{C['red']}")

        def save():
            body = {"site": site.text(), "label": title.text(), "username": user.text(), "notes": notes.toPlainText()}
            if new or pw.text():
                body["password"] = pw.text()
            if new or totp.text():
                body["totp"] = totp.text()

            def ok(r):
                self.selected = r["id"] if new else self.selected
                self.editing, self.prefill = None, None
                self.toast("Password saved" if new else "Changes saved")
                self.refresh()
            bg((lambda: common.api.post("/api/vault/entries", body)) if new else
               (lambda: common.api.put(f"/api/vault/entries/{e['id']}", body)), ok, lambda ex: err.setText(str(ex)))

        def cancel():
            self.editing, self.prefill = None, None
            self.render_main()

        w = QWidget()
        w.setLayout(vbox(
            label("New Password" if new else f"Edit {e['label']}", "h1"),
            label("Website", "dim"), site, label("Title (optional)", "dim"), title,
            label("User Name", "dim"), user,
            label("Password", "dim"), hbox(pw, button("Suggest strong password", on=lambda: pw.setText(strong_password()))),
            label("Verification code setup key (optional)" if new else "Verification code setup key (leave empty to keep)", "dim"),
            totp, label("Notes", "dim"), notes, err,
            hbox(button("Save", "primary", save), button("Cancel", on=cancel), "stretch"), spacing=6))
        return w

    def access_view(self) -> QWidget:
        d = self.data
        pending = [r for r in d["requests"] if r["status"] == "pending"]
        bar = common.ApprovalBar()
        bar.changed.connect(self.refresh)
        bar.refresh(d)

        def panel(title: str, *rows) -> QFrame:
            f = QFrame()
            f.setObjectName("panel")
            f.setLayout(vbox(label(title, "h2"), *rows, spacing=8, margins=(16, 14, 16, 14)))
            return f
        grants = [hbox(label(f"🤖 <b>{g['agent']}</b> · {g['label']}"), "stretch",
                       label(datetime.fromtimestamp(g["until"]).strftime("until %b %d %H:%M") if g["until"] else "always", "dim"),
                       button("Remove", "ghost", lambda _=False, g=g: self.revoke(g))) for g in d["grants"]]
        acts = []
        for a in d["audit"][:30]:
            what = f"{a.get('agent', '')} {a['action'].replace('_', ' ')} {a.get('site') or a.get('label') or ''}".strip()
            if a.get("scope"):
                what += f" ({a['scope']})"
            if a.get("error"):
                what += f" — {a['error']}"
            ts = datetime.fromisoformat(a["ts"]).astimezone().strftime("%b %d %H:%M")
            acts.append(hbox(label(ts, "dim"), label(what, wrap=True), "stretch", spacing=12))
        w = QWidget()
        w.setLayout(vbox(
            panel("Waiting for you", bar if pending else label(
                "No requests. When an agent needs to sign in or a code, it shows up here and at the top.", "dim", wrap=True)),
            panel("Standing permissions", *(grants or [label("No agent can use a password without asking.", "dim")])),
            panel("Activity", *(acts or [label("Nothing yet.", "dim")])), spacing=14))
        return w

    # -- actions -----------------------------------------------------------------------------------
    def entry_action(self, eid: str, action: str | None, msg: str) -> None:
        call = (lambda: common.api.post(f"/api/vault/entries/{eid}/{action}")) if action else \
            (lambda: common.api.delete(f"/api/vault/entries/{eid}"))
        bg(call, lambda _: (self.toast(msg), setattr(self, "selected", None), self.refresh()))

    def purge(self, e: dict) -> None:
        if QMessageBox.question(self, "Delete for good", f"Delete {e['label']} for good? This can't be undone.") \
                == QMessageBox.Yes:
            self.entry_action(e["id"], "purge", "Deleted for good")

    def revoke(self, g: dict) -> None:
        bg(lambda: common.api.delete(f"/api/vault/grants/{g['agent']}/{g['entry']}"),
           lambda _: (self.toast("Access removed"), self.refresh()))

    # -- live codes -------------------------------------------------------------------------------
    def tick(self, force_labels: bool = False) -> None:
        if not any(k != "_ring" for k in self.code_labels):
            return
        left = (self.codes[0]["seconds_left"] - int(time.time() - self.codes_at)) if self.codes else 0
        if left <= 0 and not force_labels:
            bg(lambda: common.api.get("/api/vault/codes"),
               lambda c: (setattr(self, "codes", c), setattr(self, "codes_at", time.time()), self.tick(True)),
               lambda _e: None)
            return
        if not self.codes and force_labels:
            self.codes_at = -1e9   # fetch on the next second
        for c in self.codes:
            for lbl in self.code_labels.get(c["id"], []):
                try:
                    lbl.setText(fmt_code(c["code"]))
                except RuntimeError:
                    pass
        for ring in self.code_labels.get("_ring", []):
            try:
                ring.set(max(left, 1))
            except RuntimeError:
                pass


def main() -> None:
    common.run("passwords", PasswordsWindow)


if __name__ == "__main__":
    main()
