"""What Stanzarr does to a song's lyrics, and the rules it keeps while doing it.

  - A synced .lrc made by a person is never overwritten automatically.
  - An AI-generated .lrc is replaced by a person-synced one from LRCLIB; the
    AI file is kept in backups/ first, so corrections are never lost.
  - Plain lyrics (.txt) are never deleted; synced ones are written beside them.
  - Nothing is written in read-only mode.
"""

import os
import shutil
import time
from datetime import date

from . import lrclib, lyricsfiles
from .config import Config
from .db import Database
from .library import sync_lyrics


class ReadOnly(Exception):
    pass


def song_or_none(db: Database, song_id: int) -> dict | None:
    return db.one("SELECT * FROM songs WHERE id=?", (song_id,))


def sidecar(song: dict, ext: str) -> str:
    return os.path.splitext(song["path"])[0] + ext


def _backup(cfg: Config, song: dict, path: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = os.path.join(cfg.backups_dir, f"{song['id']}-{stamp}-{os.path.basename(path)}")
    shutil.copy2(path, target)
    return target


def needs_search(db: Database, cfg: Config, song_id: int, now: float | None = None) -> bool:
    """A song is asked about when it has no lyrics synced by a person, and it
    was never asked or not for retry_days."""
    now = now or time.time()
    human = db.one("SELECT 1 FROM lyrics WHERE song_id=? AND synced=1 AND source!='ai'", (song_id,))
    if human:
        return False
    attempt = db.one("SELECT at FROM attempts WHERE song_id=?", (song_id,))
    return not attempt or now - attempt["at"] >= cfg.retry_days * 86400


def search_lrclib(db: Database, cfg: Config, song_id: int) -> str:
    """Ask LRCLIB for one song and write what it has, by the rules above.
    Returns what happened, in words."""
    song = song_or_none(db, song_id)
    if not song:
        return "unknown song"
    artist, title = song["artist"], song["title"]
    if not artist or not title:
        db.run("INSERT OR REPLACE INTO attempts(song_id, at, result) VALUES(?, ?, ?)", (song_id, int(time.time()), "untagged"))
        return "no artist or title to search with"
    kind, text, entry = lrclib.lookup(artist, title, song["album"], song["duration"])
    db.run("INSERT OR REPLACE INTO attempts(song_id, at, result) VALUES(?, ?, ?)", (song_id, int(time.time()), kind))
    lrc, txt = sidecar(song, ".lrc"), sidecar(song, ".txt")
    if kind == "synced":
        if os.path.exists(lrc) and not lyricsfiles.is_ai(lrc):
            return "LRCLIB has synced lyrics, but a person-synced .lrc is already there"
        if cfg.read_only:
            return "LRCLIB has synced lyrics (read-only: not written)"
        replaced = os.path.exists(lrc)
        if replaced:
            _backup(cfg, song, lrc)
        lyrics = lyricsfiles.parse_lrc(text)
        tags = ["[re:LRCLIB via Stanzarr]"] + ([f"[#:lrclib id: {entry}]"] if entry else [])
        lyricsfiles.write_atomic(lrc, lyricsfiles.render_lrc(lyrics.lines, tags))
        sync_lyrics(db, song_id, song["path"])
        message = "synced lyrics from LRCLIB" + (" replaced the AI-synced ones (kept in backups)" if replaced else "")
        db.event("lyrics", f"{artist} – {title}: {message}", song_id)
        return message
    if kind == "plain":
        if os.path.exists(txt) or os.path.exists(lrc):
            return "LRCLIB has plain lyrics only; lyrics are already there"
        if cfg.read_only:
            return "LRCLIB has plain lyrics (read-only: not written)"
        lyricsfiles.write_atomic(txt, text)
        sync_lyrics(db, song_id, song["path"])
        db.event("lyrics", f"{artist} – {title}: plain lyrics from LRCLIB", song_id)
        return "plain lyrics from LRCLIB"
    if kind == "instrumental":
        return "LRCLIB says it is instrumental"
    return "LRCLIB has nothing for it yet"


def best_text(db: Database, song_id: int) -> lyricsfiles.Lyrics | None:
    """The lyrics to start from: synced first (by a person, then AI), then plain."""
    song = song_or_none(db, song_id)
    if song:
        sync_lyrics(db, song_id, song["path"])  # files may have changed since the last scan
    rows = db.all("SELECT * FROM lyrics WHERE song_id=? ORDER BY synced DESC,"
                  " CASE source WHEN 'ai' THEN 1 ELSE 0 END, CASE format WHEN 'lrc' THEN 0 WHEN 'txt' THEN 1 ELSE 2 END",
                  (song_id,))
    for row in rows:
        if row["path"].startswith("embedded:"):
            lyrics = lyricsfiles.read_embedded(song["path"]) if song else None
        else:
            try:
                lyrics = lyricsfiles.read_sidecar(row["path"], row["format"])
            except OSError:
                continue
        if lyrics and lyrics.lines:
            return lyrics
    return None


def editor_lines(db: Database, song_id: int) -> dict:
    """The lines for the editor: the synced file as it is, or plain lyrics
    spread evenly over the song as a starting point (all marked unsure)."""
    song = song_or_none(db, song_id)
    lyrics = best_text(db, song_id)
    if not song:
        return {"lines": [], "origin": "none"}
    if lyrics is None:
        return {"lines": [], "origin": "none"}
    origin = lyricsfiles.origin(lyrics)
    if lyrics.synced:
        unsure, checked = _marked(lyrics, "uncertain lines"), _marked(lyrics, "checked lines")
        lines = [{"t": round(l.t or 0, 3), "s": l.s, "u": (i + 1) in unsure, "fixed": (i + 1) in checked}
                 for i, l in enumerate(lyrics.lines)]
        return {"lines": lines, "origin": origin.source, "ai_state": origin.ai_state}
    words = [l.s for l in lyrics.lines if l.s.strip()]
    span = max(1.0, (song["duration"] or len(words) * 4) - 10)
    lines = [{"t": round(5 + span * i / max(1, len(words)), 3), "s": w, "u": True, "fixed": False}
             for i, w in enumerate(words)]
    return {"lines": lines, "origin": "plain", "ai_state": None}


def _marked(lyrics: lyricsfiles.Lyrics, label: str) -> set[int]:
    for note in lyrics.tags.get("#", "").split("\n"):
        key, _, value = note.partition(":")
        if key.strip().lower().startswith(label):
            return {int(n) for n in lyricsfiles.NUMBERS.findall(value)}
    return set()


def save_from_editor(db: Database, cfg: Config, song_id: int, lines: list[dict]) -> int:
    """Write the editor's lines as the song's .lrc. A file that was AI-synced
    stays flagged AI-generated (so a person-synced one may still replace it);
    otherwise it is marked as edited by a person."""
    if cfg.read_only:
        raise ReadOnly()
    song = song_or_none(db, song_id)
    if not song:
        raise KeyError(song_id)
    clean = []
    for line in lines:
        t = float(line["t"])
        text = lyricsfiles.clean(str(line.get("s", ""))).strip().replace("[", "(").replace("]", ")")
        if text and 0 <= t < 24 * 3600:
            clean.append((t, text, bool(line.get("fixed")), bool(line.get("u"))))
    clean.sort(key=lambda c: c[0])
    lrc = sidecar(song, ".lrc")
    before = lyricsfiles.read_sidecar(lrc, "lrc") if os.path.exists(lrc) else None
    # Synced by hand from plain lyrics counts as a person's work; editing an
    # AI file keeps it flagged as AI.
    was_ai = before is not None and lyricsfiles.origin(before).source == "ai"
    if os.path.exists(lrc):
        _backup(cfg, song, lrc)
    unsure = [str(i + 1) for i, c in enumerate(clean) if c[3]]
    checked = [str(i + 1) for i, c in enumerate(clean) if c[2]]
    if was_ai:
        state = "corrected by a person" if checked else "not checked by a person"
        tags = [f"[by:AI-generated, {state}]", f"[re:Stanzarr editor {date.today()}]",
                "[#:uncertain lines (the AI was unsure): " + (", ".join(unsure) or "none") + "]",
                "[#:checked lines: " + (", ".join(checked) or "none") + "]"]
    else:
        tags = ["[by:a person]", f"[re:Stanzarr editor {date.today()}]"]
    lyricsfiles.write_atomic(lrc, lyricsfiles.render_lrc([lyricsfiles.Line(t, s) for t, s, _, _ in clean], tags))
    sync_lyrics(db, song_id, song["path"])
    db.event("lyrics", f"{song['artist']} – {song['title']}: edited ({len(clean)} lines)", song_id)
    return len(clean)


def save_ai_result(db: Database, cfg: Config, song_id: int, lines: list[dict], method: str) -> str:
    """Write an AI sync as the song's .lrc, flagged as AI-generated. Never over
    lyrics synced by a person."""
    song = song_or_none(db, song_id)
    if not song:
        raise KeyError(song_id)
    lrc = sidecar(song, ".lrc")
    if os.path.exists(lrc) and not lyricsfiles.is_ai(lrc):
        return "kept: a person-synced .lrc appeared meanwhile"
    if cfg.read_only:
        return "read-only: not written"
    if os.path.exists(lrc):
        _backup(cfg, song, lrc)
    unsure = [str(i + 1) for i, l in enumerate(lines) if l.get("u")]
    tags = ["[by:AI-generated, not checked by a person]", f"[re:Stanzarr AI sync {date.today()}: {method}]",
            "[#:uncertain lines (the methods disagree): " + (", ".join(unsure) or "none") + "]"]
    body = [lyricsfiles.Line(float(l["t"]), lyricsfiles.clean(str(l["s"])).strip()) for l in lines]
    lyricsfiles.write_atomic(lrc, lyricsfiles.render_lrc(body, tags))
    sync_lyrics(db, song_id, song["path"])
    db.event("ai", f"{song['artist']} – {song['title']}: synced by AI ({len(unsure)} unsure lines)", song_id)
    return f"written, {len(unsure)} lines to check"
