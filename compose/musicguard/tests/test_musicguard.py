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
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import mutagen  # noqa: E402
from mutagen.id3 import APIC, COMM, ID3, TIT2, TPE1, TXXX, WXXX  # noqa: E402

import clamd  # noqa: E402
import guard  # noqa: E402
import layout  # noqa: E402
import lyrics  # noqa: E402
import media  # noqa: E402
import tags  # noqa: E402

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
            "MUSICGUARD_QUARANTINE": self.quarantine,
            "MUSICGUARD_STATE": self.state,
            "CLAMD_SOCKET": self.socket_path,
            "MUSICGUARD_LYRICS": "0",
            "MUSICGUARD_SETTLE_SECONDS": "0",
            "DISCORD_WEBHOOK": "",
        })
        self.environment.start()
        self.musicbrainz = mock.patch.object(guard.musicbrainz, "artist_side", return_value=None)
        self.musicbrainz.start()
        self.guard = guard.Guard(guard.Config())

    def tearDown(self):
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

    def test_pick_by_length(self):
        results = [{"duration": 200, "plainLyrics": "a"}, {"duration": 181, "syncedLyrics": "[00:01.00] b"}]
        self.assertEqual(lyrics.pick(results, 180)["duration"], 181)
        self.assertIsNone(lyrics.pick(results, 100))

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
