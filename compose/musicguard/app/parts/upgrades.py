"""Songs from links and Lidarr's singles, handed to Lidarr for their album (upgrade.py).

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import os
import shutil
import time
import urllib.error
import urllib.request

import carry
import layout
import tags as tagging
import upgrade

from settings import AUDIO_EXTENSIONS, SINGLE_RETRY_SECONDS, UPGRADE_RETRY_SECONDS
from common import log


class UpgradesMixin:
    def upgrade_pass(self):
        """Hand every song in the extras library to Lidarr (upgrade.py) and
        remove the YouTube copy once Lidarr's verified file is in the library."""
        if not self.config.upgrade or not os.path.isdir(self.config.extras):
            return
        lidarr = upgrade.Lidarr(self.config.lidarr_url, self.config.lidarr_key)
        entries = self.state["upgrades"]
        present = set()
        for directory, dirnames, filenames in os.walk(self.config.extras):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                path = os.path.join(directory, name)
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                present.add(path)
                entry = entries.get(path, {})
                if time.time() < entry.get("next", 0):
                    continue
                try:
                    entries[path] = self.upgrade_one(path, entry, lidarr)
                except Exception as error:  # Lidarr or MusicBrainz down: next pass
                    log(f"upgrade failed for {path}: {error}")
                    entry["next"] = time.time() + 3600
                    entries[path] = entry
                if entries.get(path) is None:
                    entries.pop(path, None)
                self.heartbeat()
        for path in list(entries):
            if path not in present:
                entries.pop(path)
        self.singles_pass(lidarr)

    def singles_pass(self, lidarr):
        """Singles in Lidarr's library get the album their song is on."""
        entries = self.state["singles"]
        present = set()
        for directory, dirnames, filenames in os.walk(self.config.lidarr_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                path = os.path.join(directory, name)
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                present.add(path)
                if path not in self.state["files"]:
                    continue  # not verified yet
                stat = os.stat(path)
                entry = entries.get(path, {})
                if entry.get("stat") == [stat.st_size, stat.st_mtime_ns] and not entry.get("single"):
                    continue  # read before: not a single
                if time.time() < entry.get("next", 0):
                    continue
                if "single" not in entry or entry.get("stat") != [stat.st_size, stat.st_mtime_ns]:
                    found, _ = tagging.collect(tagging.read_mutagen(path) or [])
                    group = layout.first(found, "musicbrainz_releasegroupid")
                    single = (found.get("musicbrainz_albumtype") or [""])[0].lower() == "single" and group
                    entry = {"stat": [stat.st_size, stat.st_mtime_ns], "single": bool(single)}
                    if single:
                        entry.update({"group": group, "recording": layout.first(found, "musicbrainz_trackid"),
                                      "title": layout.first(found, "title"),
                                      "artist": layout.first(found, "artist")})
                if not entry["single"]:
                    entries[path] = entry
                    continue
                try:
                    kept = self.upgrade_one(path, entry, lidarr, single=entry)
                except Exception as error:
                    log(f"single upgrade failed for {path}: {error}")
                    kept = dict(entry, next=time.time() + 3600)
                if kept is None:
                    entries.pop(path, None)
                    continue
                if "album" not in kept:  # no album (yet), or Lidarr lost it: ask again later
                    entry = {k: v for k, v in entry.items() if k not in ("album", "recordings")}
                entries[path] = dict(entry, **kept)
                self.heartbeat()
        for path in list(entries):
            if path not in present:
                entries.pop(path)

    def upgrade_one(self, path, entry, lidarr, single=None):
        """The entry to keep for this song, or None once it is replaced.
        single: for a single in Lidarr's library, its release group and IDs."""
        just_asked = "album" not in entry
        if just_asked:
            if single:
                title, artist = single["title"], single["artist"]
                recordings = upgrade.recordings_for(title, artist)
                if single.get("recording"):
                    recordings.insert(0, upgrade.recording(single["recording"]))
                group = upgrade.best_release_group(recordings)
                if not group or group[2] != "Album" or group[0] == single["group"]:
                    return {"next": time.time() + SINGLE_RETRY_SECONDS}  # on no album (yet)
            else:
                items = tagging.read_mutagen(path)
                found, _ = tagging.collect(items or [])
                title, artist = layout.first(found, "title"), layout.first(found, "artist")
                if not title or not artist or artist == "Unknown Artist":
                    return {"next": time.time() + UPGRADE_RETRY_SECONDS}
                recordings = upgrade.recordings_for(title, artist, layout.first(found, "isrc"))
                group = upgrade.best_release_group(recordings)
                if not group:
                    if not entry:
                        self.event("reported", path, "NOT_IN_MUSICBRAINZ",
                                   f"{artist} - {title}: Lidarr cannot fetch it; kept as it is")
                    return {"next": time.time() + UPGRADE_RETRY_SECONDS}
            group_id, album_title, kind = group
            if self.config.dry_run:
                self.event("would-request", path, kind, f"{album_title} ({group_id})")
                return None
            album = lidarr.album(group_id)
            if album:
                lidarr.want(album)
            else:
                album = lidarr.add_album(group_id)
                if not album:
                    return {"next": time.time() + UPGRADE_RETRY_SECONDS}
            self.event("requested", path, kind, f"Lidarr fetches {album_title}")
            entry = {"album": album["id"], "title": title,
                     "recordings": sorted({r["id"] for r in recordings if r.get("id")})}
            if single:
                return entry
        try:
            if not entry.get("confirmed") and not just_asked:
                # Lidarr may have unmonitored it after adding its artist, a few
                # seconds after the request: looked at from the next pass on.
                if lidarr.still_wanted(entry["album"]):
                    self.event("requested", path, detail="Lidarr had left the album unmonitored: wanted again")
                else:
                    entry = dict(entry, confirmed=True)
            replacement = lidarr.track_file(entry["album"], set(entry["recordings"]), entry["title"])
        except urllib.error.HTTPError as error:
            if error.code == 404:  # the album was removed from Lidarr: start over
                return {}
            raise
        known = self.state["files"].get(replacement) if replacement else None
        if not known or not os.path.isfile(replacement):
            return entry  # not downloaded yet, or not yet verified by the roots pass
        if self.config.dry_run or replacement == path:
            return entry
        self.hand_over(path, replacement)
        if single:
            # Through Lidarr, which owns the file: to its recycle bin, and the
            # single unmonitored so it is not fetched again.
            if lidarr.remove_track_file(single["group"], path):
                self.event("upgraded", path, detail=f"single replaced by {replacement}")
                return None
            return entry
        self.quarantine(path, "UPGRADED", f"replaced by {replacement}", self.config.extras,
                        announce=False, lyrics_too=True)
        self.prune(os.path.dirname(path))
        self.state["lyrics"].pop(path, None)
        self.event("upgraded", path, detail=f"replaced by {replacement}")
        return None

    def hand_over(self, old, new, old_track=None, new_track=None):
        """The going copy's lyrics and album cover, to the staying copy when it
        lacks them (carry.py). Never stops the retirement."""
        try:
            extras = self.config.extras + os.sep
            old_track = old_track or self.track(old, owned=old.startswith(extras))
            new_track = new_track or self.track(new, owned=new.startswith(extras))
            if not old_track or not new_track:
                return
            plan = carry.lyrics(old, new, old_track.seconds, new_track.seconds)
            cover = carry.cover(old, new, old_track.tags, new_track.tags)
            for target, text, what in plan:
                self.event("handed-over", target, "LYRICS", f"{what}, from {old}")
                if not self.config.dry_run:
                    lyrics_file = os.path.join(os.path.dirname(target), "." + os.path.basename(target) + ".tmp")
                    with open(lyrics_file, "w", encoding="utf-8") as out:
                        out.write(text)
                    os.replace(lyrics_file, target)
            if cover:
                self.event("handed-over", cover[1], "COVER", f"from {cover[0]}")
                if not self.config.dry_run:
                    shutil.copyfile(*cover)
        except Exception as error:
            log(f"hand-over from {old} to {new} failed: {error}")
