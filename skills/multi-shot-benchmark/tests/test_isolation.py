from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_store import execute, save
from isolation import child_command


@unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap unavailable')
class IsolationTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
