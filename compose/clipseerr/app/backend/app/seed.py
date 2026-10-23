"""The first start: services and a notification made from the environment
(compose/clipseerr/docker-compose.yml), once. After that they are the
Settings screens' to change; the environment is not read again."""

import logging

from . import credits
from .config import Config
from .db import Database
from .integrations import Integrations, Invalid

log = logging.getLogger("clipseerr.seed")
KEY = "seeded"
CREDITS_KEY = "seeded_credit_sources"
# Libraries came later: their own first start.
LIBRARIES_KEY = "seeded_libraries"
# videodrop replaced MeTube (2026-10-18): once, MeTube services become it.
VIDEODROP_KEY = "seeded_videodrop"


def run(db: Database, conf: Config, services: Integrations, notifications: Integrations,
        libraries: Integrations, credit_sources: Integrations | None = None) -> None:
    _libraries(db, conf, libraries)
    if credit_sources is not None:
        _credits(db, credit_sources)
    _first_start(db, conf, services, notifications)
    _videodrop(db, conf, services)
    _api_keys(conf, services)


def _credits(db: Database, store: Integrations) -> None:
    """The credits sources needing nothing set up, once (1.24); Discogs waits for its token."""
    if db.get(CREDITS_KEY) or store.items():
        db.put(CREDITS_KEY, True)
        return
    for key in credits.FIRST:
        store.seed(credits.TYPES[key].label, key, {})
    db.put(CREDITS_KEY, True)


def _first_start(db: Database, conf: Config, services: Integrations, notifications: Integrations) -> None:
    if db.get(KEY):
        return
    wanted = []
    if conf.aurral_url:
        wanted.append((services, "Aurral", "aurral", {"base_url": conf.aurral_url, "api_key": conf.aurral_api_key}))
    if conf.songdrop_url:
        wanted.append((services, "songdrop", "songdrop", {"base_url": conf.songdrop_url}))
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


def _videodrop(db: Database, conf: Config, services: Integrations) -> None:
    """videodrop for the videos: in place of a MeTube service (same place in
    the order, same history), else as a new one; once."""
    if db.get(VIDEODROP_KEY) or not conf.videodrop_url:
        return
    config = {"base_url": conf.videodrop_url, "destination": "music-videos"}
    replaced = False
    for item in services.items():
        if item["type"] == "metube":
            services.update(item["id"], {"name": "videodrop", "type": "videodrop", "enabled": item["enabled"],
                                         "config": config})
            log.info("service %s (MeTube) is videodrop now", item["name"])
            replaced = True
    if not replaced and not any(item["type"] == "videodrop" for item in services.items()):
        try:
            services.seed("videodrop", "videodrop", config)
            log.info("made the service videodrop from the environment")
        except Invalid as exc:
            log.warning("no service videodrop from the environment: %s", exc)
    db.put(VIDEODROP_KEY, True)


def _api_keys(conf: Config, services: Integrations) -> None:
    """Every start: a videodrop or songdrop service without an API key takes
    the environment's (they want one since they have accounts). One given on
    the page is never replaced."""
    keys = {"videodrop": conf.videodrop_api_key, "songdrop": conf.songdrop_api_key}
    for item in services.items():
        key = keys.get(item["type"])
        if not key or item["config"].get("api_key"):
            continue
        try:
            services.update(item["id"], {"name": item["name"], "type": item["type"], "enabled": item["enabled"],
                                         "events": item["events"] or None,
                                         "config": {**item["config"], "api_key": key}})
            log.info("service %s has %s's API key from the environment now", item["name"], item["type"])
        except Invalid as exc:
            log.warning("service %s: no API key from the environment: %s", item["name"], exc)


def _libraries(db: Database, conf: Config, libraries: Integrations) -> None:
    if db.get(LIBRARIES_KEY):
        return
    wanted = []
    if conf.navidrome_url and conf.navidrome_user:
        wanted.append(("Navidrome", "navidrome", {"base_url": conf.navidrome_url, "user": conf.navidrome_user,
                                                  "password": conf.navidrome_password, "token": conf.navidrome_token,
                                                  "salt": conf.navidrome_salt}))
    if conf.music_videos_dir:
        wanted.append(("Music videos", "folder", {"path": conf.music_videos_dir, "holds": "videos"}))
    for name, kind, config in wanted:
        try:
            libraries.seed(name, kind, config)
            log.info("made the library %s from the environment", name)
        except Invalid as exc:
            log.warning("no library %s from the environment: %s", name, exc)
    db.put(LIBRARIES_KEY, True)
