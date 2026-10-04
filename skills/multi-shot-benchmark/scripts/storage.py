from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from benchmark_store import BenchmarkError, load, save, process_key


def registry_path() -> Path:
    return Path.home() / '.cache/multi-shot-benchmark/storage.json'


@contextmanager
def registry():
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        state = load(path) if path.exists() else {'roots': [], 'active': []}
        state['roots'] = [r for r in state['roots'] if Path(r).is_dir()]
        state['runs'] = [r for r in state.get('runs', [])
                         if any(Path(r).is_relative_to(Path(root))
                                for root in state['roots']
                                if state.get('kinds', {}).get(root) == 'storage')]
        state['kinds'] = {r: k for r, k in state.get('kinds', {}).items() if r in state['roots']}
        state['active'] = [r for r in state['active'] if process_key(r['pid']) == r['key']]
        yield state
        save(path, state)


def configure(root: Path, worktrees: Path, run: Path) -> None:
    root, worktrees, run = root.resolve(), worktrees.resolve(), run.resolve()
    with registry() as state:
        for old in state['runs']:
            other = Path(old)
            if run != other and (run.is_relative_to(other) or other.is_relative_to(run)):
                raise BenchmarkError('Run outputs must not be nested inside another run')
        additions = [(root, 'storage'), (worktrees, 'worktrees')]
        for path, kind in additions:
            for old, old_kind in [*( (Path(p), k) for p, k in state['kinds'].items()), *additions]:
                if path == old and kind == old_kind:
                    continue
                if path.is_relative_to(old) or old.is_relative_to(path):
                    raise BenchmarkError('Benchmark storage and worktree roots must be disjoint across runs')
            if (str(path) not in state['roots'] or not path.is_dir()) and state['active']:
                raise BenchmarkError('Register all benchmark and worktree roots before any run starts')
        for path, kind in additions:
            path.mkdir(parents=True, exist_ok=True)
            if str(path) not in state['roots']:
                state['roots'].append(str(path))
            state['kinds'][str(path)] = kind
        if str(run) not in state['runs']:
            state['runs'].append(str(run))


@contextmanager
def shared_storage(root: Path, output: Path):
    root = str(root.resolve())
    identity = str(output.resolve())
    with registry() as state:
        if any(r['root'] != root for r in state['active']):
            raise BenchmarkError('Overlapping runs must use the same benchmark_root on this host')
        if any(r['output'] == identity for r in state['active']):
            raise BenchmarkError('Another scheduler owns this run')
        record = {'root': root, 'output': identity, 'pid': os.getpid(), 'key': process_key(os.getpid())}
        state['active'].append(record)
    try:
        yield
    finally:
        with registry() as state:
            state['active'] = [r for r in state['active'] if r != record]


def known_roots() -> list[Path]:
    with registry() as state:
        return [Path(r) for r in state['roots']]
