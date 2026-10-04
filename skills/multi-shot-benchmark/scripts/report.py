from __future__ import annotations

import json
import secrets
from pathlib import Path

from benchmark_store import BenchmarkError, digest, load, now, save
from comparison import measure


DECISION_FIELDS = {'basis', 'recommendation', 'reasoning', 'priorities', 'uncertainty', 'evidence', 'case_notes'}


def basis(measurements: dict) -> str:
    value = {k: v for k, v in measurements.items() if k != 'measured_at'}
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def decision(value: dict | None, measurements: dict) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != DECISION_FIELDS or value['basis'] != basis(measurements):
        raise BenchmarkError('Recommendation must name the current measurement basis and all decision fields')
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ('recommendation', 'reasoning', 'priorities', 'uncertainty')):
        raise BenchmarkError('Recommendation, reasoning, priorities, and uncertainty must be nonempty text')
    if not isinstance(value['evidence'], list) or not value['evidence'] or not all(isinstance(v, str) and v.strip() for v in value['evidence']):
        raise BenchmarkError('Recommendation needs evidence references')
    names = {c['name'] for c in measurements['cases']}
    if not isinstance(value['case_notes'], dict) or set(value['case_notes']) != names or not all(isinstance(v, str) and v.strip() for v in value['case_notes'].values()):
        raise BenchmarkError('Recommendation needs a contextual note for every case')
    return value


def display(value) -> str:
    if value is None:
        return 'unknown'
    return str(round(value, 6)) if isinstance(value, float) else str(value)


def cell(value) -> str:
    return display(value).replace('|', '\\|').replace('\n', '<br>')


