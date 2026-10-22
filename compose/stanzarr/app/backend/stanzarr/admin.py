"""Stanzarr's accounts from the server's shell (batlab_common/admin.py):

    docker exec stanzarr admin setup-code
    docker exec stanzarr admin users
    docker exec -it stanzarr admin reset-password <user name>
    docker exec stanzarr admin add-key <name> [--can edit,ai]
    docker exec stanzarr admin keys
    docker exec stanzarr admin remove-key <name>
"""

import sys

from batlab_common.admin import run

from .accounts import KEY_DEFAULT, open_accounts
from .config import Config
from .db import Database


def main(argv: list[str] | None = None) -> int:
    config = Config()
    config.ensure_dirs()
    db = Database(config.db_path)
    try:
        return run(open_accounts(db), KEY_DEFAULT, argv)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
