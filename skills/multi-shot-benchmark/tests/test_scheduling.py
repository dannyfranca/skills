from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import BenchmarkError, git, load, save, process_key
from execution import run as execute, locked
from manifest import freeze, read_manifest
from scheduling import run, progress, cleanup, run_lock
from replay import cleanup as cleanup_replay

PROFILE = {'harness': 'codex', 'model': 'fixture', 'reasoning': 'high'}


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        registry = patch('storage.registry_path', return_value=self.root / 'host/storage.json')
        registry.start()
        self.addCleanup(registry.stop)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        git(self.repo, 'init')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.com')
        (self.repo / 'code').write_text('base\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'base')
        commits = []
        for n in range(5):
            (self.repo / 'code').write_text(f'change {n}\n')
            git(self.repo, 'add', '.')
            git(self.repo, 'commit', '-m', f'change {n}')
            commits.append(git(self.repo, 'rev-parse', 'HEAD').decode().strip())
        (self.root / 'task.md').write_text('Preserve the requested change')
        (self.root / 'arm.toml').write_text('max_passes = 1\nshots = 1\n')
        (self.root / 'arm-b.toml').write_text('max_passes = 4\nshots = 1\n[variants]\nb-test = 1\n[variant.b-test]\n[variant.b-test.classifier]\nharness = "codex"\nmodel = "classifier-b"\nreasoning = "high"\n[variant.b-test.slice_default]\nharness = "codex"\nmodel = "reviewer-b"\nreasoning = "high"\n[variant.b-test.judge]\nharness = "codex"\nmodel = "judge-b"\nreasoning = "high"\n')
        self.spec = {'repo': str(self.repo), 'driver': PROFILE, 'repetitions': 2,
                     'cases': [{'head': c, 'task_file': 'task.md'} for c in commits],
                     'arms': [{'name': 'a', 'config': 'arm.toml'},
                              {'name': 'b', 'config': 'arm-b.toml', 'variant': 'b-test'}]}
        self.output = self.root / 'runs/one'
        self.fixture = self.root / 'driver.py'
        script = Path(__file__).with_name('driver_fixture.py').read_text()
        script = script.replace("profile = resolve_profile", "(worktree / 'arm-result').write_text(output.name)\nprofile = resolve_profile")
        self.fixture.write_text(script)
        self.calls = []
        self.guard = threading.Lock()

    def tearDown(self):
        self.temp.cleanup()

    def freeze(self, output=None):
        return freeze(self.spec, output or self.output, input_root=self.root,
                      benchmark_root=self.root / 'runs', worktree_root=self.root / 'worktrees')

    def executor(self, output, profile):
        with self.guard:
            self.calls.append(str(output))
        return execute(output, profile, command_factory=lambda p, prompt, result, schema, cwd:
                       [sys.executable, str(self.fixture), str(output), 'silent', str(result)])

    def test_pilot_has_balanced_independent_workflows_and_resume(self):
        original = self.spec['cases'][0]['head']
        git(self.repo, 'branch', 'moving-ref', original)
        self.spec['cases'][0]['head'] = 'moving-ref'
        manifest = self.freeze()
        self.assertEqual(manifest['cases'][0]['head'], original)
        first = [a['arm'] for a in manifest['assignments'][::2]]
        self.assertEqual(first.count('a'), first.count('b'))
        (self.root / 'task.md').write_text('future requirements')
        (self.root / 'arm.toml').write_text('invalid = true')
        git(self.repo, 'branch', '-f', 'moving-ref', 'HEAD')
        result = run(self.output, executor=self.executor)
        self.assertEqual((result['completed'], result['total']), (20, 20))
        worktrees, sessions, trees = set(), set(), set()
        for row in result['executions']:
            output = Path(row['output'])
            replay = load(output / 'replay.json')
            if row['case'] == 'case-001':
                self.assertEqual(replay['head'], original)
            self.assertEqual((output / 'task.md').read_text(), 'Preserve the requested change')
            session = load(Path(replay['review_dir']) / '_state.json')['session']
            self.assertEqual(session['config']['max_passes'], 1 if row['arm'] == 'a' else 4)
            if row['arm'] == 'b':
                self.assertEqual(session['variant'], 'b-test')
                for role, model in [('classifier', 'classifier-b'), ('slice_default', 'reviewer-b'), ('judge', 'judge-b')]:
                    self.assertEqual(session['config'][role]['model'], model)
            self.assertEqual(load(output / 'execution.json')['driver'], PROFILE)
            worktrees.add(replay['worktree'])
            sessions.add(replay['review_dir'])
            trees.add(load(output / 'completion.json')['final_tree'])
            self.assertEqual((Path(replay['worktree']) / 'arm-result').read_text(), row['id'])
        self.assertEqual((len(worktrees), len(sessions), len(trees)), (20, 20, 20))
        run(self.output, executor=self.executor)
        self.assertEqual(len(self.calls), 20)
        self.assertTrue(load(self.output / 'schedule.json')['invocations'][0]['launches'])

    def test_failed_sibling_is_retried_as_same_assignment(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        manifest = self.freeze()
        failed = manifest['assignments'][0]['id']
        def fail_one(output, profile):
            if output.name == failed:
                return execute(output, profile, command_factory=lambda p, prompt, result, schema, cwd:
                               [sys.executable, str(self.fixture), str(output), 'fail', str(result)])
            return self.executor(output, profile)
        first = run(self.output, executor=fail_one)
        self.assertEqual(first['completed'], 1)
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)
        execution = load(self.output / 'executions' / failed / 'execution.json')
        self.assertEqual(len(execution['attempts']), 2)
        self.assertEqual(len(self.calls), 2)

    def test_two_runs_overlap_without_shared_results(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        other = self.root / 'runs/two'
        freeze(self.spec, other, input_root=self.root, benchmark_root=self.root / 'runs',
               worktree_root=self.root / 'other-worktrees')
        barrier = threading.Barrier(4)
        def overlap(output, profile):
            from isolation import child_command
            command = child_command(output, ['true'])
            self.assertIn(str(self.root / 'worktrees'), command)
            self.assertIn(str(self.root / 'other-worktrees'), command)
            barrier.wait(timeout=20)
            return self.executor(output, profile)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(run, output, executor=overlap) for output in (self.output, other)]
            self.assertEqual([f.result()['completed'] for f in futures], [2, 2])
        own = {r['output'] for r in progress(self.output)['executions']}
        sibling = {r['output'] for r in progress(other)['executions']}
        self.assertFalse(own & sibling)

    def test_interrupted_setup_resumes_frozen_manifest(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        with patch('scheduling.prepare', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(self.output, executor=self.executor)
        self.assertEqual(load(self.output / 'schedule.json')['invocations'][0]['status'], 'interrupted')
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)
        with run_lock(self.output):
            with self.assertRaises(BenchmarkError):
                run(self.output, executor=self.executor)

    def test_cleanup_preserves_evidence_and_rejects_active_ownership(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        run(self.output, executor=self.executor)
        directory = Path(progress(self.output)['executions'][0]['output'])
        execution = load(directory / 'execution.json')
        execution['attempts'][-1].update(pid=os.getpid(), process_key=process_key(os.getpid()))
        save(directory / 'execution.json', execution)
        with self.assertRaisesRegex(BenchmarkError, 'active driver'):
            cleanup_replay(directory)
        execution['attempts'][-1]['pid'] = None
        save(directory / 'execution.json', execution)
        with locked(directory):
            with self.assertRaises(BenchmarkError):
                cleanup_replay(directory)
        replay = load(directory / 'replay.json')
        cleanup(self.output)
        self.assertFalse(Path(replay['worktree']).exists())
        self.assertTrue((directory / 'cleanup-evidence/code.patch').exists())
        self.assertTrue((directory / 'cleanup-evidence/untracked.tar').exists())
        self.assertTrue(self.repo.exists())
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)

    def test_persisted_partial_setup_retries_under_same_assignment(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        manifest = self.freeze()
        directory = self.output / 'executions' / manifest['assignments'][0]['id']
        save(directory / 'replay.json', {'status': 'preparing', 'owned': [], 'review_dir': None})
        (directory / 'partial.log').write_text('failed setup evidence')
        result = run(self.output, executor=self.executor)
        self.assertEqual(result['completed'], 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(list((self.output / 'setup-failures').rglob('partial.log'))), 1)

    def test_cleanup_rejects_active_classifier_and_review_without_driver(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        def pending(output, profile):
            return {'status': 'pending'}
        run(self.output, executor=pending)
        directory = Path(progress(self.output)['executions'][0]['output'])
        from review_state import ReviewState
        review_dir = Path(load(directory / 'replay.json')['review_dir'])
        with ReviewState.classifier_locked(review_dir):
            with self.assertRaisesRegex(BenchmarkError, 'review session'):
                cleanup_replay(directory)
        with ReviewState.locked(review_dir) as state:
            state.add_slice(name='live', mode='prompt', target=None, prompt='Review', cwd=self.repo)
            state.reserve_eligible()
            state.save()
        with self.assertRaisesRegex(BenchmarkError, 'active review'):
            cleanup_replay(directory)
        self.assertFalse((directory / 'cleanup-evidence').exists())

    def test_scheduler_cancels_owned_workers_and_reconciles_launches(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        started = threading.Event()
        def cancellable(output, profile, cancellation):
            started.set()
            self.assertTrue(cancellation.wait(10))
            raise KeyboardInterrupt('owned cancellation')
        def interrupt(futures):
            self.assertTrue(started.wait(10))
            raise KeyboardInterrupt
            yield
        with patch('scheduling.as_completed', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(self.output, executor=cancellable)
        launches = load(self.output / 'schedule.json')['invocations'][0]['launches']
        self.assertTrue(all(l['status'] == 'interrupted' and l['end_time_unknown'] for l in launches))
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)

    def test_different_storage_roots_cannot_overlap(self):
        from storage import shared_storage
        self.freeze()
        with shared_storage(self.root / 'other-storage', self.root / 'other-run'):
            with self.assertRaisesRegex(BenchmarkError, 'same benchmark_root'):
                run(self.output, executor=self.executor)

    def test_execution_cancellation_stops_only_its_owned_child(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        cancelled = threading.Event()
        cancelled.set()
        def cancel(output, profile, cancellation=None):
            with self.assertRaises(KeyboardInterrupt):
                execute(output, profile, cancellation=cancelled,
                        command_factory=lambda *args: [sys.executable, '-c', 'import time; time.sleep(100)'])
            state = load(output / 'execution.json')
            from benchmark_store import active
            self.assertEqual(state['status'], 'interrupted')
            self.assertFalse(active(state))
            self.assertIsNotNone(process_key(os.getpid()))
            return state
        result = run(self.output, executor=cancel)
        self.assertTrue(all(r['status'] == 'interrupted' for r in result['executions']))

    def test_arm_names_cannot_collide_with_source_assets(self):
        self.spec['arms'][0]['name'] = 'b-source'
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        manifest = self.freeze()
        import tomllib
        self.assertEqual([tomllib.loads((self.output / a['config']).read_text())['max_passes']
                          for a in manifest['arms']], [1, 4])
        self.assertEqual((self.output / 'assets/arm-sources/b-source.toml').read_bytes(),
                         (self.root / 'arm.toml').read_bytes())

    def test_resume_reconciles_stale_launch_without_rerunning_completion(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        run(self.output, executor=self.executor)
        schedule = load(self.output / 'schedule.json')
        old = schedule['invocations'][0]
        old['status'] = 'running'
        old['launches'][0].update(status='running', ended_at=None)
        save(self.output / 'schedule.json', schedule)
        run(self.output, executor=self.executor)
        launch = load(self.output / 'schedule.json')['invocations'][0]['launches'][0]
        self.assertEqual(launch['status'], 'completed')
        self.assertIsNotNone(launch['ended_at'])
        self.assertEqual(len(self.calls), 2)

    def test_interruption_during_submission_cancels_started_workers(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        started = threading.Event()
        def cancellable(output, profile, cancellation):
            started.set()
            self.assertTrue(cancellation.wait(10))
            raise KeyboardInterrupt('owned cancellation')
        from scheduling import save as real_save
        def interrupt(path, state):
            launches = state.get('invocations', [{}])[-1].get('launches', [])
            if len(launches) == 2 and launches[-1]['status'] == 'running':
                self.assertTrue(started.wait(10))
                raise KeyboardInterrupt
            real_save(path, state)
        with patch('scheduling.save', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                run(self.output, executor=cancellable)
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)

    def test_scheduler_links_recovered_completion_to_existing_attempt(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        run(self.output, executor=self.executor)
        directory = Path(progress(self.output)['executions'][0]['output'])
        state = load(directory / 'execution.json')
        state['status'] = 'interrupted'
        state['attempts'][-1]['status'] = 'interrupted'
        save(directory / 'execution.json', state)
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)
        launch = load(self.output / 'schedule.json')['invocations'][-1]['launches'][0]
        self.assertEqual(launch['attempt'], 1)
        self.assertTrue(launch['recovered_without_launch'])
        self.assertEqual(len(load(directory / 'execution.json')['attempts']), 1)

    def test_old_launches_reconcile_recovered_exit_after_resume(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        def mixed(output, profile):
            mode = 'fail' if output.name == read_manifest(self.output)['assignments'][0]['id'] else 'silent'
            return execute(output, profile, command_factory=lambda p, prompt, result, schema, cwd:
                           [sys.executable, str(self.fixture), str(output), mode, str(result)])
        run(self.output, executor=mixed)
        schedule = load(self.output / 'schedule.json')
        schedule['invocations'][0]['status'] = 'running'
        expected = {}
        for launch in schedule['invocations'][0]['launches']:
            directory = self.output / 'executions' / launch['id']
            state = load(directory / 'execution.json')
            attempt = state['attempts'][0]
            expected[launch['id']] = (attempt['status'], load(Path(attempt['directory']) / 'exit.json')['ended_at'])
            state['status'] = 'running'
            attempt.update(status='running', ended_at=None, pid=None, process_key=None)
            save(directory / 'execution.json', state)
            launch.update(status='running', driver_ended_at=None, ended_at=None)
        save(self.output / 'schedule.json', schedule)
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)
        for launch in load(self.output / 'schedule.json')['invocations'][0]['launches']:
            self.assertEqual((launch['status'], launch['driver_ended_at']), expected[launch['id']])
            self.assertFalse(launch['end_time_unknown'])

    def test_cleaned_partial_setup_stays_terminal(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        manifest = self.freeze()
        directory = self.output / 'executions' / manifest['assignments'][0]['id']
        save(directory / 'replay.json', {'status': 'failed', 'owned': [], 'review_dir': None})
        cleanup(self.output)
        result = run(self.output, executor=self.executor)
        row = next(r for r in result['executions'] if r['id'] == directory.name)
        self.assertTrue(row['cleaned'])
        self.assertIn('cannot resume', row['error'])
        self.assertEqual(len(self.calls), 1)

    def test_worktrees_cannot_expose_siblings_through_run_output(self):
        with self.assertRaisesRegex(BenchmarkError, 'separate roots'):
            freeze(self.spec, self.output, input_root=self.root,
                   worktree_root=self.output / 'worktrees')

    def test_unstarted_retry_does_not_link_prior_failed_attempt(self):
        from scheduling import reconcile_launch
        launch = {'expected_attempt': 2, 'attempt': None, 'status': 'running'}
        reconcile_launch(launch, {'status': 'failed', 'attempts': [
            {'number': 1, 'status': 'failed', 'started_at': 'earlier', 'ended_at': 'earlier-end'}]})
        self.assertIsNone(launch['attempt'])
        self.assertIsNone(launch['driver_started_at'])
        self.assertTrue(launch['end_time_unknown'])

    def test_failed_launch_links_persisted_attempt(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        def failed(output, profile):
            return execute(output, profile, command_factory=lambda p, prompt, result, schema, cwd:
                           [sys.executable, str(self.fixture), str(output), 'fail', str(result)])
        run(self.output, executor=failed)
        launches = load(self.output / 'schedule.json')['invocations'][0]['launches']
        self.assertTrue(all(l['attempt'] == 1 and l['driver_started_at'] and l['driver_ended_at'] for l in launches))

    def test_empty_setup_directory_is_preserved_and_rebuilt(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        manifest = self.freeze()
        directory = self.output / 'executions' / manifest['assignments'][0]['id']
        directory.mkdir(parents=True)
        (directory / 'initial-write.log').write_text('interrupted')
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)
        self.assertEqual(len(list((self.output / 'setup-failures').rglob('initial-write.log'))), 1)

    def test_new_worktree_roots_cannot_appear_after_children_start(self):
        from storage import shared_storage
        self.freeze()
        with shared_storage(self.root / 'runs', self.output):
            with self.assertRaisesRegex(BenchmarkError, 'before any run starts'):
                freeze(self.spec, self.root / 'runs/later', input_root=self.root,
                       benchmark_root=self.root / 'runs', worktree_root=self.root / 'new-worktrees')
        self.assertFalse((self.root / 'runs/later').exists())

    def test_roots_cannot_be_nested_inside_another_run(self):
        from storage import configure
        configure(self.root / 'runs', self.root / 'worktrees', self.output)
        with self.assertRaisesRegex(BenchmarkError, 'nested inside another run'):
            configure(self.root / 'runs', self.root / 'worktrees', self.output / 'uncreated-child')
        self.freeze()
        with self.assertRaisesRegex(BenchmarkError, 'disjoint across runs'):
            freeze(self.spec, self.root / 'runs/two', input_root=self.root,
                   benchmark_root=self.root / 'runs', worktree_root=self.output / 'executions/sibling-worktrees')
        with self.assertRaisesRegex(BenchmarkError, 'nested inside another run'):
            freeze(self.spec, self.output / 'sibling-run', input_root=self.root,
                   benchmark_root=self.root / 'runs', worktree_root=self.root / 'worktrees')

    def test_latest_setup_failure_overrides_previous_driver_error(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        def failed(output, profile):
            return execute(output, profile, command_factory=lambda p, prompt, result, schema, cwd:
                           [sys.executable, str(self.fixture), str(output), 'fail', str(result)])
        run(self.output, executor=failed)
        with patch('scheduling.resume', side_effect=BenchmarkError('Frozen review tool changed')):
            result = run(self.output, executor=self.executor)
        self.assertTrue(all(r['status'] == 'setup-failed' and r['error'] == 'Frozen review tool changed'
                            and 'Driver exited' in r['driver_error'] for r in result['executions']))
        result = run(self.output, executor=self.executor)
        self.assertEqual(result['completed'], 2)
        self.assertTrue(all(r['error'] is None for r in result['executions']))

    def test_missing_tool_and_unowned_cleanup_are_rejected(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        run(self.output, executor=self.executor)
        directory = Path(progress(self.output)['executions'][0]['output'])
        from replay import resume
        (directory / 'tool/multi-shot-review/scripts/run_reviews.py').unlink()
        with self.assertRaisesRegex(BenchmarkError, 'Frozen review tool'):
            resume(directory)
        state = load(directory / 'replay.json')
        state['worktree'] = str(self.repo)
        state['owned'].append(str(self.repo))
        save(directory / 'replay.json', state)
        with self.assertRaisesRegex(BenchmarkError, 'ownership differs'):
            cleanup_replay(directory)
        self.assertFalse((directory / 'cleanup-evidence').exists())
        self.assertTrue(self.repo.exists())

    def test_setup_errors_are_visible_without_an_execution_directory(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        with patch('scheduling.prepare', side_effect=BenchmarkError('setup unavailable')):
            result = run(self.output, executor=self.executor)
        self.assertEqual([r['status'] for r in result['executions']], ['setup-failed'] * 2)
        self.assertTrue(all(r['error'] == 'setup unavailable' for r in result['executions']))
        self.assertEqual(run(self.output, executor=self.executor)['completed'], 2)

    def test_frozen_assets_cannot_change(self):
        self.freeze()
        (self.output / 'assets/tasks/case-001.md').write_text('edited')
        with self.assertRaisesRegex(BenchmarkError, 'Frozen run asset'):
            read_manifest(self.output)


if __name__ == '__main__':
    unittest.main()
