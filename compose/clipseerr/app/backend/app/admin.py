"""clipseerr's accounts from the server's shell (batlab_common/admin.py):

    docker exec clipseerr admin setup-code
    docker exec clipseerr admin users
    docker exec -it clipseerr admin reset-password <user name>
    docker exec clipseerr admin add-key <name> [--can request]
    docker exec clipseerr admin keys
    docker exec clipseerr admin remove-key <name>
"""

import sys

from batlab_common.admin import run

from .accounts import KEY_DEFAULT, open_accounts
from .config import load
from .db import Database


def main(argv: list[str] | None = None) -> int:
    db = Database(load().database)
    try:
        return run(open_accounts(db), KEY_DEFAULT, argv)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
