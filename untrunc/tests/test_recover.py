import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('recover', Path(__file__).parents[1] / 'scripts/recover.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        r.WORK = self.root / 'work'
        r.WORK.mkdir()
        self.good = self.root / 'reference with spaces.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x120:rate=30:duration=1', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264',
                        '-bf', '0', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(self.good)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_media_requires_review_and_preserves_bytes(self):
        before = r.digest(self.good)
        report = r.verify(self.good, self.root / 'check')
        self.assertEqual(report['status'], 'decode-clean-needs-review')
        self.assertEqual(report['complete_recovery'], 'unknown')
        self.assertEqual(before, r.digest(self.good))
        self.assertEqual(len(list((self.root / 'check').glob('frame-*.jpg'))), 3)

    def test_missing_moov_is_not_success(self):
        b = self.good.read_bytes()
        pos = b.index(b'moov') - 4
        bad = self.root / 'broken.mp4'
        bad.write_bytes(b[:pos])
        report = r.verify(bad, self.root / 'bad-check')
        self.assertEqual(report['status'], 'needs-investigation')
        self.assertGreater(report['error_log_lines'], 0)

    def test_reframe_preserves_audio_and_input(self):
        before = r.digest(self.good)
        report = r.reframe(self.good, '30')
        self.assertEqual(report['status'], 'decode-clean-needs-review')
        self.assertIn('audio', report['durations'])
        self.assertAlmostEqual(float(report['durations']['video']), 1, places=2)
        self.assertEqual(before, r.digest(self.good))

    def test_path_escape_and_collision(self):
        with self.assertRaises(ValueError):
            r.inside(r.WORK, '../' + self.good.name)
        (r.WORK / 'link').symlink_to(self.good)
        with self.assertRaises(ValueError):
            r.inside(r.WORK, 'link')
        self.assertNotEqual(r.attempt('same'), r.attempt('same'))


if __name__ == '__main__':
    unittest.main()
