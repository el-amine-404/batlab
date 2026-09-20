"""Human-readable summaries of a scan catalog and of batch repairs (pure functions)."""
from collections import Counter
from pathlib import Path

from catalog import (KINDS, NOT_UNTRUNC_KINDS, SUSPECT_STATUSES, UNTRUNC_EXTENSIONS, classify_suspect, packet_only_note,
                     read_log, split_log)

STATUS_ORDER = ('decode-clean', 'decode-errors', 'unreadable-or-no-video', 'probe-only-unverified', 'scan-error')
KIND_TEXT = {
    'truncated': 'the recording was cut off and its index (moov atom) is missing: what Untrunc repairs',
    'container': 'the file structure is damaged (header or index problems): Untrunc may help',
    'unreadable': 'could not be opened, for a reason other than a missing index: Untrunc may help',
    'video-errors': 'errors in the picture data although the index is fine (real damage or a device quirk): '
                    'Untrunc cannot repair picture data',
    'audio-errors': 'only the audio decoder complained and the video decoded cleanly: Untrunc cannot repair audio data',
    'not-video': 'has no video stream, for example an audio file named .mp4: nothing to repair',
    'no-real-errors': "flagged only for ffmpeg's harmless timestamp warning by an older scanner: rescan to clear",
    'other': 'could not be classified: take a look at it',
}
BATCH_LABELS = {
    'candidate-ready': 'candidate ready',
    'needs-investigation': 'needs investigation',
    'no-references': 'no references',
    'error': 'error',
}


def suspects(catalog):
    """Items that decode with errors or cannot be read as video, ordered by path."""
    return sorted((i for i in catalog.get('items', []) if i.get('status') in SUSPECT_STATUSES),
                  key=lambda i: i['path'])


def ranked_references(catalog, rel, limit):
    """Relative paths of the best decode-verified matches the scan ranked for rel."""
    return [r['path'] for r in catalog.get('rankings', {}).get(rel, [])][:limit]


def human_size(size):
    if size is None:
        return 'unknown size'
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def clip(text, width=110):
    text = ' '.join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + '...'


def first_line(text):
    for line in (text or '').splitlines():
        if line.strip():
            return clip(line)
    return ''


def first_line_of_file(file):
    """First non-empty line of a possibly huge log, read lazily; '' if unreadable."""
    try:
        with open(file, errors='replace') as f:
            for line in f:
                if line.strip():
                    return clip(line)
    except OSError:
        pass
    return ''


def describe_match(match):
    same, different, other = [], [], []
    for reason in match.get('reasons', []):
        if reason.endswith(': match'):
            same.append(reason[:-len(': match')])
        elif reason.endswith(': different'):
            different.append(reason[:-len(': different')])
        else:
            other.append(reason)
    parts = []
    if same:
        parts.append('match: ' + ', '.join(same))
    if different:
        parts.append('differ: ' + ', '.join(different))
    return ' | '.join(parts + other) or 'no comparable metadata'


def confidence(match):
    return str(match.get('confidence', 'unknown')).split(';')[0]


def select_suspects(catalog, log_dir, kinds=None):
    """(selected, left_out): the suspects a repair should attempt, and the others grouped by why they are left out.

    kinds=None selects every kind Untrunc can help with; otherwise exactly the named kinds. A suspect in a format
    Untrunc cannot read is never selected, whatever its kind.
    """
    wanted = set(kinds) if kinds else set(KINDS) - set(NOT_UNTRUNC_KINDS)
    selected, left_out = [], {}
    for item in suspects(catalog):
        kind = classify_suspect(item, log_dir)
        if kind not in wanted:
            left_out.setdefault(kind, []).append(item)
        elif Path(item['path']).suffix.lower() not in UNTRUNC_EXTENSIONS:
            left_out.setdefault('unsupported-format', []).append(item)
        else:
            selected.append(item)
    return selected, left_out


def left_out_text(left_out):
    return ', '.join(f'{len(v)} in a format Untrunc cannot read' if k == 'unsupported-format' else f'{len(v)} {k}'
                     for k, v in left_out.items())


def suspect_detail(item, log_dir):
    if item.get('status') == 'decode-errors':
        lines = read_log(Path(log_dir) / Path(item['log']).name) if item.get('log') else None
        if lines is None:
            return f'{item.get("error_log_lines", "?")} decoder message(s); the log could not be read'
        errors, _ = split_log(lines)
        if not errors:
            return 'no real decoder messages, only the ignored timestamp warning'
        return f'{len(errors)} decoder message(s); first: {clip(errors[0])}'
    reason = first_line(item.get('probe_errors')) or clip(item.get('scan_error', ''))
    return f'ffprobe: {reason}' if reason else 'ffprobe found no video stream'


