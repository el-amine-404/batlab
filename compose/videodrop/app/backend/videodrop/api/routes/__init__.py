from .destinations import router as destinations
from .jobs import router as jobs
from .links import router as links
from .notifications import router as notifications
from .sites import router as sites
from .system import router as system

ROUTERS = (jobs, links, sites, destinations, notifications, system)
