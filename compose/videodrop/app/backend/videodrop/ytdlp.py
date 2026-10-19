"""videodrop's options in yt-dlp's words: the one place that knows them. A
pure function, so that a test reads what a download would run with."""

import hashlib
import json
from pathlib import Path

from .egress import FFMPEG_PROTOCOLS
from .options import VideoOptions

_CODECS = {"h264": "vcodec:h264", "h265": "vcodec:h265", "vp9": "vcodec:vp9", "av1": "vcodec:av01"}
# Streams that go into the container without converting, preferred.
_EXTENSIONS = {"mp4": "ext:mp4:m4a", "webm": "ext:webm:webm"}
# Containers a cover can be embedded in.
_COVERS = ("mp4", "mkv")


def params(options: VideoOptions, output: str, work_dir: Path, *, rate_limit: float | None = None,
           max_filesize: float | None = None, cookies: str | None = None, deno: str | None = None,
           proxy: str | None = None) -> dict:
    """YoutubeDL's parameters, without the logger and the hooks (runner.py
    adds them). output: the output template (options.to_output_template)."""
    sort = []
    if options.quality != "best":
        sort.append(f"res:{options.quality}")
    if options.codec in _CODECS:
        sort.append(_CODECS[options.codec])
    if options.container in _EXTENSIONS:
        sort.append(_EXTENSIONS[options.container])

    processors = []
    subtitles = bool(options.subtitles)
    if subtitles:
        processors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
    if options.embed_metadata or options.embed_chapters:
        processors.append({"key": "FFmpegMetadata", "add_metadata": options.embed_metadata,
                           "add_chapters": options.embed_chapters})
    cover = options.embed_thumbnail and options.container in _COVERS
    if cover:
        processors.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        processors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    result = {
        "format": "bv*+ba/b",
        "format_sort": sort,
        "outtmpl": {"default": output},
        "paths": {"home": str(work_dir), "temp": str(work_dir / ".tmp")},
        # Names every disk and share takes.
        "windowsfilenames": True,
        "overwrites": False,
        "continuedl": True,
        "noplaylist": not options.playlist,
        "playlistend": options.playlist_limit,
        # One video of a playlist failing doesn't stop the others.
        "ignoreerrors": "only_download",
        # Never the generic extractor: it would fetch any address (links.py).
        "allowed_extractors": ["default", "-generic"],
        "writesubtitles": subtitles,
        "writeautomaticsub": subtitles and options.auto_subtitles,
        "subtitleslangs": list(options.subtitles),
        "writethumbnail": cover,
        "postprocessors": processors,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "cachedir": str(work_dir / ".cache"),
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
    }
    if options.container != "original":
        result["merge_output_format"] = options.container
    if rate_limit:
        result["ratelimit"] = rate_limit
    if max_filesize:
        result["max_filesize"] = max_filesize
    if cookies:
        result["cookiefile"] = cookies
    if deno:
        result["js_runtimes"] = {"deno": {"path": deno}}
    if proxy:
        # Every request through videodrop's egress proxy (egress.py); ffmpeg, when it downloads, too, and
        # only over the web: no file, rtmp, rtsp or udp it would reach on its own.
        result["proxy"] = proxy
        result["external_downloader_args"] = {"ffmpeg_i": ["-protocol_whitelist", FFMPEG_PROTOCOLS]}
    return result


def fingerprint(info: dict) -> str | None:
    """What the site serves for these options, as a short hash: the video
    (site, id, length, when it was last changed when the site says) and the
    exact streams picked (their ids, size, codecs, bitrate). The same
    fingerprint later means the same file would come down again; a
    re-encoded or trimmed video, another quality or a new stream doesn't
    have it. None for a playlist."""
    if info.get("_type") == "playlist" or not info.get("id"):
        return None
    picked = info.get("requested_formats") or [info]
    streams = [[f.get("format_id"), f.get("width"), f.get("height"), f.get("vcodec"), f.get("acodec"),
                f.get("filesize") or (round(f["filesize_approx"], -5) if f.get("filesize_approx") else None),
                round(f.get("tbr") or 0)] for f in picked]
    version = info.get("modified_timestamp") or info.get("modified_date")
    raw = json.dumps([info.get("extractor_key"), info["id"], round(info.get("duration") or 0), version, streams])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
