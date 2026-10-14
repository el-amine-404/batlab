"""The download options the page offers, per engine.

Each field is validated here (a fixed set of choices, never free text, so
nothing a user types reaches a command line), described for the page in
FIELDS with the engine flag it stands for, and turned into engine settings by
spotdl_settings() / ytdlp_settings(). Lyrics, file names and folders are not
options: musicguard and Stanzarr own those.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Bitrate = Literal["auto", "128k", "192k", "256k", "320k"]


class SpotdlOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["opus", "m4a", "mp3"] = "opus"
    bitrate: Bitrate = "auto"
    audio_provider: Literal["youtube-music", "youtube", "soundcloud", "bandcamp"] = "youtube-music"
    filter_results: bool = True
    only_verified_results: bool = False
    skip_explicit: bool = False
    sponsor_block: bool = False
    ytm_data: bool = False
    album_type: Literal["any", "album", "single", "compilation"] = "any"


class YtdlpOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["original", "opus", "m4a", "mp3"] = "original"
    bitrate: Bitrate = "auto"
    playlist: bool = False
    sponsor_block: bool = True
    embed_cover: bool = True


MODELS = {"spotdl": SpotdlOptions, "ytdlp": YtdlpOptions}

_BITRATES = [
    {"value": "auto", "label": "Auto (best for the format)"},
    {"value": "128k", "label": "128 kbps"},
    {"value": "192k", "label": "192 kbps"},
    {"value": "256k", "label": "256 kbps"},
    {"value": "320k", "label": "320 kbps"},
]

# What the page renders. "kinds" limits a field to some link kinds; "needs"
# to a detection flag (has_playlist).
FIELDS = {
    "spotdl": [
        {
            "key": "format", "type": "select", "flag": "--format",
            "label": "Format",
            "help": "Opus is YouTube Music's own stream, saved without converting. M4A and MP3 are converted from it.",
            "choices": [
                {"value": "opus", "label": "Opus (original, best)"},
                {"value": "m4a", "label": "M4A (AAC)"},
                {"value": "mp3", "label": "MP3"},
            ],
        },
        {
            "key": "bitrate", "type": "select", "flag": "--bitrate",
            "label": "Bitrate",
            "help": "Only used when converting. A higher number can't add quality the source doesn't have.",
            "choices": _BITRATES,
        },
        {
            "key": "audio_provider", "type": "select", "flag": "--audio",
            "label": "Find the audio on",
            "help": "Where spotDL looks for the matching song.",
            "choices": [
                {"value": "youtube-music", "label": "YouTube Music"},
                {"value": "youtube", "label": "YouTube"},
                {"value": "soundcloud", "label": "SoundCloud"},
                {"value": "bandcamp", "label": "Bandcamp"},
            ],
        },
        {
            "key": "album_type", "type": "select", "flag": "--album-type", "kinds": ["artist"],
            "label": "Artist releases",
            "help": "Which of the artist's releases to download.",
            "choices": [
                {"value": "any", "label": "Everything"},
                {"value": "album", "label": "Albums"},
                {"value": "single", "label": "Singles"},
                {"value": "compilation", "label": "Compilations"},
            ],
        },
        {
            "key": "filter_results", "type": "switch", "flag": "--dont-filter-results (off)",
            "label": "Filter search results",
            "help": "Skip matches whose title or length is too far from the Spotify song.",
        },
        {
            "key": "only_verified_results", "type": "switch", "flag": "--only-verified-results",
            "label": "Only verified results",
            "help": "Accept only official uploads. Safer, but more songs may not be found.",
        },
        {
            "key": "ytm_data", "type": "switch", "flag": "--ytm-data",
            "label": "Use YouTube Music metadata",
            "help": "Take titles and tags from YouTube Music instead of Spotify.",
        },
        {
            "key": "sponsor_block", "type": "switch", "flag": "--sponsor-block",
            "label": "Cut non-music parts",
            "help": "Remove intros and talking marked on SponsorBlock.",
        },
        {
            "key": "skip_explicit", "type": "switch", "flag": "--skip-explicit",
            "label": "Skip explicit songs",
            "help": "Songs Spotify marks explicit are left out.",
        },
    ],
    "ytdlp": [
        {
            "key": "format", "type": "select", "flag": "--audio-format",
            "label": "Format",
            "help": "Original keeps the site's own audio without converting, which is the best quality.",
            "choices": [
                {"value": "original", "label": "Original (no conversion)"},
                {"value": "opus", "label": "Opus"},
                {"value": "m4a", "label": "M4A (AAC)"},
                {"value": "mp3", "label": "MP3"},
            ],
        },
        {
            "key": "bitrate", "type": "select", "flag": "--audio-quality",
            "label": "Bitrate",
            "help": "Only used when converting. A higher number can't add quality the source doesn't have.",
            "choices": _BITRATES,
        },
        {
            "key": "playlist", "type": "switch", "flag": "--yes-playlist", "needs": "has_playlist",
            "label": "Whole playlist",
            "help": "This link points to a video inside a playlist. On: download the whole playlist. Off: just this one.",
        },
        {
            "key": "sponsor_block", "type": "switch", "flag": "--sponsorblock-remove music_offtopic",
            "label": "Cut non-music parts",
            "help": "Remove intros, skits and talking that SponsorBlock users marked in music videos.",
        },
        {
            "key": "embed_cover", "type": "switch", "flag": "--embed-thumbnail",
            "label": "Embed cover",
            "help": "Put the thumbnail in the file as its cover. musicguard replaces it with the album cover when it finds one.",
        },
    ],
}


def describe() -> dict:
    """The option fields with their defaults, for GET /api/options."""
    engines = {}
    for engine, model in MODELS.items():
        defaults = model().model_dump()
        engines[engine] = {
            "label": "spotDL" if engine == "spotdl" else "yt-dlp",
            "fields": [dict(field, default=defaults[field["key"]]) for field in FIELDS[engine]],
        }
    return {"engines": engines}


def parse(engine: str, raw: dict | None):
    return MODELS[engine].model_validate(raw or {})


def spotdl_settings(options: SpotdlOptions, job_dir: str, parallel: int) -> dict:
    """spotDL's downloader settings (spotdl.utils.config.DOWNLOADER_OPTIONS keys)."""
    if options.bitrate != "auto":
        bitrate = options.bitrate
    else:
        # Opus from YouTube Music is copied as is; a conversion picks its own rate.
        bitrate = "disable" if options.format == "opus" else "auto"
    return {
        "output": f"{job_dir}/{{artists}} - {{title}}.{{output-ext}}",
        "format": options.format,
        "bitrate": bitrate,
        "audio_providers": [options.audio_provider],
        # musicguard drops embedded lyrics and Stanzarr writes .lrc files.
        "lyrics_providers": [],
        "generate_lrc": False,
        "filter_results": options.filter_results,
        "only_verified_results": options.only_verified_results,
        "skip_explicit": options.skip_explicit,
        "sponsor_block": options.sponsor_block,
        "ytm_data": options.ytm_data,
        "album_type": None if options.album_type == "any" else options.album_type,
        # Playlist names would replace album tags; musicguard files by album.
        "playlist_numbering": False,
        "playlist_retain_track_cover": False,
        "fetch_albums": False,
        "overwrite": "skip",
        "threads": parallel,
        "print_errors": False,
        "simple_tui": True,
        "log_level": "WARNING",
    }


def ytdlp_settings(options: YtdlpOptions) -> dict:
    """What the yt-dlp runner needs; it builds yt-dlp's own options from this."""
    if options.format == "original":
        codec, quality = "best", None
    else:
        codec = options.format
        if options.bitrate != "auto":
            quality = options.bitrate.upper()  # yt-dlp takes "192K"
        else:
            # VBR: 0 is the best for MP3, 2 a transparent rate for Opus and AAC.
            quality = "0" if codec == "mp3" else "2"
    return {
        "codec": codec,
        "quality": quality,
        "playlist": options.playlist,
        "sponsor_block": options.sponsor_block,
        "embed_cover": options.embed_cover,
    }
