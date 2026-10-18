"""The one clipseerr: its database, catalog, services, notifications and the
engine following requests, made once at start and shared by the routes."""

import time

from . import config, events, libraries, notifications, outbound, services
from .cache import Cache
from .catalog.preview import Clips
from .catalog.service import Catalog
from .catalog.verdicts import Verdicts
from .db import Database
from .hotlist import HotList
from .integrations import Integrations
from .known import Known
from .settings import SettingsStore
from .tracker.engine import Engine
from .tracker.store import Requests

started_at = time.time()
conf = config.load()
db = Database(conf.database)
settings = SettingsStore(db, conf)
http = outbound.client()
cache = Cache(db)
catalog = Catalog(cache, http, settings)
# Newly judged videos join Hot: its cached lists are dropped, read again when asked.
catalog.verdicts = Verdicts(cache, on_judged=lambda: cache.forget("hot:"))
clips = Clips(conf.state_dir / "previews")
hotlist = HotList(db)
catalog.hotlist = hotlist
known = Known(db)
catalog.known = known
service_store = Integrations(db, "service", services.TYPES, describe_extra=services.describe_extra)
notification_store = Integrations(db, "notification", notifications.TYPES, events=events.NAMES)
notifier = notifications.Notifier(notification_store, http)
library_store = Integrations(db, "library", libraries.TYPES)
library = libraries.LibraryIndex(library_store, http)
requests = Requests(db)
engine = Engine(requests, service_store, notifier.emit, settings, http, f"{conf.internal_url}/api/v1",
                conf.public_url)
