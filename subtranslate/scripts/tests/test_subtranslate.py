import fcntl
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import subtranslate  # noqa: E402

DAY = 24 * 3600
EPISODE = subtranslate.KINDS["episode"]
ENGLISH = ("\ufeff1\r\n00:00:02,000 --> 00:00:03,500\r\nHello, Thorfinn.\r\n\r\n"
           "2\r\n00:00:04,000 --> 00:00:06,000\r\n<i>Where is Thorfinn?</i>\r\n\r\n"
           "3\r\n00:00:07,000 --> 00:00:08,000\r\n\r\n")  # an empty cue is dropped


class Bazarr:
    """Bazarr's API as far as this job uses it."""

    def __init__(self):
        self.wanted = [{"seriesTitle": "Show", "episode_number": "1x1", "episodeTitle": "Somewhere",
                        "sonarrSeriesId": 3, "sonarrEpisodeId": 7, "missing_subtitles": [
                            {"code2": "fr", "forced": False, "hi": False},
                            {"code2": "en", "forced": False, "hi": False}]}]
        self.results = []
        self.calls = []
        self.on_download = None

    def get(self, endpoint, timeout=60, **query):
        if endpoint == "episodes/wanted":
            return {"data": self.wanted}
        if endpoint == "episodes":
            return {"data": [{"path": "/data/media/tv/Show/S01E01.mkv"}]}
        if endpoint == "series":
            return {"data": [{"title": "Show", "year": 2019, "overview": "A boy wants revenge."}]}
        if endpoint == "providers/episodes":
            return {"data": self.results}
        raise AssertionError(endpoint)

    def request(self, method, endpoint, timeout=60, **query):
        self.calls.append((method, endpoint, query))
        if method == "POST" and self.on_download:
            self.on_download()


class Translator:
    """Gemini: answers each line with a French one unless told otherwise."""

    def __init__(self):
        self.requests = []
        self.answer = None
        self.before_answer = None

    def __call__(self, system, lines):
        self.requests.append((system, lines))
        if self.before_answer:
            self.before_answer()
        if self.answer:
            return self.answer(lines)
        if "max" in lines[0]:  # the reading-speed request
            return [{"i": line["i"], "t": line["t"][:line["max"]]} for line in lines]
        return [{"i": line["i"], "t": "FR " + line["t"]} for line in lines]


