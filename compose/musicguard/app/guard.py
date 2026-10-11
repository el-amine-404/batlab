"""musicguard: every music file is checked, stripped to audio and clean tags,
and filed, or it is quarantined.

  inbox   files from pasted links (MeTube, spotDL) are checked, rebuilt and
          filed into the extras library, then removed from the inbox
  roots   new or changed files in the libraries are checked where they are:
          rebuilt in Aurral's and the extras library, verified only in
          Lidarr's, which owns its tags (see verify())
  migrate a one-time copy of an old library into the layout, source untouched

What "checked and rebuilt" means is in docs/music.md; in short: no program,
archive or script gets through, clamd sees every file it can, the audio must
decode end to end, only the audio stream is copied into a new container, only
allow-listed tags are written back after checking each value, cover art is
decoded and encoded again, and lossless files made from lossy ones are
reported.
"""

import argparse
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone

import apple
import clamd
import mutagen
import credits as credit_fix
import identify
import layout
import lyrics
import media
import musicbrainz
import navidrome
import tags as tagging
import upgrade

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".alac", ".opus", ".ogg", ".oga",
                    ".webm", ".mka", ".wav", ".aif", ".aiff"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LYRIC_EXTENSIONS = {".lrc", ".txt"}
# Files yt-dlp and spotDL leave next to a download; removed with it.
SIDECAR_EXTENSIONS = IMAGE_EXTENSIONS | LYRIC_EXTENSIONS | {".json", ".description"}
PARTIAL_SUFFIXES = (".part", ".ytdl", ".tmp", ".temp", ".frag", ".spotdl")
DANGEROUS_EXTENSIONS = {".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".lnk", ".msi", ".js",
                        ".jse", ".vbs", ".vbe", ".wsf", ".ps1", ".psm1", ".jar", ".apk", ".dll",
                        ".sh", ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".img", ".dmg",
                        ".html", ".htm", ".svg", ".hta", ".url", ".desktop", ".pdf", ".docm",
                        ".xlsm"}
# Cover files the library may keep beside the tracks.
COVER_NAMES = {"cover", "folder", "front", "album"}

MIN_SECONDS = 5
MAX_DECODE_WARNINGS = 20
LYRICS_RETRY_SECONDS = 30 * 86400
# A song MusicBrainz does not know yet is looked up again after a week.
UPGRADE_RETRY_SECONDS = 7 * 86400
# A single whose song is on no album yet is looked at again after a month.
SINGLE_RETRY_SECONDS = 30 * 86400
ORPHAN_SECONDS = 3600
STATE_VERSION = 1
# Events after which Navidrome has something new to index.
LIBRARY_CHANGES = {"filed", "cleaned", "verified", "credited", "quarantined", "reported", "lyrics",
                   "removed", "upgraded"}


def env(name, default=None):
    value = os.environ.get(name, default)
    return value if value not in ("", None) else default


class Config:
    def __init__(self):
        self.inbox = env("MUSICGUARD_INBOX", "/inbox")
        self.extras = env("MUSICGUARD_EXTRAS", "/data/media/music-extras")
        roots = env("MUSICGUARD_ROOTS", "/data/media/music:/data/media/music-aurral")
        self.roots = [r for r in roots.split(":") if r] + [self.extras]
        # Libraries whose manager owns the tags: checked, never rewritten.
        verify = env("MUSICGUARD_VERIFY_ONLY", "/data/media/music")
        self.verify_only = {r for r in verify.split(":") if r}
        # Shazam (SongRec) identification, and MusicBrainz credits for the
        # libraries whose files carry Lidarr's MusicBrainz IDs.
        self.identify = env("MUSICGUARD_IDENTIFY", "1") == "1"
        fix = env("MUSICGUARD_CREDITS", "/data/media/music")
        self.credit_roots = {r for r in fix.split(":") if r}
        self.quarantine = env("MUSICGUARD_QUARANTINE", "/quarantine")
        self.state_dir = env("MUSICGUARD_STATE", "/state")
        self.clamd_socket = env("CLAMD_SOCKET", "/run/clamav/clamd.ctl")
        self.clamd_required = env("CLAMD_REQUIRED", "1") == "1"
        self.clamd_max = int(env("CLAMD_MAX_BYTES", str(25 * 1024 * 1024)))
        self.lyrics = env("MUSICGUARD_LYRICS", "1") == "1"
        self.webhook = env("DISCORD_WEBHOOK")
        # Where "ready to play" goes: new music is announced once Navidrome
        # has it, not when it lands (Lidarr's own import message is off).
        self.ready_webhook = env("MUSICGUARD_READY_WEBHOOK")
        self.inbox_interval = int(env("MUSICGUARD_INBOX_INTERVAL", "60"))
        # Short, because Navidrome only shows new music after a library pass.
        self.roots_interval = int(env("MUSICGUARD_ROOTS_INTERVAL", "120"))
        self.settle = int(env("MUSICGUARD_SETTLE_SECONDS", "120"))
        self.dry_run = env("MUSICGUARD_DRY_RUN", "0") == "1"
        # Songs from links are handed to Lidarr for a proper copy (upgrade.py).
        self.lidarr_url = env("MUSICGUARD_LIDARR_URL", "http://lidarr:8686")
        self.lidarr_key = env("LIDARR_API_KEY")
        self.lidarr_root = env("MUSICGUARD_LIDARR_ROOT", "/data/media/music")
        self.upgrade = env("MUSICGUARD_UPGRADE", "1") == "1" and bool(self.lidarr_key)
        # Navidrome scans only when asked (navidrome.py).
        self.navidrome_url = env("NAVIDROME_URL", "http://navidrome:4533")
        self.navidrome_user = env("NAVIDROME_USER")
        self.navidrome_password = env("NAVIDROME_PASSWORD")


