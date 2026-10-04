from __future__ import annotations

import subprocess
import tarfile
import os
from pathlib import Path

from benchmark_store import BenchmarkError, git, load, save, now, active, process_key, diff
from isolation import child_command
from replay import _resume as resume
from ownership import locked
from storage import shared_storage
from driver import driver_command, driver_prompt, validate_profile
from completion import completion


def run(output: Path, profile: dict, *, command_factory=driver_command, cancellation=None) -> dict:
    profile = validate_profile(profile)
    output = output.resolve()
    with locked(output), shared_storage(Path(load(output / 'replay.json')['benchmark_root']), output):
        replay = resume(output)
        path = output / 'execution.json'
        state = load(path) if path.exists() else {'schema': 1, 'status': 'pending', 'driver': profile, 'attempts': []}
        if state['driver'] != profile:
            raise BenchmarkError('Resume requires the original driver profile')
        if active(state):
            raise BenchmarkError('An existing driver is still active')
        if state['status'] == 'completed':
            return state
        if state['attempts']:
            previous = state['attempts'][-1]
            directory = Path(previous.get('directory', output / 'missing'))
            exit_evidence = load(directory / 'exit.json') if (directory / 'exit.json').is_file() else None
            if exit_evidence:
                previous.update(exit_code=exit_evidence['exit_code'], ended_at=exit_evidence.get('ended_at'),
                                status='failed')
                state['status'] = 'failed'
            if exit_evidence and exit_evidence['exit_code'] == 0:
                try:
                    final = finish(replay, profile, directory, output)
                except (BenchmarkError, KeyError, ValueError, OSError):
                    pass
                else:
                    previous.update(status='completed', exit_code=exit_evidence['exit_code'],
                                    ended_at=exit_evidence.get('ended_at'))
                    state.update(status='completed', completion=final)
                    state.pop('error', None)
                    save(path, state)
                    return state
        if state['status'] == 'running':
            state['status'] = 'interrupted'
            state['attempts'][-1].update(status='interrupted', recovered_at=now())
        number = len(state['attempts']) + 1
        attempt_dir = output / 'driver' / str(number)
        attempt = {'number': number, 'status': 'running', 'started_at': now(), 'ended_at': None,
                   'pid': None, 'process_key': None, 'directory': str(attempt_dir)}
        state['attempts'].append(attempt)
        state['status'] = 'running'
        save(path, state)
        proc = None
        try:
            attempt_dir.mkdir(parents=True)
            result = attempt_dir / 'result.json'
            schema = attempt_dir / 'schema.json'
            schema.write_bytes((Path(__file__).resolve().parents[1] / 'references/driver-result.schema.json').read_bytes())
            prompt = driver_prompt(output, replay)
            (attempt_dir / 'prompt.md').write_text(prompt)
            command = command_factory(profile, prompt, result, schema, Path(replay['worktree']))
            command = child_command(output, command)
            with (attempt_dir / 'stdout.log').open('wb') as stdout, (attempt_dir / 'stderr.log').open('wb') as stderr:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                attempt.update(pid=proc.pid, process_key=process_key(proc.pid))
                save(path, state)
                while True:
                    if cancellation is not None and cancellation.is_set():
                        raise KeyboardInterrupt('Scheduler cancelled this owned driver')
                    try:
                        code = proc.wait(timeout=0.2 if cancellation is not None else None)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            attempt.update(exit_code=code, ended_at=now())
            save(attempt_dir / 'exit.json', {'exit_code': code, 'ended_at': attempt['ended_at']})
            snapshot(replay, attempt_dir)
            if code:
                raise BenchmarkError(f'Driver exited with status {code}')
            final = finish(replay, profile, attempt_dir, output)
            state.update(status='completed', completion=final)
            attempt['status'] = 'completed'
            state.pop('error', None)
        except BaseException as exc:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            try:
                if attempt_dir.is_dir():
                    snapshot(replay, attempt_dir)
            except (BenchmarkError, OSError) as snapshot_error:
                attempt['snapshot_error'] = str(snapshot_error)
            status = 'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed'
            attempt.update(status=status, ended_at=now(), error=str(exc))
            state.update(status=status, error=str(exc))
            save(path, state)
            raise
        save(path, state)
        return state


def finish(replay: dict, profile: dict, directory: Path, output: Path) -> dict:
    result = directory / 'result.json'
    if profile['harness'] == 'claude-code' and not result.exists():
        envelope = load(directory / 'stdout.log')
        if not isinstance(envelope, dict) or not isinstance(envelope.get('structured_output'), dict):
            raise BenchmarkError('Claude result has no structured output')
        save(result, envelope['structured_output'])
    return completion(replay, load(result), output)


def snapshot(replay: dict, directory: Path) -> None:
    worktree = Path(replay['worktree'])
    (directory / 'code.patch').write_bytes(diff(worktree, replay['replay_base']))
    (directory / 'status.txt').write_bytes(git(worktree, 'status', '--porcelain=v1'))
    names = os.fsdecode(git(worktree, 'ls-files', '--others', '--exclude-standard', '-z')).split('\0')
    with tarfile.open(directory / 'untracked.tar', 'w') as archive:
        for name in filter(None, names):
            archive.add(worktree / name, arcname=name, recursive=False)
