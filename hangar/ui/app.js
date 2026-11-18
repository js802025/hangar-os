// Hangar Spaces. Plain JS, no build step: edit, reload.
// Pages: #/ (Spaces), #/agent/<name>, #/accounts, #/models, #/settings.
"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, root = document) => root.querySelector(sel);

// h("div.card", {onclick}, child, "text", ...) -> element. Text is always text.
function h(spec, attrs, ...kids) {
  if (attrs == null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    kids.unshift(attrs); attrs = {};
  }
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += " " + v;
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent("/" + location.hash); throw new Error("signed out"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || data || r.statusText);
  return data;
}

function toast(title, msg = "", { err = false, onclick } = {}) {
  const t = h("div.toast", { class: err ? "err" : "", onclick: () => { onclick && onclick(); t.remove(); } },
    h("b", title), msg && h("div.dim.clamp", msg));
  $("#toasts").append(t);
  setTimeout(() => t.remove(), err ? 9000 : 6000);
}

async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast("Something went wrong", e.message, { err: true }); }
}

const stateClass = (s) => "s-" + String(s || "idle").replace(/ /g, "_");
const badge = (s) => h("span.badge", { class: stateClass(s) }, s === "waiting_on_user" ? "needs you" : s || "idle");
function ago(ts) {
  if (!ts) return "";
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return new Date(ts).toLocaleDateString();
}
const modelLabel = (m) => !m ? "no model" : m.startsWith("local:") ? m.slice(6).split("/").pop() : m;

// ---------------------------------------------------------------- live state
const store = { agents: [], loaded: false, listeners: new Set() };

async function refreshAgents() {
  try { store.agents = await api("GET", "/api/agents"); } catch { return; }
  store.loaded = true;
  store.agents.sort((a, b) => a.name.localeCompare(b.name));
  store.listeners.forEach((fn) => fn());
}

let refreshTimer = null;
function refreshSoon() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshAgents, 300); }

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onopen = () => $("#conn").classList.add("live");
  es.onerror = () => $("#conn").classList.remove("live");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "vault") {
      refreshRequestsSoon();
      if (ev.status === "pending") toast(`${ev.agent} needs you`,
        `${ev.kind === "login" ? "Sign in to" : "Code for"} ${ev.site}: ${ev.reason || ""}`);
      return;
    }
    refreshSoon();
    if (ev.type === "report" && (ev.final || ev.status === "waiting_on_user")) {
      const what = { done: "finished", error: "hit a problem", waiting_on_user: "needs you" }[ev.status] || ev.status;
      toast(`${ev.agent} ${what}`, ev.message, { err: ev.status === "error", onclick: () => go(`#/agent/${ev.agent}`) });
    }
  };
}
setInterval(() => document.visibilityState === "visible" && refreshAgents(), 8000);

// ---------------------------------------------------------------- router
const view = () => $("#view");
let cleanup = [];
function go(hash) { location.hash = hash; }

// Each page can also run as its own app window (/app/<name>): on the Hangar OS
// every app gets its own launcher and taskbar icon, like separate programs.
const APPS = {
  spaces: { title: "Hangar", pages: ["spaces", "agent"] },
  passwords: { title: "Passwords", pages: ["vault"] },
  accounts: { title: "Accounts", pages: ["accounts"] },
  models: { title: "Models", pages: ["models"] },
  settings: { title: "Hangar Settings", pages: ["settings"] },
};
const APP = (location.pathname.match(/^\/app\/(\w+)/) || [])[1];
const appFor = (page) => Object.keys(APPS).find((k) => APPS[k].pages.includes(page)) || "spaces";
if (APP && APPS[APP]) {
  document.body.classList.add("standalone", `app-${APP}`);
  document.title = APPS[APP].title;
  $(".brand span").textContent = APPS[APP].title;
  $(".brand").href = "#/" + APPS[APP].pages[0];
}

