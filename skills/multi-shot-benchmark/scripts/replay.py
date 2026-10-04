from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from benchmark_store import BenchmarkError, digest, execute, git, instructions, load, resolve, save, tool_versions, import_history, snapshot_tool

REVIEW = Path(__file__).resolve().parents[2] / 'multi-shot-review'
sys.path.insert(0, str(REVIEW / 'scripts'))
from review_config import load_explicit_review_config
from review_state import ReviewState, ReviewStateError, _running_reservation_is_active
from contextlib import ExitStack
from ownership import locked
from storage import configure


def config_text(config, output: Path) -> str:
    effective = config.to_snapshot()
    lines = [f'review_root = {json.dumps(str(output / "sessions"), ensure_ascii=False)}']
    for key, value in effective.items():
        if value is not None and not isinstance(value, dict):
            lines.append(f'{key} = {json.dumps(value, ensure_ascii=False)}')
    for key, value in effective.items():
        if isinstance(value, dict):
            lines.append(f'[{key}]')
            lines.extend(f'{k} = {json.dumps(v, ensure_ascii=False)}' for k, v in value.items() if v is not None)
    if config.variant != 'default':
        lines.extend(['[variants]', f'{config.variant} = 1', f'[variant.{config.variant}]'])
    return '\n'.join(lines) + '\n'


def prepare(repo: Path, head: str, task: str, config_file: Path, output: Path, **options) -> dict:
    with locked(output):
        return _prepare(repo, head, task, config_file, output, **options)


def _prepare(repo: Path, head: str, task: str, config_file: Path, output: Path,
            *, base: str | None = None, variant: str | None = None,
            worktree_root: Path | None = None, benchmark_root: Path | None = None,
            tool_snapshot: Path | None = None, run_root: Path | None = None) -> dict:
    repo = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    output = output.resolve()
    boundary = (benchmark_root or output.parent).resolve()
    if output.is_relative_to(repo) or not output.is_relative_to(boundary) or output == boundary:
        raise BenchmarkError('Assets need a separate benchmark root that contains the output')
    if output.exists():
        raise BenchmarkError('Replay output already exists; use resume')
    if not task.strip():
        raise BenchmarkError('Historical task text is required')
    head_sha = resolve(repo, head)
    parents = git(repo, 'rev-list', '--parents', '-n', '1', head_sha).decode().split()[1:]
    if base is None:
        if len(parents) > 1:
            raise BenchmarkError('A merge commit requires an explicit base')
        base_sha = parents[0] if parents else git(repo, 'hash-object', '-t', 'tree', '--stdin', data=b'').decode().strip()
    else:
        base_sha = resolve(repo, base)
    if not (not parents and base is None):
        if base_sha == head_sha:
            raise BenchmarkError('Base must precede the selected head')
        try:
            git(repo, 'merge-base', '--is-ancestor', base_sha, head_sha)
        except BenchmarkError as exc:
            raise BenchmarkError('Base must be an ancestor of the selected head') from exc
    review_tool = tool_snapshot or REVIEW
    program = "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from review_config import load_explicit_review_config; c=load_explicit_review_config(Path(sys.argv[2]),variant=sys.argv[3] or None); print(json.dumps({'settings':c.to_snapshot(),'variant':c.variant}))"
    frozen_config = json.loads(execute([sys.executable, '-c', program, str(review_tool / 'scripts'),
                                       str(config_file), variant or '']))
    config = SimpleNamespace(variant=frozen_config['variant'], to_snapshot=lambda: frozen_config['settings'])
    common = git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()
    source_worktrees = git(repo, 'worktree', 'list', '--porcelain').decode().splitlines()
    source_paths = [line.removeprefix('worktree ') for line in source_worktrees if line.startswith('worktree ')]
    worktrees = (worktree_root or Path.home() / '.worktrees').resolve()
    if worktrees.is_relative_to(output) or output.is_relative_to(worktrees):
        raise BenchmarkError('Worktrees and replay outputs need separate roots')
    configure(boundary, worktrees, run_root or output)
    output.mkdir(parents=True)
    state = {'schema': 1, 'status': 'preparing', 'source': str(repo), 'head': head_sha,
             'base': base_sha, 'root_commit': not parents and base is None, 'owned': [],
             'benchmark_root': str(boundary), 'hidden_paths': [common, *source_paths],
             'review_dir': None, 'config': config.to_snapshot(), 'variant': config.variant}
    save(output / 'replay.json', state)
    try:
        patch = git(repo, 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
                    '--no-color', '--src-prefix=a/', '--dst-prefix=b/', '--unified=3',
                    '--inter-hunk-context=0', '--diff-algorithm=myers', '--no-indent-heuristic',
                    '--submodule=short', '--ignore-submodules=none', '--no-relative', base_sha, head_sha)
        (output / 'initial.patch').write_bytes(patch)
        (output / 'task.md').write_text(task)
        (output / 'arm-source.toml').write_bytes(config_file.read_bytes())
        (output / 'session.toml').write_text(config_text(config, output))
        save(output / 'instructions.json', instructions(repo, head_sha))
        save(output / 'tools.json', tool_versions(review_tool))
        tool = output / 'tool' / 'multi-shot-review'
        state['tool_hashes'] = snapshot_tool(review_tool, tool)
        state['diff_sha256'] = digest(patch)
        state['source_remote'] = git(repo, 'remote', '-v').decode()
        state['initial_tree'] = git(repo, 'rev-parse', f'{head_sha}^{{tree}}').decode().strip()
        store = output / 'history'
        store.mkdir()
        state['owned'].append(str(store))
        save(output / 'replay.json', state)
        state['object_format'] = git(repo, 'rev-parse', '--show-object-format').decode().strip()
        git(store, 'init', '--bare', '--template=', f'--object-format={state["object_format"]}')
        if state['root_commit']:
            tree = git(store, 'mktree', data=b'').decode().strip()
            state['replay_base'] = git(store, '-c', 'user.name=Benchmark', '-c', 'user.email=benchmark@localhost',
                                        'commit-tree', tree, data=b'Empty replay base\n').decode().strip()
        else:
            import_history(repo, store, base_sha)
            state['replay_base'] = base_sha
        git(store, 'update-ref', 'refs/heads/replay-base', state['replay_base'])
        naming = REVIEW.parent / 'worktrees/scripts/worktree-name.sh'
        name = execute(['bash', str(naming), 'benchmark'], cwd=repo).decode().strip()
        worktree = ((worktree_root or Path.home() / '.worktrees') /
                    f'{name}-{digest(str(output).encode())[:12]}').resolve()
        if worktree.exists():
            raise BenchmarkError(f'Worktree already exists: {worktree}')
        state['worktree'] = str(worktree)
        state['owned'].append(str(worktree))
        state['asset_hashes'] = {name: digest((output / name).read_bytes()) for name in
                                 ('initial.patch', 'task.md', 'session.toml', 'instructions.json', 'tools.json')}
        save(output / 'replay.json', state)
        return _resume(output)
    except Exception as exc:
        state = load(output / 'replay.json')
        state.update(status='failed', error=str(exc))
        save(output / 'replay.json', state)
        raise


