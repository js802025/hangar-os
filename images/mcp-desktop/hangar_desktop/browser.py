"""Agent-friendly browser tools.

Instead of guessing from pixels, the agent reads the page as a numbered list
of elements (`browser_snapshot`) and acts on them by number. Talks to the
agent's own Chromium over the DevTools protocol (Playwright connect_over_cdp);
Chromium listens on 127.0.0.1:9222 inside the container only.
"""

from __future__ import annotations

import asyncio
import subprocess

from playwright.async_api import Browser, Page, Playwright, async_playwright

CDP_URL = "http://127.0.0.1:9222"

# Runs in the page: tags visible elements worth acting on with data-hangar-ref
# and returns one compact line per element.
SNAPSHOT_JS = r"""
(maxItems) => {
  document.querySelectorAll('[data-hangar-ref]').forEach(e => e.removeAttribute('data-hangar-ref'));
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],' +
    '[role=radio],[role=tab],[role=menuitem],[role=option],[role=switch],[role=combobox],[role=textbox],' +
    '[role=searchbox],[contenteditable=""],[contenteditable=true],h1,h2,h3,[onclick]';
  const vw = innerWidth, vh = innerHeight;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 100);
  const nameOf = el => clean(el.getAttribute('aria-label') || el.getAttribute('alt') ||
    el.getAttribute('title') || el.getAttribute('placeholder') ||
    (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText ||
    (el.type === 'password' ? '' : el.value) || el.getAttribute('name') ||
    (el.querySelector('img[alt],svg[aria-label]') || {getAttribute: () => ''}).getAttribute('alt') ||
    (el.getAttribute('href') || '').slice(0, 60));
  const roleOf = el => {
    const r = el.getAttribute('role'); if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (/^h[1-3]$/.test(t)) return 'heading';
    if (t === 'input') {
      const ty = (el.type || 'text').toLowerCase();
      if (['checkbox', 'radio'].includes(ty)) return ty;
      if (['submit', 'button', 'reset'].includes(ty)) return 'button';
      return 'textbox';
    }
    if (t === 'textarea' || el.isContentEditable) return 'textbox';
    if (t === 'select') return 'combobox';
    return t === 'summary' ? 'button' : t;
  };
  // Collect candidates, then if over the cap keep on-screen ones first
  // (but print in page order so the list still reads top to bottom).
  const cands = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (cands.length >= 3000) break;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const off = r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw;
    cands.push({el, off});
  }
  let keep = cands;
  if (cands.length > maxItems) {
    const on = cands.filter(c => !c.off);
    const budget = Math.max(maxItems - on.length, 0);
    const offKeep = new Set(cands.filter(c => c.off).slice(0, budget));
    keep = cands.filter(c => !c.off || offKeep.has(c)).slice(0, Math.max(maxItems, on.length));
  }
  const out = []; let n = 0;
  for (const {el, off} of keep) {
    const role = roleOf(el), name = nameOf(el);
    if (role === 'heading') { out.push(`${'#'.repeat(+el.tagName[1])} ${name}`); continue; }
    n += 1; el.setAttribute('data-hangar-ref', n);
    let line = `[${n}] ${role} "${name}"`;
    if (role === 'textbox' && el.value) line += el.type === 'password' ? ' (filled)' : ` value="${clean(el.value)}"`;
    if (role === 'checkbox' || role === 'radio') line += el.checked ? ' (checked)' : '';
    if (role === 'combobox' && el.tagName === 'SELECT') line += ` value="${clean(el.value)}" options=[${[...el.options].slice(0, 15).map(o => clean(o.text)).join(' | ')}]`;
    if (el.disabled) line += ' (disabled)';
    if (off) line += ' (offscreen)';
    out.push(line);
  }
  if (cands.length > keep.length) out.push(`… ${cands.length - keep.length} more offscreen elements (scroll, then snapshot again)`);
  return out;
}
"""


