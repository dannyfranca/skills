from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from harnesses import (  # noqa: E402
    HarnessError,
    HarnessProfile,
    get_harness,
    resolve_profile,
)
from review_state import ReviewState, init_review_state, run_reviews  # noqa: E402


class ProfileResolutionTests(unittest.TestCase):
    def test_defaults_to_codex_and_harness_defaults(self) -> None:
        profile = resolve_profile(None, override_source="slice-override")

        self.assertEqual(profile.harness, "codex")
        self.assertEqual(profile.harness_source, "built-in-default")
        self.assertIsNone(profile.model)
        self.assertEqual(profile.model_source, "harness-default")
        self.assertIsNone(profile.reasoning)
        self.assertEqual(profile.reasoning_source, "harness-default")

    def test_same_harness_override_preserves_configured_choices(self) -> None:
        profile = resolve_profile(
            HarnessProfile("codex", model="gpt-x", reasoning="high"),
            harness="codex",
            override_source="slice-override",
        )

        self.assertEqual(profile.harness_source, "slice-override")
        self.assertEqual(profile.model, "gpt-x")
        self.assertEqual(profile.model_source, "configured-default")
        self.assertEqual(profile.reasoning, "high")

    def test_harness_override_is_canonicalized_before_comparison(self) -> None:
        profile = resolve_profile(
            HarnessProfile("codex", model="gpt-x", reasoning="high"),
            harness=" codex ",
            override_source="slice-override",
        )

        self.assertEqual(profile.harness, "codex")
        self.assertEqual(profile.model, "gpt-x")
        self.assertEqual(profile.reasoning, "high")

    def test_different_harness_override_clears_configured_choices(self) -> None:
        profile = resolve_profile(
            HarnessProfile("codex", model="gpt-x", reasoning="high"),
            harness="claude-code",
            override_source="slice-override",
        )

        self.assertEqual(profile.harness, "claude-code")
        self.assertIsNone(profile.model)
        self.assertEqual(profile.model_source, "harness-default")
        self.assertIsNone(profile.reasoning)
        self.assertEqual(profile.reasoning_source, "harness-default")

    def test_rejects_unknown_harness(self) -> None:
        with self.assertRaisesRegex(HarnessError, "unsupported harness"):
            resolve_profile(None, harness="other", override_source="slice-override")


class ClaudeCodeHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = resolve_profile(
            HarnessProfile("claude-code", model="sonnet", reasoning="high"),
            override_source="slice-override",
        )
        self.harness = get_harness("claude-code")

    def test_review_invocation_is_non_persistent_and_structured(self) -> None:
        invocation = self.harness.review_invocation(
            prompt="Review this change.",
            output_file=Path("unused.json"),
            profile=self.profile,
        )
        cmd = invocation.command

        self.assertEqual(cmd[0], "claude")
        self.assertIn("--no-session-persistence", cmd)
        settings = json.loads(cmd[cmd.index("--settings") + 1])
        self.assertTrue(settings["disableAllHooks"])
        self.assertFalse(settings["autoMemoryEnabled"])
        self.assertEqual(
            settings["sandbox"],
            {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "filesystem": {"denyWrite": ["."]},
            },
        )
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(
            json.loads(cmd[cmd.index("--mcp-config") + 1]),
            {"mcpServers": {}},
        )
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Bash,Glob,Grep,Read")
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "high")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "json")
        self.assertEqual(json.loads(cmd[cmd.index("--json-schema") + 1])["type"], "object")
        self.assertEqual(cmd[-2:], ["-p", "Review this change."])

    def test_classifier_limits_mutating_bash_to_slice_scripts(self) -> None:
        add_script = Path("/skill/scripts/add_slice.py")
        remove_script = Path("/skill/scripts/remove_slice.py")

        invocation = self.harness.classifier_invocation(
            prompt="Classify.",
            review_dir=Path("/review"),
            profile=self.profile,
            add_slice_script=add_script,
            remove_slice_script=remove_script,
        )

        allowed_index = invocation.command.index("--allowedTools")
        allowed = invocation.command[allowed_index + 1 : -2]
        self.assertIn(f"Bash(python3 {add_script} *)", allowed)
        self.assertIn(f"Bash(python3 {remove_script} *)", allowed)
        self.assertEqual(invocation.command[-2:], ["-p", "Classify."])
        settings = json.loads(
            invocation.command[invocation.command.index("--settings") + 1]
        )
        self.assertNotIn("filesystem", settings["sandbox"])

    def test_materializes_shared_result_from_claude_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout_log = Path(tmp) / "stdout.json"
            output_file = Path(tmp) / "result.json"
            expected = {"schema_version": 1, "findings": []}
            stdout_log.write_text(
                json.dumps({"structured_output": expected}),
                encoding="utf-8",
            )

            self.harness.materialize_review_result(
                stdout_log=stdout_log,
                output_file=output_file,
            )

            self.assertEqual(json.loads(output_file.read_text(encoding="utf-8")), expected)

    def test_rejects_missing_structured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout_log = Path(tmp) / "stdout.json"
            stdout_log.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(HarnessError, "missing structured_output"):
                self.harness.materialize_review_result(
                    stdout_log=stdout_log,
                    output_file=Path(tmp) / "result.json",
                )


