"""The one clipseerr: its database, catalog, services, notifications and the
engine following requests, made once at start and shared by the routes."""

import time

from . import config, events, libraries, notifications, outbound, services
from .cache import Cache
from .catalog.service import Catalog
from .db import Database
from .hotlist import HotList
from .integrations import Integrations
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
hotlist = HotList(db)
catalog.hotlist = hotlist
service_store = Integrations(db, "service", services.TYPES, describe_extra=services.describe_extra)
notification_store = Integrations(db, "notification", notifications.TYPES, events=events.NAMES)
notifier = notifications.Notifier(notification_store, http)
library_store = Integrations(db, "library", libraries.TYPES)
library = libraries.LibraryIndex(library_store, http)
requests = Requests(db)
engine = Engine(requests, service_store, notifier.emit, settings, http, f"{conf.internal_url}/api/v1",
                conf.public_url)
