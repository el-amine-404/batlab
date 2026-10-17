"""The first start: services and a notification made from the environment
(compose/clipseerr/docker-compose.yml), once. After that they are the
Settings screens' to change; the environment is not read again."""

import logging

from .config import Config
from .db import Database
from .integrations import Integrations, Invalid

log = logging.getLogger("clipseerr.seed")
KEY = "seeded"


def run(db: Database, conf: Config, services: Integrations, notifications: Integrations) -> None:
    if db.get(KEY):
        return
    wanted = []
    if conf.aurral_url:
        wanted.append((services, "Aurral", "aurral", {"base_url": conf.aurral_url, "api_key": conf.aurral_api_key}))
    if conf.songdrop_url:
        wanted.append((services, "songdrop", "songdrop", {"base_url": conf.songdrop_url}))
    if conf.metube_url:
        wanted.append((services, "MeTube (videos)", "metube", {"base_url": conf.metube_url, "download": "video"}))
    if conf.apprise_url:
        wanted.append((notifications, "Discord #" + (conf.apprise_tag or "all"), "apprise",
                       {"url": conf.apprise_url, "tag": conf.apprise_tag}))
    for store, name, kind, config in wanted:
        try:
            store.seed(name, kind, config)
            log.info("made the %s %s from the environment", store.family, name)
        except Invalid as exc:
            log.warning("no %s %s from the environment: %s", store.family, name, exc)
    db.put(KEY, True)
