"""Hangar's Models app (the code lives in manage.py).  python -m hangar.apps.models"""

from hangar.apps.manage import ModelsWindow

__all__ = ["ModelsWindow"]


def main() -> None:
    from hangar.apps import home
    home.open_view("settings/models")    # a view in Hangar's window


if __name__ == "__main__":
    main()
