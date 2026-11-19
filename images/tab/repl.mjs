// An agent's Playwright session for Hangar's in-app browser, Aside-style:
// pages are read as an accessibility tree with refs, and acted on with
// Playwright (by ref, or by running Playwright code). Screenshots are a fallback.
//
// Line-delimited JSON on stdin/stdout: {"id", "op", "agent", ...} -> {"id", "ok", "result"|"error"}.
// Connects through the Hangar daemon, which only shows this space its own tabs.
import { chromium } from "playwright-core";
import readline from "node:readline";

const SPACE = process.env.HANGAR_AGENT || "agent";
const daemon = (process.env.HANGAR_DAEMON_URL || "http://hangar-host:7700").replace(/^http/, "ws");
const TOKEN = process.env.HANGAR_AGENT_TOKEN || "";
const MAX = 24000;
let browser = null;
const current = new Map();            // agent -> its current page (each agent has its own tab)
const events = new Map();             // agent -> things that happened (popups, downloads, dialogs)

async function connect() {
  if (browser && browser.isConnected()) return browser;
  browser = await chromium.connectOverCDP(`${daemon}/api/agents/${SPACE}/browser/cdp`,
                                          { headers: { Authorization: `Bearer ${TOKEN}` }, timeout: 30000 });
  browser.on("disconnected", () => { browser = null; current.clear(); });
  return browser;
}
const ctx = async () => (await connect()).contexts()[0];
function note(agent, what) { (events.get(agent) || events.set(agent, []).get(agent)).push(what); }
function watch(agent, page) {
  if (page.__hangar) return page;
  page.__hangar = true;
  page.on("popup", (p) => { watch(agent, p); note(agent, `a popup opened (${p.url()}): tab_switch to use it`); });
  page.on("download", (d) => note(agent, `download started: ${d.suggestedFilename()}`));
  page.on("dialog", async (d) => { note(agent, `dialog (${d.type()}): "${d.message()}", accepted`); await d.accept().catch(() => {}); });
  page.on("close", () => { if (current.get(agent) === page) current.delete(agent); });
  return page;
}
async function page(agent, create = true) {
  let p = current.get(agent);
  if (p && !p.isClosed()) return p;
  if (!create) throw new Error("no tab open: call tab_open first");
  p = watch(agent, await (await ctx()).newPage());
  current.set(agent, p);
  return p;
}
async function snapshot(p) {
  let text;
  try { const s = await p._snapshotForAI(); text = typeof s === "string" ? s : s.full; }
  catch { text = await p.locator("body").ariaSnapshot(); }
  const head = `url: ${p.url()}\ntitle: ${await p.title()}\n`;
  return head + (text.length > MAX ? text.slice(0, MAX) + `\n… (${text.length - MAX} more characters: use tab_run to read selectively)` : text);
}
const ref = (p, r) => p.locator(`aria-ref=${String(r).replace(/^\[?ref=|\]$/g, "")}`);
function fmt(v) {
  if (v === undefined) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 1); } catch { return String(v); }
}

