"""Transient contiguous excerpts; source bodies and persisted spans stay unchanged."""

from __future__ import annotations

import re
from collections.abc import Sequence

from behavior_scheduler.bookshelf_search import sanitize_archive_text


def source_excerpt(text: str, terms: Sequence[str], limit: int) -> tuple[str, bool, bool]:
    """Return sanitized text, truncation, and whether a later window was selected.

    Matching locates evidence, not its mehuman. Keep one continuous window with
    surrounding text; never concatenate distant matches or synthesize a quote.
    Equal coverage retains the prefix. Sanitization precedes matching so a
    credential or protocol payload cannot become the center of an excerpt.
    """
    if limit <= 0:
        return '', bool(text), False
    clean = sanitize_archive_text(text, limit=max(len(text) * 2, limit))
    if len(clean) <= limit:
        return clean, False, False
    if limit <= 2:
        return clean[:limit], True, False
    # Reserve both omission markers inside the existing character budget.
    width = max(1, limit - 2)
    patterns = [re.compile(re.escape(term), re.IGNORECASE)
                for term in dict.fromkeys(terms) if term.strip()]
    starts = {0}
    for pattern in patterns:
        for match in pattern.finditer(clean):
            starts.add(min(max(0, match.start() - width // 3), len(clean) - width))
    start = max(starts, key=lambda offset: (
        sum(bool(pattern.search(clean[offset:offset + width])) for pattern in patterns),
        -offset,
    ))
    if start:
        # Prefer a nearby line/sentence start to a cut through metadata or a
        # clause. Never move past the first term this window would display.
        positions = [match.start() for pattern in patterns
                     if (match := pattern.search(clean, start, start + width))]
        first_match = min(positions)
        boundary = re.search(r'\n|(?<=[。！？!?])', clean[start:first_match])
        if boundary:
            start += boundary.end()
    end = min(len(clean), start + width)
    excerpt = ('…' if start else '') + clean[start:end] + ('…' if end < len(clean) else '')
    return excerpt, True, start > 0
