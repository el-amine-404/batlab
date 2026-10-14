"""Builds real files with ffmpeg and runs them through the guard.

    python3 -m unittest discover -s compose/musicguard/tests

Needs ffmpeg and mutagen (pip install mutagen). clamd is faked by a small
socket server, so no antivirus is needed to run these.
"""

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import mutagen  # noqa: E402
from mutagen.id3 import APIC, COMM, ID3, TIT2, TPE1, TXXX, WOAS, WXXX  # noqa: E402

import carry  # noqa: E402
import clamd  # noqa: E402
import credits  # noqa: E402
import dedup  # noqa: E402
import guard  # noqa: E402
import identify  # noqa: E402
import layout  # noqa: E402
import lyrics  # noqa: E402
import media  # noqa: E402
import reference  # noqa: E402
import tags  # noqa: E402
import upgrade  # noqa: E402

EICAR_MARK = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
MALWARE_MARK = b"PRETEND-MALWARE-SIGNATURE"


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", *args], check=True)


def tone(path, seconds=8, extra=()):
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=44100",
           "-ac", "2", *extra, path)


def noise(path, seconds=25):
    ffmpeg("-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:r=44100:a=0.3", "-ac", "2", path)


def jpeg(path):
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=640x360", "-frames:v", "1", path)


