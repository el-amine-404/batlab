"""MusicBrainz (musicbrainz.org): who made a song, as its open database
says: the performers, the writers of the work, the producers, engineers and
mixers of the recording. For the songs YouTube Music credits nothing for.

Free, no key; its rule is one request a second and a User-Agent saying who
asks. The same song has several recordings there, not all with their
credits filled in: up to MAX_RECORDINGS are read, the first with credits wins."""

import asyncio
import time

import httpx
from batlab_common import titles

API = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "clipseerr (a home server's music video app; https://github.com/el-amine-404/batlab)",
           "Accept": "application/json"}
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MIN_SCORE = 90
MAX_RECORDINGS = 3
# A recording's length may differ this much from the song's and still be it.
SLACK = 10

# MusicBrainz's relation types, as the credits panel words them, in its order.
ROLES = (
    ("Performed by", ("vocal", "instrument", "performer", "performing orchestra")),
    ("Written by", ("writer", "composer", "lyricist", "librettist")),
    ("Produced by", ("producer",)),
    ("Mixed by", ("mix",)),
    ("Engineered by", ("recording", "engineer", "audio")),
    ("Mastered by", ("mastering",)),
)

_lock = asyncio.Lock()
_last = 0.0


async def _get(http: httpx.AsyncClient, path: str, params: dict) -> dict:
    global _last
    async with _lock:
        pause = 1.1 - (time.monotonic() - _last)
        if pause > 0:
            await asyncio.sleep(pause)
        _last = time.monotonic()
        response = await http.get(f"{API}/{path}", params={**params, "fmt": "json"}, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def _quote(text: str) -> str:
    return '"' + text.replace("\\", " ").replace('"', " ") + '"'


def _credits(recording: dict) -> list[dict]:
    names: dict[str, list[str]] = {role: [] for role, _ in ROLES}

    def add(kind: str, name: str | None) -> None:
        for role, kinds in ROLES:
            if kind in kinds and name and name not in names[role]:
                names[role].append(name)

    for relation in recording.get("relations") or []:
        if relation.get("target-type") == "artist":
            add(relation.get("type") or "", (relation.get("artist") or {}).get("name"))
        elif relation.get("target-type") == "work":
            for credit in (relation.get("work") or {}).get("relations") or []:
                add(credit.get("type") or "", (credit.get("artist") or {}).get("name"))
    if names["Produced by"] or names["Written by"] or names["Engineered by"] or names["Mixed by"]:
        # The performers the relations leave out: the recording's own artists.
        for credit in recording.get("artist-credit") or []:
            add("performer", (credit.get("artist") or {}).get("name") or credit.get("name"))
    return [{"role": role, "names": people} for role, people in names.items() if people]


async def credits(http: httpx.AsyncClient, artist: str, title: str, seconds: int | None) -> dict:
    """{"credits": [{"role", "names"}], "provider": "MusicBrainz"}; no credits when it has none."""
    found = await _get(http, "recording", {"query": f"recording:{_quote(title)} AND artist:{_quote(artist)}",
                                           "limit": 10})
    candidates = []
    for recording in found.get("recordings") or []:
        credited = " & ".join(c.get("name") or "" for c in recording.get("artist-credit") or [])
        if (recording.get("score") or 0) < MIN_SCORE or not titles.agrees(title, artist, recording.get("title") or "", credited):
            continue
        if seconds and recording.get("length") and abs(recording["length"] / 1000 - seconds) > SLACK:
            continue
        candidates.append(recording["id"])
    for recording_id in candidates[:MAX_RECORDINGS]:
        detail = await _get(http, f"recording/{recording_id}", {"inc": "artist-credits+artist-rels+work-rels+work-level-rels"})
        made = _credits(detail)
        if made:
            return {"credits": made, "provider": "MusicBrainz"}
    return {"credits": [], "provider": None}
