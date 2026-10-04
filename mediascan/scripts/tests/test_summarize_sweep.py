import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("summary", Path(__file__).parents[1] / "summarize-sweep.py")
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.statuses = {key: "0" for key in summary.CHECKS}
        for key in self.statuses:
            (self.root / f"{key}.jsonl").write_text("")

    def report(self, key, entries):
        (self.root / f"{key}.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))

    def test_counts_unique_paths_and_overlapping_reasons(self):
        self.report("media", [{"path": "/data/torrents/tv/A/Extras/menu.mkv", "problems": ["SHORT: 0s", "NO_AUDIO: none"]},
                              {"path": "/data/torrents/tv/A/Extras/pv.mkv", "problems": ["SHORT: 10s"]}])
        self.statuses["media"] = "1"
        text, alert = summary.summarize(self.root, self.statuses)
        self.assertTrue(alert)
        for expected in ("2 files flagged", "SHORT 2", "NO_AUDIO 1", "torrent extras 2", "Extras only:", "menu.mkv"):
            self.assertIn(expected, text)

    def test_security_findings_are_not_described_as_legitimate_extras(self):
        entry = {"path": "/data/torrents/tv/A/Extras/bad.srt", "problems": ["MALWARE: signature"]}
        self.report("yara", [entry])
        self.report("clamav", [entry])
        text, alert = summary.summarize(self.root, self.statuses)
        self.assertTrue(alert)  # YARA writes matches but returns zero.
        self.assertIn("1 files flagged", text)
        self.assertIn("MALWARE 1", text)
        self.assertNotIn("Extras only:", text)
        self.assertIn("1 eligible", text)

    def test_failed_scanner_with_empty_report_still_alerts(self):
        self.statuses["clamav"] = "2"
        text, alert = summary.summarize(self.root, self.statuses)
        self.assertTrue(alert)
        self.assertIn("ClamAV: exited 2", text)

    def test_unknown_and_skipped_are_not_clean_verdicts(self):
        self.statuses["yara"] = "skipped"
        self.report("virustotal", [{"path": "a.srt", "verdict": "unknown to VirusTotal", "problems": []}])
        text, alert = summary.summarize(self.root, self.statuses)
        self.assertFalse(alert)
        self.assertIn("YARA: skipped", text)
        self.assertIn("1 unknown", text)

    def test_invalid_or_missing_report_cannot_look_clean(self):
        (self.root / "types.jsonl").write_text("not json")
        (self.root / "subtitles.jsonl").unlink()
        text, alert = summary.summarize(self.root, self.statuses)
        self.assertTrue(alert)
        self.assertIn("2 check/report errors", text)

    def test_large_alert_keeps_counts_security_example_and_report_path(self):
        rows = [{"path": f"/data/torrents/tv/{'x' * 200}/Extras/{i}-{'y' * 200}.mkv", "problems": ["SHORT: 10s"]}
                for i in range(200)]
        self.report("media", rows)
        self.report("subtitles", [{"path": "/data/media/tv/Show/bad.srt", "problems": ["ACTIVE_CONTENT: script"]}])
        text, alert = summary.summarize(self.root, self.statuses)
        self.assertTrue(alert)
        self.assertLessEqual(len(text), 1750)
        self.assertIn("201 files flagged", text)
        self.assertIn("bad.srt", text)
        self.assertIn("Reports:", text)
        self.assertIn("more files in reports", text)

    def test_quarantine_failure_is_reported(self):
        text, alert = summary.summarize(self.root, self.statuses, quarantine_status=1)
        self.assertTrue(alert)
        self.assertIn("Quarantine: failed", text)

    def test_filenames_cannot_trigger_mentions_or_break_message_lines(self):
        self.report("media", [{"path": "/data/media/tv/@everyone\nfoo.mkv", "problems": ["SHORT: 1s"]}])
        text, _ = summary.summarize(self.root, self.statuses)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("\nfoo", text)


class SweepIntegrationTests(unittest.TestCase):
    def test_scanner_failure_discards_stale_detection_and_still_summarizes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scripts, conf, reports = root / "scripts", root / "conf", root / "state/reports"
            for directory in (scripts, conf, reports):
                directory.mkdir(parents=True)
            source = Path(__file__).parents[1]
            for name in ("sweep.sh", "common.sh", "summarize-sweep.py"):
                shutil.copy2(source / name, scripts / name)
            (conf / "roots.txt").write_text(str(root) + "\n")
            (conf / "excludes.txt").write_text("")
            config = root / "settings.env"
            config.write_text(f"MEDIASCAN_STATE_DIR={root}/state\nMEDIASCAN_REPORT_DIR={reports}\n"
                              f"MEDIASCAN_LIBRARY_ROOT={root}\nMEDIASCAN_QUARANTINE={root}/quarantine\n")
            clean = "import sys\nfrom pathlib import Path\nPath(sys.argv[sys.argv.index('--report')+1]).write_text('')\n"
            for name in ("verify-types.py", "verify-media.py", "verify-subtitles.py"):
                (scripts / name).write_text(clean)
            clam = scripts / "scan-clamav.sh"
            clam.write_text("#!/bin/bash\nexit 2\n")
            clam.chmod(0o755)
            (scripts / "quarantine.py").write_text("raise RuntimeError('stale detections reached quarantine')\n")
            for key in ("clamav", "yara", "virustotal"):
                (reports / f"{key}.jsonl").write_text(json.dumps({"path": "OLD", "problems": ["MALWARE: stale"]}) + "\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith("MEDIASCAN_") and k != "VT_API_KEY"}
            env["MEDIASCAN_CONFIG_FILE"] = str(config)
            result = subprocess.run(["bash", str(scripts / "sweep.sh")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("ClamAV: exited 2", result.stdout)
            self.assertIn("YARA: skipped", result.stdout)
            self.assertIn("VirusTotal: skipped", result.stdout)
            self.assertNotIn("stale detections reached quarantine", result.stderr)
            self.assertNotIn("MALWARE", result.stdout)
            self.assertTrue(all(not (reports / f"{key}.jsonl").read_text() for key in ("clamav", "yara", "virustotal")))


if __name__ == "__main__":
    unittest.main()
