"""Notify the existing derived memory service only after a book commit."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def refresh():
    from . import books
    if books.ROOT != Path(__file__).resolve().parents[1]:
        return  # Private replay/test roots must never refresh the live body.
    try:
        from memory_v2.context_shadow import refresh_context_shadow_for_cognition_commit
        refresh_context_shadow_for_cognition_commit()
    except Exception as exc:
        logger.warning('Cognition recall refresh unavailable: %s', type(exc).__name__)