function route() {
  cleanup.forEach((fn) => fn()); cleanup = [];
  store.listeners.clear();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  let page = parts[0] || (APP && APPS[APP] ? APPS[APP].pages[0] : "spaces");
  if (APP && APPS[APP] && !APPS[APP].pages.includes(page)) {
    // A link into another app: open that app's window instead.
    if (parts[0]) window.open(`/app/${appFor(page)}${location.hash}`, `hangar-${appFor(page)}`);
    history.replaceState(null, "", "#/" + APPS[APP].pages[0]);
    page = APPS[APP].pages[0];
  }
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("on", a.dataset.nav === (page === "agent" ? "spaces" : page)));
  view().replaceChildren();
  ({ spaces: Spaces, agent: () => AgentPage(decodeURIComponent(parts[1] || "")),
     accounts: Accounts, models: Models, vault: Vault, settings: Settings }[page] || Spaces)();
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- Spaces
function Spaces() {
  const grid = h("div.grid");
  const head = h("div.page-head", h("h2", "Spaces"), h("span.dim"));
  view().append(head, grid);
  const cards = new Map();

  function card(a) {
    const img = h("img", { alt: `${a.name}'s desktop` });
    const c = h("article.card", { onclick: () => go(`#/agent/${encodeURIComponent(a.name)}`), tabIndex: 0,
                                   onkeydown: (e) => e.key === "Enter" && c.click() },
      h("div.thumb", img, h("div.off"), h("span.badge")),
      h("div.meta",
        h("div.name", h("span.n")),
        h("div.sub"),
        h("div.task.clamp"),
        h("div.report.clamp")));
    c._img = img;
    return c;
  }

  function render() {
    const agents = store.agents;
    if (!store.loaded) return;
    head.lastChild.textContent = agents.length ? `${agents.length} agent${agents.length > 1 ? "s" : ""}` +
      ` · ${agents.filter((a) => a.state === "working").length} working` : "";
    if (!agents.length) {
      grid.replaceChildren(h("div.empty", { style: "grid-column: 1/-1" },
        h("h2", "No agents yet"),
        h("p", "Each agent gets its own hidden desktop. Give one a task and watch it work here."),
        h("button.primary", { onclick: newAgentDialog }, "Launch your first agent")));
      cards.clear();
      return;
    }
    const keep = new Set();
    for (const a of agents) {
      let c = cards.get(a.name);
      if (!c) { c = card(a); cards.set(a.name, c); }
      keep.add(a.name);
      const running = a.status === "running";
      c._running = running;
      $(".badge", c).replaceWith(badge(a.state));
      $(".off", c).textContent = running ? "" : a.status === "exited" ? "Stopped" : a.status;
      $(".off", c).style.display = running ? "none" : "grid";
      c._img.style.display = running ? "block" : "none";
      $(".n", c).textContent = a.name;
      $(".sub", c).textContent = `${a.flavor} · ${modelLabel(a.model)}${a.account ? " · " + a.account : ""}`;
      $(".task", c).textContent = a.task || "No task";
      $(".report", c).textContent = a.report ? a.report.message : "";
      grid.append(c);   // keeps sorted order
    }
    for (const [n, c] of cards) if (!keep.has(n)) { c.remove(); cards.delete(n); }
    if (grid.querySelector(".empty")) grid.querySelector(".empty").remove();
  }

  // Live thumbnails: reload each running agent's screenshot, one request at a time per card.
  function tick() {
    if (document.visibilityState !== "visible") return;
    for (const [name, c] of cards) {
      if (!c._running || c._loading) continue;
      c._loading = true;
      const next = new Image();
      next.onload = () => { c._img.src = next.src; c._loading = false; };
      next.onerror = () => { c._loading = false; };
      next.src = `/api/agents/${encodeURIComponent(name)}/screenshot.png?w=640&t=${Date.now()}`;
    }
  }
  store.listeners.add(() => { render(); tick(); });
  const t = setInterval(tick, 2000);
  cleanup.push(() => clearInterval(t));
  render();
  refreshAgents();
}

