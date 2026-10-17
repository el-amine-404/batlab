"""python -m clipfeed: the server and its poller, in one process."""

import logging

import uvicorn

from .config import load

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

uvicorn.run(
    "clipfeed.api:app",
    host="0.0.0.0",
    port=load().port,
    workers=1,
    loop="uvloop",
    http="httptools",
    proxy_headers=True,
    forwarded_allow_ips="*",
    server_header=False,
    access_log=False,
)
