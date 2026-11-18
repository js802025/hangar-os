"""`hangar-app <name>`: start one of Hangar's native apps.

  hangar-app spaces [--overview] | passwords | accounts | models | settings | setup [--first-boot]
"""

from __future__ import annotations

import importlib
import sys

APPS = ["spaces", "passwords", "accounts", "models", "settings", "setup"]


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "spaces"
    if name not in APPS:
        sys.exit(f"usage: hangar-app [{' | '.join(APPS)}] [--overview]")
    try:
        mod = importlib.import_module(f"hangar.apps.{name}")
    except ImportError as e:
        sys.exit(f"The native apps need PySide6 ({e}). Install with: uv sync --extra apps")
    mod.main()


if __name__ == "__main__":
    main()
