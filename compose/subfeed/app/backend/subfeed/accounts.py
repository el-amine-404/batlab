"""Who may use subfeed, and what each may do: people signed in on the page
and apps with an API key (batlab_common/accounts.py, shared with videodrop,
songdrop and clipseerr). Here: subfeed's permissions.

Reading (the feeds, their posts, JSON Feed and Atom) needs no permission,
only to be someone: a person signed in, or a key. clipseerr's and Homepage's
keys may do nothing more. A feed reader that can't send a header puts its
key in the feed's address (?key=…)."""

from batlab_common.accounts import Accounts, Principal  # noqa: F401 (the app's names for them)

# What one may do beyond reading. admin holds them all.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "feeds": ("Manage feeds", "Add feeds, change them (what they read, how often), read them now, remove them."),
    "settings": ("Change settings", "Settings and notifications."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
KEY_DEFAULT: list[str] = []


def open_accounts(db) -> Accounts:
    """The accounts, in subfeed's own database (db.py)."""
    return Accounts(db, "subfeed", PERMISSIONS, key_prefix="sf_")
