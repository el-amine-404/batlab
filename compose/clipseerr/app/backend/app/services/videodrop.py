"""videodrop (compose/videodrop): the video file itself, through yt-dlp, at
the quality asked for, into one of videodrop's destinations (the music videos
library, which Jellyfin shows). clipseerr names the destination; it never
knows where that is on disk."""

from .. import outbound
from ..integrations import Choice, Field
from .base import Context, ServiceType, Update, watch_url


class Videodrop(ServiceType):
    key = "videodrop"
    label = "videodrop"
    description = "Downloads the video with yt-dlp, at the quality asked for, into one of videodrop's destinations."
    homepage = "http://videodrop.homelab.lan"
    fields = (
        Field("base_url", "Address", "url", placeholder="http://videodrop:8840", default="http://videodrop:8840"),
        Field("destination", "Destination", "text", default="music-videos",
              help="One of videodrop's destinations (its page, Destinations): where the video goes."),
        Field("quality", "Quality", "select", default="best",
              choices=tuple(Choice(v, l) for v, l in (("best", "Best"), ("2160", "2160p"), ("1440", "1440p"),
                                                       ("1080", "1080p"), ("720", "720p")))),
        Field("codec", "Video codec", "select", default="any",
              help="Any takes YouTube's best (often AV1, up to 4K). H.264 plays on every TV and phone without "
                   "Jellyfin converting it, but YouTube has it up to 1080p only.",
              choices=(Choice("any", "Any (best quality)"), Choice("h264", "H.264 (plays everywhere)"),
                       Choice("h265", "H.265 / HEVC"), Choice("vp9", "VP9"), Choice("av1", "AV1"))),
        Field("container", "File", "select", default="mp4",
              help="MP4 plays everywhere, Jellyfin included.",
              choices=(Choice("mp4", "MP4"), Choice("mkv", "MKV"), Choice("original", "As YouTube sends it"))),
    )

    def kinds(self, config):
        return ("video",)

    async def submit(self, ctx: Context, request: dict, config: dict) -> Update:
        youtube_id = request["media"].get("video_id")
        if not youtube_id:
            raise outbound.SendFailed("This song has no YouTube video for videodrop to download.")
        body = {
            "url": watch_url(youtube_id),
            "destination": config.get("destination") or "music-videos",
            "options": {"quality": config.get("quality", "best"), "codec": config.get("codec", "any"),
                        "container": config.get("container", "mp4")},
            "client": "clipseerr",
            "callback_url": f"{ctx.callback_base}/hooks/videodrop?request={request['id']}",
            "callback_events": ["finished", "failed", "cancelled"],
        }
        job = (await outbound.call(ctx.http, "POST", f"{config['base_url']}/api/v1/jobs", json=body)).json()
        return Update("sent", "Queued in videodrop.", external_id=job["id"])

    async def poll(self, ctx: Context, request: dict, attempt: dict, config: dict) -> Update:
        try:
            job = (await outbound.call(ctx.http, "GET",
                                       f"{config['base_url']}/api/v1/jobs/{attempt['external_id']}")).json()
        except outbound.SendFailed as exc:
            if " 404" in str(exc):
                return Update("failed", "videodrop no longer has this download.")
            raise
        status = job.get("status")
        if status == "queued":
            return Update("sent", "Queued in videodrop." if not job.get("retry_at")
                          else f"videodrop tries again soon: {job.get('error') or 'it failed once'}")
        if status == "running":
            percent = (job.get("progress") or {}).get("percent")
            return Update("downloading", "Downloading.",
                          progress=percent / 100 if isinstance(percent, (int, float)) else None)
        videos = [f for f in job.get("files") or [] if f.get("kind") == "video"]
        if status == "finished" and videos:
            options = job.get("options") or {}
            quality = options.get("quality") or "best"
            label = f"{(options.get('container') or '').upper()} {quality if quality == 'best' else quality + 'p'}"
            if videos[0].get("existed"):
                return Update("completed", "The library had it already.", quality=label.strip(), already=True)
            return Update("completed", f"Saved as {videos[0].get('name')}.", quality=label.strip())
        return Update("failed", job.get("error") or f"videodrop: {status}.")

    async def test(self, ctx: Context, config: dict) -> str:
        await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/v1/health")
        destination = config.get("destination") or "music-videos"
        try:
            await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/v1/destinations/{destination}")
        except outbound.SendFailed as exc:
            if " 404" in str(exc):
                raise outbound.SendFailed(f"videodrop answers, but has no destination {destination!r}.") from exc
            raise
        return f"videodrop answers; videos go to its {destination!r} destination."
