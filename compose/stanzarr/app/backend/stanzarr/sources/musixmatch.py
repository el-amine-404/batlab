"""Musixmatch: the largest catalogue of synced lyrics. Through the API its
desktop app uses, with a user token of one's own (Settings, Sources: how to
get one is in the field's help). Musixmatch's terms don't allow this use:
it is off unless added, and asked once a second at most.

The song is matched by its ISRC when the tags have one, else by title,
artist and length; synced lines come from its subtitles, else plain words."""

import json

from ..notifications.base import Field
from . import http
from .base import NOTHING, Found, LyricsSource, SongQuery

API = "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get"
HEADERS = {"authority": "apic-desktop.musixmatch.com", "cookie": "x-mxm-token-guid="}


def _body(call: dict) -> dict:
    message = (call or {}).get("message") or {}
    body = message.get("body")
    return body if isinstance(body, dict) else {}


def _stamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"[{minutes:02d}:{seconds - minutes * 60:05.2f}]"


class Musixmatch(LyricsSource):
    key = "musixmatch"
    label = "Musixmatch"
    description = "The largest catalogue of synced lyrics. Your own user token; Musixmatch's terms don't allow this use."
    homepage = "https://www.musixmatch.com"
    fields = (
        Field("token", "User token", "secret",
              help="Musixmatch's desktop app's token: open https://apic-desktop.musixmatch.com/ws/1.1/token.get"
                   "?app_id=web-desktop-app-v1.0 and copy message.body.user_token."),
    )

    def search(self, song: SongQuery, config: dict) -> Found:
        token = config.get("token") or ""
        if not token:
            raise http.Refused("Musixmatch needs its user token (Settings, Sources).")
        params = {"format": "json", "namespace": "lyrics_richsynced", "subtitle_format": "mxm",
                  "app_id": "web-desktop-app-v1.0", "usertoken": token, "q_track": song.title,
                  "q_artist": song.artist, "q_album": song.album}
        if song.duration:
            params["q_duration"] = round(song.duration)
            params["f_subtitle_length"] = round(song.duration)
        if song.isrc:
            params["track_isrc"] = song.isrc
        answer = http.get_json(API, params, HEADERS)
        header = ((answer or {}).get("message") or {}).get("header") or {}
        if header.get("status_code") == 401:
            raise http.Refused("Musixmatch refused the token" + (" (it asks for a captcha: get a new token)"
                                                                 if header.get("hint") == "captcha" else "."))
        calls = _body(answer).get("macro_calls") or {}
        track = _body(calls.get("matcher.track.get")).get("track") or {}
        if not track:
            return NOTHING
        reference = f"musixmatch id: {track.get('track_id')}"
        matched = {"artist": track.get("artist_name"), "title": track.get("track_name"), "duration": track.get("track_length")}
        if track.get("instrumental"):
            return Found("instrumental", "", reference)
        subtitles = _body(calls.get("track.subtitles.get")).get("subtitle_list") or []
        if subtitles:
            raw = ((subtitles[0] or {}).get("subtitle") or {}).get("subtitle_body") or "[]"
            try:
                lines = json.loads(raw)
            except ValueError:
                lines = []
            text = "\n".join(f"{_stamp(float((line.get('time') or {}).get('total') or 0))} {line.get('text') or ''}"
                             for line in lines if isinstance(line, dict))
            if text.strip():
                return Found("synced", text + "\n", reference, matched)
        lyrics = _body(calls.get("track.lyrics.get")).get("lyrics") or {}
        if lyrics.get("instrumental"):
            return Found("instrumental", "", reference)
        words = (lyrics.get("lyrics_body") or "").split("*******")[0].strip()
        return Found("plain", words + "\n", reference, matched) if words else NOTHING
