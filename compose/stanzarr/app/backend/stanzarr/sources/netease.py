"""NetEase Cloud Music (music.163.com): a large catalogue with synced lyrics,
strong beyond English (Chinese, Japanese, Korean, and much else). No account.

Its search gives songs with their length; the closest one by title, artist
and length (as LRCLIB's entries are weighed) has its lyrics asked for. The
credit lines NetEase puts at the top (作词 : …, Composer : …) are dropped."""

import re

from ..notifications.base import Field
from . import http
from .base import NOTHING, Found, LyricsSource, SongQuery
from .lrclib import LENGTH_SLACK, names, same_title

API = "https://music.163.com/api"
HEADERS = {"Referer": "https://music.163.com/"}
CREDIT = re.compile(r"^\s*(\[[^\]]*\]\s*)*(作词|作曲|编曲|制作人|混音|母带|和声|吉他|贝斯|鼓|录音|监制|出品|"
                    r"lyricist|lyrics|composer|composed|arranger|producer|mixed|mastered|written)\b.{0,40}[:：]",
                    re.IGNORECASE)
INSTRUMENTAL = ("纯音乐，请欣赏", "纯音乐, 请欣赏")


def _strip_credits(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not CREDIT.match(line))


class NetEase(LyricsSource):
    key = "netease"
    label = "NetEase Cloud Music"
    description = "Synced lyrics from a large catalogue, strong beyond English. No account."
    homepage = "https://music.163.com"
    fields = (Field("url", "Address", "url", required=False, default=API, help="NetEase's API.", placeholder=API),)

    def search(self, song: SongQuery, config: dict) -> Found:
        api = (config.get("url") or API).rstrip("/")
        terms = f"{song.title} {song.artist}".strip()
        # search/get answers in the clear (search/get/web is encrypted).
        result = http.get_json(f"{api}/search/get", {"s": terms, "type": 1, "limit": 20, "offset": 0}, HEADERS)
        found = (result or {}).get("result") if isinstance(result, dict) else None
        best, best_gap = None, None
        for entry in (found.get("songs") if isinstance(found, dict) else None) or []:
            if not same_title(entry.get("name") or "", song.title):
                continue
            artists = ", ".join(a.get("name") or "" for a in entry.get("artists") or [])
            if song.artist and not names(artists) & names(song.artist):
                continue
            gap = abs((entry.get("duration") or 0) / 1000 - song.duration) if song.duration else 0.0
            if gap >= LENGTH_SLACK:
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = entry, gap
        if best is None:
            return NOTHING
        lyric = http.get_json(f"{api}/song/lyric", {"id": best["id"], "lv": -1, "kv": -1, "tv": -1}, HEADERS) or {}
        if lyric.get("nolyric"):
            return Found("instrumental", "", f"netease id: {best['id']}")
        text = _strip_credits((lyric.get("lrc") or {}).get("lyric") or "").strip()
        if not text:
            return NOTHING
        if any(mark in text for mark in INSTRUMENTAL):
            return Found("instrumental", "", f"netease id: {best['id']}")
        reference = f"netease id: {best['id']}"
        if re.search(r"^\s*\[\d{1,3}:\d{2}", text, re.MULTILINE):
            return Found("synced", text + "\n", reference)
        return Found("plain", text + "\n", reference)
