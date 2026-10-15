"""The API (/api/v1), one module per area. Each module has a `router`; main.py
includes them. Routes only translate HTTP: the work is in the modules they
call. Errors are raised as exceptions and worded by main.py's handlers."""

from . import connections, folders, jobs, links, settings, system

ROUTERS = [links.router, jobs.router, settings.router, folders.router, connections.router, system.router]
