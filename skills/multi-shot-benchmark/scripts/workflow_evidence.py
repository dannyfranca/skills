from __future__ import annotations

from pathlib import Path
import re

from benchmark_store import BenchmarkError, active, digest, load
from completion import validate_result
from replay import REVIEW, ReviewState, ReviewStateError, _running_reservation_is_active
from usage import elapsed, coverage


def evidence(path: Path) -> dict:
    return {'path': str(path), 'sha256': digest(path.read_bytes())}


def guard_snapshot(output: Path, guards) -> None:
    execution = load(output / 'execution.json') if (output / 'execution.json').is_file() else {}
    if active(execution):
        raise BenchmarkError('Cannot measure an active driver')
    replay = load(output / 'replay.json') if (output / 'replay.json').is_file() else {}
    if not replay.get('review_dir'):
        if execution.get('attempts') or (output / 'completion.json').is_file():
            raise BenchmarkError('Required replay metadata is missing')
        return
    tool = output / 'tool/multi-shot-review'
    expected = replay.get('tool_hashes')
    if not expected:
        raise BenchmarkError('Pinned review tool hashes are missing')
    for name, value in expected.items():
        path = (tool / name).resolve()
        if not path.is_relative_to(tool.resolve()) or not path.is_file() or digest(path.read_bytes()) != value:
            raise BenchmarkError('Pinned review tool changed or is missing')
    review_dir = Path(replay['review_dir']).resolve()
    if not review_dir.is_relative_to((output / 'sessions').resolve()) or not (review_dir / '_state.json').is_file():
        raise BenchmarkError('Required owned review state is missing')
    try:
        guards.enter_context(ReviewState.classifier_locked(review_dir))
        state = guards.enter_context(ReviewState.locked(review_dir))
    except ReviewStateError as exc:
        raise BenchmarkError(f'Active or unavailable review state: {exc}') from exc
    for item in state.data['slices'].values():
        pending = item.get('judge_pending')
        records = [r for r in item['runs'] if r['status'] == 'running'] + ([pending] if pending else [])
        if any(_running_reservation_is_active(r) for r in records):
            raise BenchmarkError('Cannot measure active review or judge work')


def check_evidence(check: dict, output: Path) -> dict:
    path = Path(check['log']).resolve()
    owned = path.is_relative_to(output.resolve())
    actual = digest(path.read_bytes()) if owned and path.is_file() else None
    expected = check.get('sha256')
    return {**check, 'current_sha256': actual,
            'evidence_status': 'outside-owned-output' if not owned else 'missing' if actual is None else
            'verified' if expected == actual else 'changed' if expected else 'driver-claim'}


def attempt_checks(execution: dict, output: Path) -> list[dict]:
    result = []
    for attempt in execution.get('attempts', []):
        path = Path(attempt['directory']) / 'result.json'
        row = {'attempt': attempt['number'], 'status': attempt['status'], 'source': None, 'checks': [], 'resolutions': [], 'error': None}
        if not path.resolve().is_relative_to(output.resolve()):
            raise BenchmarkError('Driver result must belong to its execution')
        source, value = path, None
        if path.is_file():
            try:
                value = load(path)
            except ValueError as exc:
                row['error'] = str(exc)
        elif execution.get('driver', {}).get('harness') == 'claude-code':
            source = path.parent / 'stdout.log'
            if source.is_file():
                try:
                    native = load(source)
                    value = native.get('structured_output') if isinstance(native, dict) else None
                except ValueError as exc:
                    row['error'] = str(exc)
        if source.is_file():
            row['source'] = evidence(source)
        if value is not None:
            try:
                validate_result(value)
                row['resolutions'] = value['resolutions']
                row['checks'] = [check_evidence(c, output) for c in value['checks']]
            except (BenchmarkError, ValueError, TypeError) as exc:
                row['error'] = str(exc)
        elif row['error'] is None:
            row['error'] = 'Driver result is missing'
        result.append(row)
    return result


