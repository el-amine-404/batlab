import contextlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


case = load('case')
catalog_tool = load('catalog')
report = load('report')


def match(path, score=100.0, reasons=('codec_name: match', 'model: different', 'same directory'),
          confidence='metadata-supported'):
    return {'path': path, 'score': score, 'reasons': list(reasons), 'confidence': confidence}


class ReportTests(unittest.TestCase):
    def catalog(self, **extra):
        data = {'complete': True, 'mode': 'full', 'items': [
            {'path': 'ok.mp4', 'status': 'decode-clean', 'size': 10},
            {'path': 'z/cut.mp4', 'status': 'unreadable-or-no-video', 'size': 1536,
             'probe_errors': '\n[mov,mp4 @ 0x1] moov atom not found\nInvalid data found\n'},
            {'path': 'a/glitch.mp4', 'status': 'decode-errors', 'size': 5 * 1024 * 1024, 'error_log_lines': 7,
             'log': 'decode-00002.log'},
            {'path': 'lonely.mp4', 'status': 'unreadable-or-no-video', 'size': 0}],
            'rankings': {'z/cut.mp4': [match('ok.mp4', 250.5), match('b.mp4', 90, ['codec_name: different'],
                                                                   'heuristic-only; damaged file lacks codec configuration')],
                         'a/glitch.mp4': [match('ok.mp4', 12.34)], 'lonely.mp4': []}}
        data.update(extra)
        return data

    def render(self, catalog=None, top=1, warnings=(), log_text=None):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'catalog.json'
            if log_text is not None:
                (Path(tmp) / 'decode-00002.log').write_text(log_text)
            return '\n'.join(report.render_suspects(catalog or self.catalog(), file, '/src', top, warnings))

    def test_suspects_are_sorted_and_clean_files_excluded(self):
        self.assertEqual([i['path'] for i in report.suspects(self.catalog())], ['a/glitch.mp4', 'lonely.mp4', 'z/cut.mp4'])

    def test_listing_shows_counts_details_and_matches(self):
        text = self.render(log_text='\n[h264 @ 0x2] error while decoding MB 3 4\nmore\n')
        self.assertIn('Scanned 4 video(s), mode full: 1 decode-clean · 1 decode-errors · 2 unreadable-or-no-video', text)
        self.assertIn('ffprobe: [mov,mp4 @ 0x1] moov atom not found', text)
        self.assertNotIn('Invalid data found', text)
        self.assertIn('5.0 MB', text)
        self.assertIn('1.5 KB', text)
        self.assertIn('7 decoder message(s); first: [h264 @ 0x2] error while decoding MB 3 4', text)
        self.assertIn('match: codec_name | differ: model | same directory', text)
        self.assertIn('[heuristic-only]', text)
        self.assertIn('no decode-verified match found', text)
        self.assertIn('make untrunc-case-batch', text)
        self.assertIn('What the statuses mean:', text)
        self.assertIn('unreadable-or-no-video: cannot be opened as video', text)
        self.assertIn('permissions or an unsupported format', text)
        self.assertEqual(text.count('cannot be opened as video'), 1)  # explained once, not per file

    def test_only_the_top_matches_are_marked_as_used(self):
        lines = self.render(top=1).splitlines()
        marked = [l for l in lines if l.lstrip().startswith('*')]
        self.assertEqual(len(marked), 2)  # first match of cut.mp4 and of glitch.mp4
        self.assertTrue(any('b.mp4' in l and not l.lstrip().startswith('*') for l in lines))
        self.assertIn('max_references = 1', self.render(top=1))

    def test_missing_log_and_probe_text_degrade_gracefully(self):
        text = self.render()  # decode log file absent
        self.assertIn('7 decoder message(s)', text)
        self.assertNotIn('first:', text)
        self.assertIn('ffprobe found no video stream', text)  # lonely.mp4 has no probe text
        self.assertEqual(report.first_line_of_file('/nonexistent/file'), '')

    def test_warnings_probe_mode_and_unfinished_catalog(self):
        partial = {'complete': False, 'mode': 'full', 'items': self.catalog()['items']}
        text = self.render(partial, warnings=['catalog is incomplete'])
        self.assertIn('Warning: catalog is incomplete', text)
        self.assertIn('matches are only available after the scan finishes', text)
        self.assertNotIn('no decode-verified match found', text)
        self.assertIn('Rescan with scan_mode "full"', self.render(self.catalog(mode='probe')))

    def test_no_suspects(self):
        text = self.render({'complete': True, 'mode': 'full', 'items': [{'path': 'a.mp4', 'status': 'decode-clean'}]})
        self.assertIn('No suspect files', text)
        self.assertNotIn('make untrunc-case-batch', text)

    def test_sizes_and_clipping(self):
        self.assertEqual([report.human_size(n) for n in (None, 0, 1023, 1024, 3 * 1024 ** 3)],
                         ['unknown size', '0 B', '1023 B', '1.0 KB', '3.0 GB'])
        self.assertEqual(len(report.clip('x' * 500)), 110)
        self.assertEqual(report.clip('a\n  b\tc'), 'a b c')

    def test_batch_summary(self):
        entries = {'b.mp4': {'status': 'error', 'message': 'boom'},
                   'a.mp4': {'status': 'candidate-ready', 'candidates': ['/w/1.mp4', '/w/2.mp4', '/w/3.mp4', '/w/4.mp4']},
                   'c.mp4': {'status': 'something-new'}}
        text = '\n'.join(report.render_batch(entries, '/r.json', remaining=2))
        self.assertIn('1 candidate ready · 1 error', text)
        self.assertIn('/w/3.mp4', text)
        self.assertNotIn('/w/4.mp4', text)  # only the first three are listed
        self.assertIn('boom', text)
        self.assertIn('something-new', text)
        self.assertIn('2 suspect(s) not processed yet', text)
        self.assertIn('/r.json', text)
        self.assertNotIn('not processed yet', '\n'.join(report.render_batch(entries, '/r.json')))
        self.assertIn('play each candidate', text)
        failed = '\n'.join(report.render_batch({'x.mp4': {'status': 'error', 'message': 'm'}}, '/r.json'))
        self.assertNotIn('play each candidate', failed)  # nothing was repaired, so no review reminder


