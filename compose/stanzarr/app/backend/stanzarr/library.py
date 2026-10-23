"""Keeps the database in step with the music folders: every song, and every
lyrics file beside it or inside it. A rescan only reads what changed."""

import logging
import os
import time

import mutagen

from . import lyricsfiles
from .db import Database

log = logging.getLogger("stanzarr.library")

# What a song's lyrics are, best first. Used for the dashboard and filters.
STATUSES = ("synced", "ai_corrected", "ai_checked", "ai_unchecked", "plain", "instrumental", "missing")


class Tags(dict):
    """artist, album, title, duration, and what identifies the recording:
    albumartist, isrc, mbid (MusicBrainz's recording, as musicguard writes it)."""


def _tags(path: str) -> Tags:
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        return Tags(artist="", album="", title="", duration=0.0, albumartist="", isrc="", mbid="")
    tags = audio.tags or {}

    def first(key: str) -> str:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            return ""
        return str(value[0]).strip() if value else ""

    length = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
    return Tags(artist=first("artist") or first("albumartist"), album=first("album"), title=first("title"),
                duration=length, albumartist=first("albumartist"), isrc=first("isrc").upper()[:15],
                mbid=first("musicbrainz_trackid")[:36])


def scan(db: Database, roots: list[str], progress=None) -> dict:
    """One pass over the library. Returns counts of what changed.
    progress(seen, folder) is told as it goes, for the Activity page."""
    started = time.time()
    known = {r["path"]: r for r in db.all("SELECT id, path, size, mtime FROM songs")}
    seen: set[str] = set()
    added = updated = 0
    for root in roots:
        if not os.path.isdir(root):
            log.warning("library folder %s does not exist", root)
            continue
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            names = set(filenames)
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                stem, ext = os.path.splitext(name)
                if ext.lower() not in lyricsfiles.AUDIO:
                    continue
                path = os.path.join(directory, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                seen.add(path)
                if progress and len(seen) % 25 == 0:
                    progress(len(seen), directory)
                row = known.get(path)
                if row and row["size"] == stat.st_size and row["mtime"] == int(stat.st_mtime):
                    song_id = row["id"]
                else:
                    tags = _tags(path)
                    rel = os.path.relpath(path, root)
                    artist, album, title, duration = tags["artist"], tags["album"], tags["title"] or stem, tags["duration"]
                    ids = (tags["albumartist"], tags["isrc"], tags["mbid"])
                    if row:
                        db.run("UPDATE songs SET size=?, mtime=?, artist=?, album=?, title=?, duration=?, albumartist=?,"
                               " isrc=?, mbid=? WHERE id=?",
                               (stat.st_size, int(stat.st_mtime), artist, album, title, duration, *ids, row["id"]))
                        song_id = row["id"]
                        updated += 1
                    else:
                        song_id = db.insert(
                            "INSERT INTO songs(path, root, rel, size, mtime, artist, album, title, duration, added_at,"
                            " albumartist, isrc, mbid) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (path, root, rel, stat.st_size, int(stat.st_mtime), artist, album, title, duration,
                             int(time.time()), *ids))
                        added += 1
                sync_lyrics(db, song_id, path, names)
    removed = 0
    for path, row in known.items():
        if path not in seen:
            db.run("DELETE FROM songs WHERE id=?", (row["id"],))
            removed += 1
    if added or removed:
        db.event("scan", f"Library scanned: {added} new, {updated} changed, {removed} gone")
    return {"added": added, "updated": updated, "removed": removed, "seconds": round(time.time() - started, 1)}


def sync_lyrics(db: Database, song_id: int, audio_path: str, names: set[str] | None = None, force: bool = False) -> None:
    """Record the lyrics files of one song; a file is only read when it changed,
    or always with force (after Stanzarr wrote one itself: two writes within one
    tick of the file system's clock have the same time)."""
    directory = os.path.dirname(audio_path)
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    if names is None:
        try:
            names = set(os.listdir(directory))
        except OSError:
            names = set()
    existing = {r["path"]: r for r in db.all("SELECT * FROM lyrics WHERE song_id=?", (song_id,))}
    present: set[str] = set()
    for ext, fmt in lyricsfiles.SIDECARS.items():
        if stem + ext not in names:
            continue
        path = os.path.join(directory, stem + ext)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        present.add(path)
        row = existing.get(path)
        if row and not force and row["mtime"] == int(stat.st_mtime_ns // 1000):
            continue
        try:
            lyrics = lyricsfiles.read_sidecar(path, fmt)
        except OSError:
            continue
        _store(db, song_id, path, fmt, lyrics, int(stat.st_mtime_ns // 1000))
    # Embedded lyrics: read again only when the audio file changed.
    key = "embedded:" + audio_path
    song = db.one("SELECT mtime FROM songs WHERE id=?", (song_id,))
    row = existing.get(key)
    if row and song and row["mtime"] == song["mtime"]:
        present.add(key)
    else:
        embedded = lyricsfiles.read_embedded(audio_path)
        if embedded and embedded.lines:
            present.add(key)
            _store(db, song_id, key, "embedded", embedded, song["mtime"] if song else 0)
    for path in existing:
        if path not in present:
            db.run("DELETE FROM lyrics WHERE path=?", (path,))


def _store(db: Database, song_id: int, path: str, fmt: str, lyrics: lyricsfiles.Lyrics, mtime: int) -> None:
    origin = lyricsfiles.origin(lyrics)
    db.run(
        "INSERT INTO lyrics(song_id, path, format, synced, language, source, ai_state, lines, unsure, checked, mtime)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(path) DO UPDATE SET format=excluded.format, synced=excluded.synced, language=excluded.language,"
        " source=excluded.source, ai_state=excluded.ai_state, lines=excluded.lines, unsure=excluded.unsure,"
        " checked=excluded.checked, mtime=excluded.mtime",
        (song_id, path, fmt, int(lyrics.synced), lyricsfiles.detect_language(lyrics), origin.source,
         origin.ai_state, len(lyrics.lines), origin.unsure, origin.checked, mtime))


STATUS_SQL = """
CASE
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.source!='ai') THEN 'synced'
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.ai_state='corrected') THEN 'ai_corrected'
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.ai_state='checked') THEN 'ai_checked'
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1) THEN 'ai_unchecked'
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id) THEN 'plain'
  WHEN (SELECT result FROM attempts a WHERE a.song_id=s.id)='instrumental' THEN 'instrumental'
  ELSE 'missing'
END
"""


# The quality ladder (Settings, Quality), best last: what a song's best lyrics
# are worth. A song is asked about again until it reaches the cutoff;
# instrumental songs have nothing to reach.
LEVELS = {"none": 0, "plain": 1, "ai": 2, "ai_checked": 3, "synced": 4, "words": 5}
RANK_SQL = """
CASE
  WHEN NOT EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id)
       AND (SELECT result FROM attempts a WHERE a.song_id=s.id)='instrumental' THEN 9
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.source!='ai' AND l.format='elrc') THEN 5
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.source!='ai') THEN 4
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.ai_state IN ('checked','corrected')) THEN 3
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1) THEN 2
  WHEN EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id) THEN 1
  ELSE 0
END
"""
