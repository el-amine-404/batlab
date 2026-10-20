"""videodrop's accounts from the server's shell (batlab_common/admin.py):

    docker exec videodrop python -m videodrop.admin setup-code
    docker exec videodrop python -m videodrop.admin users
    docker exec -it videodrop python -m videodrop.admin reset-password <user name>
    docker exec videodrop python -m videodrop.admin add-key <name> [--can download,destinations]
    docker exec videodrop python -m videodrop.admin keys
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
