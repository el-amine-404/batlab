"""Who may use clipseerr, and what each may do: people signed in on the page
and apps with an API key (batlab_common/accounts.py, shared with videodrop,
songdrop and subfeed). Here: clipseerr's permissions.

Watching (the catalog, Hot, tags, previews) needs no permission, only to be
someone: a person signed in, or a key."""

from batlab_common.accounts import Accounts, Principal  # noqa: F401 (the app's names for them)

# What one may do beyond watching. admin holds them all.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "request": ("Ask for songs and videos", "Ask for a video's song or the video itself, and retry, stop or remove "
                                            "their own requests."),
    "everyone": ("See everyone's requests", "Every request, not only their own; with Ask for songs and videos, they "
                                            "can retry, stop and remove them too."),
    "settings": ("Change settings", "Settings, services, notifications, libraries, and Hot's hand-picked videos."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
KEY_DEFAULT = ["request"]


def open_accounts(db) -> Accounts:
    """The accounts, in clipseerr's own database (db.py)."""
    return Accounts(db, "clipseerr", PERMISSIONS, key_prefix="cs_", package="app")
