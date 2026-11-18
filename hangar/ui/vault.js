// Vault page (modelled on Apple's Passwords app) and the approval bar that
// appears on every page when an agent asks to sign in or for a code.
// Uses helpers from app.js (h, api, act, toast, view, cleanup, go, $).
"use strict";

const vaultState = { data: null, section: "all", selected: null, query: "", editing: null, prefill: null };

// ---------------------------------------------------------------- helpers
function siteIcon(label) {
  const colors = ["#5b8cff", "#a78bfa", "#f472b6", "#fb923c", "#34d399", "#22d3ee", "#facc15", "#f87171"];
  let n = 0; for (const c of label) n = (n * 31 + c.charCodeAt(0)) >>> 0;
  return h("span.site-icon", { style: `background:${colors[n % colors.length]}` }, (label[0] || "?").toUpperCase());
}

function strongPassword() {
  // Apple-style: three groups of six, one uppercase letter and one digit, e.g. "xocdig-fipQy7-mytbab".
  const letters = "abcdefghijkmnopqrstuvwxyz", buf = new Uint32Array(20);
  crypto.getRandomValues(buf);
  const chars = [...buf.slice(0, 18)].map((x) => letters[x % letters.length]);
  const up = buf[18] % 18; let dg = buf[19] % 18; if (dg === up) dg = (dg + 1) % 18;
  chars[up] = chars[up].toUpperCase(); chars[dg] = String(buf[19] % 10);
  return [0, 6, 12].map((i) => chars.slice(i, i + 6).join("")).join("-");
}

async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); }
  catch {   // not a secure context (LAN over http): old-school fallback
    const t = h("textarea", { value: text, style: "position:fixed;opacity:0" });
    document.body.append(t); t.select(); document.execCommand("copy"); t.remove();
  }
  toast(`${what} copied`);
}

function codeRing(seconds) {
  const r = 9, c = 2 * Math.PI * r;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("class", "ring");
  svg.innerHTML = `<circle cx="12" cy="12" r="${r}" fill="none" stroke="var(--line)" stroke-width="3"/>` +
    `<circle cx="12" cy="12" r="${r}" fill="none" stroke="${seconds <= 5 ? "var(--red)" : "var(--accent)"}" stroke-width="3" ` +
    `stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - seconds / 30)}" transform="rotate(-90 12 12)" stroke-linecap="round"/>`;
  return svg;
}
const fmtCode = (c) => c.length === 6 ? c.slice(0, 3) + " " + c.slice(3) : c;

// ---------------------------------------------------------------- approvals bar (every page)
let reqTimer = null;
function refreshRequestsSoon() { clearTimeout(reqTimer); reqTimer = setTimeout(refreshRequests, 200); }

async function refreshRequests() {
  let d;
  try { d = await api("GET", "/api/vault"); } catch { return; }
  vaultState.data = d;
  const pending = d.requests.filter((r) => r.status === "pending");
  const bar = $("#requests");
  bar.replaceChildren(...pending.map(requestCard));
  bar.style.display = pending.length ? "grid" : "none";
  const navCount = $("[data-nav=vault] .count");
  if (navCount) navCount.textContent = pending.length || "";
  if ($("#pw")) renderVault();
}

function requestCard(r) {
  const what = r.kind === "login" ? `wants to sign in to ${r.site}` : `needs a verification code for ${r.site}`;
  const usable = r.kind === "login" ? r.entries.filter((e) => e.has_password) : r.entries.filter((e) => e.has_totp);
  const entry = h("select", usable.map((e) => h("option", { value: e.id }, `${e.label} · ${e.username || "no username"}`)));
  const scope = h("select", h("option", { value: "once" }, "Just this once"),
    h("option", { value: "hour", selected: true }, "For 1 hour"), h("option", { value: "always" }, `Always for ${r.agent}`));
  const code = h("input", { placeholder: usable.length ? "or type a code" : "Code from text / email", inputmode: "numeric",
                            autocomplete: "one-time-code", style: "width:170px" });
  const err = h("span.error");
  const approve = h("button.primary", { onclick: async () => {
    approve.disabled = true; err.textContent = "";
    try {
      const res = await api("POST", `/api/vault/requests/${r.id}/approve`,
        { entry: code.value.trim() ? "" : entry.value, scope: scope.value, code: code.value.trim() });
      toast(res.status === "filled" ? `Filled in for ${r.agent}` : `Couldn't fill for ${r.agent}`, res.message,
            { err: res.status !== "filled" });
      refreshRequests();
    } catch (e) { err.textContent = e.message; approve.disabled = false; }
  } }, r.kind === "login" ? "Sign in" : "Send code");
  const deny = h("button", { onclick: () => act(() => api("POST", `/api/vault/requests/${r.id}/deny`)).then(refreshRequests) }, "Deny");
  const missing = r.kind === "login" && !usable.length;
  return h("div.request",
    h("span.lock", "🔑"),
    h("div.grow",
      h("div", h("b", r.agent), " ", what),
      h("div.dim", { style: "font-size:12.5px" }, r.reason || "No reason given."),
      missing && h("div.dim", { style: "font-size:12.5px" }, `No saved password for ${r.site} yet.`)),
    missing ? h("button.primary", { onclick: () => { vaultState.prefill = { site: r.site }; vaultState.editing = "new";
                                                      go("#/vault"); renderVault(); } }, "Add password…")
            : [usable.length > 0 && entry, usable.length > 0 && scope, r.kind === "code" && code, approve],
    deny, err);
}

