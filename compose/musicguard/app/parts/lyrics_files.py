"""Lyrics beside the tracks: fetched, followed when a track is renamed, or set aside.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import os
import time

import carry
import layout
import lyrics
import media
import tags as tagging

from settings import AUDIO_EXTENSIONS, LYRICS_RETRY_SECONDS, LYRIC_EXTENSIONS, ORPHAN_SECONDS
from common import log


class LyricsMixin:
    def has_lyrics(self, path):
        stem = os.path.splitext(path)[0]
        return any(os.path.exists(stem + e) for e in LYRIC_EXTENSIONS)

    def has_synced_lyrics(self, path):
        """Synced by a person: an AI-synced .lrc (lyrics.AI_MARK) is asked
        about again like plain lyrics, and replaced by LRCLIB's when a person
        has synced the song there."""
        sidecar = os.path.splitext(path)[0] + ".lrc"
        return os.path.exists(sidecar) and not lyrics.is_ai(sidecar)

    def fetch_lyrics(self, path, found, seconds):
        """Synced lyrics (.lrc) when LRCLIB has them, else plain (.txt). A song
        with only plain lyrics is asked again every LYRICS_RETRY_SECONDS:
        players only scroll synced ones, and people add timings to LRCLIB.
        The .txt is kept beside a new .lrc (Navidrome prefers the .lrc), so
        lyrics written by hand are never lost."""
        if self.has_synced_lyrics(path):
            return
        attempted = self.state["lyrics"].get(path, 0)
        if time.time() - attempted < LYRICS_RETRY_SECONDS:
            return
        artist = layout.first(found, "artist")
        title = layout.first(found, "title")
        if not artist or not title or artist == "Unknown Artist":
            return
        try:
            kind, text = lyrics.lookup(artist, title, layout.first(found, "album"), seconds)
        except Exception as error:
            log(f"lyrics lookup failed for {path}: {error}")
            return
        self.state["lyrics"][path] = int(time.time())
        if kind == "plain" and self.has_lyrics(path):
            return  # still no timings; the plain lyrics are already there
        if kind in ("synced", "plain"):
            sidecar = os.path.splitext(path)[0] + (".lrc" if kind == "synced" else ".txt")
            with open(sidecar, "w", encoding="utf-8") as out:
                out.write(text)
            self.event("lyrics", path, kind)

    def lyrics_for_existing(self, path):
        if not self.config.lyrics or self.has_synced_lyrics(path):
            return
        if time.time() - self.state["lyrics"].get(path, 0) < LYRICS_RETRY_SECONDS:
            return
        try:
            info = media.probe(path)
            items = tagging.read_mutagen(path)
            found, _ = tagging.collect(items if items is not None else tagging.read_ffprobe(info))
            self.fetch_lyrics(path, found, media.duration(info))
        except media.MediaError:
            pass

    def orphaned(self, path, stat):
        """A lyrics sidecar whose track has been gone for an hour."""
        stem = os.path.splitext(path)[0]
        if any(os.path.exists(stem + e) for e in AUDIO_EXTENSIONS):
            return False
        return time.time() - stat.st_mtime > ORPHAN_SECONDS

    def follow_lyrics(self, path, extension, root):
        """Lyrics whose track has gone. Lidarr renames tracks after import (a
        multi-disc release gets "1-01 - ...", an album folder a new name) and
        leaves the lyrics it did not import behind: they follow their track
        when it is the one of their title nearby (carry.renamed_track), and
        replace that track's own lyrics only when better (a person's or
        LRCLIB's over AI or unknown ones). Otherwise, or when the track is
        really gone, they go to quarantine: hand-synced lyrics are never
        deleted outright."""
        track = carry.renamed_track(path, AUDIO_EXTENSIONS)
        target = os.path.splitext(track)[0] + extension if track else None
        if target and os.path.exists(target):
            mine, theirs = carry.read(path) or "", carry.read(target) or ""
            if extension == ".lrc" and carry.better_lyrics(mine, theirs):
                self.quarantine(target, "REPLACED_LYRICS", f"better lyrics followed the track from {path}",
                                root, announce=False)
            else:
                target = None
        if target and not os.path.exists(target):
            self.event("handed-over", target, "LYRICS", f"followed its renamed track, from {path}")
            if not self.config.dry_run:
                os.rename(path, target)
                self.state["sidecars"].pop(path, None)
            return
        self.quarantine(path, "ORPHANED_LYRICS", "no track of this name or title nearby" if not track
                        else f"{track} has lyrics as good", root, announce=False)
        self.state["sidecars"].pop(path, None)
