"""Genius (genius.com): the words of almost every song, without timings.
Needs a free client access token (genius.com/api-clients, "Generate Access
Token"): its API finds the song, its page holds the words.

Plain lyrics only: a song gets its words from here, then its timings from a
synced source or the AI. The section marks Genius adds ([Chorus], [Verse 1:
…]) are kept: players show them, and they help a person timing the song."""

from html.parser import HTMLParser

from ..notifications.base import Field
from . import http
from .base import NOTHING, Found, LyricsSource, SongQuery
from .lrclib import names, same_title

API = "https://api.genius.com"
VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "source"}


class _Words(HTMLParser):
    """The text of every element marked data-lyrics-container, <br> as a
    new line; what Genius marks to leave out of a selection is left out."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0  # inside a container: how deep
        self.skip = 0  # inside something to leave out: how deep
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in VOID:
            # No end tag follows: not counted.
            if self.depth and not self.skip and tag == "br":
                self.parts.append("\n")
            return
        if self.depth:
            self.depth += 1
            if self.skip:
                self.skip += 1
            elif "data-exclude-from-selection" in attrs:
                self.skip = 1
        elif attrs.get("data-lyrics-container") == "true":
            self.depth = 1
            if self.parts:
                self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if self.depth and not self.skip and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.depth and tag not in VOID:
            self.depth -= 1
            if self.skip:
                self.skip -= 1

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)


def words_of(page: str) -> str:
    parser = _Words()
    parser.feed(page)
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    # Blank lines between sections kept once.
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


class Genius(LyricsSource):
    key = "genius"
    label = "Genius"
    description = "The words of almost every song, without timings. Needs a free access token."
    homepage = "https://genius.com/api-clients"
    synced = False
    fields = (
        Field("token", "Client access token", "secret", help="genius.com/api-clients: make a client, then "
                                                             "\"Generate Access Token\"."),
    )

    def search(self, song: SongQuery, config: dict) -> Found:
        token = config.get("token") or ""
        if not token:
            raise http.Refused("Genius needs its access token (Settings, Sources).")
        found = http.get_json(f"{API}/search", {"q": f"{song.artist} {song.title}".strip()},
                              {"Authorization": f"Bearer {token}"})
        for hit in ((found or {}).get("response") or {}).get("hits") or []:
            result = hit.get("result") or {}
            if hit.get("type") != "song" or not same_title(result.get("title") or "", song.title):
                continue
            artist = (result.get("primary_artist") or {}).get("name") or ""
            if song.artist and not names(artist) & names(song.artist):
                continue
            if result.get("instrumental"):
                return Found("instrumental", "", f"genius id: {result.get('id')}")
            if result.get("lyrics_state") not in (None, "complete"):
                continue
            text = words_of(http.get(result["url"], browser=True).decode("utf-8", "replace"))
            if text:
                return Found("plain", text + "\n", f"genius id: {result.get('id')}",
                             {"artist": artist, "title": result.get("title"), "duration": None})
        return NOTHING
