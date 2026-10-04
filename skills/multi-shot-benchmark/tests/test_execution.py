from __future__ import annotations

import sys
import os
import tarfile
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import BenchmarkError, git, load, save
from replay import prepare
from execution import run, locked, process_key
from completion import completion
from review_state import ReviewState, ReviewStateError
from harnesses import HarnessProfile, resolve_profile

PROFILE = {'harness': 'codex', 'model': 'fixture', 'reasoning': 'high'}
FIXTURE = Path(__file__).with_name('driver_fixture.py')


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        registry = patch('storage.registry_path', return_value=self.root / 'host/storage.json')
        registry.start()
        self.addCleanup(registry.stop)
        repo = self.root / 'source'
        repo.mkdir()
        git(repo, 'init')
        git(repo, 'config', 'user.name', 'Test')
        git(repo, 'config', 'user.email', 'test@example.com')
        (repo / 'code').write_text('base\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'base')
        (repo / 'code').write_text('change\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'change')
        config = self.root / 'arm.toml'
        config.write_text('max_passes = 3\nshots = 1\n')
        self.output = self.root / 'runs/one'
        prepare(repo, 'HEAD', 'Task', config, self.output, worktree_root=self.root / 'worktrees', benchmark_root=self.root / 'runs')
        self.fixture = self.root / 'fixture.py'
        self.fixture.write_bytes(FIXTURE.read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def factory(self, mode):
        return lambda profile, prompt, result, schema, worktree: [sys.executable, str(self.fixture), str(self.output), mode, str(result)]

    def test_silent_and_idempotent_completion(self):
        state = run(self.output, PROFILE, command_factory=self.factory('silent'))
        self.assertEqual(state['completion']['reason'], 'silent')
        self.assertEqual(run(self.output, PROFILE)['attempts'], state['attempts'])
        self.assertTrue((self.output / 'final.patch').is_file())

    def test_fix_then_silence(self):
        state = run(self.output, PROFILE, command_factory=self.factory('fix'))
        self.assertEqual(state['completion']['reason'], 'silent')
        self.assertTrue(state['completion']['resolutions'])
        self.assertIn(b'fixed', (self.output / 'fixes.patch').read_bytes())

    def test_rejections_are_terminal_resolution(self):
        state = run(self.output, PROFILE, command_factory=self.factory('reject'))
        self.assertEqual(state['completion']['reason'], 'terminal-resolution')

    def test_unresolved_judge_stop_retries_same_session(self):
        before = load(self.output / 'replay.json')['review_dir']
        with self.assertRaises(BenchmarkError):
            run(self.output, PROFILE, command_factory=self.factory('missing-resolution'))
        state = run(self.output, PROFILE, command_factory=self.factory('judge-stop'))
        self.assertEqual(state['completion']['reason'], 'judge-stop')
        self.assertEqual(len(state['attempts']), 2)
        self.assertEqual(state['completion']['review_dir'], before)

    def test_child_failure_retry_and_profile_guard(self):
        with self.assertRaises(BenchmarkError):
            run(self.output, PROFILE, command_factory=self.factory('fail'))
        state = run(self.output, PROFILE, command_factory=self.factory('silent'))
        self.assertEqual([a['status'] for a in state['attempts']], ['failed', 'completed'])
        with self.assertRaises(BenchmarkError):
            run(self.output, {**PROFILE, 'model': 'other'})

    def test_completed_child_is_reconciled_without_another_attempt(self):
        state = run(self.output, PROFILE, command_factory=self.factory('silent'))
        state['status'] = 'running'
        state['attempts'][-1]['status'] = 'running'
        state['attempts'][-1]['ended_at'] = None
        state['attempts'][-1]['exit_code'] = None
        save(self.output / 'execution.json', state)
        restored = run(self.output, PROFILE)
        self.assertEqual(restored['status'], 'completed')
        self.assertEqual(len(restored['attempts']), 1)

        self.assertEqual(restored['attempts'][-1]['ended_at'], load(self.output / 'driver/1/exit.json')['ended_at'])
        self.assertEqual(restored['attempts'][-1]['exit_code'], 0)

    def test_nonzero_exit_evidence_keeps_original_time_on_retry(self):
        with self.assertRaises(BenchmarkError):
            run(self.output, PROFILE, command_factory=self.factory('fail'))
        state = load(self.output / 'execution.json')
        saved_end = '2026-10-01T10:00:00+00:00'
        save(self.output / 'driver/1/exit.json', {'exit_code': 7, 'ended_at': saved_end})
        state['status'] = 'running'
        state['attempts'][0].update(status='running', ended_at=None, exit_code=None)
        save(self.output / 'execution.json', state)
        restored = run(self.output, PROFILE, command_factory=self.factory('silent'))
        self.assertEqual(restored['attempts'][0]['ended_at'], saved_end)
        self.assertEqual(restored['attempts'][0]['exit_code'], 7)
        self.assertEqual(restored['attempts'][0]['status'], 'failed')

    def test_archived_kept_finding_cannot_disappear(self):
        with self.assertRaisesRegex(BenchmarkError, 'Unresolved terminal finding'):
            run(self.output, PROFILE, command_factory=self.factory('missed'))

    def test_failure_preserves_untracked_contents(self):
        with self.assertRaises(BenchmarkError):
            run(self.output, PROFILE, command_factory=self.factory('fail'))
        with tarfile.open(self.output / 'driver/1/untracked.tar') as archive:
            self.assertEqual(archive.extractfile('unstaged-new').read(), b'Failed attempt contents\n')

    def test_live_saved_driver_blocks_launch(self):
        save(self.output / 'execution.json', {'schema': 1, 'status': 'running', 'driver': PROFILE,
             'attempts': [{'pid': os.getpid(), 'process_key': process_key(os.getpid())}]})
        def forbidden(*args):
            self.fail('A second driver launched')
        with self.assertRaisesRegex(BenchmarkError, 'still active'):
            run(self.output, PROFILE, command_factory=forbidden)

    def test_malformed_result_allows_retry(self):
        run(self.output, PROFILE, command_factory=self.factory('silent'))
        state = load(self.output / 'execution.json')
        state['status'] = 'running'
        save(self.output / 'execution.json', state)
        save(self.output / 'driver/1/result.json', {'summary': '', 'resolutions': None, 'checks': []})
        self.assertEqual(len(run(self.output, PROFILE, command_factory=self.factory('silent'))['attempts']), 2)

    def test_claude_envelope(self):
        profile = {**PROFILE, 'harness': 'claude-code'}
        state = run(self.output, profile, command_factory=self.factory('claude'))
        self.assertEqual(state['completion']['reason'], 'silent')

    def test_completion_rejects_bad_checks_and_source(self):
        run(self.output, PROFILE, command_factory=self.factory('silent'))
        result = load(self.output / 'driver/1/result.json')
        replay = load(self.output / 'replay.json')
        for field, value in [('exit_code', 1), ('log', str(self.root / 'outside.log')), ('log', str(self.output / 'missing'))]:
            changed = {**result, 'checks': [{**result['checks'][0], field: value}]}
            with self.subTest(field=field, value=value), self.assertRaises(BenchmarkError):
                completion(replay, changed, self.output)
        worktree = Path(replay['worktree'])
        (worktree / 'new').write_text('untracked')
        with self.assertRaisesRegex(BenchmarkError, 'outside the staged target'):
            completion(replay, result, self.output)
        (worktree / 'new').unlink()
        (worktree / 'code').write_text('unstaged')
        with self.assertRaisesRegex(BenchmarkError, 'unstaged source'):
            completion(replay, result, self.output)
        git(worktree, 'add', '.')
        git(worktree, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'changed base')
        with self.assertRaisesRegex(BenchmarkError, 'changed the review base'):
            completion(replay, result, self.output)
        self.assertNotIn('sha256', result['checks'][0])

    def test_partial_classification_resumes_before_review(self):
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        profile = resolve_profile(HarnessProfile(**PROFILE), override_source='fixture')
        with ReviewState.locked(review_dir) as state:
            cid = state.start_classification(profile)
            state.add_slice(name='partial', mode='prompt', prompt='Review', target=None, cwd=Path(load(self.output / 'replay.json')['worktree']))
            state.complete_classification(cid, 1)
            with self.assertRaises(ReviewStateError):
                state.start_classification(profile)
            cid = state.start_classification(profile, resume_incomplete=True)
            state.complete_classification(cid, 0)
            with self.assertRaises(ReviewStateError):
                state.start_classification(profile, resume_incomplete=True)
            state.save()

    def test_interrupted_preparation_is_recorded_and_resumes(self):
        with patch('execution.driver_prompt', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(self.output, PROFILE, command_factory=self.factory('silent'))
        state = run(self.output, PROFILE, command_factory=self.factory('silent'))
        self.assertEqual([a['status'] for a in state['attempts']], ['interrupted', 'completed'])

    def test_active_owner_and_interrupted_recovery(self):
        with locked(self.output):
            with self.assertRaises(BenchmarkError):
                run(self.output, PROFILE, command_factory=self.factory('silent'))
        save(self.output / 'execution.json', {'schema': 1, 'status': 'running', 'driver': PROFILE,
             'attempts': [{'number': 1, 'status': 'running', 'pid': 999999999, 'process_key': 'missing'}]})
        state = run(self.output, PROFILE, command_factory=self.factory('silent'))
        self.assertEqual(state['attempts'][0]['status'], 'interrupted')
        self.assertEqual(state['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
