from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from prices import PriceTable


# Codex stderr logs reach hundreds of KB; the model header and token total sit at the edges.
HEAD_BYTES = 4096
TAIL_BYTES = 8192
CODEX_MODEL_RE = re.compile(r"^model: (\S+)$", re.MULTILINE)
CODEX_TOKENS_RE = re.compile(r"tokens used\s*\n\s*([\d,]+)")


class UsdSource(StrEnum):
    LOG = "log"
    PRICE_TABLE = "price_table"
    NONE = "none"


@dataclass(frozen=True)
class LogUsage:
    harness: str | None
    model: str | None
    tokens: int | None
    usd: float | None
    usd_source: UsdSource


@dataclass(frozen=True)
class SessionUsage:
    logs: int
    logs_with_tokens: int
    logs_with_usd: int
    tokens: int
    usd: float
    usd_from_price_table: int = 0
    unpriced_models: frozenset[str] = frozenset()
    expected_logs: int = 0

    @property
    def tokens_complete(self) -> bool:
        return self._all_logs_present and self.logs_with_tokens == self.logs

    @property
    def usd_complete(self) -> bool:
        return self._all_logs_present and self.logs_with_usd == self.logs

    @property
    def _all_logs_present(self) -> bool:
        return self.logs > 0 and self.logs >= self.expected_logs


def parse_log_pair(stdout: Path, stderr: Path, prices: PriceTable) -> LogUsage:
    """Detect the log format from content, since harness names in file names are not reliable."""

    claude = _claude_result(_read_text(stdout))
    if claude is not None:
        return claude
    head, tail = _edges(stderr)
    tokens_match = CODEX_TOKENS_RE.findall(tail)
    if not tokens_match:
        return LogUsage(harness=None, model=None, tokens=None, usd=None, usd_source=UsdSource.NONE)
    model_match = CODEX_MODEL_RE.search(head)
    model = model_match.group(1) if model_match else None
    tokens = int(tokens_match[-1].replace(",", ""))
    usd = prices.total_tokens_usd(model, tokens)
    return LogUsage(
        harness="codex",
        model=model,
        tokens=tokens,
        usd=usd,
        usd_source=UsdSource.NONE if usd is None else UsdSource.PRICE_TABLE,
    )


def session_usage(session_dir: Path, prices: PriceTable, *, expected_logs: int = 0) -> SessionUsage:
    logs = [
        parse_log_pair(stdout, stdout.with_name(stdout.name.removesuffix(".stdout.log") + ".stderr.log"), prices)
        for stdout in sorted((session_dir / "_logs").glob("*.stdout.log"))
    ]
    return SessionUsage(
        logs=len(logs),
        logs_with_tokens=sum(log.tokens is not None for log in logs),
        logs_with_usd=sum(log.usd is not None for log in logs),
        tokens=sum(log.tokens or 0 for log in logs),
        usd=sum(log.usd or 0.0 for log in logs),
        usd_from_price_table=sum(log.usd_source == UsdSource.PRICE_TABLE for log in logs),
        unpriced_models=frozenset(log.model for log in logs if log.model and log.tokens is not None and log.usd is None),
        expected_logs=expected_logs,
    )


def log_model(stdout: Path, stderr: Path) -> str | None:
    claude = _claude_result(_read_text(stdout))
    if claude is not None:
        return claude.model
    match = CODEX_MODEL_RE.search(_edges(stderr)[0])
    return match.group(1) if match else None


def _claude_result(text: str) -> LogUsage | None:
    result = _last_json_object(text)
    if result is None or not isinstance(result.get("modelUsage"), dict) or not result["modelUsage"]:
        return None
    tokens = 0
    model, model_cost = None, -1.0
    for name, fields in result["modelUsage"].items():
        if not isinstance(fields, dict):
            continue
        tokens += sum(
            _count(fields.get(key))
            for key in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        )
        cost = _number(fields.get("costUSD")) or 0.0
        if cost > model_cost:
            model, model_cost = name, cost
    usd = _number(result.get("total_cost_usd"))
    return LogUsage(
        harness="claude-code",
        model=model,
        tokens=tokens,
        usd=usd,
        usd_source=UsdSource.NONE if usd is None else UsdSource.LOG,
    )


def _last_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    candidates = [stripped, *reversed(stripped.splitlines())]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _edges(path: Path) -> tuple[str, str]:
    try:
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES)
            size = handle.seek(0, 2)
            handle.seek(max(0, size - TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return "", ""
    return head.decode(errors="replace"), tail.decode(errors="replace")


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
