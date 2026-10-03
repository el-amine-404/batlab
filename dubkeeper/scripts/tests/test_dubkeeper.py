#!/usr/bin/env python3

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "dubkeeper.py"
SPEC = importlib.util.spec_from_file_location("dubkeeper", SCRIPT)
assert SPEC and SPEC.loader
keeper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = keeper
SPEC.loader.exec_module(keeper)


def stream(index: int, kind: str, lang: str | None = None, default: bool = False, title: str = "") -> dict:
    tags = {"language": lang} if lang else {}
    if title:
        tags["title"] = title
    return {"index": index, "codec_type": kind, "tags": tags, "disposition": {"default": int(default)}}


def media(*streams: dict, length: float = 1440.0) -> dict:
    return {"streams": [stream(0, "video") | {"duration": str(length)}, *streams], "format": {"duration": str(length)}}


class LanguageTests(unittest.TestCase):
    def test_missing_audio_picks_one_stream_per_lost_language(self) -> None:
        old = media(stream(1, "audio", "eng"), stream(2, "audio", "eng", title="Commentary"),
                    stream(3, "audio", "jpn"), stream(4, "audio", "fre"))
        new = media(stream(1, "audio", "jpn", default=True))
        self.assertEqual([s["index"] for s in keeper.missing_audio(old, new)], [1, 4])

    def test_commentary_alone_is_not_carried(self) -> None:
        old = media(stream(1, "audio", "jpn"), stream(2, "audio", "eng", title="Director's commentary"))
        self.assertEqual(keeper.missing_audio(old, media(stream(1, "audio", "jpn"))), [])

    def test_undetermined_language_is_never_lost(self) -> None:
        old = media(stream(1, "audio", "und"), stream(2, "audio"))
        self.assertEqual(keeper.missing_audio(old, media(stream(1, "audio", "jpn"))), [])

    def test_reference_prefers_new_default(self) -> None:
        old = media(stream(1, "audio", "eng"), stream(2, "audio", "jpn"))
        new = media(stream(1, "audio", "eng"), stream(2, "audio", "jpn", default=True))
        self.assertEqual(keeper.reference_language(old, new), "jpn")
        self.assertIsNone(keeper.reference_language(media(stream(1, "audio", "eng")), media(stream(1, "audio", "jpn"))))

    def test_video_length_reads_matroska_tag(self) -> None:
        info = {"streams": [{"codec_type": "video", "tags": {"DURATION": "00:28:26.705000000"}}], "format": {}}
        self.assertAlmostEqual(keeper.video_length(info), 1706.705)


class JudgeTests(unittest.TestCase):
    def test_nothing_lost_needs_no_audio_comparison(self) -> None:
        both = media(stream(1, "audio", "jpn"))
        verdict = keeper.judge(Path("old"), Path("new"), both, both)
        self.assertEqual((verdict.carry, verdict.problem), ([], ""))

    def test_different_lengths_are_refused(self) -> None:
        old = media(stream(1, "audio", "eng"), stream(2, "audio", "jpn"), length=1440)
        new = media(stream(1, "audio", "jpn"), length=1300)
        self.assertIn("differ in length", keeper.judge(Path("old"), Path("new"), old, new).problem)

    def test_no_shared_language_is_refused(self) -> None:
        old = media(stream(1, "audio", "eng"))
        new = media(stream(1, "audio", "jpn"))
        self.assertIn("no audio language in both", keeper.judge(Path("old"), Path("new"), old, new).problem)

    def test_offsets(self) -> None:
        old = media(stream(1, "audio", "eng"), stream(2, "audio", "jpn"))
        new = media(stream(1, "audio", "jpn"))
        cases = {"aligned": ([(-2, 0.99), (-2, 0.99)], 0, ""),
                 "shifted": ([(25, 0.98), (26, 0.97)], 255, ""),
                 "drift": ([(0, 0.99), (30, 0.99)], 0, "drifts"),
                 "unrelated": ([(0, 0.4)], 0, "does not match")}
        for name, (lags, offset, problem) in cases.items():
            with self.subTest(name):
                answers = iter(lags)
                with mock.patch.object(keeper, "loudness", return_value=[]), \
                        mock.patch.object(keeper, "best_lag", side_effect=lambda a, b: next(answers)):
                    verdict = keeper.judge(Path("old"), Path("new"), old, new)
                self.assertEqual(verdict.offset_ms, offset)
                self.assertIn(problem, verdict.problem)


