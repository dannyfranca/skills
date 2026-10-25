from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from html_parts import NA, number, text


WIDTH = 820
LABEL_END = 250
PLOT_START = 260
PLOT_END = 580
INFO_START = 592
ROW_HEIGHT = 22
TOP = 34
# Wide enough to show a two-margin miss, narrow enough that one large effect does not flatten every other row.
MIN_EXTENT = 2.0
MAX_EXTENT = 8.0


@dataclass(frozen=True)
class ForestRow:
    arm: str
    metric: str
    direction: str
    primary: bool
    contrast: Mapping[str, Any]

    @property
    def scale(self) -> float | None:
        margin = self.contrast.get("margin_abs")
        return margin if margin and self.contrast.get("diff") is not None else None

    def units(self, value: float) -> float:
        return value / self.scale if self.scale else 0.0

    def bounds(self) -> tuple[float, float] | None:
        ci = self.contrast.get("ci95")
        return (self.units(ci[0]), self.units(ci[1])) if ci and self.scale else None


def forest_rows(contrasts: Mapping[str, Mapping[str, Mapping[str, Any]]], specs: Sequence[Mapping[str, Any]]) -> list[ForestRow]:
    return [
        ForestRow(arm=arm, metric=spec["name"], direction=spec["direction"], primary=spec["primary"], contrast=by_metric[spec["name"]])
        for arm, by_metric in contrasts.items()
        for spec in specs
        if spec["name"] in by_metric
    ]


def forest_plot(rows: Sequence[ForestRow], *, title: str) -> str:
    extent = _extent(rows)
    height = TOP + ROW_HEIGHT * len(rows) + 24
    parts = [
        f'<svg class="forest" viewBox="0 0 {WIDTH} {height}" width="100%" role="img" aria-label="{text(title)}">',
        f"<title>{text(title)}</title>",
        _axis(extent, height),
    ]
    parts += [_row(row, index, extent) for index, row in enumerate(rows)]
    parts.append("</svg>")
    return "".join(parts)


def _extent(rows: Sequence[ForestRow]) -> float:
    reach = [abs(value) for row in rows if (bounds := row.bounds()) for value in (*bounds, row.units(row.contrast["diff"]))]
    return min(max([MIN_EXTENT, *(value * 1.1 for value in reach)]), MAX_EXTENT)


def _x(units: float, extent: float) -> float:
    clipped = max(-extent, min(extent, units))
    return PLOT_START + (clipped + extent) / (2 * extent) * (PLOT_END - PLOT_START)


def _axis(extent: float, height: float) -> str:
    bottom = height - 20
    band = f'<rect class="margin-band" x="{_x(-1, extent):.1f}" y="{TOP - 6}" width="{_x(1, extent) - _x(-1, extent):.1f}" height="{bottom - TOP + 6}"><title>equivalence margin [-1, 1]</title></rect>'
    ticks = []
    for value in sorted({-extent, -1.0, 0.0, 1.0, extent}):
        x = _x(value, extent)
        ticks.append(f'<line class="tick" x1="{x:.1f}" y1="{TOP - 6}" x2="{x:.1f}" y2="{bottom}"/>')
        ticks.append(f'<text class="tick-label" x="{x:.1f}" y="{TOP - 12}" text-anchor="middle">{number(value, 1)}</text>')
    legend = f'<text class="axis-title" x="{PLOT_START}" y="{height - 4}">difference in margin units (diff / margin); shaded band = margin</text>'
    header = f'<text class="axis-title" x="0" y="{TOP - 12}">arm · metric</text><text class="axis-title" x="{INFO_START}" y="{TOP - 12}">raw diff · equivalence</text>'
    return band + "".join(ticks) + legend + header


def _row(row: ForestRow, index: int, extent: float) -> str:
    y = TOP + index * ROW_HEIGHT + ROW_HEIGHT / 2
    weight = ' font-weight="bold"' if row.primary else ""
    label = f'<text class="row-label" x="0" y="{y + 4:.1f}"{weight}>{text(row.arm)} · {text(row.metric)}{_direction_hint(row.direction)}</text>'
    info = f'<text class="row-info" x="{INFO_START}" y="{y + 4:.1f}">{number(row.contrast.get("diff"))} · {text(row.contrast.get("equivalence", NA))}</text>'
    bounds = row.bounds()
    if bounds is None:
        mark = f'<text class="na" x="{(PLOT_START + PLOT_END) / 2:.1f}" y="{y + 4:.1f}" text-anchor="middle">n/a</text>'
        return f'<g class="forest-row na-row" data-arm="{text(row.arm)}" data-metric="{text(row.metric)}">{label}{mark}{info}</g>'
    return (
        f'<g class="forest-row" data-arm="{text(row.arm)}" data-metric="{text(row.metric)}">'
        f"{_better_side(row.direction, y, extent)}{label}{_whisker(row, bounds, y, extent)}{info}</g>"
    )


def _direction_hint(direction: str) -> str:
    return {"higher": " (higher better)", "lower": " (lower better)"}.get(direction, "")


def _better_side(direction: str, y: float, extent: float) -> str:
    if direction not in ("higher", "lower"):
        return ""
    start, end = (_x(0, extent), PLOT_END) if direction == "higher" else (PLOT_START, _x(0, extent))
    return f'<rect class="better-side" x="{start:.1f}" y="{y - ROW_HEIGHT / 2 + 2:.1f}" width="{end - start:.1f}" height="{ROW_HEIGHT - 4}"><title>better side</title></rect>'


def _whisker(row: ForestRow, bounds: tuple[float, float], y: float, extent: float) -> str:
    low, high = bounds
    point = row.units(row.contrast["diff"])
    clipped = " clipped" if max(abs(low), abs(high), abs(point)) > extent else ""
    tooltip = f"diff {number(row.contrast['diff'])}, 95% CI {number(row.contrast['ci95'][0])} to {number(row.contrast['ci95'][1])}"
    return (
        f'<line class="whisker{clipped}" x1="{_x(low, extent):.1f}" y1="{y:.1f}" x2="{_x(high, extent):.1f}" y2="{y:.1f}"/>'
        f'<circle class="point {_side(row.direction, point)}" cx="{_x(point, extent):.1f}" cy="{y:.1f}" r="4"><title>{text(tooltip)}</title></circle>'
    )


def _side(direction: str, point: float) -> str:
    if direction not in ("higher", "lower") or abs(point) <= 1:
        return "neutral"
    return "better" if (point > 0) == (direction == "higher") else "worse"
