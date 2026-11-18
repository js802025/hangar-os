"""Hangar's Models app (the code lives in manage.py).  python -m hangar.apps.models"""

from hangar.apps import common
from hangar.apps.manage import ModelsWindow

__all__ = ["ModelsWindow"]


def main() -> None:
    common.run("models", ModelsWindow)


if __name__ == "__main__":
    main()
