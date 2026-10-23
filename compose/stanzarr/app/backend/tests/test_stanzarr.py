"""python -m unittest discover -s tests (in the image: the Dockerfile's tested stage)."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mutagen  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from stanzarr import language, library, lyricsfiles, operations  # noqa: E402
from stanzarr.api.app import create_app  # noqa: E402
from stanzarr.config import Config  # noqa: E402
from stanzarr.db import Database  # noqa: E402
from stanzarr.services import build  # noqa: E402
from stanzarr.sources import Found, LyricsSource, SongQuery  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
PAGE = {"X-Stanzarr-Page": "1"}


def tone(path: str, seconds: int = 30, title: str = "Song", artist: str = "Band", album: str = "Record") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-ac", "2", path], check=True)
    audio = mutagen.File(path, easy=True)
    if audio.tags is None:
        audio.add_tags()
    audio["title"], audio["artist"], audio["album"] = title, artist, album
    audio.save()
    return path


class Fake(LyricsSource):
    """A source that answers what it is told, and counts the questions."""

    key = "fake"
    label = "Fake"
    description = "For the tests."

    def __init__(self, answer: Found):
        self.answer, self.asked = answer, 0

    def search(self, song: SongQuery, config: dict) -> Found:
        self.asked += 1
        return self.answer


def config(folder: str) -> Config:
    c = Config()
    c.library = [os.path.join(folder, "music")]
    os.makedirs(c.library[0], exist_ok=True)
    c.config_dir = os.path.join(folder, "config")
    c.static_dir = os.path.join(folder, "static")
    c.lrclib = False
    c.username, c.password, c.worker_key = "admin", "a long enough password", ""
    c.read_only = False
    return c


def status(db, song_id):
    return db.one(f"SELECT {library.STATUS_SQL} AS s FROM songs s WHERE s.id=?", (song_id,))["s"]


# ---- the lyrics files ------------------------------------------------------------------------

class LyricsFiles(unittest.TestCase):
    def test_lrc_with_tags_and_repeated_stamps(self):
        text = ("[ar:Band]\n[by:AI-generated, corrected by a person]\n[#:uncertain lines: 2, 3]\n"
                "[#:checked lines: 1, 2]\n[00:01.00][00:20.50] chorus\n[00:05.25] verse\n")
        lyrics = lyricsfiles.parse_lrc(text)
        self.assertTrue(lyrics.synced)
        self.assertEqual([(line.t, line.s) for line in lyrics.lines], [(1.0, "chorus"), (5.25, "verse"), (20.5, "chorus")])
        origin = lyricsfiles.origin(lyrics)
        self.assertEqual((origin.source, origin.ai_state, origin.unsure, origin.checked), ("ai", "corrected", 2, 2))

    def test_origins(self):
        parse = lyricsfiles.parse_lrc
        self.assertEqual(lyricsfiles.origin(parse("[by:AI-generated, not checked by a person]\n[00:01.00] a")).ai_state,
                         "unchecked")
        self.assertEqual(lyricsfiles.origin(parse("[by:AI-generated, checked by a person]\n[00:01.00] a")).ai_state,
                         "checked")
        self.assertEqual(lyricsfiles.origin(parse("[re:LRCLIB via Stanzarr]\n[00:01.00] a")).source, "lrclib")
        self.assertEqual(lyricsfiles.origin(parse("[00:01.00] a")).source, "unknown")

    def test_word_stamps_are_dropped_from_text(self):
        self.assertEqual(lyricsfiles.parse_lrc("[00:01.00] <00:01.00>Hello <00:01.50>world").lines[0].s, "Hello world")

    def test_ttml(self):
        lyrics = lyricsfiles.parse_ttml('<tt xml:lang="fr"><body><p begin="00:01.500">Bonjour</p>'
                                        '<p begin="1:02.0">toi</p></body></tt>')
        self.assertEqual([(line.t, line.s) for line in lyrics.lines], [(1.5, "Bonjour"), (62.0, "toi")])
        self.assertEqual(lyrics.tags["la"], "fr")

    def test_lookalike_letters_only_in_latin_words(self):
        self.assertEqual(lyricsfiles.clean("ri7tеk\nПривет\n"), "ri7tek\nПривет\n")

    def test_render_is_sorted_and_skips_empty(self):
        out = lyricsfiles.render_lrc([lyricsfiles.Line(5, "b"), lyricsfiles.Line(1.5, "a"), lyricsfiles.Line(3, " ")],
                                     ["[re:x]"])
        self.assertEqual(out, "[re:x]\n[00:01.500] a\n[00:05.000] b\n")

    def test_language_guess(self):
        self.assertEqual(language.detect("I know you love me and all the things that you do for me tonight"), "en")
        self.assertEqual(language.detect("Je ne sais pas pourquoi tu me fais ça, c'est pas la vie que je veux pour moi"),
                         "fr")
        self.assertEqual(language.detect("3tit 3lihoum demmi gherssou f gelbi, rani 9adr w wach bghit chi haja m3ak"),
                         "ary")
        self.assertEqual(language.detect("حبيبي يا نور العين"), "ar")
        self.assertEqual(language.detect("anything", "es"), "es")


# ---- the filter between the sources and the library ------------------------------------------

class Filter(unittest.TestCase):
    def test_hidden_characters_html_and_site_lines_go(self):
        text = ("[00:01.00] hel\u200blo \u202eworld\n<script>alert(1)</script>[00:02.00] two\n"
                "[00:03.00] Lyrics provided by SomeSite\nYou might also like\n[00:04.00] www.example.com\n12Embed\n")
        checked = lyricsfiles.sanitize(text, 200)
        self.assertIsNone(checked.refused)
        self.assertEqual(checked.text, "[00:01.00] hello world\nalert(1)[00:02.00] two\n")
        self.assertTrue(any("hidden" in r for r in checked.removed))

    def test_times_past_the_end_binary_and_empty_are_handled(self):
        self.assertEqual(lyricsfiles.sanitize("[00:10.00] in\n[09:00.00] out\n", 60).text, "[00:10.00] in\n")
        self.assertEqual(lyricsfiles.sanitize("a\x00b").refused, "it holds binary data")
        self.assertIsNotNone(lyricsfiles.sanitize("\u200b\n").refused)
        self.assertIsNotNone(lyricsfiles.sanitize("x" * (lyricsfiles.MAX_BYTES + 1)).refused)


# ---- the sources, on answers like theirs (never the network) ---------------------------------

class Sources(unittest.TestCase):
    QUERY = SongQuery("Band", "Song", "Record", 200.0, "USABC1234567")

    def test_netease_picks_the_closest_and_drops_credits(self):
        from stanzarr.sources import netease
        search = {"result": {"songs": [
            {"id": 1, "name": "Song", "duration": 120000, "artists": [{"name": "Band"}]},
            {"id": 2, "name": "Song", "duration": 201000, "artists": [{"name": "Band"}]},
            {"id": 3, "name": "Other", "duration": 200000, "artists": [{"name": "Band"}]}]}}
        lyric = {"lrc": {"lyric": "[00:00.00] 作词 : Someone\n[00:01.00] words\n"}}
        with mock.patch.object(netease.http, "get_json", side_effect=[search, lyric]) as asked:
            found = netease.NetEase().search(self.QUERY, {})
        self.assertEqual((found.kind, found.text, found.reference), ("synced", "[00:01.00] words\n", "netease id: 2"))
        self.assertEqual(asked.call_args_list[1].args[1]["id"], 2)

    def test_genius_reads_the_lyrics_containers(self):
        from stanzarr.sources import genius
        search = {"response": {"hits": [{"type": "song", "result": {
            "id": 9, "title": "Song", "url": "https://genius.example/song", "lyrics_state": "complete",
            "primary_artist": {"name": "Band"}}}]}}
        page = ('<div data-lyrics-container="true"><div data-exclude-from-selection="true">Song Lyrics</div>'
                '[Verse 1]<br/>First &amp; line<br><a href="#">Second</a> line</div><div>not this</div>'
                '<div data-lyrics-container="true">Third line</div>').encode()
        with mock.patch.object(genius.http, "get_json", return_value=search), \
                mock.patch.object(genius.http, "get", return_value=page):
            found = genius.Genius().search(self.QUERY, {"token": "t"})
        self.assertEqual(found.kind, "plain")
        self.assertEqual(found.text, "[Verse 1]\nFirst & line\nSecond line\nThird line\n")

    def test_musixmatch_subtitles_and_the_isrc(self):
        from stanzarr.sources import musixmatch
        body = '[{"text": "words", "time": {"total": 1.5}}, {"text": "more", "time": {"total": 63.25}}]'
        answer = {"message": {"header": {"status_code": 200}, "body": {"macro_calls": {
            "matcher.track.get": {"message": {"body": {"track": {"track_id": 5, "instrumental": 0}}}},
            "track.subtitles.get": {"message": {"body": {"subtitle_list": [{"subtitle": {"subtitle_body": body}}]}}}}}}}
        with mock.patch.object(musixmatch.http, "get_json", return_value=answer) as asked:
            found = musixmatch.Musixmatch().search(self.QUERY, {"token": "t"})
        self.assertEqual(found.text, "[00:01.50] words\n[01:03.25] more\n")
        self.assertEqual(asked.call_args.args[1]["track_isrc"], "USABC1234567")
        refused = {"message": {"header": {"status_code": 401, "hint": "captcha"}, "body": ""}}
        with mock.patch.object(musixmatch.http, "get_json", return_value=refused), self.assertRaises(Exception) as error:
            musixmatch.Musixmatch().search(self.QUERY, {"token": "t"})
        self.assertIn("captcha", str(error.exception))

    def test_lrclib_without_a_length_takes_any(self):
        from stanzarr.sources import lrclib
        entry = {"id": 1, "trackName": "Song", "artistName": "Band", "duration": 321, "syncedLyrics": "[00:01.00] a"}
        self.assertEqual(lrclib.pick([entry], 0, "Song", "Band"), entry)
        self.assertIsNone(lrclib.pick([entry], 100, "Song", "Band"))


# ---- the library and the rules ---------------------------------------------------------------

@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
class LibraryAndRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config(self.tmp.name)
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.db_path)
        self.root = self.cfg.library[0]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def song(self, **tags) -> tuple[str, int]:
        path = tone(os.path.join(self.root, "Band", "Record", "01 - Song.mp3"), **tags)
        library.scan(self.db, [self.root])
        return path, self.db.one("SELECT id FROM songs")["id"]

    def search(self, song_id, *answers: Found) -> str:
        sources = [({"id": str(n), "config": {}}, Fake(answer)) for n, answer in enumerate(answers)]
        return operations.search(self.db, self.cfg, song_id, sources)

    def test_scan_finds_songs_and_sidecars(self):
        song = tone(os.path.join(self.root, "Band", "Record (2020)", "01 - Song.mp3"))
        with open(song[:-4] + ".txt", "w") as out:
            out.write("one line\nanother line\n")
        self.assertEqual(library.scan(self.db, [self.root])["added"], 1)
        row = self.db.one("SELECT * FROM songs")
        self.assertEqual((row["artist"], row["title"]), ("Band", "Song"))
        self.assertEqual(status(self.db, row["id"]), "plain")
        with open(song[:-4] + ".lrc", "w") as out:
            out.write("[00:01.00] one line\n")
        library.scan(self.db, [self.root])
        self.assertEqual(status(self.db, row["id"]), "synced")
        os.unlink(song)
        self.assertEqual(library.scan(self.db, [self.root])["removed"], 1)

    def test_synced_replaces_ai_but_never_a_persons(self):
        path, song_id = self.song()
        lrc = path[:-4] + ".lrc"
        with open(lrc, "w") as out:
            out.write("[by:AI-generated, corrected by a person]\n[00:02.00] ai words\n")
        self.assertIn("replaced", self.search(song_id, Found("synced", "[00:01.00] real words\n", "fake id: 42")))
        self.assertIn("real words", Path(lrc).read_text())
        self.assertTrue(os.listdir(self.cfg.backups_dir), "the AI file is kept")
        self.assertEqual(status(self.db, song_id), "synced")
        self.search(song_id, Found("synced", "[00:01.00] other words\n"))
        self.assertIn("real words", Path(lrc).read_text(), "a person-synced file is never overwritten")

    def test_the_sources_are_asked_in_order_and_synced_wins(self):
        path, song_id = self.song()
        first, second = Fake(Found("plain", "plain words")), Fake(Found("synced", "[00:01.00] synced words\n"))
        third = Fake(Found("synced", "[00:01.00] never asked\n"))
        sources = [({"id": "1", "config": {}}, first), ({"id": "2", "config": {}}, second),
                   ({"id": "3", "config": {}}, third)]
        self.assertIn("synced lyrics from Fake", operations.search(self.db, self.cfg, song_id, sources))
        self.assertEqual((first.asked, second.asked, third.asked), (1, 1, 0))
        self.assertIn("synced words", Path(path[:-4] + ".lrc").read_text())

    def test_a_refused_answer_is_kept_aside_and_logged(self):
        path, song_id = self.song()
        self.search(song_id, Found("synced", "[00:01.00] a\x00b\n"))
        self.assertFalse(os.path.exists(path[:-4] + ".lrc"))
        self.assertEqual(len(os.listdir(os.path.join(self.cfg.config_dir, "quarantine"))), 1)
        event = self.db.one("SELECT * FROM events WHERE kind='filter'")
        self.assertEqual(event["ok"], 0)

    def test_the_isrc_is_read_and_asked_with(self):
        path = tone(os.path.join(self.root, "Band", "Record", "01 - Song.mp3"))
        audio = mutagen.File(path, easy=True)
        audio["isrc"] = "usabc1234567"
        audio.save()
        library.scan(self.db, [self.root])
        song_id = self.db.one("SELECT id, isrc FROM songs")
        self.assertEqual(song_id["isrc"], "USABC1234567")
        fake = Fake(Found("none"))
        seen = []
        fake.search = lambda song, config: seen.append(song) or Found("none")
        operations.search(self.db, self.cfg, song_id["id"], [({"id": "1", "config": {}}, fake)])
        self.assertEqual(seen[0].isrc, "USABC1234567")

    def test_the_quality_rank(self):
        path, song_id = self.song()
        rank = lambda: self.db.one(f"SELECT {library.RANK_SQL} AS r FROM songs s WHERE s.id=?", (song_id,))["r"]
        self.assertEqual(rank(), 0)
        with open(path[:-4] + ".txt", "w") as out:
            out.write("words\n")
        library.scan(self.db, [self.root])
        self.assertEqual(rank(), 1)
        with open(path[:-4] + ".lrc", "w") as out:
            out.write("[by:AI-generated, not checked by a person]\n[00:01.00] words\n")
        library.sync_lyrics(self.db, song_id, path, force=True)
        self.assertEqual(rank(), 2)
        with open(path[:-4] + ".lrc", "w") as out:
            out.write("[00:01.00] words\n")
        library.sync_lyrics(self.db, song_id, path, force=True)
        self.assertEqual(rank(), 4)

    def test_a_failing_source_doesnt_stop_the_next(self):
        path, song_id = self.song()

        class Down(Fake):
            def search(self, song, config):
                raise OSError("down")

        sources = [({"id": "1", "config": {}}, Down(Found("none"))), ({"id": "2", "config": {}}, Fake(Found("plain", "words")))]
        self.assertIn("plain lyrics", operations.search(self.db, self.cfg, song_id, sources))
        with self.assertRaises(OSError):
            operations.search(self.db, self.cfg, song_id, sources[:1])

    def test_editor_save_keeps_the_ai_flag(self):
        path, song_id = self.song()
        lrc = path[:-4] + ".lrc"
        with open(lrc, "w") as out:
            out.write("[by:AI-generated, not checked by a person]\n[#:uncertain lines: 2]\n[00:01.00] a\n[00:03.00] b\n")
        data = operations.editor_lines(self.db, song_id)
        self.assertEqual([line["u"] for line in data["lines"]], [False, True])
        operations.save_from_editor(self.db, self.cfg, song_id,
                                    [{"t": 1.2, "s": "a", "fixed": True}, {"t": 3, "s": "b", "u": True}])
        text = Path(lrc).read_text()
        self.assertTrue(text.startswith("[by:AI-generated, corrected by a person]"))
        self.assertIn("[#:uncertain lines (the AI was unsure): 2]", text)
        self.assertIn("[#:checked lines: 1]", text)
        self.assertEqual(status(self.db, song_id), "ai_corrected")

    def test_plain_lyrics_synced_by_hand_are_a_persons(self):
        path = tone(os.path.join(self.root, "Band", "Record", "01 - Song.mp3"))
        with open(path[:-4] + ".txt", "w") as out:
            out.write("one\ntwo\n")
        library.scan(self.db, [self.root])
        song_id = self.db.one("SELECT id FROM songs")["id"]
        data = operations.editor_lines(self.db, song_id)
        self.assertEqual((data["origin"], len(data["lines"])), ("plain", 2))
        operations.save_from_editor(self.db, self.cfg, song_id, data["lines"])
        self.assertTrue(Path(path[:-4] + ".lrc").read_text().startswith("[by:a person]"))
        self.assertTrue(os.path.exists(path[:-4] + ".txt"), "plain lyrics are kept")
        self.assertEqual(status(self.db, song_id), "synced")

    def test_ai_result_never_over_a_persons_lrc(self):
        path, song_id = self.song()
        with open(path[:-4] + ".lrc", "w") as out:
            out.write("[00:01.00] by a person\n")
        self.assertIn("kept", operations.save_ai_result(self.db, self.cfg, song_id, [{"t": 1, "s": "x"}], "test"))
        self.assertIn("by a person", Path(path[:-4] + ".lrc").read_text())

    def test_read_only_writes_nothing(self):
        self.cfg.read_only = True
        path, song_id = self.song()
        self.assertIn("read-only", self.search(song_id, Found("synced", "[00:01.00] words\n")))
        self.assertFalse(os.path.exists(path[:-4] + ".lrc"))
        with self.assertRaises(operations.ReadOnly):
            operations.save_from_editor(self.db, self.cfg, song_id, [{"t": 1, "s": "a"}])


# ---- the database before 1.0 -----------------------------------------------------------------

class Migration(unittest.TestCase):
    def test_a_database_from_before_keeps_its_songs_and_drops_the_old_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "stanzarr.db")
            from stanzarr.db import SCHEMA

            old = sqlite3.connect(path)
            old.executescript(SCHEMA[0] + "PRAGMA user_version=1;")
            old.execute("INSERT INTO songs(path, root, rel, size, mtime, added_at) VALUES('/m/a.flac', '/m', 'a.flac', 1, 1, 1)")
            old.execute("INSERT INTO users(username, password, created_at) VALUES('admin', 'scrypt$x$y', 1)")
            old.execute("INSERT INTO jobs(song_id, kind, status, created_at) VALUES(1, 'ai_sync', 'done', 1)")
            old.commit()
            old.close()
            db = Database(path)
            self.assertEqual(db.one("SELECT COUNT(*) AS n FROM songs")["n"], 1)
            self.assertEqual(db.one("SELECT status FROM jobs")["status"], "finished")
            self.assertIsNone(db.one("SELECT 1 FROM sqlite_master WHERE name='users'"))
            db.close()


# ---- the API --------------------------------------------------------------------------------

class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = config(self.tmp.name)
        self.services = build(self.cfg)
        self.client = TestClient(create_app(self.services, run_background=False), base_url="http://stanzarr.test")
        self.client.__enter__()
        signed = self.client.post("/api/v1/auth/login", json={"username": "admin", "password": "a long enough password"})
        self.assertEqual(signed.status_code, 200, signed.text)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_health_status_and_the_counts_at_zero(self):
        self.assertEqual(self.client.get("/api/v1/health").json()["ok"], True)
        found = self.client.get("/api/v1/system/status").json()
        self.assertEqual(found["songs"], 0)
        self.assertEqual(set(found["by_status"]), set(library.STATUSES))
        self.assertEqual(found["to_check"], {"songs": 0, "lines": 0})

    def test_a_change_needs_the_pages_header(self):
        self.assertEqual(self.client.patch("/api/v1/settings", json={"retry_days": 7}).status_code, 403)
        self.assertEqual(self.client.patch("/api/v1/settings", json={"retry_days": 7}, headers=PAGE).json()["retry_days"], 7)
        self.assertEqual(self.client.patch("/api/v1/settings", json={"retry_days": True}, headers=PAGE).status_code, 422)

    def test_a_wrong_method_is_a_405(self):
        response = self.client.put("/api/v1/system/status", headers=PAGE)
        self.assertEqual((response.status_code, response.headers["allow"]), (405, "GET"))

    def test_sources_are_made_ordered_and_tested(self):
        types = self.client.get("/api/v1/sources/types").json()["types"]
        self.assertIn("lrclib", [t["type"] for t in types])
        one = self.client.post("/api/v1/sources", json={"name": "LRCLIB", "type": "lrclib"}, headers=PAGE).json()
        two = self.client.post("/api/v1/sources", json={"name": "Mirror", "type": "lrclib",
                                                        "config": {"url": "https://lrclib.example/api"}},
                               headers=PAGE).json()
        ordered = self.client.put("/api/v1/sources/order", json={"ids": [two["id"], one["id"]]}, headers=PAGE).json()
        self.assertEqual([item["name"] for item in ordered["items"]], ["Mirror", "LRCLIB"])
        self.assertEqual(self.client.post("/api/v1/sources", json={"name": "x", "type": "nope"}, headers=PAGE).status_code,
                         422)
        with mock.patch("stanzarr.sources.lrclib.lookup", return_value=("synced", "[00:01.00] a", 1)):
            tested = self.client.post(f"/api/v1/sources/{one['id']}/test", json={"name": "LRCLIB", "type": "lrclib"},
                                      headers=PAGE).json()
        self.assertTrue(tested["ok"], tested)

    def test_lyrics_for_a_song_outside_the_library_are_asked_once(self):
        self.client.post("/api/v1/sources", json={"name": "LRCLIB", "type": "lrclib"}, headers=PAGE)
        with mock.patch("stanzarr.sources.lrclib.lookup", return_value=("synced", "[00:01.00] hello\n", 7)) as asked:
            first = self.client.get("/api/v1/lyrics", params={"artist": "A", "title": "T", "duration": 200}).json()
            again = self.client.get("/api/v1/lyrics", params={"artist": "A", "title": "T", "duration": 200}).json()
        self.assertEqual((first["found"], first["synced"], first["from"], first["source"]), (True, True, "source", "LRCLIB"))
        self.assertEqual(first["text"], "hello")
        self.assertEqual(again["lrc"], first["lrc"])
        self.assertEqual(asked.call_count, 1, "the answer is kept")

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_lyrics_of_a_library_song_come_from_the_library(self):
        path = tone(os.path.join(self.cfg.library[0], "A", "R", "01 - One.mp3"), title="One", artist="Artist A")
        with open(path[:-4] + ".lrc", "w") as out:
            out.write("[by:a person]\n[00:01.00] mine\n")
        library.scan(self.services.db, self.cfg.library)
        found = self.client.get("/api/v1/lyrics", params={"artist": "Artist A feat. B", "title": "One"}).json()
        self.assertEqual((found["from"], found["origin"], found["synced"]), ("library", "person", True))

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_ai_sync_end_to_end_on_both_worker_paths(self):
        song = tone(os.path.join(self.cfg.library[0], "A", "R", "01 - One.mp3"), title="One", artist="A")
        with open(song[:-4] + ".txt", "w") as out:
            out.write("first line\nsecond line\n")
        library.scan(self.services.db, self.cfg.library)
        song_id = self.client.get("/api/v1/songs").json()["songs"][0]["id"]
        self.assertEqual(self.client.post(f"/api/v1/songs/{song_id}/ai-sync", headers=PAGE).status_code, 409)
        made = self.client.post("/api/v1/workers", json={"name": "laptop"}, headers=PAGE).json()
        self.assertTrue(made["key"].startswith("szw_"))
        self.assertNotIn("key", self.client.get("/api/v1/workers").json())
        # Its own key names it, whatever name it gives.
        worker = {"X-Stanzarr-Key": made["key"], "X-Stanzarr-Worker": "something else"}
        self.assertEqual(self.client.post("/api/v1/worker/heartbeat", json={"gpu": ""},
                                          headers={"X-Stanzarr-Key": "wrong"}).status_code, 401)
        # A worker from before 1.0: /api/worker/….
        self.client.post("/api/worker/heartbeat", json={"gpu": "Quadro T1000", "vram": 4096}, headers=worker)
        self.assertTrue(self.client.get("/api/v1/system/status").json()["gpu_online"])
        self.assertEqual(self.client.post(f"/api/v1/songs/{song_id}/ai-sync", headers=PAGE).status_code, 200)
        job = self.client.post("/api/v1/worker/next", headers=worker).json()
        self.assertEqual(job["lines"], ["first line", "second line"])
        audio = self.client.get(f"/api/v1/worker/jobs/{job['job']}/audio", headers=worker)
        self.assertTrue(audio.status_code == 200 and len(audio.content) > 1000)
        self.assertEqual(self.client.put(f"/api/v1/worker/jobs/{job['job']}/voice", content=b"OggS fake",
                                         headers=worker).status_code, 200)
        done = self.client.post(f"/api/worker/jobs/{job['job']}/result", headers=worker,
                                json={"lines": [{"t": 1.0, "s": "first line"}, {"t": 4.5, "s": "second line", "u": True}],
                                      "method": "test"})
        self.assertEqual(done.status_code, 200)
        text = Path(song[:-4] + ".lrc").read_text()
        self.assertTrue(text.startswith("[by:AI-generated, not checked by a person]"))
        self.assertEqual(self.client.get(f"/api/v1/songs/{song_id}").json()["song"]["status"], "ai_unchecked")
        self.assertEqual(self.client.get("/api/v1/jobs").json()[0]["status"], "finished")
        self.assertEqual(self.client.post("/api/v1/worker/next", headers=worker).status_code, 204)
        checked = self.client.get("/api/v1/songs", params={"to_check": True}).json()
        self.assertEqual(checked["total"], 1)
        listed = self.client.get("/api/v1/workers").json()["workers"]
        self.assertEqual([(w["name"], w["online"], w["shared_key"]) for w in listed], [("laptop", True, False)])
        # Who did it is in the activity.
        log = self.client.get("/api/v1/activity", params={"kind": "ai"}).json()["events"]
        self.assertIn("laptop", [e["actor"] for e in log])
        # Removed: its key stops working at once.
        self.assertEqual(self.client.delete(f"/api/v1/workers/{made['worker']['id']}", headers=PAGE).status_code, 204)
        self.assertEqual(self.client.post("/api/v1/worker/heartbeat", json={"gpu": ""}, headers=worker).status_code, 401)

    def test_shared_key_from_before_works_until_retired(self):
        self.services.db.set_setting("worker_key", "old-shared-key")
        worker = {"X-Stanzarr-Key": "old-shared-key", "X-Stanzarr-Worker": "desktop"}
        self.assertEqual(self.client.post("/api/v1/worker/heartbeat", json={"gpu": ""}, headers=worker).status_code, 200)
        listed = self.client.get("/api/v1/workers").json()
        self.assertTrue(listed["shared_key"])
        self.assertEqual([(w["name"], w["shared_key"]) for w in listed["workers"]], [("desktop", True)])
        self.assertEqual(self.client.delete("/api/v1/workers-shared-key", headers=PAGE).status_code, 204)
        self.assertEqual(self.client.post("/api/v1/worker/heartbeat", json={"gpu": ""}, headers=worker).status_code, 401)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_library_sorting_filters_facets_files_and_history(self):
        for n, (artist, title) in enumerate((("Zed", "Alpha"), ("Abba", "Omega"), ("Mid", "Beta"))):
            song = tone(os.path.join(self.cfg.library[0], artist, "R", f"0{n} - {title}.mp3"), seconds=5,
                        title=title, artist=artist)
            if artist == "Abba":
                with open(song[:-4] + ".lrc", "w") as out:
                    out.write("[00:01.00] words\n")
        library.scan(self.services.db, self.cfg.library)
        by = lambda **p: [r["artist"] for r in self.client.get("/api/v1/songs", params=p).json()["songs"]]
        self.assertEqual(by(), ["Abba", "Mid", "Zed"])
        self.assertEqual(by(sort="title"), ["Zed", "Mid", "Abba"])
        self.assertEqual(by(status=["synced"]), ["Abba"])
        self.assertEqual(by(status=["missing", "synced"], size=2, page=2), ["Zed"])
        self.assertEqual(by(sort="status"), ["Abba", "Mid", "Zed"])
        self.assertEqual(by(made_by="unknown"), ["Abba"])
        facets = self.client.get("/api/v1/songs/facets").json()
        self.assertEqual([f["value"] for f in facets["formats"]], ["lrc"])
        song_id = self.client.get("/api/v1/songs", params={"status": ["synced"]}).json()["songs"][0]["id"]
        detail = self.client.get(f"/api/v1/songs/{song_id}").json()
        self.assertEqual(detail["ai_blocked"], "No GPU worker is online: start one (Settings, Workers).")
        self.assertIsNotNone(detail["search_blocked"])
        file_id = detail["files"][0]["id"]
        self.assertEqual(self.client.get(f"/api/v1/songs/{song_id}/files/{file_id}").text, "[00:01.00] words\n")
        lines = self.client.get(f"/api/v1/songs/{song_id}/files/{file_id}/lines").json()
        self.assertEqual(lines, {"synced": True, "lines": [{"t": 1.0, "s": "words"}]})
        self.assertEqual(self.client.get(f"/api/v1/songs/{song_id}/files/999").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/songs/{song_id}/cover").status_code, 404)
        self.services.note_coverage()
        history = self.client.get("/api/v1/songs/history").json()
        self.assertEqual(history[-1]["counts"]["synced"], 1)

    def test_find_lyrics_by_name_and_link(self):
        self.client.post("/api/v1/sources", json={"name": "LRCLIB", "type": "lrclib"}, headers=PAGE)
        with mock.patch("stanzarr.sources.lrclib.lookup", return_value=("synced", "[00:01.00] hello\n", 7)) as asked:
            found = self.client.post("/api/v1/find", json={"query": "Band - Song (Official Video)"}, headers=PAGE).json()
        self.assertEqual((found["artist"], found["title"]), ("Band", "Song"))
        self.assertEqual(asked.call_args.args[:2], ("Band", "Song"))
        self.assertTrue(found["answers"][0]["synced"])
        deezer = {"title": "Song (feat. Other)", "duration": 200, "artist": {"name": "Band"}}
        with mock.patch("stanzarr.api.routes.lyrics.web.get_json", return_value=deezer), \
                mock.patch("stanzarr.sources.lrclib.lookup", return_value=("none", "", None)) as asked:
            linked = self.client.post("/api/v1/find", json={"query": "https://www.deezer.com/en/track/123"},
                                      headers=PAGE).json()
        self.assertEqual((linked["artist"], linked["title"], linked["resolved"]["via"]), ("Band", "Song", "deezer"))
        self.assertEqual(asked.call_args.args[3], 200)
        # Never a link inside the network.
        for inside in ("http://192.168.1.10/admin", "http://stanzarr.homelab.lan/", "https://soundcloud.com:8443/x",
                       "ftp://soundcloud.com/x"):
            self.assertEqual(self.client.post("/api/v1/find", json={"query": inside}, headers=PAGE).status_code, 422, inside)
        log = self.client.get("/api/v1/activity", params={"kind": "search"}).json()
        self.assertEqual([e["actor"] for e in log["events"]][-2:], ["admin", "admin"])
        self.assertEqual(self.client.get("/api/v1/activity", params={"ok": False}).json()["total"], 5)
        self.assertIn("busy", self.client.get("/api/v1/activity/now").json())

    def test_settings_cutoff(self):
        changed = self.client.patch("/api/v1/settings", json={"cutoff": "ai", "auto_ai": True}, headers=PAGE).json()
        self.assertEqual((changed["cutoff"], changed["auto_ai"]), ("ai", True))
        self.assertEqual(self.client.patch("/api/v1/settings", json={"cutoff": "best"}, headers=PAGE).status_code, 422)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_editor_roundtrip_and_audio_ranges(self):
        song = tone(os.path.join(self.cfg.library[0], "A", "R", "01 - One.mp3"), title="One", artist="A")
        with open(song[:-4] + ".txt", "w") as out:
            out.write("first\nsecond\n")
        library.scan(self.services.db, self.cfg.library)
        song_id = self.client.get("/api/v1/songs").json()["songs"][0]["id"]
        data = self.client.get(f"/api/v1/songs/{song_id}/editor").json()
        self.assertEqual((data["origin"], len(data["lines"])), ("plain", 2))
        saved = self.client.put(f"/api/v1/songs/{song_id}/lyrics", json={"lines": data["lines"]}, headers=PAGE)
        self.assertEqual(saved.json()["saved"], 2)
        ranged = self.client.get(f"/api/v1/songs/{song_id}/audio/mix", headers={"Range": "bytes=0-99"})
        self.assertEqual((ranged.status_code, len(ranged.content)), (206, 100))
        peaks = self.client.get(f"/api/v1/songs/{song_id}/peaks/mix").json()
        self.assertEqual(peaks["rate"], 200)


if __name__ == "__main__":
    unittest.main()
