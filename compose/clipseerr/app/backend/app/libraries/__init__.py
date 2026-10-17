"""Libraries: where songs and music videos already are (Settings, Libraries)."""

from .base import LibraryType, Snapshot, song_key
from .folder import Folder
from .index import LibraryIndex
from .navidrome import Navidrome

TYPES: dict[str, LibraryType] = {kind.key: kind for kind in (Navidrome(), Folder())}

__all__ = ["TYPES", "LibraryIndex", "LibraryType", "Snapshot", "song_key"]
