from __future__ import annotations

import json
import sys
from pathlib import Path

assert sys.stdin.read() == ''
output, mode, result_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.path.insert(0, str(output / 'tool/multi-shot-review/scripts'))
from review_state import ReviewState
from harnesses import HarnessProfile, resolve_profile

replay = json.loads((output / 'replay.json').read_text())
review_dir = Path(replay['review_dir'])
worktree = Path(replay['worktree'])
profile = resolve_profile(HarnessProfile('codex', 'fixture', 'high'), override_source='fixture')
if mode == 'fail':
    (worktree / 'unstaged-new').write_text('Failed attempt contents\n')
    raise SystemExit(7)
resolutions = []
with ReviewState.locked(review_dir) as state:
    if not state.data['slices']:
        cid = state.start_classification(profile)
        state.add_slice(name='whole', mode='prompt', target=None, prompt='Review this change', cwd=worktree)
        state.complete_classification(cid, 0)
    if not state.data['completed']:
        reservation = state.reserve_eligible(max_passes=3 if mode in {'fix', 'missed'} else 1)[0]
        findings = None if mode in {'silent', 'claude'} else [{'severity': 'P2', 'title': 'Fix behavior',
            'content': 'Fixture finding', 'location': {'path': 'code', 'start_line': 1, 'end_line': 1}}]
        state.complete_run(run_id=reservation.run_id, slice_name='whole', status='no_findings' if findings is None else 'findings',
                           exit_code=0, classification='no_findings' if findings is None else 'findings', findings=findings)
        if findings:
            fid = state.data['slices']['whole']['runs'][-1]['findings'][0]['id']
            if mode == 'reject':
                state.ignore_finding(fid, 'Fixture false positive')
            elif mode in {'fix', 'missed'}:
                if mode == 'fix':
                    (worktree / 'code').write_text('fixed\n')
                resolutions.append({'id': fid, 'outcome': 'fixed', 'reason': 'Fixed the defect', 'evidence': ['code:1'], 'repeated_of': None})
                reservation = state.reserve_eligible(max_passes=3)[0]
                state.complete_run(run_id=reservation.run_id, slice_name='whole', status='no_findings', exit_code=0, classification='no_findings')
            else:
                state.record_judgement('whole', verdict='stop', reason='Only P2 findings remain', profile=profile,
                                       judged_pass=1, definition_version=1)
    if mode == 'judge-stop':
        for run in state.data['slices']['whole']['runs']:
            for finding in run.get('findings', []):
                if finding['status'] == 'open':
                    (worktree / 'code').write_text('fixed final\n')
                    resolutions.append({'id': finding['id'], 'outcome': 'fixed', 'reason': 'Fixed after judge stop',
                                        'evidence': ['code:1'], 'repeated_of': None})
    state.save()
if mode == 'missed':
    resolutions = []
import subprocess
subprocess.run(['git', '-C', str(worktree), 'add', '-A'], check=True)
log = result_path.parent / 'check.log'
log.write_text('Fixture check passed\n')
payload = {'summary': 'Fixture workflow', 'resolutions': resolutions,
                                  'checks': [{'command': 'fixture-check', 'exit_code': 0, 'log': str(log)}]}
if mode == 'claude':
    print(json.dumps({'structured_output': payload}))
else:
    result_path.write_text(json.dumps(payload))
if mode != 'claude':
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 30}}))