class ClaudeCodeRunnerIntegrationTests(unittest.TestCase):
    def test_runner_normalizes_claude_output_and_records_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            review_dir = init_review_state(root, "Review this change.")
            with ReviewState.locked(review_dir) as state:
                state.add_slice(
                    name="claude",
                    mode="native",
                    target={"uncommitted": True},
                    prompt=None,
                    cwd=root,
                    harness="claude-code",
                    harness_source="slice-override",
                    model="sonnet",
                    model_source="slice-override",
                    reasoning="high",
                    reasoning_source="slice-override",
                )
                state.save()

            def runner(cmd, cwd, input_text, output_file, slice_data):
                del cwd, input_text, output_file, slice_data
                payload = {"structured_output": {"schema_version": 1, "findings": []}}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

            rc, summary = run_reviews(review_dir, command_runner=runner)

            self.assertEqual(rc, 0)
            self.assertTrue(summary["ok"])
            state = ReviewState.load(review_dir)
            run = state.data["slices"]["claude"]["runs"][0]
            self.assertEqual(run["status"], "no_findings")
            artifact = Path(run["output_file"]).read_text(encoding="utf-8")
            self.assertIn('harness: "claude-code"', artifact)
            self.assertIn('harness_source: "slice-override"', artifact)

    def test_materialization_failure_is_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            review_dir = init_review_state(root, "Review this change.")
            with ReviewState.locked(review_dir) as state:
                state.add_slice(
                    name="claude",
                    mode="native",
                    target={"uncommitted": True},
                    prompt=None,
                    cwd=root,
                    harness="claude-code",
                    harness_source="slice-override",
                )
                state.save()

            def runner(cmd, cwd, input_text, output_file, slice_data):
                del cwd, input_text, output_file, slice_data
                return subprocess.CompletedProcess(cmd, 0, "{}", "")

            adapter = get_harness("claude-code")
            with mock.patch.object(
                adapter,
                "materialize_review_result",
                side_effect=OSError("cannot write normalized result"),
            ):
                rc, summary = run_reviews(review_dir, command_runner=runner)

            self.assertEqual(rc, 2)
            self.assertFalse(summary["ok"])
            run = ReviewState.load(review_dir).data["slices"]["claude"]["runs"][0]
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["exit_code"], 1)
            stderr = Path(summary["err"][0]["stderr"]).read_text(encoding="utf-8")
            self.assertIn("cannot write normalized result", stderr)


if __name__ == "__main__":
    unittest.main()
