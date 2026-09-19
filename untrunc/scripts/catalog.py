"""Read-only video inventory and explainable reference ranking."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess

EXTENSIONS = {'.mp4', '.mov', '.m4v', '.3gp', '.mkv', '.avi', '.webm'}


def date_of(path, metadata):
    tags = metadata.get('format', {}).get('tags', {})
    value = tags.get('creation_time', '')
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        pass
    match = re.search(r'(\d{4}-\d{2}-\d{2})[_T](\d{2})h?[-:](\d{2})m?[-:](\d{2})', path)
    if match:
        try:
            return dt.datetime.fromisoformat(match[1] + 'T' + ':'.join(match.groups()[1:])).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            pass
    return None


def signature(metadata):
    streams = metadata.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), {})
    tags = metadata.get('format', {}).get('tags', {})
    return {**{k: video.get(k) for k in ['codec_name', 'width', 'height', 'r_frame_rate', 'extradata_hash']},
            'audio_codec': audio.get('codec_name'), 'sample_rate': audio.get('sample_rate'),
            'channels': audio.get('channels'), 'android': tags.get('com.android.version'),
            'make': tags.get('make') or tags.get('com.apple.quicktime.make'),
            'model': tags.get('model') or tags.get('com.apple.quicktime.model')}


def rank(target, items, limit=5):
    ranked = []
    for item in items:
        if item['path'] == target['path'] or item['status'] != 'decode-clean':
            continue
        score, reasons = 0, []
        for key, weight in [('model', 80), ('make', 20), ('extradata_hash', 100),
                            ('codec_name', 20), ('width', 15), ('height', 15),
                            ('r_frame_rate', 10), ('audio_codec', 5), ('sample_rate', 5),
                            ('channels', 5), ('android', 10)]:
            a, b = target['signature'].get(key), item['signature'].get(key)
            if a is not None and b is not None:
                score += weight if a == b else -weight
                reasons.append(f'{key}: {"match" if a == b else "different"}')
        if Path(item['path']).parent == Path(target['path']).parent:
            score += 10
            reasons.append('same directory')
        delta = None
        if target['date'] is not None and item['date'] is not None:
            delta = abs(target['date'] - item['date'])
            score += max(0, 20 - delta / 86400)
            reasons.append(f'date distance: {delta / 3600:.2f} hours')
        ranked.append({'path': item['path'], 'score': round(score, 3), 'reasons': reasons,
                       'signature': item['signature'], 'date_distance_seconds': delta,
                       'confidence': 'metadata-supported' if target['signature'].get('extradata_hash') else
                       'heuristic-only; damaged file lacks codec configuration'})
    return sorted(ranked, key=lambda x: (-x['score'], x['date_distance_seconds'] if x['date_distance_seconds'] is not None else float('inf'), x['path']))[:limit]


def scan(root, out, mode='full', timeout=1800):
    if mode not in ('full', 'probe'):
        raise ValueError('SCAN_MODE must be full or probe')
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('Library directory is unavailable')
    out.mkdir(parents=True, exist_ok=False)
    items = []
    errors = []
    def walk_error(err):
        errors.append(str(err))
    print(f'Looking for video files under {root} ...', flush=True)
    videos = []
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink() and path.suffix.lower() in EXTENSIONS:
                videos.append(path)
    total = len(videos)
    print(f'Found {total} video file(s) to scan.', flush=True)
    for number, path in enumerate(videos, 1):
        rel = str(path.relative_to(root))
        print(f'Scanning {number:>{len(str(total))}}/{total}: {rel}', flush=True)
        item = {'path': rel, 'status': 'scan-error', 'signature': {}, 'date': date_of(rel, {})}
        try:
            r = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                '-show_data_hash', 'sha256', '-of', 'json', str(path)],
                               capture_output=True, text=True, timeout=timeout)
            metadata = json.loads(r.stdout or '{}')
            item.update(signature=signature(metadata), date=date_of(rel, metadata), metadata=metadata,
                        size=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
                        probe_errors=r.stderr)
            if r.returncode or not any(s.get('codec_type') == 'video' for s in metadata.get('streams', [])):
                item['status'] = 'unreadable-or-no-video'
            elif mode == 'probe':
                item['status'] = 'probe-only-unverified'
            else:
                log = out / f'decode-{len(items):05d}.log'
                with log.open('w') as f:
                    r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2',
                                        '-i', str(path), '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'],
                                       stdout=subprocess.DEVNULL, stderr=f, timeout=timeout)
                messages = log.read_text().splitlines()
                errors_found = [s for s in messages if s.strip() and not ('[null @' in s and 'non monotonically increasing dts' in s)]
                item.update(decode_returncode=r.returncode, error_log_lines=len(errors_found), log=log.name)
                item['status'] = 'decode-clean' if r.returncode == 0 and not errors_found else 'decode-errors'
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            item.update(status='scan-error', scan_error=str(e))
        items.append(item)
        # A partial scan is useful if interrupted, but must not look complete.
        (out / 'catalog.json').write_text(json.dumps({'complete': False, 'mode': mode, 'items': items}, indent=2))
    suspects = [i for i in items if i['status'] in ('unreadable-or-no-video', 'decode-errors')]
    report = {'complete': not errors, 'walk_errors': errors, 'mode': mode,
              'root_in_container': str(root), 'host_source_root': (os.environ.get('SCAN_HOST_ROOT') or str(root)), 'items': items,
              'rankings': {i['path']: rank(i, items) for i in suspects},
              'note': 'Unreadable may mean permissions/unsupported format, not proven corruption. Rankings are hypotheses, not device identification.'}
    (out / 'catalog.json').write_text(json.dumps(report, indent=2) + '\n')
    return report
