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


def _tags(path: str) -> tuple[str, str, str, float]:
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        return "", "", "", 0.0
    tags = audio.tags or {}

    def first(key: str) -> str:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            return ""
        return str(value[0]).strip() if value else ""

    length = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
    return first("artist") or first("albumartist"), first("album"), first("title"), length


def scan(db: Database, roots: list[str]) -> dict:
    """One pass over the library. Returns counts of what changed."""
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
                row = known.get(path)
                if row and row["size"] == stat.st_size and row["mtime"] == int(stat.st_mtime):
                    song_id = row["id"]
                else:
                    artist, album, title, duration = _tags(path)
                    rel = os.path.relpath(path, root)
                    title = title or stem
                    if row:
                        db.run("UPDATE songs SET size=?, mtime=?, artist=?, album=?, title=?, duration=? WHERE id=?",
                               (stat.st_size, int(stat.st_mtime), artist, album, title, duration, row["id"]))
                        song_id = row["id"]
                        updated += 1
                    else:
                        song_id = db.insert(
                            "INSERT INTO songs(path, root, rel, size, mtime, artist, album, title, duration, added_at)"
                            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (path, root, rel, stat.st_size, int(stat.st_mtime), artist, album, title, duration,
                             int(time.time())))
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
