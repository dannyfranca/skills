from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

JUDGE_LOG = re.compile(r"^judge-(?P<stamp>\d{8}-\d{4})-.+\.(?:stdout|stderr)\.log$")


def judge_minutes(session_dir: Path) -> float:
    """Judgements store only their end, so judge time comes from the log pairs.

    The log name holds the launch minute in UTC and the last write of the pair marks the end,
    so each duration is precise to about one minute.
    """

    ends: dict[str, tuple[datetime, float]] = {}
    for path in (session_dir / "_logs").glob("judge-*.log"):
        match = JUDGE_LOG.match(path.name)
        if match is None:
            continue
        start = datetime.strptime(match["stamp"], "%Y%m%d-%H%M").replace(tzinfo=UTC)
        pair = path.name.rsplit(".", 2)[0]
        modified = path.stat().st_mtime
        ends[pair] = (start, max(modified, ends.get(pair, (start, modified))[1]))
    return sum(max(0.0, (modified - start.timestamp()) / 60) for start, modified in ends.values())
