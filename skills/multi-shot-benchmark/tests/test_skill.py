from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import BenchmarkError, git, load, save
from benchmark import main
from github_context import recover, select
from comparison import measure
from report import build, basis, finding_table
from scheduling import run
from execution import run as execute
import test_scheduling


ASSESSMENT = {'summary': 'The deterministic fixture completed its requested behavior',
              'evidence': ['final.patch', 'driver/1/check.log'],
              'residual_defects': [], 'regressions': [], 'disputed_findings': []}


class SkillTests(unittest.TestCase):
    setUp = test_scheduling.SchedulingTests.setUp
    tearDown = test_scheduling.SchedulingTests.tearDown
    executor = test_scheduling.SchedulingTests.executor
    freeze = test_scheduling.SchedulingTests.freeze

    def command(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            status = main(args)
        self.assertEqual(status, 0)
        return json.loads(stream.getvalue())

    def recommendation(self, measurements):
        return {'basis': basis(measurements), 'recommendation': 'Inconclusive; collect real historical cases',
                'reasoning': 'Fixture results validate the workflow only. They do not compare models.',
                'priorities': 'Correctness first; measure additional cost before adoption',
                'uncertainty': 'Test doubles have no representative defect recall or complete model usage. Shared host load affects time.',
                'evidence': [measurements['executions'][0]['id']],
                'case_notes': {c['name']: 'Small fixture change; useful for workflow checks only' for c in measurements['cases']}}

    def assess_all(self, measurements):
        path = self.root / 'assessment.json'
        save(path, ASSESSMENT)
        for row in measurements['executions']:
            self.command(['assess', '--run', str(self.output), '--execution', row['id'], '--assessment', str(path)])

    def test_full_pilot_interface_and_durable_decision_report(self):
        spec = self.root / 'spec.json'
        save(spec, self.spec)
        value = self.command(['setup', '--spec', str(spec), '--run', str(self.output),
                              '--benchmark-root', str(self.root / 'runs'), '--worktree-root', str(self.root / 'worktrees')])
        self.assertEqual(value['assignments'], 20)
        finding_assignment = load(self.output / 'manifest.json')['assignments'][0]['id']
        def pilot_executor(output, profile):
            if output.name != finding_assignment:
                return self.executor(output, profile)
            with self.guard:
                self.calls.append(str(output))
            return execute(output, profile, command_factory=lambda p, prompt, result, schema, cwd:
                           [sys.executable, str(self.fixture), str(output), 'reject', str(result)])
        with patch('benchmark.run', side_effect=lambda output: run(output, executor=pilot_executor)):
            result = self.command(['run', '--run', str(self.output)])
            self.assertEqual(result['completed'], 20)
            self.command(['resume', '--run', str(self.output)])
        self.assertEqual(len(self.calls), 20)
        self.assertEqual(self.command(['progress', '--run', str(self.output)])['completed'], 20)
        self.command(['measure', '--run', str(self.output)])
        self.assess_all(load(self.output / 'measurements.json'))
        measured = self.command(['measure', '--run', str(self.output)])
        measurements = load(self.output / 'measurements.json')
        decision = self.root / 'decision.json'
        save(decision, self.recommendation(measurements))
        result = self.command(['report', '--run', str(self.output), '--decision', str(decision)])
        self.assertEqual(result['status'], 'final')
        self.assertEqual(result['basis'], measured['basis'])
        directory = Path(result['directory'])
        report = load(directory / 'report.json')
        self.assertEqual(len(report['measurements']['executions']), 20)
        self.assertEqual(len(report['measurements']['paired']), 10)
        self.assertTrue(all(p['complete'] for p in report['measurements']['paired']))
        self.assertEqual(report['measurements']['arm_totals']['a']['assessment_coverage']['current'], 10)
        self.assertTrue(all(r['cost']['estimate'] is None for r in report['measurements']['executions']))
        self.assertTrue(all(r['roles']['driver']['tokens']['input']['total'] == 80 for r in report['measurements']['executions']))
        self.assertEqual(report['decision']['recommendation'], 'Inconclusive; collect real historical cases')
        markdown = (directory / 'report.md').read_text()
        for expected in ('## Arm comparison', '### Arm a', '## Paired comparisons',
                         '### case-001', 'Fix behavior', 'Fixture finding', 'code:1-1',
                         'Parent final-code assessment:', ASSESSMENT['summary'],
                         'Final validated checks:', 'fixture-check', '| driver |',
                         'Input', 'Cache read', 'known subtotal', 'First pass:', 'Full process:'):
            self.assertIn(expected, markdown)
        self.command(['cleanup', '--run', str(self.output)])
        self.assertTrue((directory / 'report.json').is_file())
        for row in report['measurements']['executions']:
            output = Path(row['output'])
            self.assertTrue((output / 'cleanup-evidence/code-state.json').is_file())
            self.assertFalse(Path(load(output / 'replay.json')['worktree']).exists())

    def test_draft_missing_assessments_and_stale_decision(self):
        self.spec['cases'] = self.spec['cases'][:1]
        self.spec['repetitions'] = 1
        self.freeze()
        run(self.output, executor=self.executor)
        draft = build(self.output)
        self.assertEqual(draft['status'], 'draft')
        value = load(Path(draft['directory']) / 'report.json')
        with self.assertRaises(BenchmarkError):
            build(self.output, recommendation=self.recommendation(value['measurements']))
        self.assess_all(value['measurements'])
        measurements = measure(self.output)
        recommendation = self.recommendation(measurements)
        output = Path(measurements['executions'][0]['output'])
        (output / 'driver/1/stdout.log').write_text('Changed usage evidence')
        with self.assertRaises(BenchmarkError):
            build(self.output, recommendation=recommendation)
        self.assertTrue((Path(draft['directory']) / 'report.md').exists())

    def test_github_recovery_is_parent_selected_and_reads_no_reviews(self):
        git(self.repo, 'remote', 'add', 'origin', 'git@github.com:owner/repo.git')
        sha = self.spec['cases'][0]['head']
        calls = []
        def requester(command):
            calls.append(command)
            if 'api' in command:
                return [{'number': 7, 'html_url': 'https://github.com/owner/repo/pull/7',
                         'title': 'Original task', 'body': 'Original requirements. Later outcome: fixed defect.',
                         'created_at': '2026-01-01', 'updated_at': '2026-02-01'}]
            if 'pr' in command:
                return {'closingIssuesReferences': [{'number': 9, 'url': 'https://github.com/other/project/issues/9'}]}
            return {'number': 9, 'title': 'Required behavior', 'body': 'Original acceptance criteria',
                    'url': 'https://github.com/other/project/issues/9', 'createdAt': '2026-01-01', 'updatedAt': '2026-01-01'}
        value = recover(self.repo, sha, requester=requester)
        self.assertEqual(len(value['candidates']), 1)
        self.assertIn('https://github.com/other/project/issues/9', calls[-1])
        self.assertFalse(any('comments' in arg or 'reviews' in arg for call in calls for arg in call))
        task = self.root / 'selected.md'
        selected = select(value, 7, task, selected_text='Original acceptance criteria')
        self.assertNotIn('Later outcome', task.read_text())
        self.spec['cases'] = [{'head': sha, 'task_file': 'selected.md'}]
        spec = self.root / 'spec.json'
        save(spec, self.spec)
        self.command(['setup', '--spec', str(spec), '--run', str(self.output),
                      '--benchmark-root', str(self.root / 'runs'), '--worktree-root', str(self.root / 'worktrees')])
        manifest = load(self.output / 'manifest.json')
        self.assertEqual(manifest['cases'][0]['provenance'], selected)
        self.assertEqual((self.output / manifest['cases'][0]['task_file']).read_text(), 'Original acceptance criteria')
        task.write_text('Later changed requirements')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['setup', '--spec', str(spec), '--run', str(self.root / 'runs/two')])
        self.assertFalse((self.root / 'runs/two').exists())

    def test_linked_issue_failure_does_not_stop_recovery(self):
        git(self.repo, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        def requester(command):
            if 'api' in command:
                return [{'number': 7, 'html_url': 'https://github.com/owner/repo/pull/7', 'title': 'Task'}]
            if 'pr' in command:
                return {'closingIssuesReferences': [{'url': 'https://github.com/owner/repo/issues/1'},
                                                    {'url': 'https://github.com/owner/repo/issues/2'}]}
            if command[3].endswith('/1'):
                raise BenchmarkError('Issue unavailable')
            return {'url': command[3], 'title': 'Required behavior', 'body': 'Acceptance criteria'}
        value = recover(self.repo, self.spec['cases'][0]['head'], requester=requester)
        self.assertEqual(value['candidates'][0]['issues'][0]['body'], 'Acceptance criteria')
        self.assertEqual(len(value['errors']), 1)
        self.assertIn('/issues/1', value['errors'][0])

    def test_recovery_reads_body_references_even_when_link_query_fails(self):
        git(self.repo, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        calls = []
        def requester(command):
            if 'api' in command:
                return [{'number': 7, 'html_url': 'https://github.com/owner/repo/pull/7', 'title': 'Task',
                         'body': 'Implements #18 and other/project#19. See https://github.com/owner/repo/issues/18.'}]
            if 'pr' in command:
                raise BenchmarkError('Link query unavailable')
            calls.append(command[3])
            return {'url': command[3], 'title': 'Task', 'body': 'Original requirements'}
        value = recover(self.repo, self.spec['cases'][0]['head'], requester=requester)
        self.assertEqual(set(calls), {'https://github.com/owner/repo/issues/18',
                                     'https://github.com/other/project/issues/19'})
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(value['candidates'][0]['issues']), 2)
        self.assertEqual(len(value['errors']), 1)

    def test_human_report_shows_finding_details(self):
        rows = [{'id': 'f_example', 'severity': 'P1', 'title': 'Preserve input',
                 'content': 'An input | value is lost.\nKeep it.',
                 'location': {'path': 'src/task.py', 'start_line': 12, 'end_line': 14},
                 'disposition': 'retained', 'first_pass': True, 'repeated': False,
                 'resolution_chain': ['f_example'], 'source': 'findings.json'}]
        text = '\n'.join(finding_table(rows))
        self.assertIn('Preserve input', text)
        self.assertIn('An input \\| value is lost.<br>Keep it.', text)
        self.assertIn('src/task.py:12-14', text)

    def test_recovery_failure_requires_a_supplied_task(self):
        git(self.repo, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        def unavailable(command):
            raise BenchmarkError('GitHub unavailable')
        value = recover(self.repo, self.spec['cases'][0]['head'], requester=unavailable)
        self.assertEqual(value['candidates'], [])
        self.assertTrue(value['selection_required'])
        self.assertTrue(value['errors'])
        self.spec['cases'] = [{'head': self.spec['cases'][0]['head']}]
        with self.assertRaises(BenchmarkError):
            self.freeze()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
