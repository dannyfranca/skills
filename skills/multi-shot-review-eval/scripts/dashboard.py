from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agreement import ClassAccuracy, PairAgreement
from analyst_files import AnalystFile, Synthesis
from dashboard_review import analyst_views, finding_sample
from dashboard_summary import header, recommendations, reproduction, strata, worsening
from eval_inputs import SampleEntry
from forest_plot import forest_plot, forest_rows
from html_parts import section, text
from trend_chart import daily_means, trend_chart


@dataclass(frozen=True)
class DashboardInput:
    stats: Mapping[str, Any]
    entries: Sequence[SampleEntry]
    analysts: Sequence[AnalystFile]
    synthesis: Synthesis
    agreement: Sequence[PairAgreement]
    accuracy: Mapping[str, Mapping[str, Mapping[str, ClassAccuracy]]]
    files: Sequence[Path]


STYLE = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1d1d1f; --muted: #6b6b70; --line: #d6d6db; --panel: #f5f5f7;
  --band: rgba(90, 120, 200, 0.16); --better: rgba(40, 160, 90, 0.08); --good: #1f8a4c; --bad: #c4352b; --warn: #a86400;
  --s0: #2f6fdb; --s1: #d9730d; --s2: #7a3fc4; --s3: #138a7e; --s4: #c23a7a; --s5: #6b7a00; }
@media (prefers-color-scheme: dark) { :root { --bg: #16161a; --fg: #e8e8ed; --muted: #a0a0a8; --line: #3a3a42; --panel: #212127;
  --band: rgba(120, 150, 240, 0.22); --better: rgba(70, 200, 120, 0.10); --good: #4cc17c; --bad: #ff6b5f; --warn: #f0a640;
  --s0: #6b9cff; --s1: #ff9f43; --s2: #b18cff; --s3: #3cc8b8; --s4: #ff6fae; --s5: #b9c94a; } }
body { background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, sans-serif; margin: 0 auto; max-width: 1100px; padding: 24px; }
h1 { font-size: 22px; } h2 { font-size: 18px; border-bottom: 1px solid var(--line); padding-bottom: 4px; margin-top: 32px; }
table { border-collapse: collapse; margin: 8px 0; width: 100%; } th, td { border-bottom: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 600; } dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; } dt { color: var(--muted); } dd { margin: 0; }
.notice { background: var(--panel); border-left: 4px solid var(--muted); padding: 6px 10px; } .notice.warn { border-color: var(--warn); }
.reasoning-label { font-weight: 700; color: var(--warn); } .flag { border: 1px solid currentColor; border-radius: 4px; font-size: 12px; padding: 0 4px; }
.flag.warn, tr.imbalanced td, tr.worsening td { color: var(--warn); } tr.worsening td { font-weight: 600; }
.finding { background: var(--panel); border-radius: 6px; margin: 10px 0; padding: 8px 12px; } .finding h3 { margin: 0 0 4px; font-size: 15px; }
.facts { color: var(--muted); margin: 2px 0; } .verdict.valid { color: var(--good); } .verdict.invalid { color: var(--bad); } .verdict.unjudgeable { color: var(--muted); }
pre { white-space: pre-wrap; } svg text { fill: var(--fg); font-size: 12px; } .tick { stroke: var(--line); } .axis { stroke: var(--muted); }
.tick-label, .axis-title, .row-info, .na { fill: var(--muted); } .margin-band { fill: var(--band); } .better-side { fill: var(--better); }
.whisker { stroke: var(--fg); stroke-width: 2; } .whisker.clipped { stroke-dasharray: 4 2; }
.point { fill: var(--muted); } .point.better { fill: var(--good); } .point.worse { fill: var(--bad); }
.series polyline { fill: none; stroke-width: 2; }
""" + "".join(f".s{i} polyline {{ stroke: var(--s{i}); }} .s{i} circle {{ fill: var(--s{i}); }}\n" for i in range(6))


def render_dashboard(data: DashboardInput) -> str:
    stats = data.stats
    body = [
        header(stats),
        recommendations(data.synthesis),
        _forest(stats),
        _trend(stats),
        strata(stats),
        worsening(stats),
        finding_sample(data.entries, data.analysts),
        analyst_views(data.analysts, data.agreement, data.accuracy),
        reproduction(stats, data.files),
    ]
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Review eval {text(stats['eval_id'])}</title><style>{STYLE}</style></head>"
        f"<body>{''.join(body)}</body></html>\n"
    )


def _forest(stats: Mapping[str, Any]) -> str:
    specs = stats["metric_specs"]
    plot = forest_plot(forest_rows(stats["contrasts"], specs), title="Contrasts against the baseline, 95% CI")
    pooled = stats["pooled"]
    if pooled["eval_ids"]:
        title = "Pooled with " + ", ".join(pooled["eval_ids"])
        plot += f"<h3>{text(title)}</h3>" + forest_plot(forest_rows(pooled["contrasts"], specs), title=title)
    note = f"<p>Baseline arm {text(stats['baseline'])}. Shaded green half = the better side of the metric.</p>"
    return section("forest", "Contrasts", note + plot)


def _trend(stats: Mapping[str, Any]) -> str:
    metric = stats["primary_metric"]
    chart = trend_chart(daily_means(stats["session_rows"], metric), metric=metric)
    return section("trend", f"Daily trend of {metric}", "<p>Completed sessions only, ratio of sums per day.</p>" + chart)