// ---------------------------------------------------------------- agent focus
function AgentPage(name) {
  const enc = encodeURIComponent(name);
  let control = null, tab = "reports", lastReports = "";
  const frame = h("iframe", { title: `${name}'s desktop`, allow: "fullscreen; clipboard-read; clipboard-write" });
  const screen = h("div.screen", frame);
  const banner = h("div.banner", { style: "display:none" },
    h("span.grow", "You're in control. The agent's mouse and keyboard tools are paused."),
    h("button.primary", { onclick: () => act(() => api("POST", `/api/agents/${enc}/release`)).then(refreshAgents) },
      "Hand back to agent"));
  const stateEl = h("span");
  const actions = h("div.actions");
  const taskInput = h("input", { placeholder: "Give it a new task…", name: "task" });
  const taskbar = h("form.taskbar", { onsubmit: async (e) => {
    e.preventDefault();
    const task = taskInput.value.trim();
    if (!task) return;
    if (await act(() => api("POST", `/api/agents/${enc}/run`, { task }), "Task started")) taskInput.value = "";
    refreshAgents();
  } }, taskInput, h("button.primary", { type: "submit" }, "Run"));
  const body = h("div.body");
  const tabs = h("div.tabs",
    ["reports", "logs", "about"].map((t) => h("button", { type: "button", "data-t": t,
      onclick: () => { tab = t; lastReports = ""; showTab(); } }, t[0].toUpperCase() + t.slice(1))));

  view().append(
    h("div.focus-head", h("button.ghost", { onclick: () => go("#/") }, "← Spaces"), h("h2", name), stateEl, actions),
    h("div.focus",
      h("div", banner, screen, taskbar),
      h("aside.side", tabs, body)));

  function setControl(on) {
    if (control === on) return;
    control = on;
    frame.src = `/agents/${enc}/view${on ? "?control=1" : ""}`;
    screen.classList.toggle("control", on);
    banner.style.display = on ? "flex" : "none";
  }

  function render() {
    const a = store.agents.find((x) => x.name === name);
    if (!a && !store.loaded) return;
    if (!a) { view().replaceChildren(h("div.empty", h("h2", `No agent named ${name}`), h("a", { href: "#/" }, "Back to Spaces"))); return; }
    stateEl.replaceChildren(badge(a.state));
    const running = a.status === "running";
    const taken = a.state === "taken over";
    if (running) setControl(taken);
    else { control = null; frame.removeAttribute("src"); }
    screen.style.opacity = running ? 1 : .35;
    taskbar.style.display = a.model && running ? "flex" : "none";
    actions.replaceChildren(...[
      running && !taken && h("button", { onclick: () => act(() => api("POST", `/api/agents/${enc}/takeover`)).then(refreshAgents) }, "Take over"),
      running && taken && h("button", { onclick: () => act(() => api("POST", `/api/agents/${enc}/release`)).then(refreshAgents) }, "Release"),
      running && h("button.ghost", { onclick: () => screen.requestFullscreen && screen.requestFullscreen() }, "Full screen"),
      running ? h("button", { onclick: () => act(() => api("POST", `/api/agents/${enc}/stop`), "Stopped").then(refreshAgents) }, "Stop")
              : h("button", { onclick: () => act(() => api("POST", `/api/agents/${enc}/start`), "Starting…").then(refreshAgents) }, "Start"),
      h("button.danger", { onclick: () => deleteAgent(name) }, "Delete")].filter(Boolean));
    if (tab === "about") showTab();
  }

  async function showTab() {
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === tab));
    const a = store.agents.find((x) => x.name === name);
    if (tab === "about" && a) {
      body.replaceChildren(h("dl.info", [
        ["State", a.state], ["Flavor", a.flavor], ["Model", a.model || "none"], ["Account", a.account || "none"],
        ["Task", a.task || "none"], ["Shared", (a.shares || []).join("\n") || "nothing"], ["Created", a.created],
        ["MCP", location.origin + a.mcp]].map(([k, v]) => [h("dt", k), h("dd", { style: "white-space:pre-wrap" }, v)])));
    } else if (tab === "reports") {
      let reps;
      try { reps = await api("GET", `/api/agents/${enc}/reports`); } catch { return; }
      const sig = JSON.stringify(reps.slice(-1)) + reps.length;
      if (sig === lastReports || tab !== "reports") return;
      lastReports = sig;
      body.replaceChildren(reps.length ? h("div.reports", reps.slice().reverse().map((r) =>
        h("div.rep", { class: r.final ? "final" : "" },
          h("div.when", badge(r.status), ago(r.ts)),
          h("div.msg", r.message)))) : h("p.dim", "No reports yet. Agents post progress here as they work."));
    } else if (tab === "logs") {
      let text;
      try { text = await api("GET", `/api/agents/${enc}/logs?service=agent&tail=300`); } catch { return; }
      if (tab !== "logs") return;
      const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
      body.replaceChildren(h("pre.log", text.trim() || "No agent log yet (it starts with the first task)."));
      if (atBottom) body.scrollTop = body.scrollHeight;
    }
  }

  store.listeners.add(() => { render(); if (tab === "reports") showTab(); });
  const t = setInterval(() => document.visibilityState === "visible" && tab !== "about" && showTab(), 3000);
  cleanup.push(() => clearInterval(t));
  render(); showTab(); refreshAgents();
}

