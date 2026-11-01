"""Who may use Scanloom, and what each may do: people signed in on the page and
apps with an API key (batlab_common/accounts.py, shared with the other apps).
The accounts' own SQLite database (state/accounts.db): the scans are in PostgreSQL."""

from pathlib import Path

from batlab_common.accounts import Accounts, Database

PERMISSIONS: dict[str, tuple[str, str]] = {
    "scan": ("Make scans", "Make scans, and read, change and delete their own."),
    "everyone": ("See everyone's scans", "Every scan, not only their own; with Make scans, they can change and delete them too."),
    "settings": ("Change settings", "Limits, retention and the activity log."),
    "admin": ("Admin", "Everything, and the people and API keys."),
}
KEY_DEFAULT = ["scan"]


def open_accounts(state_dir: Path) -> Accounts:
    accounts = Accounts(Database(state_dir / "accounts.db"), "scanloom", PERMISSIONS, key_prefix="sl_")
    # The server is in scanloom-api; the container called scanloom only serves the page.
    accounts.setup_command = "docker exec scanloom-api admin setup-code"
    return accounts