class FakeClamd(threading.Thread):
    """Answers INSTREAM like clamd: FOUND for EICAR and a marker, else OK."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(5)

    def run(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                buffer = b""
                while len(buffer) < 10:
                    buffer += conn.recv(65536)
                buffer = buffer[len(b"zINSTREAM\0"):]
                payload = b""
                while True:
                    while len(buffer) < 4:
                        buffer += conn.recv(65536)
                    size = struct.unpack("!L", buffer[:4])[0]
                    buffer = buffer[4:]
                    if size == 0:
                        break
                    while len(buffer) < size:
                        buffer += conn.recv(65536)
                    payload += buffer[:size]
                    buffer = buffer[size:]
                if EICAR_MARK in payload:
                    conn.sendall(b"stream: Eicar-Test-Signature FOUND\0")
                elif MALWARE_MARK in payload:
                    conn.sendall(b"stream: Test.Pretend FOUND\0")
                else:
                    conn.sendall(b"stream: OK\0")

    def stop(self):
        self.server.close()


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="musicguard-test-")
        self.inbox = os.path.join(self.tmp, "inbox")
        self.music = os.path.join(self.tmp, "music")
        self.aurral = os.path.join(self.tmp, "aurral")
        self.extras = os.path.join(self.tmp, "extras")
        self.quarantine = os.path.join(self.tmp, "quarantine")
        self.state = os.path.join(self.tmp, "state")
        for d in (self.inbox, os.path.join(self.inbox, "metube"), self.music, self.aurral, self.extras):
            os.makedirs(d)
        self.socket_path = os.path.join(self.tmp, "clamd.ctl")
        self.clamd = FakeClamd(self.socket_path)
        self.clamd.start()
        self.environment = mock.patch.dict(os.environ, {
            "MUSICGUARD_INBOX": self.inbox,
            "MUSICGUARD_EXTRAS": self.extras,
            "MUSICGUARD_ROOTS": f"{self.music}:{self.aurral}",
            "MUSICGUARD_VERIFY_ONLY": self.music,
            "MUSICGUARD_CREDITS": self.music,
            "MUSICGUARD_QUARANTINE": self.quarantine,
            "MUSICGUARD_STATE": self.state,
            "CLAMD_SOCKET": self.socket_path,
            "MUSICGUARD_LYRICS": "0",
            "MUSICGUARD_SETTLE_SECONDS": "0",
            "DISCORD_WEBHOOK": "",
        })
        self.environment.start()
        # Run inside the live container, the real Discord, Lidarr and
        # Navidrome settings are in the environment: none may reach a test.
        for key in list(os.environ):
            if key.startswith(("MUSICGUARD_", "DISCORD_", "LIDARR_", "NAVIDROME_")) \
                    and key not in ("MUSICGUARD_INBOX", "MUSICGUARD_EXTRAS", "MUSICGUARD_ROOTS",
                                    "MUSICGUARD_VERIFY_ONLY", "MUSICGUARD_CREDITS", "MUSICGUARD_QUARANTINE",
                                    "MUSICGUARD_STATE", "MUSICGUARD_LYRICS", "MUSICGUARD_SETTLE_SECONDS"):
                del os.environ[key]
        # And no test talks to the network unless it says so.
        self.offline = mock.patch.object(guard.urllib.request, "urlopen",
                                         side_effect=OSError("tests are offline"))
        self.offline.start()
        self.musicbrainz = mock.patch.object(guard.musicbrainz, "artist_side", return_value=None)
        self.musicbrainz.start()
        # Offline by default: no Shazam, no MusicBrainz credits.
        self.shazam = mock.patch.object(guard.identify, "recognise", return_value=None)
        self.shazam.start()
        self.credit_lookup = mock.patch.object(guard.credit_fix.Credits, "wanted", return_value=None)
        self.credit_lookup.start()
        self.reference = mock.patch.object(guard.reference, "compare", return_value=None)
        self.reference.start()
        self.guard = guard.Guard(guard.Config())

    def tearDown(self):
        self.offline.stop()
        self.reference.stop()
        self.credit_lookup.stop()
        self.shazam.stop()
        self.musicbrainz.stop()
        self.environment.stop()
        self.clamd.stop()
        for directory, _, files in os.walk(self.tmp):
            for name in files:
                try:
                    os.chmod(os.path.join(directory, name), 0o644)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def inbox_twice(self):
        # A file is taken on the second sight, once its size has held still.
        self.guard.inbox_pass()
        self.guard.inbox_pass()

    def all_files(self, root):
        found = []
        for directory, _, files in os.walk(root):
            found.extend(os.path.relpath(os.path.join(directory, n), root) for n in files)
        return sorted(found)

    def quarantined_reasons(self):
        reasons = []
        for directory, _, files in os.walk(self.quarantine):
            for name in files:
                if name.endswith(".why.json"):
                    with open(os.path.join(directory, name), encoding="utf-8") as handle:
                        reasons.append(json.load(handle)["reason"])
        return sorted(reasons)


class HostileInbox(GuardTestCase):
    def test_link_download_is_rebuilt_with_only_clean_tags(self):
        source = os.path.join(self.inbox, "metube", "dQw4w9WgXcQ.mp3")
        tone(source)
        cover = os.path.join(self.tmp, "cover.jpg")
        jpeg(cover)
        with open(cover, "rb") as handle:
            picture = handle.read() + b"MZ\x90\x00 appended payload after the JPEG end marker"
        id3 = ID3()
        id3.add(TIT2(encoding=3, text="Stormy - KATANA (Official Music Video)"))
        id3.add(TPE1(encoding=3, text="Stormy - Topic"))
        id3.add(COMM(encoding=3, lang="eng", desc="", text="Downloaded from http://free-mp3.example"))
        id3.add(WXXX(encoding=3, desc="", url="http://tracker.example"))
        id3.add(TXXX(encoding=3, desc="note", text="<script>alert(1)</script>"))
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=picture))
        id3.save(source)

        self.inbox_twice()

        self.assertFalse(os.path.exists(source), "the inbox copy is removed once filed")
        filed = os.path.join(self.extras, "Stormy", "KATANA", "01 - KATANA.mp3")
        self.assertEqual(self.all_files(self.extras),
                         ["Stormy/KATANA/01 - KATANA.mp3", "Stormy/KATANA/cover.jpg"])
        written = ID3(filed)
        self.assertEqual(written["TIT2"].text, ["KATANA"])
        self.assertEqual(written["TPE1"].text, ["Stormy"])
        self.assertFalse(written.getall("COMM"), "comments are not on the allow-list")
        self.assertFalse(written.getall("WXXX"), "URL frames are not on the allow-list")
        self.assertFalse([f for f in written.getall("TXXX") if f.desc == "note"])
        art = written.getall("APIC")[0].data
        self.assertTrue(art.startswith(b"\xff\xd8") and art.endswith(b"\xff\xd9"))
        self.assertNotIn(b"appended payload", art, "the cover is decoded and encoded again")
        self.assertEqual(self.quarantined_reasons(), [])

    def test_executable_named_mp3_is_quarantined(self):
        path = os.path.join(self.inbox, "metube", "song.mp3")
        with open(path, "wb") as handle:
            handle.write(b"MZ\x90\x00" + os.urandom(2048))
        self.inbox_twice()
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.quarantined_reasons(), ["EXECUTABLE"])
        self.assertEqual(self.all_files(self.extras), [])

    def test_web_page_named_flac_is_quarantined(self):
        path = os.path.join(self.inbox, "metube", "song.flac")
        with open(path, "wb") as handle:
            handle.write(b"<!DOCTYPE html><html><script>fetch('http://x')</script></html>")
        self.inbox_twice()
        self.assertEqual(self.quarantined_reasons(), ["EXECUTABLE"])

    def test_random_bytes_are_not_audio(self):
        path = os.path.join(self.inbox, "metube", "song.flac")
        with open(path, "wb") as handle:
            handle.write(os.urandom(50000))
        self.inbox_twice()
        self.assertIn(self.quarantined_reasons(), (["NOT_AUDIO"], ["MISMATCH"]))

    def test_known_malware_is_quarantined(self):
        path = os.path.join(self.inbox, "metube", "song.mp3")
        tone(path)
        with open(path, "ab") as handle:
            handle.write(MALWARE_MARK)
        self.inbox_twice()
        self.assertEqual(self.quarantined_reasons(), ["MALWARE"])

    def test_clamd_down_holds_files_instead_of_passing_them(self):
        path = os.path.join(self.inbox, "metube", "song.mp3")
        tone(path)
        self.clamd.stop()
        os.unlink(self.socket_path)
        self.inbox_twice()
        self.assertTrue(os.path.exists(path), "an unscanned file stays in the inbox")
        self.assertEqual(self.all_files(self.extras), [])
        self.assertEqual(self.quarantined_reasons(), [])

    def test_too_short_is_rejected(self):
        path = os.path.join(self.inbox, "metube", "blip.mp3")
        tone(path, seconds=2)
        self.inbox_twice()
        self.assertEqual(self.quarantined_reasons(), ["TOO_SHORT"])

    def test_music_video_keeps_only_its_audio(self):
        path = os.path.join(self.inbox, "metube", "clip.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=8",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
               "-c:v", "libx264", "-c:a", "aac", "-shortest",
               "-metadata", "title=Artist Name - Song Title (Clip Officiel)", path)
        self.inbox_twice()
        files = self.all_files(self.extras)
        self.assertEqual(files, ["Artist Name/Song Title/01 - Song Title.m4a"])
        info = media.probe(os.path.join(self.extras, files[0]))
        self.assertEqual([s["codec_type"] for s in info["streams"]], ["audio"])

    def test_wav_becomes_flac(self):
        path = os.path.join(self.inbox, "metube", "take.wav")
        tone(path)
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["Unknown Artist/take/01 - take.flac"])

    def test_opus_in_webm_becomes_opus(self):
        path = os.path.join(self.inbox, "metube", "a.webm")
        tone(path, extra=("-c:a", "libopus", "-metadata", "title=Song", "-metadata", "artist=Band"))
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["Band/Song/01 - Song.opus"])

    def test_thumbnail_beside_the_audio_becomes_square_cover(self):
        audio = os.path.join(self.inbox, "metube", "Band - Song.mp3")
        tone(audio)
        jpeg(os.path.join(self.inbox, "metube", "Band - Song.jpg"))
        self.inbox_twice()
        self.assertEqual(self.all_files(self.inbox), [])
        filed = os.path.join(self.extras, "Band", "Song", "01 - Song.mp3")
        art = ID3(filed).getall("APIC")[0].data
        cover = os.path.join(self.tmp, "check.jpg")
        with open(cover, "wb") as handle:
            handle.write(art)
        stream = media.probe(cover)["streams"][0]
        self.assertEqual(stream["width"], stream["height"])

    def test_unexpected_file_is_quarantined(self):
        path = os.path.join(self.inbox, "metube", "readme.exe")
        with open(path, "wb") as handle:
            handle.write(b"hello")
        self.inbox_twice()
        self.assertEqual(self.quarantined_reasons(), ["UNEXPECTED_FILE"])

    def test_partial_downloads_are_left_alone(self):
        path = os.path.join(self.inbox, "metube", "song.mp3.part")
        with open(path, "wb") as handle:
            handle.write(b"MZ partial")
        self.inbox_twice()
        self.assertTrue(os.path.exists(path))


class Libraries(GuardTestCase):
    def test_aurral_file_is_cleaned_in_place_keeping_musicbrainz_ids(self):
        album = os.path.join(self.aurral, "Band", "Record (2020)")
        os.makedirs(album)
        path = os.path.join(album, "01 - Song.flac")
        tone(path, extra=("-c:a", "flac"))
        audio = mutagen.File(path)
        audio["TITLE"] = "Song"
        audio["ARTIST"] = "Band"
        audio["ALBUM"] = "Record"
        audio["MUSICBRAINZ_ALBUMID"] = "40300661-e00c-4661-a722-ba1134daa6d9"
        audio["MUSICBRAINZ_TRACKID"] = "not-a-uuid"
        audio["DESCRIPTION"] = "ripped by someone, visit www.example.org"
        audio.save()

        self.guard.roots_pass()

        cleaned = mutagen.File(path)
        self.assertEqual(cleaned["musicbrainz_albumid"], ["40300661-e00c-4661-a722-ba1134daa6d9"])
        self.assertNotIn("musicbrainz_trackid", cleaned, "a malformed ID is dropped")
        self.assertNotIn("description", cleaned)
        self.assertEqual(self.all_files(self.aurral), ["Band/Record (2020)/01 - Song.flac"])
        # A second pass leaves an unchanged file alone.
        before = os.stat(path).st_mtime_ns
        self.guard.roots_pass()
        self.assertEqual(os.stat(path).st_mtime_ns, before)

    def test_unsupported_codec_in_a_library_is_reported_and_left(self):
        path = os.path.join(self.music, "Band", "Record", "01 - Song.wma")
        os.makedirs(os.path.dirname(path))
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:a", "wmav2", "-f", "asf", path)
        with mock.patch.dict(media.FORMATS, {".wma": {"asf"}}):
            with mock.patch.object(guard, "AUDIO_EXTENSIONS", guard.AUDIO_EXTENSIONS | {".wma"}):
                self.guard.roots_pass()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.quarantined_reasons(), [])
        self.assertEqual([e["reason"] for e in self.guard.events], ["UNSUPPORTED_CODEC"])

    def test_lidarr_file_is_verified_not_rewritten(self):
        path = os.path.join(self.music, "Band", "Record (2020)", "01 - Song.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        cover = os.path.join(self.tmp, "c.jpg")
        jpeg(cover)
        with open(cover, "rb") as handle:
            picture = handle.read() + b"MZ\x90\x00" + b"payload" * 20
        id3 = ID3()
        id3.add(TIT2(encoding=3, text="<script>alert(1)</script>"))
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=picture))
        id3.save(path)
        link = os.path.join(self.tmp, "seeding.mp3")
        os.link(path, link)
        before = os.stat(path)

        self.guard.roots_pass()

        after = os.stat(path)
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns),
                         "the file and its hardlink to the torrent are untouched")
        reasons = sorted(e["reason"] for e in self.guard.events if e["event"] == "suspect")
        self.assertEqual(reasons, ["APPENDED_DATA", "SUSPICIOUS_TAG"])
        self.assertEqual(self.quarantined_reasons(), [])

    def test_lyrics_left_behind_by_a_rename_are_removed(self):
        album = os.path.join(self.music, "Band", "Record (2023)")
        os.makedirs(album)
        tone(os.path.join(album, "1-01 - Song.mp3"))
        kept = os.path.join(album, "1-01 - Song.lrc")
        orphan = os.path.join(album, "01 - Song.lrc")
        fresh = os.path.join(album, "02 - Other.txt")
        for path in (kept, orphan, fresh):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("[00:01.00] la\n")
        old = time.time() - 2 * guard.ORPHAN_SECONDS
        for path in (kept, orphan):
            os.utime(path, (old, old))
        self.guard.roots_pass()
        self.assertTrue(os.path.exists(kept))
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(fresh), "a track may still be on its way")

    def test_aurral_marker_survives(self):
        path = os.path.join(self.aurral, "Band", "Record", "Song.opus")
        os.makedirs(os.path.dirname(path))
        tone(path, extra=("-c:a", "libopus"))
        audio = mutagen.File(path)
        audio["title"] = "Song"
        audio["AURRAL_IDS"] = "aurral:track:1234"
        audio.save()
        self.guard.roots_pass()
        self.assertEqual(mutagen.File(path)["aurral_ids"], ["aurral:track:1234"])

    def test_dangerous_file_in_a_library_is_quarantined(self):
        os.makedirs(os.path.join(self.music, "Band"))
        bad = os.path.join(self.music, "Band", "codec-pack.exe")
        with open(bad, "wb") as handle:
            handle.write(b"MZ")
        disguised = os.path.join(self.music, "Band", "cover.jpg")
        with open(disguised, "wb") as handle:
            handle.write(b"\x7fELF" + os.urandom(64))
        self.guard.roots_pass()
        self.assertFalse(os.path.exists(bad))
        self.assertFalse(os.path.exists(disguised))
        self.assertEqual(self.quarantined_reasons(), ["EXECUTABLE", "UNEXPECTED_FILE"])

    def test_fake_lossless_is_reported_not_rejected(self):
        os.makedirs(os.path.join(self.music, "Band", "Real"))
        os.makedirs(os.path.join(self.music, "Band", "Fake"))
        real = os.path.join(self.music, "Band", "Real", "01 - Real.flac")
        noise(real)
        lossy = os.path.join(self.tmp, "lossy.mp3")
        ffmpeg("-i", real, "-c:a", "libmp3lame", "-b:a", "128k", lossy)
        fake = os.path.join(self.music, "Band", "Fake", "01 - Fake.flac")
        ffmpeg("-i", lossy, fake)
        self.guard.roots_pass()
        suspects = [e for e in self.guard.events if e["event"] == "suspect"]
        self.assertEqual([os.path.basename(e["path"]) for e in suspects], ["01 - Fake.flac"])
        self.assertEqual(suspects[0]["reason"], "LOSSY_SOURCE")
        self.assertTrue(os.path.exists(fake), "a suspect is reported, never moved")

    def test_a_genuine_mp3_320_is_not_a_suspect(self):
        os.makedirs(os.path.join(self.music, "Band", "Record"))
        source = os.path.join(self.tmp, "source.flac")
        noise(source)
        genuine = os.path.join(self.music, "Band", "Record", "01 - Song.mp3")
        ffmpeg("-i", source, "-c:a", "libmp3lame", "-b:a", "320k", genuine)
        self.guard.roots_pass()
        self.assertEqual([e for e in self.guard.events if e["event"] == "suspect"], [])

    def test_an_mp3_320_made_from_a_128k_one_is_a_suspect(self):
        os.makedirs(os.path.join(self.music, "Band", "Record"))
        source, lossy = os.path.join(self.tmp, "source.flac"), os.path.join(self.tmp, "lossy.mp3")
        noise(source)
        ffmpeg("-i", source, "-c:a", "libmp3lame", "-b:a", "128k", lossy)
        upscaled = os.path.join(self.music, "Band", "Record", "01 - Song.mp3")
        ffmpeg("-i", lossy, "-c:a", "libmp3lame", "-b:a", "320k", upscaled)
        self.guard.roots_pass()
        self.assertEqual([e["reason"] for e in self.guard.events if e["event"] == "suspect"], ["LOSSY_SOURCE"])


class Migration(GuardTestCase):
    def test_old_layout_is_copied_into_the_new_one(self):
        old = os.path.join(self.tmp, "old", "~ M ~", "Megan", "[2020] Good News")
        os.makedirs(old)
        path = os.path.join(old, "02 Circles.mp3")
        tone(path)
        id3 = ID3()
        id3.add(TIT2(encoding=3, text="Circles"))
        id3.add(TPE1(encoding=3, text="Megan Thee Stallion"))
        id3.save(path)
        audio = mutagen.File(path, easy=True)
        audio["album"] = "Good News"
        audio["albumartist"] = "Megan Thee Stallion"
        audio["tracknumber"] = "2/17"
        audio["date"] = "2020-11-20"
        audio["musicbrainz_trackid"] = "6511d5dc-f8ee-4454-844f-e87246a8931c"
        audio.save()
        self.guard.migrate(os.path.join(self.tmp, "old"), self.music)
        self.assertTrue(os.path.exists(path), "the source is never changed")
        self.assertEqual(self.all_files(self.music),
                         ["Megan Thee Stallion/Good News (2020)/02 - Circles.mp3"])
        moved = mutagen.File(os.path.join(self.music, "Megan Thee Stallion", "Good News (2020)",
                                          "02 - Circles.mp3"), easy=True)
        self.assertEqual(moved["musicbrainz_trackid"], ["6511d5dc-f8ee-4454-844f-e87246a8931c"])
        self.assertEqual(moved["tracknumber"], ["2/17"])


class Duplicates(GuardTestCase):
    """The same song filed twice: the best audio stays, under the best tags."""

    def filed(self, root, relative, seconds=8, **tags_):
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tone(path, seconds)
        audio = mutagen.File(path, easy=True)
        for key, value in tags_.items():
            audio[key] = value
        audio.save()
        return path

    def test_a_video_copy_with_an_intro_gives_way_and_its_extra_tags_stay(self):
        video = self.filed(self.extras, "ElGrandeToto/Caméléon (2021)/15 - Mghayer (Prod. By Ysos) #CAMÉLÉON.mp3",
                           seconds=14, title="Mghayer (Prod. By Ysos) #CAMÉLÉON", artist="ElGrandeToto",
                           album="Caméléon", date="2021", tracknumber="15", isrc="BEIY42000017", genre="Rap")
        self.filed(self.extras, "ElGrandeToto/Caméléon (2021)/15 - Mghayer.mp3", seconds=10, title="Mghayer",
                   artist="ElGrandeToto", album="Caméléon", date="2021", tracknumber="15", isrc="BEIY42000017")
        with open(os.path.splitext(video)[0] + ".lrc", "w") as out:
            out.write("[00:01.00]la\n")
        self.guard.roots_pass()
        self.guard.dedup_pass()
        # The video's lyrics are of unknown origin and timed 4 s off: kept as plain text.
        self.assertEqual(self.all_files(self.extras), ["ElGrandeToto/Caméléon (2021)/15 - Mghayer.mp3",
                                                       "ElGrandeToto/Caméléon (2021)/15 - Mghayer.txt"])
        with open(os.path.join(self.extras, "ElGrandeToto", "Caméléon (2021)", "15 - Mghayer.txt")) as handle:
            self.assertEqual(handle.read(), "la\n")
        self.assertEqual(self.quarantined_reasons(), ["DUPLICATE"])
        self.assertIn("15 - Mghayer (Prod. By Ysos) #CAMÉLÉON.lrc",
                      [os.path.basename(f) for f in self.all_files(self.quarantine)])
        kept = mutagen.File(os.path.join(self.extras, "ElGrandeToto", "Caméléon (2021)", "15 - Mghayer.mp3"),
                            easy=True)
        self.assertEqual(kept["title"], ["Mghayer"])
        self.assertEqual(kept["genre"], ["Rap"])  # taken from the copy that went
        self.assertIn("deduplicated", [e["event"] for e in self.guard.events])

    def test_better_audio_under_worse_tags_keeps_the_audio_and_takes_the_tags(self):
        self.filed(self.extras, "Stormy/PIRATE.mp3", title="PIRATE (Official Video)", artist="Stormy")
        self.filed(self.extras, "Stormy/OMEGA (2025)/05 - PIRATE.flac", title="PIRATE",
                          artist="Stormy", album="OMEGA", date="2025", tracknumber="5")
        self.guard.roots_pass()
        self.guard.dedup_pass()
        self.assertEqual(self.all_files(self.extras), ["Stormy/OMEGA (2025)/05 - PIRATE.flac"])

    def test_lidarrs_copy_wins_and_is_never_touched(self):
        lidarr = self.filed(self.music, "Stormy/OMEGA (2025)/05 - PIRATE.mp3", title="PIRATE", artist="Stormy",
                            album="OMEGA", date="2025", tracknumber="5")
        self.filed(self.extras, "Stormy/OMEGA (2025)/05 - PIRATE.flac", title="PIRATE", artist="Stormy")
        before = os.stat(lidarr).st_mtime_ns
        self.guard.roots_pass()
        self.guard.dedup_pass()
        self.assertEqual(self.all_files(self.extras), [])
        self.assertEqual(os.stat(lidarr).st_mtime_ns, before)

    def test_a_remix_of_another_length_is_another_song(self):
        self.filed(self.extras, "Stormy/A/01 - PIRATE.mp3", seconds=8, title="PIRATE", artist="Stormy")
        self.filed(self.extras, "Stormy/B/01 - PIRATE.mp3", seconds=30, title="PIRATE", artist="Stormy")
        self.guard.roots_pass()
        self.guard.dedup_pass()
        self.assertEqual(len(self.all_files(self.extras)), 2)

    def test_lengths_apart_are_one_song_only_when_they_sound_alike(self):
        self.filed(self.extras, "Stormy/A/01 - PIRATE.mp3", seconds=8, title="PIRATE", artist="Stormy")
        self.filed(self.extras, "Stormy/B/01 - PIRATE.mp3", seconds=20, title="PIRATE", artist="Stormy")
        self.guard.roots_pass()
        with mock.patch.object(self.guard, "alike", return_value=False):
            self.guard.dedup_pass()
        self.assertEqual(len(self.all_files(self.extras)), 2)
        with mock.patch.object(self.guard, "alike", return_value=True):
            self.guard.dedup_pass()
        self.assertEqual(len(self.all_files(self.extras)), 1)
        self.assertEqual(self.quarantined_reasons(), ["DUPLICATE"])

    def test_a_better_download_of_a_filed_song_replaces_it(self):
        self.filed(self.extras, "Stormy/OMEGA (2025)/05 - PIRATE.mp3", title="PIRATE", artist="Stormy",
                   album="OMEGA", date="2025", tracknumber="5")
        self.guard.roots_pass()
        source = os.path.join(self.inbox, "songdrop", "Stormy - PIRATE.flac")
        os.makedirs(os.path.dirname(source))
        tone(source)
        audio = mutagen.File(source, easy=True)
        audio["title"], audio["artist"] = "PIRATE", "Stormy"
        audio.save()
        self.inbox_twice()
        self.guard.roots_pass()
        self.guard.dedup_pass()
        self.assertEqual(self.all_files(self.extras), ["Stormy/OMEGA (2025)/05 - PIRATE.flac"])

    def test_a_worse_download_of_a_filed_song_is_dropped(self):
        self.filed(self.extras, "Stormy/PIRATE.flac", title="PIRATE", artist="Stormy")
        self.guard.roots_pass()
        source = os.path.join(self.inbox, "songdrop", "Stormy - PIRATE.mp3")
        os.makedirs(os.path.dirname(source))
        tone(source)
        audio = mutagen.File(source, easy=True)
        audio["title"], audio["artist"] = "PIRATE", "Stormy"
        audio.save()
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["Stormy/PIRATE.flac"])


class DedupRules(unittest.TestCase):
    def track(self, title="PIRATE", artist="Stormy", seconds=200, codec="mp3", bitrate=160000, owned=True, **more):
        values = {"title": [title], "artist": [artist], **{k: [v] for k, v in more.items()}}
        return dedup.Track(f"/x/{title}-{seconds}-{codec}", values, seconds, codec, bitrate, owned)

    def test_one_song(self):
        never = lambda a, b: self.fail("no fingerprint needed")  # noqa: E731
        self.assertTrue(dedup.same_song(self.track(isrc="X1"), self.track("Other", "Else", isrc="X1"), never))
        self.assertTrue(dedup.same_song(self.track(), self.track("PIRATE (Official Video)", seconds=203), never))
        self.assertFalse(dedup.same_song(self.track(), self.track(seconds=260), never))
        self.assertFalse(dedup.same_song(self.track(), self.track("KATANA"), never))

    def test_best_audio(self):
        padded = self.track(seconds=220, codec="opus", bitrate=160000)
        exact = self.track(seconds=214, codec="opus", bitrate=150000)
        self.assertIs(dedup.best_audio([padded, exact]), exact)
        mp3 = self.track(codec="mp3", bitrate=320000)
        flac = self.track(codec="flac", bitrate=900000)
        self.assertIs(dedup.best_audio([mp3, flac]), flac)
        opus = self.track(codec="opus", bitrate=160000)
        self.assertIs(dedup.best_audio([self.track(codec="mp3", bitrate=192000), opus]), opus)
        lidarr = self.track(codec="mp3", bitrate=128000, owned=False)
        self.assertIs(dedup.best_audio([flac, lidarr]), lidarr)

    def test_best_tags(self):
        dirty = self.track("Mghayer (Prod. By Ysos) #CAMÉLÉON", album="Caméléon", genre="Rap")
        clean = self.track("Mghayer", album="Caméléon")
        merged = dedup.best_tags([dirty, clean])
        self.assertEqual(merged["title"], ["Mghayer"])
        self.assertEqual(merged["genre"], ["Rap"])


class Identification(GuardTestCase):
    def test_shazam_names_a_link_better_than_its_video_title(self):
        path = os.path.join(self.inbox, "metube", "Georgie Fame - Stormy.mp3")
        tone(path)
        self.guard.config.identify = True
        guard.identify.recognise.return_value = identify.Match(
            "PIRATE", "Stormy", album="OMEGA - EP", year="2025", isrc="FRIDO2417560")
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["Stormy/OMEGA - EP (2025)/PIRATE.mp3"])

    def spotdl_la_paille(self):
        path = os.path.join(self.inbox, "songdrop", "ElGrandeToto - La Paille.mp3")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"] = "La Paille", "ElGrandeToto"
        audio.save()
        id3 = ID3(path)  # what spotDL leaves: the Spotify URL, an empty ISRC
        id3.add(WOAS(url="https://open.spotify.com/track/4MjhDzeuvIU3f4E8SqGH4q"))
        id3.save()
        self.guard.config.identify = True
        guard.identify.recognise.return_value = identify.Match("Pala Lagat Rahe Ho Lahnga", "Chhote Raja")

    def test_a_catalogue_named_download_of_other_audio_is_quarantined(self):
        self.spotdl_la_paille()
        guard.reference.compare.return_value = None  # no Apple preview to compare with
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), [])
        self.assertEqual(self.quarantined_reasons(), ["WRONG_SONG"])

    def test_a_catalogue_named_download_like_the_real_song_keeps_its_name(self):
        self.spotdl_la_paille()
        guard.reference.compare.return_value = 0.95
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["ElGrandeToto/La Paille/01 - La Paille.mp3"])

    def test_a_video_title_still_gives_way_to_shazam(self):
        self.spotdl_la_paille()
        ID3(os.path.join(self.inbox, "songdrop", "ElGrandeToto - La Paille.mp3")).delete()
        self.inbox_twice()
        self.assertEqual(self.quarantined_reasons(), [])
        self.assertEqual(self.all_files(self.extras),
                         ["Chhote Raja/Pala Lagat Rahe Ho Lahnga/01 - Pala Lagat Rahe Ho Lahnga.mp3"])

    def test_spotifys_album_stays_whatever_apple_dates_it(self):
        path = os.path.join(self.inbox, "songdrop", "ElGrandeToto - Halla halla.mp3")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"], audio["album"] = "Halla halla", "ElGrandeToto", "Caméléon"
        audio["albumartist"], audio["date"], audio["tracknumber"] = "ElGrandeToto", "2021-01-08", "6/17"
        audio.save()
        id3 = ID3(path)
        id3.add(WOAS(url="https://open.spotify.com/track/3mEdyuPV0yvg8F5op87n25"))
        id3.save()
        self.guard.config.identify = True
        guard.identify.recognise.return_value = identify.Match("Halla halla", "ElGrandeToto", apple_id="1")
        apple = guard.apple.Release({"collectionName": "Caméléon", "trackName": "Halla halla",
                                     "artistName": "ElGrandeToto", "trackNumber": 6, "trackCount": 17,
                                     "releaseDate": "2020-12-25T08:00:00Z", "primaryGenreName": "Rap"},
                                    {"artistName": "ElGrandeToto"})
        with mock.patch.object(guard.apple, "release", return_value=apple):
            self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["ElGrandeToto/Caméléon (2021)/06 - Halla halla.mp3"])
        filed = mutagen.File(os.path.join(self.extras, "ElGrandeToto", "Caméléon (2021)", "06 - Halla halla.mp3"),
                             easy=True)
        self.assertEqual(filed["genre"], ["Rap"])  # a gap Apple filled

    def test_a_wrong_song_in_a_library_is_quarantined_once_new(self):
        path = os.path.join(self.music, "Georgie Fame", "Hits (2004)", "26 - Stormy.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"] = "Stormy", "Georgie Fame"
        audio.save()
        guard.identify.recognise.return_value = identify.Match("PIRATE", "Stormy", isrc="FRIDO2417560")
        self.guard.roots_pass()
        self.assertEqual(self.quarantined_reasons(), ["WRONG_SONG"])

    def lidarr_4l(self):
        path = os.path.join(self.music, "21 Savage", "i am i was (2018)", "15 - 4L.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"] = "4L", "21 Savage"
        audio.save()
        return path

    def test_the_real_song_overrules_shazam(self):
        path = self.lidarr_4l()
        guard.identify.recognise.return_value = identify.Match("Just TEN", "Thedjnino", isrc="QZ0000000001")
        guard.reference.compare.return_value = 0.99
        self.guard.roots_pass()
        self.assertTrue(os.path.exists(path))
        self.assertIn("SHAZAM_OVERRULED", [e["reason"] for e in self.guard.events])

    def test_a_file_unlike_the_real_song_is_wrong_whatever_shazam_is(self):
        self.lidarr_4l()
        guard.identify.recognise.return_value = identify.Match("Just TEN", "Thedjnino")
        guard.reference.compare.return_value = 0.55
        self.guard.roots_pass()
        self.assertEqual(self.quarantined_reasons(), ["WRONG_SONG"])

    def test_a_weak_shazam_entry_alone_moves_nothing(self):
        path = self.lidarr_4l()
        guard.identify.recognise.return_value = identify.Match("Just TEN", "Thedjnino")
        self.guard.roots_pass()
        self.assertTrue(os.path.exists(path))
        self.assertIn("SHAZAM_UNSURE", [e["reason"] for e in self.guard.events])

    def test_an_audited_file_is_reported_not_moved(self):
        path = os.path.join(self.music, "A", "B (2004)", "01 - Stormy.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"] = "Stormy", "Georgie Fame"
        audio.save()
        guard.identify.recognise.return_value = identify.Match("PIRATE", "Stormy")
        rows = self.guard.audit([self.music])
        self.assertEqual([r["verdict"] for r in rows], ["WRONG"])
        self.guard.roots_pass()
        self.assertTrue(os.path.exists(path))

    def test_same_artist_other_title_is_reported(self):
        path = os.path.join(self.music, "Stormy", "Captain (2021)", "01 - Captain - la releve.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"] = "Captain - la relève", "Stormy"
        audio.save()
        guard.identify.recognise.return_value = identify.Match("Hugo", "Stormy")
        self.guard.roots_pass()
        self.assertTrue(os.path.exists(path))
        self.assertIn("WRONG_TITLE", [e["reason"] for e in self.guard.events])

    def test_lidarr_artist_credit_is_completed(self):
        path = os.path.join(self.music, "Manal", "KATANA (2026)", "01 - KATANA.flac")
        os.makedirs(os.path.dirname(path))
        tone(path, extra=("-c:a", "flac"))
        audio = mutagen.File(path)
        audio["title"], audio["artist"], audio["albumartist"] = "KATANA", "Manal", "Manal"
        audio["musicbrainz_albumid"] = "2fcb0039-ea58-4755-95b6-b88826699c4b"
        audio.save()
        link = os.path.join(self.tmp, "seed.flac")
        os.link(path, link)
        guard.credit_fix.Credits.wanted.return_value = ("Manal & Stormy", ["Manal", "Stormy"], "Manal & Stormy")
        self.guard.roots_pass()
        done = mutagen.File(path)
        self.assertEqual((done["artist"], done["albumartist"], done["artists"]),
                         (["Manal & Stormy"], ["Manal & Stormy"], ["Manal", "Stormy"]))
        self.assertEqual(os.stat(path).st_ino, os.stat(link).st_ino, "edited in place")


class LinkMetadata(GuardTestCase):
    def apple_release(self):
        track = {"wrapperType": "track", "collectionName": "OMEGA - EP", "trackName": "PIRATE",
                 "artistName": "Stormy", "trackNumber": 5, "trackCount": 6, "discNumber": 1,
                 "discCount": 1, "releaseDate": "2025-01-09T12:00:00Z", "primaryGenreName": "Hip-Hop/Rap",
                 "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/x/cover.jpg/100x100bb.jpg"}
        release = guard.apple.Release(track, {"artistName": "Stormy"})
        release.cover = lambda: None
        return release

    def test_apple_music_gives_the_album_track_number_and_genre(self):
        path = os.path.join(self.inbox, "metube", "Georgie Fame - Stormy.mp3")
        tone(path)
        audio = mutagen.File(path, easy=True)
        audio["title"], audio["artist"], audio["genre"] = "Stormy", "Georgie Fame", "Soul Jazz"
        audio.save()
        self.guard.config.identify = True
        guard.identify.recognise.return_value = identify.Match("PIRATE", "Stormy", album="OMEGA - EP",
                                                               year="2025", apple_id="1786653398",
                                                               genre="Hip-Hop/Rap")
        with mock.patch.object(guard.apple, "release", return_value=self.apple_release()):
            self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), ["Stormy/OMEGA (2025)/05 - PIRATE.mp3"])
        done = mutagen.File(os.path.join(self.extras, "Stormy/OMEGA (2025)/05 - PIRATE.mp3"), easy=True)
        self.assertEqual((done["genre"], done["tracknumber"], done["date"], done["musicbrainz_albumtype"]),
                         (["Hip-Hop/Rap"], ["5/6"], ["2025-01-09"], ["ep"]))

    def test_a_song_already_in_the_library_is_not_filed_twice(self):
        existing = os.path.join(self.music, "Drake", "Views (2016)", "12 - One Dance.mp3")
        os.makedirs(os.path.dirname(existing))
        for path in (existing, os.path.join(self.inbox, "metube", "Drake - One Dance.mp3")):
            tone(path)
            audio = mutagen.File(path, easy=True)
            audio["title"], audio["artist"] = "One Dance", "Drake"
            audio.save()
        self.inbox_twice()
        self.assertEqual(self.all_files(self.extras), [])
        self.assertEqual(self.all_files(os.path.join(self.inbox, "metube")), [])
        self.assertIn("ALREADY_IN_LIBRARY", [e["reason"] for e in self.guard.events])


class NavidromeScans(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.guard.config.navidrome_user = "musicguard"
        self.scan = mock.patch.object(guard.navidrome, "start_scan")
        self.scan.start()

    def tearDown(self):
        self.scan.stop()
        super().tearDown()

    def test_navidrome_scans_only_once_every_file_is_checked(self):
        path = os.path.join(self.music, "A", "B (2020)", "01 - C.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        self.guard.config.settle = 3600  # just arrived: not checked yet
        self.guard.roots_pass()
        self.guard.scan_navidrome()
        guard.navidrome.start_scan.assert_not_called()

        self.guard.config.settle = 0
        self.guard.roots_pass()
        self.guard.scan_navidrome()
        guard.navidrome.start_scan.assert_called_once()

        self.guard.roots_pass()  # nothing new: no second scan
        self.guard.scan_navidrome()
        guard.navidrome.start_scan.assert_called_once()

    def test_lyrics_written_by_another_tool_bring_a_scan(self):
        path = os.path.join(self.music, "A", "B (2020)", "01 - C.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        self.guard.roots_pass()
        self.guard.scan_navidrome()
        guard.navidrome.start_scan.reset_mock()
        with open(path[:-4] + ".lrc", "w") as out:  # Stanzarr writes lyrics
            out.write("[00:01.00] la\n")
        self.guard.roots_pass()
        self.guard.scan_navidrome()
        guard.navidrome.start_scan.assert_called_once()
        self.guard.roots_pass()  # nothing new: no scan
        self.guard.scan_navidrome()
        guard.navidrome.start_scan.assert_called_once()

    def test_new_albums_are_announced_once_playable(self):
        self.guard.config.ready_webhook = "http://discord.invalid/hook"
        path = os.path.join(self.music, "Billie Eilish", "HIT ME HARD AND SOFT (2024)", "01 - SKINNY.mp3")
        os.makedirs(os.path.dirname(path))
        tone(path)
        sent = []
        with mock.patch.object(guard.urllib.request, "urlopen",
                               side_effect=lambda request, timeout: sent.append(json.loads(request.data)) or mock.MagicMock()):
            self.guard.roots_pass()
            self.guard.scan_navidrome()
            self.guard.roots_pass()
            self.guard.scan_navidrome()
        self.assertEqual(len(sent), 1)
        self.assertIn("Billie Eilish – HIT ME HARD AND SOFT (2024)", sent[0]["content"])


class FakeLidarr:
    """Lidarr's API as upgrade.Lidarr uses it."""

    def __init__(self):
        self.albums = {}  # release group id -> album
        self.files = {}  # album id -> path of the song's file
        self.searched = []
        self.removed = []

    def album(self, group_id):
        return self.albums.get(group_id)

    def add_album(self, group_id):
        self.albums[group_id] = {"id": len(self.albums) + 1, "monitored": True}
        self.searched.append(group_id)
        return self.albums[group_id]

    def want(self, album):
        album["monitored"] = True

    def track_file(self, album_id, recording_ids, title):
        return self.files.get(album_id)

    def remove_track_file(self, group_id, path):
        os.unlink(path)  # Lidarr moves it to its recycle bin
        self.removed.append((group_id, path))
        return True


