from __future__ import annotations

from pathlib import Path
from collections import Counter
from contextlib import ExitStack

from benchmark_store import now, load, save
from manifest import read_manifest
from metrics import _measure_execution, role_summary
from workflow_evidence import guard_snapshot
from scheduling import progress, run_lock
from usage import read_rates, coverage, TOKEN_FIELDS
from ownership import locked


def aggregate(rows: list[dict]) -> dict:
    complete = [r for r in rows if r['status'] == 'completed']
    assessed = [r for r in complete if r['assessment']['status'] == 'current']
    costed = [r['cost']['estimate'] for r in rows if r['cost']['estimate'] is not None]
    return {'executions': len(rows), 'completed': len(complete), 'status_counts': dict(Counter(r['status'] for r in rows)),
            'finding_coverage': dict(Counter(r['finding_coverage'] for r in rows)),
            'assessment_coverage': {'current': len(assessed), 'completed': len(complete)},
            'findings': {stage: {key: sum(r[stage][key] for r in rows)
                                 for key in ('total', 'repeated', 'unique_retained_chains')} for stage in ('first_pass', 'full_workflow')},
            'dispositions': {stage: dict(Counter({key: sum(r[stage]['by_disposition'].get(key, 0) for r in rows)
                                                for key in ('retained', 'rejected', 'duplicate', 'unresolved')}))
                             for stage in ('first_pass', 'full_workflow')},
            'severity': {stage: {key: sum(r[stage]['by_severity'].get(key, 0) for r in rows)
                                 for key in ('P0', 'P1', 'P2', 'P3')} for stage in ('first_pass', 'full_workflow')},
            'effort_totals': {key: coverage([r['effort'][key] for r in rows])
                              for key in ('review_waves', 'slice_passes', 'reviewer_shots', 'review_attempts', 'review_retries', 'driver_attempts', 'driver_retries')},
            'roles': role_summary([record for row in rows for record in row['usage_records']]),
            'workflow_elapsed_seconds': coverage([r['workflow_time']['elapsed_seconds'] for r in rows]),
            'cost': {'known_subtotal': sum(r['cost']['known_subtotal'] for r in rows if r['cost']['known_subtotal'] is not None)
                     if any(r['cost']['known_subtotal'] is not None for r in rows) else None,
                     'fully_costed_executions': len(costed), 'executions': len(rows),
                     'mean_fully_costed': sum(costed) / len(costed) if costed else None},
            'quality': {'confirmed_residual_defects': sum(len(r['assessment']['residual_defects']) for r in assessed) if assessed else None,
                        'confirmed_regressions': sum(len(r['assessment']['regressions']) for r in assessed) if assessed else None,
                        'residual_severity': dict(Counter(f['severity'] for r in assessed for f in r['assessment']['residual_defects'])) if assessed else None,
                        'regression_severity': dict(Counter(f['severity'] for r in assessed for f in r['assessment']['regressions'])) if assessed else None,
                        'assessed_executions': len(assessed)}}


def delta(value, baseline):
    return value - baseline if value is not None and baseline is not None else None


def paired(rows: list[dict], arms: list[str]) -> list[dict]:
    groups = {}
    for row in rows:
        groups.setdefault((row['case'], row['repetition']), {})[row['arm']] = row
    result = []
    for (case, repetition), entries in groups.items():
        finished = len(entries) == len(arms) and all(r['status'] == 'completed' for r in entries.values())
        baseline = entries.get(arms[0])
        deltas = []
        if finished:
            for arm in arms[1:]:
                row = entries[arm]
                deltas.append({'arm': arm, 'reference_arm': arms[0],
                               'retained_chains': row['full_workflow']['unique_retained_chains'] - baseline['full_workflow']['unique_retained_chains'],
                               'review_waves': row['effort']['review_waves'] - baseline['effort']['review_waves']
                               if row['effort']['review_waves'] is not None and baseline['effort']['review_waves'] is not None else None,
                               'estimated_cost': row['cost']['estimate'] - baseline['cost']['estimate']
                               if row['cost']['estimate'] is not None and baseline['cost']['estimate'] is not None else None,
                               'workflow_elapsed_seconds': delta(row['workflow_time']['elapsed_seconds'], baseline['workflow_time']['elapsed_seconds']),
                               'roles': {role: {'seconds': delta(row['roles'][role]['seconds']['total'], baseline['roles'][role]['seconds']['total']),
                                               'tokens': {f: delta(row['roles'][role]['tokens'][f]['total'], baseline['roles'][role]['tokens'][f]['total']) for f in TOKEN_FIELDS}}
                                         for role in row['roles']}})
        result.append({'case': case, 'repetition': repetition, 'complete': finished,
                       'executions': {arm: entries[arm]['id'] if arm in entries else None for arm in arms}, 'deltas': deltas})
    return result


def measure(output: Path, *, rates_file: Path | None = None) -> dict:
    output = output.resolve()
    with run_lock(output), ExitStack() as locks:
        manifest = read_manifest(output)
        for assignment in manifest['assignments']:
            locks.enter_context(locked(output / 'executions' / assignment['id']))
        for assignment in manifest['assignments']:
            guard_snapshot(output / 'executions' / assignment['id'], locks)
        rates = read_rates(rates_file)
        statuses = {r['id']: r for r in progress(output)['executions']}
        rows = []
        for assignment in manifest['assignments']:
            row = _measure_execution(output / 'executions' / assignment['id'], assignment, manifest['driver'], rates)
            status = statuses[assignment['id']]
            row.update(status=status['status'], cleaned=status['cleaned'])
            if status['error'] and status['error'] not in row['errors']:
                row['errors'].append(status['error'])
            rows.append(row)
        result = {'schema': 1, 'measured_at': now(), 'repository': manifest['repository'],
                  'manifest': str(output / 'manifest.json'), 'cases': manifest['cases'], 'arms': manifest['arms'],
                  'driver': manifest['driver'], 'tools': manifest['tools'], 'executions': rows,
                  'schedule': load(output / 'schedule.json') if (output / 'schedule.json').exists() else None,
                  'case_arms': [{'case': case['name'], 'arm': arm['name'], **aggregate([r for r in rows if r['case'] == case['name'] and r['arm'] == arm['name']])}
                                for case in manifest['cases'] for arm in manifest['arms']],
                  'arm_totals': {arm['name']: aggregate([r for r in rows if r['arm'] == arm['name']]) for arm in manifest['arms']},
                  'paired': paired(rows, [a['name'] for a in manifest['arms']]),
                  'limitations': [manifest['load_limits'], 'One repository per run; do not pool repositories without context',
                                  'Finding counts measure driver decisions, not verified defect recall',
                                  'Unequal completion and accounting coverage can bias aggregate means',
                                  'No fixed winner score is computed; the parent agent must explain tradeoffs']}
        save(output / 'measurements.json', result)
        return result
