#!/usr/bin/env python3
"""Replay saved case/agent configuration; no session-only environment needed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from progress import Progress
from catalog import KINDS
from report import (left_out_text, ranked_references, render_batch, render_suspects, select_suspects,
                    suspects)

REPO = Path(__file__).resolve().parents[2]
SOURCE_HELP = 'See untrunc/NEXT-STEPS.md, "Troubleshooting source access".'


def path(value):
    return Path(os.path.expandvars(value)).expanduser().resolve()


def sha(file):
    h = hashlib.sha256()
    with file.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def source(root, value):
    p = (root / value).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise ValueError(f'Missing file or path outside source_root: {value}')
    return p


def stage(src, dst):
    with Progress(f'Preparing {src.name}'):
        return stage_file(src, dst)


def stage_file(src, dst):
    if dst.exists():
        if sha(src) != sha(dst):
            raise ValueError(f'Refusing to overwrite different bytes: {dst}')
    else:
        # The destination appears only once the copy is verified, so an interrupted copy leaves just a
        # .partial file that the next run replaces (auxiliary files are ignored by the repair tools).
        partial = dst.with_name(dst.name + '.partial')
        with src.open('rb') as a, partial.open('wb') as b:
            shutil.copyfileobj(a, b)
        if sha(src) != sha(partial):
            partial.unlink()
            raise ValueError(f'Copy verification failed: {dst}')
        os.replace(partial, dst)
    return {'source': str(src), 'destination': str(dst), 'sha256': sha(dst)}


def check_source_directory(src):
    try:
        mode = src.stat().st_mode
    except PermissionError as e:
        raise ValueError(
            f'Permission denied accessing source directory: {src}; check mount authentication and access.\n'
            'Common causes: a network share that is not authenticated or has expired for this user, or a shell '
            'whose primary group differs from the one that owns the mount (for example after newgrp or sg). '
            f'Compare `id` with a fresh login shell. {SOURCE_HELP}') from e
    except FileNotFoundError as e:
        raise ValueError(
            f'Source path not found: {src}; reconnect the share or update source_root.\n'
            'Desktop mount directories can change after logout or remount; `findmnt` lists the current mounts. '
            f'{SOURCE_HELP}') from e
    except OSError as e:
        raise ValueError(
            f'Source mount/path error for {src}: {e}\n'
            f'The share may be disconnected or stale; remount it and retry. {SOURCE_HELP}') from e
    if not stat.S_ISDIR(mode):
        raise ValueError(f'Source path exists but is not a directory: {src}')


def filesystem_type(target, mountinfo='/proc/self/mountinfo'):
    """Filesystem type of the innermost mount containing target, or None if unknown."""
    try:
        lines = Path(mountinfo).read_text(errors='replace').splitlines()
    except OSError:
        return None
    best = None
    for line in lines:
        left, separator, right = line.partition(' - ')
        fields, tail = left.split(), right.split()
        if not separator or len(fields) < 5 or not tail:
            continue
        # mountinfo escapes spaces and other characters as \040-style octal.
        mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields[4]))
        if (target == mount or target.is_relative_to(mount)) and (best is None or len(mount.parts) >= len(best[0].parts)):
            best = (mount, tail[0])
    return best[1] if best else None


def warn_if_userspace_mount(src):
    kind = filesystem_type(src)
    # fuseblk is excluded: it is normally a host-managed disk that Docker can read.
    if kind and (kind == 'fuse' or kind.startswith('fuse.')):
        print(f'Warning: {src} is on a user-space ({kind}) mount. The scanner reads it through the Docker daemon, '
              'which usually cannot access such mounts and reports a bind-mount "permission denied". '
              f'Use a regular CIFS/NFS mount or a local copy if that happens. {SOURCE_HELP}', file=sys.stderr)


def make_case_dirs(root):
    for name in ('input', 'references', 'work', 'recipes'):
        (root / name).mkdir(parents=True, exist_ok=True)


def case_config(config, check_source=True):
    c = json.loads(config.read_text())
    if not re.fullmatch(r'[A-Za-z0-9_-]+', c['case']):
        raise ValueError('case must contain only letters, digits, underscores and hyphens')
    root = path(c['repair_root']) / c['case']
    if root.is_relative_to(REPO):
        raise ValueError('Runtime cases must be outside the repository')
    src = path(c['source_root'])
    if check_source:
        check_source_directory(src)
    if root.is_relative_to(src) or src.is_relative_to(root):
        raise ValueError('source_root and case directory must be disjoint to avoid scanning outputs')
    make_case_dirs(root)
    return c, root, src


def load_catalog(work, missing):
    """Newest scan catalog under work as (file, data); missing is the error if none exists."""
    catalogs = sorted(work.glob('*scan*/catalog/catalog.json'))
    if not catalogs:
        raise ValueError(missing)
    return catalogs[-1], json.loads(catalogs[-1].read_text())


def catalog_problems(catalog, src):
    """Reasons a catalog cannot safely drive repairs for src (empty when usable)."""
    problems = []
    if not catalog.get('complete'):
        detail = '; '.join(str(e) for e in catalog.get('walk_errors', [])[:2])
        if catalog.get('walk_errors'):
            problems.append(f'catalog is incomplete (some folders could not be read: {detail})')
        else:
            problems.append('catalog is incomplete (the scan was interrupted: run make untrunc-case-scan again '
                            'to resume it)')
    host = catalog.get('host_source_root')
    if host is None:
        if catalog.get('complete'):
            problems.append('catalog does not record which source root it was made for')
    elif path(host) != src:
        problems.append(f'catalog belongs to a different source root ({host})')
    return problems


def usable_catalog(work, src, missing):
    file, catalog = load_catalog(work, missing)
    problems = catalog_problems(catalog, src)
    if problems:
        raise ValueError('Cannot use the latest scan: ' + '; '.join(problems) + '. Run make untrunc-case-scan again (an interrupted scan resumes; '
                         'CASE_ARGS=--fresh starts over).')
    return file, catalog


def prepare(c, root, src, catalog=None):
    refs = c.get('references', [])
    if not refs:
        if catalog is None:
            catalog = usable_catalog(root / 'work', src, 'Run scan first or configure explicit references')[1]
        refs = ranked_references(catalog, c['broken'], c.get('max_references', 3))
        if not refs:
            raise ValueError('No decode-verified references ranked; inspect catalog or supply references')
        for rel in [c['broken'], *refs]:
            row = next(i for i in catalog['items'] if i['path'] == rel)
            st = source(src, rel).stat()
            if st.st_size != row['size'] or st.st_mtime_ns != row['mtime_ns']:
                raise ValueError(f'Stale scan for {rel}; rescan first')
    selected = [source(src, r) for r in refs]
    if len({p.name for p in selected}) != len(selected):
        raise ValueError('Reference basenames collide; use separate cases or explicit unique references')
    files = [stage(source(src, c['broken']), root / 'input' / Path(c['broken']).name)]
    files.extend(stage(p, root / 'references' / p.name) for p in selected)
    (root / 'references' / 'selection.json').write_text(json.dumps([p.name for p in selected], indent=2) + '\n')
    (root / 'staged.json').write_text(json.dumps({'config': c, 'files': files}, indent=2) + '\n')
    print(f'Staged {len(selected)} references; originals unchanged: {root}')


def agent(action, config):
    c = json.loads(config.read_text())
    home = path(c['home'])
    if home == Path.home() / '.hermes':
        raise ValueError('Use a dedicated agent home; do not replace your main Hermes settings')
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(HERMES_HOME=str(home), HERMES_API_TIMEOUT=str(c['api_timeout_seconds']))
    if action == 'agent-setup':
        # JSON is valid YAML. Replace only the dedicated generated config, with backup.
        settings = {'model': {'default': c['model'], 'provider': 'custom', 'base_url': c['base_url']},
                    'terminal': {'backend': 'local', 'cwd': str(REPO)},
                    'skills': {'external_dirs': [str(REPO / 'skills')]}}
        dest = home / 'config.yaml'
        if dest.exists():
            shutil.copyfile(dest, home / ('config.backup-' + str(time.time_ns()) + '.yaml'))
        dest.write_text(json.dumps(settings, indent=2) + '\n')
        model_file = home / 'Modelfile'
        if not re.fullmatch(r'[a-zA-Z0-9._:/-]+', c['base_model']):
            raise ValueError('Invalid base_model')
        model_file.write_text(f"FROM {c['base_model']}\nPARAMETER num_ctx {int(c['context_tokens'])}\n")
        print(f'Generated {dest} and {model_file}')
        return 0
    if action == 'agent-model':
        subprocess.run(['ollama', 'pull', c['base_model']], check=True)
        subprocess.run(['ollama', 'create', c['model'], '-f', str(home / 'Modelfile')], check=True)
        return 0
    if not (home / 'config.yaml').exists():
        raise ValueError('Run agent-setup first')
    return subprocess.call(['hermes'], cwd=REPO, env=env)


def latest_results(work):
    summaries = sorted(work.glob('*-summary-*/results.json'))
    if not summaries:
        raise ValueError('No recovery summary found in this case; run the standard recovery first')
    return json.loads(summaries[-1].read_text())


BATCH_STOP_AFTER = 3


def repair_env(c, root, src):
    """Environment the compose file expects for a case stored at root."""
    env = os.environ.copy()
    env.update(REPAIR_CASE_DIR=str(root), REPAIR_UID=str(os.getuid()), REPAIR_GID=str(os.getgid()),
               REPAIR_RECIPES_DIR=str(path(c['recipes_root']) if c.get('recipes_root') else root / 'recipes'),
               SCAN_ROOT=str(src), SCAN_HOST_ROOT=str(src), SCAN_MODE=c.get('scan_mode', 'full'),
               BROKEN=Path(c['broken']).name, REFERENCE='', FPS=str(c.get('fps', '')),
               MAX_REFERENCES=str(c.get('max_references', 3)), REPAIR_TIMEOUT=str(c.get('timeout_seconds', 1800)))
    return env


def compose(env, *service_args):
    cmd = ['docker', 'compose', '-f', str(REPO / 'compose/untrunc/docker-compose.yml'), 'run', '--rm', '--no-deps']
    return subprocess.call(cmd + list(service_args), env=env, cwd=REPO)


def show_suspects(c, root, src, kinds=None):
    file, catalog = load_catalog(root / 'work', f'No scan found in {root / "work"}: run make untrunc-case-scan first')
    print('\n'.join(render_suspects(catalog, file, src, c.get('max_references', 3), catalog_problems(catalog, src), kinds)))
    return 0


def write_json(file, data):
    """Atomic replace, so an interrupted batch never leaves a half-written report."""
    tmp = file.with_name(file.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    os.replace(tmp, file)


def suspect_case_name(rel, version=''):
    stem = re.sub(r'[^A-Za-z0-9_-]+', '_', Path(rel).stem).strip('_')[:40] or 'file'
    return f'{stem}-{hashlib.sha256((rel + version).encode()).hexdigest()[:8]}'


def source_version(catalog, rels):
    """Size and mtime the scan recorded for each file. A changed file therefore gets a fresh case folder
    instead of a refusal to overwrite the earlier, different copy."""
    rows = {i['path']: i for i in catalog.get('items', [])}
    return ''.join(f'|{r}:{rows.get(r, {}).get("size")}:{rows.get(r, {}).get("mtime_ns")}' for r in rels)


def host_path(work, value):
    """Show a container path such as /work/x on the host, where /work is the case's work folder."""
    try:
        return str(work / Path(value).relative_to('/work'))
    except ValueError:
        return str(value)


