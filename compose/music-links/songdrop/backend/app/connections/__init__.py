"""Connections: where songdrop's events go (Settings, Connect). Each has a
type (TYPES), the events it wants, and its type's config."""

from .base import ConnectionType, Field, Invalid
from .discord import Discord
from .store import Connections, NotFound
from .webhook import Webhook

TYPES: dict[str, ConnectionType] = {kind.key: kind for kind in (Discord(), Webhook())}

__all__ = ["TYPES", "ConnectionType", "Connections", "Field", "Invalid", "NotFound"]
