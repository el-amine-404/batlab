"""What a file is and whether it is safe: clamd, decoding, Shazam, the reference, spectrum.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import os
import time

import apple
import clamd
import credits as credit_fix
import identify
import layout
import media
import musicbrainz
import reference
import tags as tagging

from settings import IMAGE_EXTENSIONS, MAX_DECODE_WARNINGS, MIN_SECONDS
from common import Hold, Reject, log
from batlab_common import titles


class ChecksMixin:
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
        catalogue = tagging.from_catalogue(path)
        if catalogue and identify.contradicts(match, title, artist):
            # Spotify named the song (spotDL), not a video title: other audio
            # under it is a wrong download, never a song to file under
            # whatever Shazam calls it.
            detail = f"tagged {artist} - {title}, Shazam hears {match!r}"
            try:
                score = reference.compare(path, title, artist)
            except Exception as error:
                log(f"reference comparison failed for {path}: {error}")
                score = None
            if score is None or score < reference.SAME:
                raise Reject("WRONG_SONG", detail)
            notes.append(f"{detail}; kept: it is Apple Music's {artist} - {title} ({score:.2f} alike)")
            return found, None
        if catalogue and not identify.agrees(match, title, artist):
            # Spotify named it and Shazam shares a title or an artist: a remix,
            # a feature or a translated title. Spotify's name stays.
            notes.append(f"Shazam hears {match!r}; Spotify's {artist} - {title} kept")
            return found, None
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
        album, _ = tagging.collect(release.tags())
        if catalogue:
            # Spotify's album, date and numbers stay: Apple can date the same
            # album otherwise (Caméléon: 2020, Spotify 2021), and the album
            # would be split across two folders. Apple only fills gaps.
            filled = [key for key in album if key not in found]
            for key in filled:
                found[key] = album[key]
            if filled:
                notes.append(f"from Apple Music: {', '.join(filled)}")
            return found, None
        notes.append(f"album from Apple Music: {release!r}")
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
        """For a file new to a library: Shazam hearing a different song (both
        title and artists differ) is checked against the real song before
        anything moves (reference.py): the file is compared with Apple Music's
        preview of the song its tags name. It sounds like it: kept, whatever
        Shazam says. It clearly does not: wrong song. No preview to compare
        with: Shazam decides only if its answer is a real release (an ISRC or
        an Apple Music ID); otherwise the file stays and is reported."""
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
            detail = f"tagged {artist} - {title}, Shazam hears {match!r}"
            try:
                score = reference.compare(path, title, artist)
            except Exception as error:  # Apple or fpcalc failed: no evidence either way
                log(f"reference comparison failed for {path}: {error}")
                score = None
            if score is not None and score >= reference.SAME:
                self.event("reported", path, "SHAZAM_OVERRULED",
                           f"{detail}; kept: it is Apple Music's {artist} - {title} ({score:.2f} alike)")
                return None
            if score is not None and score < reference.OTHER:
                return f"{detail}; not Apple Music's {artist} - {title} ({score:.2f} alike)"
            if match.isrc or match.apple_id:
                return detail
            self.event("suspect", path, "SHAZAM_UNSURE",
                       f"{detail}, an entry with no ISRC or Apple Music release, and no preview "
                       f"to compare with; kept")
            return None
        if not titles.same_title(match.title, title):
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
        if verdict == "POSSIBLE_LOSSY_SOURCE" and not lossless:
            # A cut near 20 kHz is how every genuine MP3 320 or AAC 256 is
            # encoded; only a lower cut means it was made from a worse file.
            verdict = None
        if verdict:
            what = "lossless file" if lossless else f"{bit_rate // 1000}k {codec}"
            self.event("suspect", path, verdict,
                       f"{what} with an empty top band, likely made from a lower-quality file",
                       levels=levels)
