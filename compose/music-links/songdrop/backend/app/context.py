"""The one songdrop: its settings, connections and jobs, made once at start
and shared by the routes (routes/)."""

from . import config, preferences
from .connections import TYPES, Connections
from .jobs import JobManager

base_settings = config.load()
_saved = preferences.stored(base_settings.state_dir)
connections = Connections(base_settings.state_dir, TYPES,
                          seed_discord=_saved.get("discord_webhook") or base_settings.discord_webhook)
manager = JobManager(preferences.apply(base_settings, _saved), emit=connections.emit)