def render_suspects(catalog, catalog_file, src, top, warnings=(), kinds=None):
    """Lines describing each suspect and the healthy files a repair would use."""
    items = catalog.get('items', [])
    counts = Counter(i.get('status', 'unknown') for i in items)
    order = [s for s in STATUS_ORDER if counts[s]] + sorted(s for s in counts if s not in STATUS_ORDER)
    found = suspects(catalog)
    lines = [f'Catalog: {catalog_file}', f'Source:  {src}',
             f'Scanned {len(items)} video(s), mode {catalog.get("mode", "unknown")}: '
             + (' · '.join(f'{counts[s]} {s}' for s in order) or 'nothing found')]
    lines += [f'Warning: {w}' for w in warnings]
    if packet_only_note(items):
        lines.append(f'Note: {packet_only_note(items)}')
    changed = [i['path'] for i in items if i.get('content_changed')]
    if changed:
        shown = ', '.join(changed[:5]) + (f' and {len(changed) - 5} more' if len(changed) > 5 else '')
        lines.append(f'Warning: {len(changed)} file(s) changed content without changing size or date since the previous '
                     f'scan (possible silent corruption or a read error): {shown}. Check them against a backup.')
    if catalog.get('mode') == 'probe':
        lines.append('Note: probe scans do not decode-verify healthy files, so no references can be ranked. '
                     'Rescan with scan_mode "full".')
    if not found:
        lines += ['', 'No suspect files: every readable video decoded cleanly.']
        return lines
    ranked = 'rankings' in catalog
    log_dir = Path(catalog_file).parent
    kind_of = {i['path']: classify_suspect(i, log_dir) for i in found}
    counts = Counter(kind_of.values())
    shown = [k for k in KINDS if counts[k] and (kinds is None or k in kinds)]
    lines += ['', f'Suspects ({len(found)}): ' + ' · '.join(f'{counts[k]} {k}' for k in KINDS if counts[k]),
              f'* marks the matches a repair would use (max_references = {top}). Untrunc rebuilds a missing or damaged '
              'index from a healthy reference; it cannot repair picture or audio data.']
    number = 0
    for kind in shown:
        members = [i for i in found if kind_of[i['path']] == kind]
        # Kinds Untrunc cannot help with are listed in short form; their matches are not worth reading.
        detailed = kinds is not None or kind not in NOT_UNTRUNC_KINDS
        lines += ['', f'== {kind} ({len(members)}): {KIND_TEXT[kind]}']
        for item in members:
            number += 1
            if not detailed:
                lines += [f'{number:>4}. {item["path"]}  ({human_size(item.get("size"))})',
                          f'      {suspect_detail(item, log_dir)}']
                continue
            lines += ['', f'{number:>4}. {item["path"]}',
                      f'      {item["status"]} · {human_size(item.get("size"))}',
                      f'      {suspect_detail(item, log_dir)}']
            if Path(item['path']).suffix.lower() not in UNTRUNC_EXTENSIONS:
                lines.append(f'      format not supported by Untrunc (it reads {", ".join(UNTRUNC_EXTENSIONS)}): '
                             'a repair is not attempted')
            matches = catalog.get('rankings', {}).get(item['path'], [])
            if matches:
                for index, match in enumerate(matches):
                    lines.append(f'      {"*" if index < top else " "} {match["score"]:>7.1f}  {match["path"]}  '
                                 f'[{confidence(match)}]')
                    lines.append(f'               {describe_match(match)}')
            elif ranked:
                lines.append('      no decode-verified match found: supply "references" and use a single-file run')
            else:
                lines.append('      matches are only available after the scan finishes')
    if len(shown) < len([k for k in KINDS if counts[k]]):
        lines += ['', f'{sum(counts[k] for k in KINDS if k not in shown)} suspect(s) of other kinds are not shown.']
    lines.append('')
    if counts['no-real-errors']:
        lines.append(f'{counts["no-real-errors"]} file(s) were flagged only for a harmless timestamp warning by an older '
                     'scanner: run the scan again (only suspects are read again) to clear them.')
    lines.append('Matches are ranked hypotheses, not proof of the same camera; review every result.')
    lines.append('Next: repair one file by setting "broken" and running `make untrunc-case-run`, or run')
    lines.append('      `make untrunc-case-batch`, which repairs only the kinds Untrunc can help with '
                 f'({", ".join(k for k in KINDS if k not in NOT_UNTRUNC_KINDS)}).')
    lines.append("      CASE_ARGS='--kinds video-errors' (or 'all') selects other kinds; --kinds also filters this listing.")
    return lines


def render_batch(entries, report_file, remaining=0, left_out=None):
    """Lines summarising batch outcomes; entries maps relative path to an outcome dict."""
    counts = Counter(e.get('status') for e in entries.values())
    lines = ['', 'Batch summary: ' + (' · '.join(f'{counts[s]} {BATCH_LABELS[s]}' for s in BATCH_LABELS if counts[s])
                                       or 'nothing processed')]
    for rel, entry in sorted(entries.items()):
        lines.append(f'  {BATCH_LABELS.get(entry.get("status"), str(entry.get("status"))):<19} {rel}')
        if entry.get('status') == 'candidate-ready':
            for candidate in entry.get('candidates', [])[:3]:
                lines.append(f'      {candidate}')
        elif entry.get('message'):
            lines.append(f'      {entry["message"]}')
    if remaining:
        lines.append(f'{remaining} suspect(s) not processed yet: run the same command again to continue.')
    if left_out:
        lines.append(f'Not attempted: {left_out_text(left_out)}. Untrunc cannot repair these; `make untrunc-case-suspects` '
                     "lists them and CASE_ARGS='--kinds <kind>' includes a kind.")
    lines.append(f'Report: {report_file}')
    if counts['candidate-ready']:
        lines.append('Decode-clean is not proof of complete recovery: play each candidate and check the footage, '
                     'duration and audio sync.')
    return lines
