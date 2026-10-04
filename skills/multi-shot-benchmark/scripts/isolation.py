from __future__ import annotations

import shutil
from pathlib import Path

from benchmark_store import BenchmarkError, load


def child_command(output: Path, command: list[str]) -> list[str]:
    state = load(output / 'replay.json')
    if state['status'] != 'ready':
        raise BenchmarkError('Replay is not ready')
    if shutil.which('bwrap') is None:
        raise BenchmarkError('bubblewrap is required for child isolation')
    worktree = Path(state['worktree']).resolve()
    output = output.resolve()
    hidden = {Path(state['source']).resolve(), Path(state['benchmark_root']), worktree.parent, Path.home() / '.reviews',
              Path.home() / '.agents/skills/multi-shot-review/reports',
              *(Path(p) for p in state['hidden_paths'])}
    if any(worktree == path or output == path for path in hidden):
        raise BenchmarkError('Isolation roots overlap')
    args = ['bwrap', '--die-with-parent', '--unshare-pid', '--ro-bind', '/', '/', '--dev-bind', '/dev', '/dev',
            '--proc', '/proc', '--bind', '/tmp', '/tmp']
    hidden = {p.resolve() for p in hidden}
    hidden = {p for p in hidden if not any(p != other and p.is_relative_to(other) for other in hidden)}
    for path in sorted(hidden, key=lambda p: len(p.parts)):
        if path.exists():
            args.extend(['--tmpfs', str(path)])
    args.extend(['--bind', str(output), str(output), '--bind', str(worktree), str(worktree),
                 '--chdir', str(worktree), '--', *command])
    return args
