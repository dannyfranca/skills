from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone


class BenchmarkError(RuntimeError):
    pass


def execute(args: list[str], *, cwd: Path | None = None, data: bytes | None = None) -> bytes:
    proc = subprocess.run(args, cwd=cwd, input=data, capture_output=True)
    if proc.returncode:
        raise BenchmarkError(f"{args[0]} failed: {proc.stderr.decode(errors='replace')}")
    return proc.stdout


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return execute(['git', '-C', str(repo), *args], data=data)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def resolve(repo: Path, ref: str) -> str:
    return git(repo, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}').decode().strip()


def instructions(repo: Path, head: str) -> dict[str, str]:
    names = os.fsdecode(git(repo, 'ls-tree', '-r', '--name-only', '-z', head)).split('\0')
    return {name: git(repo, 'show', f'{head}:{name}').decode(errors='replace') for name in names
            if Path(name).name in {'AGENTS.md', 'CLAUDE.md', 'CONTEXT.md', 'CONTEXT-MAP.md'}}


def tool_versions(skill: Path) -> dict:
    versions = {'git': execute(['git', '--version']).decode().strip()}
    for name in ('codex', 'claude'):
        try:
            versions[name] = execute([name, '--version']).decode().strip()
        except (OSError, BenchmarkError):
            versions[name] = None
    versions['review_scripts'] = {str(p.relative_to(skill)): digest(p.read_bytes())
                                   for p in sorted(skill.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    return versions


def import_history(repo: Path, store: Path, base: str) -> None:
    with tempfile.TemporaryFile() as errors:
        producer = subprocess.Popen(['git', '-C', str(repo), 'pack-objects', '--stdout', '--revs'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        try:
            producer.stdin.write((base + '\n').encode())
            producer.stdin.close()
            consumer = subprocess.run(['git', '-C', str(store), 'index-pack', '--stdin'],
                                      stdin=producer.stdout, capture_output=True)
            producer.stdout.close()
            result = producer.wait()
            if result or consumer.returncode:
                errors.seek(0)
                raise BenchmarkError('History import failed: ' +
                                     (errors.read() + consumer.stderr).decode(errors='replace'))
        finally:
            if producer.stdout is not None:
                producer.stdout.close()
            if producer.poll() is None:
                producer.terminate()
                producer.wait()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_key(pid: int) -> str | None:
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def active(state: dict) -> bool:
    attempt = state.get('attempts', [{}])[-1] if state.get('attempts') else {}
    return bool(attempt.get('pid') and attempt.get('process_key') and
                process_key(attempt['pid']) == attempt['process_key'])


def diff(repo: Path, *refs: str) -> bytes:
    return git(repo, 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv',
               '--no-color', '--src-prefix=a/', '--dst-prefix=b/', '--unified=3',
               '--inter-hunk-context=0', '--diff-algorithm=myers', '--no-indent-heuristic',
               '--submodule=short', '--ignore-submodules=none', '--no-relative', *refs)


def snapshot_tool(source: Path, target: Path) -> dict[str, str]:
    target.mkdir(parents=True)
    shutil.copyfile(source / 'SKILL.md', target / 'SKILL.md')
    for name in ('scripts', 'references'):
        shutil.copytree(source / name, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return {str(p.relative_to(target)): digest(p.read_bytes()) for p in target.rglob('*') if p.is_file()}
