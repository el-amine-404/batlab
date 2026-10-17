from .catalog import router as catalog
from .hooks import router as hooks
from .integrations import notifications_router, services_router
from .requests import router as requests
from .system import router as system

ROUTERS = (catalog, requests, services_router, notifications_router, system, hooks)
