from ..auth import gate
from .activity import router as activity
from .lyrics import router as lyrics
from .notifications import router as notifications
from .songs import router as songs
from .sources import router as sources
from .system import public as system_public
from .system import router as system
from .workers import protocol as worker_protocol
from .workers import router as queue

# Anyone may: whether Stanzarr is up, and signing in.
PUBLIC = (system_public, gate.public)
# The GPU workers, by the workers' key (workers.py), not an account.
WORKERS = (worker_protocol,)
# Who asks must be known (api/auth.py); each route says what more it needs. gate.router: one's own
# account, and the admins' people and keys.
ROUTERS = (songs, lyrics, activity, queue, sources, notifications, system, gate.router)
