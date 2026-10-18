"""Goal autonomy rules for Wander plans.

The policy remains in a run snapshot so public installations need no schema
migration: activities keep their current numeric goals in the existing table.
"""
from __future__ import annotations

from typing import Any, Optional

from .event_catalog import normalize_goal
from .event_types import EventType
from .runtime_models import GoalMode, WanderRun

_SQLITE_INT_MAX = 2**63 - 1


def normalize_extension(value: Any) -> int:
    """Return an explicit positive integer continuation, otherwise zero."""
    try:
        if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
            return 0
        if isinstance(value, str) and not value.strip().lstrip("+").isdigit():
            return 0
        count = int(value)
        return count if 0 < count <= _SQLITE_INT_MAX else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def is_autonomous_run(run: WanderRun | dict[str, Any] | None) -> bool:
    if not run:
        return False
    if isinstance(run, dict):
        if str(run.get("trigger_reason") or "") == "chat_tool":
            return False
        snapshot = run.get("context_snapshot") or {}
    else:
        if run.trigger_reason == "chat_tool":
            return False
        snapshot = run.context_snapshot or {}
    autonomy = snapshot.get("autonomy") if isinstance(snapshot, dict) else None
    return isinstance(autonomy, dict) and autonomy.get("version") == 1


def normalize_goal_policy(event_type: EventType, raw_goal: Any, *, autonomous: bool) -> tuple[GoalMode, Optional[int], str]:
    """Normalize one LLM goal without turning catalog defaults into quotas."""
    raw_goal = raw_goal if isinstance(raw_goal, dict) else {}
    requested_mode = raw_goal.get("mode")
    try:
        goal_mode, default_value = normalize_goal(event_type, requested_mode, raw_goal.get("value"))
    except (TypeError, ValueError, OverflowError):
        goal_mode, default_value = normalize_goal(event_type, requested_mode, None)
    completion = str(raw_goal.get("completion") or "").strip().lower()
    if not autonomous or goal_mode not in (GoalMode.COUNT, GoalMode.OPEN_ENDED):
        return goal_mode, default_value, "fixed"
    if completion not in {"estimate", "fixed"}:
        completion = "estimate"
    if goal_mode == GoalMode.COUNT:
        # Preserve the catalog's invalid-mode fallback and news-duration
        # conversion; only an actual count may bypass the old count ceiling.
        if requested_mode not in (None, GoalMode.COUNT, "count"):
            return goal_mode, default_value, completion
        value = raw_goal.get("value")
        try:
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            if isinstance(value, str) and (not value.strip() or not value.strip().lstrip("+").isdigit()):
                raise ValueError
            count = int(value)
            if count <= 0 or count > _SQLITE_INT_MAX:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            _, fallback_value = normalize_goal(event_type, requested_mode, None)
            count = int(fallback_value or 1)
        return goal_mode, count, completion
    return goal_mode, None, completion


def get_activity_goal_policy(run: WanderRun | dict[str, Any], activity: Any) -> dict[str, Any]:
    if not is_autonomous_run(run):
        return {"completion": "fixed"}
    snapshot = run.get("context_snapshot") if isinstance(run, dict) else run.context_snapshot
    autonomy = (snapshot or {}).get("autonomy") if isinstance(snapshot, dict) else {}
    policies = autonomy.get("activity_goal_policies") if isinstance(autonomy, dict) else {}
    activity_id = activity.get("activity_id") if isinstance(activity, dict) else activity.activity_id
    policy = policies.get(activity_id) if isinstance(policies, dict) else None
    return dict(policy) if isinstance(policy, dict) else {"completion": "fixed"}


def normalize_switch_goal(event_type: EventType, raw_goal: Any, *, autonomous: bool) -> tuple[GoalMode, Optional[int], str]:
    return normalize_goal_policy(event_type, raw_goal, autonomous=autonomous)
