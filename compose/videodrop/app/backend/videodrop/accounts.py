"""Who may use videodrop, and what each may do: people signed in on the page
and apps with an API key (batlab_common/accounts.py, shared with songdrop,
clipseerr and subfeed). Here: videodrop's permissions."""

from batlab_common.accounts import Accounts, Principal  # noqa: F401 (the app's names for them)

# What one may do. admin holds them all.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "download": ("Ask for downloads", "Download links, check them first, and stop, retry or remove their own "
                                      "downloads."),
    "destinations": ("Save into destinations", "Send downloads to the destinations' folders; without it, files are "
                                               "only kept here to download from the page."),
    "everyone": ("See everyone's downloads", "Every download, not only their own; with Ask for downloads, they can "
                                             "stop, retry and remove them too."),
    "settings": ("Change settings", "Settings, destinations, notifications, cookies, Issues and videodrop's log."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
NAMES = tuple(PERMISSIONS)
KEY_DEFAULT = ["download", "destinations"]


def open_accounts(db) -> Accounts:
    """The accounts, in videodrop's own database (db.py)."""
    return Accounts(db, "videodrop", PERMISSIONS, key_prefix="vd_")
