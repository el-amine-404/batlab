"""Any program: each event as JSON, POSTed to an address (docs/services.md,
"Messages")."""

from .. import outbound
from ..events import Event
from .base import Field, NotificationType


class Webhook(NotificationType):
    key = "webhook"
    label = "Webhook"
    description = ('POSTs each event as JSON: {"source": "stanzarr", "event", "time", "subject", "text", "url", '
                   '"data"}.')
    fields = (
        Field("url", "Address", "secret_url", placeholder="http://my-app:8080/stanzarr-events"),
        Field("token", "Bearer token", "secret", required=False,
              help="Sent as Authorization: Bearer <token>, when the program wants one."),
    )

    async def send(self, http, event: Event, config: dict) -> None:
        headers = {"Authorization": f"Bearer {config['token']}"} if config.get("token") else {}
        await outbound.call(http, "POST", config["url"], headers=headers, json=event.payload())

    def summary(self, config: dict) -> str:
        parts = outbound.host(config.get("url", ""))
        return f"{parts}/…" if parts else "Webhook"
