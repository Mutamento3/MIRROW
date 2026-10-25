"""Evidence-day counting without turning a few observations into a habit."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from .extraction import DIMENSIONS, Signal
from .source_lifecycle import MessageLookup, filter_current


@dataclass(frozen=True)
class DimensionWindow:
    dimension: str
    evidence_days: tuple[str, ...]
    source_count: int
    ready_for_review: bool


def material_windows(signals: Sequence[Signal], *, review_after_days: int = 5
                     ) -> dict[str, DimensionWindow]:
    """Count distinct dated evidence per dimension, not calendar silence.

    "ready_for_review" schedules a later comparison, not an affirmative
    stable-habit verdict. Individual claims still need their own support.
    """
    if review_after_days < 2:
        raise ValueError("review threshold must span different evidence days")
    days: dict[str, set[str]] = defaultdict(set)
    source_keys: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for signal in signals:
        if signal.dimension not in DIMENSIONS:
            raise ValueError("unknown habit dimension")
        if signal.temporal_status not in {"occurred", "stated_pattern", "expression"}:
            continue
        # This is the day on which the source was recorded, not a model-inferred
        # behavior day. Overnight sleep attribution is deferred to source review.
        date = signal.source_calendar_date
        if not date or len(date) != 10:
            continue
        days[signal.dimension].add(date)
        source_keys[signal.dimension].add((signal.source_message_id, signal.source_content_digest))
    return {dimension: DimensionWindow(
                dimension=dimension, evidence_days=tuple(sorted(days[dimension])),
                source_count=len(source_keys[dimension]),
                ready_for_review=len(days[dimension]) >= review_after_days)
            for dimension in sorted(DIMENSIONS)}


def validated_material_windows(signals: Sequence[Signal], source: MessageLookup,
                               *, review_after_days: int = 5
                               ) -> tuple[dict[str, DimensionWindow], dict[str, int]]:
    """Public shadow aggregation entry: always discard stale source evidence."""
    current, rejected = filter_current(signals, source)
    return material_windows(current, review_after_days=review_after_days), rejected
