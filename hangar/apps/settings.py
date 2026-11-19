"""Hangar's Settings app (the code lives in manage.py).  python -m hangar.apps.settings"""

from hangar.apps.manage import SettingsWindow

__all__ = ["SettingsWindow"]


def main() -> None:
    from hangar.apps import home
    home.open_view("settings")    # a view in Hangar's window


if __name__ == "__main__":
    main()
