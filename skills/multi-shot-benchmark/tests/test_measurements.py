from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import BenchmarkError, load, save, git
from usage import read_usage, read_rates, cost, elapsed
from metrics import measure_execution, usage_record
from assessment import record, read as read_assessment
from comparison import measure, aggregate, paired
from execution import run as execute
from completion import completion
from review_state import ReviewState
import test_execution
import test_scheduling

PROFILE = test_execution.PROFILE


ASSIGNMENT = {'id': 'fixture', 'case': 'case-1', 'arm': 'a', 'repetition': 1}
ASSESSMENT = {'summary': 'Checks support the final behavior', 'evidence': ['final.patch', 'driver/1/check.log'],
              'residual_defects': [], 'regressions': [], 'disputed_findings': []}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stdout, self.stderr = self.root / 'stdout', self.root / 'stderr'

    def test_codex_actual_events_and_cached_input(self):
        self.stdout.write_text('\n'.join(json.dumps(v) for v in [
            {'type': 'item.completed', 'usage': {'input_tokens': 900}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 30}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 40, 'cached_input_tokens': 10, 'output_tokens': 5}}]))
        usage = read_usage(self.stdout, self.stderr, 'codex')
        self.assertEqual(usage['tokens'], {'input': 110, 'cached_input': 30, 'cache_write': 0, 'output': 35})
        self.assertEqual(len(usage['records']), 2)
        self.assertEqual(usage['coverage'], 'structured')

    def test_claude_usage_has_separate_cache_fields(self):
        self.stdout.write_text(json.dumps({'type': 'result', 'usage': {'input_tokens': 10, 'cache_read_input_tokens': 20,
                                    'cache_creation_input_tokens': 30, 'output_tokens': 40}}))
        self.assertEqual(read_usage(self.stdout, self.stderr, 'claude-code')['tokens'],
                         {'input': 10, 'cached_input': 20, 'cache_write': 30, 'output': 40})

    def test_summary_is_not_a_billing_record(self):
        self.stderr.write_text('tokens used\n1,234\n')
        usage = read_usage(self.stdout, self.stderr, 'codex')
        self.assertEqual(usage['coverage'], 'summary-only')
        self.assertEqual(usage['reported_summaries'][0]['tokens'], 1234)
        self.assertIsNone(usage['tokens']['output'])
        self.assertIsNone(cost([{'id': 'x', 'role': 'reviewer', 'harness': 'codex', 'model': 'm', 'usage': usage}], None)['estimate'])

    def test_missing_and_invalid_fields_remain_unknown(self):
        self.stdout.write_text(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 11, 'output_tokens': True}}))
        usage = read_usage(self.stdout, self.stderr, 'codex')
        self.assertIsNone(usage['tokens']['input'])
        self.assertIsNone(usage['tokens']['cached_input'])
        self.assertIsNone(usage['tokens']['output'])
        self.assertIsNone(elapsed('2026-01-01', '2025-01-01'))
        self.assertIsNone(elapsed(None, '2026-01-01'))

    def test_recovery_times_are_not_model_runtime(self):
        row = usage_record('classifier', 'classifier', {'harness': 'codex'}, self.stdout, self.stderr,
                           '2026-10-04T10:00:00+00:00', '2026-10-04T12:00:00+00:00', status='failed')
        self.assertIsNone(row['seconds'])
        self.assertEqual(row['reported_interval_seconds'], 7200)
        row = usage_record('reviewer', 'reviewer', {'harness': 'codex'}, self.stdout, self.stderr,
                           '2026-10-04T10:00:00+00:00', '2026-10-04T12:00:00+00:00', error='stale running reservation recovered')
        self.assertIsNone(row['seconds'])

    def test_claude_model_usage_and_duration_are_preserved(self):
        self.stdout.write_text(json.dumps({'type': 'result', 'duration_ms': 6793, 'duration_api_ms': 4000,
            'usage': {'input_tokens': 999, 'output_tokens': 999}, 'modelUsage': {
                'opus': {'inputTokens': 10, 'outputTokens': 20, 'cacheReadInputTokens': 30, 'cacheCreationInputTokens': 40},
                'haiku': {'inputTokens': 2, 'outputTokens': 3, 'cacheReadInputTokens': 4, 'cacheCreationInputTokens': 5}}}))
        row = usage_record('judge', 'judge', {}, self.stdout, self.stderr)
        self.assertEqual(row['seconds'], 6.793)
        self.assertEqual(row['api_seconds'], 4)
        self.assertEqual(row['usage']['tokens']['input'], 12)
        self.assertEqual(row['usage']['records'][0]['raw']['input_tokens'], 999)
        rates = read_rates(self.rates())
        rates['models'] = {'claude-code/opus': {f: 1 for f in ('input', 'output', 'cached_input', 'cache_write')}}
        result = cost([row], rates)
        self.assertIsNone(result['estimate'])
        self.assertAlmostEqual(result['known_subtotal'], 0.0001)
        self.assertEqual(result['total_records'], 2)
        self.assertEqual(result['records'][1]['model'], 'haiku')

    def test_duration_and_model_usage_without_top_level_tokens(self):
        self.stdout.write_text(json.dumps({'type': 'result', 'duration_ms': 3000, 'duration_api_ms': 2000}))
        row = usage_record('judge', 'judge', {}, self.stdout, self.stderr)
        self.assertEqual(row['seconds'], 3)
        self.assertEqual(row['api_seconds'], 2)
        self.assertIsNone(row['usage']['tokens']['input'])
        self.stdout.write_text(json.dumps({'type': 'result', 'modelUsage': {'m': {
            'inputTokens': 1, 'outputTokens': 2, 'cacheReadInputTokens': 3, 'cacheCreationInputTokens': 4}}}))
        self.assertEqual(read_usage(self.stdout, self.stderr, 'claude-code')['tokens']['input'], 1)

    def test_workflow_span_survives_an_unknown_intermediate_end(self):
        from workflow_evidence import workflow_time
        timing = workflow_time({'attempts': [
            {'started_at': '2026-10-04T10:00:00Z', 'ended_at': None},
            {'started_at': '2026-10-04T11:00:00Z', 'ended_at': '2026-10-04T11:10:00Z'}]})
        self.assertEqual(timing['elapsed_seconds'], 4200)
        self.assertIsNone(timing['driver_lifecycle_seconds']['total'])
        self.assertEqual(timing['driver_lifecycle_seconds']['known_subtotal'], 600)

    def rates(self):
        path = self.root / 'rates.json'
        save(path, {'currency': 'USD', 'units': 'per_million_tokens', 'source': 'Test rate table', 'date': '2026-10-04',
                    'assumptions': 'Fixture rates exclude other charges',
                    'models': {'codex/m': {'input': 1, 'cached_input': 0.5, 'cache_write': 1, 'output': 2}}})
        return path

    def test_cost_has_rate_provenance_and_partial_coverage(self):
        rates = read_rates(self.rates())
        self.stdout.write_text(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 30}}))
        row = {'id': 'x', 'role': 'driver', 'harness': 'codex', 'model': 'm', 'usage': read_usage(self.stdout, self.stderr, 'codex')}
        priced = cost([row], rates)
        self.assertAlmostEqual(priced['estimate'], 0.00015)
        self.assertEqual(priced['rate_provenance']['date'], '2026-10-04')
        partial = cost([row, {**row, 'id': 'unknown', 'model': None}], rates)
        self.assertIsNone(partial['estimate'])
        self.assertAlmostEqual(partial['known_subtotal'], 0.00015)
        self.assertEqual(partial['priced_records'], 1)

    def test_invalid_rate_provenance_or_values_are_rejected(self):
        path = self.rates()
        value = load(path)
        value['models']['codex/m']['output'] = float('nan')
        save(path, value)
        with self.assertRaises(BenchmarkError):
            read_rates(path)
        value['models']['codex/m']['output'] = 2
        value['date'] = 'today'
        save(path, value)
        with self.assertRaises(BenchmarkError):
            read_rates(path)


