from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from eval_dir_fixture import analyst_document, load_entries, load_stats, synthesis_document, view, write_eval_dir

from render_dashboard import main


class RenderDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.entries = load_entries()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def invoke(self, eval_dir: Path) -> tuple[int, dict[str, Any], str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(eval_dir)])
        return code, json.loads(out.getvalue()) if out.getvalue() else {}, err.getvalue()

    def render(self, **kwargs: Any) -> str:
        code, result, err = self.invoke(write_eval_dir(self.root, **kwargs))
        self.assertEqual(code, 0, err)
        return Path(result["dashboard"]).read_text()

    def analyst(self, **kwargs: Any) -> dict[str, Any]:
        return analyst_document("claude-code/claude-opus-5-5", self.entries, **kwargs)

    def test_result_follows_contract(self) -> None:
        code, result, _ = self.invoke(write_eval_dir(self.root))
        self.assertEqual(code, 0)
        self.assertTrue(result["dashboard"].endswith("dashboard.html"))
        self.assertEqual(result["agreement"][0]["agreement"], 1.0)
        self.assertEqual(result["agreement"][0]["kappa"], 1.0)
        self.assertEqual(set(result["sample_accuracy"]), {"a", "b"})

    def test_contract_breaches_exit_2(self) -> None:
        first = self.entries[0]
        unjudgeable = next(e for e in self.entries if e["judgeability"] == "unjudgeable")
        stranger = {"session_id": "owner/repo/nope", "finding_id": "x", "verdict": "valid", "reason": "r"}
        duplicate = self.analyst()
        duplicate["judgments"].append(dict(duplicate["judgments"][-1]))
        extra = self.analyst()
        extra["judgments"].append(stranger)
        missing = self.analyst()
        missing["judgments"].pop()
        cases: list[tuple[str, dict[str, Any]]] = [
            ("unknown verdict", {"analysts": [self.analyst(verdict=lambda e: "dubious" if e is first else "unjudgeable" if e["judgeability"] == "unjudgeable" else "valid")]}),
            ("unknown proposal", {"analysts": [self.analyst(views=[view("b", proposal="ship_it")])]}),
            ("unknown confidence", {"analysts": [self.analyst(views=[view("b", confidence="certain")])]}),
            ("missing judgment", {"analysts": [missing]}),
            ("unknown entry", {"analysts": [extra]}),
            ("duplicate judgment", {"analysts": [duplicate]}),
            ("judged unjudgeable", {"analysts": [self.analyst(verdict=lambda e: "valid" if e is unjudgeable else "unjudgeable" if e["judgeability"] == "unjudgeable" else "valid")]}),
            ("missing view", {"analysts": [self.analyst(views=[view("b", proposal="new_variant")])]}),
            ("unknown arm view", {"analysts": [self.analyst(views=[view("b"), view("z", proposal="new_variant")])]}),
            ("no analysts", {"analysts": []}),
            ("synthesis proposal", {"synthesis": {"recommendations": [view("b", proposal="ship_it")], "analysts_missing": []}}),
            ("synthesis empty", {"synthesis": {"recommendations": [], "analysts_missing": []}}),
            ("synthesis missing reason", {"synthesis": synthesis_document([{"analyst": "codex/gpt-6.1-sol"}])}),
            ("synthesis missing a loaded analyst", {"synthesis": synthesis_document([{"analyst": "codex/gpt-6.1-sol", "reason": "quota"}])}),
            ("analyst absent without a reason", {"analysts": [self.analyst()]}),
            ("substituted analyst", {"analysts": [self.analyst(), analyst_document("codex/gpt-5", self.entries)]}),
            ("unknown missing analyst", {"analysts": [self.analyst()], "synthesis": synthesis_document([{"analyst": "codex/gpt-6.1-sol", "reason": "quota"}, {"analyst": "codex/gpt-5", "reason": "quota"}])}),
            ("synthesis without the compared arm", {"synthesis": {"recommendations": [view("b", proposal="new_variant")], "analysts_missing": []}}),
            ("synthesis with two base proposals", {"synthesis": {"recommendations": [view("b"), view("b", proposal="drop")], "analysts_missing": []}}),
            ("synthesis base proposal for the baseline", {"synthesis": {"recommendations": [view("b"), view("a")], "analysts_missing": []}}),
        ]
        for name, kwargs in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                code, result, err = self.invoke(write_eval_dir(Path(root), **kwargs))
                self.assertEqual(code, 2)
                self.assertEqual(result, {})
                self.assertTrue(err.startswith("error: "))

    def test_one_analyst_with_a_missing_reason_renders(self) -> None:
        missing = [{"analyst": "codex/gpt-6.1-sol", "reason": "quota exceeded"}]
        code, result, err = self.invoke(write_eval_dir(self.root, analysts=[self.analyst()], synthesis=synthesis_document(missing)))
        self.assertEqual(code, 0, err)
        self.assertEqual(result["agreement"], [])
        self.assertIn("quota exceeded", Path(result["dashboard"]).read_text())

    def test_arm_without_sampled_findings_keeps_an_accuracy_row(self) -> None:
        entries = [entry for entry in self.entries if entry["arm"] != "b"]
        analysts = [analyst_document(name, entries) for name in ("claude-code/claude-opus-5-5", "codex/gpt-6.1-sol")]
        eval_dir = write_eval_dir(self.root, analysts=analysts)
        (eval_dir / "sample.json").write_text(json.dumps({"entries": entries, "judgeability_notes": {}}))
        code, result, err = self.invoke(eval_dir)
        self.assertEqual(code, 0, err)
        row = result["sample_accuracy"]["b"]["codex/gpt-6.1-sol"]
        self.assertEqual(row, {"kept_valid": None, "kept_n": 0, "rejected_invalid": None, "rejected_n": 0})

    def test_sections_in_order(self) -> None:
        page = self.render()
        markers = ["<header>", 'id="recommendations"', 'id="forest"', 'id="trend"', 'id="strata"', 'id="worsening"', 'id="finding-sample"', 'id="analysts"', 'id="reproduction"']
        positions = [page.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))

    def test_self_contained(self) -> None:
        page = self.render().lower()
        # Price source URLs show as text; only a loaded resource breaks offline viewing.
        for forbidden in ('src="http', 'href="http', "<script", "<link", "@import", "url("):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, page)
        self.assertIn("prefers-color-scheme: dark", page)

    def test_escapes_finding_text(self) -> None:
        entries = load_entries()
        entries[0]["title"] = "<script>alert(1)</script>"
        eval_dir = write_eval_dir(self.root)
        (eval_dir / "sample.json").write_text(json.dumps({"entries": entries, "judgeability_notes": {}}))
        code, result, err = self.invoke(eval_dir)
        self.assertEqual(code, 0, err)
        page = Path(result["dashboard"]).read_text()
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_header_reports_arms_and_prices(self) -> None:
        stats = load_stats()
        stats["prices"].update(stale_models=["gpt-6-sol"], missing_models=["gpt-9"])
        page = self.render(stats=stats)
        self.assertIn("baseline", page)
        self.assertIn("older than 30 days: gpt-6-sol", page)
        self.assertIn("gpt-9", page)
        self.assertIn("from https://example.test/pricing on 2026-10-20", page)

    def test_forest_plot(self) -> None:
        page = self.render()
        self.assertEqual(page.count('class="margin-band"'), 1)
        self.assertIn('data-metric="kept_p0_p2"', page)
        na_row = re.search(r'<g class="forest-row na-row"[^>]*data-metric="kept_p0_p2_per_100_lines".*?</g>', page)
        self.assertIsNotNone(na_row)
        self.assertIn(">n/a<", na_row.group(0))
        self.assertIn('class="point worse"', page)
        self.assertIn('class="point better"', page)

    def test_pooled_forest_plot(self) -> None:
        stats = load_stats()
        stats["pooled"]["eval_ids"] = ["20261018-1200-a-vs-b"]
        page = self.render(stats=stats)
        self.assertEqual(page.count('class="margin-band"'), 2)
        self.assertIn("Pooled with 20261018-1200-a-vs-b", page)

    def test_trend_has_one_series_per_arm(self) -> None:
        page = self.render()
        trend = page[page.index('id="trend"'):page.index('id="strata"')]
        self.assertEqual(re.findall(r'<g class="series s\d" data-arm="(\w+)"', trend), ["a", "b"])
        self.assertEqual(trend.count("<circle cx"), 2 * 3 + 2)

    def test_strata_flag_imbalance(self) -> None:
        page = self.render()
        row = re.search(r'<tr class="imbalanced">.*?</tr>', page)
        self.assertIsNotNone(row)
        self.assertIn("owner/solo", row.group(0))
        self.assertEqual(page.count('<tr class="imbalanced">'), 1)

    def test_worsening_listed(self) -> None:
        page = self.render()
        row = re.search(r'<tr class="worsening">.*?</tr>', page)
        self.assertIn("wall_minutes", row.group(0))
        self.assertIn("98.0%", row.group(0))

    def test_recommendations_label_and_missing_analysts(self) -> None:
        missing = [{"analyst": "codex/gpt-6.1-sol", "reason": "quota exceeded"}]
        page = self.render(analysts=[self.analyst()], synthesis=synthesis_document(missing))
        self.assertIn("Reasoning, not a verdict", page)
        self.assertIn("analysts-missing", page)
        self.assertIn("codex/gpt-6.1-sol: quota exceeded", page)
        self.assertIn("agreement needs two", page)

    def test_sample_panel_shows_every_entry_and_verdict(self) -> None:
        page = self.render()
        self.assertEqual(page.count('<article class="finding'), len(self.entries))
        self.assertEqual(page.count("<details>"), len(self.entries))
        self.assertEqual(page.count("<b>codex/gpt-6.1-sol</b>"), len(self.entries))

    def test_round_two_entries_render(self) -> None:
        eval_dir = write_eval_dir(self.root)
        judgeable = next(e for e in self.entries if e["judgeability"] == "exact")
        extra = dict(judgeable, finding_id="round2-1", title="Second round finding")
        (eval_dir / "sample-round2.json").write_text(json.dumps({"entries": [extra], "judgeability_notes": {}}))
        for path in (eval_dir / "analysts").glob("*.json"):
            document = json.loads(path.read_text())
            document["judgments"].append({"session_id": extra["session_id"], "finding_id": "round2-1", "verdict": "valid", "reason": "r"})
            path.write_text(json.dumps(document))
        code, result, err = self.invoke(eval_dir)
        self.assertEqual(code, 0, err)
        page = Path(result["dashboard"]).read_text()
        self.assertIn("round 2", page)
        self.assertIn("sample-round2.json", page)

    def test_reproduction_footer(self) -> None:
        page = self.render()
        footer = page[page.index('id="reproduction"'):]
        for value in ("20261025-1200-a-vs-b", "2000", "tags:a|b", "2026-10-25T12:00:00Z", "eval_stats.py --tags a,b --seed 11 --prices /home/user/.cache/multi-shot-review-eval/prices.toml", "analysts/codex-gpt-6.1-sol.json", "synthesis.json"):
            with self.subTest(value):
                self.assertIn(value, footer)


if __name__ == "__main__":
    unittest.main()
