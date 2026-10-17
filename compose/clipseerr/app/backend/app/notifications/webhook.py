"""Any program: each event as JSON, POSTed to an address."""

import time

from .. import outbound
from ..events import Event
from ..integrations import Field
from .base import NotificationType


class Webhook(NotificationType):
    key = "webhook"
    label = "Webhook"
    description = 'POSTs each event as JSON: {"source": "clipseerr", "event", "time", "subject", "data"}.'
    fields = (
        Field("url", "Address", "url", placeholder="http://my-app:8080/clipseerr"),
        Field("token", "Bearer token", "secret", required=False,
              help="Sent as Authorization: Bearer <token>, when the program wants one."),
    )

    async def send(self, http, event: Event, config: dict) -> None:
        headers = {"Authorization": f"Bearer {config['token']}"} if config.get("token") else {}
        await outbound.call(http, "POST", config["url"], headers=headers,
                            json={"source": "clipseerr", "event": event.name, "time": time.time(),
                                  "subject": event.subject, "data": event.data})
