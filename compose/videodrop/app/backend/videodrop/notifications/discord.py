"""A Discord channel, through a webhook: one card per event."""

import re

from .. import outbound
from ..domain import Invalid
from ..events import Event
from .base import Field, NotificationType

_WEBHOOK = re.compile(
    r"^https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/(\d+)/[\w-]+$")
COLOURS = {"info": 0x5865F2, "success": 0x2ECC71, "warning": 0xE67E22, "failure": 0xE74C3C}


class Discord(NotificationType):
    key = "discord"
    label = "Discord"
    description = "Posts a card to a Discord channel."
    homepage = "https://support.discord.com/hc/en-us/articles/228383668"
    fields = (
        Field("webhook_url", "Webhook URL", "secret_url",
              help="In Discord: the channel's settings, Integrations, Webhooks, New Webhook, Copy Webhook URL.",
              placeholder="https://discord.com/api/webhooks/…"),
        Field("username", "Shown as", "text", required=False, default="videodrop"),
    )

    def validate(self, config: dict) -> None:
        if not _WEBHOOK.match(config["webhook_url"]):
            raise Invalid("That isn't a Discord webhook URL (https://discord.com/api/webhooks/…).")

    async def send(self, http, event: Event, config: dict) -> None:
        embed = {"title": event.subject[:256], "description": "\n".join(event.lines)[:4000],
                 "color": COLOURS[event.kind]}
        if event.url:
            embed["url"] = event.url
        await outbound.call(http, "POST", config["webhook_url"],
                            json={"username": config.get("username") or "videodrop", "embeds": [embed],
                                  "allowed_mentions": {"parse": []}})

    def summary(self, config: dict) -> str:
        found = _WEBHOOK.match(config.get("webhook_url", ""))
        return f"Webhook …{found.group(1)[-4:]}" if found else "Webhook"
