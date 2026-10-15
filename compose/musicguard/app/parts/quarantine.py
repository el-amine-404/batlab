"""Moving files out of reach, and emptying old quarantine.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import json
import os
import shutil
from datetime import datetime

import layout

from settings import AUDIO_EXTENSIONS, LYRIC_EXTENSIONS, SIDECAR_EXTENSIONS
from common import now_iso


class QuarantineMixin:
    def quarantine(self, path, reason, detail, base, announce=True, lyrics_too=False):
        """Move a file out of reach, with a note saying why; purge_quarantine
        deletes it after quarantine_days. announce=False: a routine retirement
        (an upgrade), not sent to Discord. lyrics_too: its lyrics go with it,
        unless another track of its name stays."""
        self.event("quarantined" if announce else "set-aside", path, reason, detail)
        if self.config.dry_run:
            return
        stem = os.path.splitext(path)[0]
        shared = any(os.path.exists(stem + e) for e in AUDIO_EXTENSIONS if stem + e != path)
        sidecars = [stem + e for e in sorted(LYRIC_EXTENSIONS) if lyrics_too and not shared
                    and os.path.isfile(stem + e) and not os.path.islink(stem + e)]
        relative = os.path.relpath(path, base) if base else os.path.basename(path)
        if relative.startswith(".."):
            relative = os.path.basename(path)
        target = layout.free_path(os.path.join(self.config.quarantine,
                                               datetime.now().strftime("%Y-%m-%d"), relative))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(path, target)
        os.chmod(target, 0)
        for sidecar in sidecars:
            shutil.move(sidecar, os.path.splitext(target)[0] + os.path.splitext(sidecar)[1])
        with open(target + ".why.json", "w", encoding="utf-8") as out:
            json.dump({"time": now_iso(), "from": path, "reason": reason, "detail": detail}, out,
                      ensure_ascii=False, indent=1)
        self.state["files"].pop(path, None)

    def purge_quarantine(self):
        """A day's quarantine folder is deleted quarantine_days after that day."""
        days = self.config.quarantine_days
        if days <= 0 or not os.path.isdir(self.config.quarantine):
            return
        today = datetime.now().date()
        for name in sorted(os.listdir(self.config.quarantine)):
            try:
                day = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue  # not one of musicguard's day folders
            if (today - day).days < days:
                continue
            folder = os.path.join(self.config.quarantine, name)
            count = sum(1 for _, _, files in os.walk(folder) for n in files if not n.endswith(".why.json"))
            if self.config.dry_run:
                self.event("would-purge", folder, detail=f"{count} files")
                continue
            shutil.rmtree(folder)
            self.event("purged", folder, detail=f"{count} files quarantined {(today - day).days} days ago")

    def prune(self, directory):
        """An extras folder left without audio loses its cover, then itself
        and its artist's folder once empty."""
        if not os.path.isdir(directory):
            return
        if not any(os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS for n in os.listdir(directory)):
            cover = os.path.join(directory, "cover.jpg")
            if os.path.isfile(cover):
                os.unlink(cover)
        while directory != self.config.extras and directory.startswith(self.config.extras + os.sep):
            try:
                os.rmdir(directory)  # the album's folder, then the artist's, once empty
            except OSError:
                break
            directory = os.path.dirname(directory)

    def remove_with_sidecars(self, path):
        stem = os.path.splitext(path)[0]
        os.unlink(path)
        for extension in SIDECAR_EXTENSIONS:
            sidecar = stem + extension
            if os.path.isfile(sidecar) and not os.path.islink(sidecar):
                os.unlink(sidecar)
