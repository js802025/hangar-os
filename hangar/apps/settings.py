"""Hangar's Settings app (the code lives in manage.py).  python -m hangar.apps.settings"""

from hangar.apps import common
from hangar.apps.manage import SettingsWindow

__all__ = ["SettingsWindow"]


def main() -> None:
    common.run("settings", SettingsWindow)


if __name__ == "__main__":
    main()
