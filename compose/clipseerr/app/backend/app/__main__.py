"""python -m app: the server, in one process (the engine lives in its memory)."""

import logging

import uvicorn

from .config import load

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# One line per outgoing request is noise; failures are logged where they matter.
logging.getLogger("httpx").setLevel(logging.WARNING)

uvicorn.run(
    "app.main:app",
    host="0.0.0.0",
    port=load().port,
    workers=1,
    loop="uvloop",
    http="httptools",
    proxy_headers=True,
    forwarded_allow_ips="*",
    server_header=False,
    # The page polls the requests while some are under way; the access log would be only that.
    access_log=False,
)
