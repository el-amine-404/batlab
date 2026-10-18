"""python -m subfeed: the server and its poller, in one process."""

import logging

import uvicorn

from .config import load

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# One line per outgoing request is noise; failures are logged where they matter.
logging.getLogger("httpx").setLevel(logging.WARNING)

uvicorn.run(
    "subfeed.api.app:create_default",
    factory=True,
    host="0.0.0.0",
    port=load().port,
    workers=1,
    loop="uvloop",
    http="httptools",
    proxy_headers=True,
    forwarded_allow_ips="*",
    server_header=False,
    # Feed readers poll; the access log would be only that.
    access_log=False,
)
