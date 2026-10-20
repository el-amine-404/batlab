"""songdrop's accounts from the server's shell (batlab_common/admin.py):

    docker exec songdrop admin setup-code
    docker exec songdrop admin users
    docker exec -it songdrop admin reset-password <user name>
    docker exec songdrop admin add-key <name> [--can download]
    docker exec songdrop admin keys
    docker exec songdrop admin remove-key <name>
"""

import sys

from batlab_common.admin import run

from .accounts import KEY_DEFAULT, open_accounts
from .config import load


def main(argv: list[str] | None = None) -> int:
    return run(open_accounts(load().state_dir), KEY_DEFAULT, argv)


if __name__ == "__main__":
    sys.exit(main())
