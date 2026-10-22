"""Who may use Stanzarr, and what each may do: people signed in on the page
and apps with an API key (batlab_common/accounts.py, shared with the other
apps). Here: Stanzarr's permissions.

Reading (the library, a song's lyrics, GET /api/v1/lyrics) needs no
permission, only to be someone: a person signed in, or a key. clipseerr's,
videodrop's and Homepage's keys may do nothing more. The GPU workers have a
key of their own (Settings, Workers), which only takes and returns AI jobs."""

from batlab_common.accounts import Accounts, Principal  # noqa: F401 (the app's names for them)

# What one may do beyond reading. admin holds them all.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "edit": ("Edit lyrics", "Fix timings and words in the editor, ask the sources again for a song."),
    "ai": ("Queue AI syncs", "Send songs to the GPU workers, cancel and retry their jobs."),
    "settings": ("Change settings", "Settings, sources, notifications, the workers' key; scan now."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
KEY_DEFAULT: list[str] = []


def open_accounts(db) -> Accounts:
    """The accounts, in Stanzarr's own database (db.py)."""
    return Accounts(db, "stanzarr", PERMISSIONS, key_prefix="sz_")
