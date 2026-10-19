"""videodrop's accounts from the server's shell, for whoever runs it (the
page does the rest):

    docker exec videodrop python -m videodrop.admin setup-code
    docker exec videodrop python -m videodrop.admin users
    docker exec -it videodrop python -m videodrop.admin reset-password <user name>
    docker exec videodrop python -m videodrop.admin add-key <name> [--can download,destinations]
    docker exec videodrop python -m videodrop.admin keys

reset-password asks for the new password (or makes one with --generate and
prints it); the person's sessions end. add-key prints the key, once.
"""

import argparse
import getpass
import secrets
import sys
import time

from .accounts import NAMES, Accounts
from .config import load
from .db import Database
from .domain import Conflict, Invalid, NotFound


def _when(at: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else "never"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m videodrop.admin", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-code", help="the code that makes the first admin, while nobody exists")
    commands.add_parser("users", help="the people, what they may do, when they last signed in")
    reset = commands.add_parser("reset-password", help="a new password for someone; their sessions end")
    reset.add_argument("username")
    reset.add_argument("--generate", action="store_true", help="make a random one and print it")
    key = commands.add_parser("add-key", help="an API key for an app, printed once")
    key.add_argument("name")
    key.add_argument("--can", default="download,destinations", help=f"of {', '.join(n for n in NAMES if n != 'admin')}"
                                                                    " (empty: reads only)")
    commands.add_parser("keys", help="the API keys")
    args = parser.parse_args(argv)

    db = Database(load().database)
    accounts = Accounts(db)
    try:
        if args.command == "setup-code":
            code = accounts.setup_code()
            print(code if code else "videodrop is set up already: sign in, or reset-password.")
        elif args.command == "users":
            for user in accounts.users():
                state = "" if user["enabled"] else " (turned off)"
                print(f"{user['username']:<20} {user['name']:<24} {', '.join(user['permissions']) or 'nothing':<44} "
                      f"last signed in {_when(user['last_login_at'])}{state}")
        elif args.command == "reset-password":
            found = next((u for u in accounts.users() if u["username"].casefold() == args.username.casefold()), None)
            if not found:
                raise NotFound(f"Nobody is called {args.username!r}.")
            if args.generate:
                password = secrets.token_urlsafe(12)
            else:
                password = getpass.getpass("New password: ")
                if password != getpass.getpass("Again: "):
                    raise Invalid("The two passwords differ.")
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
    except (Invalid, Conflict, NotFound) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
