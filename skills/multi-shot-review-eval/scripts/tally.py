from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields, replace
from typing import Any, Self, TypeVar


class Tally:
    """Field-wise sums for frozen dataclasses of counts.

    A `None` field is an unknown amount, so any sum that includes it stays unknown instead of reading low.
    """

    def __add__(self, other: Self) -> Self:
        return replace(self, **{item.name: _sum(getattr(self, item.name), getattr(other, item.name)) for item in fields(self)})  # type: ignore[arg-type]


T = TypeVar("T", bound=Tally)


def merge_tallies(maps: Iterable[Mapping[str, T]]) -> dict[str, T]:
    merged: dict[str, T] = {}
    for values in maps:
        for key, value in values.items():
            merged[key] = merged[key] + value if key in merged else value
    return dict(sorted(merged.items()))


def per_usd(count: int, usd: float | None) -> float | None:
    return count / usd if usd else None


def _sum(left: Any, right: Any) -> Any:
    return None if left is None or right is None else left + right