async function deleteAgent(name) {
  if (!confirm(`Delete ${name} and its files? This can't be undone.`)) return;
  if (await act(() => api("DELETE", `/api/agents/${encodeURIComponent(name)}`), `Deleted ${name}`)) {
    go("#/"); refreshAgents();
  }
}

// ---------------------------------------------------------------- new agent
async function newAgentDialog() {
  const dlg = $("#dlg");
  let opts;
  try { opts = await api("GET", "/api/options"); } catch (e) { return toast("Couldn't load options", e.message, { err: true }); }
  const model = h("select", { name: "model" },
    h("option", { value: "|" }, "No model (just a desktop)"),
    opts.accounts.length > 0 && h("optgroup", { label: "Subscriptions & API keys" },
      opts.accounts.map((a) => h("option", { value: `${a.harness}|${a.name}` },
        `${a.harness === "claude" ? "Claude Code" : "Codex"} · ${a.name}`))),
    opts.local_models.length > 0 && h("optgroup", { label: "Local models (LM Studio)" },
      opts.local_models.map((m) => h("option", { value: `local:${m.key}|` },
        `${m.name}${m.vision ? " · vision" : " · text only"}${m.loaded ? " · loaded" : ""}`))));
  if (opts.accounts.length) model.selectedIndex = 1;
  const shares = h("div", { style: "display:grid; gap:8px" });
  const addShare = () => shares.append(h("div.share-row",
    h("input", { placeholder: "/path/on/this/machine", class: "share-path" }),
    h("label.check", h("input", { type: "checkbox", class: "share-rw" }), "writable"),
    h("button.ghost", { type: "button", onclick: (e) => e.target.parentElement.remove() }, "✕")));
  const err = h("p.error");
  const submit = h("button.primary", { type: "submit" }, "Launch");
  const form = h("form", { method: "dialog", onsubmit: async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const [m, account] = String(f.get("model")).split("|");
    const body = { name: f.get("name").trim().toLowerCase(), flavor: f.get("flavor"), model: m, account,
      task: f.get("task").trim(),
      shares: [...shares.querySelectorAll(".share-row")].map((r) => {
        const p = $(".share-path", r).value.trim();
        return p && p + ($(".share-rw", r).checked ? ":rw" : "");
      }).filter(Boolean) };
    submit.disabled = true; submit.textContent = m.startsWith("local:") ? "Loading model…" : "Launching…";
    err.textContent = "";
    try {
      const r = await api("POST", "/api/agents", body);
      dlg.close();
      r.warnings.forEach((w) => toast("Heads up", w));
      await refreshAgents();
      go(`#/agent/${encodeURIComponent(r.name)}`);
    } catch (ex) { err.textContent = ex.message; }
    submit.disabled = false; submit.textContent = "Launch";
  } },
    h("h2", "New agent"),
    h("div.form-grid",
      h("label", h("span", "Name"), h("input", { name: "name", required: true, pattern: "[a-z0-9][a-z0-9\\-]{0,30}",
        placeholder: "scout", title: "lowercase letters, digits and dashes", autocomplete: "off" })),
      h("label", h("span", "Flavor"), h("select", { name: "flavor" }, opts.flavors.map((f) => h("option", f))))),
    h("label", h("span", "Model / account"), model,
      !opts.accounts.length && !opts.local_models.length &&
        h("span", "No accounts or local models yet. ", h("a", { href: "#/accounts", onclick: () => dlg.close() }, "Add an account"),
          " or ", h("a", { href: "#/models", onclick: () => dlg.close() }, "download a model"), ".")),
    h("label", h("span", "Task"), h("textarea", { name: "task", placeholder: "Find three used road bikes under $500 near Boston and compare them." })),
    h("div", { style: "display:grid; gap:8px" },
      h("div.row", h("span.dim.grow", { style: "font-size:12.5px; font-weight:500" }, "Shared folders (read-only unless writable)"),
        h("button.ghost", { type: "button", onclick: addShare }, "+ Add folder")),
      shares),
    err,
    h("div.foot", h("button", { type: "button", onclick: () => dlg.close() }, "Cancel"), submit));
  dlg.replaceChildren(form);
  dlg.showModal();
  form.elements.name.focus();
}

