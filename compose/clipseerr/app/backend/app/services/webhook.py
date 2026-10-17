"""Any other app: the request as JSON, POSTed to an address. It is done once
the app takes it, or, if the app reports back, when it says so:
POST /api/v1/requests/{id}/report {"status": "completed" | "failed" |
"downloading", "message", "quality"}."""

from .. import outbound
from ..integrations import Choice, Field
from .base import Context, ServiceType, Update


class Webhook(ServiceType):
    key = "webhook"
    label = "Webhook"
    description = "POSTs the request as JSON to any app, for anything clipseerr has no type for."
    fields = (
        Field("url", "Address", "url", placeholder="http://my-app:8080/requests"),
        Field("token", "Bearer token", "secret", required=False,
              help="Sent as Authorization: Bearer <token>, when the app wants one."),
        Field("kinds", "Takes", "select", default="audio",
              choices=(Choice("audio", "Audio"), Choice("video", "Video"), Choice("both", "Audio and video"))),
        Field("wait", "Done when", "select", default="accepted",
              choices=(Choice("accepted", "The app takes it (answers 2xx)"),
                       Choice("report", "The app reports back (POST …/report)"))),
    )

    def kinds(self, config):
        value = config.get("kinds") or "audio"
        return ("audio", "video") if value == "both" else (value,)

    async def submit(self, ctx: Context, request: dict, config: dict) -> Update:
        headers = {"Authorization": f"Bearer {config['token']}"} if config.get("token") else {}
        report = f"{ctx.callback_base}/requests/{request['id']}/report"
        await outbound.call(ctx.http, "POST", config["url"], headers=headers,
                            json={"source": "clipseerr", "event": "request.created", "request": request,
                                  "report_url": report})
        if config.get("wait") == "report":
            return Update("sent", f"Sent to {outbound.host(config['url'])}; waiting for its report.")
        return Update("completed", f"Handed to {outbound.host(config['url'])}.")

    async def poll(self, ctx: Context, request: dict, attempt: dict, config: dict) -> Update:
        # Nothing to ask: the app reports (routes/requests.py), or the attempt times out.
        return Update(attempt["state"], attempt.get("message"))

    async def test(self, ctx: Context, config: dict) -> str:
        headers = {"Authorization": f"Bearer {config['token']}"} if config.get("token") else {}
        await outbound.call(ctx.http, "POST", config["url"], headers=headers,
                            json={"source": "clipseerr", "event": "test"})
        return f"{outbound.host(config['url'])} took a test request."
