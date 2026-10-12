"""python -m stanzarr: serve the app."""

import logging

import uvicorn

from .app import create_app
from .config import Config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, proxy_headers=True, forwarded_allow_ips=cfg.trusted_proxies,
                log_level="warning")


if __name__ == "__main__":
    main()
