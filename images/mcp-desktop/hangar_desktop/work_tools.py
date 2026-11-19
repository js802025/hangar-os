"""Work without the screen: commands, files and the web.

Most work doesn't need a desktop at all, and doing it by command is faster,
cheaper and more reliable than clicking. These tools let any agent (local
models included, which have no shell of their own) run commands in its
container, read and write files, fetch pages and search the web. The screen
tools are for what's left: apps with no other way in, and sites that need
real input (forms, sign-ins, pages that only work in a browser).

Everything runs as the agent's user inside its own container, and web
requests go through the space's network rules (its allowlist gateway, if it
has one), like any other program in it.
"""

from __future__ import annotations

import html
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit

import httpx

WORKSPACE = Path.home() / "workspace"
MAX_OUT = 20_000
UA = "Mozilla/5.0 (X11; Linux) Hangar/1.0 (an AI agent working for its user)"


def _path(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else WORKSPACE / path


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)"


class _Text(HTMLParser):
    """Readable text from HTML, with links as [text](url)."""

    SKIP = {"script", "style", "noscript", "svg", "head", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "header", "footer", "ul", "ol", "table", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.href: str | None = None
        self.link: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag in ("h1", "h2", "h3"):
                self.out.append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self.out.append("- ")
        elif tag == "a" and not self.skip:
            self.href = dict(attrs).get("href")
            self.link = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "a" and self.href is not None:
            text = " ".join("".join(self.link).split())
            if text and self.href.startswith(("http", "/")):
                self.out.append(f"[{text}]({self.href})")
            else:
                self.out.append(text)
            self.href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip:
            return
        (self.link if self.href is not None else self.out).append(data)

    def text(self) -> str:
        t = "".join(self.out)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n\s*\n+", "\n\n", t)
        return t.strip()


def page_text(body: str) -> tuple[str, str]:
    p = _Text()
    p.feed(body)
    return p.title.strip(), p.text()


def _get(url: str, **kw) -> httpx.Response:
    if "://" not in url:
        url = "https://" + url
    # trust_env: proxies come from the space's network rules (HTTPS_PROXY)
    return httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30, **kw)


def register(mcp) -> None:
    @mcp.tool()
    def run_command(command: str, timeout: int = 120, cwd: str = "") -> str:
        """Run a shell command (bash) in your own Linux container, in your workspace
        (/home/agent/workspace) by default. Use this FIRST for anything a command
        can do: files, data, downloads (curl), scripts (python3), unzipping, git,
        converting documents (libreoffice --headless when installed). Returns the
        exit code and output."""
        where = _path(cwd) if cwd else WORKSPACE
        where.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(["bash", "-lc", command], cwd=where, capture_output=True, text=True,
                               timeout=max(1, min(timeout, 1800)))
        except subprocess.TimeoutExpired as e:
            out = "".join(x.decode(errors="replace") if isinstance(x, bytes) else (x or "")
                          for x in (e.stdout, e.stderr))
            return _clip(f"timed out after {timeout}s (run long jobs in the background with &)\n{out}", MAX_OUT)
        out = p.stdout + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
        return _clip(f"exit {p.returncode}\n{out}", MAX_OUT)

    @mcp.tool()
    def read_file(path: str, max_chars: int = 20000) -> str:
        """Read a text file (relative paths are in your workspace)."""
        f = _path(path)
        if not f.is_file():
            raise ValueError(f"no file {f}")
        return _clip(f.read_text(errors="replace"), max(1, max_chars))

    @mcp.tool()
    def write_file(path: str, text: str, append: bool = False) -> str:
        """Write (or append to) a text file; relative paths are in your workspace.
        Write files this way, not by typing into an editor on the desktop."""
        f = _path(path)
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a" if append else "w") as fh:
            fh.write(text)
        return f"{'appended to' if append else 'wrote'} {f} ({len(text)} characters)"

    @mcp.tool()
    def web_fetch(url: str, max_chars: int = 12000) -> str:
        """Fetch a web page or API without opening a browser: pages come back as
        readable text with links, JSON and plain text as they are. Use this to read
        pages; use the browser only when a page needs input or only works with
        JavaScript (then this returns little or no text)."""
        r = _get(url)
        kind = r.headers.get("content-type", "")
        head = f"{r.status_code} {r.url}\n"
        if "html" in kind:
            title, text = page_text(r.text)
            return _clip(head + (f"# {title}\n\n" if title else "") + text, max(500, max_chars))
        if kind.startswith(("text/", "application/json", "application/xml", "application/rss")) or not kind:
            return _clip(head + r.text, max(500, max_chars))
        return head + f"({kind}, {len(r.content)} bytes: download it with run_command and curl instead)"

    @mcp.tool()
    def web_search(query: str, max_results: int = 8) -> str:
        """Search the web (DuckDuckGo) without a browser. Returns titles, links and
        snippets; read a result with web_fetch."""
        r = _get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
        results = []
        for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)'
                             r'(?=<a[^>]+class="result__a"|$)', r.text, re.S):
            href, title, rest = m.groups()
            q = parse_qs(urlsplit(html.unescape(href)).query)
            link = unquote(q["uddg"][0]) if "uddg" in q else html.unescape(href)
            snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
            clean = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", "", s)).split())  # noqa: E731
            results.append(f"{len(results) + 1}. {clean(title)}\n   {link}\n   {clean(snip.group(1)) if snip else ''}")
            if len(results) >= max_results:
                break
        if not results:
            return f"no results parsed (HTTP {r.status_code}); try web_fetch on a site's own search page"
        return "\n".join(results)

