from ..auth import gate
from .feeds import router as feeds
from .notifications import router as notifications
from .posts import feeds as feed_formats
from .posts import router as posts
from .system import public as system_public
from .system import router as system

# Anyone may: whether subfeed is up, and signing in.
PUBLIC = (system_public, gate.public)
# Who asks must be known (api/auth.py); each route says what more it needs. gate.router: one's own
# account, and the admins' people and keys.
ROUTERS = (feeds, notifications, system, gate.router)
# Who asks must be known, by its own dependency: a key may be in the address (posts.py).
OWN_CHECK = (posts, feed_formats)
