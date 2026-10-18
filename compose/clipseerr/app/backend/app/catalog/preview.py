"""A video's preview for its card: its 360p file with sound (YouTube's format
18), played by the page's own <video>, so no YouTube player shows its title,
logo or "Watch on YouTube" over it. yt-dlp finds the file's address with
YouTube's "android_vr" client, which needs no JavaScript runtime; the address
works for whoever asked for it, so clipseerr streams the file through
(routes/catalog.py, /previews/{id})."""

import time

import yt_dlp

OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    # The 360p mp4 with sound; when YouTube's answer lacks it (now and then it
    # does), the smallest file that has both picture and sound.
    "format": "18/worst[height>=240][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]",
    "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
}


def file_url(youtube_id: str) -> dict:
    """{"url", "headers"} of the video's file with sound, the headers a player
    sends for it; raises when YouTube won't give one, twice."""
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(OPTIONS) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=False)
            break
        except yt_dlp.utils.DownloadError:
            # YouTube's answer varies from one call to the next: once more, a moment later.
            if attempt:
                raise
            time.sleep(0.5)
    url = info.get("url")
    if not url:
        raise LookupError(f"no preview file for {youtube_id}")
    return {"url": url, "headers": {k: v for k, v in (info.get("http_headers") or {}).items() if k.lower() != "accept"}}
