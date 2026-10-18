"""Stands in for videodrop.runner in the tests: the same protocol, no
network. What it does depends on the video id in the link:
okokokokokN a video (N: a digit), flakyflaky1 a 503, gonegonegon unavailable,
slowslowslo downloads for a minute, playlist000 two videos."""

import json
import sys
import time
from pathlib import Path


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


config = json.loads(sys.stdin.readline())
work = Path(config["work_dir"])
video = config["url"].split("v=")[-1][:11]


def make(index, total, ident, title):
    emit("item", index=index, total=total, id=ident, title=title, uploader="Fake Artist",
         thumbnail=f"https://i.ytimg.com/vi/{ident}/mqdefault.jpg", duration=200)
    emit("stage", stage="downloading")
    emit("progress", index=index, downloaded=500, total=1000.4, speed=100.0, eta=5)  # sizes come with decimals
    emit("stage", stage="processing")
    path = Path("Fake Artist") / f"{title} [{ident}].mp4"
    (work / path).parent.mkdir(parents=True, exist_ok=True)
    (work / path).write_bytes(b"0123456789" * 100)
    if config["options"].get("lyrics"):
        song = config.get("song") or {"artist": "Fake Artist", "title": title}
        emit("note", message=f"Lyrics added as subtitles (LRCLIB: {song['artist']} – {song['title']}).")
    emit("file", path=str(path), checks={"ok": True, "problems": [], "summary": "AV1 1920×1080 · Opus · 3:20",
                                        "duration": 200.0})


if video.startswith("okokokokok"):
    # Like the real runner: the site's fingerprint first (here: the video and its quality), then the answer.
    emit("fingerprint", value=f"{video}:{config['options']['quality']}")
    if json.loads(sys.stdin.readline() or "{}").get("reuse"):
        emit("reused", job="x")
        sys.exit(0)
    make(0, 1, video, f"Song {video[-1]}")
elif video == "playlist000":
    make(0, 2, "aaaaaaaaaa1", "First")
    make(1, 2, "aaaaaaaaaa2", "Second")
elif video == "flakyflaky1":
    emit("error", message="ERROR: [youtube] flakyflaky1: HTTP Error 503: Service Unavailable")
    sys.exit(1)
elif video == "gonegonegon":
    emit("error", message="ERROR: [youtube] gonegonegon: Video unavailable")
    sys.exit(1)
elif video == "slowslowslo":
    emit("item", index=0, total=1, id=video, title="Slow", uploader="Fake", thumbnail=None, duration=1)
    for step in range(600):
        emit("progress", index=0, downloaded=step, total=600.7, speed=1.0, eta=600 - step)
        time.sleep(0.1)
    sys.exit(1)
