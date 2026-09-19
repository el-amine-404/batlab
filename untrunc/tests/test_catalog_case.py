import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'scripts' / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


catalog = load('catalog')
case = load('case')


class CatalogTests(unittest.TestCase):
    def test_cleanup_finds_latest_summary_with_unique_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            with self.assertRaises(ValueError):
                case.latest_results(work)
            for stamp, suffix in [('20000101T120000', 'abcdef12'), ('20000101T130000', '12345678')]:
                folder = work / f'{stamp}-summary-{suffix}'
                folder.mkdir()
                (folder / 'results.json').write_text(json.dumps([{'file': suffix}]))
            self.assertEqual(case.latest_results(work), [{'file': '12345678'}])

    def test_ranking_prefers_codec_over_nearest_date(self):
        target = {'path': 'bad.mp4', 'signature': {'extradata_hash': 'correct'}, 'date': 1000}
        nearest = {'path': 'near.mp4', 'signature': {'extradata_hash': 'wrong'}, 'date': 999, 'status': 'decode-clean'}
        matching = {'path': 'far.mp4', 'signature': {'extradata_hash': 'correct'}, 'date': 0, 'status': 'decode-clean'}
        unverified = dict(matching, path='unverified.mp4', status='probe-only-unverified')
        result = catalog.rank(target, [nearest, matching, unverified])
        self.assertEqual([r['path'] for r in result], ['far.mp4', 'near.mp4'])
        target['signature'] = {}
        self.assertIn('heuristic-only', catalog.rank(target, [nearest])[0]['confidence'])

    def test_full_scan_detects_missing_metadata_and_stages_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'library'
            src.mkdir()
            good = src / '2000-01-01_12h-00m-00s.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=128x96:rate=30:duration=0.3', '-c:v', 'libx264',
                            '-bf', '0', str(good)], check=True)
            b = good.read_bytes()
            broken = src / '2000-01-01_12h-01m-00s.mp4'
            broken.write_bytes(b[:b.index(b'moov') - 4])
            config = root / 'case.json'
            config.write_text(json.dumps({'case': 'test', 'repair_root': str(root / 'cases'),
                                         'source_root': str(src), 'broken': broken.name, 'references': []}))
            c, work, source = case.case_config(config)
            report = catalog.scan(source, work / 'work/001-scan/catalog')
            self.assertEqual(len(report['rankings'][broken.name]), 1)
            before = case.sha(broken)
            case.prepare(c, work, source)
            case.prepare(c, work, source)  # rerunnable without duplicates
            self.assertEqual(case.sha(work / 'input' / broken.name), before)
            self.assertEqual(json.loads((work / 'references/selection.json').read_text()), [good.name])
            self.assertEqual(case.sha(broken), before)
            (work / 'input' / broken.name).write_bytes(b'changed')
            with self.assertRaises(ValueError):
                case.prepare(c, work, source)

    def test_probe_scan_does_not_claim_healthy_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(catalog.rank({'path': 'bad', 'signature': {}, 'date': None},
                             [{'path': 'good', 'status': 'probe-only-unverified'}]), [])
            report = catalog.scan(root, root / 'result', mode='probe')
            self.assertEqual(report['rankings'], {})


    def test_agent_setup_is_replayable_and_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'agent.json'
            home = root / 'agent-home'
            config.write_text(json.dumps({'home': str(home), 'base_url': 'http://localhost:11434/v1',
                                         'base_model': 'qwen3.5:9b', 'model': 'test-model',
                                         'context_tokens': 65536, 'api_timeout_seconds': 1800}))
            case.agent('agent-setup', config)
            before = (home / 'config.yaml').read_text()
            case.agent('agent-setup', config)
            self.assertEqual((home / 'config.yaml').read_text(), before)
            self.assertEqual(len(list(home.glob('config.backup-*.yaml'))), 1)
            self.assertIn('65536', (home / 'Modelfile').read_text())

    def test_cleanup_rejects_other_originals(self):
        tail = load('tail_recipe')
        recovery = load('recover')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'wrong.mp4'
            original.write_bytes(b'not the known recording')
            recipe = root / 'recipe.json'
            recipe.write_text(json.dumps({'original_sha256': '0' * 64}))
            with self.assertRaisesRegex(ValueError, 'different original'):
                tail.clean(recovery, original, original, recipe)


if __name__ == '__main__':
    unittest.main()