// ---------------------------------------------------------------- the Vault page
function Vault() {
  vaultState.selected = vaultState.selected || null;
  view().append(h("div.pw", { id: "pw" }));
  renderVault();
  refreshRequests();
  // Live verification codes.
  const t = setInterval(tickCodes, 1000);
  cleanup.push(() => clearInterval(t));
}

const SECTIONS = [
  ["all", "All", "🔑"], ["codes", "Codes", "⏱"], ["security", "Security", "⚠️"],
  ["access", "Agent access", "🤖"], ["deleted", "Recently Deleted", "🗑"],
];

function sectionEntries(d, s) {
  const live = d.entries.filter((e) => !e.deleted);
  if (s === "codes") return live.filter((e) => e.has_totp);
  if (s === "security") return live.filter((e) => e.warnings.length);
  if (s === "deleted") return d.entries.filter((e) => e.deleted);
  return live;
}

function renderVault() {
  const root = $("#pw");
  const d = vaultState.data;
  if (!root || !d) return;
  const s = vaultState.section;
  const side = h("aside.pw-side",
    SECTIONS.map(([key, label, icon]) => {
      const n = key === "access" ? d.grants.length + d.requests.filter((r) => r.status === "pending").length
                                 : sectionEntries(d, key).length;
      return h("button.pw-sec", { class: s === key ? "on" : "", onclick: () => {
        vaultState.section = key; vaultState.selected = null; vaultState.editing = null; renderVault(); } },
        h("span", icon), h("span.grow", label), h("span.dim", n || ""));
    }));
  let main;
  if (s === "access") {
    main = accessView(d);
  } else {
    const q = vaultState.query.toLowerCase();
    const items = sectionEntries(d, s)
      .filter((e) => !q || (e.label + " " + e.site + " " + e.username).toLowerCase().includes(q))
      .sort((a, b) => a.label.localeCompare(b.label));
    const search = h("input.pw-search", { type: "search", placeholder: "Search", value: vaultState.query,
      oninput: (e) => { vaultState.query = e.target.value; renderList(); } });
    const list = h("div.pw-items");
    function renderList() {
      const q2 = vaultState.query.toLowerCase();
      list.replaceChildren(...sectionEntries(d, s)
        .filter((e) => !q2 || (e.label + " " + e.site + " " + e.username).toLowerCase().includes(q2))
        .sort((a, b) => a.label.localeCompare(b.label))
        .map((e) => h("button.pw-item", { class: vaultState.selected === e.id ? "on" : "",
          onclick: () => { vaultState.selected = e.id; vaultState.editing = null; renderVault(); } },
          siteIcon(e.label),
          h("span.grow", h("b", e.label), h("div.dim", e.username || e.site)),
          s === "codes" && h("span.code-mini", { "data-code": e.id }, "··· ···"),
          e.warnings.length > 0 && s !== "deleted" && h("span.warn-dot", { title: e.warnings.join(", ") }))));
      if (!list.children.length) list.append(h("p.dim", { style: "padding:12px" },
        s === "deleted" ? "Nothing here. Deleted passwords stay for 30 days."
        : s === "security" ? "No weak or reused passwords. 👍"
        : s === "codes" ? "No verification codes. Add a setup key to a password to get codes here."
        : vaultState.query ? "No matches." : "No passwords yet. Click + to add one."));
    }
    const sel = items.find((e) => e.id === vaultState.selected) || (vaultState.editing ? null : items[0]);
    if (sel && !vaultState.editing) vaultState.selected = sel.id;
    renderList();
    main = [
      h("section.pw-list",
        h("div.row", search, s !== "deleted" && h("button.primary.add", { title: "New password",
          onclick: () => { vaultState.editing = "new"; vaultState.prefill = null; renderVault(); } }, "+")),
        list),
      h("section.pw-detail", vaultState.editing ? editForm(d) : sel ? detail(d, sel) : h("p.dim", "Select a password.")),
    ];
  }
  root.replaceChildren(side, ...[main].flat());
  root.classList.toggle("wide", s === "access");
  tickCodes();
}

