"""Navidrome's scan, and "Ready to play" once it has the new music.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import os
import re

import mutagen
import navidrome

from batlab_common import notify

from settings import READY_ALBUMS, READY_SONGS
from common import log


def minutes(seconds):
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02}:{rest % 60:02}" if hours else f"{rest // 60}:{rest % 60:02}"


def quality(path, track):
    """"FLAC 24-bit/96 kHz", "Opus 160 kb/s"."""
    if track.codec in ("flac", "alac"):
        try:
            info = mutagen.File(path).info
            depth, rate = getattr(info, "bits_per_sample", 0), getattr(info, "sample_rate", 0)
        except Exception:
            depth = rate = 0
        return track.codec.upper() + (f" {depth}-bit/{rate / 1000:g} kHz" if depth and rate else "")
    name = {"opus": "Opus", "aac": "AAC", "vorbis": "Vorbis", "mp3": "MP3"}.get(track.codec, "")
    return f"{name} {track.bitrate // 1000} kb/s".strip() if track.bitrate else name


def song_title(filename):
    """"16 - Trop beau.opus" or "1-01 - Trop beau.flac" → "Trop beau"."""
    stem = os.path.splitext(filename)[0]
    return re.sub(r"^\d+(?:-\d+)?\s*-\s*", "", stem) or stem


class AnnounceMixin:
    def scan_navidrome(self):
        """After a library pass: have Navidrome index what changed, but only
        when every audio file in the libraries has been checked."""
        if not self.library_changed or not self.config.navidrome_user or self.config.dry_run:
            return
        waiting = self.unchecked()
        if waiting:
            log(f"navidrome scan waits: {waiting} file(s) not checked yet")
            return
        try:
            navidrome.start_scan(self.config.navidrome_url, self.config.navidrome_user,
                                 self.config.navidrome_password)
        except Exception as error:
            if not self.scan_reported:
                self.scan_reported = True
                self.event("error", self.config.navidrome_url, "NAVIDROME_SCAN", str(error)[:300])
            return
        self.scan_reported = False
        self.library_changed = False
        log("navidrome scan done")
        self.announce_ready()

    def announce_ready(self):
        """Messages after a scan: one per album that just became playable
        ("Ready to play: Artist – Album"), with its new songs."""
        new, self.ready = self.ready, []
        albums = {}
        for path in new:
            if not os.path.exists(path):
                continue  # replaced or quarantined since
            for root in self.config.roots:
                if path.startswith(root + os.sep):
                    parts = os.path.relpath(path, root).split(os.sep)
                    if len(parts) >= 3:
                        albums.setdefault(f"{parts[0]} – {parts[1]}", []).append(path)
        if not albums or not self.config.notify_url:
            return
        names = sorted(albums)
        messages = [(f"Ready to play: {name}", self.album_text(albums[name])) for name in names[:READY_ALBUMS]]
        if len(names) > READY_ALBUMS:
            rest = names[READY_ALBUMS:]
            messages.append((f"Ready to play: {len(rest)} more albums",
                             "\n".join(f"- {name} ({len(albums[name])})" for name in rest)))
        for title, body in messages:
            try:
                notify.send(self.config.notify_url, title, body, tag=self.config.notify_ready, kind="success")
            except notify.NotSent as error:
                log(f"ready notification failed: {error}")
                return

    def album_text(self, paths):
        """"`16` **Trop beau** · 3:24 · Opus 160 kb/s" per song, then the
        count, length and where they came from."""
        owned = paths[0].startswith(self.config.extras + os.sep)
        songs = []
        for path in paths:
            track = self.track(path, owned=path.startswith(self.config.extras + os.sep))
            number = (track.first("tracknumber") if track else "").split("/")[0].strip()
            if not number:
                number = re.match(r"^(\d+(?:-\d+)?)\s*-\s", os.path.basename(path) or "")
                number = number.group(1) if number else ""
            title = (track.first("title") if track else "") or song_title(os.path.basename(path))
            seconds = track.seconds if track else 0
            details = [minutes(seconds)] if seconds else []
            if track:
                details.append(quality(path, track))
            songs.append((number.zfill(2) if number.isdigit() else number, title, seconds, details))
        songs.sort(key=lambda song: (song[0] or "~", song[1].casefold()))
        lines = []
        for number, title, _, details in songs[:READY_SONGS]:
            lines.append((f"`{number}` " if number else "") + f"**{title}**" + "".join(f" · {d}" for d in details))
        if len(songs) > READY_SONGS:
            lines.append(f"… and {len(songs) - READY_SONGS} more")
        total = sum(song[2] for song in songs)
        count = f"{len(songs)} new song" + ("" if len(songs) == 1 else "s")
        source = "from a link (music-extras)" if owned else "from Lidarr"
        lines.append("")
        lines.append("*" + " · ".join(filter(None, [count, minutes(total) if total else "", source])) + "*")
        return "\n".join(lines)
