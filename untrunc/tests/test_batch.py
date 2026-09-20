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
report_module = report  # tests below use `report` for scan results


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
        self.assertIn('2 decoder message(s); first: [h264 @ 0x2] error while decoding MB 3 4', text)  # counted from the log
        self.assertIn('match: codec_name | differ: model | same directory', text)
        self.assertIn('[heuristic-only]', text)
        self.assertIn('no decode-verified match found', text)
        self.assertIn('make untrunc-case-batch', text)
        self.assertIn('Suspects (3): 1 truncated · 1 unreadable · 1 other', text)
        self.assertIn('== truncated (1): the recording was cut off and its index (moov atom) is missing', text)
        self.assertIn('== unreadable (1): could not be opened, for a reason other than a missing index', text)
        self.assertEqual(text.count('== truncated'), 1)  # each kind is explained once, not per file

    def test_only_the_top_matches_are_marked_as_used(self):
        lines = self.render(top=1).splitlines()
        marked = [l for l in lines if l.lstrip().startswith('*') and not l.startswith('* marks')]
        self.assertEqual(len(marked), 2)  # first match of cut.mp4 and of glitch.mp4
        self.assertTrue(any('b.mp4' in l and not l.lstrip().startswith('*') for l in lines))
        self.assertIn('max_references = 1', self.render(top=1))

    def test_missing_log_and_probe_text_degrade_gracefully(self):
        text = self.render()  # decode log file absent
        self.assertIn('7 decoder message(s); the log could not be read', text)
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
                      orphan=('lonely.mp4',), kinds=None, **overrides):
        items, rankings = [], {}
        ranked = [match(g, 200 - n) for n, g in enumerate(good)]
        for names, status in ((good, 'decode-clean'), (bad, 'decode-errors'), (orphan, 'unreadable-or-no-video')):
            for name in names:
                file = self.src / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(name.encode() * 20)
                st = file.stat()
                items.append({'path': name, 'status': status, 'size': st.st_size, 'mtime_ns': st.st_mtime_ns})
                if kinds and name in kinds:
                    items[-1]['kind'] = kinds[name]
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
    """Files that look like videos to the scanner but are not: ffprobe rejects each one, so each is a suspect."""
    root.mkdir(parents=True, exist_ok=True)
    for n in range(count):
        (root / f'v{n:03d}.mp4').write_bytes(f'not a video {n}'.encode())
    return root


_SEED = []


def seed_video():
    """Bytes of one tiny healthy video, made once."""
    if not _SEED:
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'seed.mp4'
            REAL_RUN(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=15:duration=0.2',
                      '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', '-c:v', 'libx264', '-bf', '0',
                      '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(file)], check=True)
            _SEED.append(file.read_bytes())
    return _SEED[0]


def tiny_library(root, count):
    """Healthy videos (copies of one tiny clip): clean in probe and in full mode."""
    root.mkdir(parents=True, exist_ok=True)
    for n in range(count):
        (root / f'v{n:03d}.mp4').write_bytes(seed_video())
    return root


def truncated(data):
    return data[:data.index(b'moov') - 4]  # cut before the index: unreadable, like an interrupted recording


class Interrupt:
    """Stands in for subprocess.run: Ctrl-C on the Nth call, otherwise the real command; counts calls."""

    def __init__(self, at=None):
        self.at, self.calls = at, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.at == self.calls:
            raise KeyboardInterrupt
        return REAL_RUN(*args, **kwargs)


def distinct_scan_names():
    """recover.attempt() names scan folders by the second: give each scan in a test its own increasing stamp."""
    counter = itertools.count(1)
    return patch('time.strftime', lambda fmt, *args: f'20260101T{next(counter):06d}')


needs_ffmpeg = unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg is required')


@needs_ffmpeg
class ResumableScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = tiny_library(self.base / 'library', 5)
        self.out = self.base / 'scan' / 'catalog'

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, runner=None, resume=False, mode='probe', **kwargs):
        out = io.StringIO()
        with patch('subprocess.run', runner or Interrupt()), contextlib.redirect_stdout(out):
            try:
                return catalog_tool.scan(self.library, self.out, mode, resume=resume, **kwargs), out.getvalue()
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
        self.assertIn('Scanning 1/5: v000.mp4 (unchanged, skipped)', printed)
        self.assertIn('Scanning 3/5: v002.mp4\n', printed)
        self.assertEqual(self.saved()['complete'], True)

    def test_files_changed_since_the_interruption_are_scanned_again(self):
        self.scan(Interrupt(at=3))
        (self.library / 'v000.mp4').write_bytes(seed_video() + b'appended')
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

    def test_completed_scan_report_records_what_reuse_needs(self):
        report, _ = self.scan()
        self.assertEqual((report['scan_version'], report['reused'], report['dropped'], report['content_changed']),
                         (catalog_tool.SCAN_VERSION, 0, 0, []))
        self.assertEqual(self.saved()['host_source_root'], str(self.library.resolve()))

    def test_flagged_files_are_checked_again_on_resume_because_a_verdict_may_have_been_a_hiccup(self):
        self.library = junk_library(self.base / 'junk', 5)  # every file is a suspect
        self.scan(Interrupt(at=4))
        self.assertEqual(len(self.saved()['items']), 3)
        runner = Interrupt()
        report, printed = self.scan(runner, resume=True)
        self.assertEqual((runner.calls, report['reused']), (5, 0))  # all five, including the three already flagged
        self.assertNotIn('(unchanged, skipped)', printed)

    def test_decode_logs_are_named_by_file_so_they_stay_unique_on_resume(self):
        first = catalog_tool.hashlib.sha1(b'a/x.mp4').hexdigest()[:12]
        second = catalog_tool.hashlib.sha1(b'b/x.mp4').hexdigest()[:12]
        self.assertNotEqual(first, second)  # same basename in different folders

    def test_full_mode_resume_keeps_decode_logs_hashes_and_verdicts(self):
        library = self.base / 'real'
        for name in ('a/x.mp4', 'b/x.mp4', 'c/x.mp4'):
            (library / name).parent.mkdir(parents=True, exist_ok=True)
            (library / name).write_bytes(seed_video())
        self.library = library
        report, _ = self.scan(Interrupt(at=4), mode='full')  # ffprobe, ffmpeg, ffprobe, then Ctrl-C in the 2nd decode
        self.assertIsNone(report)
        kept = self.saved()['items']
        self.assertEqual([(i['path'], i['status']) for i in kept], [('a/x.mp4', 'decode-clean')])
        log_a, hash_a = kept[0]['log'], kept[0]['stream_hash']
        report, _ = self.scan(resume=True, mode='full')
        self.assertEqual(report['reused'], 1)
        by_path = {i['path']: i for i in report['items']}
        self.assertEqual((by_path['a/x.mp4']['log'], by_path['a/x.mp4']['stream_hash']), (log_a, hash_a))  # untouched
        self.assertEqual(len({i['log'] for i in report['items']}), 3)  # same basename, three different logs
        for item in report['items']:
            self.assertTrue((self.out / item['log']).exists())
        self.assertEqual({i['status'] for i in report['items']}, {'decode-clean'})


