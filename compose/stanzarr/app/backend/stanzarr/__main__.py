"""python -m stanzarr: the server and its background work, in one process."""

import logging

import uvicorn

from .config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

uvicorn.run(
    "stanzarr.api.app:create_default",
    factory=True,
    host="0.0.0.0",
    port=Config().port,
    workers=1,
    proxy_headers=True,
    forwarded_allow_ips="*",
    server_header=False,
    # The page polls the queue; the access log would be only that.
    access_log=False,
)
