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
import urllib.request
from datetime import datetime, timezone

import clamd
import layout
import lyrics
import media
import musicbrainz
import tags as tagging

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
STATE_VERSION = 1


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
        self.quarantine = env("MUSICGUARD_QUARANTINE", "/quarantine")
        self.state_dir = env("MUSICGUARD_STATE", "/state")
        self.clamd_socket = env("CLAMD_SOCKET", "/run/clamav/clamd.ctl")
        self.clamd_required = env("CLAMD_REQUIRED", "1") == "1"
        self.clamd_max = int(env("CLAMD_MAX_BYTES", str(25 * 1024 * 1024)))
        self.lyrics = env("MUSICGUARD_LYRICS", "1") == "1"
        self.webhook = env("DISCORD_WEBHOOK")
        self.inbox_interval = int(env("MUSICGUARD_INBOX_INTERVAL", "60"))
        self.roots_interval = int(env("MUSICGUARD_ROOTS_INTERVAL", "600"))
        self.settle = int(env("MUSICGUARD_SETTLE_SECONDS", "120"))
        self.dry_run = env("MUSICGUARD_DRY_RUN", "0") == "1"


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

    # --- state ------------------------------------------------------------------

    def load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            if state.get("version") == STATE_VERSION:
                return state
        except (OSError, ValueError):
            pass
        return {"version": STATE_VERSION, "files": {}, "lyrics": {}}

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
        found = tagging.complete(found, layout.component(stem, "Unknown Title"))

        cover = None
        cover_path = os.path.join(workdir, "cover.jpg")
        cover_index = media.cover_stream(info)
        # Video thumbnails are 16:9; album art is square.
        if cover_index is not None and media.extract_cover(path, cover_index, cover_path, square=from_link):
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
            if mode == "verify":
                self.spectrum(path, stream, seconds, path, index=stream["index"])
                if not self.config.dry_run:
                    self.remember(path)
                self.event("verified", path, notes=notes)
                if self.config.lyrics and not self.config.dry_run:
                    self.fetch_lyrics(path, found, seconds)
                return path
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
            if mode == "import":
                self.remove_with_sidecars(path)
            if self.config.lyrics:
                self.fetch_lyrics(target, found, seconds)
            return target

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
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if extension in IMAGE_EXTENSIONS or extension in LYRIC_EXTENSIONS:
            return
        if self.state["files"].get(path) != [stat.st_size, stat.st_mtime_ns]:
            self.event("reported", path, "UNEXPECTED_FILE", f"{extension or stem}: left in place")
            if not self.config.dry_run:
                self.remember(path)

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
