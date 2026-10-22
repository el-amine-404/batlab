"""songdrop (compose/songdrop/app): the song's own audio from YouTube
Music (lossy, about 130-160 kbps Opus), checked against its preview, into the
inbox musicguard files. A good fallback: it always finds the upload."""

import re

from .. import outbound
from ..integrations import Choice, Field
from .base import Context, ServiceType, Update, watch_url


class Songdrop(ServiceType):
    key = "songdrop"
    label = "songdrop"
    description = "Downloads the song's YouTube Music audio through yt-dlp. Always finds it; lossy."
    fields = (
        Field("base_url", "Address", "url", placeholder="http://songdrop:8810", default="http://songdrop:8810"),
        Field("api_key", "API key", "secret", required=False,
              help="In songdrop: Settings, API keys; it needs Ask for downloads."),
        Field("source", "Download", "select", default="song",
              choices=(Choice("song", "The song (official audio), else the video's sound"),
                       Choice("video", "Always the video's sound"))),
    )

    def kinds(self, config):
        return ("audio",)

    @staticmethod
    def _headers(config: dict) -> dict:
        return {"X-Api-Key": config["api_key"]} if config.get("api_key") else {}

    async def submit(self, ctx: Context, request: dict, config: dict) -> Update:
        media = request["media"]
        youtube_id = media.get("audio_id") if config.get("source", "song") == "song" else None
        youtube_id = youtube_id or media.get("video_id")
        if not youtube_id:
            raise outbound.SendFailed("This song has no YouTube upload for songdrop to download.")
        body = {"url": watch_url(youtube_id, music=True),
                "callback_url": f"{ctx.callback_base}/hooks/songdrop?request={request['id']}"}
        job = (await outbound.call(ctx.http, "POST", f"{config['base_url']}/api/v1/jobs", json=body,
                                   headers=self._headers(config))).json()
        return Update("sent", "Queued in songdrop.", external_id=job["id"])

    async def poll(self, ctx: Context, request: dict, attempt: dict, config: dict) -> Update:
        try:
            job = (await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/v1/jobs/{attempt['external_id']}",
                                       headers=self._headers(config))).json()
        except outbound.SendFailed as exc:
            if " 404" in str(exc):
                return Update("failed", "songdrop no longer has this job.")
            raise
        status = job.get("status")
        if status == "queued":
            return Update("sent", "Queued in songdrop.")
        if status == "running":
            return Update("downloading", "Downloading.", progress=job.get("progress"))
        files = job.get("files") or []
        if status in ("done", "partial") and files:
            ext = re.search(r"\.(\w+)$", files[0].get("name") or "")
            return Update("completed", "Downloaded by songdrop; musicguard files it.",
                          quality=ext.group(1).upper() if ext else None)
        errors = job.get("errors") or []
        reason = (errors[0].get("message") if errors else None) or job.get("message") or f"songdrop: {status}."
        return Update("failed", reason)

    async def test(self, ctx: Context, config: dict) -> str:
        await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/v1/health")
        try:
            me = (await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/v1/auth/me",
                                      headers=self._headers(config))).json()["me"]
        except outbound.SendFailed as exc:
            if " 401" in str(exc):
                raise outbound.SendFailed("songdrop answers, but wants an API key: make one in its Settings, API keys, "
                                          "and give it here.") from exc
            raise
        if not {"download", "admin"} & set(me.get("permissions") or ()):
            raise outbound.SendFailed(f"songdrop knows the key ({me.get('name')}), but it may not ask for downloads: "
                                      "give it that in songdrop's Settings, API keys.")
        return f"songdrop answers, and takes downloads from {me.get('name')}."
