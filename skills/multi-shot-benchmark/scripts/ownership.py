from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from benchmark_store import BenchmarkError

@contextmanager
def locked(output: Path):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / f'.{output.name}.execution.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BenchmarkError('Execution is owned by another active launcher') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
