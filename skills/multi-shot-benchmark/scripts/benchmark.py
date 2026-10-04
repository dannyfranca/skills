#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_store import BenchmarkError, digest, load, resolve, save
from manifest import freeze
from scheduling import run, progress, cleanup
from comparison import measure
from assessment import record
from github_context import recover, select
from report import build, basis


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Replay real historical diffs with isolated full review workflows')
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('setup', help='Freeze one repository, tasks, arm configs, driver, and assignments')
    setup.add_argument('--spec', type=Path, required=True)
    setup.add_argument('--run', type=Path, required=True)
    setup.add_argument('--benchmark-root', type=Path)
    setup.add_argument('--worktree-root', type=Path)
    for name in ('run', 'resume', 'progress', 'cleanup', 'measure', 'report', 'assess'):
        command = commands.add_parser(name)
        command.add_argument('--run', type=Path, required=True)
        if name in ('measure', 'report'):
            command.add_argument('--rates', type=Path)
        if name == 'report':
            command.add_argument('--decision', type=Path)
        if name == 'assess':
            command.add_argument('--execution', required=True)
            command.add_argument('--assessment', type=Path, required=True)
    recovery = commands.add_parser('recover-task', help='Save parent-only GitHub description candidates')
    recovery.add_argument('--repo', type=Path, required=True)
    recovery.add_argument('--head', required=True)
    recovery.add_argument('--output', type=Path, required=True)
    selection = commands.add_parser('select-task', help='Freeze parent-selected original requirements from recovered descriptions')
    selection.add_argument('--recovered', type=Path, required=True)
    selection.add_argument('--pr', type=int, required=True)
    selection.add_argument('--text', type=Path, required=True)
    selection.add_argument('--task', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'setup':
            spec = load(args.spec)
            for case in spec.get('cases', []):
                if isinstance(case, dict) and case.get('task_file') and not case.get('provenance'):
                    sidecar = args.spec.parent / (case['task_file'] + '.provenance.json')
                    if sidecar.is_file():
                        case['provenance'] = load(sidecar)
            repo = args.spec.parent / spec['repo']
            for case in spec.get('cases', []):
                provenance = case.get('provenance', {}) if isinstance(case, dict) else {}
                if provenance.get('kind') == 'github-parent-selection':
                    task = args.spec.parent / case['task_file']
                    if provenance.get('head') != resolve(repo, case['head']) or provenance.get('task_sha256') != digest(task.read_bytes()):
                        raise BenchmarkError('Selected GitHub task does not match this commit or task text')
            result = freeze(spec, args.run, input_root=args.spec.parent,
                            benchmark_root=args.benchmark_root, worktree_root=args.worktree_root)
            result = {'assignments': len(result['assignments']), 'run': str(args.run.resolve())}
        elif args.command in ('run', 'resume'):
            result = run(args.run)
        elif args.command == 'progress':
            result = progress(args.run)
        elif args.command == 'cleanup':
            result = cleanup(args.run)
        elif args.command == 'measure':
            value = measure(args.run, rates_file=args.rates)
            result = {'measurements': str(args.run.resolve() / 'measurements.json'), 'basis': basis(value)}
        elif args.command == 'report':
            result = build(args.run.resolve(), recommendation=load(args.decision) if args.decision else None, rates_file=args.rates)
        elif args.command == 'assess':
            from manifest import read_manifest
            manifest = read_manifest(args.run)
            if args.execution not in {a['id'] for a in manifest['assignments']}:
                raise BenchmarkError('Execution must belong to this run')
            result = record(args.run / 'executions' / args.execution, load(args.assessment))
        elif args.command == 'recover-task':
            result = recover(args.repo, args.head)
            save(args.output, result)
            result = {'candidates': len(result['candidates']), 'errors': result['errors'], 'selection_required': True,
                      'output': str(args.output.resolve())}
        else:
            result = select(load(args.recovered), args.pr, args.task, selected_text=args.text.read_text())
        print(json.dumps(result))
        if args.command in ('run', 'resume') and result['completed'] != result['total']:
            return 2
        return 0
    except (BenchmarkError, ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'Benchmark error: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