class ReuseRuleTests(unittest.TestCase):
    def stat(self, size=10, mtime_ns=5):
        return type('S', (), {'st_size': size, 'st_mtime_ns': mtime_ns})()

    def item(self, **fields):
        return dict({'status': 'decode-clean', 'size': 10, 'mtime_ns': 5, 'stream_hash': {'algorithm': 'sha256', 'value': 'ab'}}, **fields)

    def test_only_clean_unchanged_files_are_reused(self):
        reusable = catalog_tool.reusable
        self.assertTrue(reusable(self.item(), self.stat(), 'full'))
        self.assertTrue(reusable(self.item(status='probe-only-unverified', stream_hash=None), self.stat(), 'probe'))
        for status in ('decode-errors', 'unreadable-or-no-video', 'scan-error', 'something-else'):
            self.assertFalse(reusable(self.item(status=status), self.stat(), 'full'), status)
        self.assertFalse(reusable(self.item(), self.stat(size=11), 'full'))
        self.assertFalse(reusable(self.item(), self.stat(mtime_ns=6), 'full'))
        self.assertFalse(reusable(self.item(), None, 'full'))  # the file vanished or cannot be read
        self.assertFalse(reusable(None, self.stat(), 'full'))

    def test_a_full_result_without_its_hash_is_not_reused(self):
        self.assertFalse(catalog_tool.reusable(self.item(stream_hash=None), self.stat(), 'full'))
        self.assertFalse(catalog_tool.reusable(self.item(stream_hash=None), self.stat(), 'full'))


class PlanScanTests(unittest.TestCase):
    HASHED = {'stream_hash': {'algorithm': 'sha256', 'value': 'aa'}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / 'work'
        self.root = Path(self.tmp.name) / 'lib'
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, items=(), **fields):
        folder = self.work / name / 'catalog'
        folder.mkdir(parents=True)
        data = {'complete': False, 'scan_version': catalog_tool.SCAN_VERSION, 'mode': 'full',
                'host_source_root': str(self.root), 'items': [dict(i) for i in items]}
        data.update(fields)
        (folder / 'catalog.json').write_text(json.dumps(data))
        return folder

    def plan(self, mode='full', fresh=False):
        return catalog_tool.plan_scan(self.work, self.root, mode, fresh)

    def test_no_earlier_scan_means_a_new_scan(self):
        plan = self.plan()
        self.assertEqual((plan['kind'], plan['reuse'], plan['baseline'], plan['reason']), ('new', {}, {}, ''))

    def test_a_complete_scan_is_built_on_in_a_new_folder(self):
        self.write('20260101-scan-aaaa', [dict(self.HASHED, path='a.mp4')], complete=True)
        plan = self.plan()
        self.assertEqual((plan['kind'], plan['folder'], plan['source']), ('incremental', None, '20260101-scan-aaaa'))
        self.assertEqual(list(plan['reuse']), ['a.mp4'])
        self.assertEqual(list(plan['baseline']), ['a.mp4'])
        self.assertEqual(plan['baseline_source'], '20260101-scan-aaaa')

    def test_the_newest_interrupted_scan_is_resumed_in_place(self):
        self.write('20260101-scan-aaaa', [dict(self.HASHED, path='old.mp4')], complete=True)
        newest = self.write('20260102-scan-bbbb', [{'path': 'new.mp4'}])
        plan = self.plan()
        self.assertEqual((plan['kind'], plan['folder']), ('resume', newest))
        self.assertEqual(list(plan['reuse']), ['new.mp4'])  # what is reused comes from the interrupted scan
        self.assertEqual(list(plan['baseline']), ['old.mp4'])  # what hashes are compared with is the last complete one

    def test_only_the_newest_scan_decides(self):
        self.write('20260101-scan-aaaa')  # interrupted long ago
        self.write('20260102-scan-bbbb', [{'path': 'b.mp4'}], complete=True)  # a later scan finished
        plan = self.plan()
        self.assertEqual((plan['kind'], plan['source']), ('incremental', '20260102-scan-bbbb'))

    def test_fresh_reuses_nothing_but_still_compares_with_the_last_complete_scan(self):
        self.write('20260101-scan-aaaa', [dict(self.HASHED, path='a.mp4')], complete=True)
        plan = self.plan(fresh=True)
        self.assertEqual((plan['kind'], plan['reuse'], plan['reason']), ('new', {}, ''))
        self.assertEqual(list(plan['baseline']), ['a.mp4'])

    def test_mismatches_start_a_new_scan_say_why_and_never_reuse(self):
        self.write('20260101-scan-aaaa', mode='probe', complete=True)
        plan = self.plan('full')
        self.assertEqual((plan['kind'], plan['reuse'], plan['baseline']), ('new', {}, {}))
        self.assertIn('the last scan used mode "probe", not "full"', plan['reason'])
        self.write('20260102-scan-bbbb', host_source_root='/somewhere/else')
        self.assertIn('the interrupted scan was of a different folder (/somewhere/else)', self.plan()['reason'])
        self.write('20260103-scan-cccc', scan_version=catalog_tool.SCAN_VERSION - 1, complete=True)
        self.assertIn('the last scan was made by a different version of the scanner', self.plan()['reason'])
        old_format = self.write('20260104-scan-dddd')
        (old_format / 'catalog.json').write_text(json.dumps({'complete': False, 'mode': 'full', 'items': []}))
        self.assertIn('different version of the scanner', self.plan()['reason'])
        broken = self.write('20260105-scan-eeee')
        (broken / 'catalog.json').write_text('{"half written')
        plan = self.plan()
        self.assertIn('unreadable catalog', plan['reason'])
        self.assertEqual(plan['kind'], 'new')

    def test_the_hash_baseline_is_the_newest_complete_scan_that_recorded_hashes(self):
        self.write('20260101-scan-aaaa', [dict(self.HASHED, path='a.mp4')], complete=True)
        # Newer, complete, but made before hashes existed: incompatible for reuse and useless for comparing.
        self.write('20260102-scan-bbbb', [{'path': 'a.mp4'}], scan_version=catalog_tool.SCAN_VERSION - 1, complete=True)
        plan = self.plan()
        self.assertEqual(plan['kind'], 'new')
        self.assertEqual(plan['baseline_source'], '20260101-scan-aaaa')
        self.assertEqual(list(plan['baseline']), ['a.mp4'])

    def test_probe_scans_need_no_hashes_to_be_a_baseline(self):
        self.write('20260101-scan-aaaa', [{'path': 'a.mp4'}], mode='probe', complete=True)
        self.assertEqual(self.plan('probe')['baseline_source'], '20260101-scan-aaaa')
        self.assertEqual(self.plan('full')['baseline_source'], '')


