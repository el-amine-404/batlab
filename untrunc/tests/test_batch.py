import contextlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / 'scripts'
REAL_RUN = subprocess.run


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
        unreadable = case.catalog_problems({'complete': False, 'walk_errors': ['a', 'b', 'c']}, self.source)
        self.assertEqual(len(unreadable), 1)
        self.assertIn('some folders could not be read: a; b)', unreadable[0])  # only the first two are quoted
        interrupted = case.catalog_problems({'complete': False}, self.source)
        self.assertEqual(len(interrupted), 1)
        self.assertIn('the scan was interrupted: run make untrunc-case-scan again to resume it', interrupted[0])
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


class ScanProgressTests(unittest.TestCase):
    """The scan announces how many videos it found, then counts through them."""

    def scan(self, root, mode='probe'):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = catalog_tool.scan(root, root.parent / 'catalog-out', mode)
        return result, out.getvalue().splitlines()

    def test_announces_the_total_then_counts_each_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'library'
            (root / 'sub').mkdir(parents=True)
            for name in ('a.mp4', 'z.mp4', 'sub/c.MOV'):
                (root / name).write_bytes(b'not a video')
            (root / 'notes.txt').write_text('ignored')
            (root / 'link.mp4').symlink_to(root / 'a.mp4')  # symlinks are skipped, as before
            result, lines = self.scan(root)
            self.assertEqual(lines[0], f'Looking for video files under {root.resolve()} ...')
            self.assertEqual(lines[1], 'Found 3 video file(s) to scan.')
            self.assertEqual(lines[2:], ['Scanning 1/3: a.mp4', 'Scanning 2/3: z.mp4', 'Scanning 3/3: sub/c.MOV'])
            # Same order as before: a folder's files come before its sub-folders.
            self.assertEqual([i['path'] for i in result['items']], ['a.mp4', 'z.mp4', 'sub/c.MOV'])
            self.assertTrue(result['complete'])

    def test_counter_is_aligned_for_larger_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'library'
            root.mkdir()
            for n in range(10):
                (root / f'v{n}.mp4').write_bytes(b'x')
            _, lines = self.scan(root)
            self.assertEqual(lines[1], 'Found 10 video file(s) to scan.')
            self.assertEqual(lines[2], 'Scanning  1/10: v0.mp4')
            self.assertEqual(lines[-1], 'Scanning 10/10: v9.mp4')

    def test_no_videos_is_reported_and_still_a_complete_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'library'
            root.mkdir()
            (root / 'photo.jpg').write_bytes(b'x')
            result, lines = self.scan(root)
            self.assertEqual(lines[1:], ['Found 0 video file(s) to scan.'])
            self.assertEqual((result['complete'], result['items']), (True, []))

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'needs a non-root POSIX user to make a folder unreadable')
    def test_unreadable_folder_still_marks_the_scan_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'library'
            (root / 'locked').mkdir(parents=True)
            (root / 'ok.mp4').write_bytes(b'x')
            (root / 'locked').chmod(0)
            try:
                result, lines = self.scan(root)
            finally:
                (root / 'locked').chmod(0o755)
            self.assertFalse(result['complete'])
            self.assertEqual(len(result['walk_errors']), 1)
            self.assertIn('Found 1 video file(s) to scan.', lines)


