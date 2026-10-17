"""A notification type: an integration (integrations/base.py) that sends events."""

import httpx

from ..events import Event
from ..integrations import IntegrationType


class NotificationType(IntegrationType):
    async def send(self, http: httpx.AsyncClient, event: Event, config: dict) -> None:
        """Delivers the event now; raises outbound.SendFailed."""
        raise NotImplementedError
