from __future__ import annotations

from pathlib import Path
from benchmark_store import BenchmarkError, digest, git, load, save, now, diff


def completion(replay: dict, result: dict, output: Path) -> dict:
    validate_result(result)
    review_dir = Path(replay['review_dir'])
    from review_state import ReviewState
    review = ReviewState.load(review_dir).data
    if not review['completed'] or not any(not s.get('removed') for s in review['slices'].values()):
        raise BenchmarkError('Review did not reach its normal completion state')
    if not any(c.get('status') == 'succeeded' for c in review['classifications']):
        raise BenchmarkError('Successful classification evidence is missing')
    resolutions = {item['id']: item for item in result['resolutions']}
    findings = [f for s in review['slices'].values() for r in s['runs'] for f in
                (r.get('findings') or (load(Path(r['findings_archive']))['findings'] if r.get('findings_archive') else []))]
    unknown = set(resolutions) - {f['id'] for f in findings}
    if unknown:
        raise BenchmarkError(f'Unknown finding resolution IDs: {sorted(unknown)}')
    open_findings = [f for f in findings if not f.get('resolution') or f['resolution']['kind'] == 'superseded']
    for finding in open_findings:
        resolved = resolutions.get(finding['id'])
        if not resolved or resolved.get('outcome') != 'fixed' or not resolved.get('reason') or not resolved.get('evidence'):
            raise BenchmarkError(f'Unresolved terminal finding: {finding["id"]}')
    checks = [dict(check) for check in result['checks']]
    if not checks:
        raise BenchmarkError('Final check evidence is missing')
    for check in checks:
        log = Path(check['log']).resolve()
        if check['exit_code'] != 0 or not check['command'] or not log.is_relative_to(output.resolve()) or not log.is_file():
            raise BenchmarkError('Final checks need successful execution and owned logs')
        check['sha256'] = digest(log.read_bytes())
    worktree = Path(replay['worktree'])
    if git(worktree, 'rev-parse', 'HEAD').decode().strip() != replay['replay_base']:
        raise BenchmarkError('Driver changed the review base')
    if git(worktree, 'ls-files', '--others', '--exclude-standard'):
        raise BenchmarkError('Driver left source files outside the staged target')
    if git(worktree, 'diff', '--binary', '--no-ext-diff'):
        raise BenchmarkError('Driver left unstaged source changes')
    final_tree = git(worktree, 'write-tree').decode().strip()
    for name, ref in [('final.patch', 'HEAD'), ('fixes.patch', replay['initial_tree'])]:
        (output / name).write_bytes(diff(worktree, ref))
    latest = []
    stopped = False
    for item in review['slices'].values():
        if item.get('removed'):
            continue
        version = item.get('definition_version', 1)
        runs = [r for r in item['runs'] if r.get('definition_version', 1) == version]
        last_pass = max((r['pass'] for r in runs), default=0)
        shots = {r['shot']: r for r in runs if r['pass'] == last_pass}
        latest.extend(shots.values())
        stopped |= any(j.get('verdict') == 'stop' and j.get('definition_version', 1) == version
                       and j['pass'] == last_pass for j in item.get('judgements', []))
    reason = 'judge-stop' if stopped else 'silent' if latest and all(r['status'] == 'no_findings' for r in latest) else 'terminal-resolution'
    save(output / 'completion.json', {'reason': reason, 'final_tree': final_tree,
         'review_dir': str(review_dir), 'checks': checks, 'resolutions': result['resolutions'],
         'summary': result['summary'], 'completed_at': now()})
    return load(output / 'completion.json')



def validate_result(value: dict) -> None:
    if not isinstance(value, dict) or set(value) != {'summary', 'resolutions', 'checks'} or not isinstance(value['summary'], str):
        raise BenchmarkError('Invalid driver result shape')
    if not isinstance(value['resolutions'], list) or not isinstance(value['checks'], list):
        raise BenchmarkError('Driver resolutions and checks must be lists')
    ids = set()
    for item in value['resolutions']:
        if not isinstance(item, dict) or set(item) != {'id', 'outcome', 'reason', 'evidence', 'repeated_of'}:
            raise BenchmarkError('Invalid finding resolution shape')
        if not isinstance(item['id'], str) or not item['id'] or item['id'] in ids or item['outcome'] != 'fixed':
            raise BenchmarkError('Finding resolution IDs must be unique with fixed outcomes')
        if not isinstance(item['reason'], str) or not item['reason'] or not isinstance(item['evidence'], list) or not item['evidence'] or not all(isinstance(e, str) and e for e in item['evidence']):
            raise BenchmarkError('Finding resolution needs reason and evidence')
        if item['repeated_of'] is not None and not isinstance(item['repeated_of'], str):
            raise BenchmarkError('Invalid repeated finding reference')
        ids.add(item['id'])
    for check in value['checks']:
        if not isinstance(check, dict) or set(check) != {'command', 'exit_code', 'log'} or type(check['exit_code']) is not int or not isinstance(check['command'], str) or not isinstance(check['log'], str):
            raise BenchmarkError('Invalid check evidence shape')
