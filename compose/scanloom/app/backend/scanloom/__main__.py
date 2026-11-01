import sys

import uvicorn

from .config import load
from .db import Database
from .logs import setup as setup_logs


def main():
    config = load()
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        from .processor import loop

        setup_logs(component="worker", logs_dir=config.logs)
        database = Database(config.database_url, size=4)
        database.migrate()
        loop(config, database)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "notifier":
        from .notifier import loop

        setup_logs(component="notifier", logs_dir=None)
        database = Database(config.database_url, size=3, idle=1)
        database.migrate()
        loop(config, database)
        return
    setup_logs(component="api", logs_dir=config.logs)
    uvicorn.run("scanloom.api:create_app", factory=True, host="0.0.0.0", port=config.port,
                workers=1, loop="uvloop", http="httptools", proxy_headers=True,
                forwarded_allow_ips="*", server_header=False, access_log=False,
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
