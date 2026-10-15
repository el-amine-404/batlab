"""A Discord channel, through a webhook."""

import re

from .. import outbound
from ..events import Event
from .base import ConnectionType, Field, Invalid

_WEBHOOK = re.compile(
    r"^https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/(\d+)/[\w-]+$")
# Discord refuses a message over 2,000 characters.
LIMIT = 1900


class Discord(ConnectionType):
    key = "discord"
    label = "Discord"
    description = "Posts a message to a Discord channel."
    fields = (
        Field("webhook_url", "Webhook URL", "secret_url",
              help="In Discord: the channel's settings, Integrations, Webhooks, New Webhook, Copy Webhook URL.",
              placeholder="https://discord.com/api/webhooks/…"),
    )

    def validate(self, config: dict) -> None:
        if not _WEBHOOK.match(config["webhook_url"]):
            raise Invalid("That isn't a Discord webhook URL (https://discord.com/api/webhooks/…).")

    def send(self, event: Event, config: dict) -> None:
        lines = [f"**{event.subject}**", *event.lines]
        if event.url:
            lines.append(event.url)
        text = "\n".join(lines)
        if len(text) > LIMIT:
            text = text[: LIMIT - 1] + "…"
        outbound.post_json(config["webhook_url"], {"content": text, "allowed_mentions": {"parse": []}})

    def summary(self, config: dict) -> str:
        match = _WEBHOOK.match(config.get("webhook_url", ""))
        return f"Webhook …{match.group(1)[-4:]}" if match else "Webhook"
