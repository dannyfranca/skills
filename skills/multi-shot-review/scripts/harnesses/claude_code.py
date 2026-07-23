"""Claude Code harness adapter."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from review_result import RESULT_SCHEMA_PATH

from .base import HarnessError, Invocation, ResolvedProfile, ReviewHarness


class ClaudeCodeHarness(ReviewHarness):
    name = "claude-code"

    def classifier_invocation(
        self,
        *,
        prompt: str,
        review_dir: Path,
        profile: ResolvedProfile,
        add_slice_script: Path,
        remove_slice_script: Path,
    ) -> Invocation:
        del review_dir
        allowed = (
            f"Bash(python3 {shlex.quote(str(add_slice_script))} *)",
            f"Bash(python3 {shlex.quote(str(remove_slice_script))} *)",
        )
        cmd = _base_command(profile, tools="Bash,Glob,Grep,Read", read_only=False)
        cmd.extend(["--allowedTools", *allowed, "-p", prompt])
        return Invocation(cmd)

    def review_invocation(
        self,
        *,
        prompt: str,
        output_file: Path,
        profile: ResolvedProfile,
    ) -> Invocation:
        del output_file
        schema = json.dumps(
            json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8")),
            separators=(",", ":"),
        )
        cmd = _base_command(profile, tools="Bash,Glob,Grep,Read", read_only=True)
        cmd.extend(
            [
                "--output-format",
                "json",
                "--json-schema",
                schema,
                "-p",
                prompt,
            ]
        )
        return Invocation(cmd)

    def materialize_review_result(
        self,
        *,
        stdout_log: Path,
        output_file: Path,
    ) -> None:
        try:
            envelope = json.loads(stdout_log.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HarnessError(
                f"Claude Code returned an invalid JSON envelope: {exc}"
            ) from exc
        if not isinstance(envelope, dict) or not isinstance(
            envelope.get("structured_output"), dict
        ):
            raise HarnessError("Claude Code result is missing structured_output")
        output_file.write_text(
            json.dumps(envelope["structured_output"], indent=2) + "\n",
            encoding="utf-8",
        )


def _base_command(
    profile: ResolvedProfile,
    *,
    tools: str,
    read_only: bool,
) -> list[str]:
    sandbox: dict[str, object] = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
    }
    if read_only:
        sandbox["filesystem"] = {"denyWrite": ["."]}
    settings = json.dumps(
        {
            "disableAllHooks": True,
            "autoMemoryEnabled": False,
            "sandbox": sandbox,
        },
        separators=(",", ":"),
    )
    cmd = [
        "claude",
        "--no-session-persistence",
        "--settings",
        settings,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--permission-mode",
        "dontAsk",
        "--tools",
        tools,
    ]
    if profile.model is not None:
        cmd.extend(["--model", profile.model])
    if profile.reasoning is not None:
        cmd.extend(["--effort", profile.reasoning])
    return cmd
