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

from . import lyricsfiles
from .config import Config
from .db import Database
from .library import sync_lyrics
from .sources import NOTHING, Found, SongQuery


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


def _quarantine(cfg: Config, song: dict, source: str, text: str, reason: str) -> None:
    """A source's answer the filter refused, kept aside with why, never written beside the song."""
    folder = os.path.join(cfg.config_dir, "quarantine")
    os.makedirs(folder, exist_ok=True)
    name = f"{song['id']}-{source}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    with open(os.path.join(folder, name), "w", encoding="utf-8") as out:
        out.write(f"# refused: {reason}\n{text[:lyricsfiles.MAX_BYTES]}")


def search(db: Database, cfg: Config, song_id: int, sources: list, note=None, found=None, actor: str = "") -> str:
    """Ask the sources (Settings, Sources: [(item, LyricsSource)], first to
    last) for one song, and write the best answer by the rules above: synced
    lyrics from any source end the search; plain text, or "instrumental", is
    kept unless a later source has them synced. note(item_id, ok, message)
    records each answer; found(song, how) is told when lyrics are written.
    Returns what happened, in words. Raises when every source failed to
    answer (the next pass tries again)."""
    song = song_or_none(db, song_id)
    if not song:
        return "unknown song"
    artist, title = song["artist"], song["title"]
    if not artist or not title:
        db.run("INSERT OR REPLACE INTO attempts(song_id, at, result, source) VALUES(?, ?, ?, '')",
               (song_id, int(time.time()), "untagged"))
        return "no artist or title to search with"
    if not sources:
        return "no source is on (Settings, Sources)"
    query = SongQuery(artist, title, song["album"], song["duration"], song.get("isrc") or "")
    best, best_from, failed = NOTHING, None, None
    for item, kind in sources:
        try:
            answer = kind.search(query, item["config"])
        except Exception as error:
            failed = error
            if note:
                note(item["id"], False, f"{artist} – {title}: {error}")
            continue
        if answer.kind in ("synced", "plain"):
            checked = lyricsfiles.sanitize(answer.text, song["duration"])
            if checked.refused:
                _quarantine(cfg, song, kind.key, answer.text, checked.refused)
                db.event("filter", f"{artist} – {title}: {kind.label}'s lyrics refused: {checked.refused}", song_id,
                         actor, ok=False, detail="; ".join(checked.removed))
                if note:
                    note(item["id"], False, f"{artist} – {title}: refused, {checked.refused}")
                continue
            if checked.removed:
                db.event("filter", f"{artist} – {title}: {kind.label}'s lyrics cleaned", song_id, actor,
                         detail="; ".join(checked.removed))
            answer = Found(answer.kind, checked.text, answer.reference)
        if note:
            note(item["id"], True, f"{artist} – {title}: {answer.kind}")
        if answer.kind == "synced":
            best, best_from = answer, (item, kind)
            break
        if answer.kind != "none" and best.kind == "none":
            best, best_from = answer, (item, kind)
    if best.kind == "none" and failed is not None:
        raise failed
    label = best_from[1].label if best_from else ""
    db.run("INSERT OR REPLACE INTO attempts(song_id, at, result, source) VALUES(?, ?, ?, ?)",
           (song_id, int(time.time()), best.kind, best_from[1].key if best_from else ""))
    lrc, txt = sidecar(song, ".lrc"), sidecar(song, ".txt")
    if best.kind == "synced":
        if os.path.exists(lrc) and not lyricsfiles.is_ai(lrc):
            return f"{label} has synced lyrics, but a person-synced .lrc is already there"
        if cfg.read_only:
            return f"{label} has synced lyrics (read-only: not written)"
        replaced = os.path.exists(lrc)
        if replaced:
            _backup(cfg, song, lrc)
        lyrics = lyricsfiles.parse_lrc(best.text)
        tags = [f"[re:{label} via Stanzarr]"] + ([f"[#:{best.reference}]"] if best.reference else [])
        lyricsfiles.write_atomic(lrc, lyricsfiles.render_lrc(lyrics.lines, tags))
        sync_lyrics(db, song_id, song["path"], force=True)
        message = f"synced lyrics from {label}" + (" replaced the AI-synced ones (kept in backups)" if replaced else "")
        db.event("lyrics", f"{artist} – {title}: {message}", song_id, actor)
        if found:
            found(song, message)
        return message
    if best.kind == "plain":
        if os.path.exists(txt) or os.path.exists(lrc):
            return f"{label} has plain lyrics only; lyrics are already there"
        if cfg.read_only:
            return f"{label} has plain lyrics (read-only: not written)"
        lyricsfiles.write_atomic(txt, best.text)
        # A .txt can't say who made it: stanzarr keeps it.
        db.run("INSERT OR REPLACE INTO written(path, source, at) VALUES(?, ?, ?)", (txt, best_from[1].key, time.time()))
        sync_lyrics(db, song_id, song["path"], force=True)
        db.event("lyrics", f"{artist} – {title}: plain lyrics from {label}", song_id, actor)
        if found:
            found(song, f"plain lyrics from {label}")
        return f"plain lyrics from {label}"
    if best.kind == "instrumental":
        return f"{label} says it is instrumental"
    return "no source has lyrics for it yet"


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