// ---------------------------------------------------------------- jobs (sign-in, downloads)
function jobDialog(job, { onDone } = {}) {
  const dlg = $("#dlg");
  const term = h("pre.log");
  const status = h("span.dim");
  const links = h("div.row.wrap");
  const codeInput = h("input", { placeholder: "Paste the code from the sign-in page", autocomplete: "off" });
  const input = h("form.row", { style: job.kind === "login" ? "" : "display:none", onsubmit: async (e) => {
    e.preventDefault();
    const text = codeInput.value.trim();
    if (!text) return;
    await act(() => api("POST", `/api/jobs/${job.id}/input`, { text }));
    codeInput.value = "";
  } }, codeInput, h("button.primary", { type: "submit" }, "Send"));
  const closeBtn = h("button", { type: "button", onclick: () => dlg.close() }, "Close");
  const cancelBtn = h("button.danger", { type: "button", onclick: () => api("DELETE", `/api/jobs/${job.id}`) }, "Cancel");
  dlg.replaceChildren(h("div.dlg",
    h("h2", job.title),
    job.kind === "login" && h("p.dim", { style: "margin:0" },
      "This is the official sign-in. Open the link, sign in in your browser, then paste back any code it shows. Hangar never sees your password."),
    links, h("div.term", term), input,
    h("div.foot", status, h("span.grow"), cancelBtn, closeBtn)));
  if (!dlg.open) dlg.showModal();
  let stopped = false;
  dlg.addEventListener("close", () => { stopped = true; }, { once: true });
  (async function poll() {
    while (!stopped) {
      let j;
      try { j = await api("GET", `/api/jobs/${job.id}`); } catch { break; }
      const atBottom = term.parentElement.scrollTop + term.parentElement.clientHeight >= term.parentElement.scrollHeight - 30;
      term.textContent = j.lines.join("\n") || "Starting…";
      if (atBottom) term.parentElement.scrollTop = term.parentElement.scrollHeight;
      links.replaceChildren(...j.urls.map((u) => h("a.btn.primary", { href: u, target: "_blank", rel: "noopener" },
        j.kind === "login" ? "Open sign-in page ↗" : "Open link ↗")));
      if (j.done) {
        status.textContent = j.code === 0 ? "Finished." : `Exited with code ${j.code}.`;
        input.style.display = "none"; cancelBtn.style.display = "none";
        onDone && onDone(j);
        break;
      }
      status.textContent = "Running…";
      await new Promise((r) => setTimeout(r, 1000));
    }
  })();
}

// ---------------------------------------------------------------- Accounts
async function Accounts() {
  const wrap = h("div");
  view().append(h("div.page-head", h("h2", "Accounts"),
    h("span.dim", "Subscriptions and API keys. Each lives in its own volume, mounted only into the agents that use it.")), wrap);
  async function load() {
    let d;
    try { d = await api("GET", "/api/accounts"); } catch { return; }
    const provider = h("select", { name: "provider" }, Object.entries(d.providers).map(([k, v]) => h("option", { value: k }, v)));
    const keyRow = h("label", { style: "display:none" }, h("span", "API key"), h("input", { name: "key", type: "password", autocomplete: "off" }));
    provider.onchange = () => keyRow.style.display = provider.value.endsWith("-key") ? "grid" : "none";
    const err = h("p.error");
    const form = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      const f = new FormData(form);
      const name = f.get("name").trim().toLowerCase();
      try {
        const r = await api("POST", "/api/accounts", { name, provider: f.get("provider") });
        if (r.signin) {
          const job = await api("POST", `/api/accounts/${name}/login`);
          jobDialog(job, { onDone: () => load() });
        } else {
          await api("POST", `/api/accounts/${name}/key`, { key: f.get("key") });
          toast(`Saved ${name}`, "The key is stored in the account's volume.");
        }
        form.reset(); keyRow.style.display = "none";
        load();
      } catch (ex) { err.textContent = ex.message; }
    } },
      h("div.form-grid",
        h("label", h("span", "Name"), h("input", { name: "name", required: true, placeholder: "personal", pattern: "[a-z0-9][a-z0-9\\-]{0,30}" })),
        h("label", h("span", "Type"), provider),
        keyRow,
        h("button.primary", { type: "submit" }, "Add account")), err);
    const rows = d.accounts.map((a) => {
      const st = h("span.dim");
      return h("tr",
        h("td", h("b", a.name)), h("td", a.label), h("td.dim", a.agents.join(", ") || "none"), h("td", st),
        h("td.actions",
          h("button.ghost", { onclick: async () => { st.textContent = "checking…"; st.textContent = (await act(() => api("GET", `/api/accounts/${a.name}/status`)) || {}).status || ""; } }, "Status"),
          a.signin ? h("button", { onclick: async () => { const j = await act(() => api("POST", `/api/accounts/${a.name}/login`)); j && jobDialog(j); } }, "Sign in")
                   : h("button", { onclick: async () => {
                       const key = prompt(`New API key for ${a.name}`);
                       if (key) act(() => api("POST", `/api/accounts/${a.name}/key`, { key }), "Key replaced");
                     } }, "Replace key"),
          h("button.danger", { onclick: async () => {
            if (confirm(`Delete account ${a.name} and its stored sign-in?`) && await act(() => api("DELETE", `/api/accounts/${a.name}`), "Deleted")) load();
          } }, "Delete")));
    });
    wrap.replaceChildren(
      h("div.panel", h("h3", "Add an account"), form),
      h("div.panel", d.accounts.length ? h("table",
        h("thead", h("tr", ["Name", "Type", "Used by", "Status", ""].map((x) => h("th", x)))), h("tbody", rows))
        : h("p.dim", "No accounts yet. Add a Claude or ChatGPT subscription, or an API key.")));
  }
  load();
}

