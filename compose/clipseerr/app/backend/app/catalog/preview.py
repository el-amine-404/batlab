"""A video's preview for its card: its 360p file with sound (YouTube's format
18), played by the page's own <video>, so no YouTube player shows its title,
logo or "Watch on YouTube" over it. yt-dlp finds the file's address with
YouTube's "android_vr" client, which needs no JavaScript runtime; the address
works for whoever asked for it, so clipseerr streams the file through
(routes/catalog.py, /previews/{id})."""

import yt_dlp

OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "format": "18",
    "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
}


def file_url(youtube_id: str) -> str:
    """The address of the video's 360p file with sound; raises when YouTube won't give one."""
    with yt_dlp.YoutubeDL(OPTIONS) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=False)
    url = info.get("url") or next((f.get("url") for f in info.get("requested_formats") or [] if f.get("url")), None)
    if not url:
        raise LookupError(f"no preview file for {youtube_id}")
    return url
