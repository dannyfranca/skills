"""Codex harness adapter."""

from __future__ import annotations

from pathlib import Path

from review_result import JUDGE_SCHEMA_PATH, RESULT_SCHEMA_PATH

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
        return _structured_invocation(
            prompt=prompt,
            output_file=output_file,
            profile=profile,
            schema_path=RESULT_SCHEMA_PATH,
        )

    def judge_invocation(
        self,
        *,
        prompt: str,
        output_file: Path,
        profile: ResolvedProfile,
    ) -> Invocation:
        return _structured_invocation(
            prompt=prompt,
            output_file=output_file,
            profile=profile,
            schema_path=JUDGE_SCHEMA_PATH,
        )


def _structured_invocation(
    *,
    prompt: str,
    output_file: Path,
    profile: ResolvedProfile,
    schema_path: Path,
) -> Invocation:
    cmd = ["codex", "exec", "--ephemeral", "--sandbox", "read-only"]
    _append_profile(cmd, profile)
    cmd.extend(["-c", "project_doc_fallback_filenames=[]"])
    cmd.extend(["--output-schema", str(schema_path)])
    cmd.extend(["-o", str(output_file), prompt])
    return Invocation(cmd)


def _append_profile(cmd: list[str], profile: ResolvedProfile) -> None:
    if profile.model is not None:
        cmd.extend(["-m", profile.model])
    if profile.reasoning is not None:
        cmd.extend(["-c", f'model_reasoning_effort="{profile.reasoning}"'])
