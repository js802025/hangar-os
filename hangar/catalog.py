"""Local models the setup wizard suggests.

Keys are LM Studio catalog names (what `lms get` takes). Capabilities and
minimum memory come from each model's page on lmstudio.ai (checked
2026-11-18). Agents need tool use; vision lets them use screenshots for
desktop (computer-use) work. Edit freely: it's just a list.
"""

from __future__ import annotations

MODELS = [
    {"key": "qwen/qwen3-vl-4b", "name": "Qwen3-VL 4B", "params": "4B", "min_gb": 3, "vision": True, "tools": True,
     "reasoning": False, "blurb": "Tiny and quick. Fine for simple browsing on small machines."},
    {"key": "qwen/qwen3-vl-8b", "name": "Qwen3-VL 8B", "params": "8B", "min_gb": 6, "vision": True, "tools": True,
     "reasoning": False, "blurb": "Solid all-rounder that can see the screen."},
    {"key": "google/gemma-4-12b", "name": "Gemma 4 12B", "params": "12B", "min_gb": 7, "vision": True, "tools": True,
     "reasoning": True, "blurb": "Sees the screen and thinks things through. Great value for its size."},
    {"key": "qwen/qwen3.5-9b", "name": "Qwen3.5 9B", "params": "9B", "min_gb": 7, "vision": True, "tools": True,
     "reasoning": True, "blurb": "Newest small Qwen: vision, tools and reasoning."},
    {"key": "openai/gpt-oss-20b", "name": "gpt-oss 20B", "params": "20B", "min_gb": 12, "vision": False, "tools": True,
     "reasoning": True, "blurb": "Strong planner, text only: works through the browser's text tools."},
    {"key": "google/gemma-4-26b-a4b", "name": "Gemma 4 26B A4B", "params": "26B (4B active)", "min_gb": 16,
     "vision": True, "tools": True, "reasoning": True,
     "blurb": "Big-model quality at small-model speed (mixture of experts)."},
    {"key": "qwen/qwen3-vl-30b", "name": "Qwen3-VL 30B A3B", "params": "30B (3B active)", "min_gb": 18,
     "vision": True, "tools": True, "reasoning": False, "blurb": "Excellent at reading screens; fast for its size."},
    {"key": "openai/gpt-oss-120b", "name": "gpt-oss 120B", "params": "120B", "min_gb": 65, "vision": False,
     "tools": True, "reasoning": True, "blurb": "For big workstations. Text only."},
]


def recommend(budget_gb: float, downloaded: list[str] | None = None) -> list[dict]:
    """The catalog with fit, speed and a few picks marked as recommended:
    the best vision model that fits, a lighter vision model, and the best
    text model that fits."""
    downloaded = [d.lower() for d in downloaded or []]
    out = []
    for m in MODELS:
        short = m["key"].split("/")[-1]
        out.append({**m, "fits": m["min_gb"] <= budget_gb, "tight": budget_gb * 0.8 < m["min_gb"] <= budget_gb,
                    "downloaded": any(short in d for d in downloaded), "recommended": False})
    fitting = [m for m in out if m["fits"]]
    vision = sorted((m for m in fitting if m["vision"] and not m["tight"]), key=lambda m: m["min_gb"])
    if not vision:
        vision = sorted((m for m in fitting if m["vision"]), key=lambda m: m["min_gb"])
    picks = []
    if vision:
        picks.append(vision[-1])                         # most capable vision model that fits
        if len(vision) > 1 and vision[0]["min_gb"] <= vision[-1]["min_gb"] / 2:
            picks.append(vision[0])                      # plus a light, fast one
    text = sorted((m for m in fitting if not m["vision"] and not m["tight"]), key=lambda m: m["min_gb"])
    if text:
        picks.append(text[-1])
    for m in picks:
        m["recommended"] = True
    return sorted(out, key=lambda m: (not m["recommended"], not m["fits"], m["min_gb"]))