@needs_ffmpeg
class IncrementalScanTests(unittest.TestCase):
    """Rescanning a folder after files were added, changed or deleted, with real tiny videos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.recover = load('recover')
        r = self.recover
        r.WORK, r.LIBRARY = self.base / 'work', self.base / 'library'
        r.HOST_CASE, r.HOST_LIBRARY = '', ''
        r.WORK.mkdir()
        self.library = r.LIBRARY
        self.library.mkdir()
        self.env = patch.dict(os.environ, {'SCAN_MODE': 'full'})
        self.env.start()
        stamps = distinct_scan_names()
        stamps.start()
        self.addCleanup(stamps.stop)
        os.environ.pop('SCAN_FRESH', None)
        os.environ.pop('SCAN_HOST_ROOT', None)
        self.addCleanup(self.env.stop)
        for name in ('a.mp4', 'b.mp4'):
            (self.library / name).write_bytes(seed_video())
        (self.library / 'cut.mp4').write_bytes(truncated(seed_video()))

    def tearDown(self):
        self.tmp.cleanup()

    def run_scan(self, fresh=False):
        runner, out = Interrupt(), io.StringIO()
        with patch.dict(os.environ, {'SCAN_FRESH': '1'} if fresh else {}), patch('subprocess.run', runner), \
                contextlib.redirect_stdout(out):
            code = self.recover.run_scan()
        return code, out.getvalue(), runner.calls

    def newest(self):
        return json.loads(sorted(self.recover.WORK.glob('*-scan-*/catalog/catalog.json'))[-1].read_text())

    def test_unchanged_clean_files_are_skipped_and_suspects_are_checked_again(self):
        self.assertEqual(self.run_scan()[0], 0)
        code, printed, calls = self.run_scan()
        self.assertEqual(code, 0)
        self.assertIn('Building on the last complete scan', printed)
        self.assertIn('Scanning 1/3: a.mp4 (unchanged, skipped)', printed)
        self.assertIn('Scanning 2/3: b.mp4 (unchanged, skipped)', printed)
        self.assertIn('Scanning 3/3: cut.mp4\n', printed)  # a suspect is never skipped
        self.assertIn('Scan finished: 3 video file(s) checked (2 unchanged and skipped).', printed)
        self.assertEqual(calls, 1)  # only ffprobe of the truncated file: no decoding of the two clean ones
        self.assertEqual(len(list(self.recover.WORK.glob('*-scan-*'))), 2)  # each complete scan stays as its own record

    def test_new_changed_and_deleted_files_are_handled_and_rankings_use_the_new_files(self):
        self.run_scan()
        first = self.newest()
        self.assertEqual([r['path'] for r in first['rankings']['cut.mp4']], ['a.mp4', 'b.mp4'])
        (self.library / 'b.mp4').unlink()                                   # deleted
        (self.library / 'a.mp4').write_bytes(seed_video() + b'more bytes')  # changed
        (self.library / 'c.mp4').write_bytes(seed_video())                  # new
        code, printed, calls = self.run_scan()
        self.assertEqual(code, 0)
        report = self.newest()
        self.assertEqual([i['path'] for i in report['items']], ['a.mp4', 'c.mp4', 'cut.mp4'])
        self.assertEqual((report['reused'], report['dropped']), (0, 1))
        self.assertIn('1 file(s) from the earlier scan no longer exist and were dropped.', printed)
        self.assertNotIn('unchanged, skipped', printed)
        self.assertEqual({r['path'] for r in report['rankings']['cut.mp4']}, {'a.mp4', 'c.mp4'})  # the new clip is a candidate

    def test_fresh_scans_everything_again(self):
        self.run_scan()
        code, printed, calls = self.run_scan(fresh=True)
        self.assertEqual(code, 0)
        self.assertIn('Scanning everything again (--fresh); content hashes are compared with the last complete scan', printed)
        self.assertNotIn('skipped', printed)
        self.assertEqual(calls, 2 * 2 + 1)  # ffprobe + ffmpeg for each healthy file, ffprobe for the truncated one

    def test_a_different_mode_never_reuses_the_last_scan(self):
        self.run_scan()
        with patch.dict(os.environ, {'SCAN_MODE': 'probe'}):
            code, printed, calls = self.run_scan()
        self.assertIn('Not reusing an earlier scan: the last scan used mode "full", not "probe". Scanning everything.', printed)
        self.assertEqual(calls, 3)  # ffprobe for every file
        self.assertNotIn('skipped', printed)


@needs_ffmpeg
class ContentHashTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = self.base / 'library'
        self.library.mkdir()
        (self.library / 'a.mp4').write_bytes(seed_video())
        (self.library / 'copy.mp4').write_bytes(seed_video())
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, **kwargs):
        self.n += 1
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report = catalog_tool.scan(self.library, self.base / f'scan{self.n}', 'full', **kwargs)
        return report, out.getvalue()

    def flip_middle_byte_keeping_size_and_date(self, name):
        file = self.library / name
        stat = file.stat()
        data = bytearray(file.read_bytes())
        data[data.index(b'mdat') + 200] ^= 0xFF
        file.write_bytes(bytes(data))
        os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_every_full_result_carries_a_hash_computed_in_the_same_pass(self):
        report, _ = self.scan()
        hashes = {i['path']: i['stream_hash'] for i in report['items']}
        for value in hashes.values():
            self.assertEqual(value['algorithm'], 'sha256')
            self.assertRegex(value['value'], r'^[0-9a-f]{64}$')
        self.assertEqual(hashes['a.mp4'], hashes['copy.mp4'])  # same content, same fingerprint
        probe = catalog_tool.scan(self.library, self.base / 'probe', 'probe')
        self.assertTrue(all('stream_hash' not in i for i in probe['items']))  # probe mode never reads whole files

    def test_hash_covers_the_media_and_changes_when_it_does(self):
        before, _ = self.scan()
        self.flip_middle_byte_keeping_size_and_date('a.mp4')
        after, _ = self.scan()
        old = {i['path']: i['stream_hash'] for i in before['items']}
        new = {i['path']: i['stream_hash'] for i in after['items']}
        self.assertNotEqual(old['a.mp4'], new['a.mp4'])
        self.assertEqual(old['copy.mp4'], new['copy.mp4'])

    def test_silent_change_with_same_size_and_date_is_reported_on_a_rescan(self):
        first, _ = self.scan()
        baseline = {i['path']: i for i in first['items']}
        self.flip_middle_byte_keeping_size_and_date('a.mp4')
        report, printed = self.scan(baseline=baseline)
        self.assertEqual([c['path'] for c in report['content_changed']], ['a.mp4'])
        self.assertEqual(report['content_changed'][0]['previous_hash'], baseline['a.mp4']['stream_hash']['value'])
        changed = next(i for i in report['items'] if i['path'] == 'a.mp4')
        self.assertEqual(changed['content_changed']['previous_hash'], baseline['a.mp4']['stream_hash']['value'])
        self.assertIn('WARNING: a.mp4 has different content than in the previous scan although its size and date are unchanged.', printed)
        self.assertIn('WARNING: 1 file(s) changed content without changing size or date', printed)
        self.assertNotIn('copy.mp4 has different', printed)  # the untouched copy raises no alarm
        text = '\n'.join(report_module.render_suspects(report, '/c.json', '/src', 3))
        self.assertIn('Warning: 1 file(s) changed content without changing size or date since the previous scan', text)
        self.assertIn('a.mp4', text.split('Warning: 1 file(s) changed content')[1])

    def test_an_ordinary_edit_or_a_different_algorithm_raises_no_alarm(self):
        first, _ = self.scan()
        baseline = {i['path']: i for i in first['items']}
        # A legitimate edit changes the size (or the date): not silent, so not reported.
        (self.library / 'a.mp4').write_bytes(seed_video() + b'appended')
        report, printed = self.scan(baseline=baseline)
        self.assertEqual(report['content_changed'], [])
        self.assertNotIn('WARNING', printed)
        # An earlier hash made with another algorithm cannot be compared.
        odd = {p: dict(i, stream_hash={'algorithm': 'md5', 'value': '00'}) for p, i in baseline.items()}
        self.flip_middle_byte_keeping_size_and_date('copy.mp4')
        report, printed = self.scan(baseline=odd)
        self.assertEqual(report['content_changed'], [])

    def test_the_alarm_persists_while_the_flagged_clean_file_is_reused(self):
        first, _ = self.scan()
        baseline = {i['path']: i for i in first['items']}
        self.flip_middle_byte_keeping_size_and_date('a.mp4')
        second, _ = self.scan(baseline=baseline)
        flagged = next(i for i in second['items'] if i['path'] == 'a.mp4')
        if flagged['status'] in catalog_tool.CLEAN_STATUSES:
            third, _ = self.scan(reuse={i['path']: i for i in second['items']})
            self.assertEqual([c['path'] for c in third['content_changed']], ['a.mp4'])  # not silently forgotten

    def test_command_and_parser(self):
        metadata = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264'},
                                {'index': 1, 'codec_type': 'audio', 'codec_tag_string': 'apac'}]}
        plain, _ = catalog_tool.decode_command('a.mp4', metadata)
        hashed, packet_only = catalog_tool.decode_command('a.mp4', metadata, with_hash=True)
        self.assertNotIn('hash', plain)
        self.assertEqual(hashed[:len(plain)], plain)  # the decode output is unchanged
        self.assertEqual(hashed[len(plain):], ['-map', '0:0', '-map', '0:1', '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-'])
        self.assertEqual(packet_only, [{'index': 1, 'codec_tag': 'apac'}])
        fallback, _ = catalog_tool.decode_command('a.mp4', {}, with_hash=True)  # no stream information
        self.assertEqual(fallback[8:], ['-map', '0:v?', '-map', '0:a?', '-f', 'null', '-',
                                        '-map', '0:v?', '-map', '0:a?', '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-'])
        parse = catalog_tool.parse_hash
        self.assertEqual(parse('noise\nSHA256=ABCDEF0123\n'), 'abcdef0123')
        self.assertEqual(parse('sha256=00ff'), '00ff')
        self.assertIsNone(parse('MD5=abcd'))
        self.assertIsNone(parse(''))
        self.assertIsNone(parse(None))

    def test_a_file_with_undecodable_audio_still_gets_its_hash_and_no_false_alarm(self):
        good = self.base / 'good.mp4'
        good.write_bytes(seed_video())
        undecodable_audio_copy(good, self.library / 'iphone.mp4')
        report, _ = self.scan()
        item = next(i for i in report['items'] if i['path'] == 'iphone.mp4')
        self.assertEqual(item['status'], 'decode-clean')
        self.assertRegex(item['stream_hash']['value'], r'^[0-9a-f]{64}$')
        self.assertTrue(item['packet_checked_streams'])


@needs_ffmpeg
class RunScanTests(unittest.TestCase):
    """recover.py's scan entry point, in-process: which folder it uses and what it tells the user."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.recover = load('recover')
        r = self.recover
        r.WORK, r.LIBRARY = base / 'work', tiny_library(base / 'library', 5)
        r.HOST_CASE, r.HOST_LIBRARY = '', ''
        r.WORK.mkdir()
        patcher = patch.dict(os.environ, {'SCAN_MODE': 'probe'})
        patcher.start()
        os.environ.pop('SCAN_FRESH', None)
        os.environ.pop('SCAN_HOST_ROOT', None)
        self.addCleanup(patcher.stop)
        stamps = distinct_scan_names()
        stamps.start()
        self.addCleanup(stamps.stop)

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
        self.assertIn('Scan finished: 5 video file(s) checked (2 unchanged and skipped).', printed)
        self.assertEqual(self.scans(), [before])  # no second scan folder
        self.assertTrue(json.loads((before / 'catalog/catalog.json').read_text())['complete'])

    def test_after_a_finished_scan_the_next_run_builds_on_it_in_a_new_folder(self):
        self.assertEqual(self.run_scan()[0], 0)
        code, printed = self.run_scan()
        self.assertEqual(code, 0)
        self.assertIn('Building on the last complete scan', printed)
        self.assertIn('Scan finished: 5 video file(s) checked (5 unchanged and skipped).', printed)
        self.assertEqual(len(self.scans()), 2)

    def test_fresh_flag_ignores_an_interrupted_scan(self):
        self.run_scan(Interrupt(at=3))
        with patch.dict(os.environ, {'SCAN_FRESH': '1'}):
            code, printed = self.run_scan()
        self.assertEqual(code, 0)
        self.assertNotIn('Resuming', printed)
        self.assertIn('Scanning everything again (--fresh).', printed)  # no complete scan to compare with
        self.assertEqual(len(self.scans()), 2)

    def test_a_different_mode_is_not_reused_and_the_user_is_told(self):
        self.run_scan(Interrupt(at=3))
        with patch.dict(os.environ, {'SCAN_MODE': 'full'}):
            code, printed = self.run_scan()
        self.assertIn('Not reusing an earlier scan: the interrupted scan used mode "probe", not "full". Scanning everything.', printed)
        self.assertEqual(len(self.scans()), 2)

    def test_sigterm_handler_is_restored_after_the_scan(self):
        before = signal.getsignal(signal.SIGTERM)
        self.run_scan()
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_summary_mentions_skipped_files_only_when_there_are_some(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.recover.finish_scan({'items': [{}], 'complete': True, 'walk_errors': []}, Path('/work/s/catalog'))
        self.assertIn('Scan finished: 1 video file(s) checked.', out.getvalue())
        self.assertNotIn('skipped', out.getvalue())


@unittest.skipUnless(os.name == 'posix' and shutil.which('ffprobe') and shutil.which('ffmpeg'), 'needs POSIX signals and ffmpeg')
class RealSignalTests(unittest.TestCase):
    """The real recover.py process, interrupted the way Ctrl-C and `docker stop` do it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = tiny_library(self.base / 'library', 40)
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


DTS = '[null @ 0x5555] Application provided invalid, non monotonically increasing dts to muxer in stream 0: 41 >= 41'
REPEAT = '    Last message repeated 6 times'
REAL = '[h264 @ 0x5555] error while decoding MB 3 4, bytestream 45'


class LogCountingTests(unittest.TestCase):
    """Which decoder messages count. The rule may drop false alarms but must never hide a real message."""

    split = staticmethod(catalog_tool.split_log)

    def test_the_timestamp_warning_and_its_repeat_lines_are_ignored(self):
        self.assertEqual(self.split([DTS, REPEAT]), ([], [DTS, REPEAT]))
        self.assertEqual(self.split([DTS, REPEAT, REPEAT]), ([], [DTS, REPEAT, REPEAT]))
        self.assertEqual(self.split([DTS, '', REPEAT]), ([], [DTS, REPEAT]))  # blank lines do not matter

    def test_a_repeat_of_a_real_message_still_counts(self):
        self.assertEqual(self.split([REAL, REPEAT]), ([REAL, REPEAT], []))
        self.assertEqual(self.split([DTS, REAL, REPEAT]), ([REAL, REPEAT], [DTS]))  # the repeat follows the real one
        self.assertEqual(self.split([DTS, REPEAT, REAL, REPEAT]), ([REAL, REPEAT], [DTS, REPEAT]))

    def test_a_repeat_with_nothing_before_it_cannot_be_attributed_so_it_counts(self):
        self.assertEqual(self.split([REPEAT]), ([REPEAT], []))
        self.assertEqual(self.split([REPEAT, DTS, REPEAT]), ([REPEAT], [DTS, REPEAT]))

    def test_other_messages_from_the_null_muxer_still_count(self):
        other = '[null @ 0x5555] Application provided invalid timestamp'
        self.assertEqual(self.split([other]), ([other], []))

    def test_randomised_sequences_never_hide_a_real_message(self):
        import random
        rng = random.Random(7)
        pool = [DTS, REPEAT, REAL, '[aac @ 0x1] Input buffer exhausted before END element found', '',
                '[mov,mp4 @ 0x2] stream 1, offset 0x81cf: partial file']
        for _ in range(2000):
            lines = [rng.choice(pool) for _ in range(rng.randint(0, 12))]
            errors, ignored = self.split(lines)
            nonblank = [l for l in lines if l.strip()]
            self.assertEqual(len(errors) + len(ignored), len(nonblank))
            self.assertEqual([l for l in nonblank if l in errors or l in ignored], nonblank)
            previous_ignored = False
            expected_errors = []
            for line in nonblank:
                if line == DTS or (line == REPEAT and previous_ignored):
                    previous_ignored = True
                else:
                    expected_errors.append(line)
                    previous_ignored = False
            self.assertEqual(errors, expected_errors, lines)
            for line in nonblank:  # every real message survives
                if line not in (DTS, REPEAT):
                    self.assertIn(line, errors)
            self.assertGreaterEqual(len(errors), len([l for l in nonblank if l not in (DTS, REPEAT)]))

    def test_the_new_rule_never_counts_more_than_the_old_one(self):
        import random
        rng = random.Random(11)
        pool = [DTS, REPEAT, REAL, '[aac @ 0x1] problem']
        for _ in range(500):
            lines = [rng.choice(pool) for _ in range(rng.randint(0, 10))]
            old = [l for l in lines if l.strip() and not catalog_tool.is_timing_warning(l)]
            self.assertLessEqual(len(self.split(lines)[0]), len(old))


@needs_ffmpeg
class ScanCountingTests(unittest.TestCase):
    """The scan and the repair verification apply the rule, and the scan stores each suspect's kind."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.library = tiny_library(self.base / 'library', 1)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def scan_with_log(self, lines, mode='full'):
        self.n += 1

        def fake(args, **kwargs):
            if args[0] == 'ffmpeg':
                kwargs['stderr'].write('\n'.join(lines) + '\n')
                return subprocess.CompletedProcess(args, 0, stdout='SHA256=' + 'ab' * 32 + '\n', stderr='')
            return REAL_RUN(args, **kwargs)
        with patch('subprocess.run', fake), contextlib.redirect_stdout(io.StringIO()):
            return catalog_tool.scan(self.library, self.base / f'out{self.n}', mode)['items'][0]

    def test_a_file_with_only_the_ignored_warning_is_clean(self):
        item = self.scan_with_log([DTS, REPEAT, DTS, REPEAT])
        self.assertEqual((item['status'], item['error_log_lines']), ('decode-clean', 0))
        self.assertNotIn('kind', item)  # only suspects get a kind

    def test_a_real_error_and_its_repeat_still_flag_the_file_with_a_kind(self):
        item = self.scan_with_log([DTS, REAL, REPEAT])
        self.assertEqual((item['status'], item['error_log_lines']), ('decode-errors', 2))
        self.assertEqual(item['kind'], 'video-errors')  # [h264 @ ...] against the file's h264 video stream

    def test_a_single_real_error_is_never_hidden_among_many_ignored_lines(self):
        item = self.scan_with_log([DTS, REPEAT] * 50 + [REAL] + [DTS, REPEAT] * 50)
        self.assertEqual((item['status'], item['error_log_lines']), ('decode-errors', 1))

    def test_truncated_files_get_their_kind_and_healthy_files_none(self):
        (self.library / 'cut.mp4').write_bytes(truncated(seed_video()))
        self.n += 1
        with contextlib.redirect_stdout(io.StringIO()):
            items = {i['path']: i for i in catalog_tool.scan(self.library, self.base / 'kinds', 'full')['items']}
        self.assertEqual(items['cut.mp4']['kind'], 'truncated')
        self.assertNotIn('kind', items['v000.mp4'])

    def test_repair_verification_applies_the_same_rule(self):
        recover = load('recover')
        recover.WORK = self.base / 'work'
        recover.WORK.mkdir()

        def verify(lines):
            def fake_run(args, log, stdout=None, duration=None):
                if log.stem == 'decode':
                    log.write_text('\n'.join(lines) + '\n')
                return 0
            with patch.object(recover, 'run', fake_run), contextlib.redirect_stdout(io.StringIO()):
                return recover.verify(self.library / 'v000.mp4', self.base / f'check{self.n}')
        self.n += 1
        clean = verify([DTS, REPEAT])
        self.assertEqual((clean['status'], clean['error_log_lines']), ('decode-clean-needs-review', 0))
        self.assertEqual(clean['null_muxer_timing_warnings'], [DTS, REPEAT])  # still recorded for the reviewer
        self.n += 1
        bad = verify([DTS, REAL, REPEAT])
        self.assertEqual((bad['status'], bad['error_log_lines']), ('needs-investigation', 2))


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def kind(self, status='decode-errors', log=None, streams=(), **fields):
        item = dict({'path': 'a.mp4', 'status': status, 'metadata': {'streams': list(streams)}}, **fields)
        if log is not None:
            (self.dir / 'x.log').write_text('\n'.join(log) + '\n')
            item['log'] = 'x.log'
        return catalog_tool.classify_suspect(item, self.dir)

    V = {'codec_type': 'video', 'codec_name': 'h264'}
    A = {'codec_type': 'audio', 'codec_name': 'aac'}

    def test_files_that_cannot_be_opened(self):
        self.assertEqual(self.kind('unreadable-or-no-video', probe_errors='[mov,mp4 @ 0x1] moov atom not found'), 'truncated')
        self.assertEqual(self.kind('unreadable-or-no-video', streams=[{'codec_type': 'audio', 'codec_name': 'mp3'}]), 'not-video')
        self.assertEqual(self.kind('unreadable-or-no-video', probe_errors='Invalid data found when processing input'), 'unreadable')
        self.assertEqual(self.kind('unreadable-or-no-video'), 'unreadable')

    def test_decode_errors_are_sorted_by_where_the_complaint_comes_from(self):
        s = [self.V, self.A]
        self.assertEqual(self.kind(log=[REAL], streams=s), 'video-errors')
        self.assertEqual(self.kind(log=[DTS, REPEAT, '[aac @ 0x1] Input buffer exhausted before END element found'], streams=s), 'audio-errors')
        self.assertEqual(self.kind(log=['[mp3float @ 0x1] bad frame'], streams=[{'codec_type': 'audio', 'codec_name': 'mp3'}, self.V]), 'audio-errors')
        self.assertEqual(self.kind(log=['[mov,mp4,m4a,3gp,3g2,mj2 @ 0x1] stream 1, offset 0x81cf: partial file'], streams=s), 'container')
        self.assertEqual(self.kind(log=['[mov,mp4,m4a,3gp,3g2,mj2 @ 0x1] stream 1, missing mandatory atoms, broken header'], streams=s), 'container')
        self.assertEqual(self.kind(log=['Cannot determine format of input stream 0:0 after EOF'], streams=s), 'container')
        self.assertEqual(self.kind(log=['[avi @ 0x1] Invalid chunk'], streams=s), 'container')

    def test_when_several_kinds_of_complaint_appear_the_most_serious_wins(self):
        s = [self.V, self.A]
        audio = '[aac @ 0x1] Input buffer exhausted before END element found'
        self.assertEqual(self.kind(log=[audio, REAL], streams=s), 'video-errors')
        self.assertEqual(self.kind(log=[audio, REAL, '[mov,mp4 @ 0x2] partial file'], streams=s), 'container')

    def test_only_the_ignored_warning_means_no_real_errors_but_uncertainty_stays_visible(self):
        self.assertEqual(self.kind(log=[DTS, REPEAT], streams=[self.V]), 'no-real-errors')
        self.assertEqual(self.kind(log=[DTS, REAL, REPEAT], streams=[self.V]), 'video-errors')  # the repeat is not ignored
        self.assertEqual(self.kind(streams=[self.V]), 'other')  # no log recorded
        self.assertEqual(catalog_tool.classify_suspect({'status': 'decode-errors', 'log': 'gone.log'}, self.dir), 'other')
        self.assertEqual(catalog_tool.classify_suspect({'status': 'decode-errors', 'log': 'x.log'}, None), 'other')
        self.assertEqual(self.kind(log=['[somethingelse @ 0x1] odd'], streams=[self.V, self.A]), 'other')
        self.assertEqual(self.kind(log=['[aist#0:1/none @ 0x1] Decoding requested, but no decoder found for: none'], streams=[self.V, self.A]), 'other')
        self.assertEqual(self.kind(log=[REAL], streams=[]), 'other')  # no stream information to match against

    def test_a_stored_kind_is_used_and_an_invalid_one_ignored(self):
        self.assertEqual(self.kind(log=[REAL], streams=[self.V], kind='audio-errors'), 'audio-errors')
        self.assertEqual(self.kind(log=[REAL], streams=[self.V], kind='bogus'), 'video-errors')


class SelectionTests(unittest.TestCase):
    def setUp(self):
        def item(path, kind, status='decode-errors'):
            return {'path': path, 'status': status, 'kind': kind}
        self.catalog = {'items': [
            {'path': 'clean.mp4', 'status': 'decode-clean'},
            item('cut.mp4', 'truncated', 'unreadable-or-no-video'), item('head.mov', 'container'),
            item('odd.mp4', 'other'), item('pic.mp4', 'video-errors'), item('snd.mp4', 'audio-errors'),
            item('song.mp4', 'not-video', 'unreadable-or-no-video'), item('noise.mp4', 'no-real-errors'),
            item('old.avi', 'truncated', 'unreadable-or-no-video'), item('mix.mkv', 'video-errors')]}

    def names(self, items):
        return [i['path'] for i in items]

    def test_default_takes_everything_untrunc_can_help_with_and_anything_unclassified(self):
        selected, left = report_module.select_suspects(self.catalog, None)
        self.assertEqual(self.names(selected), ['cut.mp4', 'head.mov', 'odd.mp4'])  # 'other' is attempted, not dismissed
        self.assertEqual({k: self.names(v) for k, v in left.items()},
                         {'video-errors': ['mix.mkv', 'pic.mp4'], 'audio-errors': ['snd.mp4'], 'not-video': ['song.mp4'],
                          'no-real-errors': ['noise.mp4'], 'unsupported-format': ['old.avi']})

    def test_named_kinds_select_exactly_those(self):
        selected, left = report_module.select_suspects(self.catalog, None, ('audio-errors',))
        self.assertEqual(self.names(selected), ['snd.mp4'])
        self.assertEqual(self.names(left['truncated']), ['cut.mp4', 'old.avi'])  # reported under its kind
        self.assertNotIn('unsupported-format', left)  # that label is only for kinds that were asked for

    def test_all_kinds_still_never_selects_a_format_untrunc_cannot_read(self):
        selected, left = report_module.select_suspects(self.catalog, None, catalog_tool.KINDS)
        self.assertNotIn('old.avi', self.names(selected))
        self.assertNotIn('mix.mkv', self.names(selected))
        self.assertEqual(set(left), {'unsupported-format'})
        self.assertEqual(self.names(left['unsupported-format']), ['mix.mkv', 'old.avi'])

    def test_clean_files_are_never_suspects(self):
        selected, left = report_module.select_suspects(self.catalog, None, catalog_tool.KINDS)
        self.assertNotIn('clean.mp4', self.names(selected) + [n for v in left.values() for n in self.names(v)])

    def test_text_for_the_left_out_kinds(self):
        text = report_module.left_out_text({'audio-errors': [1, 2], 'unsupported-format': [3]})
        self.assertEqual(text, '2 audio-errors, 1 in a format Untrunc cannot read')


class KindListingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / 'first.log').write_text(DTS + '\n' + REPEAT + '\n[aac @ 0x1] Input buffer exhausted before END element found\n')
        (self.dir / 'noise.log').write_text(DTS + '\n' + REPEAT + '\n')
        streams = [{'codec_type': 'video', 'codec_name': 'h264'}, {'codec_type': 'audio', 'codec_name': 'aac'}]
        item = lambda path, status, **f: dict({'path': path, 'status': status, 'size': 2048, 'metadata': {'streams': streams}}, **f)
        self.catalog = {'complete': True, 'mode': 'full', 'rankings': {
            'a-cut.mp4': [match('ok.mp4', 90.0)], 'b-audio.mp4': [match('ok.mp4', 80.0)], 'c-noise.mp4': [match('ok.mp4', 70.0)],
            'd-old.avi': [match('ok.mp4', 60.0)]}, 'items': [
            {'path': 'ok.mp4', 'status': 'decode-clean', 'size': 1},
            item('d-old.avi', 'unreadable-or-no-video', probe_errors='moov atom not found'),
            item('c-noise.mp4', 'decode-errors', log='noise.log'),
            item('b-audio.mp4', 'decode-errors', log='first.log'),
            item('a-cut.mp4', 'unreadable-or-no-video', probe_errors='[mov,mp4 @ 0x1] moov atom not found')]}

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, kinds=None):
        return '\n'.join(report_module.render_suspects(self.catalog, self.dir / 'catalog.json', '/src', 3, kinds=kinds))

    def test_suspects_are_grouped_by_kind_most_useful_first_with_continuous_numbers(self):
        text = self.render()
        self.assertIn('Suspects (4): 2 truncated · 1 audio-errors · 1 no-real-errors', text)
        order = [text.index(h) for h in ('== truncated (2)', '== audio-errors (1)', '== no-real-errors (1)')]
        self.assertEqual(order, sorted(order))
        for number, name in enumerate(['a-cut.mp4', 'd-old.avi', 'b-audio.mp4', 'c-noise.mp4'], 1):
            self.assertRegex(text, rf'\s{number}\. {name}')

    def test_kinds_untrunc_cannot_help_are_listed_in_short_form_without_matches(self):
        text = self.render()
        self.assertIn('  3. b-audio.mp4  (2.0 KB)', text)
        self.assertNotIn('80.0', text)  # the audio-errors file's match is not worth reading
        self.assertNotIn('70.0', text)
        self.assertIn('90.0  ok.mp4', text)  # the truncated file's match is shown

    def test_the_first_message_shown_is_the_first_real_one_not_the_ignored_warning(self):
        text = self.render()
        self.assertIn('1 decoder message(s); first: [aac @ 0x1] Input buffer exhausted before END element found', text)
        self.assertNotIn('non monotonically', text)
        self.assertIn('no real decoder messages, only the ignored timestamp warning', text)
        self.assertIn('flagged only for ffmpeg\'s harmless timestamp warning by an older scanner: rescan to clear', text)
        self.assertIn('1 file(s) were flagged only for a harmless timestamp warning by an older scanner', text)

    def test_unsupported_formats_are_marked_in_the_listing(self):
        text = self.render()
        self.assertIn('format not supported by Untrunc (it reads .mp4, .mov, .m4v, .3gp): a repair is not attempted', text)
        self.assertEqual(text.count('format not supported by Untrunc'), 1)  # only d-old.avi

    def test_kinds_filter_the_listing_and_show_full_detail(self):
        text = self.render(('audio-errors',))
        self.assertIn('b-audio.mp4', text)
        self.assertNotIn('a-cut.mp4', text)
        self.assertIn('80.0  ok.mp4', text)  # asked for explicitly, so matches are shown
        self.assertIn('3 suspect(s) of other kinds are not shown.', text)
        self.assertIn('Suspects (4):', text)  # the counts always cover everything

    def test_the_footer_says_what_the_batch_will_and_will_not_do(self):
        text = self.render()
        self.assertIn('repairs only the kinds Untrunc can help with (truncated, container, unreadable, other)', text)
        self.assertIn("CASE_ARGS='--kinds video-errors' (or 'all')", text)


