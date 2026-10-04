from __future__ import annotations

from pathlib import Path

from benchmark_store import BenchmarkError, digest, load, save, now
from ownership import locked


def artifact(output: Path) -> dict | None:
    path = output / 'completion.json'
    if not path.is_file():
        return None
    value = load(path)
    files = {}
    for name in ('completion.json', 'final.patch', 'fixes.patch'):
        file = output / name
        if not file.is_file():
            return None
        files[name] = digest(file.read_bytes())
    return {'final_tree': value['final_tree'], 'files': files}


def validate(value: dict) -> None:
    required = {'summary', 'evidence', 'residual_defects', 'regressions', 'disputed_findings'}
    if not isinstance(value, dict) or set(value) != required or not isinstance(value['summary'], str) or not value['summary'].strip():
        raise BenchmarkError('Assessment needs summary, evidence, residual_defects, regressions, and disputed_findings')
    def evidence(items):
        return isinstance(items, list) and bool(items) and all(isinstance(x, str) and x.strip() for x in items)
    if not evidence(value['evidence']):
        raise BenchmarkError('Assessment needs source evidence')
    for field in ('residual_defects', 'regressions'):
        if not isinstance(value[field], list):
            raise BenchmarkError('Assessment defects and regressions must be lists')
        for item in value[field]:
            if not isinstance(item, dict) or set(item) != {'severity', 'description', 'evidence'} or item['severity'] not in ('P0', 'P1', 'P2', 'P3') or not isinstance(item['description'], str) or not item['description'].strip() or not evidence(item['evidence']):
                raise BenchmarkError('Each assessed defect needs severity, description, and evidence')
    if not isinstance(value['disputed_findings'], list):
        raise BenchmarkError('Disputed findings must be a list')
    for item in value['disputed_findings']:
        if not isinstance(item, dict) or set(item) != {'id', 'reason', 'evidence'} or not all(isinstance(item[k], str) and item[k].strip() for k in ('id', 'reason')) or not evidence(item['evidence']):
            raise BenchmarkError('Each disputed finding needs its ID, reason, and evidence')


def record(output: Path, value: dict) -> dict:
    validate(value)
    output = output.resolve()
    with locked(output):
        state = load(output / 'execution.json') if (output / 'execution.json').is_file() else {'status': 'pending'}
        snapshot = artifact(output)
        if state['status'] != 'completed' or snapshot is None:
            raise BenchmarkError('Assess only a completed execution with saved final code evidence')
        replay = load(output / 'replay.json')
        review = load(Path(replay['review_dir']) / '_state.json')
        ids = set()
        for item in review['slices'].values():
            for run in item['runs']:
                findings = load(Path(run['findings_archive']))['findings'] if run.get('findings_archive') else run.get('findings') or []
                ids.update(f['id'] for f in findings)
        if any(item['id'] not in ids for item in value['disputed_findings']):
            raise BenchmarkError('Disputed findings must name findings from this execution')
        result = {'schema': 1, 'assessed_at': now(), 'artifact': snapshot, **value}
        save(output / 'assessment.json', result)
        return result


def read(output: Path) -> dict:
    path = output / 'assessment.json'
    if not path.is_file():
        return {'status': 'missing', 'residual_defects': None, 'regressions': None, 'disputed_findings': None}
    value = load(path)
    validate({k: value[k] for k in ('summary', 'evidence', 'residual_defects', 'regressions', 'disputed_findings')})
    return {**value, 'status': 'current' if value['artifact'] == artifact(output) else 'stale', 'source': str(path)}
