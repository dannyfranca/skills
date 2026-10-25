from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from analyst_files import Synthesis
from html_parts import definitions, interval, notice, number, section, share, table, text


def header(stats: Mapping[str, Any]) -> str:
    baseline = stats["baseline"]
    arms = stats["arms"]
    arm_rows = [
        [
            text(arm) + (' <span class="flag">baseline</span>' if arm == baseline else ""),
            number(summary["n_completed"]),
            number(summary["n_incomplete"]),
        ]
        for arm, summary in arms.items()
    ]
    window = stats["window"]
    facts = definitions([
        ("eval", text(stats["eval_id"])),
        ("mode", text(stats["mode"])),
        ("contrast key", text(stats["contrast_key"])),
        ("window", f"{text(window['start'])} to {text(window['end'])}"),
        ("primary metric", text(stats["primary_metric"])),
    ])
    return (
        f"<header><h1>Review eval {text(stats['eval_id'])}</h1>{facts}"
        f"{table(['arm', 'completed', 'incomplete'], arm_rows)}{_price_warnings(stats['prices'])}</header>"
    )


def _price_warnings(prices: Mapping[str, Any]) -> str:
    parts = []
    if prices.get("stale"):
        parts.append(notice("warn", f"The price table is stale (updated {text(prices.get('updated'))}); USD values may be off."))
    if prices.get("missing_models"):
        parts.append(notice("warn", "No price for models: " + text(", ".join(prices["missing_models"])) + "; their USD is missing."))
    parts += [notice("info", text(assumption)) for assumption in prices.get("assumptions") or []]
    return "".join(parts)


def recommendations(synthesis: Synthesis) -> str:
    rows = [
        [text(item.arm), text(item.proposal), text(item.confidence), text(item.reasoning)]
        for item in synthesis.recommendations
    ]
    missing = ""
    if synthesis.analysts_missing:
        items = "".join(f"<li>{text(item.analyst)}: {text(item.reason)}</li>" for item in synthesis.analysts_missing)
        missing = f'<div class="notice warn analysts-missing"><p>Analysts missing:</p><ul>{items}</ul></div>'
    label = '<p class="reasoning-label">Reasoning, not a verdict: the numbers below are the evidence; the decision stays with you.</p>'
    return section(
        "recommendations",
        "Recommendations",
        label + missing + table(["arm", "proposal", "confidence", "reasoning"], rows),
    )


def strata(stats: Mapping[str, Any]) -> str:
    arms = list(stats["arms"])
    baseline = stats["baseline"]
    others = [arm for arm in arms if arm != baseline]
    headers = ["value", *(f"n {arm}" for arm in arms), *(f"mean {arm}" for arm in arms), *(f"diff {arm}" for arm in others), "balance"]
    parts = [f"<p>Primary metric {text(stats['primary_metric'])}; point estimates only.</p>"]
    for dimension, rows in stats["strata"].items():
        cells = [
            [
                text(row["value"]),
                *(number(row["counts"].get(arm)) for arm in arms),
                *(number(row["primary"]["means"].get(arm)) for arm in arms),
                *(number(row["primary"]["diff"].get(arm)) for arm in others),
                '<span class="flag warn">imbalanced</span>' if row["imbalanced"] else "balanced",
            ]
            for row in rows
        ]
        classes = ["imbalanced" if row["imbalanced"] else "" for row in rows]
        parts.append(f"<h3>{text(dimension)}</h3>" + table(headers, cells, row_classes=classes))
    return section("strata", "Strata", "".join(parts))


def worsening(stats: Mapping[str, Any]) -> str:
    flags = stats["worsening"]
    if flags:
        body = table(
            ["arm", "metric", "P(worse)", "diff", "95% CI"],
            [
                [text(flag["arm"]), text(flag["metric"]), share(flag["p_worse"]), number(_contrast(stats, flag["arm"], flag["metric"]).get("diff")), interval(_contrast(stats, flag["arm"], flag["metric"]).get("ci95"))]
                for flag in flags
            ],
            row_classes=["worsening"] * len(flags),
        )
    else:
        body = "<p>No metric is flagged as worsening.</p>"
    offers = table(
        ["arm", "offer round 2", "CI overlap", "kept share gap"],
        [
            [text(arm), number(offer["offer"]), number(offer["ci_overlap"]), share(offer["kept_share_gap"])]
            for arm, offer in stats["second_round_offer"].items()
        ],
    )
    return section("worsening", "Worsening flags and second round", body + "<h3>Second-round offer</h3>" + offers)


def _contrast(stats: Mapping[str, Any], arm: str, metric: str) -> Mapping[str, Any]:
    return stats["contrasts"].get(arm, {}).get(metric, {})


def reproduction(stats: Mapping[str, Any], files: Sequence[Path]) -> str:
    inputs = stats["inputs"]
    window = stats["window"]
    facts = definitions([
        ("eval id", text(stats["eval_id"])),
        ("seed", text(inputs["seed"])),
        ("resamples", text(inputs["resamples"])),
        ("contrast key", text(stats["contrast_key"])),
        ("window", f"{text(window['start'])} to {text(window['end'])}"),
        ("created at", text(stats["created_at"])),
    ])
    listed = "".join(f"<li><code>{text(path.name if path.parent.name != 'analysts' else 'analysts/' + path.name)}</code></li>" for path in files)
    command = text(shlex.join(["eval_stats.py", *inputs["argv"]]))
    return (
        f'<footer id="reproduction"><h2>Reproduction</h2>{facts}'
        f"<p>Command, run from the same working directory over the same review root: <code>{command}</code></p>"
        f"<p>Files:</p><ul>{listed}</ul></footer>"
    )