class MeasurementTests(unittest.TestCase):
    setUp = test_execution.ExecutionTests.setUp
    tearDown = test_execution.ExecutionTests.tearDown
    factory = test_execution.ExecutionTests.factory

    def measure(self):
        return measure_execution(self.output, ASSIGNMENT, PROFILE)

    def test_fix_archive_first_pass_and_actual_driver_usage(self):
        execute(self.output, PROFILE, command_factory=self.factory('fix'))
        result = self.measure()
        self.assertEqual(result['completion_reason'], 'silent')
        self.assertEqual(result['first_pass']['by_disposition'], {'retained': 1})
        self.assertEqual(result['full_workflow']['unique_retained_chains'], 1)
        self.assertIn('/history/', result['findings'][0]['source'])
        self.assertEqual(result['effort']['review_waves'], 2)
        self.assertEqual(result['effort']['reviewer_shots'], 2)
        self.assertEqual(result['roles']['driver']['tokens']['input']['known_subtotal'], 80)
        self.assertEqual(result['roles']['reviewer']['usage_coverage'], {'missing': 2})
        self.assertEqual(result['assessment']['status'], 'missing')
        self.assertIsNone(result['cost']['estimate'])

    def test_rejected_findings_are_not_retained(self):
        execute(self.output, PROFILE, command_factory=self.factory('reject'))
        result = self.measure()
        self.assertEqual(result['full_workflow']['by_disposition'], {'rejected': 1})
        self.assertEqual(result['full_workflow']['unique_retained_chains'], 0)
        self.assertEqual(result['completion_reason'], 'terminal-resolution')

    def test_unresolved_terminal_and_failed_attempts_remain_visible(self):
        with self.assertRaises(BenchmarkError):
            execute(self.output, PROFILE, command_factory=self.factory('missing-resolution'))
        result = self.measure()
        self.assertTrue(result['review_completed'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['full_workflow']['by_disposition'], {'unresolved': 1})
        self.assertEqual(result['effort']['judge_verdicts'], 1)
        self.assertEqual(result['roles']['judge']['usage_coverage'], {'missing': 1})
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        save(review_dir / '_logs/judge-fixture.stdout.log', {'type': 'result', 'usage': {'input_tokens': 10,
             'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 30, 'output_tokens': 40}})
        result = self.measure()
        self.assertEqual(result['roles']['judge']['tokens']['input']['known_subtotal'], 10)
        self.assertEqual(result['roles']['judge']['tokens']['output']['known_subtotal'], 40)
        self.assertIsNone(next(r for r in result['usage_records'] if r['role'] == 'judge')['model'])
        execute(self.output, PROFILE, command_factory=self.factory('judge-stop'))
        result = self.measure()
        self.assertEqual(result['effort']['driver_retries'], 1)
        self.assertEqual([r['status'] for r in result['usage_records'] if r['role'] == 'driver'], ['failed', 'completed'])
        self.assertEqual(result['completion_reason'], 'judge-stop')

    def test_repeat_chains_preserve_findings_without_double_count(self):
        replay = load(self.output / 'replay.json')
        review_dir = Path(replay['review_dir'])
        ids = []
        from harnesses import HarnessProfile, resolve_profile
        with ReviewState.locked(review_dir) as state:
            cid = state.start_classification(resolve_profile(HarnessProfile('codex', 'fixture', 'high'), override_source='fixture'))
            state.add_slice(name='whole', mode='prompt', target=None, prompt='Review', cwd=Path(replay['worktree']))
            state.complete_classification(cid, 0)
            for number in range(2):
                reservation = state.reserve_eligible(max_passes=3)[0]
                state.complete_run(run_id=reservation.run_id, slice_name='whole', status='findings', exit_code=0, classification='findings',
                                   findings=[{'severity': 'P1', 'title': 'Defect', 'content': 'Same defect',
                                              'location': {'path': 'code', 'start_line': 1, 'end_line': 1}}])
                ids.append(state.data['slices']['whole']['runs'][-1]['findings'][0]['id'])
            reservation = state.reserve_eligible(max_passes=3)[0]
            state.complete_run(run_id=reservation.run_id, slice_name='whole', status='no_findings', exit_code=0, classification='no_findings')
            state.save()
        git(Path(replay['worktree']), 'add', '-A')
        log = self.output / 'check.log'
        log.write_text('Passed')
        completion(replay, {'summary': 'Repeated fix', 'checks': [{'command': 'check', 'exit_code': 0, 'log': str(log)}],
                           'resolutions': [{'id': identity, 'outcome': 'fixed', 'reason': 'Fixed', 'evidence': ['code:1'],
                                            'repeated_of': ids[0] if index else None} for index, identity in enumerate(ids)]}, self.output)
        result = self.measure()
        self.assertEqual(result['full_workflow']['total'], 2)
        self.assertEqual(result['full_workflow']['repeated'], 1)
        self.assertEqual(result['full_workflow']['unique_retained_chains'], 1)
        self.assertEqual(result['findings'][1]['resolution_chain'], [ids[1], ids[0]])

    def test_duplicate_and_review_retry_have_separate_counts(self):
        replay = load(self.output / 'replay.json')
        review_dir = Path(replay['review_dir'])
        from harnesses import HarnessProfile, resolve_profile
        with ReviewState.locked(review_dir) as state:
            cid = state.start_classification(resolve_profile(HarnessProfile('codex', 'fixture', 'high'), override_source='fixture'))
            state.add_slice(name='whole', mode='prompt', target=None, prompt='Review', cwd=Path(replay['worktree']), shots=2)
            state.complete_classification(cid, 0)
            first, second = state.reserve_eligible(max_passes=3)
            finding = {'severity': 'P2', 'title': 'Same defect', 'content': 'Same defect',
                       'location': {'path': 'code', 'start_line': 1, 'end_line': 1}}
            state.complete_run(run_id=first.run_id, slice_name='whole', status='findings', exit_code=0, classification='findings', findings=[finding])
            state.complete_run(run_id=second.run_id, slice_name='whole', status='failed', exit_code=1, classification=None, error='Capacity failure')
            retry = state.reserve_eligible(max_passes=3)[0]
            state.complete_run(run_id=retry.run_id, slice_name='whole', status='findings', exit_code=0, classification='findings', findings=[finding])
            state.save()
        result = self.measure()
        self.assertEqual(result['effort']['slice_passes'], 1)
        self.assertEqual(result['effort']['reviewer_shots'], 2)
        self.assertEqual(result['effort']['review_attempts'], 3)
        self.assertEqual(result['effort']['review_retries'], 1)
        self.assertEqual(result['full_workflow']['by_disposition'], {'unresolved': 1, 'duplicate': 1})
        duplicate = next(r for r in result['findings'] if r['disposition'] == 'duplicate')
        original = next(r for r in result['findings'] if r['disposition'] == 'unresolved')
        self.assertEqual(duplicate['resolution_chain'], [duplicate['id'], original['id']])
        self.assertEqual(len(result['unfinished_slices']), 1)

    def test_parent_assessment_has_evidence_and_becomes_stale(self):
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        value = {**ASSESSMENT, 'regressions': [{'severity': 'P1', 'description': 'Changed behavior', 'evidence': ['final.patch:4']}]}
        record(self.output, value)
        result = self.measure()
        self.assertEqual(result['assessment']['status'], 'current')
        self.assertEqual(aggregate([result])['quality']['confirmed_regressions'], 1)
        (self.output / 'final.patch').write_text('Changed evidence')
        self.assertEqual(read_assessment(self.output)['status'], 'stale')
        self.assertIsNone(aggregate([self.measure()])['quality']['confirmed_regressions'])

    def test_unequal_execution_counts_keep_repetitions_and_pairs(self):
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        row = self.measure()
        rows = [row, {**row, 'id': 'b1', 'arm': 'b'}, {**row, 'id': 'a2', 'repetition': 2}]
        pairs = paired(rows, ['a', 'b'])
        self.assertTrue(pairs[0]['complete'])
        self.assertFalse(pairs[1]['complete'])
        self.assertIsNone(pairs[1]['executions']['b'])
        self.assertEqual(pairs[1]['deltas'], [])
        self.assertEqual(aggregate(rows)['assessment_coverage'], {'current': 0, 'completed': 3})

    def test_failed_checks_and_judge_errors_survive_success(self):
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        state = load(self.output / 'execution.json')
        attempt = state['attempts'][0]
        directory = Path(attempt['directory'])
        result = load(directory / 'result.json')
        result['checks'][0]['exit_code'] = 1
        save(directory / 'result.json', result)
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        (review_dir / '_errors.md').write_text('## 2026-10-04 judge failed\n\nError: Capacity failure\n')
        (review_dir / '_logs').mkdir(exist_ok=True)
        (review_dir / '_logs/judge-failed.stderr.log').write_text('[runner] judge command exited with code 1')
        row = self.measure()
        self.assertEqual(row['attempt_checks'][0]['checks'][0]['exit_code'], 1)
        self.assertEqual(row['checks'][0]['exit_code'], 0)
        self.assertTrue(any('Capacity failure' in message for message in row['errors']))
        self.assertTrue(any(r.get('diagnostic_text') for r in row['usage_records'] if r['role'] == 'judge'))

    def test_missing_state_changed_tool_and_orphan_driver_are_rejected(self):
        from benchmark_store import process_key
        import os
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        path = Path(load(self.output / 'replay.json')['review_dir']) / '_state.json'
        content = path.read_bytes()
        path.unlink()
        with self.assertRaises(BenchmarkError):
            self.measure()
        path.write_bytes(content)
        tool = self.output / 'tool/multi-shot-review/scripts/review_state.py'
        content = tool.read_bytes()
        tool.write_text('raise RuntimeError("Do not execute this")')
        with self.assertRaises(BenchmarkError):
            self.measure()
        tool.write_bytes(content)
        state = load(self.output / 'execution.json')
        state['attempts'][-1].update(pid=os.getpid(), process_key=process_key(os.getpid()))
        save(self.output / 'execution.json', state)
        with self.assertRaises(BenchmarkError):
            self.measure()

    def test_aggregate_unknowns_and_paired_usage_keep_coverage(self):
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        row = self.measure()
        unknown = {**row, 'effort': {**row['effort'], 'review_waves': None}}
        result = aggregate([row, unknown])
        self.assertIsNone(result['effort_totals']['review_waves']['total'])
        self.assertEqual(result['effort_totals']['review_waves']['known_records'], 1)
        self.assertIsNone(aggregate([unknown])['effort_totals']['review_waves']['known_subtotal'])
        self.assertEqual(result['roles']['driver']['tokens']['input']['total'], 160)
        other = {**row, 'id': 'b1', 'arm': 'b'}
        comparison = paired([row, other], ['a', 'b'])[0]['deltas'][0]
        self.assertEqual(comparison['roles']['driver']['tokens']['input'], 0)
        self.assertIsNone(comparison['roles']['reviewer']['tokens']['input'])
        self.assertEqual(comparison['workflow_elapsed_seconds'], 0)

    def test_missing_replay_and_abandoned_classifier(self):
        from harnesses import HarnessProfile, resolve_profile
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        with ReviewState.locked(review_dir) as state:
            state.start_classification(resolve_profile(HarnessProfile('codex', 'fixture', 'high'), override_source='fixture'))
            state.save()
        row = self.measure()
        self.assertEqual(len(row['unfinished_classifications']), 1)
        self.assertIsNone(row['roles']['classifier']['seconds']['total'])
        path = self.output / 'replay.json'
        path.unlink()
        save(self.output / 'execution.json', {'attempts': [{'number': 1, 'directory': str(self.output / 'driver/1'), 'status': 'failed'}]})
        with self.assertRaises(BenchmarkError):
            self.measure()

    def test_failed_judge_usage_does_not_cover_missing_verdict_log(self):
        execute(self.output, PROFILE, command_factory=self.factory('judge-stop'))
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        directory = review_dir / '_logs'
        directory.mkdir(exist_ok=True)
        save(directory / 'judge-failed.stdout.log', {'type': 'result', 'usage': {'input_tokens': 10,
             'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 30, 'output_tokens': 40}})
        (directory / 'judge-failed.stderr.log').write_text('[runner] judge command exited with code 1')
        row = self.measure()
        self.assertEqual(row['roles']['judge']['records'], 2)
        self.assertEqual(row['roles']['judge']['tokens']['input']['known_subtotal'], 10)
        self.assertIsNone(row['roles']['judge']['tokens']['input']['total'])
        self.assertFalse(row['cost']['complete'])

    def test_successful_judge_log_uses_saved_profile(self):
        from usage import cost
        execute(self.output, PROFILE, command_factory=self.factory('judge-stop'))
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        with ReviewState.locked(review_dir) as state:
            judgement = state.data['slices']['whole']['judgements'][0]
            judgement.update(harness='claude-code', model='judge-model', reasoning='high')
            state.save()
        stem = '20261004-1200-1-whole-abcdef'
        save(review_dir / 'judge' / f'{stem}.json', {'verdict': judgement['verdict'], 'reason': judgement['reason']})
        save(review_dir / '_logs' / f'judge-{stem}.stdout.log', {'type': 'result', 'usage': {
             'input_tokens': 10, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 30, 'output_tokens': 40}})
        row = self.measure()
        record = next(r for r in row['usage_records'] if r['role'] == 'judge')
        self.assertEqual(record['model'], 'judge-model')
        self.assertEqual(row['roles']['judge']['records'], 1)
        rates = {'currency': 'USD', 'models': {'claude-code/judge-model': {f: 1 for f in ('input', 'output', 'cached_input', 'cache_write')}}}
        self.assertAlmostEqual(cost([record], rates)['estimate'], 0.0001)

    def test_failed_attempt_fix_claims_keep_links_but_remain_unconfirmed(self):
        execute(self.output, PROFILE, command_factory=self.factory('fix'))
        execution = load(self.output / 'execution.json')
        attempt = execution['attempts'][0]
        result_path = Path(attempt['directory']) / 'result.json'
        result = load(result_path)
        result['checks'][0]['exit_code'] = 1
        result['resolutions'][0]['repeated_of'] = result['resolutions'][0]['id']
        save(result_path, result)
        (self.output / 'completion.json').unlink()
        attempt['status'] = execution['status'] = 'failed'
        save(self.output / 'execution.json', execution)
        row = self.measure()
        finding = row['findings'][0]
        self.assertEqual(finding['disposition'], 'unresolved')
        self.assertIsNone(finding['driver_resolution'])
        self.assertEqual(finding['driver_claims'][0]['attempt'], 1)
        self.assertEqual(finding['repeated_of'], finding['id'])
        self.assertTrue(row['errors'])

    def test_abandoned_judge_without_logs_is_unknown_usage(self):
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        review_dir = Path(load(self.output / 'replay.json')['review_dir'])
        with ReviewState.locked(review_dir) as state:
            state.data['slices']['whole']['judge_pending'] = {'pass': 1, 'definition_version': 1,
                'runner_pid': 99999999, 'runner_key': 'dead', 'started_at': '2026-10-04T10:00:00Z'}
            state.save()
        row = self.measure()
        self.assertEqual(len(row['unfinished_judges']), 1)
        self.assertEqual(row['roles']['judge']['usage_coverage'], {'missing': 1})
        self.assertFalse(row['cost']['complete'])

    def test_failed_claude_result_recovers_native_stdout_claims(self):
        execute(self.output, PROFILE, command_factory=self.factory('fix'))
        state = load(self.output / 'execution.json')
        state['driver']['harness'] = 'claude-code'
        state['status'] = state['attempts'][0]['status'] = 'failed'
        save(self.output / 'execution.json', state)
        result_path = Path(state['attempts'][0]['directory']) / 'result.json'
        payload = load(result_path)
        payload['checks'][0]['exit_code'] = 1
        result_path.unlink()
        save(result_path.parent / 'stdout.log', {'type': 'result', 'structured_output': payload})
        (self.output / 'completion.json').unlink()
        row = self.measure()
        self.assertEqual(row['attempt_checks'][0]['checks'][0]['exit_code'], 1)
        self.assertTrue(row['attempt_checks'][0]['source']['path'].endswith('stdout.log'))
        self.assertEqual(row['findings'][0]['driver_claims'][0]['attempt'], 1)
        self.assertEqual(row['findings'][0]['disposition'], 'unresolved')

    def test_judge_scope_compares_full_slice_name(self):
        from workflow_evidence import match_judgements, pending_judges
        review_dir = self.output / 'scope-fixture'
        logs, judgements = [], []
        for name in ('a-b', 'a'):
            stem = f'20261004-1200-1-{name}-abcdef'
            path = review_dir / '_logs' / f'judge-{stem}.stdout.log'
            save(path, {})
            save(review_dir / 'judge' / f'{stem}.json', {'verdict': 'stop', 'reason': 'Only P2 findings remain'})
            logs.append(path)
        judgements = [{'slice': name, 'pass': 1, 'verdict': 'stop', 'reason': 'Only P2 findings remain'} for name in ('a', 'a-b')]
        matched = match_judgements(review_dir, logs, judgements, [])
        self.assertEqual(matched[0], logs[1])
        self.assertEqual(matched[1], logs[0])
        pending = pending_judges({'slices': {'a': {'judge_pending': {'pass': 1}}}}, logs, {})
        self.assertEqual(pending[0]['log'], str(logs[1]))

    def test_pending_execution_and_invalid_assessment_are_unknown(self):
        row = self.measure()
        self.assertFalse(row['review_completed'])
        self.assertEqual(row['finding_coverage'], 'not-started')
        self.assertIsNone(row['assessment']['residual_defects'])
        with self.assertRaises(BenchmarkError):
            record(self.output, ASSESSMENT)
        execute(self.output, PROFILE, command_factory=self.factory('silent'))
        with self.assertRaises(BenchmarkError):
            record(self.output, {**ASSESSMENT, 'evidence': []})


class RunMeasurementTests(unittest.TestCase):
    setUp = test_scheduling.SchedulingTests.setUp
    tearDown = test_scheduling.SchedulingTests.tearDown
    freeze = test_scheduling.SchedulingTests.freeze
    executor = test_scheduling.SchedulingTests.executor

    def test_frozen_run_reconciles_measurements_and_saved_sources(self):
        from scheduling import run
        self.spec['cases'] = self.spec['cases'][:1]
        manifest = self.freeze()
        run(self.output, executor=self.executor)
        value = measure(self.output)
        self.assertEqual(len(value['executions']), 4)
        self.assertEqual(sum(p['complete'] for p in value['paired']), 2)
        self.assertEqual(value['arm_totals']['a']['completed'], 2)
        self.assertEqual(value['arm_totals']['b']['completed'], 2)
        self.assertEqual(value['cases'], manifest['cases'])
        self.assertEqual(value['executions'][0]['checks'][0]['exit_code'], 0)
        self.assertTrue((self.output / 'measurements.json').is_file())
        self.assertEqual(value['executions'][0]['sources'][0]['sha256'],
                         __import__('hashlib').sha256(Path(value['executions'][0]['sources'][0]['path']).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
