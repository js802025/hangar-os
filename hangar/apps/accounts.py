"""Hangar's Accounts app (the code lives in manage.py).  python -m hangar.apps.accounts"""

from hangar.apps.manage import AccountsWindow

__all__ = ["AccountsWindow"]


def main() -> None:
    from hangar.apps import home
    home.open_view("settings/accounts")    # a view in Hangar's window


if __name__ == "__main__":
    main()