def undecodable_audio_copy(good, target):
    """Relabel good's AAC track as a codec ffmpeg has no decoder for, like Apple spatial audio (apac)."""
    data = good.read_bytes()
    assert data.count(b'mp4a') == 1 and data.count(b'esds') >= 1
    target.write_bytes(data.replace(b'mp4a', b'apac').replace(b'esds', b'esdz'))  # same lengths


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg is required')
class UndecodableAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        good = self.base / 'good.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=30:duration=2',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-bf', '0',
                        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(good)], check=True)
        self.library = self.base / 'library'
        self.library.mkdir()
        undecodable_audio_copy(good, self.library / 'iphone.mp4')
        self.good = good

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, file):
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(file)],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def scan(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = catalog_tool.scan(self.library, self.base / 'out')
        return result, out.getvalue()

    def test_fixture_really_has_an_audio_track_ffmpeg_cannot_decode(self):
        audio = self.probe(self.library / 'iphone.mp4')['streams'][1]
        self.assertEqual((audio['codec_type'], audio.get('codec_name'), audio['codec_tag_string']), ('audio', None, 'apac'))
        old = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(self.library / 'iphone.mp4'),
                              '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'], capture_output=True, text=True)
        self.assertIn('no decoder found', old.stderr)  # what made healthy iPhone files look damaged

    def test_command_decodes_what_it_can_and_packet_checks_the_rest(self):
        metadata = {'streams': [
            {'index': 0, 'codec_type': 'video', 'codec_name': 'hevc'},
            {'index': 1, 'codec_type': 'data'},
            {'index': 2, 'codec_type': 'audio', 'codec_name': 'aac'},
            {'index': 3, 'codec_type': 'audio', 'codec_tag_string': 'apac'},
            {'index': 4, 'codec_type': 'audio', 'codec_name': 'unknown', 'codec_tag_string': 'zzzz'},
            {'index': 5, 'codec_type': 'audio', 'codec_name': 'none', 'codec_tag_string': 'yyyy'}]}
        command, packet_only = catalog_tool.decode_command('/x/a.mov', metadata)
        self.assertEqual(command, ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2', '-i', '/x/a.mov',
                                   '-map', '0:0', '-map', '0:2', '-map', '0:3', '-c:2', 'copy',
                                   '-map', '0:4', '-c:3', 'copy', '-map', '0:5', '-c:4', 'copy',
                                   '-f', 'null', '-'])  # positions are output streams
        self.assertEqual([x['index'] for x in packet_only], [3, 4, 5])

    def test_ordinary_files_and_undecodable_video_keep_the_strict_check(self):
        plain = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264'},
                             {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac'}]}
        command, packet_only = catalog_tool.decode_command('a.mp4', plain)
        self.assertNotIn('copy', command)
        self.assertEqual(packet_only, [])
        odd_video = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_tag_string': 'zzzz'}]}
        command, packet_only = catalog_tool.decode_command('a.mp4', odd_video)
        self.assertNotIn('copy', command)  # a picture that cannot be decoded is reported, not waved through
        self.assertEqual(packet_only, [])
        fallback = catalog_tool.decode_command('a.mp4', {})[0]  # no stream information: the original mapping
        self.assertEqual(fallback[8:], ['-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'])

    def test_a_garbled_tag_may_be_damage_so_it_is_still_reported(self):
        data = (self.library / 'iphone.mp4').read_bytes()
        (self.library / 'iphone.mp4').write_bytes(data.replace(b'apac', b'\xc3\x01\x9f\x02'))
        audio = self.probe(self.library / 'iphone.mp4')['streams'][1]
        self.assertEqual((audio.get('codec_name'), audio['codec_tag_string']), (None, '[195][1][159][2]'))
        result, printed = self.scan()
        item = result['items'][0]
        self.assertEqual(item['status'], 'decode-errors')  # cannot be told apart from a damaged header
        self.assertNotIn('packet_checked_streams', item)
        self.assertNotIn('no decoder for (codec tag', printed)

    def test_odd_but_well_formed_tags_are_named_in_the_note(self):
        data = (self.library / 'iphone.mp4').read_bytes()
        (self.library / 'iphone.mp4').write_bytes(data.replace(b'apac', b'Xpac'))
        result, printed = self.scan()
        self.assertEqual(result['items'][0]['status'], 'decode-clean')
        self.assertIn('codec tag: Xpac x1)', printed)  # no explanation invented for an unknown tag
        self.assertNotIn('Apple', printed)
        self.assertIn('An unexpected tag name deserves a closer look', printed)

    def test_note_counts_files_and_tags_without_asserting_a_cause(self):
        note = catalog_tool.packet_only_note([
            {'packet_checked_streams': [{'index': 2, 'codec_tag': 'apac'}]},
            {'packet_checked_streams': [{'index': 2, 'codec_tag': 'apac'}, {'index': 3, 'codec_tag': 'zzzz'}]},
            {}])
        self.assertTrue(note.startswith('2 file(s) contain audio that ffmpeg has no decoder for (codec tag: '))
        self.assertIn('apac x2 = Apple spatial audio, zzzz x1)', note)
        self.assertEqual(catalog_tool.packet_only_note([{}, {'packet_checked_streams': []}]), '')

    def test_healthy_file_with_undecodable_audio_is_clean_but_labelled(self):
        result, printed = self.scan()
        item = result['items'][0]
        self.assertEqual(item['status'], 'decode-clean')
        self.assertEqual(item['packet_checked_streams'], [{'index': 1, 'codec_tag': 'apac'}])
        self.assertEqual(result['rankings'], {})
        self.assertIn('Note: 1 file(s) contain audio that ffmpeg has no decoder for (codec tag: apac x1 = Apple spatial audio)', printed)
        self.assertIn('but not corruption inside that audio', printed)

    def test_truncation_is_still_detected_through_the_packet_check(self):
        data = (self.library / 'iphone.mp4').read_bytes()
        (self.library / 'iphone.mp4').write_bytes(data[:int(len(data) * 0.6)])  # index at the front, data cut
        result, _ = self.scan()
        item = result['items'][0]
        self.assertEqual(item['status'], 'decode-errors')
        log = (self.base / 'out' / item['log']).read_text()
        self.assertIn('stream 1', log)  # the audio track's own packets were reported as partial
        self.assertIn('partial file', log)

    def test_repair_verification_accepts_it_too(self):
        recover = load('recover')
        recover.WORK = self.base / 'work'
        recover.WORK.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            report_data = recover.verify(self.library / 'iphone.mp4', self.base / 'check')
        self.assertEqual(report_data['status'], 'decode-clean-needs-review')
        self.assertEqual(report_data['packet_checked_streams'], [{'index': 1, 'codec_tag': 'apac'}])

    def test_suspect_listing_mentions_packet_only_checks(self):
        catalog = {'complete': True, 'mode': 'full', 'rankings': {}, 'items': [
            {'path': 'a.mov', 'status': 'decode-clean', 'packet_checked_streams': [{'index': 2, 'codec_tag': 'apac'}]},
            {'path': 'b.mov', 'status': 'decode-clean'}]}
        text = '\n'.join(report.render_suspects(catalog, '/c.json', '/src', 3))
        self.assertIn('Note: 1 file(s) contain audio that ffmpeg has no decoder for (codec tag: apac x1 = Apple spatial audio)', text)
        plain = '\n'.join(report.render_suspects(dict(catalog, items=[catalog['items'][1]]), '/c.json', '/src', 3))
        self.assertNotIn('no decoder for', plain)


class HostPathTests(unittest.TestCase):
    """Messages produced inside the container must point at real folders on the host."""

    def setUp(self):
        self.recover = load('recover')
        r = self.recover
        r.INPUT, r.REFS, r.WORK = Path('/input'), Path('/references'), Path('/work')
        r.HOST_CASE, r.HOST_LIBRARY = '/home/u/cases/trip', '/mnt/share/photos/trip'

    def test_container_paths_become_host_paths(self):
        shown = self.recover.shown
        self.assertEqual(shown('/work/20260101-scan-ab/catalog/catalog.json'),
                         '/home/u/cases/trip/work/20260101-scan-ab/catalog/catalog.json')
        self.assertEqual(shown(Path('/work')), '/home/u/cases/trip/work')
        self.assertEqual(shown('/references/a.mp4'), '/home/u/cases/trip/references/a.mp4')
        self.assertEqual(shown('/input/b.mp4'), '/home/u/cases/trip/input/b.mp4')
        self.assertEqual(shown('/library'), '/mnt/share/photos/trip')
        self.assertEqual(shown('/library/day 1/c.mp4'), '/mnt/share/photos/trip/day 1/c.mp4')

    def test_other_paths_and_missing_host_information_are_left_alone(self):
        shown = self.recover.shown
        self.assertEqual(shown('/workshop/x'), '/workshop/x')  # only a real mount, not a shared name prefix
        self.assertEqual(shown('/tmp/x'), '/tmp/x')
        self.recover.HOST_CASE = self.recover.HOST_LIBRARY = ''
        self.assertEqual((shown('/work/a'), shown('/library/b')), ('/work/a', '/library/b'))

    def test_scan_summary_names_the_host_catalog_and_flags_incomplete_scans(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.recover.finish_scan({'items': [{}, {}, {}], 'complete': True, 'walk_errors': []},
                                            Path('/work/20260101-scan-ab/catalog'))
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn('Scan finished: 3 video file(s) checked.', text)
        self.assertIn('Catalog saved to: /home/u/cases/trip/work/20260101-scan-ab/catalog/catalog.json', text)
        self.assertNotIn('/work/2026', text.replace('/home/u/cases/trip/work/2026', ''))  # no bare container path
        self.assertNotIn('INCOMPLETE', text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.recover.finish_scan({'items': [], 'complete': False, 'walk_errors': ['[Errno 13] Permission denied: x']},
                                            Path('/work/s/catalog'))
        self.assertEqual(code, 2)
        self.assertIn('INCOMPLETE', out.getvalue())
        self.assertIn('Permission denied: x', out.getvalue())

    def test_scanner_announces_the_host_folder_not_the_mount_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'library'
            root.mkdir()

            def first_line(outdir):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    catalog_tool.scan(root, Path(tmp) / outdir, 'probe')
                return out.getvalue().splitlines()[0]
            with patch.dict(os.environ, {'SCAN_HOST_ROOT': '/mnt/share/photos/trip'}):
                self.assertEqual(first_line('a'), 'Looking for video files under /mnt/share/photos/trip ...')
            with patch.dict(os.environ):
                os.environ.pop('SCAN_HOST_ROOT', None)
                self.assertEqual(first_line('b'), f'Looking for video files under {root.resolve()} ...')

    def test_compose_passes_the_host_case_folder_into_the_container(self):
        compose = (Path(__file__).parents[2] / 'compose/untrunc/docker-compose.yml').read_text()
        self.assertIn('HOST_CASE_DIR: "${REPAIR_CASE_DIR:-}"', compose)  # what shown() relies on
        self.assertIn('SCAN_HOST_ROOT: "${SCAN_HOST_ROOT:-}"', compose)
        self.assertIn('SCAN_FRESH: "${SCAN_FRESH:-}"', compose)  # --fresh reaches the scanner


def junk_library(root, count):
    """Files that look like videos to the scanner but are not: ffprobe rejects each one in milliseconds."""
    root.mkdir(parents=True, exist_ok=True)
    for n in range(count):
        (root / f'v{n:03d}.mp4').write_bytes(f'not a video {n}'.encode())
    return root


class Interrupt:
    """Stands in for subprocess.run: Ctrl-C on the Nth call, otherwise the real command; counts calls."""

    def __init__(self, at=None):
        self.at, self.calls = at, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.at == self.calls:
            raise KeyboardInterrupt
        return REAL_RUN(*args, **kwargs)


@unittest.skipUnless(shutil.which('ffprobe'), 'ffprobe is required')
class ResumableScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = junk_library(self.base / 'library', 5)
        self.out = self.base / 'scan' / 'catalog'

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, runner=None, resume=False, mode='probe'):
        out = io.StringIO()
        with patch('subprocess.run', runner or Interrupt()), contextlib.redirect_stdout(out):
            try:
                return catalog_tool.scan(self.library, self.out, mode, resume=resume), out.getvalue()
            except KeyboardInterrupt:
                return None, out.getvalue()

    def saved(self):
        return json.loads((self.out / 'catalog.json').read_text())

    def test_interrupt_saves_progress_and_resume_scans_only_the_rest(self):
        report, printed = self.scan(Interrupt(at=3))  # Ctrl-C while probing the third file
        self.assertIsNone(report)
        self.assertIn('Interrupted: 2 of 5 file(s) are saved. Run the same command again to resume.', printed)
        partial = self.saved()
        self.assertEqual([i['path'] for i in partial['items']], ['v000.mp4', 'v001.mp4'])
        self.assertEqual((partial['complete'], partial['mode'], partial['scan_version'], partial['host_source_root']),
                         (False, 'probe', catalog_tool.SCAN_VERSION, str(self.library.resolve())))
        self.assertEqual(list(self.out.glob('*.tmp')), [])  # atomic writes leave no temporary file
        runner = Interrupt()
        report, printed = self.scan(runner, resume=True)
        self.assertEqual(runner.calls, 3)  # ffprobe ran only for the three files not yet done
        self.assertEqual((report['complete'], report['reused']), (True, 2))
        self.assertEqual([i['path'] for i in report['items']], [f'v{n:03d}.mp4' for n in range(5)])
        self.assertIn('Scanning 1/5: v000.mp4 (already scanned)', printed)
        self.assertIn('Scanning 3/5: v002.mp4\n', printed)
        self.assertIn('Resuming:', printed)
        self.assertEqual(self.saved()['complete'], True)

    def test_files_changed_since_the_interruption_are_scanned_again(self):
        self.scan(Interrupt(at=3))
        (self.library / 'v000.mp4').write_bytes(b'changed, and longer than before')
        runner = Interrupt()
        report, _ = self.scan(runner, resume=True)
        self.assertEqual((runner.calls, report['reused']), (4, 1))  # v000 again + the three unfinished files

    def test_files_that_failed_to_scan_are_retried_not_kept(self):
        self.scan(Interrupt(at=3))
        partial = self.saved()
        partial['items'][0].update(status='scan-error', scan_error='timed out')
        partial['items'][0].pop('size')
        (self.out / 'catalog.json').write_text(json.dumps(partial))
        runner = Interrupt()
        report, _ = self.scan(runner, resume=True)
        self.assertEqual((runner.calls, report['reused']), (4, 1))
        self.assertNotIn('scan-error', {i['status'] for i in report['items']})

    def test_resuming_never_loses_results_that_have_not_been_revisited_yet(self):
        self.scan(Interrupt(at=5))  # four files done
        self.assertEqual(len(self.saved()['items']), 4)

        def stop_on_second_file(*args, **kwargs):
            if args and str(args[0]).startswith('Scanning 2/5'):
                raise KeyboardInterrupt
            print(*args, **kwargs)
        out = io.StringIO()
        with patch.object(catalog_tool, 'print', stop_on_second_file, create=True), contextlib.redirect_stdout(out), \
                self.assertRaises(KeyboardInterrupt):
            catalog_tool.scan(self.library, self.out, 'probe', resume=True)
        self.assertEqual([i['path'] for i in self.saved()['items']], [f'v{n:03d}.mp4' for n in range(4)])  # all four kept

    def test_a_stray_temporary_file_from_a_crash_is_harmless(self):
        self.scan(Interrupt(at=3))
        (self.out / 'catalog.json.tmp').write_text('{"half written')
        report, _ = self.scan(resume=True)
        self.assertTrue(report['complete'])
        self.assertEqual(list(self.out.glob('*.tmp')), [])

    def test_interrupt_before_any_file_still_leaves_a_resumable_scan(self):
        report, printed = self.scan(Interrupt(at=1))
        self.assertIsNone(report)
        self.assertIn('Interrupted: 0 of 5', printed)
        self.assertEqual(self.saved()['items'], [])
        self.assertEqual(self.scan(resume=True)[0]['reused'], 0)

    def test_completed_scan_report_records_what_resuming_needs(self):
        report, _ = self.scan()
        self.assertEqual((report['scan_version'], report['reused'], report['mode']), (catalog_tool.SCAN_VERSION, 0, 'probe'))
        self.assertEqual(self.saved()['host_source_root'], str(self.library.resolve()))

    def test_decode_logs_are_named_by_file_so_they_stay_unique_on_resume(self):
        first = catalog_tool.hashlib.sha1(b'a/x.mp4').hexdigest()[:12]
        second = catalog_tool.hashlib.sha1(b'b/x.mp4').hexdigest()[:12]
        self.assertNotEqual(first, second)  # same basename in different folders

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is required')
    def test_full_mode_resume_keeps_decode_logs_and_verdicts(self):
        good = self.base / 'seed.mp4'
        REAL_RUN(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=15:duration=0.2',
                  '-c:v', 'libx264', '-bf', '0', '-pix_fmt', 'yuv420p', str(good)], check=True)
        library = self.base / 'real'
        for name in ('a/x.mp4', 'b/x.mp4', 'c/x.mp4'):
            (library / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(good, library / name)
        self.library = library
        report, _ = self.scan(Interrupt(at=4), mode='full')  # ffprobe, ffmpeg, ffprobe, then Ctrl-C in the 2nd decode
        self.assertIsNone(report)
        kept = self.saved()['items']
        self.assertEqual([(i['path'], i['status']) for i in kept], [('a/x.mp4', 'decode-clean')])
        log_a = kept[0]['log']
        report, _ = self.scan(resume=True, mode='full')
        self.assertEqual(report['reused'], 1)
        logs = {i['path']: i['log'] for i in report['items']}
        self.assertEqual(logs['a/x.mp4'], log_a)  # the kept file's log is untouched
        self.assertEqual(len(set(logs.values())), 3)  # same basename, three different logs
        for name in logs.values():
            self.assertTrue((self.out / name).exists())
        self.assertEqual({i['status'] for i in report['items']}, {'decode-clean'})


class ResumeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'work'
        self.root = Path(self.tmp.name) / 'lib'
        self.root.mkdir()
        self.version = catalog_tool.SCAN_VERSION

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, **fields):
        folder = self.work / name / 'catalog'
        folder.mkdir(parents=True)
        data = {'complete': False, 'scan_version': self.version, 'mode': 'full', 'host_source_root': str(self.root), 'items': []}
        data.update(fields)
        (folder / 'catalog.json').write_text(data if isinstance(data, str) else json.dumps(data))
        return folder

    def decide(self, mode='full'):
        return catalog_tool.resumable_scan(self.work, self.root, mode)

    def test_no_earlier_scan_and_finished_scans_are_not_resumed(self):
        self.assertEqual(self.decide(), (None, ''))
        self.write('20260101-scan-aaaa', complete=True)
        self.assertEqual(self.decide(), (None, ''))

    def test_the_newest_interrupted_matching_scan_is_resumed(self):
        self.write('20260101-scan-aaaa')
        newest = self.write('20260102-scan-bbbb')
        self.assertEqual(self.decide(), (newest, ''))

    def test_only_the_newest_scan_counts(self):
        self.write('20260101-scan-aaaa')  # interrupted long ago
        self.write('20260102-scan-bbbb', complete=True)  # a later scan finished
        self.assertEqual(self.decide(), (None, ''))

    def test_mismatches_start_a_new_scan_and_say_why(self):
        self.write('20260101-scan-aaaa', mode='probe')
        self.assertIn('used mode "probe", not "full"', self.decide('full')[1])
        self.assertIsNotNone(self.decide('probe')[0])
        self.write('20260102-scan-bbbb', host_source_root='/somewhere/else')
        self.assertIn('different folder (/somewhere/else)', self.decide()[1])
        self.write('20260103-scan-cccc', scan_version=self.version - 1)
        self.assertIn('different version of the scanner', self.decide()[1])
        older = self.write('20260104-scan-dddd')
        (older / 'catalog.json').write_text('{"half written')
        self.assertIn('unreadable catalog', self.decide()[1])
        old_format = self.write('20260105-scan-eeee')
        (old_format / 'catalog.json').write_text(json.dumps({'complete': False, 'mode': 'full', 'items': []}))  # no version
        self.assertIn('different version of the scanner', self.decide()[1])


class RunScanTests(unittest.TestCase):
    """recover.py's scan entry point, in-process: which folder it uses and what it tells the user."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.recover = load('recover')
        r = self.recover
        r.WORK, r.LIBRARY = base / 'work', junk_library(base / 'library', 5)
        r.HOST_CASE, r.HOST_LIBRARY = '', ''
        r.WORK.mkdir()
        patcher = patch.dict(os.environ, {'SCAN_MODE': 'probe'})
        patcher.start()
        os.environ.pop('SCAN_FRESH', None)
        os.environ.pop('SCAN_HOST_ROOT', None)
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_scan(self, runner=None):
        out = io.StringIO()
        with patch('subprocess.run', runner or Interrupt()), contextlib.redirect_stdout(out):
            return self.recover.run_scan(), out.getvalue()

    def scans(self):
        return sorted(self.recover.WORK.glob('*-scan-*'))

    def test_interrupt_returns_130_and_the_next_run_continues_the_same_scan(self):
        code, printed = self.run_scan(Interrupt(at=3))
        self.assertEqual(code, 130)
        self.assertEqual(len(self.scans()), 1)
        before = self.scans()[0]
        code, printed = self.run_scan()
        self.assertEqual(code, 0)
        self.assertIn(f'Resuming the interrupted scan saved in {before / "catalog"}', printed)
        self.assertIn('Scan finished: 5 video file(s) checked (2 kept from the interrupted scan).', printed)
        self.assertEqual(self.scans(), [before])  # no second scan folder
        self.assertTrue(json.loads((before / 'catalog/catalog.json').read_text())['complete'])

    def test_after_a_finished_scan_the_next_run_starts_a_new_one(self):
        self.assertEqual(self.run_scan()[0], 0)
        code, printed = self.run_scan()
        self.assertEqual(code, 0)
        self.assertNotIn('Resuming', printed)
        self.assertEqual(len(self.scans()), 2)

    def test_fresh_flag_ignores_an_interrupted_scan(self):
        self.run_scan(Interrupt(at=3))
        with patch.dict(os.environ, {'SCAN_FRESH': '1'}):
            code, printed = self.run_scan()
        self.assertEqual(code, 0)
        self.assertNotIn('Resuming', printed)
        self.assertEqual(len(self.scans()), 2)

    def test_a_different_mode_is_not_resumed_and_the_user_is_told(self):
        self.run_scan(Interrupt(at=3))
        with patch.dict(os.environ, {'SCAN_MODE': 'full'}):
            code, printed = self.run_scan()
        self.assertIn('Not resuming an earlier scan: the interrupted scan used mode "probe", not "full". Starting a new scan.', printed)
        self.assertEqual(len(self.scans()), 2)

    def test_sigterm_handler_is_restored_after_the_scan(self):
        before = signal.getsignal(signal.SIGTERM)
        self.run_scan()
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_summary_mentions_kept_files_only_when_there_are_some(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.recover.finish_scan({'items': [{}], 'complete': True, 'walk_errors': []}, Path('/work/s/catalog'))
        self.assertIn('Scan finished: 1 video file(s) checked.', out.getvalue())
        self.assertNotIn('kept', out.getvalue())


@unittest.skipUnless(os.name == 'posix' and shutil.which('ffprobe'), 'needs POSIX signals and ffprobe')
class RealSignalTests(unittest.TestCase):
    """The real recover.py process, interrupted the way Ctrl-C and `docker stop` do it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = junk_library(self.base / 'library', 40)
        (self.base / 'work').mkdir()
        self.env = dict(os.environ, REPAIR_WORK=str(self.base / 'work'), REPAIR_LIBRARY=str(self.library),
                        REPAIR_INPUT=str(self.base / 'input'), REPAIR_REFERENCES=str(self.base / 'refs'),
                        SCAN_MODE='probe', HOST_CASE_DIR='', SCAN_HOST_ROOT='', PYTHONUNBUFFERED='1')
        self.env.pop('SCAN_FRESH', None)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        return subprocess.Popen([sys.executable, str(SCRIPTS / 'recover.py'), 'scan'], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def interrupt_after(self, process, marker, sig):
        lines = []
        for line in process.stdout:
            lines.append(line)
            if marker in line:
                process.send_signal(sig)
                break
        rest, _ = process.communicate(timeout=30)
        return ''.join(lines) + rest

    def catalogs(self):
        return sorted((self.base / 'work').glob('*-scan-*/catalog/catalog.json'))

    def test_sigterm_saves_progress_and_a_second_run_finishes_the_same_scan(self):
        process = self.start()
        printed = self.interrupt_after(process, 'Scanning  8/40', signal.SIGTERM)
        self.assertEqual(process.returncode, 130, printed)
        self.assertIn('Interrupted:', printed)
        (catalog_file,) = self.catalogs()
        partial = json.loads(catalog_file.read_text())
        self.assertFalse(partial['complete'])
        self.assertGreaterEqual(len(partial['items']), 7)  # the 8th was announced, not finished
        second = subprocess.run([sys.executable, str(SCRIPTS / 'recover.py'), 'scan'], env=self.env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn('Resuming the interrupted scan', second.stdout)
        self.assertEqual(self.catalogs(), [catalog_file])
        final = json.loads(catalog_file.read_text())
        self.assertEqual((final['complete'], len(final['items'])), (True, 40))
        self.assertGreaterEqual(final['reused'], 7)

    def test_sigint_behaves_like_ctrl_c(self):
        process = self.start()
        printed = self.interrupt_after(process, 'Scanning  8/40', signal.SIGINT)
        self.assertEqual(process.returncode, 130, printed)
        self.assertNotIn('Traceback', printed)
        self.assertIn('Run the same command again to resume.', printed)
        self.assertFalse(json.loads(self.catalogs()[0].read_text())['complete'])


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

    def fake_docker(self, exit_code):
        bin_dir = self.base / f'bin{exit_code}'
        bin_dir.mkdir()
        fake = bin_dir / 'docker'
        fake.write_text(f'#!/bin/sh\nexit {exit_code}\n')
        fake.chmod(0o755)
        return bin_dir

    def test_successful_scan_says_what_to_run_next_with_the_full_config_path(self):
        result = self.cli('scan', '--config', str(self.config), path_prefix=str(self.fake_docker(0)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'make untrunc-case-suspects REPAIR_CONFIG={self.config.resolve()}', result.stdout)

    def test_config_path_with_spaces_is_quoted_in_the_hint(self):
        spaced = self.base / 'my configs'
        spaced.mkdir()
        target = spaced / 'case file.json'
        target.write_text(self.config.read_text())
        result = self.cli('scan', '--config', str(target), path_prefix=str(self.fake_docker(0)))
        self.assertIn(f"REPAIR_CONFIG='{target}'", result.stdout)

    def test_failed_scan_does_not_suggest_the_next_step(self):
        result = self.cli('scan', '--config', str(self.config), path_prefix=str(self.fake_docker(1)))
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('untrunc-case-suspects', result.stdout)
        self.assertIn('permission denied', result.stderr)

    def env_recording_docker(self, exit_code=0):
        bin_dir = self.base / f'envbin{exit_code}'
        bin_dir.mkdir()
        fake = bin_dir / 'docker'
        fake.write_text('#!/bin/sh\necho "SCAN_FRESH=[$SCAN_FRESH]" >> "$FAKE_LOG"\n' f'exit {exit_code}\n')
        fake.chmod(0o755)
        return bin_dir

    def test_fresh_flag_reaches_the_container_only_when_asked(self):
        log = self.base / 'fresh.log'
        bin_dir = self.env_recording_docker()
        self.assertEqual(self.cli('scan', '--config', str(self.config), path_prefix=str(bin_dir), FAKE_LOG=str(log)).returncode, 0)
        self.assertEqual(self.cli('scan', '--fresh', '--config', str(self.config), path_prefix=str(bin_dir), FAKE_LOG=str(log)).returncode, 0)
        self.assertEqual(log.read_text().splitlines(), ['SCAN_FRESH=[]', 'SCAN_FRESH=[1]'])
        stray = self.cli('scan', '--config', str(self.config), path_prefix=str(bin_dir), FAKE_LOG=str(log), SCAN_FRESH='1')
        self.assertEqual(log.read_text().splitlines()[-1], 'SCAN_FRESH=[]')  # a stray variable in your shell is ignored

    def test_fresh_flag_is_only_for_scan(self):
        result = self.cli('batch', '--config', str(self.config), '--fresh')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--fresh only applies to scan', result.stderr)

    def test_interrupted_scan_explains_how_to_resume_and_is_not_blamed_on_docker(self):
        result = self.cli('scan', '--config', str(self.config), path_prefix=str(self.fake_docker(130)))
        self.assertEqual(result.returncode, 130)
        self.assertIn('Run the same command again to resume it (add --fresh to start over)', result.stderr)
        self.assertNotIn('If Docker reported', result.stderr)
        self.assertNotIn('untrunc-case-suspects', result.stdout)

    def test_incomplete_scan_exit_status_is_not_blamed_on_docker_either(self):
        result = self.cli('scan', '--config', str(self.config), path_prefix=str(self.fake_docker(2)))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('If Docker reported', result.stderr)

    def test_batch_command_reports_a_missing_docker_clearly(self):
        empty = self.base / 'empty-bin'
        empty.mkdir()
        result = self.cli('batch', '--config', str(self.config), PATH=str(empty))
        self.assertEqual(result.returncode, 1)
        self.assertIn('docker was not found in PATH', result.stderr)


if __name__ == '__main__':
    unittest.main()