class Upgrades(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.guard.config.upgrade = True
        self.lidarr = FakeLidarr()
        self.client = mock.patch.object(guard.upgrade, "Lidarr", return_value=self.lidarr)
        self.client.start()
        self.lookup = mock.patch.object(guard.upgrade, "recordings_for", return_value=[{
            "id": "rec-1", "releases": [{"status": "Official", "date": "2016-05-06",
                                         "release-group": {"id": "views", "title": "Views",
                                                           "primary-type": "Album"}}]}])
        self.lookup.start()

    def tearDown(self):
        self.lookup.stop()
        self.client.stop()
        super().tearDown()

    def extras_song(self):
        path = os.path.join(self.extras, "Drake", "One Dance - Single (2016)", "One Dance.opus")
        os.makedirs(os.path.dirname(path))
        tone(path, extra=("-c:a", "libopus"))
        audio = mutagen.File(path)
        audio["title"], audio["artist"] = "One Dance", "Drake"
        audio.save()
        for name in ("One Dance.lrc", "cover.jpg"):
            with open(os.path.join(os.path.dirname(path), name), "w") as out:
                out.write("x")
        return path

    def test_link_song_is_replaced_once_lidarr_has_it_verified(self):
        path = self.extras_song()
        self.guard.upgrade_pass()
        self.assertEqual(self.lidarr.searched, ["views"], "the full album is requested")
        self.assertTrue(os.path.exists(path))

        lidarr_file = os.path.join(self.music, "Drake", "Views (2016)", "12 - One Dance.flac")
        os.makedirs(os.path.dirname(lidarr_file))
        tone(lidarr_file, extra=("-c:a", "flac"))
        self.lidarr.files[1] = lidarr_file
        self.guard.upgrade_pass()
        self.assertTrue(os.path.exists(path), "kept until musicguard has verified Lidarr's file")

        self.guard.roots_pass()
        self.guard.upgrade_pass()
        self.assertFalse(os.path.exists(os.path.join(self.extras, "Drake")), "copy, lyrics, cover, folders gone")
        self.assertTrue(os.path.exists(lidarr_file))
        self.assertEqual(self.quarantined_reasons(), ["UPGRADED"], "kept a while, in case")
        self.assertNotIn("quarantined", [e["event"] for e in self.guard.events], "an upgrade is not news")

    def upgraded(self, lyrics_text, lidarr_seconds=8, album="Views", lidarr_lyrics=None):
        """An extras song with these lyrics and a cover, replaced by Lidarr's copy."""
        path = os.path.join(self.extras, "Drake", "Views (2016)", "12 - One Dance.opus")
        os.makedirs(os.path.dirname(path))
        tone(path, extra=("-c:a", "libopus"))
        audio = mutagen.File(path)
        audio["title"], audio["artist"], audio["album"] = "One Dance", "Drake", album
        audio.save()
        jpeg(os.path.join(os.path.dirname(path), "cover.jpg"))
        with open(os.path.splitext(path)[0] + ".lrc", "w") as out:
            out.write(lyrics_text)
        self.guard.upgrade_pass()
        lidarr_file = os.path.join(self.music, "Drake", "Views (2016)", "12 - One Dance.flac")
        os.makedirs(os.path.dirname(lidarr_file))
        tone(lidarr_file, seconds=lidarr_seconds, extra=("-c:a", "flac"))
        audio = mutagen.File(lidarr_file)
        audio["title"], audio["artist"], audio["album"] = "One Dance", "Drake", "Views"
        audio.save()
        if lidarr_lyrics:
            with open(os.path.splitext(lidarr_file)[0] + ".lrc", "w") as out:
                out.write(lidarr_lyrics)
        self.lidarr.files[1] = lidarr_file
        self.guard.roots_pass()
        self.guard.upgrade_pass()
        self.assertFalse(os.path.exists(path))
        return os.path.dirname(lidarr_file)

    def read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_a_persons_lyrics_and_the_albums_cover_go_to_lidarrs_copy(self):
        mine = "[by:a person]\n[re:Stanzarr editor 2026-10-14]\n[00:01.00]ana\n"
        folder = self.upgraded(mine)
        self.assertEqual(self.read(os.path.join(folder, "12 - One Dance.lrc")), mine)
        self.assertTrue(os.path.exists(os.path.join(folder, "cover.jpg")))
        self.assertIn("12 - One Dance.lrc", [os.path.basename(f) for f in self.all_files(self.quarantine)],
                      "the original goes to quarantine with its song")

    def test_synced_lyrics_of_unknown_origin_go_over_as_plain_text(self):
        # A synced .lrc not flagged AI would stop Stanzarr asking LRCLIB for good.
        folder = self.upgraded("[ar:Drake]\n[00:01.00]ana\n[00:02.50]<00:02.60>nta\n")
        self.assertFalse(os.path.exists(os.path.join(folder, "12 - One Dance.lrc")))
        self.assertEqual(self.read(os.path.join(folder, "12 - One Dance.txt")), "ana\nnta\n")

    def test_synced_lyrics_timed_for_another_length_go_over_as_plain_text(self):
        folder = self.upgraded("[re:LRCLIB via Stanzarr]\n[00:01.00]ana\n", lidarr_seconds=12)
        self.assertFalse(os.path.exists(os.path.join(folder, "12 - One Dance.lrc")))
        self.assertEqual(self.read(os.path.join(folder, "12 - One Dance.txt")), "ana\n")

    def test_lidarrs_own_better_lyrics_and_other_albums_covers_are_left_alone(self):
        theirs = "[by:a person]\n[00:03.00]theirs\n"
        folder = self.upgraded("[re:LRCLIB via Stanzarr]\n[00:01.00]ana\n", album="One Dance - Single",
                               lidarr_lyrics=theirs)
        self.assertEqual(self.read(os.path.join(folder, "12 - One Dance.lrc")), theirs)
        self.assertFalse(os.path.exists(os.path.join(folder, "cover.jpg")), "a single's cover is not the album's")

    def test_quarantine_is_emptied_after_its_days(self):
        old = os.path.join(self.quarantine, "2026-01-02", "Drake")
        recent = os.path.join(self.quarantine, datetime.now().strftime("%Y-%m-%d"))
        other = os.path.join(self.quarantine, "notes")
        for folder in (old, recent, other):
            os.makedirs(folder)
            with open(os.path.join(folder, "song.mp3"), "w") as out:
                out.write("x")
        os.chmod(os.path.join(old, "song.mp3"), 0)
        self.guard.purge_quarantine()
        self.assertEqual(sorted(os.listdir(self.quarantine)), sorted([os.path.basename(recent), "notes"]))
        self.guard.config.quarantine_days = 0
        os.makedirs(old)
        self.guard.purge_quarantine()
        self.assertTrue(os.path.isdir(old), "0 keeps everything")

    def test_a_lidarr_single_gets_its_album_then_goes(self):
        self.guard.config.lidarr_root = self.music
        single = os.path.join(self.music, "Drake", "One Dance (2016)", "01 - One Dance.flac")
        os.makedirs(os.path.dirname(single))
        tone(single, extra=("-c:a", "flac"))
        audio = mutagen.File(single)
        audio["title"], audio["artist"] = "One Dance", "Drake"
        audio["musicbrainz_albumtype"] = "single"
        audio["musicbrainz_releasegroupid"] = "6f4c3295-db6b-4954-b0f0-c95a6b79b458"
        audio["musicbrainz_trackid"] = "57c319d6-31ed-4668-acf1-13350ab8b67b"
        audio.save()
        with mock.patch.object(guard.upgrade, "recording", return_value={"id": "rec-1", "releases": []}):
            self.guard.roots_pass()
            self.guard.upgrade_pass()
            self.assertEqual(self.lidarr.searched, ["views"], "the album is requested")
            self.assertTrue(os.path.exists(single))

            album_file = os.path.join(self.music, "Drake", "Views (2016)", "12 - One Dance.flac")
            os.makedirs(os.path.dirname(album_file))
            tone(album_file, extra=("-c:a", "flac"))
            self.lidarr.files[1] = album_file
            self.guard.roots_pass()
            self.guard.upgrade_pass()
        self.assertEqual(self.lidarr.removed, [("6f4c3295-db6b-4954-b0f0-c95a6b79b458", single)])
        self.assertTrue(os.path.exists(album_file))

    def test_an_album_track_is_never_taken_for_a_single(self):
        self.guard.config.lidarr_root = self.music
        path = os.path.join(self.music, "Drake", "Views (2016)", "12 - One Dance.flac")
        os.makedirs(os.path.dirname(path))
        tone(path, extra=("-c:a", "flac"))
        audio = mutagen.File(path)
        audio["title"], audio["artist"], audio["musicbrainz_albumtype"] = "One Dance", "Drake", "album"
        audio.save()
        self.guard.roots_pass()
        self.guard.upgrade_pass()
        self.assertEqual(self.lidarr.searched, [])

    def test_song_musicbrainz_does_not_know_is_kept_and_asked_weekly(self):
        path = self.extras_song()
        guard.upgrade.recordings_for.return_value = []
        self.guard.upgrade_pass()
        self.guard.upgrade_pass()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(guard.upgrade.recordings_for.call_count, 1)
        self.assertEqual([e["reason"] for e in self.guard.events], ["NOT_IN_MUSICBRAINZ"])


class ReferenceRules(unittest.TestCase):
    def test_similarity_finds_the_preview_inside_the_song(self):
        song = [(i * 2654435761) & 0xFFFFFFFF for i in range(300)]
        self.assertEqual(reference.similarity(song[100:140], song), 1.0)
        other = [(i * 40503 + 7) & 0xFFFFFFFF for i in range(40)]
        self.assertLess(reference.similarity(other, song), reference.SAME)

    def test_fpcalc_tells_the_same_song_from_another(self):
        if not shutil.which("fpcalc"):
            self.skipTest("fpcalc not installed (it is in the image)")
        workdir = tempfile.mkdtemp()
        try:
            song, clip, other = (os.path.join(workdir, n) for n in ("song.flac", "clip.flac", "other.flac"))
            # Two different melodies: Chromaprint hears pitch, so noise
            # against noise would look alike (0.76) without being a song.
            ffmpeg("-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*(262+131*floor(mod(t*2\\,4)))*t)"
                   "+0.2*sin(2*PI*(392+98*floor(mod(t*3\\,5)))*t):d=60:s=44100", song)
            ffmpeg("-ss", "20", "-t", "30", "-i", song, clip)
            ffmpeg("-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*(311+155*floor(mod(t*5\\,3)))*t)"
                   "+0.2*sin(2*PI*(466+117*floor(mod(t*1.5\\,6)))*t):d=30:s=44100", other)
            same = reference.similarity(reference.fingerprint(clip), reference.fingerprint(song))
            different = reference.similarity(reference.fingerprint(other), reference.fingerprint(song))
            self.assertGreaterEqual(same, reference.SAME)
            self.assertLess(different, reference.OTHER)
        finally:
            shutil.rmtree(workdir)


class CarryRules(unittest.TestCase):
    def test_origins_as_stanzarr_reads_them(self):
        self.assertEqual(carry.origin("[by:AI-generated, corrected by a person]\n[00:01.00]a"), "ai")
        self.assertEqual(carry.origin("[re:LRCLIB via Stanzarr]\n[00:01.00]a"), "lrclib")
        self.assertEqual(carry.origin("[by:a person]\n[00:01.00]a"), "person")
        self.assertEqual(carry.origin("[re:Stanzarr editor 2026-10-14]\n[00:01.00]a"), "person")
        self.assertEqual(carry.origin("[ar:Drake]\n[00:01.00]a"), "unknown")

    def test_album_names_compare_without_editions(self):
        self.assertEqual(carry.album_key({"album": ["Caméléon (Deluxe)"]}), carry.album_key({"album": ["caméléon"]}))


class UpgradeRules(unittest.TestCase):
    def test_the_full_album_wins_over_single_compilation_and_bootleg(self):
        def release(group, kind, date, secondary=None, status="Official"):
            return {"status": status, "date": date,
                    "release-group": {"id": group, "title": group, "primary-type": kind,
                                      "secondary-types": secondary or []}}
        recordings = [{"releases": [release("single", "Single", "2016-04-05"),
                                    release("hits", "Album", "2016-01-01", ["Compilation"]),
                                    release("bootleg", "Album", "2015-01-01", status="Bootleg"),
                                    release("views", "Album", "2016-05-06")]}]
        self.assertEqual(upgrade.best_release_group(recordings), ("views", "views", "Album"))
        self.assertEqual(upgrade.best_release_group([{"releases": [release("single", "Single", "2016")]}]),
                         ("single", "single", "Single"))
        self.assertIsNone(upgrade.best_release_group([{"releases": [release("mix", "Album", "2016", ["DJ-mix"])]}]))


class IdentifyRules(unittest.TestCase):
    def test_parse(self):
        answer = {"track": {"title": "PIRATE", "subtitle": "Stormy", "isrc": "FRIDO2417560",
                            "sections": [{"metadata": [{"title": "Album", "text": "OMEGA - EP"},
                                                       {"title": "Released", "text": "2025"}]}],
                            "genres": {"primary": "Hip-Hop/Rap"},
                            "hub": {"actions": [{"type": "applemusicplay", "id": "1786653398"}]}}}
        match = identify.parse(answer)
        self.assertEqual((match.title, match.artist, match.album, match.year), ("PIRATE", "Stormy", "OMEGA - EP", "2025"))
        self.assertEqual((match.apple_id, match.genre), ("1786653398", "Hip-Hop/Rap"))
        self.assertIsNone(identify.parse({"matches": []}))

    def test_agreement(self):
        match = identify.Match("KATANA", "Manal & Stormy")
        self.assertTrue(identify.agrees(match, "KATANA", "Manal"))
        self.assertFalse(identify.contradicts(match, "KATANA (Remix)", "Someone"))
        self.assertTrue(identify.contradicts(identify.Match("PIRATE", "Stormy"), "Stormy", "Georgie Fame"))
        self.assertTrue(identify.agrees(identify.Match("One Dance (feat. Wizkid & Kyla)", "Drake"),
                                        "One Dance", "Drake"))

    def test_release_credits(self):
        release = {"artist-credit": [{"name": "Manal", "joinphrase": " & "}, {"name": "Stormy"}],
                   "media": [{"tracks": [{"id": "t1", "recording": {"id": "r1"},
                                          "artist-credit": [{"name": "Manal", "joinphrase": " feat. "},
                                                            {"name": "Stormy"}]}]}]}
        found = credits.release_credits(release)
        self.assertEqual(found["album"], ("Manal & Stormy", ["Manal", "Stormy"]))
        self.assertEqual(found["tracks"]["r1"], ("Manal feat. Stormy", ["Manal", "Stormy"]))


class TagRules(unittest.TestCase):
    def test_values_are_checked(self):
        found, findings = tags.collect([
            ("Title", "Song‮3pm.exe"),
            ("MusicBrainz Album Id", "40300661-E00C-4661-A722-BA1134DAA6D9"),
            ("musicbrainz_artistid", "4475daf7-6095-4b1a-b13c-73f086a5b174/2df91a44-ab7c-4bf5-929e-6453c73c2d51"),
            ("acoustid_id", "nope"),
            ("date", "20200517"),
            ("track", "3"),
            ("TRACKTOTAL", "12"),
            ("isrc", "us-rc1-76-07839"),
            ("genre", "{{7*7}}"),
            ("artist", "Band https://spam.example"),
            ("comment", "anything"),
            ("TXXX:AURRAL_IDS", "aurral:1"),
        ])
        self.assertEqual(found["title"], ["Song3pm.exe"], "direction overrides are removed")
        self.assertEqual(found["musicbrainz_albumid"], ["40300661-e00c-4661-a722-ba1134daa6d9"])
        self.assertEqual(len(found["musicbrainz_artistid"]), 2)
        self.assertNotIn("acoustid_id", found)
        self.assertEqual(found["date"], ["2020-05-17"])
        self.assertEqual(found["tracknumber"], ["3/12"])
        self.assertEqual(found["isrc"], ["USRC17607839"])
        self.assertNotIn("genre", found)
        self.assertEqual(found["artist"], ["Band"])
        self.assertNotIn("comment", found)
        self.assertEqual(found["aurral_ids"], ["aurral:1"])
        self.assertTrue(any("URL" in f for f in findings))

    def test_link_titles(self):
        found = tags.fill_from_link({"title": ["Figoshin - Zone De Confort [Official Video]"],
                                     "artist": ["Figoshin"]})
        self.assertEqual((found["artist"], found["title"]), (["Figoshin"], ["Zone De Confort"]))
        dashed = tags.fill_from_link({"title": ["Kevin MacLeod \u2013 Monkeys Spinning Monkeys"],
                                      "artist": ["Kevin MacLeod"]})
        self.assertEqual(dashed["title"], ["Monkeys Spinning Monkeys"])
        colon = tags.fill_from_link({"title": ["Kevin MacLeod: Monkeys Spinning Monkeys"],
                                     "artist": ["Kevin MacLeod Archive"]})
        self.assertEqual((colon["artist"], colon["title"]), (["Kevin MacLeod"], ["Monkeys Spinning Monkeys"]))
        reversed_ = tags.fill_from_link(
            {"title": ["Monkeys Spinning Monkeys \u2013 Kevin MacLeod (No Copyright Music)"],
             "artist": ["Audio Library"]},
            artist_side=lambda left, right: "right" if right == "Kevin MacLeod" else None)
        self.assertEqual((reversed_["artist"], reversed_["title"]),
                         (["Kevin MacLeod"], ["Monkeys Spinning Monkeys"]))
        unknown = tags.fill_from_link({"title": ["Foo - Bar"], "artist": ["Some Channel"]},
                                      artist_side=lambda left, right: None)
        self.assertEqual((unknown["artist"], unknown["title"]), (["Some Channel"], ["Foo - Bar"]))
        vevo = tags.fill_from_link({"title": ["Drake - One Dance"], "artist": ["DrakeVEVO"]})
        self.assertEqual((vevo["artist"], vevo["title"]), (["Drake"], ["One Dance"]))
        kept = tags.fill_from_link({"title": ["Remix - Part 2"], "artist": ["Someone Else"],
                                    "album": ["Album"]})
        self.assertEqual(kept["title"], ["Remix - Part 2"], "music tags are trusted over the title")


class LayoutRules(unittest.TestCase):
    def test_names(self):
        self.assertEqual(layout.component("AC/DC: Live?", "x"), "AC-DC - Live")
        self.assertEqual(layout.component("...", "fallback"), "fallback")
        self.assertEqual(layout.component(".hidden", "x"), "hidden")
        self.assertEqual(layout.relative({"albumartist": ["A"], "album": ["B"], "title": ["T"],
                                          "tracknumber": ["5/20"], "discnumber": ["2/2"],
                                          "date": ["1999-01-01"]}, ".flac"),
                         "A/B (1999)/2-05 - T.flac")
        self.assertEqual(layout.relative({"albumartist": ["A"], "album": ["B"], "title": ["T"],
                                          "tracknumber": ["5"], "discnumber": ["1/1"]}, ".mp3"),
                         "A/B/05 - T.mp3")


class LyricsRules(unittest.TestCase):
    def test_synced_and_cleaning(self):
        text = lyrics.clean("[00:01.00] one\r\n[00:02.50] two\x07\n")
        self.assertEqual(text, "[00:01.00] one\n[00:02.50] two\n")
        self.assertTrue(lyrics.synced_ok("[ar: Band]\n[00:01.00] one\n"))
        self.assertFalse(lyrics.synced_ok("one\n[00:01.00] two\n"))

    def test_lookalike_letters_are_latinised_only_inside_latin_words(self):
        text = lyrics.clean("Rani nkhmmem fik ri7t\u0435k\n\u041f\u0440\u0438\u0432\u0435\u0442 \u03b1\u03b2\u03b3\n")
        self.assertEqual(text, "Rani nkhmmem fik ri7tek\n\u041f\u0440\u0438\u0432\u0435\u0442 \u03b1\u03b2\u03b3\n")

    def test_an_ai_synced_file_is_replaced_by_a_human_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "01 - Song.mp3")
            open(path, "wb").close()
            with open(os.path.join(tmp, "01 - Song.lrc"), "w", encoding="utf-8") as out:
                out.write("[by:AI-generated, not checked by a person]\n[00:01.00] la\n")
            g = guard.Guard.__new__(guard.Guard)
            g.config = mock.Mock(dry_run=True)
            g.state = {"lyrics": {}}
            g.events = []
            self.assertFalse(g.has_synced_lyrics(path))
            with mock.patch.object(lyrics, "lookup", return_value=("synced", "[00:01.20] la\n")):
                g.fetch_lyrics(path, {"artist": ["Band"], "title": ["Song"]}, 180)
            with open(os.path.join(tmp, "01 - Song.lrc"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "[00:01.20] la\n")
            self.assertTrue(g.has_synced_lyrics(path))

    def test_pick_by_length(self):
        results = [{"duration": 200, "plainLyrics": "a"}, {"duration": 181, "syncedLyrics": "[00:01.00] b"}]
        self.assertEqual(lyrics.pick(results, 180)["duration"], 181)
        self.assertIsNone(lyrics.pick(results, 100))

    def test_synced_lyrics_win_over_a_closer_plain_entry(self):
        results = [
            {"trackName": "KATANA", "artistName": "Manal & Stormy", "duration": 157, "plainLyrics": "words"},
            {"trackName": "KATANA", "artistName": "Manal", "duration": 158, "syncedLyrics": "[00:13.73] words"},
            {"trackName": "KATANA", "artistName": "Someone Else", "duration": 157, "syncedLyrics": "[00:01.00] x"},
            {"trackName": "Other", "artistName": "Manal", "duration": 157, "syncedLyrics": "[00:01.00] y"},
        ]
        best = lyrics.pick(results, 156.6, "KATANA", "Manal & Stormy")
        self.assertEqual((best["artistName"], best["duration"]), ("Manal", 158))

    def test_sidecar_written_after_filing(self):
        with mock.patch.object(lyrics, "lookup", return_value=("synced", "[00:01.00] la\n")):
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "01 - Song.mp3")
                open(path, "wb").close()
                g = guard.Guard.__new__(guard.Guard)
                g.config = mock.Mock(dry_run=True)
                g.state = {"lyrics": {}}
                g.events = []
                g.fetch_lyrics(path, {"artist": ["Band"], "title": ["Song"]}, 180)
                with open(os.path.join(tmp, "01 - Song.lrc"), encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "[00:01.00] la\n")

    def test_plain_lyrics_are_upgraded_to_synced_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "01 - Song.mp3")
            open(path, "wb").close()
            with open(os.path.join(tmp, "01 - Song.txt"), "w", encoding="utf-8") as out:
                out.write("words written by hand\n")
            g = guard.Guard.__new__(guard.Guard)
            g.config = mock.Mock(dry_run=True)
            g.state = {"lyrics": {path: 0}}  # last asked long ago
            g.events = []
            with mock.patch.object(lyrics, "lookup", return_value=("plain", "other words\n")):
                g.fetch_lyrics(path, {"artist": ["Band"], "title": ["Song"]}, 180)
            with open(os.path.join(tmp, "01 - Song.txt"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "words written by hand\n", "plain lyrics are not replaced")
            g.state["lyrics"][path] = 0
            with mock.patch.object(lyrics, "lookup", return_value=("synced", "[00:01.00] la\n")):
                g.fetch_lyrics(path, {"artist": ["Band"], "title": ["Song"]}, 180)
            self.assertTrue(os.path.exists(os.path.join(tmp, "01 - Song.lrc")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "01 - Song.txt")), "kept beside the synced ones")