function field(label, value, ...extra) {
  return h("div.pw-field", h("div.dim", label), h("div.row", h("div.grow.val", value), ...extra));
}

function detail(d, e) {
  const id = e.id;
  if (e.deleted) {
    const left = Math.max(0, 30 - Math.floor((Date.now() / 1000 - e.deleted) / 86400));
    return h("div.pw-card",
      h("div.pw-title", siteIcon(e.label), h("div", h("h2", e.label), h("div.dim", `Deleted · removed for good in ${left} days`))),
      field("User Name", e.username || "—"), field("Website", e.site),
      h("div.row", { style: "margin-top:16px" },
        h("button.primary", { onclick: () => act(() => api("POST", `/api/vault/entries/${id}/restore`), "Restored").then(refreshRequests) }, "Recover"),
        h("button.danger", { onclick: async () => {
          if (confirm(`Delete ${e.label} for good? This can't be undone.`) &&
              await act(() => api("POST", `/api/vault/entries/${id}/purge`), "Deleted for good")) { vaultState.selected = null; refreshRequests(); }
        } }, "Delete now")));
  }
  const pwVal = h("span.secret", "••••••••••••");
  let shown = false;
  const reveal = h("button.ghost", { onclick: async () => {
    shown = !shown;
    if (shown) { const sec = await api("GET", `/api/vault/entries/${id}/secret?audit=1`); pwVal.textContent = sec.password; pwVal.classList.add("shown"); }
    else { pwVal.textContent = "••••••••••••"; pwVal.classList.remove("shown"); }
    reveal.textContent = shown ? "Hide" : "Show";
  } }, "Show");
  const grants = d.grants.filter((g) => g.entry === id);
  return h("div.pw-card",
    h("div.pw-title", siteIcon(e.label), h("div.grow", h("h2", e.label), h("div.dim", e.site)),
      h("button", { onclick: () => { vaultState.editing = id; renderVault(); } }, "Edit")),
    e.warnings.length > 0 && h("div.pw-warn", "⚠️ ", e.warnings.includes("reused") && e.warnings.includes("weak")
      ? "This password is weak and used on other sites." : e.warnings.includes("reused")
      ? "This password is used on other sites. Change it on the website, then here." : "This password is weak. Change it on the website, then here."),
    field("User Name", e.username || "—", e.username && h("button.ghost", { onclick: () => copy(e.username, "User name") }, "Copy")),
    e.has_password && field("Password", pwVal, reveal, h("button.ghost", { onclick: async () =>
      copy((await api("GET", `/api/vault/entries/${id}/secret?audit=1`)).password, "Password") }, "Copy")),
    e.has_totp && field("Verification Code", h("span.code", { "data-code": id }, "··· ···"),
      h("span", { "data-ring": id }),
      h("button.ghost", { onclick: async () => copy((await api("GET", `/api/vault/entries/${id}/secret`)).code, "Code") }, "Copy")),
    field("Website", e.site),
    e.notes && field("Notes", h("span", { style: "white-space:pre-wrap" }, e.notes)),
    h("div.pw-field", h("div.dim", "Agent access"),
      grants.length ? grants.map((g) => h("div.row", h("span.grow", `🤖 ${g.agent}`),
          h("span.dim", g.until ? `until ${new Date(g.until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "always"),
          h("button.ghost", { onclick: () => act(() => api("DELETE", `/api/vault/grants/${g.agent}/${id}`), "Access removed").then(refreshRequests) }, "Remove")))
        : h("div.dim", { style: "font-size:13px" }, "No agent can use this without asking you first.")),
    h("button.danger", { style: "margin-top:18px", onclick: async () => {
      if (await act(() => api("DELETE", `/api/vault/entries/${id}`), "Moved to Recently Deleted")) { vaultState.selected = null; refreshRequests(); }
    } }, "Delete"));
}

function editForm(d) {
  const isNew = vaultState.editing === "new";
  const e = isNew ? { site: "", label: "", username: "", notes: "", ...(vaultState.prefill || {}) }
                  : d.entries.find((x) => x.id === vaultState.editing);
  const pw = h("input", { name: "password", type: "text", autocomplete: "off", spellcheck: false,
                          placeholder: isNew ? "" : "Leave empty to keep the current password" });
  const err = h("p.error");
  const form = h("form.pw-card", { onsubmit: async (ev) => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(form));
    try {
      if (isNew) {
        const r = await api("POST", "/api/vault/entries", f);
        vaultState.selected = r.id;
      } else {
        if (!f.password) delete f.password;
        if (!f.totp) delete f.totp;
        await api("PUT", `/api/vault/entries/${e.id}`, f);
      }
      vaultState.editing = null; vaultState.prefill = null;
      await refreshRequests();
      toast(isNew ? "Password saved" : "Changes saved");
    } catch (ex) { err.textContent = ex.message; }
  } },
    h("h2", isNew ? "New Password" : `Edit ${e.label}`),
    h("label", h("span", "Website"), h("input", { name: "site", value: e.site, required: true, placeholder: "github.com" })),
    h("label", h("span", "Title (optional)"), h("input", { name: "label", value: isNew ? "" : e.label, placeholder: "GitHub" })),
    h("label", h("span", "User Name"), h("input", { name: "username", value: e.username, autocomplete: "off" })),
    h("label", h("span", "Password"), h("div.row", pw,
      h("button", { type: "button", onclick: () => { pw.value = strongPassword(); } }, "Suggest strong password"))),
    h("label", h("span", isNew ? "Verification code setup key (optional)" : "Verification code setup key (leave empty to keep)"),
      h("input", { name: "totp", autocomplete: "off", spellcheck: false, placeholder: "Base32 key or otpauth:// link from the site's 2FA setup" })),
    h("label", h("span", "Notes"), h("textarea", { name: "notes", value: e.notes || "" })),
    err,
    h("div.row", h("button.primary", { type: "submit" }, "Save"),
      h("button", { type: "button", onclick: () => { vaultState.editing = null; vaultState.prefill = null; renderVault(); } }, "Cancel")));
  return form;
}

function accessView(d) {
  const pending = d.requests.filter((r) => r.status === "pending");
  return h("section.pw-access",
    h("div.panel", h("h3", "Waiting for you"),
      pending.length ? h("div", { style: "display:grid; gap:10px" }, pending.map(requestCard))
                     : h("p.dim", "No requests. When an agent needs to sign in or a code, it shows up here and at the top of every page.")),
    h("div.panel", h("h3", "Standing permissions"),
      d.grants.length ? h("table", h("thead", h("tr", ["Agent", "Password", "Until", ""].map((x) => h("th", x)))),
        h("tbody", d.grants.map((g) => h("tr", h("td", g.agent), h("td", g.label),
          h("td.dim", g.until ? new Date(g.until * 1000).toLocaleString() : "always"),
          h("td.actions", h("button.ghost", { onclick: () => act(() => api("DELETE", `/api/vault/grants/${g.agent}/${g.entry}`), "Access removed").then(refreshRequests) }, "Remove"))))))
        : h("p.dim", "No agent can use a password without asking.")),
    h("div.panel", h("h3", "Activity"),
      d.audit.length ? h("table", h("tbody", d.audit.map((a) => h("tr",
        h("td.dim", { style: "white-space:nowrap" }, ago(a.ts)),
        h("td", [a.agent && h("b", a.agent + " "), a.action.replace("_", " "), " ", a.site || a.label || "",
                 a.scope ? ` (${a.scope})` : "", a.entry && a.action === "filled" ? ` with ${a.entry}` : "",
                 a.error ? ` — ${a.error}` : ""])))))
        : h("p.dim", "Nothing yet.")));
}

// Live codes: fetch once, count down locally, refetch when the 30s window rolls over.
let codeCache = { at: 0, codes: [] };
async function tickCodes(force) {
  const els = document.querySelectorAll("[data-code]");
  if (!els.length) return;
  const age = (Date.now() - codeCache.at) / 1000;
  const left = codeCache.codes[0] ? codeCache.codes[0].seconds_left - Math.floor(age) : 0;
  if (force === true || left <= 0) {
    try { codeCache = { at: Date.now(), codes: await api("GET", "/api/vault/codes") }; } catch { return; }
  }
  const secs = codeCache.codes[0] ? Math.max(1, codeCache.codes[0].seconds_left - Math.floor((Date.now() - codeCache.at) / 1000)) : 30;
  for (const el of document.querySelectorAll("[data-code]")) {
    const c = codeCache.codes.find((x) => x.id === el.dataset.code);
    if (c) el.textContent = fmtCode(c.code);
  }
  for (const el of document.querySelectorAll("[data-ring]")) el.replaceChildren(codeRing(secs));
}