// ---------------------------------------------------------------- Models
async function Models() {
  const wrap = h("div");
  view().append(h("div.page-head", h("h2", "Models"), h("span.dim", "Local models run in LM Studio on this machine and are shared by every agent.")), wrap);
  async function load() {
    let d;
    try { d = await api("GET", "/api/models"); } catch { return; }
    if (!d.installed) {
      wrap.replaceChildren(h("div.panel", h("h3", "LM Studio isn't installed"),
        h("p.dim", "Install LM Studio from lmstudio.ai and open it once, then come back here.")));
      return;
    }
    const q = h("input", { name: "query", placeholder: "e.g. qwen/qwen2.5-vl-7b", required: true });
    const dl = h("form.row", { onsubmit: async (e) => {
      e.preventDefault();
      const j = await act(() => api("POST", "/api/models/download", { query: q.value.trim() }));
      if (j) { q.value = ""; jobDialog(j, { onDone: load }); }
    } }, q, h("button.primary", { type: "submit" }, "Download"));
    wrap.replaceChildren(
      h("div.panel",
        h("div.row", h("h3.grow", { style: "margin:0" }, "LM Studio"),
          h("span.pill", { class: d.server ? "ok" : "" }, d.server ? "server running" : "server stopped"),
          !d.server && h("button", { onclick: () => act(() => api("POST", "/api/models/serve"), "Server started").then(load) }, "Start server")),
        h("p.dim", "Agents reach it at ", h("code", d.agent_url), ". Vision models can use screenshots; text-only models use the browser's text tools."),
        dl),
      h("div.panel", d.models.length ? h("table",
        h("thead", h("tr", ["Model", "Size", "Vision", "Tools", "Loaded", ""].map((x) => h("th", x)))),
        h("tbody", d.models.map((m) => h("tr",
          h("td", h("b", m.name), h("div.dim", { style: "font-size:12px" }, m.key)),
          h("td", `${m.size_gb} GB`), h("td", m.vision ? h("span.yes", "yes") : h("span.dim", "no")),
          h("td", m.tools ? h("span.yes", "yes") : h("span.dim", "no")), h("td", m.loaded ? "●" : ""),
          h("td.actions", h("button.danger", { onclick: async () => {
            if (confirm(`Delete ${m.name} from disk?`) && await act(() => api("POST", "/api/models/remove", { key: m.key }), "Deleted")) load();
          } }, "Delete"))))))
        : h("p.dim", "No models downloaded yet.")));
  }
  load();
}

