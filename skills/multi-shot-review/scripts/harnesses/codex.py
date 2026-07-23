"""Codex harness adapter."""

from __future__ import annotations

from pathlib import Path

from review_result import RESULT_SCHEMA_PATH

from .base import Invocation, ResolvedProfile, ReviewHarness


class CodexHarness(ReviewHarness):
    name = "codex"

    def classifier_invocation(
        self,
        *,
        prompt: str,
        review_dir: Path,
        profile: ResolvedProfile,
        add_slice_script: Path,
        remove_slice_script: Path,
    ) -> Invocation:
        del add_slice_script, remove_slice_script
        cmd = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-C",
            str(review_dir),
        ]
        _append_profile(cmd, profile)
        cmd.extend(["-c", "project_doc_fallback_filenames=[]", prompt])
        return Invocation(cmd)

    def review_invocation(
        self,
        *,
        prompt: str,
        output_file: Path,
        profile: ResolvedProfile,
    ) -> Invocation:
        cmd = ["codex", "exec", "--ephemeral", "--sandbox", "read-only"]
        _append_profile(cmd, profile)
        cmd.extend(["-c", "project_doc_fallback_filenames=[]"])
        cmd.extend(["--output-schema", str(RESULT_SCHEMA_PATH)])
        cmd.extend(["-o", str(output_file), prompt])
        return Invocation(cmd)


def _append_profile(cmd: list[str], profile: ResolvedProfile) -> None:
    if profile.model is not None:
        cmd.extend(["-m", profile.model])
    if profile.reasoning is not None:
        cmd.extend(["-c", f'model_reasoning_effort="{profile.reasoning}"'])