class Subtranslate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.show = self.root / "media/tv/Show"
        self.show.mkdir(parents=True)
        self.video = self.show / "S01E01.mkv"
        self.video.write_bytes(b"v")
        self.english = self.show / "S01E01.en.srt"
        self.english.write_text(ENGLISH, encoding="utf-8", newline="")
        self.french = self.show / "S01E01.fr.srt"
        self.checked()
        self.bazarr, self.translator, self.state = Bazarr(), Translator(), {}
        self.now = self.video.stat().st_mtime + 30 * DAY

    def checked(self):
        """subextract's record after a complete run on the video."""
        self.handled = {str(self.video): {"video": subtranslate.file_stamp(self.video), "sidecars": {
            str(path): subtranslate.file_stamp(path) for path in self.show.glob("*.srt")}}}

    def translate(self, now=None, limit=5, apply=True):
        return subtranslate.translate_missing(self.bazarr, EPISODE, self.root, self.handled, self.state,
                                              now or self.now, self.translator, "gemini-x", limit, apply,
                                              log=lambda *a: None)

    def test_writes_the_translation_with_the_english_timing_and_the_tag(self):
        self.assertEqual(self.translate(), (["Show 1x1 → fr"], [], False))
        cues = subtranslate.parse_srt(self.french.read_text())
        self.assertEqual(cues, [("00:00:00,000", "00:00:02,000", "Translated by AI (gemini-x)"),
                                ("00:00:02,000", "00:00:03,500", "FR Hello, Thorfinn."),
                                ("00:00:04,000", "00:00:06,000", "FR <i>Where is Thorfinn?</i>")])
        system = self.translator.requests[0][0]
        for words in ("French", '"Show" (2019)', "A boy wants revenge.", '"Somewhere"', "Thorfinn"):
            self.assertIn(words, system)
        self.assertIn(("PATCH", "series", {"seriesid": 3, "action": "scan-disk"}), self.bazarr.calls)
        self.assertEqual(list(self.state["ai"]), ["/data/media/tv/Show/S01E01.fr.srt"])
        self.assertTrue(subtranslate.is_translation(self.french))
        self.assertEqual(list(self.show.glob(".subtranslate-*")), [])

    def test_waits_for_bazarr_then_for_subextract(self):
        self.assertEqual(self.translate(now=self.video.stat().st_mtime + 2 * DAY)[0], [])
        self.handled = {}
        self.assertEqual(self.translate()[0], [])
        self.checked()
        self.video.write_bytes(b"a new release")  # subextract hasn't seen this file yet
        self.assertEqual(self.translate()[0], [])
        self.assertEqual(self.translator.requests, [])

    def test_never_over_a_subtitle_in_that_language(self):
        (self.show / "S01E01.fr.hi.ass").write_text("")
        self.assertEqual(self.translate()[0], [])

    def test_a_subtitle_written_during_the_translation_wins(self):
        self.translator.before_answer = lambda: self.french.write_text("human")
        self.assertEqual(self.translate()[0], [])
        self.assertEqual(self.french.read_text(), "human")
        self.assertNotIn(str(self.french), str(self.state.get("ai")))
        self.assertEqual(self.state["attempts"], {})
        self.assertEqual(list(self.show.glob(".subtranslate-*")), [])

    def test_only_from_an_english_subtitle_subextract_checked(self):
        self.english.write_text("1\n00:00:02,000 --> 00:00:03,000\nChanged since\n")
        self.assertEqual(self.translate()[0], [])
        self.checked()
        self.english.write_text("1\n00:00:00,000 --> 00:00:02,000\nTranslated by AI (x)\n")
        self.checked()
        self.assertEqual(self.translate()[0], [])

    def test_plain_english_before_hearing_impaired(self):
        (self.show / "S01E01.en.hi.srt").write_text("1\n00:00:02,000 --> 00:00:03,000\n[door creaks]\n")
        self.checked()
        self.translate()
        self.assertNotIn("door", str(self.translator.requests[0][1]))

    def test_a_bad_answer_writes_nothing_and_is_tried_three_times(self):
        self.translator.answer = lambda lines: [{"i": line["i"], "t": "FR"} for line in lines[1:]]
        for day in range(3):
            self.assertEqual(self.translate(now=self.now + day * DAY)[0], [])
        self.assertFalse(self.french.exists())
        self.assertEqual(self.translate(now=self.now + 3 * DAY), ([], ["Show 1x1 → fr"], False))
        self.assertEqual(self.translate(now=self.now + 4 * DAY), ([], [], False))
        self.assertEqual(len(self.translator.requests), 3)
        self.bazarr.wanted = []
        self.translate(now=self.now + 5 * DAY)
        self.assertEqual(self.state["attempts"], {})

    def test_empty_doubled_or_unknown_lines_are_refused(self):
        cues = [("00:00:01,000", "00:00:02,000", "a"), ("00:00:03,000", "00:00:04,000", "b")]
        for answer in ([{"i": 0, "t": "x"}, {"i": 1, "t": " "}], [{"i": 0, "t": "x"}, {"i": 0, "t": "y"}],
                       [{"i": 0, "t": "x"}, {"i": 5, "t": "y"}], {"i": 0}):
            with self.assertRaises(subtranslate.TranslationFailed):
                subtranslate.translate_cues(lambda system, lines: answer, "", cues)

    def test_a_quota_refusal_stops_the_run_and_costs_no_try(self):
        def refuse(lines):
            raise subtranslate.QuotaExceeded("quota")
        self.translator.answer = refuse
        self.assertEqual(self.translate(), ([], [], True))
        self.assertEqual(self.state["attempts"], {})
        self.assertFalse(self.french.exists())

    def test_a_long_film_goes_in_several_requests(self):
        cues = [(subtranslate.timestamp(n * 2000 + 1000), subtranslate.timestamp(n * 2000 + 2000), f"line {n}")
                for n in range(subtranslate.CHUNK + 10)]
        out = subtranslate.translate_cues(self.translator, "", cues)
        self.assertEqual(len(self.translator.requests), 2)
        self.assertEqual(self.translator.requests[1][1][0], {"i": subtranslate.CHUNK, "t": f"line {subtranslate.CHUNK}"})
        self.assertEqual(out[-1], (cues[-1][0], cues[-1][1], f"FR line {subtranslate.CHUNK + 9}"))

    def test_lines_too_long_for_their_time_are_shortened(self):
        fast = "So, what did you see out there, Thorfinn?"  # 41 characters in 1.5 s: already over 20 a second
        english = [("00:00:01,000", "00:00:02,000", "Run!"),           # 20 characters fit in 1 s
                   ("00:00:03,000", "00:00:06,000", "Where were you?"),  # 60 fit in 3 s
                   ("00:00:07,000", "00:00:08,500", fast)]
        long, short = "Cours, cours aussi vite que tu peux !", "Où étais-tu ?"
        same = "Alors, t'as vu quoi là-bas, Thorfinn ?"  # shorter than the English: left alone
        longer = "Alors, qu'est-ce que tu as vu là-bas, Thorfinn ?"
        translated = [(*english[0][:2], long), (*english[1][:2], short), (*english[2][:2], same)]
        out, stopped = subtranslate.shorten(self.translator, "French", english, translated, log=lambda *a: None)
        self.assertFalse(stopped)
        self.assertEqual(self.translator.requests[0][1], [{"i": 0, "t": long, "en": "Run!", "max": 20}])
        self.assertIn("French", self.translator.requests[0][0])
        self.assertEqual(out, [(*english[0][:2], long[:20]), translated[1], translated[2]])
        translated[2] = (*english[2][:2], longer)
        subtranslate.shorten(self.translator, "French", english, translated, log=lambda *a: None)
        self.assertEqual(self.translator.requests[1][1][1], {"i": 2, "t": longer, "en": fast, "max": len(fast)})

    def test_a_shortening_that_isnt_shorter_or_fails_keeps_the_line(self):
        english = [("00:00:01,000", "00:00:02,000", "Run!")]
        translated = [(*english[0][:2], "<i>Cours, cours aussi vite que tu peux !</i>")]
        for answer in (lambda lines: [{"i": 0, "t": "Cours, cours, cours aussi vite que tu le peux !"}],
                       lambda lines: []):
            self.translator.answer = answer
            out, stopped = subtranslate.shorten(self.translator, "French", english, translated, log=lambda *a: None)
            self.assertEqual((out, stopped), (translated, False))

    def test_a_quota_refusal_while_shortening_still_writes_the_translation(self):
        self.english.write_text("1\n00:00:02,000 --> 00:00:02,500\nSo, what did you see out there?\n")
        self.checked()

        def answer(lines):
            if "max" in lines[0]:
                raise subtranslate.QuotaExceeded("quota")
            return [{"i": 0, "t": "Alors, qu'est-ce que tu as vu là-bas ?"}]
        self.translator.answer = answer
        self.assertEqual(self.translate(), (["Show 1x1 → fr"], [], True))
        self.assertIn("qu'est-ce que tu as vu là-bas", self.french.read_text())

    def test_dry_run_asks_gemini_nothing_and_writes_nothing(self):
        self.assertEqual(self.translate(apply=False)[0], ["Show 1x1 → fr"])
        self.assertEqual(self.translator.requests, [])
        self.assertFalse(self.french.exists())
        self.assertEqual(self.state["attempts"], {})

    def look(self, now=None, minimum=90):
        return subtranslate.look_for_humans(self.bazarr, self.root, self.state, {"episode": minimum, "movie": 70},
                                            now or self.now, 5, 7, True, log=lambda *a: None)

    def test_a_human_subtitle_at_the_minimum_score_replaces_it(self):
        self.translate()
        self.bazarr.calls = []
        self.bazarr.results = [
            {"language": "fr", "forced": "False", "hearing_impaired": "False", "score": 86, "provider": "a", "subtitle": "low"},
            {"language": "ar", "forced": "False", "hearing_impaired": "False", "score": 99, "provider": "b", "subtitle": "ar"},
            {"language": "fr", "forced": "False", "hearing_impaired": "True", "score": 99, "provider": "c", "subtitle": "hi"},
            {"language": "fr", "forced": "False", "hearing_impaired": "False", "score": 94, "provider": "d", "subtitle": "ok"}]
        self.bazarr.on_download = lambda: self.french.write_text("1\n00:00:06,000 --> 00:00:07,000\nSalut\n")
        self.assertEqual(self.look(), ["Show 1x1 fr (d, 94%)"])
        method, endpoint, query = self.bazarr.calls[0]
        self.assertEqual((method, endpoint, query["provider"], query["subtitle"], query["seriesid"], query["episodeid"]),
                         ("POST", "providers/episodes", "d", "ok", 3, 7))
        self.assertEqual(self.state["ai"], {})

    def test_nothing_good_enough_waits_a_week(self):
        self.translate()
        self.bazarr.results = [{"language": "fr", "forced": "False", "hearing_impaired": "False", "score": 86,
                                "provider": "a", "subtitle": "low"}]
        self.assertEqual(self.look(), [])
        self.bazarr.results[0]["score"] = 95
        self.bazarr.on_download = lambda: self.french.write_text("human")
        self.assertEqual(self.look(now=self.now + DAY), [])  # not searched again yet
        self.assertEqual(len(self.look(now=self.now + 7 * DAY)), 1)

    def test_an_ai_subtitle_replaced_by_bazarr_is_forgotten(self):
        self.translate()
        self.french.write_text("1\n00:00:06,000 --> 00:00:07,000\nSalut\n")
        self.assertEqual(self.look(), [])
        self.assertEqual(self.state["ai"], {})

    def test_waits_for_subextract_to_finish(self):
        held = subtranslate.subextract_lock(self.root, 0)
        self.addCleanup(held.close)
        self.assertIsNone(subtranslate.subextract_lock(self.root, 0))
        fcntl.flock(held, fcntl.LOCK_UN)
        again = subtranslate.subextract_lock(self.root, 0)
        self.assertIsNotNone(again)
        again.close()


if __name__ == "__main__":
    unittest.main()
