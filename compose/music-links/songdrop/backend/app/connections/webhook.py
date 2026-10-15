"""Any program: the event as JSON (docs/services.md, "Messages"), as data
rather than a message for people."""

import urllib.parse

from .. import outbound
from ..events import Event
from .base import ConnectionType, Field


class Webhook(ConnectionType):
    key = "webhook"
    label = "Webhook"
    description = "Sends each event as JSON (POST) to an address of your choice."
    fields = (
        Field("url", "URL", "secret_url",
              help="Receives a POST with source, event, time, subject, text, url and data (the job).",
              placeholder="http://my-app:8080/songdrop-events"),
    )

    def send(self, event: Event, config: dict) -> None:
        outbound.post_json(config["url"], event.as_payload())

    def summary(self, config: dict) -> str:
        parts = urllib.parse.urlsplit(config.get("url", ""))
        return f"{parts.scheme}://{parts.netloc}/…" if parts.netloc else "Webhook"
