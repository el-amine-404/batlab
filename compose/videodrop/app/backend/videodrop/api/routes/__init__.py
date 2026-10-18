from .destinations import router as destinations
from .jobs import router as jobs
from .notifications import router as notifications
from .system import router as system

ROUTERS = (jobs, destinations, notifications, system)
