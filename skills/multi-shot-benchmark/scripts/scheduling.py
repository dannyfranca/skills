from __future__ import annotations

import fcntl
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from threading import Event
import shutil
import inspect

from benchmark_store import BenchmarkError, load, save, now, active
from execution import run as run_execution
from manifest import read_manifest
from replay import _prepare as prepare, _resume as resume, cleanup as cleanup_replay
from ownership import locked
from storage import shared_storage


@contextmanager
def run_lock(output: Path):
    with (output / '.run.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BenchmarkError('Another scheduler or cleanup owns this run') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def progress(output: Path) -> dict:
    manifest = read_manifest(output)
    rows = []
    schedule = load(output / 'schedule.json') if (output / 'schedule.json').exists() else {'invocations': []}
    setup_errors = {}
    for invocation in schedule['invocations']:
        for identity in invocation.get('prepared', []):
            setup_errors.pop(identity, None)
        for error in invocation['setup_errors']:
            setup_errors[error['id']] = error['error']
    for entry in manifest['assignments']:
        directory = output / 'executions' / entry['id']
        execution = load(directory / 'execution.json') if (directory / 'execution.json').exists() else {}
        replay = load(directory / 'replay.json') if (directory / 'replay.json').exists() else {}
        status = execution.get('status', replay.get('status', 'pending'))
        if status == 'running' and not active(execution):
            status = 'interrupted'
        error = execution.get('error', replay.get('error'))
        if entry['id'] in setup_errors and status != 'completed':
            status, error = 'setup-failed', setup_errors[entry['id']]
        rows.append({**entry, 'status': status,
                     'driver_error': execution.get('error'), 'execution_status': execution.get('status'),
                     'attempts': len(execution.get('attempts', [])), 'output': str(directory),
                     'cleaned': replay.get('status') == 'cleaned', 'error': error if status not in ('ready', 'completed') else None})
    return {'repository': manifest['repository'], 'executions': rows,
            'completed': sum(r['status'] == 'completed' for r in rows), 'total': len(rows)}


def reconcile_launch(launch: dict, evidence: dict, *, fallback: str = 'interrupted') -> None:
    expected = launch.get('expected_attempt', launch.get('attempt'))
    selected = launch.get('attempt') or expected
    attempts = evidence.get('attempts', [])
    attempt = next((a for a in attempts if a['number'] == selected), None)
    if attempt is None and attempts and evidence.get('status') == 'completed':
        previous = attempts[-1]
        if expected is not None and previous['number'] < expected and previous['status'] == 'completed':
            attempt = previous
    actual = attempt or {}
    status = actual.get('status', fallback)
    if status == 'running' and not active(evidence):
        status = 'interrupted'
    launch.update(attempt=actual.get('number'), status=status,
                  driver_started_at=actual.get('started_at'), driver_ended_at=actual.get('ended_at'),
                  ended_at=actual.get('ended_at'), end_time_unknown=actual.get('ended_at') is None,
                  recovered_without_launch=actual.get('number') is not None and expected is not None and
                  actual['number'] < expected)


def launch_evidence(output: Path, launch: dict) -> dict:
    path = output / 'executions' / launch['id'] / 'execution.json'
    return load(path) if path.exists() else {}


def reconcile_records(output: Path, schedule: dict) -> None:
    for invocation in schedule['invocations']:
        for launch in invocation['launches']:
            if launch.get('attempt') is not None:
                reconcile_launch(launch, launch_evidence(output, launch))


def run(output: Path, *, executor=run_execution) -> dict:
    output = output.resolve()
    manifest = read_manifest(output)
    with run_lock(output), shared_storage(Path(manifest['benchmark_root']), output):
        cases = {c['name']: c for c in manifest['cases']}
        arms = {a['name']: a for a in manifest['arms']}
        path = output / 'schedule.json'
        schedule = load(path) if path.exists() else {'invocations': []}
        for old in schedule['invocations']:
            if old['status'] == 'running':
                old.update(status='interrupted', ended_at=now())
                for launch in old['launches']:
                    if launch['status'] != 'running':
                        continue
                    reconcile_launch(launch, launch_evidence(output, launch))
        reconcile_records(output, schedule)
        invocation = {'started_at': now(), 'ended_at': None, 'launches': [], 'setup_errors': [], 'prepared': [], 'status': 'running'}
        schedule['invocations'].append(invocation)
        save(path, schedule)
        ready = set()
        for entry in manifest['assignments']:
            directory = output / 'executions' / entry['id']
            if (directory / 'execution.json').exists() and load(directory / 'execution.json')['status'] == 'completed':
                continue
            case, arm = cases[entry['case']], arms[entry['arm']]
            try:
                with locked(directory):
                    if (directory / 'replay.json').exists() and load(directory / 'replay.json')['status'] == 'cleaned':
                        raise BenchmarkError('Cleaned unfinished executions cannot resume')
                    if directory.exists() and (not (directory / 'replay.json').exists() or
                                               'asset_hashes' not in load(directory / 'replay.json')):
                        archive = output / 'setup-failures' / entry['id'] / str(len(schedule['invocations']))
                        archive.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(directory), str(archive))
                    if (directory / 'replay.json').exists():
                        resume(directory)
                    else:
                        prepare(Path(manifest['repository']), case['head'], (output / case['task_file']).read_text(),
                                output / arm['config'], directory, base=case['base'], variant=arm['variant'],
                                worktree_root=Path(manifest['worktree_root']), benchmark_root=Path(manifest['benchmark_root']),
                                tool_snapshot=output / 'tool/multi-shot-review', run_root=output)
                ready.add(entry['id'])
                invocation['prepared'].append(entry['id'])
                save(path, schedule)
            except Exception as exc:
                invocation['setup_errors'].append({'id': entry['id'], 'error': str(exc), 'at': now()})
                save(path, schedule)
            except BaseException:
                invocation.update(status='interrupted', ended_at=now())
                save(path, schedule)
                raise
        groups = {}
        for entry in manifest['assignments']:
            groups.setdefault((entry['case'], entry['repetition']), []).append(entry)
        cancellation = Event()
        try:
            for entries in groups.values():
                pending = [entry for entry in entries if entry['id'] in ready]
                with ThreadPoolExecutor(max_workers=len(arms)) as pool:
                    futures = {}
                    try:
                        for entry in pending:
                            execution_path = output / 'executions' / entry['id'] / 'execution.json'
                            attempts = load(execution_path)['attempts'] if execution_path.exists() else []
                            launch = {'id': entry['id'], 'expected_attempt': len(attempts) + 1, 'attempt': None, 'submitted_at': now(),
                                      'ended_at': None, 'status': 'running'}
                            invocation['launches'].append(launch)
                            save(path, schedule)
                            options = {'cancellation': cancellation} if 'cancellation' in inspect.signature(executor).parameters else {}
                            futures[pool.submit(executor, output / 'executions' / entry['id'], manifest['driver'], **options)] = launch
                        for future in as_completed(futures):
                            launch = futures[future]
                            try:
                                state = future.result()
                                reconcile_launch(launch, state, fallback=state['status'])
                            except Exception as exc:
                                launch['error'] = str(exc)
                                reconcile_launch(launch, launch_evidence(output, launch), fallback='failed')
                            launch['scheduler_observed_at'] = now()
                            save(path, schedule)
                    except BaseException:
                        cancellation.set()
                        for future, launch in futures.items():
                            try:
                                state = future.result()
                                reconcile_launch(launch, state, fallback=state['status'])
                            except BaseException as exc:
                                launch['error'] = str(exc)
                                reconcile_launch(launch, launch_evidence(output, launch))
                            launch['scheduler_observed_at'] = now()
                        for launch in invocation['launches']:
                            if launch['status'] == 'running':
                                reconcile_launch(launch, launch_evidence(output, launch))
                        save(path, schedule)
                        raise
            invocation['status'] = 'completed' if progress(output)['completed'] == len(manifest['assignments']) else 'partial'
        except BaseException:
            invocation['status'] = 'interrupted'
            raise
        finally:
            reconcile_records(output, schedule)
            invocation['ended_at'] = now()
            save(path, schedule)
        return progress(output)


def cleanup(output: Path) -> dict:
    output = output.resolve()
    with run_lock(output):
        manifest = read_manifest(output)
        for entry in manifest['assignments']:
            directory = output / 'executions' / entry['id']
            if (directory / 'replay.json').is_file():
                cleanup_replay(directory)
        return progress(output)
