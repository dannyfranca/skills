from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from eval_errors import EvalError
from fixtures import PRICES, claude_stdout, codex_stderr, write_logs

from prices import ModelPrice, PriceTable, load_price_table
from usage import UsdSource, parse_log_pair, session_usage


TABLE = PriceTable(
    updated=date(2026, 10, 1),
    models={"gpt-6-sol": ModelPrice(input=2.0, cached_input=0.2, output=10.0)},
)


class PriceTableTests(unittest.TestCase):
    def test_bundled_table_loads_and_covers_seed_models(self) -> None:
        table = load_price_table(PRICES)
        for model in ("gpt-6-sol", "gpt-6.1-sol", "claude-opus-5-5"):
            self.assertIn(model, table.models)

    def test_invalid_rates_fail_to_load(self) -> None:
        for field, rate in (("input", "nan"), ("output", "inf"), ("cached_input", "-0.5"), ("cache_write", "-inf")):
            with self.subTest(field=field, rate=rate), tempfile.TemporaryDirectory() as tmp:
                rates = {"input": "1.0", "cached_input": "0.1", "output": "5.0", field: rate}
                path = Path(tmp) / "prices.toml"
                path.write_text(
                    'updated = 2026-10-01\n[models."m"]\n' + "".join(f"{key} = {value}\n" for key, value in rates.items())
                )
                with self.assertRaises(EvalError):
                    load_price_table(path)

    def test_total_only_tokens_use_input_rate(self) -> None:
        self.assertAlmostEqual(TABLE.total_tokens_usd("gpt-6-sol", 1_000_000), 2.0)
        self.assertIsNone(TABLE.total_tokens_usd("unknown", 10))

    def test_staleness(self) -> None:
        self.assertFalse(TABLE.is_stale(date(2026, 10, 31)))
        self.assertTrue(TABLE.is_stale(date(2026, 11, 1)))


class ParseLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def pair(self, name: str) -> tuple[Path, Path]:
        logs = self.dir / "_logs"
        return logs / f"{name}.stdout.log", logs / f"{name}.stderr.log"

    def test_claude_result_reports_cost_and_tokens(self) -> None:
        write_logs(self.dir, "judge", stdout=claude_stdout(0.25))
        usage = parse_log_pair(*self.pair("judge"), TABLE)
        self.assertEqual(usage.harness, "claude-code")
        self.assertEqual(usage.model, "claude-opus-5-5")
        self.assertEqual(usage.tokens, 200)
        self.assertAlmostEqual(usage.usd, 0.25)
        self.assertEqual(usage.usd_source, UsdSource.LOG)

    def test_codex_tail_uses_price_table(self) -> None:
        write_logs(self.dir, "run", stdout='{"findings": []}', stderr=codex_stderr("1,000,000"))
        usage = parse_log_pair(*self.pair("run"), TABLE)
        self.assertEqual(usage.harness, "codex")
        self.assertEqual(usage.model, "gpt-6-sol")
        self.assertEqual(usage.tokens, 1_000_000)
        self.assertAlmostEqual(usage.usd, 2.0)
        self.assertEqual(usage.usd_source, UsdSource.PRICE_TABLE)

    def test_codex_without_price_keeps_tokens(self) -> None:
        write_logs(self.dir, "run", stderr=codex_stderr("12", model="gpt-9"))
        usage = parse_log_pair(*self.pair("run"), TABLE)
        self.assertEqual(usage.tokens, 12)
        self.assertIsNone(usage.usd)
        self.assertEqual(usage.usd_source, UsdSource.NONE)

    def test_missing_usage(self) -> None:
        write_logs(self.dir, "run", stdout="plain text", stderr="crashed")
        usage = parse_log_pair(*self.pair("run"), TABLE)
        self.assertIsNone(usage.tokens)
        self.assertIsNone(usage.usd)

    def test_session_usage_reports_coverage(self) -> None:
        write_logs(self.dir, "a", stdout=claude_stdout(0.5))
        write_logs(self.dir, "b", stderr=codex_stderr("100"))
        write_logs(self.dir, "c", stderr="crashed")
        usage = session_usage(self.dir, TABLE)
        self.assertEqual((usage.logs, usage.logs_with_tokens, usage.logs_with_usd), (3, 2, 2))
        self.assertEqual(usage.tokens, 300)
        self.assertFalse(usage.tokens_complete)
        self.assertEqual(usage.usd_from_price_table, 1)

    def test_missing_logs_make_coverage_incomplete(self) -> None:
        write_logs(self.dir, "a", stdout=claude_stdout(0.5))
        self.assertTrue(session_usage(self.dir, TABLE, expected_logs=1).usd_complete)
        usage = session_usage(self.dir, TABLE, expected_logs=2)
        self.assertEqual(usage.expected_logs, 2)
        self.assertFalse(usage.tokens_complete)
        self.assertFalse(usage.usd_complete)

    def test_session_without_logs_has_no_coverage(self) -> None:
        usage = session_usage(self.dir, TABLE)
        self.assertEqual(usage.logs, 0)
        self.assertFalse(usage.tokens_complete)
        self.assertFalse(usage.usd_complete)


if __name__ == "__main__":
    unittest.main()
