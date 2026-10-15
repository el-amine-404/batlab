"""The one songdrop: its settings, connections and jobs, made once at start
and shared by the routes (routes/)."""

from . import config, preferences
from .connections import TYPES, Connections, Seed
from .jobs import JobManager

base_settings = config.load()
_saved = preferences.stored(base_settings.state_dir)
_discord = _saved.get("discord_webhook") or base_settings.discord_webhook
if base_settings.apprise_url:
    _seed = Seed("apprise", "Apprise", {"url": base_settings.apprise_url, "tag": base_settings.apprise_tag or ""})
elif _discord:
    _seed = Seed("discord", "Discord", {"webhook_url": _discord})
else:
    _seed = None
connections = Connections(base_settings.state_dir, TYPES, seed=_seed)
manager = JobManager(preferences.apply(base_settings, _saved), emit=connections.emit)
