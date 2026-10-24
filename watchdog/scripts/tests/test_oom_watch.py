import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).parents[1] / "oom_watch.py"
SPEC = importlib.util.spec_from_file_location("oom_watch", SCRIPT)
oom_watch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = oom_watch
SPEC.loader.exec_module(oom_watch)


class ParseEventsTest(unittest.TestCase):
    def test_parses_container_limit_and_killed_process(self):
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        container = "a" * 64
        entries = [
            (now, "ffmpeg invoked oom-killer: gfp_mask=0xcc0"),
            (now, "memory: usage 1572864kB, limit 1572864kB, failcnt 4"),
            (now, "swap: usage 123kB, limit 1572864kB, failcnt 0"),
            (now, f"Memory cgroup stats for /system.slice/docker-{container}.scope:"),
            (now, "Memory cgroup out of memory: Killed process 42 (ffmpeg) total-vm:1kB"),
        ]

        self.assertEqual(
            oom_watch.parse_events(entries),
            [oom_watch.OomEvent(now, container, 42, "ffmpeg", (1572864, 1572864), (123, 1572864))],
        )

    def test_parses_host_oom_without_a_container(self):
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        events = oom_watch.parse_events(
            [(now, "Out of memory: Killed process 7 (worker) total-vm:1kB")]
        )
        self.assertIsNone(events[0].container_id)
        self.assertEqual(events[0].process, "worker")

    def test_new_report_resets_previous_container_details(self):
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        entries = [
            (now, f"Memory cgroup stats for /docker-{'b' * 64}.scope:"),
            (now, "one invoked oom-killer:"),
            (now, "Out of memory: Killed process 8 (host-worker) total-vm:1kB"),
        ]
        self.assertIsNone(oom_watch.parse_events(entries)[0].container_id)


if __name__ == "__main__":
    unittest.main()