class LagTests(unittest.TestCase):
    def test_best_lag_finds_a_shift(self) -> None:
        signal = [math.sin(i / 7) + math.sin(i / 3.1) * 0.5 for i in range(600)]
        shifted = [0.0] * 12 + signal[:-12]
        lag, correlation = keeper.best_lag(signal, shifted, limit=40)
        self.assertEqual(lag, 12)
        self.assertGreater(correlation, 0.99)


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def touch(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
        return path

    def test_restore_sidecars_renames_and_keeps_existing(self) -> None:
        old = self.touch("recycle/Show/Season 01/Show - S01E01 - old.mkv")
        self.touch("recycle/Show/Season 01/Show - S01E01 - old.en.srt")
        self.touch("recycle/Show/Season 01/Show - S01E01 - old.ar.srt")
        self.touch("recycle/Show/Season 01/Show - S01E01 - old.nfo")
        self.touch("recycle/Show/Season 01/Show - S01E01 - older.en.srt")
        new = self.touch("media/Show/Season 01/Show - S01E01 - new.mkv")
        self.touch("media/Show/Season 01/Show - S01E01 - new.en.srt")
        pairs = keeper.missing_sidecars(old, new)
        self.assertEqual(keeper.restore_sidecars(pairs, 0, apply=True), ([".ar.srt"], []))
        self.assertEqual((new.parent / "Show - S01E01 - new.ar.srt").read_text(),
                         "recycle/Show/Season 01/Show - S01E01 - old.ar.srt")
        self.assertEqual((new.parent / "Show - S01E01 - new.en.srt").read_text(),
                         "media/Show/Season 01/Show - S01E01 - new.en.srt")

    def test_an_offset_shifts_srt_and_skips_other_formats(self) -> None:
        old = self.touch("recycle/S/Ep - old.mkv")
        (self.root / "recycle/S/Ep - old.en.srt").write_text("1\n00:00:01,000 --> 00:00:02,500\nHi\n")
        self.touch("recycle/S/Ep - old.fr.ass")
        new = self.touch("media/S/Ep - new.mkv")
        restored, skipped = keeper.restore_sidecars(keeper.missing_sidecars(old, new), 1250, apply=True)
        self.assertEqual((restored, skipped), ([".en.srt"], [".fr.ass"]))
        self.assertIn("00:00:02,250 --> 00:00:03,750", (self.root / "media/S/Ep - new.en.srt").read_text())
        self.assertFalse((self.root / "media/S/Ep - new.fr.ass").exists())

    def test_subtitles_alone_still_need_the_same_video(self) -> None:
        both = media(stream(1, "audio", "jpn"))
        with mock.patch.object(keeper, "loudness", return_value=[]), \
                mock.patch.object(keeper, "best_lag", return_value=(0, 0.3)):
            verdict = keeper.judge(Path("old"), Path("new"), both, both, subtitles=True)
        self.assertIn("subtitles lost", verdict.problem)
        self.assertEqual(keeper.judge(Path("old"), Path("new"), both, both).problem, "")

    def test_find_recycled_prefers_same_series(self) -> None:
        self.touch("recycle/Other/Season 01/Ep.mkv")
        wanted = self.touch("recycle/Show/Season 01/Ep.mkv")
        found = keeper.find_recycled(self.root / "recycle", "/data/media/tv/Show/Season 01/Ep.mkv")
        self.assertEqual(found, wanted)
        self.assertIsNone(keeper.find_recycled(self.root / "recycle", "/data/media/tv/Show/Season 01/None.mkv"))


class HistoryTests(unittest.TestCase):
    def test_recent_upgrades_groups_by_old_file_and_settles(self) -> None:
        now = dt.datetime(2026, 10, 3, 12, tzinfo=dt.timezone.utc)

        def at(minutes_ago: float) -> str:
            return (now - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")

        records = [
            {"sourceTitle": "/data/a.mkv", "seriesId": 1, "episodeId": 10, "date": at(60), "data": {"reason": "Upgrade"}},
            {"sourceTitle": "/data/a.mkv", "seriesId": 1, "episodeId": 11, "date": at(60), "data": {"reason": "Upgrade"}},
            {"sourceTitle": "/data/b.mkv", "seriesId": 1, "episodeId": 12, "date": at(5), "data": {"reason": "Upgrade"}},
            {"sourceTitle": "/data/c.mkv", "seriesId": 1, "episodeId": 13, "date": at(60), "data": {"reason": "Manual"}},
            {"sourceTitle": "/data/d.mkv", "seriesId": 1, "episodeId": 14, "date": at(60 * 24 * 20), "data": {"reason": "Upgrade"}},
        ]

        class FakeSonarr:
            def get(self, endpoint: str, **query: object) -> dict:
                return {"records": records}

        upgrades = keeper.recent_upgrades(FakeSonarr(), now, days=14, settle_minutes=10)
        self.assertEqual([(u.old, u.episode_ids) for u in upgrades], [("/data/a.mkv", {10, 11})])


if __name__ == "__main__":
    unittest.main()
