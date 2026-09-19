"""Read-only video inventory and explainable reference ranking."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

EXTENSIONS = {'.mp4', '.mov', '.m4v', '.3gp', '.mkv', '.avi', '.webm'}
# Bump when scan results change meaning, so an earlier scan made by older code is not reused.
SCAN_VERSION = 2
FLUSH_SECONDS = 5
CLEAN_STATUSES = ('decode-clean', 'probe-only-unverified')
HASH_ALGORITHM = 'sha256'
KNOWN_TAGS = {'apac': 'Apple spatial audio'}


def undecodable_audio(stream):
    """An audio track ffmpeg cannot decode although its codec tag is a proper four-character code.

    A missing or garbled tag (for example '[195][1][159][2]') is not accepted here: it cannot be told apart
    from a damaged header, so such a track stays in the strict decode check and is reported.
    """
    if stream.get('codec_type') != 'audio' or stream.get('codec_name') not in (None, '', 'unknown', 'none'):
        return False
    return bool(re.fullmatch(r'[ -~]{4}', stream.get('codec_tag_string') or ''))


def packet_only_note(items):
    """A factual sentence about tracks that were only packet-checked; empty when there are none."""
    tags = {}
    files = 0
    for item in items:
        found = [s['codec_tag'] for s in item.get('packet_checked_streams', [])]
        files += bool(found)
        for tag in found:
            tags[tag] = tags.get(tag, 0) + 1
    if not files:
        return ''
    shown = ', '.join(f'{tag} x{count}' + (f' = {KNOWN_TAGS[tag]}' if tag in KNOWN_TAGS else '')
                      for tag, count in sorted(tags.items()))
    return (f'{files} file(s) contain audio that ffmpeg has no decoder for (codec tag: {shown}). Those tracks were not '
            'decoded: only their packets were read, which detects truncation and index damage but not corruption '
            'inside that audio. An unexpected tag name deserves a closer look.')


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


def decode_command(path, metadata, with_hash=False):
    """ffmpeg command that decodes every audio/video stream, plus the streams it can only packet-check.

    With with_hash, a second output hashes the packets of the same streams in the same pass over the file
    (ffmpeg prints 'SHA256=<hex>'): a content fingerprint that costs no extra read.

    Audio ffmpeg has no decoder for (for example Apple spatial audio, tag apac) cannot be decoded, but its
    packets are still read with stream copy in the same pass. That reports truncation and index damage, not
    bit errors inside that audio. A video stream without a decoder stays in the decode list so it is reported:
    a picture that cannot be decoded cannot be verified.
    """
    args = ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2', '-i', str(path)]
    packet_only, position, maps = [], 0, []
    for stream in metadata.get('streams', []):
        if stream.get('codec_type') not in ('video', 'audio'):
            continue
        args += ['-map', f'0:{stream["index"]}']
        maps += ['-map', f'0:{stream["index"]}']
        if undecodable_audio(stream):
            args += [f'-c:{position}', 'copy']
            packet_only.append({'index': stream['index'], 'codec_tag': stream.get('codec_tag_string')})
        position += 1
    if not position:
        maps = ['-map', '0:v?', '-map', '0:a?']
        args += maps
    args += ['-f', 'null', '-']
    if with_hash:
        args += maps + ['-c', 'copy', '-f', 'hash', '-hash', HASH_ALGORITHM, '-']
    return args, packet_only


def parse_hash(output):
    """The hex digest ffmpeg's hash output printed, or None."""
    for line in (output or '').splitlines():
        found = re.fullmatch(f'{HASH_ALGORITHM}=([0-9a-f]+)', line.strip(), re.IGNORECASE)
        if found:
            return found[1].lower()
    return None


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


def host_root_of(root):
    """The library folder as its owner knows it (inside Docker it is mounted at /library)."""
    return os.environ.get('SCAN_HOST_ROOT') or str(root)


