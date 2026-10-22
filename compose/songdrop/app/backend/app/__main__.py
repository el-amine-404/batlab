"""python -m app: the server, with one process (jobs live in its memory)."""

import logging

import uvicorn

from .config import load

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

uvicorn.run(
    "app.main:app",
    host="0.0.0.0",
    port=load().port,
    workers=1,
    proxy_headers=True,
    forwarded_allow_ips="*",
    server_header=False,
    # The page polls every couple of seconds; the access log would be only that.
    access_log=False,
)
