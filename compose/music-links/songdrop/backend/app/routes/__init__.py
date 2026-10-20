"""The API (/api/v1), one module per area. Each module has a `router`; main.py
includes them. Routes only translate HTTP: the work is in the modules they
call. Errors are raised as exceptions and worded by main.py's handlers."""

from ..auth import gate
from . import connections, folders, jobs, links, settings, system

# Anyone may: whether songdrop is up, and signing in.
PUBLIC = [links.public, gate.public]
# Who asks must be known (auth.py); each route says what more it needs. gate.router: one's own account,
# and the admins' people and keys.
ROUTERS = [links.router, jobs.router, settings.router, folders.router, connections.router, system.router, gate.router]
