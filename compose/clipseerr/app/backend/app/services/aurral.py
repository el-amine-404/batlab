"""Aurral (compose/aurral): a single track, searched on Soulseek first (FLAC
when someone shares it), then with its own yt-dlp; into music-aurral, which
musicguard checks. The best audio this stack can get for one song."""

from .. import outbound
from ..integrations import Field
from .base import Context, ServiceType, Update

_STATES = {"pending": "sent", "downloading": "downloading", "done": "completed", "failed": "failed"}


def quality(job: dict) -> str | None:
    """ "FLAC 24-bit/96 kHz", "MP3 320 kbps"…, from what Aurral measured."""
    kind = (job.get("qualityFormat") or "").upper()
    if not kind:
        return None
    if job.get("qualityBitDepth") and job.get("qualitySampleRate"):
        rate = job["qualitySampleRate"] / 1000
        return f"{kind} {job['qualityBitDepth']}-bit/{rate:g} kHz"
    if job.get("qualityBitrateKbps"):
        return f"{kind} {round(job['qualityBitrateKbps'])} kbps"
    return kind


class Aurral(ServiceType):
    key = "aurral"
    label = "Aurral"
    description = ("Gets the song alone from Soulseek (lossless when shared), else with its own yt-dlp. "
                   "Best for audio.")
    homepage = "https://github.com/lklynet/aurral"
    fields = (
        Field("base_url", "Address", "url", placeholder="http://aurral:3001", default="http://aurral:3001"),
        Field("api_key", "API key", "secret", help="In Aurral: Settings, General, API key."),
    )

    def kinds(self, config):
        return ("audio",)

    def _headers(self, config: dict) -> dict:
        return {"X-Api-Key": config["api_key"], "Accept": "application/json"}

    async def submit(self, ctx: Context, request: dict, config: dict) -> Update:
        media = request["media"]
        body = {"artistName": media["artist"], "trackName": media["title"]}
        if media.get("album"):
            body["albumName"] = media["album"]
        if media.get("year"):
            body["releaseYear"] = str(media["year"])
        if media.get("duration"):
            body["durationMs"] = int(media["duration"]) * 1000
        response = await outbound.call(ctx.http, "POST", f"{config['base_url']}/api/library/downloads/track",
                                       headers=self._headers(config), json=body)
        found = response.json()
        if found.get("alreadyOwned"):
            return Update("completed", "Already in your library.", already=True)
        if found.get("reused"):
            return Update("completed", "Aurral already had the file.", external_id=found.get("jobId"))
        if not found.get("jobId"):
            raise outbound.SendFailed("Aurral took the request but gave no job.")
        return Update("sent", "Waiting in Aurral's queue." if not found.get("alreadyQueued")
                      else "Aurral was already getting it.", external_id=str(found["jobId"]))

    async def poll(self, ctx: Context, request: dict, attempt: dict, config: dict) -> Update:
        async def jobs():
            response = await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/playlists/jobs",
                                           headers=self._headers(config))
            return {str(job.get("id")): job for job in response.json() if isinstance(job, dict)}

        job = (await ctx.once(f"aurral:{config['base_url']}", jobs)).get(attempt["external_id"])
        if not job:
            return Update("failed", "Aurral no longer has this request.")
        state = _STATES.get(job.get("status"), "sent")
        source = job.get("downloadSource") or job.get("downloadClient")
        if state == "completed":
            return Update("completed", f"Downloaded by Aurral{f' from {source}' if source else ''}.",
                          quality=quality(job))
        if state == "failed":
            return Update("failed", job.get("error") or "Aurral couldn't find it.")
        if state == "downloading":
            return Update("downloading", f"Downloading{f' from {source}' if source else ''}.")
        return Update("sent", "Searching." if job.get("startedAt") else "Waiting in Aurral's queue.")

    async def test(self, ctx: Context, config: dict) -> str:
        await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/playlists/status",
                            headers=self._headers(config))
        return "Connected to Aurral; the API key works."
