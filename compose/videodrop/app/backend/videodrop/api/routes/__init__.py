from .auth import public as auth_public
from .auth import router as auth
from .destinations import router as destinations
from .jobs import router as jobs
from .links import router as links
from .notifications import router as notifications
from .people import router as people
from .sites import router as sites
from .system import public as system_public
from .system import router as system

# Anyone may: whether videodrop is up, and signing in.
PUBLIC = (system_public, auth_public)
# Who asks must be known (api/auth.py); each route says what more it needs.
ROUTERS = (jobs, links, sites, destinations, notifications, system, auth, people)
