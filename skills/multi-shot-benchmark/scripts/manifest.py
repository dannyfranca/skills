from __future__ import annotations

import re
from pathlib import Path

from benchmark_store import BenchmarkError, digest, git, load, resolve, save, tool_versions, snapshot_tool
from driver import validate_profile
from storage import configure
from replay import REVIEW, config_text
from review_config import load_explicit_review_config


def label(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', value):
        raise BenchmarkError('Names must use lowercase letters, digits, and hyphens')
    return value


def freeze(spec: dict, output: Path, *, input_root: Path = Path('.'), benchmark_root: Path | None = None,
           worktree_root: Path | None = None) -> dict:
    output, input_root = output.resolve(), input_root.resolve()
    repo = Path(spec['repo'])
    if not repo.is_absolute():
        repo = input_root / repo
    repo = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    boundary = (benchmark_root or output.parent).resolve()
    if output.is_relative_to(repo) or not output.is_relative_to(boundary) or output == boundary:
        raise BenchmarkError('Run assets need a separate benchmark root')
    if output.exists():
        raise BenchmarkError('Run already exists; use resume')
    repetitions = spec.get('repetitions', 2)
    if type(repetitions) is not int or repetitions < 1:
        raise BenchmarkError('Repetitions must be a positive integer')
    driver = validate_profile(spec['driver'])
    cases, arms, assets = [], [], {}
    for index, value in enumerate(spec['cases'], 1):
        case = dict(value) if isinstance(value, dict) else {'head': value}
        name = label(case.get('name', f'case-{index:03}'))
        head = resolve(repo, case['head'])
        parents = git(repo, 'rev-list', '--parents', '-n', '1', head).decode().split()[1:]
        if len(parents) > 1 and not case.get('base'):
            raise BenchmarkError(f'{name}: merge commits require a base')
        base = resolve(repo, case['base']) if case.get('base') else parents[0] if parents else None
        if base:
            if base == head:
                raise BenchmarkError(f'{name}: base must precede head')
            git(repo, 'merge-base', '--is-ancestor', base, head)
        if not case.get('task_file'):
            raise BenchmarkError(f'{name}: original task is missing; recover GitHub requirements or supply a task file')
        task = (input_root / case['task_file']).read_bytes()
        if not task.decode().strip():
            raise BenchmarkError(f'{name}: original task is empty')
        task_asset = f'assets/tasks/{name}.md'
        assets[task_asset] = task
        cases.append({'name': name, 'head': head, 'base': base, 'original_head': case['head'],
                      'original_base': case.get('base'), 'task_file': task_asset,
                      'provenance': case.get('provenance', {'kind': 'supplied-task'})})
    for arm in spec['arms']:
        name = label(arm['name'])
        config = input_root / arm['config']
        effective = load_explicit_review_config(config, variant=arm.get('variant'))
        config_asset = f'assets/arms/{name}.toml'
        assets[f'assets/arm-sources/{name}.toml'] = config.read_bytes()
        assets[config_asset] = config_text(effective, output).encode()
        arms.append({'name': name, 'config': config_asset, 'variant': arm.get('variant')})
    if not cases or not arms or len({c['name'] for c in cases}) != len(cases) or len({a['name'] for a in arms}) != len(arms):
        raise BenchmarkError('Cases and arms must be nonempty with unique names')
    assignments = []
    for case_index, case in enumerate(cases):
        for repetition in range(1, repetitions + 1):
            offset = (case_index + repetition - 1) % len(arms)
            for arm in arms[offset:] + arms[:offset]:
                identity = f'{case["name"]}:{arm["name"]}:{repetition}'
                assignments.append({'id': digest(identity.encode())[:20], 'case': case['name'],
                                    'arm': arm['name'], 'repetition': repetition, 'order': len(assignments) + 1})
    worktrees = (worktree_root or Path.home() / '.worktrees').resolve()
    if worktrees.is_relative_to(output) or output.is_relative_to(worktrees):
        raise BenchmarkError('Worktrees and run outputs need separate roots')
    configure(boundary, worktrees, output)
    output.mkdir(parents=True)
    for name, data in assets.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    tool = output / 'tool/multi-shot-review'
    snapshot_tool(REVIEW, tool)
    manifest = {'schema': 1, 'repository': str(repo), 'benchmark_root': str(boundary),
                'worktree_root': str(worktrees),
                'driver': driver, 'repetitions': repetitions, 'cases': cases, 'arms': arms,
                'assignments': assignments, 'tools': tool_versions(tool),
                'load_limits': 'Arms overlap on one host. CPU, disk, caches, network, and model capacity are shared.'}
    save(output / 'manifest.json', manifest)
    frozen = {str(path.relative_to(output)): digest(path.read_bytes()) for path in
              [output / 'manifest.json', *sorted((output / 'assets').rglob('*')), *sorted(tool.rglob('*'))]
              if path.is_file()}
    save(output / 'freeze.json', {'hashes': frozen})
    return manifest


def read_manifest(output: Path) -> dict:
    output = output.resolve()
    for name, expected in load(output / 'freeze.json')['hashes'].items():
        path = output / name
        if not path.resolve().is_relative_to(output) or digest(path.read_bytes()) != expected:
            raise BenchmarkError(f'Frozen run asset changed: {name}')
    return load(output / 'manifest.json')
