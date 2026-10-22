"""Is a downloaded file the Spotify song it was downloaded for?

spotDL picks a YouTube upload by title and length; a re-upload can carry the
right title and length over other audio (on 2026-10-14 "ElGrandeToto - la
paille (Ft Farid Bang)" was a Bhojpuri song). The file is compared with
Spotify's own 30 s preview: Chromaprint fingerprints of both (ffmpeg's
chromaprint muxer), the preview slid along the file, the share of equal bits
at the best offset. The same song scores about 0.9, another about 0.55. The
preview and the fingerprints are batlab_common's (lib/batlab-common), as in
musicguard.

Standard library and batlab_common only: this runs in spotDL's virtualenv.
"""

from batlab_common import fingerprint, http, previews

OTHER = fingerprint.OTHER  # below: another recording


def score(path: str, track_id: str) -> float | None:
    """How much the file sounds like Spotify's preview, or None when there is
    no preview to compare with (or Spotify could not be reached)."""
    try:
        url = previews.spotify(track_id)
        return previews.score(path, url) if url else None
    except (OSError, http.TooLarge):  # Spotify unreachable or odd; urllib's errors are OSErrors
        return None


def candidates(song, tried: set[str], limit: int = 4) -> list[str]:
    """Other YouTube uploads of the song, of its length within 7 s."""
    import yt_dlp  # spotDL's own, installed with it

    artists = ", ".join(song.artists or []) or song.artist or ""
    query = f"ytsearch15:{artists} - {song.name}"
    options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        entries = (ydl.extract_info(query, download=False) or {}).get("entries") or []
    found = []
    for entry in entries:
        url = f"https://www.youtube.com/watch?v={entry.get('id')}"
        duration = entry.get("duration")
        if not entry.get("id") or url in tried or duration is None:
            continue
        if abs(float(duration) - float(song.duration)) <= 7:
            found.append(url)
        if len(found) >= limit:
            break
    return found
