"""The inbox and library passes, the audit and migration.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import json
import os
import time
import traceback

import identify
import layout
import lyrics
import media
import tags as tagging

from settings import AUDIO_EXTENSIONS, DANGEROUS_EXTENSIONS, IMAGE_EXTENSIONS, LYRIC_EXTENSIONS, PARTIAL_SUFFIXES, SIDECAR_EXTENSIONS
from common import Hold, name_is_hidden


class PassesMixin:
    def settled(self, path, stat):
        """Unchanged since the last pass and untouched for the settle time."""
        previous = self.seen_sizes.get(path)
        self.seen_sizes[path] = stat.st_size
        return previous == stat.st_size and time.time() - stat.st_mtime > self.config.settle

    def inbox_pass(self):
        self.last_inbox = time.monotonic()
        inbox = self.config.inbox
        if not os.path.isdir(inbox):
            return
        audio, others = [], []
        for directory, dirnames, filenames in os.walk(inbox):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                path = os.path.join(directory, name)
                lowered = name.lower()
                if lowered.endswith(PARTIAL_SUFFIXES) or ".part-frag" in lowered:
                    continue
                try:
                    stat = os.lstat(path)
                except FileNotFoundError:
                    continue
                if not self.settled(path, stat):
                    continue
                extension = os.path.splitext(lowered)[1]
                (audio if extension in AUDIO_EXTENSIONS else others).append(path)
        for path in audio:
            self.guarded(path, "import", inbox, self.config.extras)
            self.heartbeat()
        for path in others:
            if not os.path.exists(path):
                continue  # removed with its audio
            stem = os.path.splitext(path)[0]
            if any(os.path.exists(stem + e) for e in AUDIO_EXTENSIONS):
                continue  # a sidecar whose audio is not ready yet
            if os.path.splitext(path)[1].lower() in SIDECAR_EXTENSIONS and not media.sniff(path) \
                    and time.time() - os.lstat(path).st_mtime > 3600:
                # A thumbnail or note whose audio was filed or rejected.
                if not self.config.dry_run:
                    os.unlink(path)
                continue
            if os.path.splitext(path)[1].lower() not in SIDECAR_EXTENSIONS:
                self.quarantine(path, "UNEXPECTED_FILE", "not audio", inbox)
        self.prune_empty(inbox)

    def prune_empty(self, top):
        if self.config.dry_run:
            return
        for directory, _, _ in sorted(os.walk(top), key=lambda item: -item[0].count(os.sep)):
            if directory == top or os.path.dirname(directory) == top:
                continue  # keep the inbox and its per-app folders
            try:
                os.rmdir(directory)
            except OSError:
                pass

    def guarded(self, path, mode, base, destination_root=None):
        try:
            self.process(path, mode, base, destination_root)
            self.held_reported.discard(path)
        except Hold as hold:
            if path not in self.held_reported:
                self.held_reported.add(path)
                self.event("held", path, "CLAMD_DOWN", str(hold))
        except Exception as error:
            self.event("error", path, type(error).__name__, str(error)[:300])
            traceback.print_exc()

    def roots_pass(self):
        known = self.state["files"]
        present = set()
        for root in self.config.roots:
            if not os.path.isdir(root):
                continue
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    path = os.path.join(directory, name)
                    if name.startswith(".musicguard-"):
                        self.remove_stale_temporary(path)
                        continue
                    try:
                        stat = os.lstat(path)
                    except FileNotFoundError:
                        continue
                    present.add(path)
                    extension = os.path.splitext(name)[1].lower()
                    if extension not in AUDIO_EXTENSIONS:
                        self.check_other(path, extension, stat, root)
                        continue
                    if known.get(path) == [stat.st_size, stat.st_mtime_ns]:
                        self.lyrics_for_existing(path)
                        continue
                    if time.time() - stat.st_mtime < self.config.settle:
                        continue
                    self.guarded(path, "verify" if root in self.config.verify_only else "inplace", root)
                    self.heartbeat()
                    # A library pass after Lidarr retags everything takes a
                    # while; a pasted link should not wait for it.
                    if time.monotonic() - self.last_inbox >= self.config.inbox_interval:
                        self.inbox_pass()
        for path in list(known):
            if path not in present:
                known.pop(path)
                self.state["lyrics"].pop(path, None)
                self.library_changed = True
        for path in list(self.state["sidecars"]):
            if path not in present:
                self.state["sidecars"].pop(path)
                self.library_changed = True

    def unchecked(self):
        """Audio files in the libraries musicguard has not passed (new, held,
        or failed with an error). Navidrome must not index any of them."""
        known = self.state["files"]
        count = 0
        for root in self.config.roots:
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    path = os.path.join(directory, name)
                    try:
                        stat = os.lstat(path)
                    except FileNotFoundError:
                        continue
                    if known.get(path) != [stat.st_size, stat.st_mtime_ns]:
                        count += 1
        return count

    def remove_stale_temporary(self, path):
        """A half-written copy left by a crash; the original was never touched."""
        try:
            if time.time() - os.lstat(path).st_mtime > 86400 and not self.config.dry_run:
                os.unlink(path)
        except FileNotFoundError:
            pass

    def check_other(self, path, extension, stat, root):
        """Covers and lyrics belong beside tracks; programs and archives never."""
        if name_is_hidden(path):
            return
        if extension in DANGEROUS_EXTENSIONS or os.path.islink(path):
            self.quarantine(path, "UNEXPECTED_FILE", f"{extension or 'no extension'} in a music library", root)
            return
        kind = media.sniff(path) if stat.st_size else None
        if kind:
            self.quarantine(path, "EXECUTABLE", kind, root)
            return
        if extension in LYRIC_EXTENSIONS and stat.st_size > lyrics.MAX_SIDECAR_BYTES:
            self.quarantine(path, "UNEXPECTED_FILE", "lyrics file too large", root)
            return
        if extension in LYRIC_EXTENSIONS and self.orphaned(path, stat):
            self.follow_lyrics(path, extension, root)
            return
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if extension in LYRIC_EXTENSIONS:
            # Lyrics written by another tool (Stanzarr): Navidrome reads them
            # on its next scan, which musicguard starts once audio is checked.
            if self.state["sidecars"].get(path) != [stat.st_size, stat.st_mtime_ns]:
                self.state["sidecars"][path] = [stat.st_size, stat.st_mtime_ns]
                self.library_changed = True
            return
        if extension in IMAGE_EXTENSIONS:
            return
        if self.state["files"].get(path) != [stat.st_size, stat.st_mtime_ns]:
            self.event("reported", path, "UNEXPECTED_FILE", f"{extension or stem}: left in place")
            if not self.config.dry_run:
                self.remember(path)

    def audit(self, roots):
        """Ask Shazam about every track and compare: a report, and every file
        seen is marked audited, so only files that arrive later are
        quarantined for being the wrong song."""
        rows = []
        for root in roots:
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in sorted(filenames):
                    path = os.path.join(directory, name)
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    items = tagging.read_mutagen(path)
                    found, _ = tagging.collect(items or [])
                    title, artist = layout.first(found, "title"), layout.first(found, "artist")
                    match = self.shazam(path)
                    if not match:
                        verdict = "unknown"
                    elif identify.agrees(match, title, artist):
                        verdict = "ok"
                    elif identify.contradicts(match, title, artist):
                        verdict = "WRONG"
                    else:
                        verdict = "differs"
                    self.state["audited"][path] = f"{os.stat(path).st_size}"
                    rows.append({"verdict": verdict, "path": path, "tags": f"{artist} - {title}",
                                 "shazam": repr(match) if match else ""})
                    print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        self.save_state()
        return rows

    def migrate(self, source, destination):
        """Copy an old library into the layout. The source is never changed."""
        files = []
        for directory, dirnames, filenames in os.walk(source):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            files.extend(os.path.join(directory, n) for n in filenames)
        for path in sorted(files):
            extension = os.path.splitext(path)[1].lower()
            if extension in AUDIO_EXTENSIONS:
                self.guarded(path, "copy", source, destination)
            elif extension not in IMAGE_EXTENSIONS | LYRIC_EXTENSIONS:
                self.event("skipped", path, "NOT_AUDIO")
        self.save_state()
