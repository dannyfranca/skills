from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import json
import sys

from benchmark_store import BenchmarkError, digest, load, execute
from assessment import read as read_assessment
from usage import read_usage, elapsed, cost, coverage, TOKEN_FIELDS
from workflow_evidence import evidence, guard_snapshot, check_evidence, attempt_checks, diagnostics, workflow_time, ReviewState, match_judgements, pending_judges
from ownership import locked
from contextlib import ExitStack


def finding_rows(review: dict, review_dir: Path, completion: dict, attempts: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    fixes = {item['id']: item for item in completion.get('resolutions', [])}
    claims = {}
    for attempt in attempts or []:
        for claim in attempt['resolutions']:
            claims.setdefault(claim['id'], []).append({**claim, 'attempt': attempt['attempt'], 'attempt_status': attempt['status'], 'source': attempt['source']})
    for name, item in review.get('slices', {}).items():
        for run in item['runs']:
            findings = run.get('findings') or []
            archive = run.get('findings_archive')
            source = Path(run['output_file'])
            if archive:
                source = Path(archive).resolve()
                if not source.is_relative_to(review_dir.resolve()):
                    raise BenchmarkError('Finding archive must belong to this review session')
                saved = load(source)
                if saved['run_id'] != run['id']:
                    raise BenchmarkError('Finding archive does not match its run')
                findings = saved['findings']
            for finding in findings:
                resolution = finding.get('resolution') or {}
                kind = resolution.get('kind')
                fixed = fixes.get(finding['id'])
                observations = claims.get(finding['id'], [])
                link = fixed or (observations[-1] if observations else {})
                disposition = 'duplicate' if kind == 'duplicate' else 'rejected' if kind == 'rejected' else 'retained' if fixed else 'unresolved'
                rows.append({**finding, 'slice': name, 'run_id': run['id'], 'pass': run['pass'], 'shot': run['shot'],
                             'definition_version': run.get('definition_version', 1), 'disposition': disposition,
                             'driver_resolution': fixed, 'driver_claims': observations, 'source': str(source),
                             'first_pass': run['pass'] == 1 and run.get('definition_version', 1) == 1,
                             'repeated_of': link.get('repeated_of')})
    by_id = {r['id']: r for r in rows}
    if len(by_id) != len(rows):
        raise BenchmarkError('Finding IDs are not unique')
    for identity in (set(fixes) | set(claims)) - set(by_id):
        errors.append(f'Unknown resolution ID: {identity}')
    for row in rows:
        chain, current = [], row['id']
        while current is not None:
            if current in chain:
                errors.append(f'Repeated finding cycle: {row["id"]}')
                current = None
                break
            chain.append(current)
            if current not in by_id:
                errors.append(f'Unknown repeated finding ID: {current}')
                current = None
                break
            finding = by_id[current]
            following = finding['repeated_of'] or (finding.get('resolution') or {}).get('finding_id')
            if following is None:
                break
            current = following
        row.update(resolution_chain=chain, canonical_id=current, repeated=row['repeated_of'] is not None)
    return rows, errors


def findings_summary(rows: list[dict]) -> dict:
    return {'total': len(rows), 'by_severity': dict(Counter(r['severity'] for r in rows)),
            'by_disposition': dict(Counter(r['disposition'] for r in rows)),
            'severity_disposition': {severity: dict(Counter(r['disposition'] for r in rows if r['severity'] == severity))
                                     for severity in ('P0', 'P1', 'P2', 'P3')},
            'repeated': sum(r['repeated'] for r in rows),
            'unique_retained_chains': len({r['canonical_id'] for r in rows
                                          if r['disposition'] == 'retained' and r['canonical_id'] is not None})}


def usage_record(identity: str, role: str, profile: dict, stdout: Path, stderr: Path,
                 start=None, end=None, **context) -> dict:
    recovered = role == 'classifier' and context.get('status') in ('failed', 'running') or 'stale' in str(context.get('error', '')) or 'recovered' in str(context.get('classification', ''))
    usage = read_usage(stdout, stderr, profile.get('harness'))
    runtime = usage['runtime']
    reported = runtime['wall_seconds']['total']
    return {'id': identity, 'role': role, 'harness': profile.get('harness'), 'model': profile.get('model'),
            'reasoning': profile.get('reasoning'), 'started_at': start, 'ended_at': end,
            'seconds': reported if reported is not None else None if recovered else elapsed(start, end),
            'api_seconds': runtime['api_seconds']['total'], 'reported_interval_seconds': elapsed(start, end),
            'time_basis': 'Harness duration_ms' if reported is not None else 'Recorded lifecycle interval; recovery end times are uncertain',
            'usage': usage, **context}


def role_summary(records: list[dict]) -> dict:
    result = {}
    for role in ('driver', 'classifier', 'reviewer', 'judge'):
        items = [r for r in records if r['role'] == role]
        seconds = coverage([r['seconds'] for r in items])
        result[role] = {'records': len(items), 'usage_coverage': dict(Counter(r['usage']['coverage'] for r in items)),
                        'known_seconds': seconds['known_subtotal'], 'timed_records': seconds['known_records'],
                        'seconds': seconds, 'api_seconds': coverage([r['api_seconds'] for r in items]),
                        'reported_summary_tokens': coverage([sum(s['tokens'] for s in r['usage']['reported_summaries'])
                                                            if r['usage']['reported_summaries'] else None for r in items]),
                        'tokens': {f: coverage([r['usage']['tokens'][f] for r in items]) for f in TOKEN_FIELDS}}
    return result


def measure_execution(output: Path, assignment: dict, driver: dict, rates: dict | None = None) -> dict:
    with locked(output), ExitStack() as guards:
        guard_snapshot(output, guards)
        return _measure_execution(output, assignment, driver, rates)


def _measure_execution(output: Path, assignment: dict, driver: dict, rates: dict | None = None) -> dict:
    state_path, replay_path = output / 'execution.json', output / 'replay.json'
    execution = load(state_path) if state_path.is_file() else {}
    replay = load(replay_path) if replay_path.is_file() else {}
    completed = load(output / 'completion.json') if (output / 'completion.json').is_file() else {}
    review_dir = Path(replay['review_dir']) if replay.get('review_dir') else None
    review_path = review_dir / '_state.json' if review_dir else None
    review = {}
    if review_path and review_path.is_file():
        review = ReviewState.load(review_dir).data
    historical_checks = attempt_checks(execution, output)
    findings, errors = finding_rows(review, review_dir, completed, historical_checks) if review_dir else ([], [])
    records, run_rows, unfinished = [], [], []
    for attempt in execution.get('attempts', []):
        directory = Path(attempt['directory'])
        records.append(usage_record(f"driver-{attempt['number']}", 'driver', driver,
                                   directory / 'stdout.log', directory / 'stderr.log',
                                   attempt.get('started_at'), attempt.get('ended_at'), status=attempt['status']))
    for item in review.get('classifications', []):
        records.append(usage_record(item['id'], 'classifier', item,
                                   review_dir / '_logs' / f"classifier-{item['id']}.stdout.log",
                                   review_dir / '_logs' / f"classifier-{item['id']}.stderr.log",
                                   item.get('started_at'), item.get('ended_at'), status=item['status']))
    judge_logs = list((review_dir / '_logs').glob('judge-*.stdout.log')) if review_dir else []
    if review_dir:
        judge_logs += [p.with_name(p.name.replace('.stderr.log', '.stdout.log'))
                       for p in (review_dir / '_logs').glob('judge-*.stderr.log')
                       if not p.with_name(p.name.replace('.stderr.log', '.stdout.log')).exists()]
    for path in sorted(judge_logs):
        records.append(usage_record(path.stem, 'judge', {}, path,
                                   path.with_name(path.name.replace('.stdout.log', '.stderr.log')),
                                   diagnostic_source=evidence(path.with_name(path.name.replace('.stdout.log', '.stderr.log'))) if path.with_name(path.name.replace('.stdout.log', '.stderr.log')).is_file() else None,
                                   diagnostic_text=path.with_name(path.name.replace('.stdout.log', '.stderr.log')).read_text(errors='replace') if path.with_name(path.name.replace('.stdout.log', '.stderr.log')).is_file() else None,
                                   profile_limitation='Judge logs do not persist a per-attempt command profile or complete lifecycle timestamps'))
    judgements = []
    for name, item in review.get('slices', {}).items():
        safe = re.sub(r'[^a-zA-Z0-9._-]+', '-', name)
        for judgement in item.get('judgements', []):
            judgements.append({'slice': name, **judgement})
        if not item.get('removed') and not item.get('complete'):
            unfinished.append({'slice': name, 'next_pass': item.get('next_pass'), 'last_error': item.get('last_error')})
        for run in item['runs']:
            stem = f"{run['id']}-{run['pass']}-{safe}"
            records.append(usage_record(run['id'], 'reviewer', run,
                                       review_dir / '_logs' / f'{stem}.stdout.log', review_dir / '_logs' / f'{stem}.stderr.log',
                                       run.get('started_at'), run.get('ended_at'), status=run['status'],
                                       slice=name, pass_number=run['pass'], shot=run['shot'],
                                       error=run.get('error'), classification=run.get('classification')))
            run_rows.append({'slice': name, **{k: run.get(k) for k in ('id', 'pass', 'shot', 'definition_version', 'status', 'error', 'output_file')}})
    saved_diagnostics = diagnostics(review_dir, execution, review)
    matched = match_judgements(review_dir, judge_logs, judgements, saved_diagnostics) if review_dir else {}
    for index, path in matched.items():
        record = next(r for r in records if r['role'] == 'judge' and r['id'] == path.stem)
        record.update({key: judgements[index].get(key) for key in ('harness', 'model', 'reasoning')})
        record['usage'] = read_usage(path, path.with_name(path.name.replace('.stdout.log', '.stderr.log')), record['harness'])
    unfinished_judges = pending_judges(review, judge_logs, matched)
    for index, pending in enumerate(unfinished_judges):
        if pending['log']:
            record = next(r for r in records if r['role'] == 'judge' and r['id'] == Path(pending['log']).stem)
            record['pending_reservation'] = pending
        else:
            records.append(usage_record(f'judge-pending-{index + 1}', 'judge', {}, output / 'missing-judge.stdout', output / 'missing-judge.stderr',
                                       status='unfinished', pending_reservation=pending))
    for index, judgement in enumerate(judgements):
        if index not in matched:
            records.append(usage_record(f'judge-missing-{index + 1}', 'judge', judgement, output / 'missing-judge.stdout', output / 'missing-judge.stderr',
                                       profile_limitation='Judge verdict has no matched successful log attempt'))
    histories = review.get('history', [])
    waves = sum(h['event'] == 'run_reserved' and (i == 0 or histories[i - 1]['event'] != 'run_reserved')
                for i, h in enumerate(histories))
    passes = {(r['slice'], r.get('definition_version') or 1, r['pass']) for r in run_rows}
    shots = {(*p, r['shot']) for r in run_rows for p in [(r['slice'], r.get('definition_version') or 1, r['pass'])]}
    sources = [evidence(p) for p in (state_path, replay_path, output / 'completion.json', review_path) if p and p.is_file()]
    sources.extend(evidence(Path(r['findings_archive'])) for item in review.get('slices', {}).values()
                   for r in item['runs'] if r.get('findings_archive'))
    checks = [check_evidence(c, output) for c in completed.get('checks', [])]
    historical_checks = attempt_checks(execution, output)
    saved_diagnostics = diagnostics(review_dir, execution, review)
    patch = output / 'initial.patch'
    task = output / 'task.md'
    context = {'head': replay.get('head'), 'base': replay.get('base'), 'initial_tree': replay.get('initial_tree'),
               'task': task.read_text() if task.is_file() else None, 'config': replay.get('config'),
               'initial_patch': evidence(patch) if patch.is_file() else None}
    return {**assignment, 'output': str(output), 'status': execution.get('status', replay.get('status', 'pending')),
            'completion_reason': completed.get('reason'), 'driver_summary': completed.get('summary'),
            'findings': findings, 'first_pass': findings_summary([r for r in findings if r['first_pass']]),
            'full_workflow': findings_summary(findings),
            'effort': {'review_waves': waves if review else None, 'wave_basis': 'Contiguous run_reserved groups in saved history, including retry waves',
                       'slice_passes': len(passes), 'reviewer_shots': len(shots), 'review_attempts': len(run_rows),
                       'review_retries': len(run_rows) - len(shots), 'driver_attempts': len(execution.get('attempts', [])),
                       'driver_retries': max(0, len(execution.get('attempts', [])) - 1), 'judge_verdicts': len(judgements),
                       'judge_log_attempts': len(judge_logs), 'classification_attempts': len(review.get('classifications', []))},
            'runs': run_rows, 'driver_attempts': execution.get('attempts', []), 'judgements': judgements,
            'unfinished_judges': unfinished_judges, 'attempt_results': historical_checks,
            'unfinished_classifications': [c for c in review.get('classifications', []) if c['status'] == 'running'],
            'checks': checks, 'attempt_checks': historical_checks, 'diagnostics': saved_diagnostics,
            'workflow_time': workflow_time(execution), 'unfinished_slices': unfinished, 'context': context,
            'review_completed': review.get('completed'), 'errors': errors + [d['message'] for d in saved_diagnostics] + ([execution['error']] if execution.get('error') else []),
            'finding_coverage': 'available' if run_rows else 'not-started' if review else 'missing',
            'usage_records': records, 'roles': role_summary(records), 'cost': cost(records, rates),
            'assessment': read_assessment(output), 'sources': sources,
            'limitations': ['First pass means pass 1 of each initial slice definition, including its retries',
                            'Role times can overlap; driver time includes child time',
                            'Fixed resolutions are driver claims; parent assessment determines final quality',
                            'Review silence does not prove that the final code has no defects']}
