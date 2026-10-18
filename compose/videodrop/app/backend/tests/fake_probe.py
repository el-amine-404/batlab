"""Stands in for videodrop.probe in the tests: the same protocol, no
network. gonegonegon is unavailable; any other id is a 3:20 video."""

import json
import sys

config = json.load(sys.stdin)
video = config["url"].split("v=")[-1][:11]
if video == "gonegonegon":
    print(json.dumps({"ok": False, "message": "Video unavailable"}))
    sys.exit(1)
print(json.dumps({"ok": True, "info": {
    "kind": "video", "id": video, "title": "Song", "uploader": "Fake Artist",
    "channel_url": "https://www.youtube.com/@fake", "webpage_url": f"https://www.youtube.com/watch?v={video}",
    "extractor": "Youtube", "thumbnail": None, "duration": 200, "format": {
        "width": 1920, "height": 1080, "fps": 25, "vcodec": "av01", "acodec": "opus", "video_kbps": 1500,
        "audio_kbps": 130, "dynamic_range": "SDR", "container": config["options"]["container"],
        "size_bytes": 40_000_000, "size_exact": False, "format_id": "399+251"},
    "heights": [1080, 720], "subtitles": ["en"], "automatic_captions": 150, "automatic_has_english": True,
    "filename": f"Fake Artist/Song [{video}].mp4", "fingerprint": f"{video}:{config['options']['quality']}",
    "entries": None}}))
