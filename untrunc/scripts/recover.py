#!/usr/bin/env python3
"""Bounded, non-destructive repair experiments; never certify complete recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from fractions import Fraction

# Also supports loading this script directly for local tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from catalog import decode_command
from progress import Progress, say

INPUT = Path(os.environ.get('REPAIR_INPUT', '/input')).resolve()
REFS = Path(os.environ.get('REPAIR_REFERENCES', '/references')).resolve()
WORK = Path(os.environ.get('REPAIR_WORK', '/work')).resolve()
TIMEOUT = int(os.environ.get('REPAIR_TIMEOUT') or '1800')
# Host folder behind /input, /references and /work (the container only sees those short mount names).
HOST_CASE = os.environ.get('HOST_CASE_DIR', '')
HOST_LIBRARY = os.environ.get('SCAN_HOST_ROOT', '')


def shown(path):
    """A container path as it appears on the host, so messages point to real folders."""
    path = Path(path)
    if HOST_CASE:
        for mount, name in ((WORK, 'work'), (REFS, 'references'), (INPUT, 'input')):
            if path == mount or mount in path.parents:
                return str(Path(HOST_CASE) / name / path.relative_to(mount))
    if HOST_LIBRARY and (path == Path('/library') or Path('/library') in path.parents):
        return str(Path(HOST_LIBRARY) / path.relative_to('/library'))
    return str(path)


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def inside(root, name):
    if not name:
        raise ValueError('Missing filename; see make untrunc-help')
    p = (root / name).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise ValueError(f'Expected an existing file inside {shown(root)}: {name}')
    return p


def digest(path):
    h = hashlib.sha256()
    size = path.stat().st_size
    read = 0
    with Progress('Checking file fingerprint', lambda: read / size if size else None):
        with path.open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
                read += len(block)
    return h.hexdigest()


def attempt(label):
    p = WORK / (time.strftime('%Y%m%dT%H%M%S') + '-' + label + '-' + uuid.uuid4().hex[:8])
    p.mkdir(parents=True, exist_ok=False)
    return p


def run(args, log, stdout=None, duration=None):
    args = list(map(str, args))
    labels = {'decode': 'Checking all video and audio for errors',
              'retained-decode': 'Checking retained video frames',
              'untrunc': 'Reconstructing the damaged container',
              'extract': 'Extracting video for frame reconstruction',
              'remux': 'Writing reconstructed video',
              'rotation': 'Restoring video orientation',
              'audio': 'Saving full recovered audio'}
    label = labels.get(log.stem, 'Saving review image' if log.stem.startswith('frame-') else log.stem)
    progress_file = log.with_suffix('.progress')
    if Path(args[0]).name == 'ffmpeg':
        args[1:1] = ['-nostats', '-progress', str(progress_file)]

    def fraction():
        if not duration or not progress_file.exists():
            return None
        values = dict(line.split('=', 1) for line in progress_file.read_text().splitlines() if '=' in line)
        return float(values.get('out_time_us', 0)) / 1000000 / duration

    save(log.with_suffix('.command.json'), args)
    with Progress(label, fraction) as progress, log.open('w') as err:
        try:
            r = subprocess.run(args, stdout=stdout if stdout is not None else err,
                               stderr=err, timeout=TIMEOUT, check=False)
            if r.returncode:
                progress.outcome = f'exit {r.returncode}; see {shown(log)}'
            return r.returncode
        except subprocess.TimeoutExpired:
            err.write('\nTIMEOUT: experiment stopped; output is incomplete.\n')
            progress.outcome = f'timed out after {TIMEOUT}s; output incomplete'
            return 124


def probe(path):
    with Progress('Reading video metadata'):
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                        '-of', 'json', str(path)], capture_output=True, text=True,
                       timeout=TIMEOUT)
    return {'returncode': r.returncode, 'errors': r.stderr,
            'metadata': json.loads(r.stdout) if r.stdout.strip() else {}}


def verify(path, out):
    out.mkdir(parents=True, exist_ok=False)
    info = probe(path)
    save(out / 'probe.json', info)
    log = out / 'decode.log'
    length = float(info['metadata'].get('format', {}).get('duration') or 0)
    # Decode all audio/video. Exit status alone is insufficient: FFmpeg can
    # conceal corruption and still exit zero. Count its error-level messages.
    command, packet_only = decode_command(path, info['metadata'])
    rc = run(command, log, duration=length)
    messages = [s for s in log.read_text().splitlines() if s.strip()]
    # Null muxer rounds timestamps; duplicate DTS here is not a picture
    # decoding error. Preserve it separately instead of rejecting a reference.
    timing = [s for s in messages if '[null @' in s and 'non monotonically increasing dts' in s]
    lines = len(messages) - len(timing)
    streams = info['metadata'].get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    durations = {s['codec_type']: s.get('duration') for s in streams
                 if s.get('codec_type') in ('audio', 'video')}
    clean = bool(video) and info['returncode'] == 0 and rc == 0 and lines == 0
    result = {'file': str(path), 'sha256': digest(path), 'decode_returncode': rc,
              'error_log_lines': lines, 'null_muxer_timing_warnings': timing, 'durations': durations,
              'status': 'decode-clean-needs-review' if clean else 'needs-investigation',
              'packet_checked_streams': packet_only, 'complete_recovery': 'unknown', 'visual_review': 'pending',
              'audio_sync_review': 'pending'}
    # Export small review frames including the tail. These are evidence for a
    # reviewer, not automatic proof of correctness or a substitute for listening.
    duration = float(video.get('duration') or info['metadata'].get('format', {}).get('duration') or 0)
    if duration > 0:
        for n, t in enumerate(sorted(set([0.0, duration / 2, max(0, duration - 0.1)]))):
            run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(t), '-i', path,
                 '-frames:v', '1', '-vf', 'scale=480:-2', '-n', out / f'frame-{n}.jpg'],
                out / f'frame-{n}.log')
    save(out / 'report.json', result)
    say(f'Verification: {result["status"]} · {lines} decoding error messages · {shown(out / "report.json")}')
    return result


def repair(broken, reference, skip=False):
    out = attempt('skip' if skip else 'untrunc')
    # Never hardlink the original: the tool receives an independent copy.
    staged = out / 'input' / broken.name
    staged.parent.mkdir()
    with Progress('Copying input for a separate attempt'):
        shutil.copyfile(broken, staged)
    before = digest(broken)
    save(out / 'inputs.json', {'original': str(broken), 'original_sha256': before,
         'reference': str(reference), 'reference_sha256': digest(reference), 'skip_unknown': skip})
    rc = run(['untrunc', *(['-s'] if skip else []), reference, staged], out / 'untrunc.log')
    results = []
    for p in sorted(staged.parent.iterdir()):
        if p != staged and p.is_file() and p.suffix.lower() in ('.mp4', '.mov', '.m4v', '.3gp'):
            results.append(verify(p, out / ('verify-' + p.name)))
    unchanged = digest(broken) == before
    save(out / 'attempt.json', {'returncode': rc, 'original_unchanged': unchanged, 'results': results})
    if not unchanged:
        raise RuntimeError('Original changed during experiment; stop and investigate external writers')
    return results


def reframe(source, fps, reference=None):
    rate = Fraction(fps)
    if not 0 < rate <= 240:
        raise ValueError('FPS must be a positive, justified rate <= 240')
    info = probe(source)['metadata']
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
    if video.get('codec_name') != 'h264':
        raise ValueError('Frame-boundary reconstruction currently supports H.264 only')
    if video.get('has_b_frames', 0):
        raise ValueError('B-frame timing needs a specialized reconstruction; automatic reframe refused')
    out = attempt('reframe')
    raw = out / 'video.h264'
    intermediate = out / 'unrotated.mp4'
    final = out / 'reconstructed.mp4'
    save(out / 'assumptions.json', {'source': str(source), 'sha256': digest(source),
         'fps_assumption': fps, 'trimmed': False, 'reencoded': False,
         'original_timing_recovered': False, 'rotation_reference': str(reference) if reference else None})
    rc = run(['ffmpeg', '-v', 'error', '-nostdin', '-i', source, '-map', '0:v:0',
              '-c', 'copy', '-bsf:v', 'h264_mp4toannexb', '-f', 'h264', '-n', raw], out / 'extract.log')
    if rc:
        raise RuntimeError(f'Extraction failed; see {shown(out)}')
    rc = run(['ffmpeg', '-v', 'warning', '-nostdin', '-r', str(rate), '-i', raw,
              '-i', source, '-map', '0:v:0', '-map', '1:a?', '-c', 'copy',
              '-movflags', '+faststart', '-n', intermediate], out / 'remux.log')
    if rc:
        raise RuntimeError(f'Remux failed; see {shown(out)}')
    rotation_data = video.get('side_data_list', [])
    if not any('rotation' in item for item in rotation_data) and reference is not None:
        ref_video = next((s for s in probe(reference)['metadata'].get('streams', [])
                          if s.get('codec_type') == 'video'), {})
        rotation_data = ref_video.get('side_data_list', [])
    rotation = next((s['rotation'] for s in rotation_data if 'rotation' in s), 0)
    help_text = subprocess.run(['ffmpeg', '-hide_banner', '-h', 'full'],
                               capture_output=True, text=True, timeout=30).stdout
    rotation_input = ['-display_rotation:v:0', str(rotation)] if '-display_rotation' in help_text else []
    rotation_output = [] if rotation_input else ['-metadata:s:v:0', f'rotate={rotation}']
    rc = run(['ffmpeg', '-v', 'warning', '-nostdin', *rotation_input,
              '-i', intermediate, '-map', '0', '-c', 'copy', *rotation_output,
              '-movflags', '+faststart', '-n', final], out / 'rotation.log')
    if rc:
        raise RuntimeError(f'Rotation remux failed; see {shown(out)}')
    result = verify(final, out / 'verification')
    result['expected_rotation'] = rotation
    result['actual_rotation'] = [s.get('side_data_list', []) for s in probe(final)['metadata'].get('streams', [])
                                 if s.get('codec_type') == 'video']
    save(out / 'result.json', result)
    return result


def finish_scan(report, out):
    """Say what the scan did and where its catalog is; exit 0 only for a complete scan."""
    catalog = shown(out / 'catalog.json')
    print(f'\nScan finished: {len(report["items"])} video file(s) checked.', flush=True)
    print(f'Catalog saved to: {catalog}', flush=True)
    if not report['complete']:
        print('WARNING: the catalog is INCOMPLETE because some folders could not be read '
              f'(first problem: {report["walk_errors"][0] if report["walk_errors"] else "unknown"}).', flush=True)
        return 2
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['inventory', 'repair', 'auto', 'verify', 'reframe', 'scan', 'clean-tail'])
    a = p.parse_args()
    say(f'Starting {a.action}. Long stages report activity every 5 seconds; estimates are per stage.')
    if a.action == 'clean-tail':
        if os.environ.get('ALLOW_TRIM') != '1':
            raise ValueError('Cleanup excludes damaged video and shortens audio: set ALLOW_TRIM=1 explicitly')
        from tail_recipe import clean
        recipe = inside(Path('/recipes'), os.environ.get('RECIPE', ''))
        result = clean(sys.modules[__name__], inside(INPUT, os.environ.get('BROKEN', '')),
                       inside(WORK, os.environ.get('CANDIDATE', '')), recipe)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'decode-clean-needs-review' else 2
    if a.action == 'scan':
        from catalog import scan
        out = attempt('scan') / 'catalog'
        report = scan(Path('/library'), out, os.environ.get('SCAN_MODE') or 'full', TIMEOUT)
        return finish_scan(report, out)
    if a.action == 'inventory':
        out = attempt('inventory')
        items = {str(f): probe(f) for root in (INPUT, REFS) for f in sorted(root.iterdir())
                 if f.is_file() and f.suffix.lower() in ('.mp4', '.mov', '.m4v', '.3gp')}
        save(out / 'inventory.json', items)
        print(shown(out))
        return 0
    if a.action in ('verify', 'reframe'):
        source = inside(WORK, os.environ.get('CANDIDATE', ''))
        result = (verify(source, attempt('check') / 'verification') if a.action == 'verify'
                  else reframe(source, os.environ.get('FPS') or '0',
                       inside(REFS, os.environ['REFERENCE']) if os.environ.get('REFERENCE') else None))
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'decode-clean-needs-review' else 2
    broken = inside(INPUT, os.environ.get('BROKEN', ''))
    if a.action == 'repair':
        results = repair(broken, inside(REFS, os.environ.get('REFERENCE', '')))
    else:
        limit = int(os.environ.get('MAX_REFERENCES') or '3')
        if not 1 <= limit <= 10:
            raise ValueError('MAX_REFERENCES must be 1..10')
        refs = ([inside(REFS, os.environ['REFERENCE'])] if os.environ.get('REFERENCE') else
                ([inside(REFS, name) for name in json.loads((REFS / 'selection.json').read_text())][:limit]
                 if (REFS / 'selection.json').exists() else
                 [f for f in sorted(REFS.iterdir()) if f.is_file() and f.suffix.lower() in ('.mp4', '.mov', '.m4v', '.3gp')][:limit]))
        if not refs:
            raise ValueError('Add healthy clips to references/ first')
        results = []
        say(f'Plan: {len(refs)} references, 2 repair modes each; reconstruction and verification as needed.')
        for ref_index, reference in enumerate(refs, 1):
            say(f'\nReference {ref_index}/{len(refs)}: {reference.name}')
            refcheck = verify(reference, attempt('reference') / 'verification')
            if refcheck['status'] != 'decode-clean-needs-review':
                print(f'Skipping reference with decode errors: {shown(reference)}', flush=True)
                continue
            for skip in (False, True):
                say(f'Attempt {2 if skip else 1}/2: {"skip unknown data" if skip else "standard repair"}')
                current = repair(broken, reference, skip)
                results.extend(current)
                if os.environ.get('FPS') and not any(r['status'] == 'decode-clean-needs-review' for r in current):
                    for r in current:
                        try:
                            results.append(reframe(Path(r['file']), os.environ['FPS'], reference))
                        except (ValueError, RuntimeError) as e:
                            print(str(e), file=sys.stderr)
    summary = attempt('summary')
    save(summary / 'results.json', results)
    print(f'Results: {shown(summary / "results.json")}', flush=True)
    print('Human review of footage, duration and audio sync is still required.')
    return 0 if any(r['status'] == 'decode-clean-needs-review' for r in results) else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
