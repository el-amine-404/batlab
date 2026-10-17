"""MeTube: the video file itself (or its sound), through yt-dlp, at the best
quality YouTube has. A second MeTube writes into the music videos library
(compose/music-links, metube-videos), which Jellyfin shows."""

import time

from .. import outbound
from ..integrations import Choice, Field
from .base import Context, ServiceType, Update, watch_url

_ACTIVE = ("pending", "preparing", "downloading")
_RECENT = 120  # seconds MeTube may take to list what it was given


class Metube(ServiceType):
    key = "metube"
    label = "MeTube"
    description = "Downloads the video (or its sound) with yt-dlp at the best quality YouTube has."
    homepage = "https://github.com/alexta69/metube"
    fields = (
        Field("base_url", "Address", "url", placeholder="http://metube-videos:8081"),
        Field("download", "Download", "select", default="video",
              choices=(Choice("video", "The video"), Choice("audio", "Only the sound"))),
        Field("quality", "Quality", "select", default="best",
              choices=tuple(Choice(v, l) for v, l in (("best", "Best"), ("2160", "2160p"), ("1440", "1440p"),
                                                       ("1080", "1080p"), ("720", "720p")))),
        Field("codec", "Video codec", "select", default="auto",
              help="Auto takes YouTube's best (often AV1, up to 4K). H.264 plays on every TV and phone without "
                   "Jellyfin converting it, but YouTube has it up to 1080p only.",
              choices=(Choice("auto", "Auto (best quality)"), Choice("h264", "H.264 (plays everywhere)"),
                       Choice("h265", "H.265 / HEVC"), Choice("vp9", "VP9"), Choice("av1", "AV1"))),
        Field("format", "Format", "select", default="mp4",
              help="MP4 plays everywhere, Jellyfin included; Auto keeps what YouTube sends (often WebM).",
              choices=(Choice("mp4", "MP4"), Choice("any", "Auto"))),
        Field("folder", "Folder", "text", required=False,
              help="A folder inside MeTube's own; only when MeTube runs with CUSTOM_DIRS on."),
    )

    def kinds(self, config):
        return (config.get("download") or "video",)

    async def submit(self, ctx: Context, request: dict, config: dict) -> Update:
        youtube_id = request["media"].get("video_id")
        if not youtube_id:
            raise outbound.SendFailed("This song has no YouTube video for MeTube to download.")
        url = watch_url(youtube_id)
        audio = config.get("download") == "audio"
        body = {"url": url, "download_type": "audio" if audio else "video", "auto_start": True,
                "quality": "best" if audio else config.get("quality", "best"),
                "format": "m4a" if audio else config.get("format", "mp4")}
        if not audio:
            body["codec"] = config.get("codec", "auto")
        if config.get("folder"):
            body["folder"] = config["folder"]
        found = (await outbound.call(ctx.http, "POST", f"{config['base_url']}/add", json=body)).json()
        if found.get("status") == "error":
            raise outbound.SendFailed(f"MeTube refused it: {found.get('msg') or 'no reason given'}.")
        return Update("sent", "Queued in MeTube.", external_id=url)

    async def poll(self, ctx: Context, request: dict, attempt: dict, config: dict) -> Update:
        async def history():
            found = (await outbound.call(ctx.http, "GET", f"{config['base_url']}/history")).json()
            items = {}
            # done first, so a newer try in the queue wins over an old finished one.
            for group in ("done", "pending", "queue"):
                for item in found.get(group) or []:
                    items[item.get("url")] = item
            return items

        item = (await ctx.once(f"metube:{config['base_url']}", history)).get(attempt["external_id"])
        if not item:
            if time.time() - (attempt.get("started_at") or 0) < _RECENT:
                return Update("sent", "Queued in MeTube.")
            return Update("failed", "MeTube no longer lists this download.")
        status = item.get("status")
        if status in _ACTIVE:
            percent = item.get("percent")
            return Update("downloading" if status == "downloading" else "sent",
                          "Downloading." if status == "downloading" else "Queued in MeTube.",
                          progress=percent / 100 if isinstance(percent, (int, float)) else None)
        if status == "finished":
            quality = item.get("quality") or "best"
            label = f"{(item.get('format') or '').upper()} {quality if quality != 'best' else 'best'}".strip()
            return Update("completed", f"Saved as {item.get('filename') or 'a file'}.", quality=label)
        return Update("failed", item.get("error") or item.get("msg") or "MeTube couldn't download it.")

    async def test(self, ctx: Context, config: dict) -> str:
        await outbound.call(ctx.http, "GET", f"{config['base_url']}/history")
        return "MeTube answers."
