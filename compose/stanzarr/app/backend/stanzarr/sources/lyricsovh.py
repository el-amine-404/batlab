"""lyrics.ovh: the words of a song by its artist and title, without
timings. Free, no account; it finds the song itself, so only an exact
artist and title find anything. Words for songs the synced sources lack,
then timed by AI or by hand."""

import urllib.error
import urllib.parse

from ..notifications.base import Field
from . import http
from .base import NOTHING, Found, LyricsSource, SongQuery

API = "https://api.lyrics.ovh/v1"


class LyricsOvh(LyricsSource):
    key = "lyricsovh"
    label = "lyrics.ovh"
    description = "The words of many songs, without timings. Free, no account; needs the artist and title as they are."
    homepage = "https://lyricsovh.docs.apiary.io"
    synced = False
    fields = (Field("url", "Address", "url", required=False, default=API, help="lyrics.ovh's API.", placeholder=API),)

    def search(self, song: SongQuery, config: dict) -> Found:
        if not song.artist or not song.title:
            return NOTHING
        api = (config.get("url") or API).rstrip("/")
        try:
            answer = http.get_json(f"{api}/{urllib.parse.quote(song.artist, safe='')}/{urllib.parse.quote(song.title, safe='')}")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return NOTHING
            raise
        words = ((answer or {}).get("lyrics") or "").replace("\r\n", "\n").strip()
        # Its first line is sometimes "Paroles de la chanson … par …": the filter drops it.
        if not words:
            return NOTHING
        return Found("plain", words + "\n", "lyrics.ovh", {"artist": song.artist, "title": song.title, "duration": None})