const ops = {
  async open({ agent, url }) {
    const p = watch(agent, await (await ctx()).newPage());
    current.set(agent, p);
    if (url) await p.goto(url.includes("://") ? url : "https://" + url, { waitUntil: "domcontentloaded", timeout: 45000 });
    return await snapshot(p);
  },
  async go({ agent, url }) {
    const p = await page(agent);
    await p.goto(url.includes("://") ? url : "https://" + url, { waitUntil: "domcontentloaded", timeout: 45000 });
    return await snapshot(p);
  },
  async snapshot({ agent }) { return await snapshot(await page(agent, false)); },
  async click({ agent, ref: r }) {
    const p = await page(agent, false);
    await ref(p, r).click({ timeout: 10000 });
    await p.waitForLoadState("domcontentloaded").catch(() => {});
    return await snapshot(p);
  },
  async type({ agent, ref: r, text, submit }) {
    const p = await page(agent, false);
    await ref(p, r).fill(text, { timeout: 10000 });
    if (submit) { await ref(p, r).press("Enter"); await p.waitForLoadState("domcontentloaded").catch(() => {}); }
    return await snapshot(p);
  },
  async select({ agent, ref: r, option }) {
    const p = await page(agent, false);
    await ref(p, r).selectOption(option, { timeout: 10000 });
    return await snapshot(p);
  },
  async run({ agent, code }) {
    const p = await page(agent);
    const logs = [];
    const console_ = { log: (...a) => logs.push(a.map(fmt).join(" ")) };
    const fn = new Function("page", "context", "browser", "console", "ref",
                            `return (async () => { ${code}\n })()`);
    const value = await fn(p, await ctx(), await connect(), console_, (r) => ref(p, r));
    const out = [logs.join("\n"), fmt(value)].filter(Boolean).join("\n");
    return out.length > MAX ? out.slice(0, MAX) + "\n…" : out || "(done, nothing returned)";
  },
  async tabs({ agent }) {
    const pages = (await ctx()).pages();
    const mine = current.get(agent);
    return (await Promise.all(pages.map(async (p, i) => `${p === mine ? "*" : " "} ${i}: ${await p.title().catch(() => "")} — ${p.url()}`))).join("\n") || "no tabs";
  },
  async switch({ agent, index }) {
    const p = (await ctx()).pages()[index];
    if (!p) throw new Error(`no tab ${index}`);
    current.set(agent, watch(agent, p));
    return await snapshot(p);
  },
  async close({ agent }) {
    const p = current.get(agent);
    if (p) await p.close();
    current.delete(agent);
    return "closed";
  },
  // Hangar's vault fills a login into the agent's tab (after the user approves). Same rules as
  // the desktop browser: only on that site, never over plain http. The values never reach the model.
  async fill({ agent, site, username, password, code }) {
    const p = current.get(agent);
    if (!p || p.isClosed()) throw new Error("no tab open");
    const u = new URL(p.url());
    const match = (h) => { h = (h || "").toLowerCase().replace(/^www\./, ""); return h === site || h.endsWith("." + site); };
    if (!match(u.hostname)) throw new Error(`the tab is on ${u.hostname || p.url()}, not ${site}. Open the ${site} sign-in page first.`);
    if (u.protocol !== "https:" && !["localhost", "127.0.0.1", "hangar-host"].includes(u.hostname))
      throw new Error("refusing to fill a login over plain http");
    const USER = 'input[autocomplete="username"], input[type="email"], input[name*="user" i], input[name*="email" i], ' +
                 'input[name*="login" i], input[id*="user" i], input[id*="email" i], input[id*="login" i]';
    const CODE = 'input[autocomplete="one-time-code"], input[name*="otp" i], input[name*="code" i], ' +
                 'input[id*="otp" i], input[id*="code" i], input[inputmode="numeric"]';
    const first = async (f, sel) => { const l = f.locator(sel);
      for (let i = 0; i < Math.min(await l.count(), 10); i++) { const e = l.nth(i);
        if (await e.isVisible() && await e.isEditable()) return e; } return null; };
    const filled = [];
    for (const f of p.frames().filter((f) => { try { return match(new URL(f.url()).hostname); } catch { return false; } })) {
      let e;
      if (code && !filled.includes("code") && (e = await first(f, CODE))) { await e.fill(code); filled.push("code"); }
      if (password && !filled.includes("password") && (e = await first(f, 'input[type="password"]'))) { await e.fill(password); filled.push("password"); }
      if (username && !filled.includes("username")) {
        e = await first(f, USER);
        if (!e && filled.includes("password")) e = await first(f, 'input[type="text"]');
        if (e) { await e.fill(username); filled.push("username"); }
      }
    }
    if (!filled.length) throw new Error("no login or code field found on this page");
    return JSON.stringify({ filled, page: u.hostname + u.pathname });
  },
  async screenshot({ agent }) {
    const p = await page(agent, false);
    return (await p.screenshot({ type: "png" })).toString("base64");
  },
};

const rl = readline.createInterface({ input: process.stdin });
let queue = Promise.resolve();        // one operation at a time, in order
rl.on("line", (line) => { queue = queue.then(() => handle(line)); });
async function handle(line) {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  const agent = msg.agent || SPACE;
  try {
    let result = await ops[msg.op]({ ...msg, agent });
    const ev = events.get(agent);
    if (ev && ev.length && msg.op !== "screenshot") { result += "\n\nMeanwhile: " + ev.join("; "); events.set(agent, []); }
    process.stdout.write(JSON.stringify({ id: msg.id, ok: true, result }) + "\n");
  } catch (e) {
    process.stdout.write(JSON.stringify({ id: msg.id, ok: false, error: String(e && e.message || e).split("\n")[0] }) + "\n");
  }
}
rl.on("close", () => queue.then(() => process.exit(0)));