def diagnostics(review_dir: Path | None, execution: dict, review: dict) -> list[dict]:
    rows = []
    for attempt in execution.get('attempts', []):
        if attempt.get('error'):
            rows.append({'role': 'driver', 'id': attempt['number'], 'message': attempt['error']})
    for item in review.get('classifications', []):
        if item['status'] == 'failed':
            rows.append({'role': 'classifier', 'id': item['id'], 'message': item.get('error') or 'Classification failed'})
    for item in review.get('slices', {}).values():
        for run in item['runs']:
            if run.get('error'):
                rows.append({'role': 'reviewer', 'id': run['id'], 'message': run['error']})
    path = review_dir / '_errors.md' if review_dir else None
    if path and path.is_file():
        for block in re.split(r'(?m)(?=^## )', path.read_text()):
            if block.strip():
                rows.append({'role': 'review', 'message': block.strip(), 'source': evidence(path)})
    if review_dir:
        for path in sorted((review_dir / '_logs').glob('judge-*.stderr.log')):
            text = path.read_text(errors='replace')
            if '[runner]' in text and any(word in text.lower() for word in ('exited', 'failed', 'invalid', 'error')):
                rows.append({'role': 'judge', 'id': path.stem, 'message': text.strip(), 'source': evidence(path)})
    return rows


def workflow_time(execution: dict) -> dict:
    attempts = execution.get('attempts', [])
    intervals = [elapsed(a.get('started_at'), a.get('ended_at')) for a in attempts]
    return {'elapsed_seconds': elapsed(attempts[0].get('started_at'), attempts[-1].get('ended_at')) if attempts else None,
            'driver_lifecycle_seconds': coverage(intervals),
            'basis': 'Wall span from first driver start to last recorded driver end, including waits between retries; child role intervals overlap'}


def judge_scope(path: Path):
    match = re.fullmatch(r'judge-\d{8}-\d{4}-(\d+)-(.+)-[0-9a-f]{6}\.stdout\.log', path.name)
    return (int(match[1]), match[2]) if match else None


def match_judgements(review_dir: Path, logs: list[Path], judgements: list[dict], saved_errors: list[dict]) -> dict[int, Path]:
    matched, used = {}, set()
    for index, judgement in enumerate(judgements):
        safe = re.sub(r'[^a-zA-Z0-9._-]+', '-', judgement['slice'])
        for path in logs:
            if path in used:
                continue
            stem = path.name.removeprefix('judge-').removesuffix('.stdout.log')
            output = review_dir / 'judge' / f'{stem}.json'
            stderr = path.with_name(path.name.replace('.stdout.log', '.stderr.log'))
            failed = stderr.is_file() and '[runner]' in stderr.read_text(errors='replace')
            failed |= any(str(output) in d['message'] for d in saved_errors)
            if failed or not output.is_file() or judge_scope(path) != (judgement['pass'], safe):
                continue
            try:
                value = load(output)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get('verdict') == judgement['verdict'] and value.get('reason') == judgement['reason']:
                matched[index] = path
                used.add(path)
                break
    return matched


def pending_judges(review: dict, logs: list[Path], matched: dict[int, Path]) -> list[dict]:
    used = set(matched.values())
    result = []
    for name, item in review.get('slices', {}).items():
        pending = item.get('judge_pending')
        if not pending:
            continue
        safe = re.sub(r'[^a-zA-Z0-9._-]+', '-', name)
        candidates = [p for p in logs if p not in used and judge_scope(p) == (pending['pass'], safe)]
        path = candidates[-1] if len(candidates) == 1 else None
        if path:
            used.add(path)
        result.append({'slice': name, **pending, 'log': str(path) if path else None,
                       'accounting_link': 'Unique unmatched slice/pass log; reservation IDs are absent from logs' if path else 'Missing or ambiguous log'})
    return result
