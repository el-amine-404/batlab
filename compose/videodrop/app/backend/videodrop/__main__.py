"""python -m videodrop: the server and its scheduler, in one process; each
download runs in a process of its own (runner.py)."""

import uvicorn

from . import logs
from .config import load

logs.setup()

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
    # Its records go to the root logger (logs.setup): cleaned, and kept for the page.
    log_config=None,
)
