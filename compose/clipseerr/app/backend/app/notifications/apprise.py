"""Apprise (compose/apprise): one address for every service batlab can tell,
Discord, Telegram, ntfy, e-mail…, chosen in Apprise's configuration by the
message's tag. The way batlab's own services send (batlab_common.notify)."""

import asyncio
import urllib.parse

from batlab_common import notify

from .. import outbound
from ..events import Event
from ..integrations import Field, Invalid
from .base import NotificationType


class Apprise(NotificationType):
    key = "apprise"
    label = "Apprise"
    description = "Sends through Apprise to the channel its tag names: Discord, Telegram, ntfy, e-mail and 100 more."
    homepage = "https://github.com/caronc/apprise-api"
    fields = (
        Field("url", "Apprise address", "url", help="Apprise's /notify/ address with its configuration key.",
              placeholder="http://apprise:8000/notify/clipseerr"),
        Field("tag", "Channel", "text", required=False,
              help="The tag of the channel in Apprise's configuration, as requests. Empty: every channel.",
              placeholder="requests"),
    )

    def validate(self, config: dict) -> None:
        if "/notify/" not in urllib.parse.urlsplit(config["url"]).path + "/":
            raise Invalid("The Apprise address ends in /notify/ and the configuration key, "
                          "as http://apprise:8000/notify/clipseerr.")
        if "tag" in config and not all(c.isalnum() or c in "-_ ," for c in config["tag"]):
            raise Invalid("A channel is letters, digits, - and _ (several: separated by commas).")

    async def send(self, http, event: Event, config: dict) -> None:
        body = "\n".join([*event.lines, *([f"[Open in clipseerr]({event.url})"] if event.url else [])])
        try:
            await asyncio.to_thread(notify.send, config["url"], event.subject, body or event.subject,
                                    tag=config.get("tag") or None, kind=event.kind)
        except notify.NotSent as exc:
            raise outbound.SendFailed(str(exc)) from exc

    def summary(self, config: dict) -> str:
        parts = urllib.parse.urlsplit(config.get("url", ""))
        where = f"{parts.netloc}{parts.path}" if parts.netloc else "Apprise"
        return f"{where} · {config['tag']}" if config.get("tag") else where
