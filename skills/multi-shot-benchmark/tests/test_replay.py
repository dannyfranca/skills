from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import BenchmarkError, git, load
from replay import prepare, cleanup, resume


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init')
        git(self.repo, 'config', 'user.email', 'test@example.com')
        git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'old').write_text('old\n')
        (self.repo / 'AGENTS.md').write_text('Historical rules\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'base')
        self.base = git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        (self.repo / 'old').rename(self.repo / 'new')
        (self.repo / 'binary').write_bytes(bytes(range(256)))
        (self.repo / 'run').write_text('#!/bin/sh\nexit 0\n')
        (self.repo / 'run').chmod(0o755)
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'change')
        self.head = git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        (self.repo / 'future').write_text('answer')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'future fix')
        self.future = git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        self.config = self.root / 'arm.toml'
        self.config.write_text('shots = 2\n[slice_default]\nharness = "codex"\nmodel = "test-model"\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_tree_private_history_and_session(self):
        out = self.root / 'runs' / 'one'
        state = prepare(self.repo, self.head, 'Change behavior', self.config, out,
                        worktree_root=self.root / 'worktrees')
        worktree = Path(state['worktree'])
        self.assertEqual(git(worktree, 'write-tree').decode().strip(), state['initial_tree'])
        self.assertFalse((worktree / 'old').exists())
        self.assertEqual((worktree / 'binary').read_bytes(), bytes(range(256)))
        self.assertTrue((worktree / 'run').stat().st_mode & 0o111)
        with self.assertRaises(BenchmarkError):
            git(worktree, 'cat-file', '-e', self.future)
        session = load(Path(state['review_dir']) / '_state.json')
        self.assertEqual(session['session']['config']['shots'], 2)
        self.assertEqual(session['session']['config']['slice_default']['model'], 'test-model')
        cleanup(out)
        self.assertFalse(worktree.exists())
        self.assertTrue(self.repo.exists())

    def test_root_commit_replays_empty_tree(self):
        state = prepare(self.repo, self.base, 'Task', self.config, self.root / 'root-run',
                        worktree_root=self.root / 'worktrees')
        self.assertTrue(state['root_commit'])
        self.assertEqual(git(Path(state['worktree']), 'write-tree').decode().strip(), state['initial_tree'])

    def test_display_settings_and_repeated_output_names(self):
        git(self.repo, 'config', 'diff.noprefix', 'true')
        git(self.repo, 'config', 'diff.context', '0')
        git(self.repo, 'config', 'color.ui', 'always')
        outputs = [self.root / 'runs' / arm / 'rep-1' for arm in ('a', 'b')]
        states = [prepare(self.repo, self.head, 'Task', self.config, out,
                          worktree_root=self.root / 'worktrees', benchmark_root=self.root / 'runs') for out in outputs]
        self.assertNotEqual(states[0]['worktree'], states[1]['worktree'])

    def test_variant_identity_and_resume(self):
        self.config.write_text('shots = 2\n[variants]\nchosen = 1\n[variant.chosen]\n')
        out = self.root / 'chosen'
        state = prepare(self.repo, self.head, 'Task', self.config, out, variant='chosen',
                        worktree_root=self.root / 'worktrees')
        self.assertEqual(load(Path(state['review_dir']) / '_state.json')['session']['variant'], 'chosen')
        state['status'] = 'failed'
        from benchmark_store import save
        save(out / 'replay.json', state)
        self.assertEqual(resume(out)['review_dir'], state['review_dir'])

    def test_future_base_is_rejected(self):
        with self.assertRaises(BenchmarkError):
            prepare(self.repo, self.head, 'Task', self.config, self.root / 'invalid', base=self.future)
        self.assertFalse((self.root / 'invalid').exists())

    def test_missing_prepared_worktree_is_rebuilt_without_answers(self):
        out = self.root / 'rebuild'
        state = prepare(self.repo, self.head, 'Task', self.config, out,
                        worktree_root=self.root / 'worktrees')
        git(out / 'history', 'worktree', 'remove', '--force', state['worktree'])
        restored = resume(out)
        self.assertTrue(Path(restored['worktree']).is_dir())
        self.assertEqual(git(Path(restored['worktree']), 'write-tree').decode().strip(), state['initial_tree'])
        self.assertEqual({p.name for p in (out / 'tool/multi-shot-review').iterdir()},
                         {'scripts', 'references', 'SKILL.md'})

    def test_unicode_config_round_trip(self):
        self.config.write_text('[slice_default]\nharness = "codex"\nmodel = "model-🚀"\n')
        state = prepare(self.repo, self.head, 'Task', self.config, self.root / '🚀',
                        worktree_root=self.root / 'worktrees')
        self.assertEqual(load(Path(state['review_dir']) / '_state.json')['session']['config']['slice_default']['model'], 'model-🚀')

    def test_cleanup_partial_preparation_preserves_evidence(self):
        from benchmark_store import save
        out = self.root / 'partial'
        (out / 'history').mkdir(parents=True)
        save(out / 'replay.json', {'status': 'failed', 'owned': [str(out / 'history')], 'error': 'pack failed'})
        cleanup(out)
        self.assertEqual(load(out / 'replay.json')['status'], 'cleaned')
        self.assertTrue((out / 'history').is_dir())
        self.assertTrue(self.repo.is_dir())

    def test_sha256_source_has_matching_private_store(self):
        repo = self.root / 'sha256'
        repo.mkdir()
        git(repo, 'init', '--object-format=sha256')
        git(repo, 'config', 'user.email', 'test@example.com')
        git(repo, 'config', 'user.name', 'Test')
        (repo / 'code').write_text('content\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'root')
        state = prepare(repo, 'HEAD', 'Task', self.config, self.root / 'sha256-run',
                        worktree_root=self.root / 'worktrees')
        self.assertEqual(state['object_format'], 'sha256')
        self.assertEqual(git(Path(state['worktree']), 'write-tree').decode().strip(), state['initial_tree'])

    def test_unchanged_non_utf8_filename_does_not_block_replay(self):
        import os
        (self.repo / os.fsdecode(b'legacy-\xff.bin')).write_bytes(b'legacy')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'legacy filename')
        (self.repo / 'new').write_text('changed\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'real change')
        state = prepare(self.repo, 'HEAD', 'Task', self.config, self.root / 'legacy-run',
                        worktree_root=self.root / 'worktrees')
        self.assertEqual(git(Path(state['worktree']), 'write-tree').decode().strip(), state['initial_tree'])

    def test_failure_record_preserves_assets(self):
        (self.repo / 'scripts').mkdir()
        (self.repo / 'scripts/hydrate-worktree.sh').write_text('exit 42\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'failed hydration')
        out = self.root / 'failed'
        with self.assertRaises(BenchmarkError):
            prepare(self.repo, 'HEAD', 'Task', self.config, out, worktree_root=self.root / 'worktrees')
        self.assertEqual(load(out / 'replay.json')['status'], 'failed')
        self.assertTrue((out / 'initial.patch').is_file())


if __name__ == '__main__':
    unittest.main()
