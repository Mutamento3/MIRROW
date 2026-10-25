"""Stable time boxes feeding the existing whole-message encoding planner.

Box boundaries are derived from the authority, never from a summary.  The
last box stays open until a later human message crosses the gap, a divider,
or the completed-day cutoff.  Technical batches remain separate from boxes.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from datetime import date

from time_utils import parse_ts
from .replay import ReplayPlanner


@dataclass(frozen=True)
class SceneBox:
    box_id: str
    active_date: str
    session_id: str
    closed: bool
    batches: tuple


class SceneReplayPlanner(ReplayPlanner):
    def __init__(self, source, config=None, *, gap_minutes=15):
        super().__init__(source, config)
        if "+scene-digest-v1" not in self.config.prompt_version:
            self.config = replace(self.config, prompt_version=self.config.prompt_version + "+scene-digest-v1")
        if not 1 <= gap_minutes <= 1440:
            raise ValueError("invalid_scene_gap")
        self.gap_minutes = gap_minutes

    def boxes_for_day(self, active_date, *, completed_before=""):
        messages = self.source.read_active_date(active_date, roles=self.config.roles)
        accepted = {message.row_id: message for message in messages}
        # Read identities and clocks only; no second copy of private bodies.
        with self.source._connect() as db:
            rows = db.execute(
                "SELECT id,session_id,timestamp,role FROM conversation_messages "
                "WHERE active_date=? ORDER BY timestamp,id", (active_date,),
            ).fetchall()
        by_session = {}
        for row in rows:
            if self.source.canonical_sessions_only:
                from .conversation_source import is_canonical_session_id
                if not is_canonical_session_id(row["session_id"]):
                    continue
            by_session.setdefault(row["session_id"], []).append(row)
        result = []
        for session_id, session_rows in by_session.items():
            current, last_user = [], None
            first_row = None

            def flush(closed):
                nonlocal current, last_user, first_row
                if current:
                    identity = f"{active_date}\0{session_id}\0{first_row}"
                    result.append(SceneBox(
                        "box_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                        active_date, session_id, closed,
                        tuple(self._plan_session(current)),
                    ))
                current, last_user, first_row = [], None, None

            for row in session_rows:
                if row["role"] == "divider":
                    flush(True)
                    continue
                if row["role"] == "user":
                    timestamp = parse_ts(row["timestamp"])
                    if last_user is not None and (timestamp - last_user).total_seconds() > self.gap_minutes * 60:
                        flush(True)
                    last_user = timestamp
                if row["id"] in accepted:
                    if first_row is None:
                        first_row = row["id"]
                    current.append(accepted[row["id"]])
            flush(bool(completed_before and active_date < completed_before))
        result.sort(key=lambda box: (
            box.batches[0].batch_sources[0].source_ts, box.session_id, box.box_id,
        ))
        flattened = [ref.message_id for box in result for batch in box.batches for ref in batch.batch_sources]
        if len(flattened) != len(messages) or len(set(flattened)) != len(flattened):
            raise ValueError("scene_plan_source_coverage_failed")
        return tuple(result)

    def plan_active_date(self, active_date):
        original = super().plan_active_date(active_date)
        return replace(original, batches=tuple(
            batch for box in self.boxes_for_day(active_date) for batch in box.batches
        ))


def scene_start_date():
    value = os.environ.get("MIRROW_MEMORY_V2_SCENE_START_DATE", "").strip()
    if value and date.fromisoformat(value).isoformat() != value:
        raise ValueError("invalid_scene_start_date")
    return value


def planner_for_ingest(source, config=None):
    """Historical days keep their old geometry; rollout changes only new days."""
    start = scene_start_date()
    if not start:
        return ReplayPlanner(source, config)
    from scene_manager.scene import configured_gap_minutes

    class RolloutPlanner(SceneReplayPlanner):
        def plan_active_date(self, active_date):
            if active_date < start:
                return ReplayPlanner(self.source, config).plan_active_date(active_date)
            return super().plan_active_date(active_date)

    return RolloutPlanner(source, config, gap_minutes=configured_gap_minutes())
