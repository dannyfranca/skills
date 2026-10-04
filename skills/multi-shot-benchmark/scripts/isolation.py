from __future__ import annotations

import shutil
import os
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
    for cache in (Path.home() / '.cargo', Path.home() / '.cache'):
        if cache.is_dir():
            args.extend(['--bind', str(cache), str(cache)])
    hidden = {p.resolve() for p in hidden}
    hidden = {p for p in hidden if not any(p != other and p.is_relative_to(other) for other in hidden)}
    for path in sorted(hidden, key=lambda p: len(p.parts)):
        if path.exists():
            args.extend(['--tmpfs', str(path)])
    args.extend(['--bind', str(output), str(output), '--bind', str(worktree), str(worktree)])
    homes = {'.codex': ('CODEX_HOME', ('auth.json', 'config.toml')),
             '.claude': ('CLAUDE_CONFIG_DIR', ('.credentials.json', 'settings.json'))}
    for directory, (variable, files) in homes.items():
        original = Path(os.environ.get(variable, str(Path.home() / directory))).expanduser().resolve()
        runtime = output / 'runtime' / directory
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = original if original.exists() else runtime
        args.extend(['--bind', str(runtime), str(destination)])
        args.extend(['--setenv', variable, str(destination)])
        for name in files:
            source = original / name
            if source.is_file():
                target = runtime / name
                target.touch(exist_ok=True)
                args.extend(['--ro-bind', str(source), str(destination / name)])
    settings = Path.home() / '.claude.json'
    if settings.is_file():
        args.extend(['--ro-bind', str(settings), str(settings)])
    args.extend(['--chdir', str(worktree), '--', *command])
    return args
