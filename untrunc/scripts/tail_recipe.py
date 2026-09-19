"""Explicit, hash-guarded replay of a known file's clean-ending derivative."""
import json
import re
from fractions import Fraction
from pathlib import Path
import subprocess


def clean(recovery, broken, candidate, recipe_path):
    recovery.say('Cleanup: verify original → scan frames → validate → preserve audio → write → verify.')
    r = json.loads(recipe_path.read_text())
    if recovery.digest(broken) != r['original_sha256']:
        raise ValueError('This forensic recipe belongs to a different original; refusing to apply it')
    out = recovery.attempt('clean-tail')
    with recovery.Progress('Reading original video'):
        data = broken.read_bytes()
    hits = []
    scanned = 0
    with recovery.Progress('Scanning original frame boundaries', lambda: scanned / len(data)):
        for match in re.finditer(b'[\x41\x65]', data):
            i = match.start()
            scanned = i
            if i < 4:
                continue
            n = int.from_bytes(data[i-4:i], 'big')
            if not 100 < n < 1000000 or i+n > len(data):
                continue
            bits = ''.join(f'{v:08b}' for v in data[i+1:i+24].replace(b'\x00\x00\x03', b'\x00\x00'))
            p = 0
            def ue():
                nonlocal p
                q = p
                while bits[q] == '0':
                    q += 1
                zeros = q-p
                value = int(bits[q:q+zeros+1], 2)-1
                p = q+zeros+1
                return value
            try:
                first, slice_type, pps = ue(), ue(), ue()
                frame = int(bits[p:p+r['frame_num_bits']], 2)
            except (IndexError, ValueError):
                continue
            typ = data[i] & 31
            if first == 0 and pps == 0 and ((typ == 5 and slice_type == 7 and frame == 0) or
                                          (typ == 1 and slice_type == 5 and 0 < frame < r['gop_cycle'])):
                hits.append((i, n, frame))
    if len(hits) != r['expected_candidates'] or any(x[2] != n % r['gop_cycle'] for n, x in enumerate(hits)):
        raise ValueError('Candidate count/continuity differs from the documented recipe')
    retained = hits[:-r['exclude_final_candidates']]
    raw = out / 'retained.h264'
    with recovery.Progress('Writing retained video frames'), raw.open('xb') as f:
        for header in (r['sps_hex'], r['pps_hex']):
            f.write(b'\0\0\0\1'+bytes.fromhex(header))
        for offset, length, _ in retained:
            f.write(b'\0\0\0\1'+data[offset:offset+length])
    del data
    recovery.say(f'Retaining {len(retained)} frames; full audio will be saved separately.')
    rc = recovery.run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2', '-i', raw,
                       '-f', 'null', '-'], out / 'retained-decode.log', duration=len(retained) / r['fps'])
    if rc or (out / 'retained-decode.log').read_text().strip():
        raise ValueError(f'Retained sequence failed verification: {recovery.shown(out)}')
    # Preserve every available audio sample separately before shortening the derivative.
    audio = out / 'full-recovered-audio.m4a'
    rc = recovery.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', candidate, '-map', '0:a:0',
                       '-c', 'copy', '-n', audio], out / 'audio.log')
    if rc:
        raise ValueError('Audio extraction failed; see attempt log')
    duration = float(Fraction(len(retained), r['fps']))
    intermediate = out / 'unrotated.mp4'
    rc = recovery.run(['ffmpeg', '-v', 'warning', '-nostdin', '-r', str(r['fps']), '-i', raw,
                       '-i', audio, '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy',
                       '-t', str(duration), '-movflags', '+faststart', '-n', intermediate], out / 'remux.log')
    if rc:
        raise ValueError('Clean derivative mux failed')
    help_text = subprocess.run(['ffmpeg', '-h', 'full'], capture_output=True, text=True, timeout=30).stdout
    ri = ['-display_rotation:v:0', str(r['rotation'])] if '-display_rotation' in help_text else []
    ro = [] if ri else ['-metadata:s:v:0', f"rotate={r['rotation']}"]
    final = out / broken.name
    rc = recovery.run(['ffmpeg', '-v', 'error', '-nostdin', *ri, '-i', intermediate,
                       '-map', '0', '-c', 'copy', *ro, '-movflags', '+faststart', '-n', final], out / 'rotation.log')
    if rc:
        raise ValueError('Rotation remux failed')
    report = recovery.verify(final, out / 'verification')
    report.update(trimmed=True, retained_frames=len(retained), excluded_candidates=r['exclude_final_candidates'],
                  recovered_additional_footage=False, full_audio=str(audio), recipe=r)
    recovery.save(out / 'cleanup-report.json', report)
    return report