def save_from_editor(db: Database, cfg: Config, song_id: int, lines: list[dict], actor: str = "") -> int:
    """Write the editor's lines as the song's .lrc. A file that was AI-synced
    stays flagged AI-generated (so a person-synced one may still replace it);
    otherwise it is marked as edited by a person."""
    if cfg.read_only:
        raise ReadOnly("Stanzarr is read-only (STANZARR_READ_ONLY): nothing is written.")
    song = song_or_none(db, song_id)
    if not song:
        raise KeyError(song_id)
    clean = []
    for line in lines:
        t = float(line["t"])
        text = lyricsfiles.HIDDEN.sub("", lyricsfiles.clean(str(line.get("s", "")))).strip().replace("[", "(").replace("]", ")")[:lyricsfiles.MAX_LINE]
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
    sync_lyrics(db, song_id, song["path"], force=True)
    db.event("lyrics", f"{song['artist']} – {song['title']}: edited ({len(clean)} lines)", song_id, actor)
    return len(clean)


def save_ai_result(db: Database, cfg: Config, song_id: int, lines: list[dict], method: str, actor: str = "",
                   force: bool = False, job_created_at: float = 0) -> str:
    """Write an AI sync as the song's .lrc, flagged as AI-generated. Never over
    lyrics synced by a person unless forced / user-queued."""
    song = song_or_none(db, song_id)
    if not song:
        raise KeyError(song_id)
    lrc = sidecar(song, ".lrc")
    if os.path.exists(lrc) and not lyricsfiles.is_ai(lrc) and not force:
        mtime = os.path.getmtime(lrc)
        if job_created_at and mtime > job_created_at:
            return "kept: a person-synced .lrc appeared meanwhile"
        if not job_created_at:
            return "kept: a person-synced .lrc appeared meanwhile"
    if cfg.read_only:
        return "read-only: not written"
    if os.path.exists(lrc):
        _backup(cfg, song, lrc)
    unsure = [str(i + 1) for i, l in enumerate(lines) if l.get("u")]
    confidence = round(100 * (len(lines) - len(unsure)) / max(len(lines), 1))
    tags = ["[by:AI-generated, not checked by a person]", f"[re:Stanzarr AI {date.today()}: {method}]",
            f"[#:verification: {confidence}% verified]",
            "[#:uncertain lines (the methods disagree): " + (", ".join(unsure) or "none") + "]"]
    body = [lyricsfiles.Line(float(l["t"]), lyricsfiles.HIDDEN.sub("", lyricsfiles.clean(str(l["s"]))).strip()[:lyricsfiles.MAX_LINE])
            for l in lines]
    lyricsfiles.write_atomic(lrc, lyricsfiles.render_lrc(body, tags))
    txt = sidecar(song, ".txt")
    if not os.path.exists(txt) or force:
        plain_lines = []
        for i, l in enumerate(lines):
            txt_line = lyricsfiles.HIDDEN.sub("", lyricsfiles.clean(str(l["s"]))).strip()[:lyricsfiles.MAX_LINE]
            if not txt_line:
                continue
            if i > 0 and (l.get("stanza_start") or (float(l.get("t", 0)) - float(lines[i-1].get("t", 0)) >= 2.8)):
                plain_lines.append("")
            plain_lines.append(txt_line)
        plain_body = "\n".join(plain_lines) + "\n"
        lyricsfiles.write_atomic(txt, plain_body)
        db.run(
            "INSERT INTO written(path, source, at) VALUES(?, 'ai', ?)"
            " ON CONFLICT(path) DO UPDATE SET source='ai', at=excluded.at",
            (txt, time.time()),
        )
    sync_lyrics(db, song_id, song["path"], force=True)
    db.event("ai", f"{song['artist']} – {song['title']}: synced by AI ({len(unsure)} unsure lines, {confidence}% verified)", song_id, actor)
    return f"written, {len(unsure)} lines to check ({confidence}% verified)"
