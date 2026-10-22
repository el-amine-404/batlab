"""Who may use songdrop, and what each may do: people signed in on the page
and apps with an API key (batlab_common/accounts.py, shared with videodrop,
clipseerr and subfeed). Here: songdrop's permissions, and the accounts' own
database (state_dir/accounts.db: songdrop keeps its jobs and settings in
files)."""

from pathlib import Path

from batlab_common.accounts import Accounts, Database, Principal  # noqa: F401 (the app's names for them)

# What one may do. admin holds them all.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "download": ("Ask for downloads", "Download links, check them first, and stop, retry or remove their own "
                                      "downloads."),
    "everyone": ("See everyone's downloads", "Every download, not only their own; with Ask for downloads, they can "
                                             "stop, retry and remove them too."),
    "settings": ("Change settings", "Settings, the output folder, cookies and connections."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
KEY_DEFAULT = ["download"]


def open_accounts(state_dir: Path) -> Accounts:
    return Accounts(Database(state_dir / "accounts.db"), "songdrop", PERMISSIONS, key_prefix="sd_")
