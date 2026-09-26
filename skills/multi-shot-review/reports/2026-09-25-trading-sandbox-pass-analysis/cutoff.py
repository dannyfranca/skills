"""Shared session cutoff so every report script reads the same 2026-09-25 population."""
import os
from datetime import datetime

# The source sessions are live: later sessions add files and running sessions keep changing.
# The cutoff keeps later sessions out; changes to older sessions after the report date still drift.
CREATED_BEFORE = os.environ.get("MSR_CREATED_BEFORE", "2026-09-25T22:40:00Z")


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def in_cutoff(state):
    return _ts(state["session"]["created_at"]) < _ts(CREATED_BEFORE)
