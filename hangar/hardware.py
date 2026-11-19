"""What this machine can run: CPU, RAM, GPU/VRAM, and a memory budget for
local models (what's left after the OS, apps and the agents themselves)."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

AGENT_RESERVE_GB = 6       # a few agent containers (each capped at 4 GB, most idle)
OS_RESERVE_GB = 6          # desktop, browser, the daemon


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _docker_vm_gb() -> float:
    """On a Mac, Docker Desktop's VM holds its memory whether or not agents use it."""
    if os.environ.get("HANGAR_RUNTIME") == "apple":     # Apple's container: a small VM per agent, no shared one
        return 0.0
    out = _run("docker", "info", "--format", "{{.MemTotal}}") if shutil.which("docker") else ""
    try:
        return int(out) / 2**30
    except ValueError:
        return 0.0


def detect() -> dict:
    system = platform.system()
    info: dict = {"platform": system, "arch": platform.machine(), "cores": os.cpu_count() or 1,
                  "cpu": "", "ram_gb": 0.0, "gpus": [], "unified": False}
    if system == "Darwin":
        info["cpu"] = _run("sysctl", "-n", "machdep.cpu.brand_string")
        info["ram_gb"] = int(_run("sysctl", "-n", "hw.memsize") or 0) / 2**30
        if info["arch"] == "arm64":
            info["unified"] = True
            info["gpus"] = [{"name": f"{info['cpu']} GPU", "vram_gb": None, "kind": "apple"}]
    else:
        cpuinfo = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
        m = re.search(r"model name\s*:\s*(.+)", cpuinfo)
        info["cpu"] = m.group(1).strip() if m else platform.processor()
        mem = Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""
        m = re.search(r"MemTotal:\s*(\d+) kB", mem)
        info["ram_gb"] = int(m.group(1)) / 2**20 if m else 0.0
        info["gpus"] = _linux_gpus()
    info["ram_gb"] = round(info["ram_gb"], 1)
    info.update(budget(info))
    return info


def _linux_gpus() -> list[dict]:
    gpus = []
    if shutil.which("nvidia-smi"):
        for line in _run("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits").splitlines():
            name, _, mib = line.rpartition(",")
            try:
                gpus.append({"name": name.strip(), "vram_gb": round(int(mib) / 1024, 1), "kind": "nvidia"})
            except ValueError:
                pass
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        vram = card / "device/mem_info_vram_total"          # AMD (amdgpu)
        if vram.exists():
            try:
                gb = int(vram.read_text()) / 2**30
            except (OSError, ValueError):
                continue
            if gb >= 1:
                gpus.append({"name": "AMD Radeon GPU", "vram_gb": round(gb, 1), "kind": "amd"})
    if not gpus and shutil.which("lspci"):
        for line in _run("lspci").splitlines():
            if re.search(r"VGA|3D controller", line):
                gpus.append({"name": line.split(": ", 1)[-1], "vram_gb": None, "kind": "integrated"})
    return gpus


def budget(info: dict) -> dict:
    """How much memory a local model can use, and how fast it'll be."""
    ram = info["ram_gb"]
    if info["unified"]:
        # Apple Silicon: one pool for CPU and GPU. macOS + apps + the Docker VM come first.
        vm = _docker_vm_gb() or 8
        gb = ram - OS_RESERVE_GB - max(vm, AGENT_RESERVE_GB)
        return {"model_budget_gb": round(max(gb, 0), 1), "accel": "gpu",
                "note": f"{ram:.0f} GB unified memory, minus macOS, your apps and Docker ({vm:.0f} GB)."}
    vram = max((g["vram_gb"] or 0 for g in info["gpus"] if g["kind"] in ("nvidia", "amd")), default=0)
    if vram >= 4:
        return {"model_budget_gb": round(vram - 0.5, 1), "accel": "gpu",
                "note": f"Models that fit in the GPU's {vram:.0f} GB run fast. Bigger ones spill into RAM and slow down."}
    gb = (ram - OS_RESERVE_GB - AGENT_RESERVE_GB) * 0.7
    return {"model_budget_gb": round(max(gb, 0), 1), "accel": "cpu",
            "note": "No dedicated GPU found, so models run on the CPU: pick small ones, and expect slow answers."}


def summary(info: dict) -> str:
    gpu = ", ".join(g["name"] + (f" ({g['vram_gb']:.0f} GB)" if g["vram_gb"] else "") for g in info["gpus"]
                    if g["kind"] != "apple") or ("Apple GPU (shares memory)" if info["unified"] else "no dedicated GPU")
    return f"{info['cpu'] or info['arch']} · {info['cores']} cores · {info['ram_gb']:.0f} GB RAM · {gpu}"
