from .catalog import router as catalog
from .hooks import router as hooks
from .hot import router as hot
from .integrations import libraries_router, notifications_router, services_router
from .requests import router as requests
from .system import router as system
from .tags import router as tags

ROUTERS = (catalog, hot, tags, requests, services_router, notifications_router, libraries_router, system, hooks)
