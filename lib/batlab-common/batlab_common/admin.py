"""An app's accounts (accounts.py) from the server's shell, for whoever runs
it (the page does the rest). Each app's own `python -m <app>.admin` opens its
accounts and calls run():

    docker exec <app> python -m <app>.admin setup-code
    docker exec <app> python -m <app>.admin users
    docker exec -it <app> python -m <app>.admin reset-password <user name>
    docker exec <app> python -m <app>.admin add-key <name> [--can download]
    docker exec <app> python -m <app>.admin keys
    docker exec <app> python -m <app>.admin remove-key <name>

reset-password asks for the new password (or makes one with --generate and
prints it); the person's sessions end. add-key prints the key, once.
"""

import argparse
import getpass
import secrets
import sys
import time

from .accounts import Accounts, Refusal


def _when(at: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "never"


def run(accounts: Accounts, key_default: list[str], argv: list[str] | None = None) -> int:
    app = accounts.app
    prog = accounts.setup_command.removeprefix(f"docker exec {app} ").removesuffix(" setup-code")
    parser = argparse.ArgumentParser(prog=prog, description=f"{app}'s people and API keys.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-code", help="the code that makes the first admin, while nobody exists")
    commands.add_parser("users", help="the people, what they may do, when they last signed in")
    reset = commands.add_parser("reset-password", help="a new password for someone; their sessions end")
    reset.add_argument("username")
    reset.add_argument("--generate", action="store_true", help="make a random one and print it")
    key = commands.add_parser("add-key", help="an API key for an app, printed once")
    key.add_argument("name")
    key.add_argument("--can", default=",".join(key_default),
                     help=f"of {', '.join(n for n in accounts.names if n != 'admin')} (empty: reads only)")
    commands.add_parser("keys", help="the API keys")
    remove = commands.add_parser("remove-key", help="an API key removed: the app using it is refused from now on")
    remove.add_argument("name")
    args = parser.parse_args(argv)

    try:
        if args.command == "setup-code":
            code = accounts.setup_code()
            print(f"{code}\n(12 letters and digits; the dashes are only for reading)" if code
                  else f"{app} is set up already: sign in, or reset-password.")
        elif args.command == "users":
            for user in accounts.users():
                state = "" if user["enabled"] else " (turned off)"
                print(f"{user['username']:<20} {user['name']:<24} {', '.join(user['permissions']) or 'nothing':<44} "
                      f"last signed in {_when(user['last_login_at'])}{state}")
        elif args.command == "reset-password":
            found = next((u for u in accounts.users() if u["username"].casefold() == args.username.casefold()), None)
            if not found:
                print(f"Nobody is called {args.username!r}.", file=sys.stderr)
                return 1
            if args.generate:
                password = secrets.token_urlsafe(12)
            else:
                password = getpass.getpass("New password: ")
                if password != getpass.getpass("Again: "):
                    print("The two passwords differ.", file=sys.stderr)
                    return 1
            accounts.set_password(found["id"], password)
            print(f"{found['username']}'s password is changed; their sessions ended."
                  + (f"\nThe new password: {password}" if args.generate else ""))
        elif args.command == "add-key":
            permissions = [p.strip() for p in args.can.split(",") if p.strip()]
            token, made = accounts.create_key(args.name, permissions, None)
            print(f"API key {made['name']} ({', '.join(made['permissions']) or 'reads only'}):\n{token}\n"
                  "It is shown this once: the app sends it as X-Api-Key.")
        elif args.command == "keys":
            for item in accounts.keys():
                print(f"{item['name']:<20} {item['prefix']}…  {', '.join(item['permissions']) or 'reads only':<30} "
                      f"last used {_when(item['last_used_at'])}")
        elif args.command == "remove-key":
            found = next((k for k in accounts.keys() if k["name"].casefold() == args.name.casefold()), None)
            if not found:
                print(f"No key is called {args.name!r}.", file=sys.stderr)
                return 1
            accounts.delete_key(found["id"])
            print(f"API key {found['name']} removed: the app using it is refused from now on.")
    except Refusal as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
