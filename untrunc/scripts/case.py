#!/usr/bin/env python3
"""Replay saved case/agent configuration; no session-only environment needed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from progress import Progress

REPO = Path(__file__).resolve().parents[2]


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
        # Exclusive destination creation; interrupted copies fail the next hash check.
        with src.open('rb') as a, dst.open('xb') as b:
            shutil.copyfileobj(a, b)
        if sha(src) != sha(dst):
            raise ValueError(f'Copy verification failed: {dst}')
    return {'source': str(src), 'destination': str(dst), 'sha256': sha(dst)}


def check_source_directory(src):
    try:
        mode = src.stat().st_mode
    except PermissionError as e:
        raise ValueError(f'Permission denied accessing source directory: {src}; check mount authentication and access') from e
    except FileNotFoundError as e:
        raise ValueError(f'Source path not found: {src}; reconnect the share or update source_root') from e
    except OSError as e:
        raise ValueError(f'Source mount/path error for {src}: {e}') from e
    if not stat.S_ISDIR(mode):
        raise ValueError(f'Source path exists but is not a directory: {src}')


def case_config(config):
    c = json.loads(config.read_text())
    if not re.fullmatch(r'[A-Za-z0-9_-]+', c['case']):
        raise ValueError('case must contain only letters, digits, underscores and hyphens')
    root = path(c['repair_root']) / c['case']
    if root.is_relative_to(REPO):
        raise ValueError('Runtime cases must be outside the repository')
    src = path(c['source_root'])
    check_source_directory(src)
    if root.is_relative_to(src) or src.is_relative_to(root):
        raise ValueError('source_root and case directory must be disjoint to avoid scanning outputs')
    for name in ('input', 'references', 'work', 'recipes'):
        (root / name).mkdir(parents=True, exist_ok=True)
    return c, root, src


def prepare(c, root, src):
    refs = c.get('references', [])
    if not refs:
        catalogs = sorted((root / 'work').glob('*scan*/catalog/catalog.json'))
        if not catalogs:
            raise ValueError('Run scan first or configure explicit references')
        catalog = json.loads(catalogs[-1].read_text())
        if not catalog.get('complete') or path(catalog.get('host_source_root', '/unavailable')) != src:
            raise ValueError('Catalog is incomplete or belongs to a different source root')
        refs = [r['path'] for r in catalog.get('rankings', {}).get(c['broken'], [])][:c.get('max_references', 3)]
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'scan', 'run', 'clean', 'agent-setup', 'agent-model', 'agent'])
    p.add_argument('--config', required=True, type=Path)
    args = p.parse_args()
    if args.action.startswith('agent'):
        return agent(args.action, args.config)
    c, root, src = case_config(args.config)
    if args.action == 'prepare':
        prepare(c, root, src)
        return 0
    env = os.environ.copy()
    env.update(REPAIR_CASE_DIR=str(root), REPAIR_UID=str(os.getuid()), REPAIR_GID=str(os.getgid()),
               REPAIR_RECIPES_DIR=str(path(c['recipes_root']) if c.get('recipes_root') else root / 'recipes'),
               SCAN_ROOT=str(src), SCAN_HOST_ROOT=str(src), SCAN_MODE=c.get('scan_mode', 'full'),
               BROKEN=Path(c['broken']).name, REFERENCE='', FPS=str(c.get('fps', '')),
               MAX_REFERENCES=str(c.get('max_references', 3)), REPAIR_TIMEOUT=str(c.get('timeout_seconds', 1800)))
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
    cmd = ['docker', 'compose', '-f', str(REPO / 'compose/untrunc/docker-compose.yml'), 'run', '--rm', '--no-deps']
    print(f'Outputs and logs: {root / "work"}', flush=True)
    return subprocess.call(cmd + (['scanner'] if args.action == 'scan' else ['untrunc', 'clean-tail' if args.action == 'clean' else 'auto']), env=env, cwd=REPO)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
