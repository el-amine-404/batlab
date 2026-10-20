from ..auth import gate
from .destinations import router as destinations
from .jobs import router as jobs
from .links import router as links
from .notifications import router as notifications
from .sites import router as sites
from .system import public as system_public
from .system import router as system

# Anyone may: whether videodrop is up, and signing in.
PUBLIC = (system_public, gate.public)
# Who asks must be known (api/auth.py); each route says what more it needs. gate.router: one's own
# account, and the admins' people and keys.
ROUTERS = (jobs, links, sites, destinations, notifications, system, gate.router)
