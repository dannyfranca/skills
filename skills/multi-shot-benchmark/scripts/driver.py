from __future__ import annotations

import json
from pathlib import Path
from benchmark_store import BenchmarkError
from replay import REVIEW
from harnesses import HarnessProfile, resolve_profile


def driver_command(profile: dict, prompt: str, result: Path, schema: Path, worktree: Path) -> list[str]:
    resolved = resolve_profile(HarnessProfile(**validate_profile(profile)), override_source='benchmark-driver')
    if resolved.harness == 'codex':
        return ['codex', 'exec', '--ephemeral', '--dangerously-bypass-approvals-and-sandbox',
                '--json', '--skip-git-repo-check', '-C', str(worktree), '-m', resolved.model,
                '-c', f'model_reasoning_effort={json.dumps(resolved.reasoning)}',
                '--output-schema', str(schema), '-o', str(result), prompt]
    return ['claude', '--no-session-persistence', '--permission-mode', 'bypassPermissions',
            '--settings', '{"disableAllHooks":true,"autoMemoryEnabled":false}',
            '--model', resolved.model, '--effort', resolved.reasoning, '--output-format', 'json',
            '--json-schema', schema.read_text(), '-p', prompt]


def driver_prompt(output: Path, replay: dict) -> str:
    tool = output / 'tool/multi-shot-review'
    return f'''Run the full review and fix workflow for this historical task.
Task: {(output / 'task.md').read_text()}
Worktree: {replay['worktree']}
Existing review session: {replay['review_dir']}
Current review tool: {tool}
Read historical AGENTS.md, CLAUDE.md, CONTEXT.md and mapped context in the worktree.
Use only this worktree, this execution's assets, and the current review tool as source evidence.
Read no source checkout, remote history, other arm, prior review report, or benchmark answer.
Keep HEAD at the replay base. Review the whole uncommitted change. Preserve the task behavior.
Reuse the existing review session. It already has this arm's explicit config. Create no other session.
On resume, inspect saved session state and own previous attempt logs. Join active review work before advancing.
Require a successful classification before any review. If classification is unfinished with
partial slices, run classify_slices.py --resume-incomplete to finish it. With no active slices,
run normal classification. After a successful classification, resume its existing slices.
Follow the current skill below. Validate each finding, fix valid defects, record rejections and duplicates
through its tools, run relevant checks, and repeat normal waves. Keep the target frozen during a wave.
Preserve normal pass windows and judge stops. Resolve final findings without a forced new review session.
Add no benchmark token or time cap. Keep all check logs and workflow notes under {output}.
Before finishing, stage changed source files, save successful final check logs, and return the required JSON.
For each fixed finding, include its ID, reason, and file/line evidence in resolutions. Include earlier fixed
findings too. For a repeated issue, include repeated_of with the earlier finding ID. Rejections and duplicates
must be in review state. Each check needs its actual command, exit code, and absolute saved log path.
The parent will assess final quality. Your silence does not prove that all defects are gone.

{(tool / 'SKILL.md').read_text()}
'''



def validate_profile(profile: dict) -> dict:
    if set(profile) != {'harness', 'model', 'reasoning'} or not profile.get('model') or not profile.get('reasoning'):
        raise BenchmarkError('Driver harness, model, and reasoning must be explicit and fixed')
    resolved = resolve_profile(HarnessProfile(**profile), override_source='benchmark-driver')
    return {'harness': resolved.harness, 'model': resolved.model, 'reasoning': resolved.reasoning}