class Hold(Exception):
    """Not safe to decide now (clamd down); leave the file and try later."""


class Reject(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message):
    print(f"{now_iso()} {message}", flush=True)


class Guard:
    def __init__(self, config):
        self.config = config
        self.state_path = os.path.join(config.state_dir, "state.json")
        self.state = self.load_state()
        self.events = []
        self.clamd_ok_at = 0.0
        self.seen_sizes = {}
        self.held_reported = set()
        self.last_inbox = 0.0
        self.credits = credit_fix.Credits(self.state["releases"])
        # path -> (size, mtime, title, artist, seconds), for finding duplicates.
        self.songs = {}
        # Navidrome is told to scan once a library pass leaves nothing unchecked.
        self.library_changed = True
        self.scan_reported = False
        # Library files that became playable since the last scan, announced after it.
        self.ready = []

    # --- state ------------------------------------------------------------------

    def load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            if state.get("version") == STATE_VERSION:
                state.setdefault("audited", {})
                state.setdefault("releases", {})
                state.setdefault("upgrades", {})
                state.setdefault("singles", {})
                return state
        except (OSError, ValueError):
            pass
        return {"version": STATE_VERSION, "files": {}, "lyrics": {}, "audited": {}, "releases": {},
                "upgrades": {}, "singles": {}}

    def save_state(self):
        if self.config.dry_run:
            return
        os.makedirs(self.config.state_dir, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.config.state_dir, prefix=".state.")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(self.state, out)
        os.replace(temporary, self.state_path)

    def remember(self, path):
        stat = os.stat(path)
        self.state["files"][path] = [stat.st_size, stat.st_mtime_ns]

    def heartbeat(self):
        os.makedirs(self.config.state_dir, exist_ok=True)
        with open(os.path.join(self.config.state_dir, "heartbeat"), "w", encoding="utf-8") as out:
            out.write(now_iso())

    # --- reporting ----------------------------------------------------------------

    def event(self, kind, path, reason="", detail="", **extra):
        record = {"time": now_iso(), "event": kind, "path": path, "reason": reason, "detail": detail}
        record.update(extra)
        self.events.append(record)
        if kind in LIBRARY_CHANGES:
            self.library_changed = True

        log(f"{kind} {reason} {path} {detail}".rstrip())
        if self.config.dry_run:
            return
        reports = os.path.join(self.config.state_dir, "reports")
        os.makedirs(reports, exist_ok=True)
        with open(os.path.join(reports, datetime.now().strftime("%Y-%m") + ".jsonl"), "a",
                  encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    def notify(self):
        """One Discord message for what a pass found that needs a look."""
        worth = [e for e in self.events if e["event"] in ("quarantined", "suspect", "error", "held")]
        self.events = []
        if not worth or not self.config.webhook or self.config.dry_run:
            return
        lines = ["**musicguard**"]
        for e in worth[:15]:
            name = os.path.basename(e["path"])
            lines.append(f"- {e['event']} `{e['reason']}` {name}" + (f": {e['detail'][:120]}" if e["detail"] else ""))
        if len(worth) > 15:
            lines.append(f"... and {len(worth) - 15} more (see /state/reports)")
        body = json.dumps({"content": "\n".join(lines)[:1900], "allowed_mentions": {"parse": []}})
        request = urllib.request.Request(self.config.webhook, data=body.encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "batlab-musicguard"})
        try:
            urllib.request.urlopen(request, timeout=15).close()
        except Exception as error:  # a notification never stops the guard
            log(f"discord notification failed: {error}")

    # --- quarantine ---------------------------------------------------------------

    def quarantine(self, path, reason, detail, base):
        """Move a file out of reach, with a note saying why. Nothing is deleted."""
        self.event("quarantined", path, reason, detail)
        if self.config.dry_run:
            return
        relative = os.path.relpath(path, base) if base else os.path.basename(path)
        if relative.startswith(".."):
            relative = os.path.basename(path)
        target = layout.free_path(os.path.join(self.config.quarantine,
                                               datetime.now().strftime("%Y-%m-%d"), relative))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(path, target)
        os.chmod(target, 0)
        with open(target + ".why.json", "w", encoding="utf-8") as out:
            json.dump({"time": now_iso(), "from": path, "reason": reason, "detail": detail}, out,
                      ensure_ascii=False, indent=1)
        self.state["files"].pop(path, None)

    # --- checks -------------------------------------------------------------------

    def malware_scan(self, path, size):
        if size > self.config.clamd_max:
            return "not scanned: larger than clamd accepts; the rebuild drops everything but audio"
        try:
            if time.monotonic() - self.clamd_ok_at > 3600:
                clamd.self_test(self.config.clamd_socket)
                self.clamd_ok_at = time.monotonic()
            infected, signature = clamd.scan_file(self.config.clamd_socket, path)
        except (OSError, clamd.ClamdError) as error:
            self.clamd_ok_at = 0.0
            if self.config.clamd_required:
                raise Hold(f"clamd unavailable: {error}") from error
            return f"not scanned: clamd unavailable ({error})"
        if infected:
            raise Reject("MALWARE", signature)
        return ""

    def inspect(self, path, notes):
        """Every check that reads the file as it is. Returns (info, stream,
        codec, extension, seconds); raises Reject for a file that must go."""
        stat = os.lstat(path)
        if not os.path.isfile(path) or os.path.islink(path):
            raise Reject("NOT_A_FILE")
        if stat.st_size == 0:
            raise Reject("EMPTY")
        kind = media.sniff(path)
        if kind:
            raise Reject("EXECUTABLE", kind)
        note = self.malware_scan(path, stat.st_size)
        if note:
            notes.append(note)
        try:
            info = media.probe(path)
            media.check_format(path, info)
            stream = media.audio_stream(info)
            codec = stream.get("codec_name", "")
            _, extension, _ = media.target_for(codec)
            seconds = media.duration(info)
            if seconds < MIN_SECONDS:
                raise Reject("TOO_SHORT", f"{seconds:.1f} s")
            warnings = media.decode_errors(path, stream["index"])
        except media.MediaError as error:
            raise Reject(error.reason, error.detail) from error
        if len(warnings) > MAX_DECODE_WARNINGS:
            raise Reject("CORRUPT", f"{len(warnings)} decode errors, first: {warnings[0][:200]}")
        if warnings:
            notes.append(f"{len(warnings)} decode warnings")
        return info, stream, codec, extension, seconds

    def check_and_rebuild(self, path, workdir, from_link, notes):
        """Verify path and write a clean copy into workdir. Returns
        (clean_path, tags, source_audio_stream, seconds, cover_jpeg_bytes)."""
        info, stream, codec, extension, seconds = self.inspect(path, notes)

        items = tagging.read_mutagen(path)
        if items is None:
            items = tagging.read_ffprobe(info)
        found, findings = tagging.collect(items)
        notes.extend(findings)
        stem = os.path.splitext(os.path.basename(path))[0]
        if from_link:
            if not found.get("title"):
                # yt-dlp names files "Artist - Title" when the video had no tags.
                found["title"] = [tagging.clean_text(stem, notes)]
            found = tagging.fill_from_link(found, musicbrainz.artist_side)
            found, apple_cover = self.link_identity(path, found, notes)
        else:
            apple_cover = None
        found = tagging.complete(found, layout.component(stem, "Unknown Title"))

        cover = None
        cover_path = os.path.join(workdir, "cover.jpg")
        cover_index = media.cover_stream(info)
        if apple_cover:
            downloaded = os.path.join(workdir, "apple-cover")
            with open(downloaded, "wb") as out:
                out.write(apple_cover)
            if not media.sniff(downloaded) and media.extract_cover(downloaded, None, cover_path, square=True):
                cover = cover_path
        # Video thumbnails are 16:9; album art is square.
        if cover is None and cover_index is not None \
                and media.extract_cover(path, cover_index, cover_path, square=from_link):
            cover = cover_path
        elif from_link:
            for extension_ in IMAGE_EXTENSIONS:
                sidecar = os.path.join(os.path.dirname(path), stem + extension_)
                if os.path.isfile(sidecar) and not media.sniff(sidecar):
                    if media.extract_cover(sidecar, None, cover_path, square=True):
                        cover = cover_path
                    break
        cover_bytes = None
        if cover:
            with open(cover, "rb") as handle:
                cover_bytes = handle.read()

        clean = os.path.join(workdir, "clean" + extension)
        media.rebuild(path, stream["index"], codec, clean)
        skipped = tagging.write(clean, found, cover_bytes)
        if skipped:
            notes.append(f"tags this format cannot hold: {', '.join(sorted(skipped))}")

        rebuilt = media.probe(clean)
        streams = rebuilt.get("streams", [])
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        other = [s for s in streams if s.get("codec_type") != "audio"
                 and not s.get("disposition", {}).get("attached_pic")]
        if len(audio) != 1 or other:
            raise Reject("REBUILD_FAILED", f"rebuilt file has {len(audio)} audio and {len(other)} other streams")
        rebuilt_seconds = media.duration(rebuilt)
        if abs(rebuilt_seconds - seconds) > max(1.0, seconds * 0.01):
            raise Reject("REBUILD_FAILED", f"length changed from {seconds:.1f} to {rebuilt_seconds:.1f} s")
        return clean, found, stream, seconds, cover_bytes

    def shazam(self, path):
        """identify.Match, or None (no match, or Shazam unreachable)."""
        if not self.config.identify:
            return None
        try:
            return identify.recognise(path)
        except Exception as error:  # never block a file on Shazam being down
            log(f"shazam failed for {path}: {error}")
            return None

    def link_identity(self, path, found, notes):
        """A link's title is a video's; what Shazam heard wins over it, and
        Apple Music's catalogue supplies the album, track number, date, genre
        and cover. Returns (tags, cover bytes or None). When Shazam hears
        another song, every tag of the old identity goes, genre included."""
        match = self.shazam(path)
        if not match:
            return found, None
        title, artist = layout.first(found, "title"), layout.first(found, "artist")
        if not identify.agrees(match, title, artist):
            notes.append(f"identified by Shazam as {match!r} (file said {artist} - {title})")
            found, _ = tagging.collect([("title", match.title), ("artist", match.artist),
                                        ("albumartist", match.artist), ("album", match.album),
                                        ("date", match.year), ("isrc", match.isrc),
                                        ("genre", match.genre)])
        elif match.isrc and "isrc" not in found:
            found["isrc"] = [match.isrc]
        release = None
        if match.apple_id:
            try:
                release = apple.release(match.apple_id)
            except Exception as error:  # Apple down: Shazam's data is still right
                log(f"apple lookup failed for {path}: {error}")
        if not release:
            return found, None
        notes.append(f"album from Apple Music: {release!r}")
        album, _ = tagging.collect(release.tags())
        for key in ("album", "albumartist", "date", "genre", "tracknumber", "discnumber",
                    "musicbrainz_albumtype"):
            found.pop(key, None)
        found.update(album)
        try:
            cover = release.cover()
        except Exception as error:
            log(f"apple cover failed for {path}: {error}")
            cover = None
        return found, cover

    def wrong_song(self, path, found, root):
        """For a file new to a library: Shazam sure it is a different song
        (both title and artists differ) means a wrong or fake download."""
        stat = os.stat(path)
        key = f"{stat.st_size}"
        if self.state["audited"].get(path) == key:
            return None
        match = self.shazam(path)
        self.state["audited"][path] = key
        if not match:
            return None
        title, artist = layout.first(found, "title"), layout.first(found, "artist")
        if identify.contradicts(match, title, artist):
            return f"tagged {artist} - {title}, Shazam hears {match!r}"
        if not identify.same_title(match.title, title):
            # Same artist, another title: the classic wrong match within an
            # artist's catalogue, but a translated or renamed title looks the
            # same, so it is reported rather than moved.
            self.event("suspect", path, "WRONG_TITLE", f"tagged {artist} - {title}, Shazam hears {match!r}")
        return None

    def fix_credits(self, path, found):
        try:
            wanted = self.credits.wanted(found)
        except Exception as error:
            log(f"musicbrainz credits failed for {path}: {error}")
            return
        if not wanted or self.config.dry_run:
            return
        artist, artists, album_artist = wanted
        changed = credit_fix.apply(path, artist, artists, album_artist)
        if changed:
            self.event("credited", path, detail=f"{artist} / album {album_artist}")
            self.remember(path)

    def verify(self, path, workdir, notes):
        """The checks without the rewrite, for a library whose manager owns its
        tags (Lidarr scrubs every download's tags and writes MusicBrainz's).
        Rewriting there would start a retag war and break the hardlink to the
        seeding torrent. What a rewrite would have removed is reported."""
        info, stream, _, _, seconds = self.inspect(path, notes)
        items = tagging.read_mutagen(path)
        found, findings = tagging.collect(items if items is not None else tagging.read_ffprobe(info))
        for finding in findings:
            if finding.startswith("markup"):
                self.event("suspect", path, "SUSPICIOUS_TAG", finding)
        extra = [s for s in info.get("streams", []) if s.get("codec_type") != "audio"
                 and not s.get("disposition", {}).get("attached_pic")]
        audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
        if extra or len(audio) > 1:
            self.event("suspect", path, "EXTRA_STREAM",
                       f"{len(audio)} audio and {len(extra)} other streams: "
                       + ", ".join(s.get("codec_name", "?") for s in extra[:5]))
        cover_index = media.cover_stream(info)
        if cover_index is not None:
            problem = media.check_cover(path, cover_index, workdir)
            if problem:
                self.event("suspect", path, *problem)
        return found, stream, seconds

    def spectrum(self, clean, stream, seconds, path, index=0):
        codec = stream.get("codec_name", "")
        try:
            bit_rate = int(stream.get("bit_rate") or 0)
        except ValueError:
            bit_rate = 0
        lossless = codec in media.LOSSLESS or media.PCM.match(codec)
        high_lossy = codec in ("mp3", "aac") and bit_rate >= 256000
        if not (lossless or high_lossy):
            return
        verdict, levels = media.spectrum_verdict(clean, index, seconds, int(stream.get("sample_rate") or 0))
        if verdict:
            what = "lossless file" if lossless else f"{bit_rate // 1000}k {codec}"
            self.event("suspect", path, verdict,
                       f"{what} with an empty top band, likely made from a lower-quality file",
                       levels=levels)

    # --- one file -----------------------------------------------------------------

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
        same length within 5 s, so a remix or live take is not one), or None."""
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
                    if abs(cached[4] - seconds) <= 5 and identify.agrees(song, cached[2], cached[3]):
                        return path
        for path in list(self.songs):
            if path not in present:
                self.songs.pop(path)
        return None

    def remove_with_sidecars(self, path):
        stem = os.path.splitext(path)[0]
        os.unlink(path)
        for extension in SIDECAR_EXTENSIONS:
            sidecar = stem + extension
            if os.path.isfile(sidecar) and not os.path.islink(sidecar):
                os.unlink(sidecar)

    # --- lyrics -------------------------------------------------------------------

    def has_lyrics(self, path):
        stem = os.path.splitext(path)[0]
        return any(os.path.exists(stem + e) for e in LYRIC_EXTENSIONS)

    def fetch_lyrics(self, path, found, seconds):
        if self.has_lyrics(path):
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
        if kind in ("synced", "plain"):
            sidecar = os.path.splitext(path)[0] + (".lrc" if kind == "synced" else ".txt")
            with open(sidecar, "w", encoding="utf-8") as out:
                out.write(text)
            self.event("lyrics", path, kind)

    def lyrics_for_existing(self, path):
        if not self.config.lyrics or self.has_lyrics(path):
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

    # --- passes -------------------------------------------------------------------

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
        """One Discord message per scan: the albums that just became playable."""
        new, self.ready = self.ready, []
        albums = {}
        for path in new:
            if not os.path.exists(path):
                continue  # replaced or quarantined since
            for root in self.config.roots:
                if path.startswith(root + os.sep):
                    parts = os.path.relpath(path, root).split(os.sep)
                    if len(parts) >= 3:
                        key = f"{parts[0]} – {parts[1]}"
                        albums[key] = albums.get(key, 0) + 1
        if not albums or not self.config.ready_webhook:
            return
        lines = ["**Ready to play in Navidrome**"]
        for name, count in sorted(albums.items())[:20]:
            lines.append(f"- {name}" + (f" ({count} songs)" if count > 1 else ""))
        if len(albums) > 20:
            lines.append(f"... and {len(albums) - 20} more")
        body = json.dumps({"content": "\n".join(lines)[:1900], "allowed_mentions": {"parse": []}})
        request = urllib.request.Request(self.config.ready_webhook, data=body.encode(), method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "batlab-musicguard"})
        try:
            urllib.request.urlopen(request, timeout=15).close()
        except Exception as error:
            log(f"ready notification failed: {error}")

    def orphaned(self, path, stat):
        """A lyrics sidecar whose track has been gone for an hour."""
        stem = os.path.splitext(path)[0]
        if any(os.path.exists(stem + e) for e in AUDIO_EXTENSIONS):
            return False
        return time.time() - stat.st_mtime > ORPHAN_SECONDS

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
            # Lidarr renames tracks after import (a multi-disc release gets
            # "1-01 - ..."), and leaves the lyrics it did not import behind;
            # the renamed track gets its own lyrics on the next pass.
            if not self.config.dry_run:
                os.unlink(path)
            self.event("removed", path, "ORPHANED_LYRICS", "no track of this name")
            return
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if extension in IMAGE_EXTENSIONS or extension in LYRIC_EXTENSIONS:
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

    # --- links upgraded by Lidarr -------------------------------------------------

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
        if "album" not in entry:
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
        if single:
            # Through Lidarr, which owns the file: to its recycle bin, and the
            # single unmonitored so it is not fetched again.
            if lidarr.remove_track_file(single["group"], path):
                self.event("upgraded", path, detail=f"single replaced by {replacement}")
                return None
            return entry
        self.remove_with_sidecars(path)
        directory = os.path.dirname(path)
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
        self.state["files"].pop(path, None)
        self.state["lyrics"].pop(path, None)
        self.event("upgraded", path, detail=f"replaced by {replacement}")
        return None

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


def name_is_hidden(path):
    return os.path.basename(path).startswith(".")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="watch the inbox and libraries until stopped")
    commands.add_parser("once", help="one inbox pass and one library pass")
    migrate = commands.add_parser("migrate", help="copy an old library into the layout")
    migrate.add_argument("source")
    migrate.add_argument("destination")
    check = commands.add_parser("check", help="check one file and say what would happen")
    check.add_argument("path")
    audit = commands.add_parser("audit", help="compare every track's tags with what Shazam hears")
    audit.add_argument("roots", nargs="*")
    commands.add_parser("upgrade", help="one pass handing songs from links to Lidarr")
    ident = commands.add_parser("identify", help="ask Shazam what files are")
    ident.add_argument("paths", nargs="+")
    arguments = parser.parse_args(argv)

    config = Config()
    if arguments.command == "check":
        config.dry_run = True
        config.lyrics = False
    guard = Guard(config)

    if arguments.command == "check":
        guard.process(os.path.abspath(arguments.path), "copy", os.path.dirname(arguments.path),
                      "/would-be-library")
        return 0
    if arguments.command == "identify":
        for path in arguments.paths:
            print(f"{path}: {guard.shazam(path)!r}", flush=True)
        return 0
    if arguments.command == "upgrade":
        guard.upgrade_pass()
        guard.save_state()
        return 0
    if arguments.command == "audit":
        guard.audit(arguments.roots or config.roots)
        return 0
    if arguments.command == "migrate":
        guard.migrate(arguments.source, arguments.destination)
        guard.notify()
        return 0

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    last_roots = 0.0
    while not stopping:
        guard.heartbeat()
        guard.inbox_pass()
        if time.monotonic() - last_roots >= config.roots_interval or arguments.command == "once":
            guard.roots_pass()
            guard.upgrade_pass()
            guard.scan_navidrome()
            last_roots = time.monotonic()
        guard.save_state()
        guard.notify()
        if arguments.command == "once":
            break
        for _ in range(config.inbox_interval):
            if stopping:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
