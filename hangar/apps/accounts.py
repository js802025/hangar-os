"""Hangar's Accounts app (the code lives in manage.py).  python -m hangar.apps.accounts"""

from hangar.apps import common
from hangar.apps.manage import AccountsWindow

__all__ = ["AccountsWindow"]


def main() -> None:
    common.run("accounts", AccountsWindow)


if __name__ == "__main__":
    main()
