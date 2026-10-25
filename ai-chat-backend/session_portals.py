"""Non-chat portals and retired entry metadata; no domain runtime imports."""


RETIRED_PORTAL_SESSION_IDS = frozenset({"parallel_timeline"})


def is_visible_session(session_id: str) -> bool:
    """Hide a retired portal without deleting its historical JSON or index entry."""
    return session_id not in RETIRED_PORTAL_SESSION_IDS


def is_chat_session(session_id: str) -> bool:
    return is_visible_session(session_id)
