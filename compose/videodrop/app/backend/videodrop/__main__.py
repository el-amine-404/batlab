"""python -m videodrop: the server and its scheduler, in one process; each
download runs in a process of its own (runner.py)."""

import logging

import uvicorn

from .config import load

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# One line per outgoing request is noise; failures are logged where they matter.
logging.getLogger("httpx").setLevel(logging.WARNING)

uvicorn.run(
    "videodrop.api.app:create_default",
    factory=True,
    host="0.0.0.0",
    port=load().port,
    workers=1,
    loop="uvloop",
    http="httptools",
    proxy_headers=True,
    forwarded_allow_ips="*",
    server_header=False,
    # Callers poll and pages stream; the access log would be mostly that.
    access_log=False,
)
