"""How a video is downloaded (quality, codec, container, subtitles…) and how
its file is named (a destination's template). videodrop's own words: callers
never see yt-dlp's; ytdlp.py translates.

Options come in three layers, the first set wins: the job's own, its
destination's, the settings' defaults (resolve())."""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Quality = Literal["best", "2160", "1440", "1080", "720", "480", "360"]
Codec = Literal["any", "h264", "h265", "vp9", "av1"]
Container = Literal["mp4", "mkv", "webm", "original"]

_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
MAX_PLAYLIST = 500


class VideoOptions(BaseModel):
    """Every option, set: what a download runs with."""

    quality: Quality = Field(description="The highest resolution to take (its height); best: the highest there "
                                         "is.")
    codec: Codec = Field(description="The video codec to prefer; any: the best quality whatever it is. h264 plays "
                                     "on every TV and phone without converting, but YouTube has it up to 1080p.")
    container: Container = Field(description="The file: mp4 plays everywhere; mkv holds anything; original keeps "
                                             "what the site sends.")
    subtitles: list[str] = Field(description="Subtitle languages to embed (en, fr, pt-BR…); empty: none.")
    auto_subtitles: bool = Field(description="Also the site's automatic captions, when a language has no "
                                             "subtitles.")
    lyrics: bool = Field(description="A music video: its synced lyrics (stanzarr's, else LRCLIB's) as a subtitle "
                                     "track named Lyrics, when there are some. The job's song names the song; else it "
                                     "is guessed from the video's title.")
    embed_metadata: bool = Field(description="Title, artist, date and description written into the file.")
    embed_thumbnail: bool = Field(description="The video's picture as the file's cover (not in webm).")
    embed_chapters: bool = Field(description="The video's chapters as the file's chapters.")
    playlist: bool = Field(description="A link to a video in a playlist: the whole playlist, not only the "
                                       "video.")
    playlist_limit: int = Field(ge=1, le=MAX_PLAYLIST, description="A playlist's first videos to take at most.")


def _languages(value):
    if value is None:
        return value
    cleaned = []
    for language in value:
        language = language.strip()
        if not _LANGUAGE.match(language):
            raise ValueError(f"{language!r} isn't a language code (en, fr, pt-BR).")
        if language not in cleaned:
            cleaned.append(language)
    if len(cleaned) > 10:
        raise ValueError("At most 10 subtitle languages.")
    return cleaned


class OptionsPatch(BaseModel):
    """Some options; the others come from the next layer."""

    model_config = ConfigDict(extra="forbid")

    quality: Quality | None = None
    codec: Codec | None = None
    container: Container | None = None
    subtitles: list[str] | None = None
    auto_subtitles: bool | None = None
    lyrics: bool | None = None
    embed_metadata: bool | None = None
    embed_thumbnail: bool | None = None
    embed_chapters: bool | None = None
    playlist: bool | None = None
    playlist_limit: int | None = Field(None, ge=1, le=MAX_PLAYLIST)

    @field_validator("subtitles")
    @classmethod
    def _subtitles(cls, value):
        return _languages(value)


DEFAULTS = VideoOptions(quality="best", codec="any", container="mp4", subtitles=[], auto_subtitles=False,
                        lyrics=False, embed_metadata=True, embed_thumbnail=True, embed_chapters=True, playlist=False,
                        playlist_limit=50)


def resolve(*layers: dict | None, base: VideoOptions = DEFAULTS) -> VideoOptions:
    """base with each layer's set values on top, the first layer winning."""
    merged = base.model_dump()
    for layer in reversed([layer for layer in layers if layer]):
        merged.update({key: value for key, value in layer.items() if value is not None})
    return VideoOptions.model_validate(merged)


# ---- file names ---------------------------------------------------------------------------