class BatchKindTests(Fixture):
    def run_batch(self, fake, **kwargs):
        out = io.StringIO()
        with patch.object(case, 'compose', fake), patch.object(case.shutil, 'which', return_value='/usr/bin/docker'), \
                contextlib.redirect_stdout(out):
            code = case.run_batch(self.c, self.root, self.source, **kwargs)
        return code, out.getvalue()

    def entries(self):
        return json.loads((self.root / 'batch' / 'batch-report.json').read_text())['entries']

    def setUp(self):
        super().setUp()
        self.write_catalog(orphan=(), kinds={'trip/bad-a.mp4': 'audio-errors', 'trip/bad-b.mp4': 'truncated'})

    def test_by_default_only_kinds_untrunc_can_help_with_are_repaired_and_the_rest_reported(self):
        fake = FakeCompose()
        code, out = self.run_batch(fake)
        self.assertEqual(fake.brokens, ['bad-b.mp4'])
        self.assertEqual(list(self.entries()), ['trip/bad-b.mp4'])  # nothing is recorded for what was left out
        self.assertIn('Suspects to repair: 1 of 2 (left out: 1 audio-errors)', out)
        self.assertIn('Not attempted: 1 audio-errors. Untrunc cannot repair these', out)
        self.assertEqual(code, 0)  # every selected suspect has a candidate; the report says what was left out

    def test_named_kinds_include_exactly_those(self):
        fake = FakeCompose()
        code, out = self.run_batch(fake, kinds=('audio-errors',))
        self.assertEqual(fake.brokens, ['bad-a.mp4'])
        self.assertIn('left out: 1 truncated', out)

    def test_all_kinds_repairs_everything_untrunc_can_read(self):
        fake = FakeCompose()
        self.run_batch(fake, kinds=catalog_tool.KINDS)
        self.assertEqual(sorted(fake.brokens), ['bad-a.mp4', 'bad-b.mp4'])

    def test_nothing_selected_runs_no_container_and_says_why(self):
        self.write_catalog(orphan=(), kinds={'trip/bad-a.mp4': 'audio-errors', 'trip/bad-b.mp4': 'video-errors'})
        fake = FakeCompose()
        code, out = self.run_batch(fake)
        self.assertEqual((code, fake.calls), (0, []))
        self.assertIn('Nothing to repair: none of the 2 suspect(s) is of a kind Untrunc can help with (1 audio-errors, 1 video-errors).', out)
        self.assertFalse((self.root / 'batch' / 'batch-report.json').exists())

    def test_a_format_untrunc_cannot_read_is_reported_not_attempted(self):
        self.write_catalog(bad=('trip/bad-a.mp4', 'trip/clip.avi'), orphan=(), kinds={'trip/clip.avi': 'truncated'})
        fake = FakeCompose()
        code, out = self.run_batch(fake)
        self.assertEqual(fake.brokens, ['bad-a.mp4'])
        self.assertIn('1 in a format Untrunc cannot read', out)

    def test_unclassified_suspects_are_still_attempted(self):
        self.write_catalog(orphan=(), kinds=None)  # no kinds stored and no logs: cannot be classified
        fake = FakeCompose()
        self.run_batch(fake)
        self.assertEqual(sorted(fake.brokens), ['bad-a.mp4', 'bad-b.mp4'])


