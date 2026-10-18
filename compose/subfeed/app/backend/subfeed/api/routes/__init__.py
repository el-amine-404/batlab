from .feeds import router as feeds
from .notifications import router as notifications
from .posts import router as posts
from .system import router as system

ROUTERS = (feeds, posts, notifications, system)