# A template's fields: what yt-dlp fills each one with, cut short enough for
# any disk (.150B: 150 bytes). artist falls back on the uploader (a music
# video's artist is often only the channel's name).
FIELDS = {
    "id": "%(id)s",
    "title": "%(title).150B",
    "artist": "%(artist,creator,uploader|Unknown).80B",
    "uploader": "%(uploader|Unknown).80B",
    "channel": "%(channel,uploader|Unknown).80B",
    "date": "%(upload_date>%Y-%m-%d|unknown)s",
    "year": "%(upload_date>%Y|unknown)s",
    "playlist": "%(playlist_title,playlist|Unknown).80B",
    "index": "%(playlist_index|0)03d",
    "site": "%(extractor_key)s",
}
DEFAULT_TEMPLATE = "{artist}/{title} [{id}]"
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def check_template(template: str) -> str:
    """The template, trimmed; raises ValueError with a sentence. A path
    relative to the destination's folder, without the extension: folders
    are separated by /."""
    template = template.strip().strip("/")
    if not template:
        raise ValueError("The file name can't be empty.")
    if len(template) > 200:
        raise ValueError("The file name is 200 characters at most.")
    for name in _PLACEHOLDER.findall(template):
        if name not in FIELDS:
            raise ValueError(f"{{{name}}} isn't a field videodrop knows; use {', '.join('{' + f + '}' for f in FIELDS)}.")
    literal = _PLACEHOLDER.sub("", template)
    if "{" in literal or "}" in literal:
        raise ValueError("A { or } in the file name isn't part of a field.")
    if any(c in literal for c in '\\:*?"<>|\0'):
        raise ValueError('The file name can\'t hold \\ : * ? " < > |.')
    if any(part.strip() in ("", ".", "..") for part in template.split("/")):
        raise ValueError("Each folder in the file name needs a name (no empty part, no . or ..).")
    if "{id}" not in template:
        raise ValueError("The file name must hold {id}: two videos with the same title would otherwise clash, "
                         "and other apps find a video by its id.")
    return template


class Section(BaseModel):
    """A part of the video, in seconds from its start."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0, le=86400 * 7, description="Where it starts.")
    end: float = Field(gt=0, le=86400 * 7, description="Where it ends.")

    @model_validator(mode="after")
    def _order(self):
        if self.end - self.start < 1:
            raise ValueError("A section ends at least a second after it starts.")
        return self

    def label(self) -> str:
        """For a file name: "1m30s-2m45s"."""
        return f"{_stamp(self.start)}-{_stamp(self.end)}"


def _stamp(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, total % 3600 // 60, total % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def to_output_template(template: str, section: Section | None = None) -> str:
    """The template as yt-dlp's output template, the extension added; a
    section's times after the name, so that it never takes the whole
    video's place."""
    parts = []
    position = 0
    for found in _PLACEHOLDER.finditer(template):
        parts.append(template[position:found.start()].replace("%", "%%"))
        parts.append(FIELDS[found.group(1)])
        position = found.end()
    parts.append(template[position:].replace("%", "%%"))
    if section:
        parts.append(f" [{section.label()}]")
    return "".join(parts) + ".%(ext)s"


# yt-dlp writes the characters a file name can't hold as look-alikes
# (full-width "：", "⧸"…): still odd for a share, a script or a player. Each
# is turned into plain characters, or dropped.
_LOOK_ALIKES = str.maketrans({"\uff1a": ":", "\uff5c": "|", "\u29f8": "/", "\u29f9": "\\", "\uff02": '"',
                              "\uff1f": "?", "\uff0a": "*", "\uff1c": "<", "\uff1e": ">"})
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _safe_part(part: str) -> str:
    part = unicodedata.normalize("NFC", part).translate(_LOOK_ALIKES)
    part = "".join(c for c in part if unicodedata.category(c) != "Cc")
    part = _INVISIBLE.sub("", part)
    part = re.sub(r"(?<=\d):(?=\d)", "-", part)       # 12:30 → 12-30
    part = re.sub(r"\s*[:|]\s*", " - ", part)         # "Song: Live" → "Song - Live"
    part = re.sub(r"[/\\]", "-", part)                 # AC/DC → AC-DC
    part = part.replace('"', "'")
    part = re.sub(r"[?*<>]", "", part)
    part = re.sub(r"\s+", " ", part)
    part = re.sub(r"(?: - )+", " - ", part)
    return part.strip(" .-")


def safe_path(relative: str) -> str:
    """A file's path (folders/name.ext) with names any disk, share and
    player takes: no : | / \\ " ? * < > (nor yt-dlp's look-alikes for them),
    no control or invisible characters, no leading or trailing dot, dash or
    space. Letters of every language stay."""
    *folders, name = relative.split("/")
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = _safe_part(stem) or "video"
    parts = [_safe_part(folder) or "_" for folder in folders]
    return "/".join([*parts, f"{stem}.{extension}" if dot else stem])
