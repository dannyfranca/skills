from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from eval_errors import EvalError
from fixtures import PRICES, claude_stdout, codex_stderr, write_logs

from prices import ModelPrice, PriceTable, load_price_table
from prices import main as prices_main
from usage import UsdSource, parse_log_pair, session_usage


TABLE = PriceTable(
    path=Path("unused.toml"),
    models={
        "gpt-6-sol": ModelPrice(
            input=2.0, cached_input=0.2, output=10.0, source="https://example.test", fetched_at=date(2026, 10, 1)
        )
    },
)


class PriceTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "cache" / "prices.toml"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def set_price(self, *args: str, today: date = date(2026, 10, 25)) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return prices_main(["--cache", str(self.path), "set", *args], today=today)

    def test_fixture_cache_loads(self) -> None:
        table = load_price_table(PRICES)
        self.assertEqual(table.models["gpt-6-sol"].fetched_at, date(2026, 10, 20))

    def test_missing_cache_is_empty(self) -> None:
        self.assertEqual(load_price_table(self.path).models, {})

    def test_set_writes_and_refreshes_one_model(self) -> None:
        rates = ["--input", "2", "--cached-input", "0.2", "--output", "10", "--source", "https://a.test"]
        self.assertEqual(self.set_price("gpt-6.1-sol", *rates, today=date(2026, 9, 1)), 0)
        self.assertEqual(self.set_price("claude-x", *rates, "--cache-write", "2.5"), 0)
        self.assertEqual(self.set_price("gpt-6.1-sol", "--input", "3", *rates[2:]), 0)
        table = load_price_table(self.path)
        self.assertEqual(table.models["gpt-6.1-sol"].input, 3.0)
        self.assertEqual(table.models["gpt-6.1-sol"].fetched_at, date(2026, 10, 25))
        self.assertEqual(table.models["claude-x"].cache_write, 2.5)
        self.assertEqual(table.models["claude-x"].source, "https://a.test")

    def test_set_rejects_invalid_rates(self) -> None:
        for field, rate in (("--input", "nan"), ("--output", "inf"), ("--cached-input", "-0.5"), ("--cache-write", "-1")):
            with self.subTest(field=field):
                rates = {"--input": "1", "--cached-input": "0.1", "--output": "5", field: rate}
                args = [item for pair in rates.items() for item in pair]
                self.assertEqual(self.set_price("m", *args, "--source", "https://a.test"), 2)
                self.assertFalse(self.path.exists())

    def test_entry_needs_source_and_date(self) -> None:
        self.path.parent.mkdir(parents=True)
        for extra in (
            'fetched_at = 2026-10-01\n',
            'source = "https://a.test"\n',
            'source = "https://a.test"\nfetched_at = 2026-10-01T00:00:00Z\n',
        ):
            with self.subTest(extra=extra):
                self.path.write_text('[models."m"]\ninput = 1.0\ncached_input = 0.1\noutput = 5.0\n' + extra)
                with self.assertRaises(EvalError):
                    load_price_table(self.path)

    def test_set_reports_unwritable_cache(self) -> None:
        self.path.parent.write_text("a file, not a directory")
        args = ["--input", "1", "--cached-input", "0.1", "--output", "5", "--source", "https://a.test"]
        self.assertEqual(self.set_price("m", *args), 2)

    def test_cache_rates_must_be_numbers(self) -> None:
        self.path.parent.mkdir(parents=True)
        for rate in ("true", '"1.5"', "1" + "0" * 400):
            with self.subTest(rate=rate):
                self.path.write_text(
                    f'[models."m"]\ninput = {rate}\ncached_input = 0.1\noutput = 5.0\n'
                    'source = "https://a.test"\nfetched_at = 2026-10-01\n'
                )
                with self.assertRaises(EvalError):
                    load_price_table(self.path)

    def test_total_only_tokens_use_input_rate(self) -> None:
        self.assertAlmostEqual(TABLE.total_tokens_usd("gpt-6-sol", 1_000_000), 2.0)
        self.assertIsNone(TABLE.total_tokens_usd("unknown", 10))

    def test_staleness_is_per_model(self) -> None:
        self.assertEqual(TABLE.stale_models(["gpt-6-sol", "unknown"], date(2026, 10, 31)), [])
        self.assertEqual(TABLE.stale_models(["gpt-6-sol", "unknown"], date(2026, 11, 1)), ["gpt-6-sol"])


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
        self.assertEqual(usage.priced_models, {"gpt-6-sol"})

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