def repair_suspect(c, catalog, item, src, batch_root):
    """Prepare and repair one suspect in its own case folder; returns its outcome record."""
    rel = item['path']
    refs = ranked_references(catalog, rel, c.get('max_references', 3))
    entry = {'status': 'error', 'references': refs, 'source_size': item.get('size'),
             'source_mtime_ns': item.get('mtime_ns')}
    if not refs:
        entry.update(status='no-references', message='no decode-verified healthy match in the scan; '
                     'use a single-file run with explicit references')
        return entry
    case_dir = batch_root / suspect_case_name(rel, source_version(catalog, [rel, *refs]))
    entry['case_dir'] = str(case_dir)
    print(f'References: {", ".join(refs)}', flush=True)
    try:
        make_case_dirs(case_dir)
        one = dict(c, broken=rel, references=[])
        prepare(one, case_dir, src, catalog)
        print(f'Outputs and logs: {case_dir / "work"}', flush=True)
        status = compose(repair_env(one, case_dir, src), 'untrunc', 'auto')
    except (ValueError, OSError, KeyError) as e:
        entry['message'] = f'KeyError: catalog entry is missing {e}' if isinstance(e, KeyError) else str(e)
        return entry
    if status not in (0, 2):
        entry['message'] = (f'container exited with status {status}; read its output above and '
                            'NEXT-STEPS.md, "Troubleshooting source access"')
        return entry
    work = case_dir / 'work'
    try:
        results = latest_results(work)
    except (ValueError, OSError):
        results = []
    candidates = [host_path(work, r['file']) for r in results
                  if r.get('status') == 'decode-clean-needs-review' and r.get('file')]
    entry['candidates'] = candidates
    if status == 0 and candidates:
        entry['status'] = 'candidate-ready'
    else:
        entry.update(status='needs-investigation',
                     message=f'no decode-clean candidate; read the reports under {work}')
    return entry


