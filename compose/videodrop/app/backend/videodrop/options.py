"""How a video is downloaded (quality, codec, container, subtitles…) and how
its file is named (a destination's template). videodrop's own words: callers
never see yt-dlp's; ytdlp.py translates.

Options come in three layers, the first set wins: the job's own, its
destination's, the settings' defaults (resolve())."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    lyrics: bool = Field(description="A music video: its synced lyrics (LRCLIB) as a subtitle track named Lyrics, "
                                     "when there are some. The job's song names the song; else it is guessed from "
                                     "the video's title.")
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


def to_output_template(template: str) -> str:
    """The template as yt-dlp's output template, the extension added."""
    parts = []
    position = 0
    for found in _PLACEHOLDER.finditer(template):
        parts.append(template[position:found.start()].replace("%", "%%"))
        parts.append(FIELDS[found.group(1)])
        position = found.end()
    parts.append(template[position:].replace("%", "%%"))
    return "".join(parts) + ".%(ext)s"