def table(headers: list[str], rows: list[list]) -> list[str]:
    return ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |',
            *['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows], '']


def coverage_text(value: dict) -> str:
    return f"{display(value['total'])}; known subtotal {display(value['known_subtotal'])}; {value['known_records']}/{value['total_records']} records"


def role_table(roles: dict) -> list[str]:
    return table(['Role', 'Records', 'Input', 'Cache read', 'Cache write', 'Output', 'Seconds', 'API seconds', 'Unpriced summary tokens'],
                 [[role, r['records'], *[coverage_text(r['tokens'][f]) for f in ('input', 'cached_input', 'cache_write', 'output')],
                   coverage_text(r['seconds']), coverage_text(r['api_seconds']), coverage_text(r['reported_summary_tokens'])]
                  for role, r in roles.items()])


def finding_table(rows: list[dict]) -> list[str]:
    return table(['ID', 'Severity', 'Title', 'Description', 'Location', 'Disposition', 'First pass', 'Repeated', 'Chain', 'Source'],
                 [[r['id'], r['severity'], r['title'], r['content'],
                   f"{r['location']['path']}:{r['location']['start_line']}-{display(r['location']['end_line'])}",
                   r['disposition'], r['first_pass'], r['repeated'],
                   ' → '.join(r['resolution_chain']), r['source']] for r in rows])


def render(value: dict) -> str:
    data, recommendation = value['measurements'], value['decision']
    rows = data['executions']
    lines = ['# Historical review benchmark', '', f"Report status: {value['status']}. Created: {value['created_at']}.", '',
             f"Repository: `{data['repository']}`. Measurement basis: `{value['basis']}`.", '',
             f"Manifest: `{data['manifest']}`. Full evidence and parent assessments: `report.json` in this report directory.", '',
             '## Parent recommendation', '']
    if recommendation:
        for key in ('recommendation', 'reasoning', 'priorities', 'uncertainty'):
            lines += [f"**{key.capitalize()}:** {recommendation[key]}", '']
        lines += ['Evidence: ' + '; '.join(recommendation['evidence']), '']
    else:
        lines += ['Pending. The parent must assess final code and write a reasoned recommendation. An inconclusive decision is valid.', '']
    lines += ['## Coverage and limits', '',
              f"Executions: {len(rows)}. Completed: {sum(r['status'] == 'completed' for r in rows)}. Complete pairs: {sum(p['complete'] for p in data['paired'])}/{len(data['paired'])}.", '',
              f"Current assessments: {sum(r['assessment']['status'] == 'current' for r in rows)}. Missing or stale quality evidence remains unknown.", '']
    lines += [f'- {item}' for item in value['limitations']] + ['']
    lines += ['## Arm comparison', '']
    lines += table(['Arm', 'Completed / executions', 'First findings', 'Full findings', 'Retained chains', 'Residual defects', 'Regressions', 'Cost known subtotal', 'Fully costed', 'Workflow seconds'],
                   [[name, f"{r['completed']}/{r['executions']}", r['findings']['first_pass']['total'], r['findings']['full_workflow']['total'],
                     r['findings']['full_workflow']['unique_retained_chains'], r['quality']['confirmed_residual_defects'], r['quality']['confirmed_regressions'],
                     r['cost']['known_subtotal'], r['cost']['fully_costed_executions'], coverage_text(r['workflow_elapsed_seconds'])]
                    for name, r in data['arm_totals'].items()])
    for name, aggregate in data['arm_totals'].items():
        lines += [f'### Arm {name}', '']
        lines += role_table(aggregate['roles'])
        lines += ['Effort totals: ' + json.dumps(aggregate['effort_totals'], sort_keys=True), '',
                  'Severity and dispositions: ' + json.dumps({'severity': aggregate['severity'], 'dispositions': aggregate['dispositions']}, sort_keys=True), '',
                  'Quality coverage: ' + json.dumps(aggregate['quality'], sort_keys=True), '']
    lines += ['## Paired comparisons', '']
    for pair in data['paired']:
        lines += [f"- {pair['case']}, repetition {pair['repetition']}: complete={pair['complete']}; executions={json.dumps(pair['executions'])}; deltas={json.dumps(pair['deltas'], sort_keys=True)}"]
    lines += ['', '## Cases and repetitions', '']
    for case in data['cases']:
        lines += [f"### {case['name']}", '', f"Base: `{case['base']}`. Head: `{case['head']}`.", '',
                  'Task provenance: ' + json.dumps(case['provenance'], sort_keys=True), '',
                  'Task difficulty and case selection: ' + (recommendation['case_notes'][case['name']] if recommendation else 'Parent interpretation pending. Read the frozen task and patch.'), '']
        for row in [r for r in rows if r['case'] == case['name']]:
            lines += [f"#### {row['arm']}, repetition {row['repetition']} — {row['id']}", '',
                      f"Status: {row['status']}; completion: {display(row['completion_reason'])}; finding coverage: {row['finding_coverage']}.", '',
                      'Task: ' + (row['context']['task'] or 'unknown'), '',
                      'First pass: ' + json.dumps(row['first_pass'], sort_keys=True), '',
                      'Full process: ' + json.dumps(row['full_workflow'], sort_keys=True), '',
                      'Rounds and attempts: ' + json.dumps(row['effort'], sort_keys=True), '',
                      'Workflow time: ' + json.dumps(row['workflow_time'], sort_keys=True), '']
            lines += finding_table(row['findings']) + role_table(row['roles'])
            lines += ['Cost: ' + json.dumps(row['cost'], sort_keys=True), '',
                      'Parent final-code assessment: ' + json.dumps(row['assessment'], sort_keys=True), '',
                      'Final validated checks: ' + json.dumps(row['checks'], sort_keys=True), '',
                      'All attempt checks and fix claims: ' + json.dumps(row['attempt_results'], sort_keys=True), '',
                      'Unfinished coverage: ' + json.dumps({'slices': row['unfinished_slices'], 'classifiers': row['unfinished_classifications'], 'judges': row['unfinished_judges']}, sort_keys=True), '',
                      'Saved errors: ' + json.dumps({'diagnostics': row['diagnostics'], 'errors': row['errors']}, sort_keys=True), '',
                      'Resolution claims and evidence: ' + json.dumps([{k: f[k] for k in ('id', 'driver_resolution', 'driver_claims')} for f in row['findings']], sort_keys=True), '',
                      'Review runs and judge verdicts: ' + json.dumps({'runs': row['runs'], 'judgements': row['judgements']}, sort_keys=True), '',
                      'Evidence sources: ' + json.dumps(row['sources'], sort_keys=True), '']
    return '\n'.join(lines) + '\n'


def build(output: Path, *, recommendation: dict | None = None, rates_file: Path | None = None) -> dict:
    measurements = measure(output, rates_file=rates_file)
    selected = decision(recommendation, measurements)
    unassessed = [r['id'] for r in measurements['executions'] if r['status'] == 'completed' and r['assessment']['status'] != 'current']
    if selected and unassessed:
        raise BenchmarkError('Assess completed final artifacts before the final recommendation: ' + ', '.join(unassessed))
    value = {'schema': 1, 'created_at': now(), 'basis': basis(measurements), 'status': 'final' if selected else 'draft',
             'decision': selected, 'measurements': measurements,
             'limitations': [*measurements['limitations'],
                 'First-pass findings and full-process findings measure observed decisions, not ground-truth recall.',
                 'Classification can differ across arms. Compare slice definitions and failed coverage in the saved review states.',
                 'GitHub descriptions can contain later edits. Supplied and recovered task gaps affect interpretation.',
                 'Role times overlap. Workflow wall spans include retry waits and host load.',
                 'Discuss disputed findings, final regressions, missing usage, and project priorities before selecting an arm.']}
    directory = output / 'reports' / (value['created_at'].replace(':', '').replace('+', '-') + '-' + secrets.token_hex(3))
    directory.mkdir(parents=True)
    save(directory / 'report.json', value)
    (directory / 'report.md').write_text(render(value))
    save(output / 'report.json', {'directory': str(directory), 'basis': value['basis'], 'status': value['status']})
    return {'directory': str(directory), 'status': value['status'], 'basis': value['basis']}
