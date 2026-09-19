import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from progress import Progress
import recover


class ProgressTests(unittest.TestCase):
    def test_heartbeat_and_estimate(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with Progress('Decode', lambda: 0.5, interval=0.01):
                time.sleep(0.06)
        text = output.getvalue()
        self.assertIn('50%', text)
        self.assertIn('remaining', text)
        self.assertIn('done', text)

    def test_unknown_duration_does_not_invent_eta(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with Progress('Repair', interval=0.01):
                time.sleep(0.06)
        self.assertIn('remaining time unknown', output.getvalue())

    def test_timeout_stops_child_and_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = recover.TIMEOUT
            recover.TIMEOUT = 0.05
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    rc = recover.run([sys.executable, '-c', 'import time; time.sleep(10)'], Path(tmp) / 'test.log')
                self.assertEqual(rc, 124)
                self.assertIn('timed out', output.getvalue())
                self.assertIn('incomplete', (Path(tmp) / 'test.log').read_text())
            finally:
                recover.TIMEOUT = old