def run_batch(c, root, src, limit=None, kinds=None):
    """Repair every suspect in the latest scan, one isolated case folder each; resumable."""
    if c.get('references'):
        raise ValueError('Batch chooses references per file from the scan: set "references" to [] '
                         '(use a single-file run for explicit references)')
    if c.get('fps'):
        raise ValueError('Batch does not apply "fps" because a frame rate is evidence about one file: '
                         'set "fps" to "" (use a single-file run for a justified rate)')
    if not shutil.which('docker'):
        raise ValueError('docker was not found in PATH; install Docker with the Compose plugin')
    catalog_file, catalog = usable_catalog(root / 'work', src, f'No scan found in {root / "work"}: '
                                           'run make untrunc-case-scan first')
    if catalog.get('mode') != 'full':
        raise ValueError('Batch needs a scan made with scan_mode "full": probe scans cannot verify healthy references')
    everything = suspects(catalog)
    if not everything:
        print('No suspect files in the latest scan: nothing to repair.')
        return 0
    found, left_out = select_suspects(catalog, catalog_file.parent, kinds)
    if not found:
        print(f'Nothing to repair: none of the {len(everything)} suspect(s) is of a kind Untrunc can help with '
              f'({left_out_text(left_out)}).\nRun make untrunc-case-suspects to review them.')
        return 0
    batch_root = root / 'batch'
    batch_root.mkdir(exist_ok=True)
    report_file = batch_root / 'batch-report.json'
    try:
        saved = json.loads(report_file.read_text())['entries'] if report_file.exists() else {}
        if not isinstance(saved, dict) or not all(isinstance(v, dict) for v in saved.values()):
            raise ValueError('unexpected layout')
    except (ValueError, KeyError, OSError) as e:
        raise ValueError(f'Cannot read {report_file} ({e}); move it aside to start the batch over') from e
    done = {i['path'] for i in found if saved.get(i['path'], {}).get('status') == 'candidate-ready'
            and saved[i['path']].get('source_size') == i.get('size')
            and saved[i['path']].get('source_mtime_ns') == i.get('mtime_ns')}
    pending = [i for i in found if i['path'] not in done]
    todo = pending[:limit] if limit else pending
    print(f'Batch repair using scan {catalog_file}\n'
          f'Suspects to repair: {len(found)} of {len(everything)}'
          + (f' (left out: {left_out_text(left_out)})' if left_out else '') + '\n'
          f'Already have a candidate: {len(done)} · to process now: {len(todo)}\n'
          f'Each suspect gets its own case folder under {batch_root}\n'
          'Ctrl-C is safe: finished files are remembered, so run the same command again to resume.', flush=True)
    consecutive, processed, stopped, interrupted = 0, 0, False, False
    try:
        for number, item in enumerate(todo, 1):
            rel = item['path']
            print(f'\n[{number}/{len(todo)}] {rel}', flush=True)
            entry = repair_suspect(c, catalog, item, src, batch_root)
            entry['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            saved[rel] = entry
            processed += 1
            write_json(report_file, {'catalog': str(catalog_file), 'entries': saved})
            # Long messages stay in the final summary; errors are worth showing immediately.
            print(f'Result: {entry["status"]}' + (f' ({entry["message"]})' if entry['status'] == 'error' else ''), flush=True)
            consecutive = consecutive + 1 if entry['status'] == 'error' else 0
            if consecutive >= BATCH_STOP_AFTER:
                stopped = True
                print(f'\nStopping after {BATCH_STOP_AFTER} consecutive errors: fix the cause shown above '
                      '(see NEXT-STEPS.md, "Troubleshooting source access"), then run the same command again.',
                      file=sys.stderr)
                break
    except KeyboardInterrupt:
        interrupted = True
        print('\nInterrupted. Finished files are saved; run the same command again to resume.', file=sys.stderr)
    entries = {i['path']: saved[i['path']] for i in found if i['path'] in saved}
    print('\n'.join(render_batch(entries, report_file, len(pending) - processed, left_out)))
    if interrupted:
        return 130
    if stopped:
        return 1
    return 0 if all(entries.get(i['path'], {}).get('status') == 'candidate-ready' for i in found) else 2


def parse_kinds(value):
    """--kinds: comma-separated kinds, or 'all'."""
    names = tuple(v.strip() for v in value.split(',') if v.strip())
    if names == ('all',):
        return KINDS
    unknown = [n for n in names if n not in KINDS]
    if unknown or not names:
        what = ', '.join(unknown) if unknown else repr(value)
        raise argparse.ArgumentTypeError(f'unknown kind(s) {what}; choose from {", ".join(KINDS)} or all')
    return names


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be at least 1')
    return number


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'scan', 'suspects', 'batch', 'run', 'clean',
                                      'agent-setup', 'agent-model', 'agent'])
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--limit', type=positive_int, help='batch only: process at most this many suspects now')
    p.add_argument('--kinds', type=parse_kinds, help='suspects and batch: only these kinds of suspect (comma-separated '
                   'or all); the batch default is every kind Untrunc can help with')
    p.add_argument('--fresh', action='store_true', help='scan only: scan every file again instead of skipping unchanged ones (also compares content hashes)')
    args = p.parse_args()
    if args.limit and args.action != 'batch':
        p.error('--limit only applies to batch')
    if args.fresh and args.action != 'scan':
        p.error('--fresh only applies to scan')
    if args.kinds and args.action not in ('suspects', 'batch'):
        p.error('--kinds only applies to suspects and batch')
    if args.action.startswith('agent'):
        return agent(args.action, args.config)
    c, root, src = case_config(args.config, check_source=args.action != 'suspects')
    if args.action == 'prepare':
        prepare(c, root, src)
        return 0
    if args.action == 'suspects':
        return show_suspects(c, root, src, args.kinds)
    if args.action == 'batch':
        return run_batch(c, root, src, args.limit, args.kinds)
    env = repair_env(c, root, src)
    if args.action == 'run':
        prepare(c, root, src)
    if args.action == 'clean':
        if os.environ.get('ALLOW_TRIM') != '1':
            raise ValueError('Set ALLOW_TRIM=1 to request the optional shortened derivative')
        results = latest_results(root / 'work')
        with_audio = [r for r in results if float(r.get('durations', {}).get('audio') or 0) > 0]
        if not with_audio:
            raise ValueError('No candidate with recovered audio found')
        best = max(with_audio, key=lambda r: float(r['durations']['audio']))
        env.update(CANDIDATE=best['file'], RECIPE=c['cleanup_recipe'], ALLOW_TRIM='1')
    if args.action == 'scan':
        warn_if_userspace_mount(src)
        env['SCAN_FRESH'] = '1' if args.fresh else ''
    print(f'Outputs and logs: {root / "work"}', flush=True)
    status = compose(env, *(['scanner'] if args.action == 'scan' else ['untrunc', 'clean-tail' if args.action == 'clean' else 'auto']))
    if status == 0 and args.action == 'scan':
        print(f'Next: list the damaged-looking files and their healthy matches with\n'
              f'  make untrunc-case-suspects REPAIR_CONFIG={shlex.quote(str(args.config.resolve()))}', flush=True)
    if status == 130 and args.action == 'scan':
        print('Scan interrupted. Run the same command again to resume it (add --fresh to start over).', file=sys.stderr)
    if status not in (0, 2, 130) and args.action == 'scan':
        print('If Docker reported a socket or bind-mount "permission denied" error, '
              f'see untrunc/NEXT-STEPS.md, "Troubleshooting source access".', file=sys.stderr)
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