class KindsCommandLineTests(Fixture):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / 'case.py'), *args], capture_output=True, text=True, cwd=self.base)

    def test_the_listing_can_be_filtered_by_kind(self):
        self.write_catalog(orphan=(), kinds={'trip/bad-a.mp4': 'audio-errors', 'trip/bad-b.mp4': 'truncated'})
        result = self.cli('suspects', '--config', str(self.config), '--kinds', 'truncated')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('== truncated (1)', result.stdout)
        self.assertNotIn('== audio-errors', result.stdout)
        self.assertIn('1 suspect(s) of other kinds are not shown.', result.stdout)
        everything = self.cli('suspects', '--config', str(self.config), '--kinds', 'all')
        self.assertIn('== audio-errors (1)', everything.stdout)

    def test_bad_kinds_are_refused_with_the_valid_choices(self):
        result = self.cli('suspects', '--config', str(self.config), '--kinds', 'truncated,bogus')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown kind(s) bogus', result.stderr)
        self.assertIn('video-errors', result.stderr)
        self.assertEqual(self.cli('suspects', '--config', str(self.config), '--kinds', '').returncode, 2)

    def test_kinds_only_apply_to_suspects_and_batch(self):
        result = self.cli('scan', '--config', str(self.config), '--kinds', 'truncated')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--kinds only applies to suspects and batch', result.stderr)


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
        self.assertIn('Suspects to repair: 3 of 3\nAlready have a candidate: 0 · to process now: 3', out)
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
        self.assertIn('Already have a candidate: 1 · to process now: 1', out)
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