class BrowserLink:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()

    async def _cdp_up(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-fsS", "-m", "1", f"{CDP_URL}/json/version",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return await proc.wait() == 0
        except OSError:
            return False

    async def browser(self) -> Browser:
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            if not await self._cdp_up():
                subprocess.Popen(["hangar-browser", "about:blank"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                for _ in range(50):
                    await asyncio.sleep(0.2)
                    if await self._cdp_up():
                        break
                else:
                    raise RuntimeError("Chromium did not start")
            if not self._pw:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.connect_over_cdp(CDP_URL)
            self._page = None
            return self._browser

    async def pages(self) -> list[Page]:
        b = await self.browser()
        pages = [p for ctx in b.contexts for p in ctx.pages if not p.url.startswith("devtools://")]
        if not pages:
            ctx = b.contexts[0] if b.contexts else await b.new_context()
            pages = [await ctx.new_page()]
        return pages

    async def page(self) -> Page:
        pages = await self.pages()
        if self._page is None or self._page.is_closed() or self._page not in pages:
            self._page = pages[-1]
        return self._page

    def select(self, page: Page) -> None:
        self._page = page

    async def snapshot(self, max_items: int = 250) -> str:
        p = await self.page()
        try:
            await p.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        lines = await p.evaluate(SNAPSHOT_JS, max_items)
        head = f"url: {p.url}\ntitle: {await p.title()}\n"
        return head + ("\n".join(lines) if lines else "(no interactive elements found)")

    async def locate(self, ref: int):
        p = await self.page()
        loc = p.locator(f'[data-hangar-ref="{ref}"]')
        if await loc.count() == 0:
            raise RuntimeError(f"No element [{ref}]; take a new browser_snapshot first.")
        return p, loc.first


link = BrowserLink()


def register(mcp, guard) -> None:
    """Add the browser_* tools to a FastMCP server. `guard` raises while paused."""

    @mcp.tool()
    async def browser_navigate(url: str) -> str:
        """Open a URL in the agent's browser (starts it if needed). Returns a page snapshot."""
        guard()
        if "://" not in url and not url.startswith(("about:", "data:")):
            url = "https://" + url
        p = await link.page()
        await p.goto(url, wait_until="domcontentloaded", timeout=45000)
        await p.bring_to_front()
        return await link.snapshot()

    @mcp.tool()
    async def browser_snapshot() -> str:
        """Read the current page as a numbered list of elements: `[n] role "name"`.
        Headings are shown as # lines. Use the numbers with browser_click / browser_type.
        Numbers change after every snapshot."""
        return await link.snapshot()

    @mcp.tool()
    async def browser_click(ref: int) -> str:
        """Click element [ref] from the latest snapshot. Returns a fresh snapshot."""
        guard()
        p, loc = await link.locate(ref)
        await loc.click(timeout=10000)
        await asyncio.sleep(0.6)
        return await link.snapshot()

    @mcp.tool()
    async def browser_type(ref: int, text: str, submit: bool = False) -> str:
        """Replace the text in field [ref] with `text`; submit=True presses Enter after."""
        guard()
        p, loc = await link.locate(ref)
        await loc.fill(text, timeout=10000)
        if submit:
            await loc.press("Enter")
            await asyncio.sleep(1.0)
        return await link.snapshot()

    @mcp.tool()
    async def browser_select(ref: int, option: str) -> str:
        """Choose `option` (its visible text or value) in dropdown [ref]."""
        guard()
        p, loc = await link.locate(ref)
        try:
            await loc.select_option(label=option, timeout=5000)
        except Exception:
            await loc.select_option(value=option, timeout=5000)
        return await link.snapshot()

    @mcp.tool()
    async def browser_press(key: str) -> str:
        """Press a key in the page, Playwright style: 'Enter', 'Escape', 'Control+A', 'PageDown'."""
        guard()
        p = await link.page()
        await p.keyboard.press(key)
        await asyncio.sleep(0.4)
        return await link.snapshot()

    @mcp.tool()
    async def browser_back() -> str:
        """Go back one page."""
        guard()
        p = await link.page()
        await p.go_back(wait_until="domcontentloaded")
        return await link.snapshot()

    @mcp.tool()
    async def browser_text(max_chars: int = 8000) -> str:
        """The page's readable text (for reading articles, results, docs)."""
        p = await link.page()
        text = await p.evaluate("() => document.body ? document.body.innerText : ''")
        return f"url: {p.url}\n\n{text[:max_chars]}" + ("\n…(truncated)" if len(text) > max_chars else "")

    @mcp.tool()
    async def browser_tabs() -> str:
        """List open tabs; the current one is marked with *."""
        cur = await link.page()
        lines = []
        for i, pg in enumerate(await link.pages()):
            lines.append(f"{'*' if pg == cur else ' '} {i}: {await pg.title()} — {pg.url}")
        return "\n".join(lines)

    @mcp.tool()
    async def browser_switch_tab(index: int) -> str:
        """Switch to tab `index` from browser_tabs, or -1 to open a new tab."""
        guard()
        pages = await link.pages()
        if index == -1:
            pg = await pages[0].context.new_page()
        else:
            pg = pages[index]
        link.select(pg)
        await pg.bring_to_front()
        return await link.snapshot()
