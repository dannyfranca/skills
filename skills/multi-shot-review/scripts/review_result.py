"""Validate reviewer results and render stable human-readable artifacts."""

from __future__ import annotations

import json
import secrets
import string
from pathlib import Path
from typing import Any, Iterable

RESULT_SCHEMA_VERSION = 1
FINDING_ID_ALPHABET = string.ascii_letters + string.digits + "_-"
FINDING_ID_LENGTH = 8
SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
FINDING_STATUSES = frozenset({"open", "ignored", "superseded"})
RESULT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references" / "review-result.schema.json"


class ReviewResultError(ValueError):
    """Raised when a reviewer result does not match the public result schema."""


def parse_review_result(text: str) -> list[dict[str, Any]]:
    """Return normalized findings from a strict review-result document."""
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewResultError(f"review output is not valid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ReviewResultError("review result must be an object")
    _require_exact_keys(result, {"schema_version", "findings"}, "review result")
    if (
        isinstance(result["schema_version"], bool)
        or not isinstance(result["schema_version"], int)
        or result["schema_version"] != RESULT_SCHEMA_VERSION
    ):
        raise ReviewResultError(
            f"review result schema_version must be {RESULT_SCHEMA_VERSION}"
        )
    if not isinstance(result["findings"], list):
        raise ReviewResultError("review result findings must be an array")
    return [_validate_finding(value, index) for index, value in enumerate(result["findings"])]


def assign_finding_ids(
    findings: Iterable[dict[str, Any]],
    *,
    used_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    used = set() if used_ids is None else set(used_ids)
    enriched: list[dict[str, Any]] = []
    for finding in findings:
        finding_id = _new_finding_id(used)
        used.add(finding_id)
        enriched.append(
            {
                "id": finding_id,
                **finding,
                "status": "open",
                "resolution": None,
            }
        )
    return enriched


def validate_stored_finding(value: Any, *, owner: str = "finding") -> dict[str, Any]:
    """Validate a finding after the runner has assigned identity and lifecycle state."""
    if not isinstance(value, dict):
        raise ReviewResultError(f"{owner} must be an object")
    _require_exact_keys(
        value,
        {"id", "severity", "title", "content", "location", "status", "resolution"},
        owner,
    )
    finding_id = value["id"]
    if (
        not isinstance(finding_id, str)
        or not finding_id.startswith("f_")
        or len(finding_id) != FINDING_ID_LENGTH + 2
        or any(character not in FINDING_ID_ALPHABET for character in finding_id[2:])
    ):
        raise ReviewResultError(f"{owner} has invalid id")
    normalized = _validate_finding(
        {key: value[key] for key in ("severity", "title", "content", "location")},
        owner,
    )
    status = value["status"]
    if not isinstance(status, str) or status not in FINDING_STATUSES:
        raise ReviewResultError(f"{owner} has invalid status")
    resolution = _validate_resolution(value["resolution"], status=status, owner=owner)
    return {"id": finding_id, **normalized, "status": status, "resolution": resolution}


def render_review_markdown(
    findings: list[dict[str, Any]],
    *,
    model: str | None,
    model_source: str,
    reasoning: str | None,
    reasoning_source: str,
) -> str:
    model_value = "null" if model is None else json.dumps(model)
    reasoning_value = "null" if reasoning is None else json.dumps(reasoning)
    outcome = "findings" if findings else "no_findings"
    lines = [
        "---",
        f"model: {model_value}",
        f"model_source: {json.dumps(model_source)}",
        f"reasoning: {reasoning_value}",
        f"reasoning_source: {json.dumps(reasoning_source)}",
        f"schema_version: {RESULT_SCHEMA_VERSION}",
        f"outcome: {outcome}",
        "---",
        "",
    ]
    if not findings:
        lines.extend(["No findings.", ""])
        return "\n".join(lines)

    lines.extend(["# Review findings", ""])
    for finding in findings:
        location = finding["location"]
        start_line = location["start_line"]
        end_line = location["end_line"]
        line_range = str(start_line) if end_line == start_line else f"{start_line}-{end_line}"
        lines.extend(
            [
                f"## {finding['severity']} · {finding['title']} · {finding['id']}",
                "",
                f"**Location:** `{location['path']}:{line_range}`",
                "",
                finding["content"],
                "",
            ]
        )
        resolution = finding.get("resolution")
        if finding.get("status") == "ignored" and isinstance(resolution, dict):
            if resolution.get("kind") == "duplicate":
                detail = f"Duplicate of `{resolution['finding_id']}`."
            else:
                detail = str(resolution["text"])
            lines.extend(["### Resolution", "", f"Ignored: {detail}", ""])
        elif finding.get("status") == "superseded" and isinstance(resolution, dict):
            if "successor_run_id" in resolution:
                detail = f"run `{resolution['successor_run_id']}`"
            elif resolution.get("removed") is True:
                detail = "slice removal"
            else:
                detail = f"slice definition {resolution['definition_version']}"
            lines.extend(
                [
                    "### Resolution",
                    "",
                    f"Superseded by {detail}.",
                    "",
                ]
            )
    return "\n".join(lines)


def render_review_failure_markdown(
    error: str,
    *,
    model: str | None,
    model_source: str,
    reasoning: str | None,
    reasoning_source: str,
) -> str:
    """Render a human-readable record for output that could not become findings."""
    model_value = "null" if model is None else json.dumps(model)
    reasoning_value = "null" if reasoning is None else json.dumps(reasoning)
    return "\n".join(
        [
            "---",
            f"model: {model_value}",
            f"model_source: {json.dumps(model_source)}",
            f"reasoning: {reasoning_value}",
            f"reasoning_source: {json.dumps(reasoning_source)}",
            f"schema_version: {RESULT_SCHEMA_VERSION}",
            "outcome: failed",
            "---",
            "",
            "# Review failed",
            "",
            error,
            "",
        ]
    )


def _validate_finding(value: Any, index: int | str) -> dict[str, Any]:
    owner = f"finding {index}" if isinstance(index, int) else index
    if not isinstance(value, dict):
        raise ReviewResultError(f"{owner} must be an object")
    _require_exact_keys(value, {"severity", "title", "content", "location"}, owner)
    severity = value["severity"]
    if not isinstance(severity, str) or severity not in SEVERITIES:
        raise ReviewResultError(f"{owner} severity must be P0, P1, P2, or P3")
    title = _non_empty_string(value["title"], f"{owner} title")
    content = _non_empty_string(value["content"], f"{owner} content")
    location = value["location"]
    if not isinstance(location, dict):
        raise ReviewResultError(f"{owner} location must be an object")
    location_keys = {"path", "start_line", "end_line"}
    if set(location) != location_keys:
        raise ReviewResultError(
            f"{owner} location must contain exactly path, start_line, and end_line"
        )
    path = _non_empty_string(location["path"], f"{owner} location path")
    start_line = _positive_int(location["start_line"], f"{owner} location start_line")
    end_line = (
        start_line
        if location["end_line"] is None
        else _positive_int(location["end_line"], f"{owner} location end_line")
    )
    if end_line < start_line:
        raise ReviewResultError(f"{owner} location end_line cannot precede start_line")
    return {
        "severity": severity,
        "title": title,
        "content": content,
        "location": {"path": path, "start_line": start_line, "end_line": end_line},
    }


def _validate_resolution(value: Any, *, status: str, owner: str) -> dict[str, Any] | None:
    if status == "open":
        if value is not None:
            raise ReviewResultError(f"{owner} open status requires null resolution")
        return None
    if not isinstance(value, dict):
        raise ReviewResultError(f"{owner} terminal status requires a resolution object")
    kind = value.get("kind")
    if status == "ignored" and kind == "rejected":
        _require_exact_keys(value, {"kind", "text", "at"}, f"{owner} resolution")
        return {
            "kind": kind,
            "text": _non_empty_string(value["text"], f"{owner} resolution text"),
            "at": _non_empty_string(value["at"], f"{owner} resolution at"),
        }
    if status == "ignored" and kind == "duplicate":
        _require_exact_keys(
            value, {"kind", "finding_id", "at"}, f"{owner} resolution"
        )
        canonical_id = value["finding_id"]
        if not isinstance(canonical_id, str) or not canonical_id:
            raise ReviewResultError(
                f"{owner} duplicate resolution requires finding_id"
            )
        return {
            "kind": kind,
            "finding_id": canonical_id,
            "at": _non_empty_string(value["at"], f"{owner} resolution at"),
        }
    if status == "superseded" and kind == "superseded":
        if "successor_run_id" in value:
            _require_exact_keys(
                value, {"kind", "successor_run_id", "at"}, f"{owner} resolution"
            )
            successor = {
                "successor_run_id": _non_empty_string(
                    value["successor_run_id"], f"{owner} successor run id"
                )
            }
        elif "definition_version" in value:
            _require_exact_keys(
                value, {"kind", "definition_version", "at"}, f"{owner} resolution"
            )
            successor = {
                "definition_version": _positive_int(
                    value["definition_version"], f"{owner} definition version"
                )
            }
        else:
            _require_exact_keys(
                value, {"kind", "removed", "at"}, f"{owner} resolution"
            )
            if value["removed"] is not True:
                raise ReviewResultError(f"{owner} removed resolution must be true")
            successor = {"removed": True}
        return {
            "kind": kind,
            **successor,
            "at": _non_empty_string(value["at"], f"{owner} resolution at"),
        }
    raise ReviewResultError(f"{owner} resolution does not match status {status}")


def _require_exact_keys(value: dict[str, Any], expected: set[str], owner: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ReviewResultError(f"{owner} has invalid fields: {'; '.join(details)}")


def _non_empty_string(value: Any, owner: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewResultError(f"{owner} must be a non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReviewResultError(f"{owner} must contain valid Unicode") from exc
    return value.strip()


def _positive_int(value: Any, owner: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewResultError(f"{owner} must be a positive integer")
    return value


def _new_finding_id(used_ids: set[str]) -> str:
    while True:
        candidate = "f_" + "".join(
            secrets.choice(FINDING_ID_ALPHABET) for _ in range(FINDING_ID_LENGTH)
        )
        if candidate not in used_ids:
            return candidate
