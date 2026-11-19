"""A small agent loop for local models (LM Studio or any OpenAI-compatible API).

Gives local models the same tools as the subscription harnesses: it connects
to this container's desktop MCP server, offers its tools as OpenAI function
tools, and loops until the model calls `report(final=True)` or stops asking
for tools. Screenshots are passed back as images when the model has vision.

  hangar-local-agent --model qwen/qwen2.5-vl-7b "find the weather in Paris"
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import json
import os
import re
import sys
import time
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SYSTEM = """You are an autonomous agent working on a private Linux computer inside Hangar.
Use the tools to get the task done; nobody is watching to answer questions.
Work the cheapest reliable way, in this order:
- Commands and files first: run_command, read_file, write_file (in /home/agent/workspace).
- Then the web without a browser: web_search, then web_fetch to read a page or API.
- Only when a site needs input (forms, sign-ins) or JavaScript, use the browser_* tools:
  browser_navigate(url), then the numbered elements with browser_click / browser_type.
- {vision_line}
- To sign in to a site, open its sign-in page and call request_login(site, reason);
  for verification codes call request_code(site, reason). Never ask for passwords.
- Post short progress updates with report(message). When finished, call
  report(message=<summary of what you found or did>, status="done", final=True).
  If you are stuck or need the user, call report(..., status="waiting_on_user", final=True).
Be efficient: act, don't narrate."""

VISION = ("For desktop apps use app_snapshot / app_click / app_type; screenshot() and "
          "click/type only as a last resort.")
NO_VISION = ("You cannot see images: rely on web_fetch, browser_snapshot / browser_text and "
             "app_snapshot.")
IMAGE_TOOLS = {"screenshot"}
KEEP_IMAGES = 2           # only the latest screenshots stay in context
OLD_RESULT_CHARS = 1500   # older tool results get trimmed to this


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def to_openai_tools(tools, vision: bool) -> list[dict]:
    out = []
    for t in tools:
        if not vision and t.name in IMAGE_TOOLS:
            continue
        out.append({"type": "function", "function": {
            "name": t.name, "description": (t.description or "")[:1000],
            "parameters": t.inputSchema or {"type": "object", "properties": {}}}})
    return out


def parse_text_calls(content: str, tools: list[dict]) -> list[dict]:
    """Some local models (not trained for tool use) write calls as text instead
    of structured tool calls. Recognise the common shapes:
      <tool_call>{"name": ..., "arguments": {...}}</tool_call>
      {"name": ..., "arguments": {...}}
      browser_navigate("https://x.com") / click(x=10, y=20)
    and turn them into OpenAI-style tool_calls."""
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    calls: list[tuple[str, dict]] = []
    for blob in re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.S) or [content.strip()]:
        try:
            obj = json.loads(blob.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
            for o in obj if isinstance(obj, list) else [obj]:
                name = o.get("name") or o.get("function", {}).get("name")
                args = o.get("arguments") or o.get("parameters") or o.get("function", {}).get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                if name in schemas:
                    calls.append((name, args))
        except (ValueError, AttributeError):
            pass
    if not calls:
        names = "|".join(map(re.escape, schemas))
        for m in re.finditer(rf"\b({names})\((.*?)\)\s*$", content, re.M):
            try:
                call = ast.parse(f"f({m.group(2)})", mode="eval").body
                props = list(schemas[m.group(1)].get("properties", {}))
                args = {props[i]: ast.literal_eval(a) for i, a in enumerate(call.args) if i < len(props)}
                args.update({k.arg: ast.literal_eval(k.value) for k in call.keywords})
                calls.append((m.group(1), args))
            except (SyntaxError, ValueError):
                pass
    return [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}} for n, a in calls]


def compact(messages: list[dict]) -> None:
    """Keep the context small: drop old screenshots, trim old tool results."""
    images = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], list)]
    for i in images[:-KEEP_IMAGES]:
        messages[i]["content"] = "(older screenshot removed)"
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    for i in tool_idx[:-4]:
        c = messages[i]["content"]
        if len(c) > OLD_RESULT_CHARS:
            messages[i]["content"] = c[:OLD_RESULT_CHARS] + "\n…(trimmed)"


