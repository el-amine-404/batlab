"""What a request keeps of the video it is for (its media): a snapshot of the
catalog's details, with the names a service searches by. For audio that is
the song's official audio when one matched (its album, its clean title), else
the video's."""

from .catalog.models import VideoDetail


def snapshot(video: VideoDetail, kind: str) -> dict:
    audio = video.audio
    use_audio = kind == "audio" and audio is not None
    return {
        "video_key": video.id,
        "video_id": video.youtube_id,
        "audio_id": audio.youtube_id if audio else None,
        "title": audio.title if use_audio else video.title,
        "artist": audio.artist if use_audio else video.artist,
        "album": audio.album if use_audio else None,
        "year": (audio.year if use_audio and audio.year else (video.released or "")[:4] or None),
        "duration": (audio.duration if use_audio and audio.duration else video.duration),
        "thumbnail": video.thumbnail,
        "video_title": video.original_title or video.title,
        "artist_id": next((a.id for a in video.artists if a.id), None),
        "links": video.links,
    }
