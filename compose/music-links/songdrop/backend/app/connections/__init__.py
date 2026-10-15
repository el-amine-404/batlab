"""Connections: where songdrop's events go (Settings, Connect). Each has a
type (TYPES), the events it wants, and its type's config."""

from .apprise import Apprise
from .base import ConnectionType, Field, Invalid
from .discord import Discord
from .store import Connections, NotFound, Seed
from .webhook import Webhook

TYPES: dict[str, ConnectionType] = {kind.key: kind for kind in (Apprise(), Discord(), Webhook())}

__all__ = ["TYPES", "ConnectionType", "Connections", "Field", "Invalid", "NotFound", "Seed"]