async def chat(client: httpx.AsyncClient, base: str, model: str, messages, tools) -> dict:
    r = await client.post(f"{base}/chat/completions", json={
        "model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
        "temperature": 0.2, "stream": False}, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"model API returned {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]


class Tools:
    """One or more MCP servers (the desktop, plus "control" for the pilot),
    presented to the model as one tool list."""

    def __init__(self) -> None:
        self.owner: dict = {}
        self.listed: list = []

    async def add(self, stack: contextlib.AsyncExitStack, url: str, headers: dict) -> None:
        read, write, _ = await stack.enter_async_context(streamablehttp_client(url, headers=headers))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        for t in (await session.list_tools()).tools:
            if t.name not in self.owner:
                self.owner[t.name] = session
                self.listed.append(t)

    async def call_tool(self, name: str, args: dict):
        if name not in self.owner:
            raise RuntimeError(f"no tool named {name}")
        return await self.owner[name].call_tool(name, args)


async def run(task: str, model: str, base: str, mcp_url: str, vision: bool, max_steps: int,
              system: str = "") -> int:
    token = os.environ.get("HANGAR_AGENT_TOKEN")
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    me = os.environ.get("HANGAR_AGENT_NAME", "")
    async with contextlib.AsyncExitStack() as stack:
        mcp = Tools()
        await mcp.add(stack, mcp_url, {**auth, **({"X-Hangar-Agent": me} if me else {})})
        if os.environ.get("HANGAR_PILOT") == "1" and os.environ.get("HANGAR_DAEMON_URL"):
            await mcp.add(stack, os.environ["HANGAR_DAEMON_URL"] + "/pilot/mcp", auth)
        tools = to_openai_tools(mcp.listed, vision)
        vline = VISION if vision else NO_VISION
        messages: list[dict] = [
            {"role": "system", "content": (system + "\n- " + vline) if system else SYSTEM.format(vision_line=vline)},
            {"role": "user", "content": task},
        ]
        attach = os.environ.get("HANGAR_ATTACH", "")
        if attach and vision and os.path.exists(attach):
            import base64
            with open(attach, "rb") as f:
                url = "data:image/png;base64," + base64.b64encode(f.read()).decode()
            messages.append({"role": "user", "content": [{"type": "text", "text": "The attached screenshot:"},
                                                         {"type": "image_url", "image_url": {"url": url}}]})
        log(f"model={model} vision={vision} tools={len(tools)}")
        recent: list[str] = []   # signatures of recent calls, to catch loops
        async with httpx.AsyncClient() as http:
            for step in range(1, max_steps + 1):
                compact(messages)
                msg = await chat(http, base, model, messages, tools)
                calls = msg.get("tool_calls") or parse_text_calls(msg.get("content") or "", tools)
                messages.append({"role": "assistant", "content": msg.get("content") or "",
                                 **({"tool_calls": calls} if calls else {})})
                if msg.get("content"):
                    log(f"[{step}] says: {msg['content'][:300]}")
                if not calls:
                    # The model answered without tools: treat that as its final summary.
                    summary = (msg.get("content") or "Finished.").strip()
                    await mcp.call_tool("report", {"message": summary[:2000], "status": "done", "final": True})
                    log("done (no more tool calls)")
                    return 0
                looping = False
                for call in calls:
                    fn = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    sig = f"{fn}:{json.dumps(args, sort_keys=True)}"
                    recent = (recent + [sig])[-8:]
                    looping = looping or (fn != "report" and recent.count(sig) >= 3)
                    log(f"[{step}] {fn}({json.dumps(args)[:200]})")
                    images: list[str] = []
                    try:
                        res = await mcp.call_tool(fn, args)
                        parts = []
                        for c in res.content:
                            if c.type == "text":
                                parts.append(c.text)
                            elif c.type == "image":
                                images.append(f"data:{c.mimeType};base64,{c.data}")
                                parts.append("(screenshot attached in the next message)")
                        text = "\n".join(parts) or "ok"
                        if res.isError:
                            text = "ERROR: " + text
                    except Exception as e:  # tool failures go back to the model
                        text = f"ERROR: {e}"
                    messages.append({"role": "tool", "tool_call_id": call.get("id", fn), "content": text})
                    if images and vision:
                        messages.append({"role": "user", "content": [
                            {"type": "text", "text": f"Result of {fn}:"},
                            *({"type": "image_url", "image_url": {"url": u}} for u in images)]})
                    if fn == "report" and args.get("final"):
                        log("done (final report)")
                        return 0
                if looping:
                    log("loop detected; nudging the model")
                    messages.append({"role": "user", "content": (
                        "You are repeating the same actions. Stop and look at what you already have. "
                        "If you can answer the task, call report(message=<answer>, status=\"done\", "
                        "final=true) now. Otherwise try a different approach.")})
                    recent.clear()
            await mcp.call_tool("report", {"message": f"Stopped after {max_steps} steps without finishing.",
                                           "status": "error", "final": True})
            log("gave up: step limit")
            return 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", required=True)
    ap.add_argument("--system", default="", help="System prompt (the runner passes Hangar's)")
    ap.add_argument("--base-url", default=os.environ.get("HANGAR_LLM_URL", "http://hangar-host:1234/v1"))
    ap.add_argument("--mcp", default="http://127.0.0.1:8765/mcp")
    ap.add_argument("--no-vision", action="store_true", default=os.environ.get("HANGAR_MODEL_VISION") == "0")
    ap.add_argument("--max-steps", type=int, default=int(os.environ.get("HANGAR_MAX_STEPS", "40")))
    a = ap.parse_args()
    try:
        code = asyncio.run(run(a.task, a.model, a.base_url.rstrip("/"), a.mcp, not a.no_vision, a.max_steps,
                               a.system))
    except BaseException as e:  # noqa: BLE001 - one clear line for the user's report
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        if isinstance(e, httpx.TimeoutException):
            msg = f"The local model at {a.base_url} took too long to answer (is it too big for this machine's memory?)"
        elif isinstance(e, httpx.ConnectError):
            msg = f"Couldn't reach the local model server at {a.base_url}. Is LM Studio's server running?"
        else:
            msg = f"{type(e).__name__}: {e}"
        print(f"hangar-error: {msg}", flush=True)
        raise
    sys.exit(code)


if __name__ == "__main__":
    main()