class PictureEnds(unittest.TestCase):
    def test_jpeg_with_a_thumbnail_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.jpg")
            jpeg(path)
            with open(path, "rb") as handle:
                data = handle.read()
        thumbnail = b"\xff\xd8\xff\xd9"  # a whole JPEG inside an APP1 segment
        app1 = b"\xff\xe1" + (len(thumbnail) + 8).to_bytes(2, "big") + b"Exif\x00\x00" + thumbnail
        nested = data[:2] + app1 + data[2:]
        self.assertEqual(media.image_end(nested), len(nested))
        self.assertEqual(media.image_end(nested + b"PK\x03\x04hidden"), len(nested))
        self.assertIsNone(media.image_end(nested[:len(nested) // 2]))

    def test_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.png")
            ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x64", "-frames:v", "1", path)
            with open(path, "rb") as handle:
                data = handle.read()
        self.assertEqual(media.image_end(data), len(data))
        self.assertEqual(media.image_end(data + b"<?php"), len(data))
        self.assertIsNone(media.image_end(b"GIF89a"))


class ClamdProtocol(unittest.TestCase):
    def test_replies(self):
        self.assertEqual(clamd.parse("stream: OK"), (False, ""))
        self.assertEqual(clamd.parse("stream: Eicar-Test-Signature FOUND"), (True, "Eicar-Test-Signature"))
        with self.assertRaises(clamd.ClamdError):
            clamd.parse("INSTREAM size limit exceeded. ERROR")


if __name__ == "__main__":
    unittest.main()