def write_json(file, data):
    """Atomic replace, so an interruption never leaves a half-written catalog."""
    tmp = file.with_name(file.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    os.replace(tmp, file)


def mismatch_reason(data, mode, root):
    """Why an earlier catalog cannot be reused for this library and mode, or ''."""
    what = 'the last scan' if data.get('complete') else 'the interrupted scan'
    if data.get('scan_version') != SCAN_VERSION:
        return f'{what} was made by a different version of the scanner'
    if data.get('mode') != mode:
        return f'{what} used mode "{data.get("mode")}", not "{mode}"'
    if data.get('host_source_root') != host_root_of(root):
        return f'{what} was of a different folder ({data.get("host_source_root")})'
    return ''


def usable_baseline(data, mode, root):
    """A complete scan of this folder and mode whose content hashes can be compared with a new scan's."""
    return bool(data.get('complete') and data.get('mode') == mode and data.get('host_source_root') == host_root_of(root)
                and (mode != 'full' or any(i.get('stream_hash') for i in data.get('items', []))))


def plan_scan(work, root, mode, fresh=False):
    """How a scan should start.

    kind 'resume' continues the interrupted scan in plan['folder']; 'incremental' starts a new scan that skips
    files unchanged since the last complete one; 'new' scans everything. reuse holds the earlier results (by
    relative path) that may stand in for unchanged files. baseline is the last complete scan of this folder and
    mode that recorded content hashes, used only to compare them when a file is scanned again.
    """
    plan = {'kind': 'new', 'folder': None, 'source': '', 'reuse': {}, 'baseline': {}, 'baseline_source': '', 'reason': ''}
    newest, baseline = None, None
    for file in sorted(work.glob('*scan*/catalog/catalog.json'), reverse=True):
        try:
            data = json.loads(file.read_text())
        except (OSError, ValueError):
            data = None
        if newest is None:
            newest = (file, data)
        if data and usable_baseline(data, mode, root):
            baseline = (file, data)
            break
    if baseline:
        plan.update(baseline={i['path']: i for i in baseline[1].get('items', [])}, baseline_source=baseline[0].parent.parent.name)
    if fresh or newest is None:
        return plan
    file, data = newest
    if data is None:
        plan['reason'] = f'the newest scan ({file.parent.parent.name}) has an unreadable catalog'
        return plan
    plan['reason'] = mismatch_reason(data, mode, root)
    if not plan['reason']:
        plan.update(reuse={i['path']: i for i in data.get('items', [])}, source=file.parent.parent.name,
                    kind='incremental' if data.get('complete') else 'resume',
                    folder=None if data.get('complete') else file.parent)
    return plan


def reusable(old, stat, mode):
    """An earlier result that can stand in for a new scan of the same, unchanged file.

    Only clean verdicts are reused. A file flagged as damaged, or whose scan failed, is always checked again:
    a wrong 'unreadable' caused by a share hiccup would otherwise be kept for good, and suspects are few.
    """
    return bool(old and stat and old.get('status') in CLEAN_STATUSES
                and old.get('size') == stat.st_size and old.get('mtime_ns') == stat.st_mtime_ns
                and (mode != 'full' or old.get('stream_hash')))


def scan(root, out, mode='full', timeout=1800, resume=False, reuse=None, baseline=None):
    """Catalog every video under root.

    resume: out is the folder of an interrupted scan; its results are reused unless reuse is given.
    reuse: earlier results by relative path; a clean file whose size and date are unchanged is not scanned again.
    baseline: earlier results used only to compare content hashes of files that are scanned again.
    """
    if mode not in ('full', 'probe'):
        raise ValueError('SCAN_MODE must be full or probe')
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('Library directory is unavailable')
    if resume:
        if reuse is None:
            reuse = {i['path']: i for i in json.loads((out / 'catalog.json').read_text())['items']}
    else:
        out.mkdir(parents=True, exist_ok=False)
    reuse, baseline = reuse or {}, baseline or {}
    host_root = host_root_of(root)
    items = []
    errors = []
    def walk_error(err):
        errors.append(str(err))
    # Inside Docker the library is mounted at /library: show the folder as it is on the host.
    print(f'Looking for video files under {host_root} ...', flush=True)
    videos = []
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink() and path.suffix.lower() in EXTENSIONS:
                videos.append(path)
    total = len(videos)
    print(f'Found {total} video file(s) to scan.', flush=True)
    if reuse:
        print(f'Earlier results on record: {len(reuse)}. Clean files with unchanged size and date are skipped; '
              'new, changed and previously flagged files are scanned.', flush=True)
    seen = set()
    reused = 0
    last_saved = time.monotonic()

    def snapshot():
        # Finished files first, then earlier results not yet revisited, so a resume never loses progress.
        return items + [old for rel, old in reuse.items() if rel not in seen]

    def save_partial(force=False):
        nonlocal last_saved
        if force or time.monotonic() - last_saved >= FLUSH_SECONDS:
            # A partial scan is useful if interrupted, but must not look complete.
            write_json(out / 'catalog.json', {'complete': False, 'scan_version': SCAN_VERSION, 'mode': mode,
                                              'host_source_root': host_root, 'items': snapshot()})
            last_saved = time.monotonic()
    save_partial(force=not resume)
    try:
        for number, path in enumerate(videos, 1):
            rel = str(path.relative_to(root))
            label = f'Scanning {number:>{len(str(total))}}/{total}: {rel}'
            try:
                stat = path.stat()
            except OSError:
                stat = None
            old = reuse.get(rel)
            if reusable(old, stat, mode):
                print(f'{label} (unchanged, skipped)', flush=True)
                items.append(old)
                seen.add(rel)
                reused += 1
                save_partial()
                continue
            print(label, flush=True)
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
                    # Named after the file, not its position, so it stays unique when a scan is resumed.
                    log = out / f'decode-{hashlib.sha1(rel.encode()).hexdigest()[:12]}.log'
                    command, packet_only = decode_command(path, metadata, with_hash=True)
                    with log.open('w') as f:
                        r = subprocess.run(command, stdout=subprocess.PIPE, stderr=f, text=True, timeout=timeout)
                    digest = parse_hash(r.stdout)
                    messages = log.read_text().splitlines()
                    errors_found = [s for s in messages if s.strip() and not ('[null @' in s and 'non monotonically increasing dts' in s)]
                    item.update(decode_returncode=r.returncode, error_log_lines=len(errors_found), log=log.name)
                    if packet_only:
                        item['packet_checked_streams'] = packet_only
                    if digest:
                        item['stream_hash'] = {'algorithm': HASH_ALGORITHM, 'value': digest}
                    item['status'] = 'decode-clean' if r.returncode == 0 and not errors_found else 'decode-errors'
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                item.update(status='scan-error', scan_error=str(e))
            before = baseline.get(rel) or {}
            was, now = before.get('stream_hash') or {}, item.get('stream_hash') or {}
            if was.get('algorithm') == now.get('algorithm') and was.get('value') and now.get('value') \
                    and was['value'] != now['value'] and before.get('size') == item.get('size') \
                    and before.get('mtime_ns') == item.get('mtime_ns'):
                item['content_changed'] = {'previous_hash': was['value']}
                print(f'WARNING: {rel} has different content than in the previous scan although its size and date '
                      'are unchanged.', flush=True)
            items.append(item)
            seen.add(rel)
            save_partial()
    except KeyboardInterrupt:
        save_partial(force=True)
        print(f'\nInterrupted: {min(len(snapshot()), total)} of {total} file(s) are saved. '
              'Run the same command again to resume.', flush=True)
        raise
    note = packet_only_note(items)
    if note:
        print(f'Note: {note}', flush=True)
    dropped = sum(1 for rel in reuse if rel not in seen)
    if dropped:
        print(f'{dropped} file(s) from the earlier scan no longer exist and were dropped.', flush=True)
    changed = [{'path': i['path'], **i['content_changed']} for i in items if i.get('content_changed')]
    if changed:
        print(f'WARNING: {len(changed)} file(s) changed content without changing size or date since the previous scan: '
              'possible silent corruption or a read error. Check those files against a backup.', flush=True)
    suspects = [i for i in items if i['status'] in ('unreadable-or-no-video', 'decode-errors')]
    report = {'complete': not errors, 'walk_errors': errors, 'mode': mode, 'scan_version': SCAN_VERSION, 'reused': reused,
              'dropped': dropped, 'content_changed': changed,
              'root_in_container': str(root), 'host_source_root': host_root, 'items': items,
              'rankings': {i['path']: rank(i, items) for i in suspects},
              'note': 'Unreadable may mean permissions/unsupported format, not proven corruption. Rankings are hypotheses, not device identification.'}
    write_json(out / 'catalog.json', report)
    return report