def verify_ownership(output: Path, state: dict) -> tuple[Path, Path]:
    store = output.resolve() / 'history'
    worktree = Path(state['worktree'])
    if str(store) not in state['owned'] or str(worktree) not in state['owned']:
        raise BenchmarkError('Missing ownership record')
    if worktree.exists() and git(worktree, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip() != str(store):
        raise BenchmarkError('Worktree ownership differs from the record')
    return store, worktree


def resume(output: Path) -> dict:
    with locked(output):
        return _resume(output)


def _resume(output: Path) -> dict:
    output = output.resolve()
    state = load(output / 'replay.json')
    if state['status'] == 'cleaned':
        raise BenchmarkError('Cleaned replays cannot resume')
    if 'asset_hashes' not in state:
        raise BenchmarkError('Setup failed before assets were frozen; preserve evidence and create a new replay')
    for name, expected in state['asset_hashes'].items():
        if digest((output / name).read_bytes()) != expected:
            raise BenchmarkError(f'Frozen asset changed: {name}')
    hashes = state.get('tool_hashes', load(output / 'tools.json')['review_scripts'])
    for name, expected in hashes.items():
        tool_file = output / 'tool/multi-shot-review' / name
        if (name in state.get('tool_hashes', {}) and not tool_file.is_file()) or (
                tool_file.is_file() and digest(tool_file.read_bytes()) != expected):
            raise BenchmarkError(f'Frozen review tool changed: {name}')
    store, worktree = verify_ownership(output, state)
    if state['status'] == 'ready' and worktree.is_dir():
        return state
    try:
        if not worktree.exists():
            if state['review_dir'] and load(Path(state['review_dir']) / '_state.json')['slices']:
                raise BenchmarkError('Missing reviewed worktree; restore its saved final code before resume')
            git(store, 'worktree', 'prune')
            worktree.parent.mkdir(parents=True, exist_ok=True)
            git(store, 'worktree', 'add', '--detach', str(worktree), state['replay_base'])
        tree = git(worktree, 'write-tree').decode().strip()
        if tree != state['initial_tree']:
            if tree != git(worktree, 'rev-parse', 'HEAD^{tree}').decode().strip():
                raise BenchmarkError('Partial replay changed; inspect it before resume')
            git(worktree, 'apply', '--index', '--binary', data=(output / 'initial.patch').read_bytes())
        if git(worktree, 'write-tree').decode().strip() != state['initial_tree']:
            raise BenchmarkError('Replay tree differs from the historical head')
        hydration = worktree / 'scripts/hydrate-worktree.sh'
        if hydration.is_file():
            execute(['bash', str(hydration)], cwd=worktree)
        if git(worktree, 'write-tree').decode().strip() != state['initial_tree'] or git(worktree, 'diff', '--binary', '--no-ext-diff'):
            raise BenchmarkError('Hydration changed tracked files')
        if state['review_dir'] is None:
            state['review_dir'] = execute([sys.executable, str(output / 'tool/multi-shot-review/scripts/init_state.py'),
                                          '--root', str(worktree), '--task-file', str(output / 'task.md'),
                                          '--config-file', str(output / 'session.toml'),
                                          '--variant', state['variant']]).decode().strip()
        state.update(status='ready')
        state.pop('error', None)
        save(output / 'replay.json', state)
        return state
    except Exception as exc:
        state.update(status='failed', error=str(exc))
        save(output / 'replay.json', state)
        raise


def cleanup(output: Path) -> None:
    from execution import snapshot
    from benchmark_store import active
    with locked(output), ExitStack() as guards:
        execution = output / 'execution.json'
        if execution.exists() and active(load(execution)):
            raise BenchmarkError('Cannot clean an active driver')
        state = load(output / 'replay.json')
        if state['status'] == 'cleaned':
            return
        if 'worktree' in state:
            verify_ownership(output, state)
        if state.get('review_dir'):
            review_dir = Path(state['review_dir'])
            try:
                guards.enter_context(ReviewState.classifier_locked(review_dir))
                review = guards.enter_context(ReviewState.locked(review_dir))
            except ReviewStateError as exc:
                raise BenchmarkError(f'Active or unavailable review session: {exc}') from exc
            for item in review.data['slices'].values():
                pending = item.get('judge_pending')
                records = [r for r in item['runs'] if r['status'] == 'running'] + ([pending] if pending else [])
                if any(_running_reservation_is_active(r) for r in records):
                    raise BenchmarkError('Cannot clean an active review session')
        if 'worktree' in state and Path(state['worktree']).is_dir():
            archive = output / 'cleanup-evidence'
            archive.mkdir(exist_ok=True)
            snapshot(state, archive)
            save(archive / 'code-state.json', {
                'head': git(Path(state['worktree']), 'rev-parse', 'HEAD').decode().strip(),
                'index_tree': git(Path(state['worktree']), 'write-tree').decode().strip()})
        _remove_owned_worktree(output, state)


def _remove_owned_worktree(output: Path, state: dict) -> None:
    state = load(output / 'replay.json')
    if 'worktree' in state:
        store, worktree = verify_ownership(output, state)
        if worktree.exists():
            git(store, 'worktree', 'remove', '--force', str(worktree))
    else:
        store = output.resolve() / 'history'
        if state['owned'] and state['owned'] != [str(store)]:
            raise BenchmarkError('Unexpected partial ownership record')
    state['status'] = 'cleaned'
    save(output / 'replay.json', state)


def main() -> None:
    parser = argparse.ArgumentParser(description='Prepare one exact historical review replay')
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--head', required=True)
    parser.add_argument('--base')
    parser.add_argument('--task-file', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--variant')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--benchmark-root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.head, args.task_file.read_text(), args.config,
                             args.output, base=args.base, variant=args.variant, benchmark_root=args.benchmark_root)))


if __name__ == '__main__':
    main()
