import sys

import uvicorn

from .config import load
from .db import Database
from .processor import loop


def main():
    config = load()
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        database = Database(config.database_url)
        database.migrate()
        loop(config, database)
        return
    uvicorn.run("scanloom.api:create_app", factory=True, host="0.0.0.0", port=config.port,
                workers=1, loop="uvloop", http="httptools", proxy_headers=True,
                forwarded_allow_ips="*", server_header=False, access_log=False)


if __name__ == "__main__":
    main()
