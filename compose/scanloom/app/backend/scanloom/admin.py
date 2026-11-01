"""Scanloom's accounts from the server's shell (batlab_common/admin.py):

    docker exec scanloom-api admin setup-code
    docker exec scanloom-api admin users
    docker exec -it scanloom-api admin reset-password <user name>
    docker exec scanloom-api admin add-key <name> [--can scan]
"""

import sys

from batlab_common.admin import run

from .accounts import KEY_DEFAULT, open_accounts
from .config import load


def main(argv: list[str] | None = None) -> int:
    return run(open_accounts(load().state), KEY_DEFAULT, argv)


if __name__ == "__main__":
    sys.exit(main())
