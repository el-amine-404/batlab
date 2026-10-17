"""Aurral (compose/aurral): a single track, searched on Soulseek first (FLAC
when someone shares it), then with its own yt-dlp; into music-aurral, which
musicguard checks. The best audio this stack can get for one song."""

import time

from .. import outbound
from ..integrations import Field
from .base import Context, ServiceType, Update

# Aurral's activity feed (/api/requests) is where it tells about track jobs:
# its own job list only shows playlist flows.
_FOUND_LATE = 15 * 60  # seconds a new job may take to show in the feed


def _file_kind(name: str | None) -> str | None:
    """ "Housewife.flac" → "FLAC"."""
    ext = (name or "").rpartition(".")[2]
    return ext.upper() if ext and ext != name and len(ext) <= 5 else None


def from_activity(entry: dict) -> Update:
    """Where a track job is, from its entry in Aurral's activity feed."""
    status, label = entry.get("status"), entry.get("statusLabel") or ""
    source = entry.get("source")
    via = f" from {'Soulseek' if source == 'slskd' else source}" if source else ""
    if status == "completed":
        return Update("completed", "Already had it; Aurral reused the file." if label == "Reused"
                      else f"Downloaded by Aurral{via}.", quality=_file_kind(entry.get("sourceFilename")))
    if status == "failed":
        return Update("failed", entry.get("subtitle") or "Aurral couldn't find it.")
    if status == "blocked":
        return Update("sent", "Aurral holds it for review (Aurral, Requests).")
    if label in ("Downloading", "Moving"):
        return Update("downloading", f"{label}{via}.")
    if status == "processing":
        return Update("sent", f"Searching{via}.")
    return Update("sent", "Waiting in Aurral's queue.")


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
        async def activity():
            response = await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/requests",
                                           headers=self._headers(config))
            found = response.json()
            entries = found if isinstance(found, list) else found.get("requests") or []
            return {str(e.get("jobId")): e for e in entries
                    if isinstance(e, dict) and e.get("kind") == "track_download" and e.get("jobId")}

        entry = (await ctx.once(f"aurral:{config['base_url']}", activity)).get(attempt["external_id"])
        if entry:
            return from_activity(entry)
        if time.time() - attempt["started_at"] < _FOUND_LATE:
            return Update("sent", attempt.get("message") or "Waiting in Aurral's queue.")
        return Update("failed", "Aurral no longer lists this request.")

    async def test(self, ctx: Context, config: dict) -> str:
        await outbound.call(ctx.http, "GET", f"{config['base_url']}/api/playlists/status",
                            headers=self._headers(config))
        return "Connected to Aurral; the API key works."
