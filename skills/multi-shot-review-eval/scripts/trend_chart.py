from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from html_parts import number, text


WIDTH = 820
HEIGHT = 260
LEFT = 60
RIGHT = 700
TOP = 20
BOTTOM = 210
SERIES_CLASSES = 6

DailyMeans = dict[str, dict[str, float]]


def daily_means(session_rows: Mapping[str, Sequence[Mapping[str, Any]]], metric: str) -> DailyMeans:
    """Ratio of sums per day, matching how eval_stats pools sessions into an arm mean."""

    result: DailyMeans = {}
    for arm, rows in session_rows.items():
        totals: dict[str, list[float]] = {}
        for row in rows:
            ratio = row["metrics"].get(metric)
            if not row["completed"] or ratio is None:
                continue
            day = totals.setdefault(row["created_at"][:10], [0.0, 0.0])
            day[0] += ratio[0]
            day[1] += ratio[1]
        result[arm] = {day: num / den for day, (num, den) in sorted(totals.items()) if den}
    return result


def trend_chart(means: DailyMeans, *, metric: str) -> str:
    days = sorted({day for by_day in means.values() for day in by_day})
    if not days:
        return f'<p class="empty">No completed session has a value for {text(metric)}.</p>'
    values = [value for by_day in means.values() for value in by_day.values()]
    low, high = min(values), max(values)
    pad = (high - low) * 0.1 or abs(high) * 0.1 or 1.0
    low, high = low - pad, high + pad
    x = {day: _x(index, len(days)) for index, day in enumerate(days)}

    def y(value: float) -> float:
        return BOTTOM - (value - low) / (high - low) * (BOTTOM - TOP)

    parts = [
        f'<svg class="trend" viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" role="img" aria-label="Daily mean of {text(metric)}">',
        f"<title>Daily mean of {text(metric)}</title>",
        _axes(days, x, low, high, y),
    ]
    for index, (arm, by_day) in enumerate(means.items()):
        parts.append(_series(arm, index, by_day, x, y))
    parts.append(_legend(list(means)))
    parts.append("</svg>")
    return "".join(parts)


def _x(index: int, count: int) -> float:
    return (LEFT + RIGHT) / 2 if count == 1 else LEFT + index / (count - 1) * (RIGHT - LEFT)


def _axes(days: Sequence[str], x: Mapping[str, float], low: float, high: float, y: Any) -> str:
    parts = [f'<line class="axis" x1="{LEFT}" y1="{BOTTOM}" x2="{RIGHT}" y2="{BOTTOM}"/>']
    for value in (low, (low + high) / 2, high):
        parts.append(f'<line class="tick" x1="{LEFT}" y1="{y(value):.1f}" x2="{RIGHT}" y2="{y(value):.1f}"/>')
        parts.append(f'<text class="tick-label" x="{LEFT - 6}" y="{y(value) + 4:.1f}" text-anchor="end">{number(value, 2)}</text>')
    # Every other label once days crowd, so dates stay readable on long windows.
    stride = max(1, len(days) // 10)
    for index, day in enumerate(days):
        if index % stride == 0:
            parts.append(f'<text class="tick-label" x="{x[day]:.1f}" y="{BOTTOM + 16}" text-anchor="middle">{text(day[5:])}</text>')
    return "".join(parts)


def _series(arm: str, index: int, by_day: Mapping[str, float], x: Mapping[str, float], y: Any) -> str:
    css = f"series s{index % SERIES_CLASSES}"
    points = " ".join(f"{x[day]:.1f},{y(value):.1f}" for day, value in by_day.items())
    dots = "".join(
        f'<circle cx="{x[day]:.1f}" cy="{y(value):.1f}" r="3.5"><title>{text(arm)} {text(day)}: {number(value)}</title></circle>'
        for day, value in by_day.items()
    )
    return f'<g class="{css}" data-arm="{text(arm)}"><polyline points="{points}"/>{dots}</g>'


def _legend(arms: Sequence[str]) -> str:
    items = []
    for index, arm in enumerate(arms):
        y = TOP + index * 18
        items.append(
            f'<g class="series s{index % SERIES_CLASSES} legend-item"><circle cx="{RIGHT + 20}" cy="{y}" r="4"/>'
            f'<text class="legend-label" x="{RIGHT + 30}" y="{y + 4}">{text(arm)}</text></g>'
        )
    return '<g class="legend">' + "".join(items) + "</g>"
