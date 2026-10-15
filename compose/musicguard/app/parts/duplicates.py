"""The same song filed twice (dedup.py), and remixes beside their original.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import os
import shutil

import mutagen
import dedup
import layout
import tags as tagging
from batlab_common import fingerprint

from settings import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, SIDECAR_EXTENSIONS
from common import log, now_iso


class DuplicatesMixin:
    def track(self, path, owned):
        """dedup.Track for a library file, cached by size and mtime; None if unreadable."""
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return None
        cached = self.tracks.get(path)
        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            cached[2].owned = owned
            return cached[2]
        try:
            audio = mutagen.File(path)
        except Exception:
            audio = None
        if audio is None or not getattr(audio, "info", None):
            return None
        items = tagging.read_mutagen(path) or []
        found, _ = tagging.collect(items)
        seconds = getattr(audio.info, "length", 0) or 0
        kind = type(audio).__name__.lower()
        codec = {"mp3": "mp3", "easymp3": "mp3", "flac": "flac", "oggopus": "opus",
                 "oggvorbis": "vorbis"}.get(kind, "")
        if not codec and kind in ("mp4", "easymp4"):
            codec = "alac" if "alac" in str(getattr(audio.info, "codec", "")) else "aac"
        bitrate = int(stat.st_size * 8 / max(seconds, 1))
        track = dedup.Track(path, found, seconds, codec, bitrate, owned)
        self.tracks[path] = (stat.st_size, stat.st_mtime_ns, track)
        return track

    def alike(self, a, b):
        try:
            return fingerprint.same_recording(a.path, b.path)
        except Exception as error:  # fpcalc failed: not shown to be one song
            log(f"fingerprint comparison failed for {a.path}, {b.path}: {error}")
            return False

    def dedup_pass(self):
        """The same song filed twice (dedup.py): the best audio stays, under the
        best tags, and the other copies in music-extras go to quarantine. Only
        files the guard has passed are weighed; Lidarr's and Aurral's are
        never moved."""
        if not self.config.dedup or not os.path.isdir(self.config.extras):
            return
        tracks = []
        for path in list(self.state["files"]):
            if os.path.splitext(path)[1].lower() not in AUDIO_EXTENSIONS:
                continue
            if not any(path.startswith(root + os.sep) for root in self.config.roots):
                continue
            track = self.track(path, owned=path.startswith(self.config.extras + os.sep))
            if track:
                tracks.append(track)
        live = {t.path for t in tracks}
        for path in list(self.tracks):
            if path not in live:
                self.tracks.pop(path)
        related = []
        for group in dedup.groups(tracks, self.alike, related):
            try:
                self.resolve(group)
            except Exception as error:  # one group never stops the others
                log(f"dedup failed for {[t.path for t in group]}: {error}")
        self.flag_related(related, live)

    def flag_related(self, related, live):
        """Two copies close enough to be mistaken for one song but not one (a
        remix beside its original, one title for two recordings): both stay,
        and each pair is flagged once, for a person to look at."""
        flagged = self.state["flagged"]
        for a, b, reason in related:
            key = "\n".join(sorted((a.path, b.path)))
            if key in flagged:
                continue
            flagged[key] = now_iso()
            mine, other = (a, b) if a.owned else (b, a)
            kind = "VERSION" if reason.startswith("two versions") else "SAME_TITLE"
            self.event("flagged", mine.path, kind, f"{reason}, both kept; the other: {other.path}")
        for key in list(flagged):
            if not all(path in live for path in key.split("\n")):
                flagged.pop(key)

    def resolve(self, group):
        keep = dedup.best_audio(group)
        others = [t for t in group if t is not keep and t.owned]
        if not others:
            return
        if self.config.dry_run:
            self.event("would-deduplicate", keep.path, detail=f"over {[t.path for t in others]}")
            return
        merged = dedup.best_tags(group) if keep.owned else None
        emptied = set()
        # The others go first: the kept copy may take one's name.
        for track in others:
            self.hand_over(track.path, keep.path, track, keep)
            self.quarantine(track.path, "DUPLICATE", f"the same song as {keep.path}, which is better",
                            self.config.extras, lyrics_too=True)
            self.state["lyrics"].pop(track.path, None)
            emptied.add(os.path.dirname(track.path))
        kept = keep.path
        if keep.owned:
            if merged != keep.tags:
                tagging.write(kept, merged)
            target = os.path.join(self.config.extras, layout.relative(merged, os.path.splitext(kept)[1]))
            if target != kept:
                target = layout.free_path(target)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                old_dir = os.path.dirname(kept)
                cover = os.path.join(old_dir, "cover.jpg")
                if os.path.isfile(cover) and not os.path.exists(os.path.join(os.path.dirname(target), "cover.jpg")):
                    shutil.copy2(cover, os.path.join(os.path.dirname(target), "cover.jpg"))
                os.rename(kept, target)
                for extension in SIDECAR_EXTENSIONS - IMAGE_EXTENSIONS:
                    sidecar = os.path.splitext(kept)[0] + extension
                    if os.path.isfile(sidecar) and not os.path.islink(sidecar):
                        os.rename(sidecar, os.path.splitext(target)[0] + extension)
                self.state["files"].pop(kept, None)
                if kept in self.state["lyrics"]:
                    self.state["lyrics"][target] = self.state["lyrics"].pop(kept)
                emptied.add(old_dir)
                kept = target
            self.remember(kept)
        for directory in sorted(emptied, key=len, reverse=True):
            self.prune(directory)
        self.event("deduplicated", kept, detail=f"kept over {len(others)} other cop"
                                                f"{'y' if len(others) == 1 else 'ies'}: "
                                                + ", ".join(os.path.basename(t.path) for t in others))
