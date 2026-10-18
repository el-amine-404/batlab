from .destinations import router as destinations
from .jobs import router as jobs
from .links import router as links
from .notifications import router as notifications
from .system import router as system

ROUTERS = (jobs, links, destinations, notifications, system)
