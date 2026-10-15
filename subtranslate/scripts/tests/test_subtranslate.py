import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subtranslate  # noqa: E402

DAY = 24 * 3600
EPISODE = subtranslate.KINDS[0]
CUE = "1\n00:00:01,000 --> 00:00:05,000\n# Subtitles translated with gemini-2.5-flash # \n\n"


class Bazarr:
    """Bazarr's API as far as this job uses it."""

    def __init__(self):
        self.wanted = []
        self.items = {}
        self.history = []
        self.results = []
        self.calls = []
        self.on_download = None

    def get(self, endpoint, timeout=60, **query):
        if endpoint == "episodes/wanted":
            return {"data": self.wanted}
        if endpoint == "episodes":
            return {"data": [self.items[query["episodeid[]"][0]]]}
        if endpoint == "episodes/history":
            return {"data": self.history}
        if endpoint == "providers/episodes":
            return {"data": self.results}
        raise AssertionError(endpoint)

    def request(self, method, endpoint, timeout=60, **query):
        self.calls.append((method, endpoint, query))
        if method == "POST" and self.on_download:
            self.on_download()


class Subtranslate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.show = self.root / "media/tv/Show"
        self.show.mkdir(parents=True)
        self.video = self.show / "S01E01.mkv"
        self.video.write_bytes(b"v")
        (self.show / "S01E01.en.srt").write_text("1\n00:00:02,000 --> 00:00:03,000\nHello\n")
        self.bazarr = Bazarr()
        self.bazarr.wanted = [{"seriesTitle": "Show", "episode_number": "1x1", "sonarrSeriesId": 3,
                               "sonarrEpisodeId": 7, "missing_subtitles": [
                                   {"code2": "fr", "forced": False, "hi": False},
                                   {"code2": "en", "forced": False, "hi": False}]}]
        self.bazarr.items[7] = {"path": "/data/media/tv/Show/S01E01.mkv", "subtitles": [
            {"code2": "en", "path": None, "forced": False, "hi": False, "embedded_track_id": 3},
            {"code2": "en", "path": "/data/media/tv/Show/S01E01.en.srt", "forced": False, "hi": False}]}
        self.state = {}
        self.now = self.video.stat().st_mtime + 30 * DAY

    def translate(self, now=None, limit=5, apply=True):
        return subtranslate.translate_missing(self.bazarr, EPISODE, self.root, self.state, now or self.now,
                                              limit, apply, log=lambda *a: None)

    def test_translates_the_missing_language_from_the_english_sidecar(self):
        asked, gave_up = self.translate()
        self.assertEqual(asked, ["Show 1x1 → fr"])
        self.assertEqual(gave_up, [])
        method, endpoint, query = self.bazarr.calls[0]
        self.assertEqual((method, endpoint, query["action"], query["language"], query["path"], query["id"]),
                         ("PATCH", "subtitles", "translate", "fr", "/data/media/tv/Show/S01E01.en.srt", 7))

    def test_bazarr_goes_first_on_new_videos(self):
        self.assertEqual(self.translate(now=self.video.stat().st_mtime + 2 * DAY)[0], [])

    def test_no_english_sidecar_or_only_a_translation_means_no_source(self):
        (self.show / "S01E01.en.srt").write_text(CUE)
        self.assertEqual(self.translate()[0], [])
        self.bazarr.items[7]["subtitles"].pop()
        self.assertEqual(self.translate()[0], [])

    def test_three_tries_a_day_apart_then_reported_once(self):
        self.assertEqual(len(self.translate()[0]), 1)
        self.assertEqual(self.translate(now=self.now + 3600)[0], [])  # still running, perhaps
        self.assertEqual(len(self.translate(now=self.now + DAY)[0]), 1)
        self.assertEqual(len(self.translate(now=self.now + 2 * DAY)[0]), 1)
        self.assertEqual(self.translate(now=self.now + 3 * DAY), ([], ["Show 1x1 → fr"]))
        self.assertEqual(self.translate(now=self.now + 4 * DAY), ([], []))
        self.bazarr.wanted = []
        self.translate(now=self.now + 5 * DAY)
        self.assertEqual(self.state["attempts"], {})

    def test_dry_run_asks_nothing(self):
        self.assertEqual(len(self.translate(apply=False)[0]), 1)
        self.assertEqual(self.bazarr.calls, [])
        self.assertEqual(self.state["attempts"], {})

    def ai_file(self):
        path = self.show / "S01E01.fr.srt"
        path.write_text(CUE + "2\n00:00:06,000 --> 00:00:07,000\nBonjour\n")
        self.bazarr.history = [{"action": 6, "subtitles_path": "/data/media/tv/Show/S01E01.fr.srt",
                                "sonarrSeriesId": 3, "sonarrEpisodeId": 7, "seriesTitle": "Show",
                                "episode_number": "1x1", "language": {"code2": "fr"}},
                               {"action": 1, "subtitles_path": "/data/media/tv/Show/S01E01.en.srt",
                                "language": {"code2": "en"}}]
        return path

    def look(self, now=None, minimum=90):
        ai = subtranslate.ai_subtitles(self.bazarr, EPISODE, self.root)
        return subtranslate.look_for_humans(self.bazarr, EPISODE, ai, self.root, self.state, now or self.now,
                                            minimum, 5, 7, True, log=lambda *a: None)

    def test_the_cue_marks_an_ai_subtitle(self):
        path = self.ai_file()
        self.assertEqual(list(subtranslate.ai_subtitles(self.bazarr, EPISODE, self.root)),
                         ["/data/media/tv/Show/S01E01.fr.srt"])
        path.write_text("1\n00:00:06,000 --> 00:00:07,000\nBonjour\n")
        self.assertEqual(subtranslate.ai_subtitles(self.bazarr, EPISODE, self.root), {})

    def test_a_human_subtitle_at_the_minimum_score_replaces_it(self):
        path = self.ai_file()
        self.bazarr.results = [
            {"language": "fr", "forced": "False", "hearing_impaired": "False", "score": 86, "provider": "a", "subtitle": "low"},
            {"language": "ar", "forced": "False", "hearing_impaired": "False", "score": 99, "provider": "b", "subtitle": "ar"},
            {"language": "fr", "forced": "False", "hearing_impaired": "True", "score": 99, "provider": "c", "subtitle": "hi"},
            {"language": "fr", "forced": "False", "hearing_impaired": "False", "score": 94, "provider": "d", "subtitle": "ok"}]
        self.bazarr.on_download = lambda: path.write_text("1\n00:00:06,000 --> 00:00:07,000\nSalut\n")
        self.assertEqual(self.look(), ["Show 1x1 fr (d, 94%)"])
        method, endpoint, query = self.bazarr.calls[0]
        self.assertEqual((method, endpoint, query["provider"], query["subtitle"], query["episodeid"]),
                         ("POST", "providers/episodes", "d", "ok", 7))

    def test_nothing_good_enough_waits_a_week(self):
        path = self.ai_file()
        self.bazarr.results = [{"language": "fr", "forced": "False", "hearing_impaired": "False", "score": 86,
                                "provider": "a", "subtitle": "low"}]
        self.assertEqual(self.look(), [])
        self.assertEqual(self.bazarr.calls, [])
        self.bazarr.results[0]["score"] = 95
        self.bazarr.on_download = lambda: path.write_text("1\n00:00:06,000 --> 00:00:07,000\nSalut\n")
        self.assertEqual(self.look(now=self.now + DAY), [])  # not searched again yet
        self.assertEqual(len(self.look(now=self.now + 7 * DAY)), 1)


if __name__ == "__main__":
    unittest.main()
