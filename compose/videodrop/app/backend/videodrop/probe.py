"""What a link would download, without downloading it (python -m
videodrop.probe): the video's title, picture and source, and the format the
options pick, with its size and bitrate. A process of its own, like
runner.py: yt-dlp reads the site there.

stdin: {"url", "options", "output", "cookies", "deno"}. stdout: one JSON
object, {"ok": true, "info": …} or {"ok": false, "message": …}."""

import json
import os
import re
import sys
from pathlib import Path

from .options import safe_path
from .ytdlp import fingerprint

ENTRIES_SHOWN = 10


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def describe_format(info: dict) -> dict:
    """The streams the options pick (a video and a sound joined, or one
    file), as a person reads them."""
    picked = info.get("requested_formats") or [info]
    video = next((f for f in picked if f.get("vcodec") not in (None, "none")), None)
    audio = next((f for f in picked if f.get("acodec") not in (None, "none")), None)
    sizes = [f.get("filesize") or f.get("filesize_approx") for f in picked]
    exact = all(f.get("filesize") for f in picked)
    size = int(sum(sizes)) if all(sizes) else None
    estimated_from = "site" if size else None
    duration = _number(info.get("duration"))
    if size is None and duration:
        # The site gives no size (streams cut in pieces, HLS): bitrate × length.
        if len(picked) > 1:
            kbps = ((video or {}).get("vbr") or (video or {}).get("tbr") or 0) + ((audio or {}).get("abr") or (audio or {}).get("tbr") or 0)
        else:
            kbps = picked[0].get("tbr") or 0
        if kbps:
            size, estimated_from = int(kbps * 1000 / 8 * duration), "bitrate"
    return {
        "width": _number((video or {}).get("width")),
        "height": _number((video or {}).get("height")),
        "fps": _number((video or {}).get("fps")),
        "vcodec": (video or {}).get("vcodec"),
        "acodec": (audio or {}).get("acodec"),
        "video_kbps": _number((video or {}).get("vbr") or (video or {}).get("tbr")),
        "audio_kbps": _number((audio or {}).get("abr") or ((audio or {}).get("tbr") if audio is not video else None)),
        "dynamic_range": (video or {}).get("dynamic_range"),
        "container": info.get("ext"),
        "size_bytes": size,
        "size_exact": bool(size and exact and estimated_from == "site"),
        "size_from": estimated_from,
        "format_id": info.get("format_id"),
    }


def describe(info: dict, filename: str | None) -> dict:
    subtitles = sorted(k for k in (info.get("subtitles") or {}) if k != "live_chat")
    automatic = sorted(info.get("automatic_captions") or {})
    heights = sorted({f["height"] for f in info.get("formats") or [] if _number(f.get("height"))
                      and f.get("vcodec") not in (None, "none")}, reverse=True)
    return {
        "kind": "video",
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "webpage_url": info.get("webpage_url"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "thumbnail": info.get("thumbnail") if str(info.get("thumbnail") or "").startswith("https://") else None,
        "duration": _number(info.get("duration")),
        "upload_date": info.get("upload_date"),
        "views": _number(info.get("view_count")),
        "is_live": bool(info.get("is_live")),
        "age_limit": _number(info.get("age_limit")),
        "artist": info.get("artist") or info.get("creator"),
        "track": info.get("track"),
        "format": describe_format(info),
        "heights": heights,
        "subtitles": subtitles,
        "automatic_captions": len(automatic),
        "automatic_has_english": any(code.split("-")[0] == "en" for code in automatic),
        "filename": filename,
        "fingerprint": fingerprint(info),
        "entries": None,
    }


def main() -> int:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    config = json.load(sys.stdin)
    if config.get("proxy"):
        from . import egress

        os.environ.update(egress.environment(config["proxy"]))
        egress.lock(config["proxy"])

    import yt_dlp
    from yt_dlp.utils import DownloadError

    from .options import VideoOptions
    from .ytdlp import params

    options = VideoOptions.model_validate(config["options"])
    home = Path("/nonexistent-probe")
    ydl_params = params(options, config["output"], home, cookies=config.get("cookies"), deno=config.get("deno"),
                        proxy=config.get("proxy"))
    ydl_params.update(simulate=True, skip_download=True, quiet=True, no_warnings=True, cachedir=False)
    if options.playlist:
        ydl_params["extract_flat"] = "in_playlist"

    def answer(result: dict) -> int:
        out.write(json.dumps(result, ensure_ascii=False, default=str))
        out.flush()
        return 0 if result["ok"] else 1

    try:
        with yt_dlp.YoutubeDL(ydl_params) as ydl:
            info = ydl.extract_info(config["url"], download=False)
            if info is None:
                return answer({"ok": False, "message": "Nothing to download at this link."})
            if info.get("_type") == "playlist":
                entries = [e for e in info.get("entries") or [] if e]
                limit = options.playlist_limit
                return answer({"ok": True, "info": {
                    "kind": "playlist", "id": info.get("id"), "title": info.get("title"),
                    "uploader": info.get("uploader") or info.get("channel"),
                    "channel_url": info.get("channel_url") or info.get("uploader_url"),
                    "webpage_url": info.get("webpage_url"), "extractor": info.get("extractor_key"),
                    "thumbnail": next((t.get("url") for t in reversed(info.get("thumbnails") or [])
                                       if str(t.get("url") or "").startswith("https://")), None),
                    "count": len(entries), "taken": min(len(entries), limit),
                    "entries": [{"title": e.get("title"), "id": e.get("id"), "duration": _number(e.get("duration"))}
                                for e in entries[:ENTRIES_SHOWN]],
                }})
            filename = safe_path(os.path.relpath(ydl.prepare_filename(info), home))
            return answer({"ok": True, "info": describe(info, filename)})
    except DownloadError as exc:
        return answer({"ok": False, "message": re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*[\w-]+:\s*)?", "", str(exc))[:500]})
    except Exception as exc:  # yt-dlp's own bug: said, not hidden
        return answer({"ok": False, "message": f"yt-dlp failed: {exc.__class__.__name__}: {exc}"[:500]})


if __name__ == "__main__":
    sys.exit(main())
