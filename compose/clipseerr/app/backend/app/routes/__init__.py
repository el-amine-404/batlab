from ..auth import gate
from .catalog import router as catalog
from .hooks import router as hooks
from .hot import router as hot
from .integrations import credits_router, libraries_router, notifications_router, services_router
from .requests import router as requests
from .system import public as system_public
from .system import router as system
from .tags import router as tags

# Anyone may: whether clipseerr is up, signing in, and the apps' callbacks (they only wake the engine,
# which then asks the app itself: a forged call changes nothing).
PUBLIC = (system_public, gate.public, hooks)
# Who asks must be known (auth.py); each route says what more it needs. gate.router: one's own account,
# and the admins' people and keys.
ROUTERS = (catalog, hot, tags, requests, services_router, notifications_router, libraries_router, credits_router, system,
           gate.router)