// ---------------------------------------------------------------- Settings
async function Settings() {
  const wrap = h("div.cols");
  view().append(h("div.page-head", h("h2", "Settings")), wrap);
  const LABELS = { url: "Topic URL", token: "Access token (optional)", webhook: "Incoming webhook URL",
                   bot_token: "Bot token", chat_id: "Chat ID" };
  const HELP = { ntfy: "Easiest for phones: install the ntfy app and subscribe to a hard-to-guess topic.",
                 slack: "Create an incoming webhook for a channel.", telegram: "Make a bot with @BotFather, then message it once." };
  async function load() {
    let n, s;
    try { [n, s] = await Promise.all([api("GET", "/api/notify"), api("GET", "/api/settings")]); } catch { return; }
    const channels = Object.entries(n.fields).map(([ch, fields]) => {
      const cur = n.channels[ch] || {};
      const form = h("form", { style: "display:grid; gap:10px", onsubmit: async (e) => {
        e.preventDefault();
        const body = Object.fromEntries(new FormData(form));
        if (await act(() => api("PUT", `/api/notify/${ch}`, body), `Saved ${ch}`)) load();
      } },
        h("div.row", h("h3.grow", { style: "margin:0; text-transform:capitalize" }, ch),
          h("span.pill", { class: n.channels[ch] ? "ok" : "" }, n.channels[ch] ? "on" : "off")),
        h("p.dim", { style: "margin:0; font-size:12.5px" }, HELP[ch]),
        fields.map((f) => h("label", h("span", LABELS[f]),
          h("input", { name: f, value: cur[f] || "", type: n.secret_fields.includes(f) ? "password" : "text", autocomplete: "off",
                       placeholder: f === "url" ? "https://ntfy.sh/hangar-7f3k2q" : "" }))),
        h("div.row", h("button.primary", { type: "submit" }, "Save"),
          n.channels[ch] && h("button.ghost", { type: "button", onclick: async () => {
            if (await act(() => api("DELETE", `/api/notify/${ch}`), `Removed ${ch}`)) load();
          } }, "Remove")));
      return h("div", { style: "padding:14px 0; border-top:1px solid var(--line)" }, form);
    });
    const results = h("span.dim");
    const lan = h("input", { type: "checkbox", checked: s.lan, onchange: async () => {
      await act(() => api("PUT", "/api/settings", { lan: lan.checked }));
      restart.style.display = "inline-block";
    } });
    const restart = h("button.primary", { style: "display:none", onclick: async () => {
      restart.disabled = true; restart.textContent = "Restarting…";
      await api("POST", "/api/daemon/restart").catch(() => {});
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 500));
        try { if ((await fetch("/healthz")).ok) { location.reload(); return; } } catch {}
      }
    } }, "Restart the daemon to apply");
    const qr = h("div.qr");
    qr.innerHTML = s.qr;   // SVG generated by our own server
    wrap.replaceChildren(
      h("div.panel", h("div.row", h("h3.grow", { style: "margin:0" }, "Notifications"),
          h("button", { onclick: async () => {
            results.textContent = "sending…";
            const r = await act(() => api("POST", "/api/notify/test"));
            results.textContent = r ? (Object.entries(r.results).map(([k, v]) => `${k}: ${v}`).join(" · ") || "No channels set") : "";
          } }, "Send test")),
        h("p.dim", "You get a notification when an agent finishes, hits a problem or needs you. ", results),
        channels),
      h("div.panel", h("h3", "Access"),
        h("dl.info", h("dt", "This machine"), h("dd", h("code", s.local_url)),
          s.lan_urls.length > 0 && [h("dt", "On your network"), h("dd", s.lan_urls.map((u) => h("div", h("code", u))))]),
        h("label.check", lan, "Open Spaces from other devices on my network"),
        h("p.dim", { style: "font-size:12.5px" }, "Everything still needs the admin token. Traffic on your network isn't encrypted, so only turn this on for a network you trust."),
        restart,
        s.qr && h("div", { style: "margin-top:12px; display:grid; gap:8px; justify-items:start" },
          h("span.dim", "Scan to open Spaces on your phone (signs it in):"), qr)),
      h("div.panel", h("h3", "About"),
        h("dl.info", h("dt", "State"), h("dd", h("code", s.state_dir)), h("dt", "Config"), h("dd", h("code", s.config_dir)),
          h("dt", "Admin token"), h("dd", h("code", "hangar daemon token")))));
  }
  load();
}

// ---------------------------------------------------------------- boot
$("#new-agent").addEventListener("click", newAgentDialog);
connectEvents();
route();
refreshRequests();
