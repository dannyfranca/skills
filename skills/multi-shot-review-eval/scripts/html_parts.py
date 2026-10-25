from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from typing import Any


NA = "n/a"


def text(value: Any) -> str:
    return html.escape(str(value), quote=True)


def number(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return NA
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    places = 1 if abs(value) >= 100 else digits
    rendered = f"{value:,.{places}f}".rstrip("0").rstrip(".")
    return "0" if rendered in ("", "-0") else rendered


def share(value: float | None) -> str:
    return NA if value is None else f"{value * 100:.1f}%"


def interval(bounds: Sequence[float] | None) -> str:
    return NA if not bounds else f"[{number(bounds[0])}, {number(bounds[1])}]"


def section(section_id: str, title: str, body: str) -> str:
    return f'<section id="{text(section_id)}"><h2>{text(title)}</h2>{body}</section>'


def table(headers: Sequence[str], rows: Iterable[Sequence[str]], *, row_classes: Sequence[str] | None = None) -> str:
    """Cells are pre-rendered HTML; callers escape text, so markup such as flags can sit inside a cell."""

    head = "".join(f"<th>{text(header)}</th>" for header in headers)
    body = []
    for index, row in enumerate(rows):
        css = f' class="{text(row_classes[index])}"' if row_classes and row_classes[index] else ""
        body.append(f"<tr{css}>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def definitions(pairs: Iterable[tuple[str, str]]) -> str:
    return "<dl>" + "".join(f"<dt>{text(term)}</dt><dd>{value}</dd>" for term, value in pairs) + "</dl>"


def notice(kind: str, body: str) -> str:
    return f'<p class="notice {text(kind)}">{body}</p>'
