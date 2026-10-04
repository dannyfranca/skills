from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import execute, save
from isolation import child_command


@unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap unavailable')
class IsolationTests(unittest.TestCase):
    def setUp(self):
        registry_temp = tempfile.TemporaryDirectory()
        self.addCleanup(registry_temp.cleanup)
        registry = patch('storage.registry_path', return_value=Path(registry_temp.name) / 'storage.json')
        registry.start()
        self.addCleanup(registry.stop)

    def test_nested_siblings_history_and_host_proc_are_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            shared = root / 'shared'
            sibling = root / 'runs' / 'arm-b' / 'rep-1'
            output = root / 'runs' / 'arm-a' / 'rep-1'
            worktree = root / 'worktrees' / 'own'
            for path in (source, shared, sibling, output, worktree):
                path.mkdir(parents=True)
                (path / 'sentinel').write_text('secret')
            alias = root / 'source-alias'
            alias.symlink_to(source, target_is_directory=True)
            save(output / 'replay.json', {'status': 'ready', 'source': str(source),
                 'worktree': str(worktree), 'benchmark_root': str(root / 'runs'),
                 'hidden_paths': [str(shared), str(alias / 'sentinel')]})
            hidden = [source / 'sentinel', shared / 'sentinel', sibling / 'sentinel',
                      Path(f'/proc/{os.getpid()}/root') / str(sibling / 'sentinel').lstrip('/')]
            program = 'import pathlib; assert all(not pathlib.Path(p).exists() for p in ' + repr([str(p) for p in hidden]) + '); assert pathlib.Path(' + repr(str(worktree / 'sentinel')) + ').read_text() == "secret"'
            execute(child_command(output, [sys.executable, '-c', program]))

    def test_private_harness_home_retains_readonly_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home, source, output, worktree = [root / n for n in ('home', 'source', 'runs/one', 'worktrees/one')]
            for p in (home / '.codex', source, output, worktree):
                p.mkdir(parents=True)
            (home / '.codex/auth.json').write_text('dummy credential')
            save(output / 'replay.json', {'status': 'ready', 'source': str(source), 'worktree': str(worktree),
                 'benchmark_root': str(root / 'runs'), 'hidden_paths': []})
            program = 'from pathlib import Path; h=Path(' + repr(str(home / '.codex')) + '); assert (h/"auth.json").read_text()=="dummy credential"; (h/"new-state").write_text("private")'
            with patch('isolation.Path.home', return_value=home), patch.dict(os.environ, {'CODEX_HOME': str(home / '.codex')}):
                execute(child_command(output, [sys.executable, '-c', program]))
            self.assertEqual((output / 'runtime/.codex/new-state').read_text(), 'private')
            self.assertFalse((home / '.codex/new-state').exists())

    def test_custom_codex_home_uses_private_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            custom, source, output, worktree = [root / n for n in ('custom', 'source', 'runs/one', 'worktrees/one')]
            for path in (custom, source, output, worktree):
                path.mkdir(parents=True)
            (custom / 'config.toml').write_text('custom config')
            save(output / 'replay.json', {'status': 'ready', 'source': str(source), 'worktree': str(worktree),
                 'benchmark_root': str(root / 'runs'), 'hidden_paths': []})
            program = 'import os; from pathlib import Path; h=Path(os.environ["CODEX_HOME"]); assert (h/"config.toml").read_text()=="custom config"; (h/"new-state").write_text("private")'
            with patch.dict(os.environ, {'CODEX_HOME': str(custom)}):
                execute(child_command(output, [sys.executable, '-c', program]))
            self.assertEqual((output / 'runtime/.codex/new-state').read_text(), 'private')
            self.assertFalse((custom / 'new-state').exists())

    def test_custom_and_absent_claude_home_use_private_state(self):
        for exists in (True, False):
            with self.subTest(exists=exists), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                custom, source, output, worktree = [root / n for n in ('custom', 'source', 'runs/one', 'worktrees/one')]
                for path in (source, output, worktree):
                    path.mkdir(parents=True)
                if exists:
                    custom.mkdir()
                    (custom / '.credentials.json').write_text('dummy')
                save(output / 'replay.json', {'status': 'ready', 'source': str(source), 'worktree': str(worktree),
                     'benchmark_root': str(root / 'runs'), 'hidden_paths': []})
                program = 'import os; from pathlib import Path; h=Path(os.environ["CLAUDE_CONFIG_DIR"]); (h/"new-state").write_text("private")'
                if exists:
                    program += '; assert (h/".credentials.json").read_text()=="dummy"'
                with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(custom)}):
                    execute(child_command(output, [sys.executable, '-c', program]))
                self.assertEqual((output / 'runtime/.claude/new-state').read_text(), 'private')
                self.assertFalse((custom / 'new-state').exists())


if __name__ == '__main__':
    unittest.main()