class Fixture(unittest.TestCase):
    """A source library, a case configuration and a hand-written scan catalog."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.src = self.base / 'library'
        self.config = self.base / 'case.json'
        self.config.write_text(json.dumps({'case': 'lib', 'repair_root': str(self.base / 'cases'),
                                           'source_root': str(self.src), 'broken': '', 'references': [],
                                           'max_references': 2, 'scan_mode': 'full'}))
        self.src.mkdir()
        self.c, self.root, self.source = case.case_config(self.config)
        self.write_catalog()

    def tearDown(self):
        self.tmp.cleanup()

    def write_catalog(self, good=('trip/good-a.mp4', 'trip/good-b.mp4'), bad=('trip/bad-a.mp4', 'trip/bad-b.mp4'),
                      orphan=('lonely.mp4',), **overrides):
        items, rankings = [], {}
        ranked = [match(g, 200 - n) for n, g in enumerate(good)]
        for names, status in ((good, 'decode-clean'), (bad, 'decode-errors'), (orphan, 'unreadable-or-no-video')):
            for name in names:
                file = self.src / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(name.encode() * 20)
                st = file.stat()
                items.append({'path': name, 'status': status, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns})
        for name in bad:
            rankings[name] = ranked
        for name in orphan:
            rankings[name] = []
        data = {'complete': True, 'mode': 'full', 'host_source_root': str(self.source), 'walk_errors': [],
                'items': items, 'rankings': rankings}
        data.update(overrides)
        folder = self.root / 'work' / '20000101T000000-scan-abcd12ef' / 'catalog'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'catalog.json').write_text(json.dumps(data))
        return data

    def edit_catalog(self, change):
        file = self.root / 'work' / '20000101T000000-scan-abcd12ef' / 'catalog' / 'catalog.json'
        data = json.loads(file.read_text())
        change(data)
        file.write_text(json.dumps(data))


class FakeCompose:
    """Stands in for `docker compose run`: records the call and writes a summary like recover.py does."""

    serial = itertools.count(1)  # like recover.py's timestamped names: unique across runs

    def __init__(self, codes=None, default=0):
        self.codes, self.default, self.calls = codes or {}, default, []

    def __call__(self, env, *args):
        self.calls.append((dict(env), args))
        broken = env['BROKEN']
        code = self.codes.get(broken, self.default)
        if code in (0, 2):
            folder = Path(env['REPAIR_CASE_DIR']) / 'work' / f'{next(self.serial):06d}-summary-fake'
            folder.mkdir(parents=True)
            status = 'decode-clean-needs-review' if code == 0 else 'needs-investigation'
            (folder / 'results.json').write_text(json.dumps([{'file': f'/work/attempt-{broken}/out.mp4', 'status': status},
                                                            {'file': '/work/other.mp4', 'status': 'needs-investigation'}]))
        return code

    @property
    def brokens(self):
        return [env['BROKEN'] for env, _ in self.calls]


class CatalogTests(Fixture):
    def test_problems(self):
        good = self.write_catalog()
        self.assertEqual(case.catalog_problems(good, self.source), [])
        incomplete = case.catalog_problems({'complete': False, 'walk_errors': ['a', 'b', 'c']}, self.source)
        self.assertEqual(len(incomplete), 1)
        self.assertIn('incomplete', incomplete[0])
        self.assertIn('a; b', incomplete[0])
        self.assertNotIn('c', incomplete[0].split('folders')[1])  # only the first two are quoted
        other = case.catalog_problems(dict(good, host_source_root='/elsewhere'), self.source)
        self.assertIn('different source root (/elsewhere)', other[0])
        self.assertIn('does not record', case.catalog_problems({'complete': True}, self.source)[0])

    def test_load_and_usable_catalog(self):
        file, data = case.load_catalog(self.root / 'work', 'none')
        self.assertTrue(file.name == 'catalog.json' and data['complete'])
        with self.assertRaisesRegex(ValueError, 'no scan'):
            case.load_catalog(self.base / 'empty', 'no scan')
        self.write_catalog(complete=False)
        with self.assertRaisesRegex(ValueError, 'Cannot use the latest scan: catalog is incomplete'):
            case.usable_catalog(self.root / 'work', self.source, 'x')

    def test_newest_catalog_wins(self):
        newer = self.root / 'work' / '20990101T000000-scan-99999999' / 'catalog'
        newer.mkdir(parents=True)
        (newer / 'catalog.json').write_text(json.dumps({'complete': True, 'marker': 'new'}))
        self.assertEqual(case.load_catalog(self.root / 'work', 'x')[1]['marker'], 'new')

    def test_prepare_uses_a_supplied_catalog_and_still_detects_stale_files(self):
        data = self.write_catalog()
        sub = self.base / 'sub'
        case.make_case_dirs(sub)
        one = dict(self.c, broken='trip/bad-a.mp4', references=[])
        case.prepare(one, sub, self.source, data)
        self.assertEqual(json.loads((sub / 'references/selection.json').read_text()), ['good-a.mp4', 'good-b.mp4'])
        (self.src / 'trip/good-b.mp4').write_bytes(b'changed after the scan')
        with self.assertRaisesRegex(ValueError, 'Stale scan for trip/good-b.mp4'):
            case.prepare(one, sub, self.source, data)

    def test_source_check_can_be_skipped_for_listing(self):
        config = json.loads(self.config.read_text())
        config['source_root'] = str(self.base / 'unmounted')
        self.config.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'not found'):
            case.case_config(self.config)
        self.assertEqual(case.case_config(self.config, check_source=False)[2], self.base / 'unmounted')


class StagingTests(Fixture):
    def test_interrupted_copy_is_replaced_on_the_next_run_not_refused(self):
        src, dst = self.src / 'trip/bad-a.mp4', self.base / 'input.mp4'

        def interrupted(a, b, *args):
            b.write(a.read(50))  # half a file reached the disk
            raise KeyboardInterrupt
        with patch.object(case.shutil, 'copyfileobj', interrupted), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                case.stage_file(src, dst)
        self.assertFalse(dst.exists())  # never a truncated file under the final name
        self.assertTrue(dst.with_name('input.mp4.partial').exists())
        result = case.stage_file(src, dst)
        self.assertEqual(dst.read_bytes(), src.read_bytes())
        self.assertFalse(dst.with_name('input.mp4.partial').exists())
        self.assertEqual(result['sha256'], case.sha(src))
        dst.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite different bytes'):
            case.stage_file(src, dst)  # a finished, different file is still never overwritten
        self.assertEqual(dst.read_bytes(), b'different')

    def test_failed_verification_leaves_nothing_behind(self):
        src, dst = self.src / 'trip/bad-a.mp4', self.base / 'input.mp4'
        calls = iter([case.sha(src), 'not-the-same'])
        with patch.object(case, 'sha', side_effect=lambda f: next(calls)):
            with self.assertRaisesRegex(ValueError, 'Copy verification failed'):
                case.stage_file(src, dst)
        self.assertEqual(list(self.base.glob('input.mp4*')), [])


class BatchTests(Fixture):
    def run_batch(self, fake, limit=None, config=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(case, 'compose', fake), patch.object(case.shutil, 'which', return_value='/usr/bin/docker'), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = case.run_batch(config or self.c, self.root, self.source, limit)
        return code, out.getvalue(), err.getvalue()

    def entries(self):
        return json.loads((self.root / 'batch' / 'batch-report.json').read_text())['entries']

    def test_every_suspect_gets_its_own_prepared_case_and_report_entry(self):
        fake = FakeCompose()
        code, out, _ = self.run_batch(fake)
        self.assertEqual(code, 2)  # lonely.mp4 has no references
        self.assertEqual(sorted(fake.brokens), ['bad-a.mp4', 'bad-b.mp4'])
        entries = self.entries()
        self.assertEqual({k: v['status'] for k, v in entries.items()},
                         {'trip/bad-a.mp4': 'candidate-ready', 'trip/bad-b.mp4': 'candidate-ready',
                          'lonely.mp4': 'no-references'})
        for rel in ('trip/bad-a.mp4', 'trip/bad-b.mp4'):
            folder = Path(entries[rel]['case_dir'])
            self.assertEqual(folder.parent, self.root / 'batch')
            self.assertEqual((folder / 'input' / Path(rel).name).read_bytes(), (self.src / rel).read_bytes())
            self.assertEqual(sorted(p.name for p in (folder / 'references').glob('*.mp4')), ['good-a.mp4', 'good-b.mp4'])
            self.assertEqual(entries[rel]['candidates'], [str(folder / 'work' / f'attempt-{Path(rel).name}' / 'out.mp4')])
            self.assertEqual(entries[rel]['references'], ['trip/good-a.mp4', 'trip/good-b.mp4'])
        self.assertIn('Suspects: 3 · already have a candidate: 0 · to process now: 3', out)
        self.assertIn('no references', out)
        # Originals untouched.
        self.assertEqual((self.src / 'trip/bad-a.mp4').read_bytes(), b'trip/bad-a.mp4' * 20)

    def test_container_environment_is_per_suspect(self):
        fake = FakeCompose()
        self.run_batch(fake)
        env, args = fake.calls[0]
        self.assertEqual(args, ('untrunc', 'auto'))
        folder = Path(self.entries()['trip/bad-a.mp4']['case_dir'])
        self.assertEqual(env['REPAIR_CASE_DIR'], str(folder))
        self.assertEqual(env['REPAIR_RECIPES_DIR'], str(folder / 'recipes'))
        self.assertEqual((env['BROKEN'], env['FPS'], env['MAX_REFERENCES'], env['REFERENCE']), ('bad-a.mp4', '', '2', ''))
        self.assertTrue((folder / 'recipes').is_dir() and (folder / 'work').is_dir())

    def test_max_references_limits_the_staged_references(self):
        fake = FakeCompose()
        self.run_batch(fake, config=dict(self.c, max_references=1))
        folder = Path(self.entries()['trip/bad-a.mp4']['case_dir'])
        self.assertEqual(json.loads((folder / 'references/selection.json').read_text()), ['good-a.mp4'])

    def test_exit_codes_and_statuses_follow_the_container(self):
        self.write_catalog(orphan=())
        code, out, _ = self.run_batch(FakeCompose(codes={'bad-a.mp4': 2, 'bad-b.mp4': 1}))
        self.assertEqual(code, 2)
        entries = self.entries()
        self.assertEqual(entries['trip/bad-a.mp4']['status'], 'needs-investigation')
        self.assertIn('no decode-clean candidate', entries['trip/bad-a.mp4']['message'])
        self.assertEqual(entries['trip/bad-b.mp4']['status'], 'error')
        self.assertIn('exited with status 1', entries['trip/bad-b.mp4']['message'])
        self.assertNotIn('candidates', entries['trip/bad-b.mp4'])  # never read a stale summary after a failed run
        self.write_catalog(orphan=())
        self.assertEqual(self.run_batch(FakeCompose())[0], 0)

    def test_zero_status_without_a_listed_candidate_is_not_reported_as_success(self):
        class Silent(FakeCompose):
            def __call__(self, env, *args):
                self.calls.append((dict(env), args))
                return 0
        self.write_catalog(orphan=())
        code, _, _ = self.run_batch(Silent())
        self.assertEqual(code, 2)
        self.assertEqual({e['status'] for e in self.entries().values()}, {'needs-investigation'})

    def test_resume_skips_finished_files_and_retries_the_rest(self):
        self.write_catalog(orphan=())
        first = FakeCompose(codes={'bad-b.mp4': 2})
        self.assertEqual(self.run_batch(first)[0], 2)
        retried_folder = self.entries()['trip/bad-b.mp4']['case_dir']
        second = FakeCompose()
        code, out, _ = self.run_batch(second)
        self.assertEqual(second.brokens, ['bad-b.mp4'])
        self.assertEqual(self.entries()['trip/bad-b.mp4']['case_dir'], retried_folder)  # same folder on a retry
        self.assertEqual(code, 0)
        self.assertIn('already have a candidate: 1 · to process now: 1', out)
        third = FakeCompose()
        code, out, _ = self.run_batch(third)
        self.assertEqual((code, third.calls), (0, []))
        self.assertIn('to process now: 0', out)

    def test_changed_source_invalidates_only_that_finished_entry(self):
        self.write_catalog(orphan=())
        self.run_batch(FakeCompose())
        (self.src / 'trip/bad-a.mp4').write_bytes(b'rewritten after the first run')
        st = (self.src / 'trip/bad-a.mp4').stat()

        def rescan(data):
            row = next(i for i in data['items'] if i['path'] == 'trip/bad-a.mp4')
            row.update(size=st.st_size, mtime_ns=st.st_mtime_ns)
        self.edit_catalog(rescan)
        old_folder = self.entries()['trip/bad-a.mp4']['case_dir']
        again = FakeCompose()
        code, _, _ = self.run_batch(again)
        self.assertEqual((again.brokens, code), (['bad-a.mp4'], 0))
        new_folder = self.entries()['trip/bad-a.mp4']['case_dir']
        self.assertNotEqual(old_folder, new_folder)  # the earlier copy and attempts are kept, not overwritten
        self.assertEqual((Path(old_folder) / 'input/bad-a.mp4').read_bytes(), b'trip/bad-a.mp4' * 20)
        self.assertEqual((Path(new_folder) / 'input/bad-a.mp4').read_bytes(), b'rewritten after the first run')

    def test_limit_processes_a_prefix_and_reports_the_rest(self):
        self.write_catalog(orphan=())
        first = FakeCompose()
        code, out, _ = self.run_batch(first, limit=1)
        self.assertEqual((first.brokens, code), (['bad-a.mp4'], 2))
        self.assertIn('1 suspect(s) not processed yet', out)
        second = FakeCompose()
        code, out, _ = self.run_batch(second, limit=1)
        self.assertEqual((second.brokens, code), (['bad-b.mp4'], 0))
        self.assertNotIn('not processed yet', out)

    def test_a_bad_file_does_not_stop_the_others(self):
        self.write_catalog(good=('trip/g1.mp4', 'trip/g2.mp4', 'trip/g3.mp4'), bad=('trip/a-bad.mp4', 'trip/b-bad.mp4'), orphan=())
        self.edit_catalog(lambda d: d['rankings'].update({'trip/a-bad.mp4': [match('trip/g3.mp4')]}))
        (self.src / 'trip/g3.mp4').write_bytes(b'changed after the scan')  # only a-bad depends on g3
        fake = FakeCompose()
        code, out, _ = self.run_batch(fake)
        entries = self.entries()
        self.assertEqual(code, 2)
        self.assertEqual(entries['trip/a-bad.mp4']['status'], 'error')
        self.assertIn('Stale scan for trip/g3.mp4', entries['trip/a-bad.mp4']['message'])
        self.assertEqual(entries['trip/b-bad.mp4']['status'], 'candidate-ready')
        self.assertEqual(fake.brokens, ['b-bad.mp4'])  # no container was started for the stale file

    def test_stops_after_repeated_errors_and_keeps_finished_work(self):
        self.write_catalog(bad=('a1.mp4', 'a2.mp4', 'a3.mp4', 'a4.mp4', 'a5.mp4'), orphan=())
        fake = FakeCompose(codes={'a1.mp4': 0, 'a2.mp4': 125, 'a3.mp4': 125, 'a4.mp4': 125}, default=0)
        code, out, err = self.run_batch(fake)
        self.assertEqual((code, fake.brokens), (1, ['a1.mp4', 'a2.mp4', 'a3.mp4', 'a4.mp4']))
        self.assertIn('Stopping after 3 consecutive errors', err)
        self.assertEqual(self.entries()['a1.mp4']['status'], 'candidate-ready')
        self.assertIn('1 suspect(s) not processed yet', out)

    def test_a_success_resets_the_error_streak(self):
        self.write_catalog(bad=('a1.mp4', 'a2.mp4', 'a3.mp4', 'a4.mp4', 'a5.mp4'), orphan=())
        fake = FakeCompose(codes={'a1.mp4': 125, 'a2.mp4': 125, 'a3.mp4': 0, 'a4.mp4': 125, 'a5.mp4': 125})
        code, _, err = self.run_batch(fake)
        self.assertEqual((code, len(fake.calls)), (2, 5))
        self.assertNotIn('Stopping', err)

    def test_ctrl_c_saves_finished_files_and_exits_130(self):
        self.write_catalog(orphan=())

        class Interrupting(FakeCompose):
            def __call__(self, env, *args):
                if len(self.calls) == 1:
                    raise KeyboardInterrupt
                return super().__call__(env, *args)
        fake = Interrupting()
        code, out, err = self.run_batch(fake)
        self.assertEqual(code, 130)
        self.assertIn('Interrupted', err)
        self.assertEqual(list(self.entries()), ['trip/bad-a.mp4'])
        again = FakeCompose()
        self.assertEqual(self.run_batch(again)[0], 0)
        self.assertEqual(again.brokens, ['bad-b.mp4'])

    def test_unusable_setups_are_refused_before_any_container_runs(self):
        for bad, message in [({'references': ['x.mp4']}, 'references'), ({'fps': '30'}, 'fps')]:
            fake = FakeCompose()
            with self.assertRaisesRegex(ValueError, message):
                self.run_batch(fake, config=dict(self.c, **bad))
            self.assertEqual(fake.calls, [])
        self.write_catalog(mode='probe')
        with self.assertRaisesRegex(ValueError, 'scan_mode "full"'):
            self.run_batch(FakeCompose())
        self.write_catalog(complete=False)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.run_batch(FakeCompose())
        self.write_catalog(host_source_root='/elsewhere')
        with self.assertRaisesRegex(ValueError, 'different source root'):
            self.run_batch(FakeCompose())
        with patch.object(case.shutil, 'which', return_value=None), self.assertRaisesRegex(ValueError, 'docker was not found'):
            case.run_batch(self.c, self.root, self.source)
        empty = self.base / 'empty-case'
        c2, root2, src2 = case.case_config(self.write_config(empty))
        with patch.object(case.shutil, 'which', return_value='/usr/bin/docker'), self.assertRaisesRegex(ValueError, 'No scan found'):
            case.run_batch(c2, root2, src2)

    def write_config(self, repair_root):
        config = self.base / 'other.json'
        config.write_text(json.dumps({'case': 'other', 'repair_root': str(repair_root), 'source_root': str(self.src)}))
        return config

    def test_nothing_to_repair(self):
        self.write_catalog(bad=(), orphan=())
        fake = FakeCompose()
        code, out, _ = self.run_batch(fake)
        self.assertEqual((code, fake.calls), (0, []))
        self.assertIn('nothing to repair', out)

    def test_damaged_report_is_not_silently_replaced(self):
        (self.root / 'batch').mkdir()
        for text in ('{not json', '{"entries": []}', '{"entries": {"a": 1}}', '{}'):
            (self.root / 'batch/batch-report.json').write_text(text)
            with self.assertRaisesRegex(ValueError, 'move it aside'):
                self.run_batch(FakeCompose())
        self.assertEqual((self.root / 'batch/batch-report.json').read_text(), '{}')

    def test_case_names_change_with_the_source_version(self):
        catalog = {'items': [{'path': 'a.mp4', 'size': 1, 'mtime_ns': 2}, {'path': 'r.mp4', 'size': 3, 'mtime_ns': 4}]}
        first = case.suspect_case_name('a.mp4', case.source_version(catalog, ['a.mp4', 'r.mp4']))
        self.assertEqual(first, case.suspect_case_name('a.mp4', case.source_version(catalog, ['a.mp4', 'r.mp4'])))
        catalog['items'][1]['size'] = 99  # a reference changed
        self.assertNotEqual(first, case.suspect_case_name('a.mp4', case.source_version(catalog, ['a.mp4', 'r.mp4'])))
        self.assertIn('None', case.source_version(catalog, ['unknown.mp4']))  # missing rows do not raise

    def test_case_names_are_safe_unique_and_stable(self):
        names = [case.suspect_case_name(p) for p in ('a/x.mp4', 'b/x.mp4', 'we ird/../na me!.mov', '???.mp4', 'é' * 100 + '.mp4')]
        self.assertEqual(len(set(names)), len(names))
        for name in names:
            self.assertRegex(name, r'^[A-Za-z0-9_-]+$')
            self.assertLessEqual(len(name), 49)
        self.assertEqual(case.suspect_case_name('a/x.mp4'), names[0])

    def test_container_paths_are_shown_on_the_host(self):
        self.assertEqual(case.host_path(Path('/cases/w'), '/work/a/b.mp4'), '/cases/w/a/b.mp4')
        self.assertEqual(case.host_path(Path('/cases/w'), '/elsewhere/b.mp4'), '/elsewhere/b.mp4')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg is required')
class RealScanEndToEnd(unittest.TestCase):
    """A real (tiny) scan feeds the listing and the batch: no hand-written catalog."""

    def make_video(self, file, seed):
        colour = ['red', 'blue'][seed % 2]
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={colour}:size=64x48:rate=15:duration=0.3',
                        '-c:v', 'libx264', '-bf', '0', '-pix_fmt', 'yuv420p', str(file)], check=True)

    def test_scan_then_suspects_then_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src = base / 'library' / 'event'
            src.mkdir(parents=True)
            for n, name in enumerate(['2000-01-01_10h-00m-00s.mp4', '2000-01-01_10h-05m-00s.mp4']):
                self.make_video(src / name, n)
            data = (src / '2000-01-01_10h-00m-00s.mp4').read_bytes()
            (src / '2000-01-01_10h-10m-00s.mp4').write_bytes(data[:data.index(b'moov') - 4])  # cut before the index
            config = base / 'case.json'
            config.write_text(json.dumps({'case': 'real', 'repair_root': str(base / 'cases'), 'source_root': str(base / 'library'),
                                          'broken': '', 'references': [], 'max_references': 2}))
            c, root, source = case.case_config(config)
            with contextlib.redirect_stdout(io.StringIO()):
                found = catalog_tool.scan(source, root / 'work/20000101T000000-scan-real0001/catalog')
            self.assertEqual([i['path'] for i in report.suspects(found)], ['event/2000-01-01_10h-10m-00s.mp4'])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(case.show_suspects(c, root, source), 0)
            text = out.getvalue()
            self.assertIn('Scanned 3 video(s), mode full: 2 decode-clean · 1 unreadable-or-no-video', text)
            self.assertIn('event/2000-01-01_10h-10m-00s.mp4', text)
            self.assertIn('moov atom not found', text)
            self.assertIn('* ', text)
            self.assertIn('event/2000-01-01_10h-00m-00s.mp4', text)
            self.assertIn('same directory', text)

            fake = FakeCompose()
            with patch.object(case, 'compose', fake), patch.object(case.shutil, 'which', return_value='/usr/bin/docker'), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(case.run_batch(c, root, source), 0)
            folder = next((root / 'batch').glob('2000-01-01_10h-10m-00s-*'))
            self.assertEqual(sorted(p.name for p in (folder / 'references').glob('*.mp4')),
                             ['2000-01-01_10h-00m-00s.mp4', '2000-01-01_10h-05m-00s.mp4'])
            self.assertEqual((folder / 'input' / '2000-01-01_10h-10m-00s.mp4').read_bytes(),
                             (src / '2000-01-01_10h-10m-00s.mp4').read_bytes())


@unittest.skipUnless(os.name == 'posix' and shutil.which('sh'), 'needs a POSIX shell')
class CommandLineTests(Fixture):
    def cli(self, *args, path_prefix=None, **env):
        environment = dict(os.environ, **env)
        if path_prefix:
            environment['PATH'] = f'{path_prefix}{os.pathsep}{environment["PATH"]}'
        return subprocess.run([sys.executable, str(SCRIPTS / 'case.py'), *args], capture_output=True, text=True,
                              env=environment, cwd=self.base)

    def test_suspects_command_lists_without_needing_docker_or_the_source(self):
        shutil.rmtree(self.src)  # e.g. the share is currently unmounted
        result = self.cli('suspects', '--config', str(self.config), path_prefix=str(self.base / 'no-docker-here'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('trip/bad-a.mp4', result.stdout)
        self.assertIn('lonely.mp4', result.stdout)
        self.assertIn('trip/good-a.mp4', result.stdout)

    def test_suspects_command_without_a_scan_says_what_to_do(self):
        other = self.write_other()
        result = self.cli('suspects', '--config', str(other))
        self.assertEqual(result.returncode, 1)
        self.assertIn('run make untrunc-case-scan first', result.stderr)

    def write_other(self):
        config = self.base / 'other.json'
        config.write_text(json.dumps({'case': 'other', 'repair_root': str(self.base / 'cases2'), 'source_root': str(self.src)}))
        return config

    def test_limit_is_validated(self):
        self.assertEqual(self.cli('suspects', '--config', str(self.config), '--limit', '2').returncode, 2)
        self.assertIn('only applies to batch', self.cli('scan', '--config', str(self.config), '--limit', '2').stderr)
        result = self.cli('batch', '--config', str(self.config), '--limit', '0')
        self.assertEqual(result.returncode, 2)
        self.assertIn('at least 1', result.stderr)

    def test_batch_command_reaches_docker_with_the_right_arguments(self):
        bin_dir = self.base / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'docker'
        fake.write_text('#!/bin/sh\n'
                        '{ printf "ARGS:%s\\n" "$*"; echo "BROKEN:$BROKEN"; echo "CASE:$REPAIR_CASE_DIR"; '
                        'echo "UID:$REPAIR_UID"; } >> "$FAKE_LOG"\n'
                        'mkdir -p "$REPAIR_CASE_DIR/work/0001-summary-x"\n'
                        'echo \'[{"file": "/work/a/out.mp4", "status": "decode-clean-needs-review"}]\' '
                        '> "$REPAIR_CASE_DIR/work/0001-summary-x/results.json"\n')
        fake.chmod(0o755)
        log = self.base / 'docker.log'
        self.write_catalog(orphan=())
        result = self.cli('batch', '--config', str(self.config), '--limit', '1', path_prefix=str(bin_dir), FAKE_LOG=str(log))
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)  # one suspect is still waiting
        lines = log.read_text().splitlines()
        self.assertRegex(lines[0], r'^ARGS:compose -f .*compose/untrunc/docker-compose\.yml run --rm --no-deps untrunc auto$')
        self.assertEqual(lines[1], 'BROKEN:bad-a.mp4')
        recorded = json.loads((self.root / 'batch/batch-report.json').read_text())['entries']['trip/bad-a.mp4']['case_dir']
        self.assertEqual(lines[2], f'CASE:{recorded}')
        self.assertEqual(Path(recorded).parent, self.root / 'batch')
        self.assertEqual(lines[3], f'UID:{os.getuid()}')
        self.assertIn('1 suspect(s) not processed yet', result.stdout)
        self.assertIn('candidate ready', result.stdout)

    def test_batch_command_reports_a_missing_docker_clearly(self):
        empty = self.base / 'empty-bin'
        empty.mkdir()
        result = self.cli('batch', '--config', str(self.config), PATH=str(empty))
        self.assertEqual(result.returncode, 1)
        self.assertIn('docker was not found in PATH', result.stderr)


if __name__ == '__main__':
    unittest.main()
