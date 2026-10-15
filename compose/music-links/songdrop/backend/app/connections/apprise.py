"""Apprise (compose/apprise): one address for every service batlab can tell,
Discord, Telegram, ntfy, e-mail…, chosen in Apprise's configuration by the
message's tag. The way batlab's own services send (batlab_common.notify)."""

import urllib.parse

from batlab_common import notify

from .. import outbound
from ..events import KINDS, Event
from .base import ConnectionType, Field, Invalid


class Apprise(ConnectionType):
    key = "apprise"
    label = "Apprise"
    description = "Sends through Apprise to the channel its tag names: Discord, Telegram, ntfy, e-mail and 100 more."
    fields = (
        Field("url", "Apprise address", "url",
              help="Apprise's /notify/ address with its configuration key.",
              placeholder="http://apprise:8000/notify/songdrop"),
        Field("tag", "Channel", "text", required=False,
              help="The tag of the channel in Apprise's configuration, as download-issues. Empty: every channel.",
              placeholder="download-issues"),
    )

    def validate(self, config: dict) -> None:
        if "/notify/" not in urllib.parse.urlsplit(config["url"]).path + "/":
            raise Invalid("The Apprise address ends in /notify/ and the configuration key, "
                          "as http://apprise:8000/notify/songdrop.")
        if "tag" in config and not all(c.isalnum() or c in "-_ ," for c in config["tag"]):
            raise Invalid("A channel is letters, digits, - and _ (several: separated by commas).")

    def send(self, event: Event, config: dict) -> None:
        body = "\n".join([*event.lines, *([f"[Open in songdrop]({event.url})"] if event.url else [])])
        try:
            notify.send(config["url"], event.subject, body or event.subject, tag=config.get("tag") or None,
                        kind=KINDS.get(event.name, "info"))
        except notify.NotSent as exc:
            raise outbound.SendFailed(str(exc)) from exc

    def summary(self, config: dict) -> str:
        parts = urllib.parse.urlsplit(config.get("url", ""))
        where = f"{parts.netloc}{parts.path}" if parts.netloc else "Apprise"
        return f"{where} · {config['tag']}" if config.get("tag") else where
