"""One file through the checks and into its library.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import os
import shutil
import tempfile

import mutagen
import dedup
import identify
import layout

from settings import AUDIO_EXTENSIONS
from common import Reject
from batlab_common import titles


class FilingMixin:
    def process(self, path, mode, base, destination_root=None):
        """mode: "import" (inbox -> destination_root, source removed),
        "copy" (migration, source untouched), "inplace" (rewrite where it is),
        "verify" (check where it is, change nothing unless it must go)."""
        from_link = mode == "import"
        # A file new to the libraries is announced once Navidrome has it.
        new = mode != "copy" and path not in self.state["files"]
        notes = []
        with tempfile.TemporaryDirectory(prefix="musicguard-") as workdir:
            try:
                if mode == "verify":
                    found, stream, seconds = self.verify(path, workdir, notes)
                else:
                    clean, found, stream, seconds, cover = self.check_and_rebuild(path, workdir, from_link, notes)
            except Reject as rejection:
                if mode == "copy":
                    self.event("rejected", path, rejection.reason, rejection.detail)
                elif mode in ("inplace", "verify") and rejection.reason == "UNSUPPORTED_CODEC":
                    # A WMA or APE that Lidarr imported is unusual, not
                    # dangerous: it passed the checks before this one.
                    self.event("reported", path, rejection.reason,
                               f"{rejection.detail}; left as it is")
                    if not self.config.dry_run:
                        self.remember(path)
                else:
                    self.quarantine(path, rejection.reason, rejection.detail, base)
                return None
            if mode in ("verify", "inplace"):
                wrong = self.wrong_song(path, found, base)
                if wrong:
                    self.quarantine(path, "WRONG_SONG", wrong, base)
                    return None
            if mode == "verify":
                if base in self.config.credit_roots:
                    self.fix_credits(path, found)
                self.spectrum(path, stream, seconds, path, index=stream["index"])
                if not self.config.dry_run:
                    self.remember(path)
                self.event("verified", path, notes=notes)
                if new:
                    self.ready.append(path)
                if self.config.lyrics and not self.config.dry_run:
                    self.fetch_lyrics(path, found, seconds)
                return path
            if mode == "import":
                existing = self.already_in_library(found, seconds)
                if existing and existing.startswith(self.config.extras + os.sep):
                    old = self.track(existing, owned=True)
                    new_copy = dedup.Track(clean, found, seconds, stream.get("codec_name", ""),
                                           int(os.path.getsize(clean) * 8 / max(seconds, 1)))
                    if old and dedup.best_audio([old, new_copy]) is new_copy:
                        notes.append(f"better than {existing}, which dedup will retire")
                        existing = None
                if existing:
                    self.event("duplicate", path, "ALREADY_IN_LIBRARY", existing)
                    if not self.config.dry_run:
                        self.remove_with_sidecars(path)
                    return existing
            self.spectrum(clean, stream, seconds, path)
            extension = os.path.splitext(clean)[1]
            if mode == "inplace":
                target = path
                if os.path.splitext(path)[1].lower() != extension:
                    self.event("reported", path, "UNUSUAL_CONTAINER",
                               f"would become {extension}; left as it is")
                    if not self.config.dry_run:
                        self.remember(path)
                    return None
            else:
                target = layout.free_path(os.path.join(destination_root,
                                                       layout.relative(found, extension)))
            if self.config.dry_run:
                self.event("would-file" if mode != "inplace" else "would-clean", path,
                           detail=target, notes=notes)
                return target
            directory = os.path.dirname(target)
            os.makedirs(directory, exist_ok=True)
            # Built beside the target and renamed over it, so nothing ever sees
            # half a file. The name keeps the audio extension and a leading dot,
            # which Navidrome and Lidarr skip.
            handle, temporary = tempfile.mkstemp(dir=directory, prefix=".musicguard-", suffix=extension)
            os.close(handle)
            try:
                shutil.copyfile(clean, temporary)
                os.chmod(temporary, 0o644)
                os.replace(temporary, target)
            except BaseException:
                if os.path.exists(temporary):
                    os.unlink(temporary)
                raise
            if cover and mode != "inplace":
                album_cover = os.path.join(directory, "cover.jpg")
                if not os.path.exists(album_cover):
                    with open(album_cover, "wb") as out:
                        out.write(cover)
            self.remember(target)
            self.event("filed" if mode != "inplace" else "cleaned", path,
                       detail="" if mode == "inplace" else target, notes=notes)
            if new:
                self.ready.append(target)
            if mode == "import":
                self.remove_with_sidecars(path)
            if self.config.lyrics:
                self.fetch_lyrics(target, found, seconds)
            return target

    def already_in_library(self, found, seconds):
        """A library file that is this song (same title, an artist in common,
        same length within 5 s, and no remix or live take of it: see
        titles.version), or None."""
        song = identify.Match(layout.first(found, "title"), layout.first(found, "artist"))
        present = set()
        for root in self.config.roots:
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames:
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    path = os.path.join(directory, name)
                    try:
                        stat = os.stat(path)
                    except FileNotFoundError:
                        continue
                    present.add(path)
                    cached = self.songs.get(path)
                    if not cached or cached[:2] != (stat.st_size, stat.st_mtime_ns):
                        try:
                            audio = mutagen.File(path, easy=True)
                        except Exception:
                            audio = None
                        tags_ = (audio.tags if audio is not None and audio.tags is not None else {}) or {}
                        cached = (stat.st_size, stat.st_mtime_ns, (tags_.get("title") or [""])[0],
                                  (tags_.get("artist") or [""])[0],
                                  getattr(getattr(audio, "info", None), "length", 0) or 0)
                        self.songs[path] = cached
                    if (abs(cached[4] - seconds) <= 5 and identify.agrees(song, cached[2], cached[3])
                            and titles.same_version(song.title, cached[2])):
                        return path
        for path in list(self.songs):
            if path not in present:
                self.songs.pop(path)
        return None
