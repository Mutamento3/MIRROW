"""Affect v2 的本地 append-only 存储。

所有表都在独立的 ``events/affect.db`` 中。这里绝不打开或迁移
``open_loops.db``；开放线索的权威状态仍然属于 ``open_loops`` 模块，
本库只保存可丢弃的 attention projection 和审计信息。
"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "events" / "affect.db"


def now_iso() -> str:
    """返回项目约定的本地 ISO 时间（秒精度）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _bounded_diagnostic(value: Any, cap: int = 500) -> str:
    """Return a short technical diagnostic without credentials or payloads.

    Episode failures are queried by monitoring code, so this is a final
    storage-side guard even when a caller accidentally passes an exception
    string containing provider details.  Normal settlement code passes a
    typed, already-bounded code; this helper also protects direct store use.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]+\b", "[redacted]", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|key|token|secret|password)\b\s*[:=]\s*(?:['\"])?[^\s,;}\]\\\"']+(?:['\"])?",
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    # JSON/raw provider fragments are not diagnostics and may contain message
    # text.  Drop complete structured fragments rather than storing them.
    text = re.sub(r"```[\s\S]*?```", "[redacted]", text)
    text = re.sub(r"\{[\s\S]*?\}|\[[\s\S]*?\]", "[redacted]", text)
    text = re.sub(r"(?i)\b(?:raw(?:_output)?|provider(?:_output)?|content|excerpt|prompt)\s*[:=]\s*[^;\n]*", "payload=[redacted]", text)
    return text[:max(1, int(cap))]


DEFAULT_SETTINGS: dict[str, Any] = {
    # v2 settings are independent.  ``context_mode`` remains a read-only
    # compatibility alias for the first scaffold; new callers should use the
    # split switches below.
    "engine_mode": "enabled",
    "self_context_enabled": True,
    "resonance_recognition_enabled": True,
    "resonance_channel_enabled": False,
    "provisional_self_enabled": True,
    "context_mode": "shadow",
    "quiet_minutes": 30,
    "active_checkpoint_minutes": 10,
    "active_checkpoint_user_messages": 8,
    "batch_window_seconds": 20,
    "snapshot_min_confidence": 0.60,
    "context_min_confidence": 0.45,
    "emotion_context_char_budget": 1400,
    "open_loop_char_budget": 3200,
    "open_loop_foreground_limit": 6,
    "open_loop_related_limit": 3,
    "open_loop_due_limit": 2,
    "prompt_version": "affect-v2-components-5",
    "schema_version": 2,
}

# System vocabulary is seeded by this module only. User-authored reason
# presets use generated ids and ``builtin=0``; the public upsert API cannot
# create or promote a row into this list.
BUILTIN_PRESET_DEFINITIONS = (
    # preset_id, label, emoji, family, intensity, persistence, dimensions, sort_order
    ("happy", "开心", "😊", "愉悦", .55, "momentary", {"valence": .75, "arousal": .55, "agency": .35}, 10),
    ("joy_glad", "喜悦", "🌞", "愉悦", .55, "momentary", {"valence": .80, "arousal": .60, "agency": .30}, 11),
    ("joy_content", "满足", "🍃", "愉悦", .50, "situational", {"valence": .65, "arousal": .35, "agency": .40}, 12),
    ("affection_tender", "温柔", "🌹", "亲近", .55, "situational", {"valence": .70, "arousal": .35, "agency": .25}, 15),
    ("affection_attached", "牵挂", "🫶", "亲近", .55, "lingering", {"valence": .45, "arousal": .30, "agency": .20}, 16),
    ("affection_close", "亲昵", "💞", "亲近", .60, "momentary", {"valence": .75, "arousal": .55, "agency": .25}, 17),
    ("safe", "安心", "🌿", "安定", .50, "situational", {"valence": .55, "arousal": .25, "agency": .45}, 20),
    ("calm_peaceful", "平静", "🪷", "安定", .45, "situational", {"valence": .35, "arousal": .15, "agency": .35}, 21),
    ("calm_relaxed", "放松", "🌱", "安定", .45, "situational", {"valence": .45, "arousal": .10, "agency": .35}, 22),
    ("anticipating", "期待", "✨", "兴趣", .55, "situational", {"valence": .65, "arousal": .60, "agency": .35}, 30),
    ("interest_curious", "好奇", "🔎", "兴趣", .55, "situational", {"valence": .50, "arousal": .60, "agency": .50}, 31),
    ("interest_focused", "专注", "🧭", "兴趣", .55, "situational", {"valence": .45, "arousal": .50, "agency": .65}, 32),
    ("tired", "疲惫", "🌙", "低落", .60, "situational", {"valence": -.25, "arousal": .20, "agency": -.15}, 40),
    ("sad", "难过", "💧", "低落", .55, "situational", {"valence": -.65, "arousal": .30, "agency": -.30}, 41),
    ("low_lost", "失落", "🍂", "低落", .55, "situational", {"valence": -.55, "arousal": .35, "agency": -.25}, 42),
    ("worried", "担心", "🫧", "担忧", .55, "situational", {"valence": -.40, "arousal": .55, "agency": -.35}, 50),
    ("worry_nervous", "紧张", "🫨", "担忧", .55, "momentary", {"valence": -.35, "arousal": .70, "agency": -.20}, 51),
    ("worry_uneasy", "不安", "🌫️", "担忧", .50, "situational", {"valence": -.45, "arousal": .50, "agency": -.35}, 52),
    ("irritated", "烦躁", "🌧️", "恼怒", .55, "momentary", {"valence": -.45, "arousal": .65, "agency": .05}, 60),
    ("anger_angry", "生气", "🔥", "恼怒", .60, "momentary", {"valence": -.55, "arousal": .75, "agency": .15}, 61),
    ("anger_frustrated", "憋闷", "💢", "恼怒", .55, "situational", {"valence": -.45, "arousal": .55, "agency": -.05}, 62),
    ("aversion_dislike", "反感", "🫒", "排斥", .50, "situational", {"valence": -.55, "arousal": .45, "agency": .20}, 70),
    ("aversion_disgust", "厌恶", "🫥", "排斥", .55, "momentary", {"valence": -.70, "arousal": .55, "agency": .25}, 71),
    ("aversion_resist", "抗拒", "🛑", "排斥", .50, "situational", {"valence": -.50, "arousal": .50, "agency": .45}, 72),
    ("surprise_amazed", "惊讶", "😮", "惊讶", .50, "momentary", {"valence": .10, "arousal": .75, "agency": .05}, 80),
    ("surprise_unexpected", "意外", "🌠", "惊讶", .45, "momentary", {"valence": .05, "arousal": .65, "agency": .00}, 81),
    ("surprise_delighted", "惊喜", "🎁", "惊讶", .60, "momentary", {"valence": .70, "arousal": .75, "agency": .20}, 82),
    ("numb", "麻木", "🪶", "复杂/未归类", .45, "lingering", {"valence": -.10, "arousal": .10, "agency": -.20}, 90),
    ("complex_conflicted", "矛盾", "🪢", "复杂/未归类", .50, "situational", {"valence": 0, "arousal": .45, "agency": .00}, 91),
    ("complex_mixed", "混杂", "🌀", "复杂/未归类", .50, "situational", {"valence": 0, "arousal": .40, "agency": .00}, 92),
)

BUILTIN_PRESET_IDS = tuple(item[0] for item in BUILTIN_PRESET_DEFINITIONS)
LEGACY_BUILTIN_PRESET_IDS = (
    "happy", "safe", "anticipating", "tired", "irritated", "sad", "worried", "numb",
)


class SnapshotConflict(RuntimeError):
    """CAS 版本不匹配；调用方必须重新读取快照后再评价。"""


class AffectStore:
    """SQLite-backed affect ledger.

    ``AffectStore`` 的构造只会创建新库和新表，或执行明确的幂等增列
    迁移；不会删除旧记录，也不会打开 ``open_loops.db`` 等无关数据库。
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS affect_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS emotion_snapshots (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    self_state_json TEXT NOT NULL,
                    resonance_state_json TEXT NOT NULL,
                    last_event_id TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS emotion_events (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    explicitness TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    time_scope TEXT NOT NULL DEFAULT '',
                    observed_state_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    raw_output TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    previous_snapshot_version INTEGER NOT NULL DEFAULT 0,
                    result_snapshot_version INTEGER NOT NULL DEFAULT 0,
                    validation_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_emotion_events_source
                    ON emotion_events(source_type, source_id);
                CREATE INDEX IF NOT EXISTS idx_emotion_events_accepted
                    ON emotion_events(accepted_at DESC);

                CREATE TABLE IF NOT EXISTS affect_jobs (
                    job_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    prompt_version TEXT NOT NULL DEFAULT '',
                    input_summary_json TEXT NOT NULL,
                    output_summary_json TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    event_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_affect_jobs_status
                    ON affect_jobs(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS affect_message_materials (
                    source_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '人类伙伴',
                    content TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    trace_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_affect_materials_session
                    ON affect_message_materials(session_id, occurred_at);

                CREATE TABLE IF NOT EXISTS affect_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    from_message_id TEXT NOT NULL,
                    to_message_id TEXT NOT NULL,
                    message_ids_json TEXT NOT NULL,
                    watermark_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    job_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_affect_checkpoints_session
                    ON affect_checkpoints(session_id, watermark_at DESC);

                CREATE TABLE IF NOT EXISTS affect_buffers (
                    session_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    message_ids_json TEXT NOT NULL,
                    first_message_id TEXT NOT NULL,
                    last_message_id TEXT NOT NULL,
                    last_message_at TEXT NOT NULL,
                    dirty INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS affect_gate_decisions (
                    gate_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    triggered INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    matched_pattern TEXT NOT NULL DEFAULT '',
                    diagnostics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_affect_gate_source
                    ON affect_gate_decisions(source_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS open_loop_attention (
                    loop_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    wake_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS affect_context_audits (
                    audit_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    recipe TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    selected_json TEXT NOT NULL,
                    omitted_json TEXT NOT NULL,
                    emotion_self_chars INTEGER NOT NULL DEFAULT 0,
                    emotion_resonance_chars INTEGER NOT NULL DEFAULT 0,
                    open_loop_chars INTEGER NOT NULL DEFAULT 0,
                    injected INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                /* Affect v2 is an event-sourced ledger.  These tables are
                   intentionally all local to affect.db; no open_loops table
                   is attached or altered here. */
                CREATE TABLE IF NOT EXISTS affect_source_revisions (
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    content_excerpt TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    supersedes_revision INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_type, source_id, source_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_affect_source_current
                    ON affect_source_revisions(source_type, source_id, status, source_revision DESC);

                CREATE TABLE IF NOT EXISTS affect_components (
                    component_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL CHECK(owner IN ('k_self','human_resonance')),
                    family TEXT NOT NULL DEFAULT '复杂/未归类',
                    label TEXT NOT NULL,
                    target_type TEXT NOT NULL DEFAULT 'unknown',
                    target_id TEXT NOT NULL DEFAULT '',
                    valence REAL NOT NULL DEFAULT 0,
                    arousal REAL NOT NULL DEFAULT 0.5,
                    agency REAL NOT NULL DEFAULT 0,
                    initial_intensity REAL NOT NULL DEFAULT 0,
                    persistence_class TEXT NOT NULL DEFAULT 'situational',
                    half_life_seconds REAL NOT NULL DEFAULT 10800,
                    started_at TEXT NOT NULL,
                    last_reinforced_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    appraisal_json TEXT NOT NULL DEFAULT '{}',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    authorship TEXT NOT NULL DEFAULT 'observer_inferred',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    parent_transition_id TEXT NOT NULL DEFAULT '',
                    branch_state TEXT NOT NULL DEFAULT 'root',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_affect_components_owner_status
                    ON affect_components(owner, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS affect_transitions (
                    transition_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    owner TEXT NOT NULL CHECK(owner IN ('k_self','human_resonance')),
                    operation TEXT NOT NULL,
                    component_id TEXT NOT NULL DEFAULT '',
                    parent_transition_id TEXT NOT NULL DEFAULT '',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'accepted',
                    branch_state TEXT NOT NULL DEFAULT 'root',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_invalidated INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_affect_transitions_component
                    ON affect_transitions(component_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_affect_transitions_parent
                    ON affect_transitions(parent_transition_id, created_at);

                CREATE TABLE IF NOT EXISTS affect_projections (
                    projection_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    generated_at TEXT NOT NULL,
                    self_json TEXT NOT NULL DEFAULT '{}',
                    resonance_json TEXT NOT NULL DEFAULT '{}',
                    last_transition_id TEXT NOT NULL DEFAULT '',
                    renderer_self TEXT NOT NULL DEFAULT '',
                    renderer_resonance TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS affect_manual_presets (
                    preset_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    emoji TEXT NOT NULL DEFAULT '🙂',
                    family TEXT NOT NULL DEFAULT '复杂/未归类',
                    reason TEXT NOT NULL DEFAULT '',
                    builtin INTEGER NOT NULL DEFAULT 0,
                    default_intensity REAL NOT NULL DEFAULT 0.5,
                    default_persistence TEXT NOT NULL DEFAULT 'situational',
                    dimensions_json TEXT NOT NULL DEFAULT '{}',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS affect_manual_checkins (
                    checkin_id TEXT PRIMARY KEY,
                    preset_id TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    label TEXT NOT NULL,
                    emoji TEXT NOT NULL DEFAULT '🙂',
                    family TEXT NOT NULL DEFAULT '复杂/未归类',
                    intensity REAL NOT NULL DEFAULT 0.5,
                    persistence TEXT NOT NULL DEFAULT 'situational',
                    dimensions_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    source_revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_affect_checkins_status
                    ON affect_manual_checkins(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS affect_episodes (
                    episode_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'dirty',
                    first_message_id TEXT NOT NULL DEFAULT '',
                    last_message_id TEXT NOT NULL DEFAULT '',
                    first_message_at TEXT NOT NULL DEFAULT '',
                    last_message_at TEXT NOT NULL DEFAULT '',
                    quiet_due_at TEXT NOT NULL DEFAULT '',
                    watermark_json TEXT NOT NULL DEFAULT '{}',
                    trace_id TEXT NOT NULL DEFAULT '',
                    settled_at TEXT NOT NULL DEFAULT '',
                    settlement_job_id TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_affect_episodes_due
                    ON affect_episodes(status, quiet_due_at);

                CREATE TABLE IF NOT EXISTS affect_episode_messages (
                    episode_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL DEFAULT 1,
                    role TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(episode_id, source_type, source_id, source_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_affect_episode_messages_source
                    ON affect_episode_messages(source_type, source_id, source_revision);

                CREATE TABLE IF NOT EXISTS affect_episode_observations (
                    observation_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL,
                    owner TEXT NOT NULL CHECK(owner IN ('k_self','human_resonance')),
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL DEFAULT 1,
                    operation TEXT NOT NULL DEFAULT 'add',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    diagnostic TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_affect_observations_episode
                    ON affect_episode_observations(episode_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_affect_observations_source
                    ON affect_episode_observations(source_type, source_id, source_revision);
                """
            )
            self._ensure_manual_preset_columns(conn)
            self._ensure_episode_columns(conn)
            conn.execute(
                "INSERT OR IGNORE INTO emotion_snapshots "
                "(id, version, updated_at, self_state_json, resonance_state_json, last_event_id) "
                "VALUES ('current', 0, ?, '{}', '{}', '')",
                (now_iso(),),
            )
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO affect_meta(key, value) VALUES (?, ?)",
                    (key, json_dumps(value)),
                )
            # Existing v2 scaffolds used the old single-resonance prompt
            # label.  Keep user switches, but make the persisted contract
            # marker unambiguously identify the component parser.
            conn.execute(
                "UPDATE affect_meta SET value=? WHERE key='prompt_version'",
                (json_dumps(DEFAULT_SETTINGS["prompt_version"]),),
            )
            # The v2 projection is disposable and can always be rebuilt from
            # components/transitions.  Keep the legacy snapshot table above
            # for a compatibility read while making the v2 projection explicit.
            conn.execute(
                """INSERT OR IGNORE INTO affect_projections
                (projection_id, version, generated_at, self_json, resonance_json)
                VALUES ('current', 0, ?, '{}', '{}')""",
                (now_iso(),),
            )
            for preset_id, label, emoji, family, intensity, persistence, dimensions, sort_order in BUILTIN_PRESET_DEFINITIONS:
                conn.execute(
                    """INSERT OR IGNORE INTO affect_manual_presets
                    (preset_id,label,emoji,family,reason,builtin,default_intensity,default_persistence,
                     dimensions_json,sort_order,enabled,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?, ?,?,?,1,?,?)""",
                    (preset_id, label, emoji, family, "", 1, intensity, persistence,
                     json_dumps(dimensions), sort_order, now_iso(), now_iso()),
                )
            # Existing installations may have been created before the
            # reason/builtin columns.  This is an additive, idempotent
            # migration and never deletes or rewrites a manual record.
            placeholders = ",".join("?" for _ in LEGACY_BUILTIN_PRESET_IDS)
            conn.execute(
                f"UPDATE affect_manual_presets SET builtin=1 WHERE preset_id IN ({placeholders})",
                LEGACY_BUILTIN_PRESET_IDS,
            )
            # Promote the short-lived compatibility representation used by an
            # earlier client into the independent reason column.  Keep the
            # JSON value for old readers; all new writes use the column.
            rows = conn.execute(
                "SELECT preset_id,reason,dimensions_json FROM affect_manual_presets WHERE reason=''"
            ).fetchall()
            for row in rows:
                dimensions = json_loads(row["dimensions_json"], {})
                if isinstance(dimensions, dict) and str(dimensions.get("reason") or "").strip():
                    conn.execute(
                        "UPDATE affect_manual_presets SET reason=? WHERE preset_id=?",
                        (str(dimensions["reason"]).strip()[:500], row["preset_id"]),
                    )
            # Preserve the one current Affect state from an existing v1
            # affect.db without importing the old event/raw/material tables.
            # This is intentionally a one-time, bounded legacy import: the v2
            # ledger remains authoritative and no open_loops.db is opened.
            self._import_legacy_snapshot(conn)

    @staticmethod
    def _ensure_manual_preset_columns(conn: sqlite3.Connection) -> None:
        """Add preset metadata columns without a destructive migration."""
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(affect_manual_presets)").fetchall()
        }
        if "reason" not in columns:
            conn.execute("ALTER TABLE affect_manual_presets ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
        if "builtin" not in columns:
            conn.execute("ALTER TABLE affect_manual_presets ADD COLUMN builtin INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _ensure_episode_columns(conn: sqlite3.Connection) -> None:
        """Add bounded episode diagnostics without touching other databases."""
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(affect_episodes)").fetchall()
        }
        if "error" not in columns:
            conn.execute("ALTER TABLE affect_episodes ADD COLUMN error TEXT NOT NULL DEFAULT ''")

    def _import_legacy_snapshot(self, conn: sqlite3.Connection) -> None:
        """Import only the old current snapshot into the v2 ledger once.

        Older installations may already have ``emotion_snapshots.current``
        in this same independent database.  Its current labels are useful
        for continuity, while its event/raw/material history is not a v2
        source contract.  We therefore create one backend-owned
        ``legacy_import`` revision and bounded v2 components, never copying
        old model output or the three-parameter mood state.
        """
        guard = conn.execute(
            "SELECT value FROM affect_meta WHERE key='legacy_snapshot_import_v2'"
        ).fetchone()
        if guard is not None:
            return

        snapshot = conn.execute(
            "SELECT version,updated_at,self_state_json,resonance_state_json "
            "FROM emotion_snapshots WHERE id='current'"
        ).fetchone()
        imported_components: list[dict[str, Any]] = []
        if snapshot is not None:
            self_state = json_loads(snapshot["self_state_json"], {})
            resonance_state = json_loads(snapshot["resonance_state_json"], {})
            try:
                snapshot_version = max(0, int(snapshot["version"] or 0))
            except (TypeError, ValueError):
                snapshot_version = 0
            occurred_at = str(snapshot["updated_at"] or now_iso())
            source_id = f"legacy_snapshot:current:v{snapshot_version}"

            def _labels(state: Any) -> list[str]:
                if not isinstance(state, dict):
                    return []
                values: Any = state.get("labels")
                if values is None:
                    values = state.get("to")
                if isinstance(values, dict):
                    values = values.get("labels") or values.get("label") or values.get("name")
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    return []
                result: list[str] = []
                for value in values:
                    if isinstance(value, dict):
                        value = value.get("label") or value.get("name")
                    value = str(value or "").strip()
                    if value and value not in result:
                        result.append(value[:120])
                return result[:8]

            def _reason(state: Any) -> str:
                if not isinstance(state, dict):
                    return "旧版 Affect 当前快照"
                for key in ("description", "reason", "delta"):
                    value = state.get(key)
                    if isinstance(value, (str, int, float)) and str(value).strip():
                        return str(value).strip()[:240]
                previous = state.get("from")
                if isinstance(previous, dict):
                    previous = previous.get("label") or previous.get("name")
                return (f"从{str(previous).strip()[:80]}延续至当前快照"
                        if str(previous or "").strip() else "旧版 Affect 当前快照")

            def _confidence(state: Any) -> float:
                if isinstance(state, dict):
                    try:
                        value = float(state.get("confidence", 0.5) or 0.5)
                        return max(0.0, min(1.0, value))
                    except (TypeError, ValueError):
                        pass
                return 0.5

            def _import_owner(owner: str, state: Any) -> None:
                labels = _labels(state)
                if not labels:
                    return
                reason = _reason(state)
                confidence = _confidence(state)
                # One source revision covers this single snapshot import;
                # components remain owner-separated in the v2 ledger.
                source_content = f"旧版 Affect 快照：{'、'.join(labels)}；{reason}"[:500]
                conn.execute(
                    """INSERT OR IGNORE INTO affect_source_revisions
                    (source_type,source_id,source_revision,owner,role,content_hash,
                     content_excerpt,occurred_at,status,supersedes_revision,metadata_json,
                     created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("legacy_import", source_id, 1, owner, "legacy_import",
                     hashlib.sha256(source_content.encode("utf-8", "replace")).hexdigest(),
                     source_content, occurred_at, "active", 0,
                     json_dumps({"legacy_snapshot_version": snapshot_version,
                                 "imported_from": "emotion_snapshots"}),
                     now_iso(), now_iso()),
                )
                refs = [{"source_type": "legacy_import", "source_id": source_id,
                         "source_revision": 1, "relation": "causal"}]
                for index, label in enumerate(labels):
                    digest = hashlib.sha256(
                        f"{source_id}:{owner}:{index}:{label}".encode("utf-8", "replace")
                    ).hexdigest()[:16]
                    component_id = f"legacy_component_{digest}"
                    transition_id = f"legacy_transition_{digest}"
                    initial = max(0.35, confidence)
                    conn.execute(
                        """INSERT OR IGNORE INTO affect_components
                        (component_id,owner,family,label,target_type,target_id,valence,arousal,agency,
                         initial_intensity,persistence_class,half_life_seconds,started_at,last_reinforced_at,
                         reason,appraisal_json,source_refs_json,authorship,confidence,status,parent_transition_id,
                         branch_state,created_at,updated_at,resolved_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (component_id, owner, "复杂/未归类", label, "unknown", "", 0.0, 0.5, 0.0,
                         initial, "situational", 3 * 3600, occurred_at, occurred_at, reason, "{}",
                         json_dumps(refs), "legacy_import", confidence, "active", "", "root",
                         now_iso(), now_iso(), ""),
                    )
                    conn.execute(
                        """INSERT OR IGNORE INTO affect_transitions
                        (transition_id,trace_id,owner,operation,component_id,parent_transition_id,
                         source_refs_json,before_json,after_json,reason,status,branch_state,idempotency_key,
                         source_invalidated,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (transition_id, f"legacy_trace_{snapshot_version}", owner, "add", component_id, "",
                         json_dumps(refs), "{}", json_dumps({"label": label, "authorship": "legacy_import"}),
                         "legacy snapshot imported", "accepted", "root",
                         f"legacy-import:{source_id}:{owner}:{index}:{label}", 0, occurred_at),
                    )
                    conn.execute(
                        "UPDATE affect_components SET parent_transition_id=? WHERE component_id=?",
                        (transition_id, component_id),
                    )
                    imported_components.append({
                        "component_id": component_id, "label": label,
                        "owner": owner,
                        "initial_intensity": initial, "persistence_class": "situational",
                        "reason": reason, "authorship": "legacy_import",
                    })

            _import_owner("k_self", self_state)
            _import_owner("human_resonance", resonance_state)

            if imported_components:
                self_items = [item for item in imported_components if item.get("owner") == "k_self"]
                resonance_items = [item for item in imported_components if item.get("owner") == "human_resonance"]
                # The stored projection is disposable; this seed only makes
                # the migration visible to offline readers before the service
                # performs its normal read-time decay/rebuild.
                self_projection = {
                    "components": self_items,
                    "dimensions": {"valence": 0.0, "arousal": 0.5, "agency": 0.0},
                }
                resonance_projection = {
                    "components": resonance_items,
                    "dimensions": {"valence": 0.0, "arousal": 0.5, "agency": 0.0},
                }
                conn.execute(
                    """UPDATE affect_projections SET version=1,generated_at=?,self_json=?,
                    resonance_json=?,last_transition_id=? WHERE projection_id='current'""",
                    (now_iso(), json_dumps(self_projection), json_dumps(resonance_projection),
                     "legacy_transition_" + hashlib.sha256(
                         f"{source_id}:k_self:0:{_labels(self_state)[0] if _labels(self_state) else ''}".encode("utf-8", "replace")
                     ).hexdigest()[:16] if _labels(self_state) else ""),
                )

        conn.execute(
            "INSERT INTO affect_meta(key,value) VALUES ('legacy_snapshot_import_v2','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )

    # ---- generic read helpers -------------------------------------------------

    @staticmethod
    def _dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        return dict(row) if row is not None else None

    @staticmethod
    def _decode_row(row: Optional[dict[str, Any]], json_fields: tuple[str, ...]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        for key in json_fields:
            default = [] if key in {"evidence_json", "source_ids_json", "message_ids_json", "selected_json", "omitted_json"} else {}
            result[key] = json_loads(result.get(key), default)
        return result

    def get_settings(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM affect_meta").fetchall()
        result = dict(DEFAULT_SETTINGS)
        for row in rows:
            result[row["key"]] = json_loads(row["value"], row["value"])
        return result

    def set_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        clean: dict[str, Any] = {}
        for key, value in (updates or {}).items():
            if key in allowed:
                clean[key] = value
        if "context_mode" in clean and clean["context_mode"] not in {"shadow", "enabled", "disabled"}:
            raise ValueError("context_mode must be shadow, enabled, or disabled")
        for key in ("quiet_minutes", "active_checkpoint_minutes", "active_checkpoint_user_messages", "batch_window_seconds", "emotion_context_char_budget", "open_loop_char_budget",
                    "open_loop_foreground_limit", "open_loop_related_limit", "open_loop_due_limit"):
            if key in clean:
                clean[key] = max(0, int(clean[key]))
        for key in ("snapshot_min_confidence", "context_min_confidence"):
            if key in clean:
                clean[key] = max(0.0, min(1.0, float(clean[key])))
        with self._connect() as conn:
            for key, value in clean.items():
                conn.execute(
                    "INSERT INTO affect_meta(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json_dumps(value)),
                )
        return self.get_settings()

    def get_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM emotion_snapshots WHERE id='current'").fetchone()
        data = self._dict(row) or {
            "id": "current", "version": 0, "updated_at": now_iso(),
            "self_state_json": "{}", "resonance_state_json": "{}", "last_event_id": "",
        }
        data["self_state"] = json_loads(data.pop("self_state_json", "{}"), {})
        data["resonance_state"] = json_loads(data.pop("resonance_state_json", "{}"), {})
        return data

    def cas_update_resonance(
        self,
        *,
        expected_version: int,
        resonance_state: dict[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT version FROM emotion_snapshots WHERE id='current'").fetchone()
            actual = int(row["version"] if row else 0)
            if actual != int(expected_version):
                raise SnapshotConflict(f"snapshot version changed: expected={expected_version} actual={actual}")
            new_version = actual + 1
            conn.execute(
                "UPDATE emotion_snapshots SET version=?, updated_at=?, resonance_state_json=?, last_event_id=? WHERE id='current'",
                (new_version, now, json_dumps(resonance_state), event_id),
            )
        return self.get_snapshot()

    def cas_update_self(
        self,
        *,
        expected_version: int,
        self_state: dict[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        """仅供主 AI 自己的 sidecar/appraisal 使用；共振评价不会调用它。"""
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT version FROM emotion_snapshots WHERE id='current'").fetchone()
            actual = int(row["version"] if row else 0)
            if actual != int(expected_version):
                raise SnapshotConflict(f"snapshot version changed: expected={expected_version} actual={actual}")
            new_version = actual + 1
            conn.execute(
                "UPDATE emotion_snapshots SET version=?, updated_at=?, self_state_json=?, last_event_id=? WHERE id='current'",
                (new_version, now, json_dumps(self_state), event_id),
            )
        return self.get_snapshot()

    def record_event(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        fields = (
            "event_id", "trace_id", "source_type", "source_id", "occurred_at", "accepted_at", "status",
            "operation", "explicitness", "confidence", "time_scope", "observed_state_json", "evidence_json",
            "raw_output", "prompt_version", "idempotency_key", "previous_snapshot_version",
            "result_snapshot_version", "validation_error",
        )
        values = [event.get(field, "") for field in fields]
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO emotion_events (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
                values,
            )
            row = conn.execute("SELECT * FROM emotion_events WHERE idempotency_key=?", (event["idempotency_key"],)).fetchone()
        return self._decode_row(self._dict(row), ("observed_state_json", "evidence_json"))

    def record_event_with_snapshot(
        self,
        event: dict[str, Any],
        *,
        expected_version: int,
        resonance_state: Optional[dict[str, Any]] = None,
        self_state: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """在同一个事务中 append event 并更新对应快照。

        ``resonance_state`` 和 ``self_state`` 至少有一个时才会递增版本；
        ``no_change`` 事件可以传两个 ``None``，仅追加账本记录。
        """
        fields = (
            "event_id", "trace_id", "source_type", "source_id", "occurred_at", "accepted_at", "status",
            "operation", "explicitness", "confidence", "time_scope", "observed_state_json", "evidence_json",
            "raw_output", "prompt_version", "idempotency_key", "previous_snapshot_version",
            "result_snapshot_version", "validation_error",
        )
        values = [event.get(field, "") for field in fields]
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM emotion_events WHERE idempotency_key=?", (event["idempotency_key"],)
            ).fetchone()
            if existing is not None:
                existing_dict = self._decode_row(self._dict(existing), ("observed_state_json", "evidence_json")) or {}
                snap_row = conn.execute("SELECT * FROM emotion_snapshots WHERE id='current'").fetchone()
                snap = self._dict(snap_row) or {}
                snap["self_state"] = json_loads(snap.pop("self_state_json", "{}"), {})
                snap["resonance_state"] = json_loads(snap.pop("resonance_state_json", "{}"), {})
                return existing_dict, snap

            snap_row = conn.execute("SELECT * FROM emotion_snapshots WHERE id='current'").fetchone()
            actual = int(snap_row["version"] if snap_row else 0)
            if actual != int(expected_version):
                raise SnapshotConflict(f"snapshot version changed: expected={expected_version} actual={actual}")
            new_version = actual + (1 if (resonance_state is not None or self_state is not None) else 0)
            event["previous_snapshot_version"] = actual
            event["result_snapshot_version"] = new_version
            values = [event.get(field, "") for field in fields]
            conn.execute(
                "INSERT INTO emotion_events (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
                values,
            )
            if new_version != actual:
                current_self = json_loads(snap_row["self_state_json"] if snap_row else "{}", {})
                current_resonance = json_loads(snap_row["resonance_state_json"] if snap_row else "{}", {})
                conn.execute(
                    """UPDATE emotion_snapshots SET version=?, updated_at=?, self_state_json=?,
                    resonance_state_json=?, last_event_id=? WHERE id='current'""",
                    (new_version, now_iso(), json_dumps(self_state if self_state is not None else current_self),
                     json_dumps(resonance_state if resonance_state is not None else current_resonance), event["event_id"]),
                )
            row = conn.execute("SELECT * FROM emotion_events WHERE event_id=?", (event["event_id"],)).fetchone()
            snap_row = conn.execute("SELECT * FROM emotion_snapshots WHERE id='current'").fetchone()
        event_dict = self._decode_row(self._dict(row), ("observed_state_json", "evidence_json")) or {}
        snap = self._dict(snap_row) or {}
        snap["self_state"] = json_loads(snap.pop("self_state_json", "{}"), {})
        snap["resonance_state"] = json_loads(snap.pop("resonance_state_json", "{}"), {})
        return event_dict, snap

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM emotion_events ORDER BY accepted_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        return [self._decode_row(self._dict(row), ("observed_state_json", "evidence_json")) for row in rows]

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM emotion_events WHERE event_id=?", (event_id,)).fetchone()
        return self._decode_row(self._dict(row), ("observed_state_json", "evidence_json"))

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        values = (
            job.get("job_id") or make_id("affect_job"), job.get("trace_id") or make_id("trace"),
            job.get("batch_id") or make_id("affect_batch"), job.get("source_type", "user_message"),
            json_dumps(job.get("source_ids", [])), job.get("session_id", ""), job.get("status", "pending"),
            int(job.get("attempt", 0)), job.get("prompt_version", ""), json_dumps(job.get("input_summary", {})),
            json_dumps(job.get("output_summary", {})), job.get("error", ""), job.get("created_at", now), now,
            job.get("event_id", ""),
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_jobs
                (job_id, trace_id, batch_id, source_type, source_ids_json, session_id, status, attempt,
                 prompt_version, input_summary_json, output_summary_json, error, created_at, updated_at, event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values,
            )
        return self.get_job(values[0]) or {}

    def update_job(self, job_id: str, **updates: Any) -> Optional[dict[str, Any]]:
        allowed = {"status", "attempt", "source_ids", "input_summary", "output_summary", "error", "event_id", "prompt_version"}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key in {"input_summary", "output_summary"}:
                column = f"{key}_json"
            elif key == "source_ids":
                column = "source_ids_json"
            else:
                column = key
            assignments.append(f"{column}=?")
            values.append(json_dumps(value) if key in {"source_ids", "input_summary", "output_summary"} else value)
        if not assignments:
            return self.get_job(job_id)
        assignments.append("updated_at=?")
        values.append(now_iso())
        values.append(job_id)
        with self._connect() as conn:
            conn.execute("UPDATE affect_jobs SET " + ", ".join(assignments) + " WHERE job_id=?", values)
        return self.get_job(job_id)

    def claim_job(self, job_id: str) -> bool:
        """Atomically claim a pending job for one Flash worker."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE affect_jobs SET status='running', attempt=attempt+1, updated_at=? "
                "WHERE job_id=? AND status='pending'",
                (now_iso(), job_id),
            )
            return cursor.rowcount == 1

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._decode_row(self._dict(row), ("source_ids_json", "input_summary_json", "output_summary_json"))

    def get_job_for_source(self, source_id: str) -> Optional[dict[str, Any]]:
        """读取同一真实 source 的最新 job，避免 HTTP/WS 重试重复召回 Flash。"""
        if not source_id:
            return None
        with self._connect() as conn:
            try:
                row = conn.execute(
                    """SELECT j.* FROM affect_jobs j
                    WHERE EXISTS (SELECT 1 FROM json_each(j.source_ids_json) WHERE json_each.value = ?)
                    ORDER BY j.created_at DESC LIMIT 1""", (source_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                row = conn.execute(
                    "SELECT * FROM affect_jobs WHERE source_ids_json LIKE ? ORDER BY created_at DESC LIMIT 1",
                    (f'%"{source_id}"%',),
                ).fetchone()
        return self._decode_row(self._dict(row), ("source_ids_json", "input_summary_json", "output_summary_json"))

    def get_job_for_batch(self, batch_id: str) -> Optional[dict[str, Any]]:
        if not batch_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM affect_jobs WHERE batch_id=? ORDER BY created_at DESC LIMIT 1",
                (batch_id,),
            ).fetchone()
        return self._decode_row(self._dict(row), ("source_ids_json", "input_summary_json", "output_summary_json"))

    def get_pending_job_for_session(self, session_id: str) -> Optional[dict[str, Any]]:
        if not session_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM affect_jobs WHERE session_id=? AND status='pending' "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._decode_row(self._dict(row), ("source_ids_json", "input_summary_json", "output_summary_json"))

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM affect_jobs ORDER BY updated_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        return [self._decode_row(self._dict(row), ("source_ids_json", "input_summary_json", "output_summary_json")) for row in rows]

    # ---- bounded message material for batch prompts ------------------------

    def save_message_material(
        self,
        *,
        source_id: str,
        content: str,
        session_id: str = "",
        role: str = "人类伙伴",
        occurred_at: str = "",
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """Persist bounded prompt material keyed by the real source ID.

        This is deliberately separate from the conversation authority and is
        only used to assemble a debounced affect batch.  It never creates a
        message ID and never changes the daily topic/memory chain.
        """
        if not source_id:
            return None
        now = now_iso()
        bounded = str(content or "")[:2000]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO affect_message_materials
                (source_id, session_id, role, content, occurred_at, trace_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET session_id=excluded.session_id,
                role=excluded.role, content=excluded.content, occurred_at=excluded.occurred_at,
                trace_id=excluded.trace_id""",
                (source_id, session_id, role or "人类伙伴", bounded, occurred_at or now, trace_id, now),
            )
            row = conn.execute(
                "SELECT * FROM affect_message_materials WHERE source_id=?", (source_id,)
            ).fetchone()
        return self._dict(row)

    def get_message_materials(self, source_ids: list[str]) -> list[dict[str, Any]]:
        """Return material in caller-provided source order, never inventing IDs."""
        ids = [str(item) for item in (source_ids or []) if str(item)]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM affect_message_materials WHERE source_id IN ({placeholders})",
                ids,
            ).fetchall()
        by_id = {str(row["source_id"]): dict(row) for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def record_gate(self, decision: dict[str, Any]) -> dict[str, Any]:
        gate_id = decision.get("gate_id") or make_id("gate")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO affect_gate_decisions
                (gate_id, trace_id, source_id, session_id, triggered, reason, matched_pattern, diagnostics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gate_id, decision.get("trace_id", ""), decision.get("source_id", ""), decision.get("session_id", ""),
                 1 if decision.get("triggered") else 0, decision.get("reason", ""), decision.get("matched_pattern", ""),
                 json_dumps(decision.get("diagnostics", {})), decision.get("created_at", now_iso())),
            )
        return {**decision, "gate_id": gate_id}

    def list_gates(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM affect_gate_decisions ORDER BY created_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["triggered"] = bool(item.get("triggered"))
            item["diagnostics"] = json_loads(item.pop("diagnostics_json", "{}"), {})
            result.append(item)
        return result

    # ---- quiet buffer/checkpoint ---------------------------------------------

    def get_buffer(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_buffers WHERE session_id=?", (session_id,)).fetchone()
        return self._decode_row(self._dict(row), ("message_ids_json",))

    def touch_buffer(
        self,
        *,
        session_id: str,
        message_id: str,
        occurred_at: str,
        dirty: bool,
        trace_id: str = "",
        batch_window_seconds: int = 20,
    ) -> dict[str, Any]:
        """写入消息水位；普通消息只会重置已有 dirty buffer 的静默计时。"""
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_buffers WHERE session_id=?", (session_id,)).fetchone()
            if row is None and not dirty:
                return {
                    "session_id": session_id, "dirty": False, "message_ids": [],
                    "last_message_id": message_id, "last_message_at": occurred_at,
                }
            if row is None:
                batch_id = make_id("affect_batch")
                ids = [message_id]
                conn.execute(
                    """INSERT INTO affect_buffers
                    (session_id, trace_id, batch_id, message_ids_json, first_message_id, last_message_id,
                     last_message_at, dirty, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (session_id, trace_id or make_id("trace"), batch_id, json_dumps(ids), message_id, message_id, occurred_at, now),
                )
            else:
                current = dict(row)
                ids = json_loads(current.get("message_ids_json"), [])
                if not isinstance(ids, list):
                    ids = []
                if message_id and message_id not in ids:
                    ids.append(message_id)
                is_dirty = bool(current.get("dirty")) or dirty
                conn.execute(
                    """UPDATE affect_buffers SET message_ids_json=?, last_message_id=?, last_message_at=?,
                    dirty=?, updated_at=? WHERE session_id=?""",
                    (json_dumps(ids), message_id or current.get("last_message_id", ""), occurred_at,
                     1 if is_dirty else 0, now, session_id),
                )
        return self.get_buffer(session_id) or {}

    def list_due_buffers(self, *, now: str, quiet_seconds: int) -> list[dict[str, Any]]:
        # ISO local timestamps sort correctly within one configured timezone.
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM affect_buffers WHERE dirty=1 ORDER BY last_message_at ASC"
            ).fetchall()
        due = []
        current = datetime.fromisoformat(now)
        for row in rows:
            item = self._decode_row(self._dict(row), ("message_ids_json",)) or {}
            try:
                age = (current - datetime.fromisoformat(item["last_message_at"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                continue
            if age >= max(0, quiet_seconds):
                due.append(item)
        return due

    def create_checkpoint(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        values = (
            checkpoint.get("checkpoint_id") or make_id("checkpoint"), checkpoint.get("trace_id") or make_id("trace"),
            checkpoint.get("session_id", ""), checkpoint.get("from_message_id", ""), checkpoint.get("to_message_id", ""),
            json_dumps(checkpoint.get("message_ids", [])), checkpoint.get("watermark_at", now), checkpoint.get("status", "pending"),
            checkpoint.get("idempotency_key") or f"quiet:{checkpoint.get('session_id','')}:{checkpoint.get('to_message_id','')}",
            checkpoint.get("created_at", now), now, checkpoint.get("error", ""), checkpoint.get("job_id", ""),
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_checkpoints
                (checkpoint_id, trace_id, session_id, from_message_id, to_message_id, message_ids_json,
                 watermark_at, status, idempotency_key, created_at, updated_at, error, job_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values,
            )
            row = conn.execute("SELECT * FROM affect_checkpoints WHERE idempotency_key=?", (values[8],)).fetchone()
        return self._decode_row(self._dict(row), ("message_ids_json",)) or {}

    def update_checkpoint(self, checkpoint_id: str, *, status: str, error: str = "", job_id: str = "") -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            conn.execute(
                "UPDATE affect_checkpoints SET status=?, error=?, job_id=COALESCE(NULLIF(?,''),job_id), updated_at=? WHERE checkpoint_id=?",
                (status, error, job_id, now_iso(), checkpoint_id),
            )
            row = conn.execute("SELECT * FROM affect_checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone()
        return self._decode_row(self._dict(row), ("message_ids_json",))

    def get_checkpoint(self, checkpoint_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM affect_checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
        return self._decode_row(self._dict(row), ("message_ids_json",))

    def list_checkpoints(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM affect_checkpoints ORDER BY created_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        return [self._decode_row(self._dict(row), ("message_ids_json",)) for row in rows]

    def mark_buffer_clean_through(self, session_id: str, watermark_message_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT last_message_id FROM affect_buffers WHERE session_id=?", (session_id,)).fetchone()
            if row and row["last_message_id"] == watermark_message_id:
                conn.execute("UPDATE affect_buffers SET dirty=0, updated_at=? WHERE session_id=?", (now_iso(), session_id))

    # ---- open-loop sidecar / context audit -----------------------------------

    def get_loop_attention(self, loop_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM open_loop_attention WHERE loop_id=?", (loop_id,)).fetchone()
        return self._dict(row)

    def list_loop_attention(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM open_loop_attention ORDER BY updated_at DESC").fetchall()]

    def set_loop_attention(self, loop_id: str, state: str, *, wake_at: str = "", source_id: str = "", reason: str = "") -> dict[str, Any]:
        if state not in {"foreground", "background", "snoozed", "resolved"}:
            raise ValueError("invalid open-loop attention state")
        item = (loop_id, state, wake_at, now_iso(), source_id, reason)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO open_loop_attention(loop_id,state,wake_at,updated_at,source_id,reason)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(loop_id) DO UPDATE SET state=excluded.state,
                wake_at=excluded.wake_at, updated_at=excluded.updated_at, source_id=excluded.source_id,
                reason=excluded.reason""", item,
            )
            row = conn.execute("SELECT * FROM open_loop_attention WHERE loop_id=?", (loop_id,)).fetchone()
        return dict(row)

    def record_context_audit(self, *, recipe: str, mode: str, selected: list[dict[str, Any]], omitted: list[dict[str, Any]],
                             emotion_self_chars: int = 0, emotion_resonance_chars: int = 0, open_loop_chars: int = 0,
                             injected: bool = False, trace_id: str = "") -> dict[str, Any]:
        audit_id = make_id("ctx_affect")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO affect_context_audits
                (audit_id,trace_id,recipe,mode,selected_json,omitted_json,emotion_self_chars,
                 emotion_resonance_chars,open_loop_chars,injected,created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, trace_id or make_id("trace"), recipe, mode, json_dumps(selected), json_dumps(omitted),
                 int(emotion_self_chars), int(emotion_resonance_chars), int(open_loop_chars), 1 if injected else 0, now_iso()),
            )
        return self.get_context_audit(audit_id) or {}

    def get_context_audit(self, audit_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_context_audits WHERE audit_id=?", (audit_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["selected"] = json_loads(item.pop("selected_json", "[]"), [])
        item["omitted"] = json_loads(item.pop("omitted_json", "[]"), [])
        return item

    def list_context_audits(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT audit_id FROM affect_context_audits ORDER BY created_at DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
        return [self.get_context_audit(row["audit_id"]) for row in rows]

    # ---- Affect v2 source/revision ledger ---------------------------------

    @staticmethod
    def _row_json(row: Optional[sqlite3.Row], fields: tuple[str, ...]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        item = dict(row)
        for field in fields:
            default = [] if field.endswith("_json") and field in {
                "source_refs_json", "dimensions_json", "watermark_json",
                "payload_json", "message_ids_json",
            } else {}
            item[field[:-5] if field.endswith("_json") else field] = json_loads(item.get(field), default)
        return item

    def register_source_revision(
        self,
        *,
        source_type: str,
        source_id: str,
        source_revision: int = 1,
        owner: str = "",
        role: str = "",
        content: str = "",
        occurred_at: str = "",
        metadata: Optional[dict[str, Any]] = None,
        supersedes_revision: int = 0,
    ) -> dict[str, Any]:
        """Register a backend-owned source identity and immutable revision."""
        source_type = str(source_type or "").strip()
        source_id = str(source_id or "").strip()
        if not source_type or not source_id:
            raise ValueError("source_type and source_id are required")
        revision = max(1, int(source_revision or 1))
        now = now_iso()
        text = str(content or "")[:2000]
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_source_revisions
                (source_type,source_id,source_revision,owner,role,content_hash,
                 content_excerpt,occurred_at,status,supersedes_revision,metadata_json,
                 created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_type, source_id, revision, str(owner or ""), str(role or ""),
                 digest, text, str(occurred_at or now), "active", max(0, int(supersedes_revision or 0)),
                 json_dumps(metadata or {}), now, now),
            )
            row = conn.execute(
                """SELECT * FROM affect_source_revisions
                WHERE source_type=? AND source_id=? AND source_revision=?""",
                (source_type, source_id, revision),
            ).fetchone()
        return self._row_json(row, ("metadata_json",)) or {}

    def get_source_revision(self, source_type: str, source_id: str, source_revision: int = 0) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            if source_revision:
                row = conn.execute(
                    """SELECT * FROM affect_source_revisions
                    WHERE source_type=? AND source_id=? AND source_revision=?""",
                    (source_type, source_id, int(source_revision)),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM affect_source_revisions
                    WHERE source_type=? AND source_id=?
                    ORDER BY source_revision DESC LIMIT 1""",
                    (source_type, source_id),
                ).fetchone()
        return self._row_json(row, ("metadata_json",))

    def next_source_revision(self, source_type: str, source_id: str) -> int:
        current = self.get_source_revision(source_type, source_id)
        return int((current or {}).get("source_revision", 0) or 0) + 1

    def invalidate_source_revision(
        self, source_type: str, source_id: str, source_revision: int = 0, *, status: str = "invalidated"
    ) -> list[dict[str, Any]]:
        """Invalidate one revision; descendants are marked for reappraisal.

        The method only touches affect.db.  Projection readers independently
        verify causal references, so a failed rebuild cannot leak a ghost
        component into a prompt.
        """
        with self._connect() as conn:
            if source_revision:
                conn.execute(
                    """UPDATE affect_source_revisions SET status=?,content_hash='',content_excerpt='',
                    metadata_json='{}',updated_at=?
                    WHERE source_type=? AND source_id=? AND source_revision=?""",
                    (status, now_iso(), source_type, source_id, int(source_revision)),
                )
                rows = conn.execute(
                    """SELECT component_id,source_refs_json FROM affect_components
                    WHERE status IN ('active','provisional')"""
                ).fetchall()
            else:
                conn.execute(
                    """UPDATE affect_source_revisions SET status=?,content_hash='',content_excerpt='',
                    metadata_json='{}',updated_at=?
                    WHERE source_type=? AND source_id=? AND status='active'""",
                    (status, now_iso(), source_type, source_id),
                )
                rows = conn.execute(
                    """SELECT component_id,source_refs_json FROM affect_components
                    WHERE status IN ('active','provisional')"""
                ).fetchall()
            # Remove body-bearing compatibility/episode material immediately.
            # The immutable identity remains as an opaque invalidation audit,
            # but deleted or superseded wording cannot be reconstructed here.
            if source_type == "conversation_message":
                conn.execute("DELETE FROM affect_message_materials WHERE source_id=?", (source_id,))
                conn.execute(
                    """UPDATE affect_episode_messages SET content=''
                    WHERE source_type=? AND source_id=?""",
                    (source_type, source_id),
                )
                conn.execute(
                    """UPDATE affect_episode_observations SET payload_json='{}',status='invalidated',
                    diagnostic='source_invalidated',updated_at=?
                    WHERE source_type=? AND source_id=?""",
                    (now_iso(), source_type, source_id),
                )
                conn.execute(
                    """UPDATE emotion_events SET observed_state_json='{}',evidence_json='[]',raw_output='',
                    validation_error='source_invalidated',status='invalidated'
                    WHERE source_type=? AND source_id=?""",
                    (source_type, source_id),
                )
                conn.execute(
                    """UPDATE affect_jobs SET input_summary_json='{}',output_summary_json='{}',
                    error='source_invalidated',updated_at=? WHERE source_ids_json LIKE ?""",
                    (now_iso(), f'%"{source_id}"%'),
                )

            affected: list[str] = []
            for row in rows:
                refs = json_loads(row["source_refs_json"], [])
                if not isinstance(refs, list):
                    continue
                matches = [ref for ref in refs if (
                    isinstance(ref, dict)
                    and str(ref.get("source_type") or ref.get("type") or "conversation_message") == source_type
                    and str(ref.get("source_id") or "") == source_id
                    and (not source_revision or int(ref.get("source_revision", 1) or 1) == int(source_revision))
                )]
                if not matches:
                    continue
                remaining = [ref for ref in refs if ref not in matches]
                root_matches = [ref for ref in matches if str(ref.get("relation", "causal")) in {"causal", "self_report"}]
                remaining_roots = [
                    ref for ref in remaining
                    if isinstance(ref, dict)
                    and str(ref.get("relation", "causal")) in {"causal", "self_report"}
                    and self.source_ref_valid(ref)
                ]
                if root_matches and not remaining_roots:
                    affected.append(str(row["component_id"]))
                    conn.execute(
                        """UPDATE affect_components SET status='invalidated',branch_state='source_invalidated',
                        source_refs_json=?,reason='',appraisal_json='{}',updated_at=?,resolved_at=?
                        WHERE component_id=?""",
                        (json_dumps(remaining), now_iso(), now_iso(), row["component_id"]),
                    )
                else:
                    # A current component with another valid causal root can
                    # survive as a rebased branch.  Its old combined reason is
                    # replaced with an opaque continuity fact so deleted text
                    # cannot linger in the ordinary monitor or renderer.
                    conn.execute(
                        """UPDATE affect_components SET source_refs_json=?,branch_state='rebased',
                        reason='经仍有效的独立证据延续',appraisal_json='{}',updated_at=?
                        WHERE component_id=?""",
                        (json_dumps(remaining), now_iso(), row["component_id"]),
                    )

            # Scrub every transition that directly cited this source, even if
            # a later independently-sourced transition has already rebased the
            # current component.  Later transitions remain accepted.
            transition_rows = conn.execute(
                "SELECT transition_id,source_refs_json FROM affect_transitions"
            ).fetchall()
            invalid_transition_ids: list[str] = []
            for transition in transition_rows:
                refs = json_loads(transition["source_refs_json"], [])
                if not isinstance(refs, list):
                    continue
                matched = any(
                    isinstance(ref, dict)
                    and str(ref.get("source_type") or ref.get("type") or "conversation_message") == source_type
                    and str(ref.get("source_id") or "") == source_id
                    and (not source_revision or int(ref.get("source_revision", 1) or 1) == int(source_revision))
                    for ref in refs
                )
                if matched:
                    invalid_transition_ids.append(str(transition["transition_id"]))
                    conn.execute(
                        """UPDATE affect_transitions SET source_refs_json='[]',before_json='{}',after_json='{}',
                        reason='source_invalidated',status='invalidated',branch_state='source_invalidated',
                        source_invalidated=1 WHERE transition_id=?""",
                        (transition["transition_id"],),
                    )
            if invalid_transition_ids:
                placeholders = ",".join("?" for _ in invalid_transition_ids)
                conn.execute(
                    f"""UPDATE affect_transitions SET branch_state='rebased'
                    WHERE parent_transition_id IN ({placeholders}) AND status='accepted'""",
                    invalid_transition_ids,
                )
            return [dict(row) for row in rows if str(row["component_id"]) in set(affected)]

    def list_source_revisions(self, source_type: str = "", source_id: str = "") -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if source_type:
            clauses.append("source_type=?"); values.append(source_type)
        if source_id:
            clauses.append("source_id=?"); values.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM affect_source_revisions" + where + " ORDER BY source_id,source_revision",
                values,
            ).fetchall()
        return [self._row_json(row, ("metadata_json",)) or {} for row in rows]

    def source_ref_valid(self, ref: dict[str, Any]) -> bool:
        if not isinstance(ref, dict):
            return False
        source_type = str(ref.get("source_type") or ref.get("type") or "conversation_message")
        source_id = str(ref.get("source_id") or "")
        try:
            revision = int(ref.get("source_revision", 1) or 1)
        except (TypeError, ValueError):
            return False
        if not source_id:
            return False
        row = self.get_source_revision(source_type, source_id, revision)
        return bool(row and row.get("status") == "active")

    # ---- v2 components/transitions/projection -----------------------------

    def create_component(self, component: dict[str, Any]) -> dict[str, Any]:
        now = now_iso()
        item = dict(component or {})
        component_id = str(item.get("component_id") or make_id("component"))
        owner = str(item.get("owner") or "")
        if owner not in {"k_self", "human_resonance"}:
            raise ValueError("invalid component owner")
        persistence = str(item.get("persistence_class") or item.get("persistence") or "situational")
        half_life = {"momentary": 20 * 60, "situational": 3 * 3600, "lingering": 18 * 3600}.get(persistence, 3 * 3600)
        source_refs = item.get("source_refs", item.get("source_refs_json", []))
        if isinstance(source_refs, str):
            source_refs = json_loads(source_refs, [])
        dimensions = item.get("dimensions") or {}
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_components
                (component_id,owner,family,label,target_type,target_id,valence,arousal,agency,
                 initial_intensity,persistence_class,half_life_seconds,started_at,last_reinforced_at,
                 reason,appraisal_json,source_refs_json,authorship,confidence,status,parent_transition_id,
                 branch_state,created_at,updated_at,resolved_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (component_id, owner, str(item.get("family") or "复杂/未归类"), str(item.get("label") or "未命名感受")[:120],
                 str(item.get("target_type") or (item.get("target") or {}).get("type") or "unknown"),
                 str(item.get("target_id") or (item.get("target") or {}).get("id") or ""),
                 max(-1.0, min(1.0, float(dimensions.get("valence", item.get("valence", 0)) or 0))),
                 max(0.0, min(1.0, float(dimensions.get("arousal", item.get("arousal", 0.5)) or 0.5))),
                 max(-1.0, min(1.0, float(dimensions.get("agency", item.get("agency", 0)) or 0))),
                 max(0.0, min(1.0, float(item.get("initial_intensity", item.get("intensity", 0.5)) or 0))),
                 persistence, float(item.get("half_life_seconds") or half_life),
                 str(item.get("started_at") or now), str(item.get("last_reinforced_at") or now),
                 str(item.get("reason") or "")[:500], json_dumps(item.get("appraisal") or {}), json_dumps(source_refs or []),
                 str(item.get("authorship") or "observer_inferred"), max(0.0, min(1.0, float(item.get("confidence", 0) or 0))),
                 str(item.get("status") or "active"), str(item.get("parent_transition_id") or ""),
                 str(item.get("branch_state") or "root"), now, now, str(item.get("resolved_at") or "")),
            )
            row = conn.execute("SELECT * FROM affect_components WHERE component_id=?", (component_id,)).fetchone()
        return self._row_json(row, ("appraisal_json", "source_refs_json")) or {}

    def update_component(self, component_id: str, **updates: Any) -> Optional[dict[str, Any]]:
        allowed = {
            "label", "family", "target_type", "target_id", "valence", "arousal", "agency",
            "initial_intensity", "persistence_class", "half_life_seconds", "last_reinforced_at",
            "reason", "appraisal", "source_refs", "authorship", "confidence", "status",
            "parent_transition_id", "branch_state", "resolved_at",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            column = key
            if key in {"appraisal", "source_refs"}:
                column = f"{key}_json"
                value = json_dumps(value)
            assignments.append(f"{column}=?"); values.append(value)
        if assignments:
            assignments.append("updated_at=?"); values.append(now_iso()); values.append(component_id)
            with self._connect() as conn:
                conn.execute("UPDATE affect_components SET " + ",".join(assignments) + " WHERE component_id=?", values)
        return self.get_component(component_id)

    def get_component(self, component_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_components WHERE component_id=?", (component_id,)).fetchone()
        return self._row_json(row, ("appraisal_json", "source_refs_json"))

    def list_components(self, owner: str = "", include_inactive: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if owner:
            clauses.append("owner=?"); values.append(owner)
        if not include_inactive:
            clauses.append("status IN ('active','provisional')")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM affect_components" + where + " ORDER BY updated_at DESC", values).fetchall()
        return [self._row_json(row, ("appraisal_json", "source_refs_json")) or {} for row in rows]

    def append_transition(self, transition: dict[str, Any]) -> dict[str, Any]:
        item = dict(transition or {})
        transition_id = str(item.get("transition_id") or make_id("transition"))
        idem = str(item.get("idempotency_key") or transition_id)
        refs = item.get("source_refs", item.get("source_refs_json", []))
        if isinstance(refs, str):
            refs = json_loads(refs, [])
        now = str(item.get("created_at") or now_iso())
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_transitions
                (transition_id,trace_id,owner,operation,component_id,parent_transition_id,
                 source_refs_json,before_json,after_json,reason,status,branch_state,idempotency_key,
                 source_invalidated,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (transition_id, str(item.get("trace_id") or make_id("trace")), str(item.get("owner") or "k_self"),
                 str(item.get("operation") or "add"), str(item.get("component_id") or ""),
                 str(item.get("parent_transition_id") or ""), json_dumps(refs or []),
                 json_dumps(item.get("before") or {}), json_dumps(item.get("after") or {}),
                 str(item.get("reason") or "")[:500], str(item.get("status") or "accepted"),
                 str(item.get("branch_state") or "root"), idem, int(bool(item.get("source_invalidated"))), now),
            )
            row = conn.execute("SELECT * FROM affect_transitions WHERE idempotency_key=?", (idem,)).fetchone()
        return self._row_json(row, ("source_refs_json", "before_json", "after_json")) or {}

    def get_transition(self, transition_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_transitions WHERE transition_id=?", (transition_id,)).fetchone()
        return self._row_json(row, ("source_refs_json", "before_json", "after_json"))

    def get_transition_by_idempotency(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        if not idempotency_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM affect_transitions WHERE idempotency_key=?",
                (str(idempotency_key),),
            ).fetchone()
        return self._row_json(row, ("source_refs_json", "before_json", "after_json"))

    def list_transitions(self, owner: str = "", limit: int = 200) -> list[dict[str, Any]]:
        clause = " WHERE owner=?" if owner else ""
        values: list[Any] = [owner] if owner else []
        values.append(max(1, min(1000, int(limit))))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM affect_transitions" + clause + " ORDER BY created_at DESC LIMIT ?", values
            ).fetchall()
        return [self._row_json(row, ("source_refs_json", "before_json", "after_json")) or {} for row in rows]

    def mark_projection(self, *, self_state: dict[str, Any], resonance_state: dict[str, Any],
                        last_transition_id: str = "", renderer_self: str = "", renderer_resonance: str = "") -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT version FROM affect_projections WHERE projection_id='current'").fetchone()
            version = int(row["version"] if row else 0) + 1
            conn.execute(
                """INSERT INTO affect_projections
                (projection_id,version,generated_at,self_json,resonance_json,last_transition_id,renderer_self,renderer_resonance)
                VALUES ('current',?,?,?,?,?,?,?)
                ON CONFLICT(projection_id) DO UPDATE SET version=excluded.version,
                generated_at=excluded.generated_at,self_json=excluded.self_json,
                resonance_json=excluded.resonance_json,last_transition_id=excluded.last_transition_id,
                renderer_self=excluded.renderer_self,renderer_resonance=excluded.renderer_resonance""",
                (version, now, json_dumps(self_state or {}), json_dumps(resonance_state or {}),
                 str(last_transition_id or ""), str(renderer_self or ""), str(renderer_resonance or "")),
            )
            row = conn.execute("SELECT * FROM affect_projections WHERE projection_id='current'").fetchone()
        return self._row_json(row, ("self_json", "resonance_json")) or {}

    def get_projection(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_projections WHERE projection_id='current'").fetchone()
        return self._row_json(row, ("self_json", "resonance_json")) or {
            "projection_id": "current", "version": 0, "self": {}, "resonance": {},
        }

    # ---- manual presets/check-ins ------------------------------------------

    def list_presets(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        clause = "" if include_disabled else " WHERE enabled=1 AND deleted_at=''"
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM affect_manual_presets" + clause + " ORDER BY sort_order,preset_id").fetchall()
        return [self._row_json(row, ("dimensions_json",)) or {} for row in rows]

    def get_preset(self, preset_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_manual_presets WHERE preset_id=?", (preset_id,)).fetchone()
        return self._row_json(row, ("dimensions_json",))

    def upsert_preset(self, preset: dict[str, Any]) -> dict[str, Any]:
        item = dict(preset or {})
        preset_id = str(item.get("preset_id") or make_id("preset"))
        now = now_iso()
        existing = self.get_preset(preset_id)
        if existing and bool(existing.get("builtin")):
            raise ValueError("builtin preset is immutable")
        raw_builtin = item.get("builtin")
        requested_builtin = raw_builtin is True or raw_builtin == 1 or (
            isinstance(raw_builtin, str) and raw_builtin.strip().lower() in {"1", "true", "yes"}
        )
        if requested_builtin:
            raise ValueError("builtin preset is seed-only")
        persistence = str(item.get("default_persistence") or "situational")
        if persistence not in {"momentary", "situational", "lingering"}:
            raise ValueError("invalid default persistence")
        reason = str(item.get("reason") or "").strip()[:500]
        dimensions = item.get("dimensions") or item.get("dimensions_json") or {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        if not reason:
            reason = str(dimensions.get("reason") or "").strip()[:500]
        if not reason:
            raise ValueError("preset reason is required")
        # Keep the independent column authoritative for all new writes while
        # tolerating one older client that nested the value in dimensions.
        dimensions = {key: value for key, value in dimensions.items() if key != "reason"}
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO affect_manual_presets
                (preset_id,label,emoji,family,reason,builtin,default_intensity,default_persistence,dimensions_json,
                 sort_order,enabled,created_at,updated_at,deleted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(preset_id) DO UPDATE SET label=excluded.label,emoji=excluded.emoji,
                family=excluded.family,reason=excluded.reason,builtin=excluded.builtin,
                default_intensity=excluded.default_intensity,
                default_persistence=excluded.default_persistence,dimensions_json=excluded.dimensions_json,
                sort_order=excluded.sort_order,enabled=excluded.enabled,updated_at=excluded.updated_at,
                deleted_at=excluded.deleted_at""",
                (preset_id, str(item.get("label") or "未命名")[:80], str(item.get("emoji") or "🙂")[:8],
                 str(item.get("family") or "复杂/未归类")[:40], reason,
                 0, max(0, min(1, float(item.get("default_intensity", .5)))),
                 persistence, json_dumps(dimensions), int(item.get("sort_order", 0)), int(bool(item.get("enabled", True))),
                 str(item.get("created_at") or now), now, str(item.get("deleted_at") or "")),
            )
            row = conn.execute("SELECT * FROM affect_manual_presets WHERE preset_id=?", (preset_id,)).fetchone()
        return self._row_json(row, ("dimensions_json",)) or {}

    def delete_preset(self, preset_id: str) -> Optional[dict[str, Any]]:
        existing = self.get_preset(preset_id)
        if existing and bool(existing.get("builtin")):
            raise ValueError("builtin preset is immutable")
        with self._connect() as conn:
            conn.execute("UPDATE affect_manual_presets SET enabled=0,deleted_at=?,updated_at=? WHERE preset_id=?", (now_iso(), now_iso(), preset_id))
        return self.get_preset(preset_id)

    def create_checkin(self, checkin: dict[str, Any]) -> dict[str, Any]:
        item = dict(checkin or {})
        checkin_id = str(item.get("checkin_id") or make_id("checkin"))
        now = now_iso()
        persistence = str(item.get("persistence") or "situational")
        if persistence not in {"momentary", "situational", "lingering"}:
            raise ValueError("invalid persistence")
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_manual_checkins
                (checkin_id,preset_id,revision,label,emoji,family,intensity,persistence,dimensions_json,note,
                 status,source_revision,created_at,updated_at,resolved_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (checkin_id, str(item.get("preset_id") or ""), int(item.get("revision", 1)), str(item.get("label") or "未命名")[:80],
                 str(item.get("emoji") or "🙂")[:8], str(item.get("family") or "复杂/未归类")[:40],
                 max(0, min(1, float(item.get("intensity", .5)))), persistence,
                 json_dumps(item.get("dimensions") or item.get("dimensions_json") or {}), str(item.get("note") or "")[:500],
                 str(item.get("status") or "active"), int(item.get("source_revision", 1)), str(item.get("created_at") or now), now, str(item.get("resolved_at") or "")),
            )
            row = conn.execute("SELECT * FROM affect_manual_checkins WHERE checkin_id=?", (checkin_id,)).fetchone()
        return self._row_json(row, ("dimensions_json",)) or {}

    def get_checkin(self, checkin_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM affect_manual_checkins WHERE checkin_id=?", (checkin_id,)).fetchone()
        return self._row_json(row, ("dimensions_json",))

    def update_checkin(self, checkin_id: str, **updates: Any) -> Optional[dict[str, Any]]:
        row = self.get_checkin(checkin_id)
        if not row:
            return None
        revision = int(row.get("revision", 1) or 1) + 1
        merged = dict(row)
        merged.update(updates)
        merged["revision"] = revision
        merged["checkin_id"] = checkin_id
        # A check-in edit is a new immutable revision represented by a new row
        # identity; the original row remains technical history.
        new_id = str(updates.get("new_checkin_id") or f"{checkin_id}:r{revision}")
        merged["checkin_id"] = new_id
        merged["status"] = "active"
        return self.create_checkin(merged)

    def set_checkin_status(self, checkin_id: str, status: str) -> Optional[dict[str, Any]]:
        if status not in {"active", "resolved", "invalidated", "deleted"}:
            raise ValueError("invalid checkin status")
        with self._connect() as conn:
            conn.execute(
                "UPDATE affect_manual_checkins SET status=?,updated_at=?,resolved_at=? WHERE checkin_id=?",
                (status, now_iso(), now_iso() if status == "resolved" else "", checkin_id),
            )
        return self.get_checkin(checkin_id)

    def redact_checkin_content(self, checkin_id: str) -> Optional[dict[str, Any]]:
        """Keep only an opaque manual-checkin audit identity."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE affect_manual_checkins SET label='',emoji='',family='',note='',
                dimensions_json='{}',updated_at=? WHERE checkin_id=?""",
                (now_iso(), checkin_id),
            )
        return self.get_checkin(checkin_id)

    def list_checkins(self, status: str = "") -> list[dict[str, Any]]:
        clause = " WHERE status=?" if status else ""
        values = [status] if status else []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM affect_manual_checkins" + clause + " ORDER BY created_at DESC", values).fetchall()
        return [self._row_json(row, ("dimensions_json",)) or {} for row in rows]

    # ---- episode buffer/observation ----------------------------------------

    @staticmethod
    def _add_minutes(value: str, minutes: int) -> str:
        try:
            return (datetime.fromisoformat(value) + timedelta(minutes=max(0, int(minutes)))).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return now_iso()

    def get_open_episode(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM affect_episodes WHERE session_id=? AND status='dirty'
                ORDER BY updated_at DESC LIMIT 1""", (session_id,)
            ).fetchone()
        return self._row_json(row, ("watermark_json",))

    def get_episode(self, episode_id: str) -> Optional[dict[str, Any]]:
        """Read one episode, including its bounded failure diagnostic."""
        if not episode_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM affect_episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
        return self._row_json(row, ("watermark_json",))

    def append_episode_message(
        self, *, session_id: str, source_type: str, source_id: str, source_revision: int = 1,
        role: str = "", content: str = "", occurred_at: str = "", quiet_minutes: int = 30,
        trace_id: str = "", max_messages: int = 96,
        active_checkpoint_minutes: int = 10, active_checkpoint_user_messages: int = 8,
    ) -> dict[str, Any]:
        if not session_id or not source_id:
            raise ValueError("session_id and source_id are required")
        now = now_iso(); occurred = occurred_at or now
        with self._connect() as conn:
            # The read/rotate/append sequence must be one writer transaction.
            # A checkpoint may never claim an episode between this SELECT and
            # the authoritative message insert.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM affect_episodes WHERE session_id=? AND status='dirty' ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is not None and str(role or "").lower() in {"user", "人类伙伴"}:
                counts = conn.execute(
                    """SELECT COUNT(CASE WHEN role IN ('user','人类伙伴') THEN 1 END) AS users,
                              MAX(CASE WHEN role IN ('user','人类伙伴') THEN rowid END) AS last_user,
                              MAX(CASE WHEN role='assistant' THEN rowid END) AS last_assistant
                       FROM affect_episode_messages WHERE episode_id=?""",
                    (row["episode_id"],),
                ).fetchone()
                completed_turn = bool(counts and counts["last_assistant"] and counts["last_user"]
                                      and int(counts["last_assistant"]) > int(counts["last_user"]))
                try:
                    elapsed = (datetime.fromisoformat(occurred) - datetime.fromisoformat(row["first_message_at"])).total_seconds()
                except (TypeError, ValueError):
                    elapsed = 0.0
                checkpoint_due = (
                    elapsed >= max(1, int(active_checkpoint_minutes)) * 60
                    or int(counts["users"] or 0) >= max(2, int(active_checkpoint_user_messages))
                    or str(row["quiet_due_at"] or "") <= occurred
                )
                if completed_turn and checkpoint_due:
                    # The previous AI turn and its sidecar have completed before
                    # the next user message enters this session's SOP lock.
                    # The timer settles this immutable box; the new user starts
                    # another, so active and quiet settlement cannot duplicate.
                    conn.execute(
                        "UPDATE affect_episodes SET status='checkpoint_ready',updated_at=? WHERE episode_id=? AND status='dirty'",
                        (now, row["episode_id"]),
                    )
                    row = None
            if row is None:
                episode_id = make_id("episode")
                conn.execute(
                    """INSERT INTO affect_episodes
                    (episode_id,session_id,status,first_message_id,last_message_id,first_message_at,last_message_at,
                     quiet_due_at,watermark_json,trace_id,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (episode_id, session_id, "dirty", source_id, source_id, occurred, occurred,
                     self._add_minutes(occurred, quiet_minutes), json_dumps({"from_message_id": source_id, "to_message_id": source_id}),
                     trace_id or make_id("trace"), now, now),
                )
            else:
                episode_id = str(row["episode_id"])
                # The quiet watermark is anchored to the latest *user*
                # message.  Assistant bubbles still enter the bounded
                # episode, but they must not postpone the 30-minute quiet
                # settlement after the user has stopped speaking.
                if str(role or "").lower() in {"user", "人类伙伴"}:
                    conn.execute(
                        """UPDATE affect_episodes SET last_message_id=?,last_message_at=?,quiet_due_at=?,
                        watermark_json=?,trace_id=COALESCE(NULLIF(?,''),trace_id),updated_at=? WHERE episode_id=?""",
                        (source_id, occurred, self._add_minutes(occurred, quiet_minutes),
                         json_dumps({"from_message_id": row["first_message_id"], "to_message_id": source_id}), trace_id, now, episode_id),
                    )
                else:
                    conn.execute(
                        """UPDATE affect_episodes SET trace_id=COALESCE(NULLIF(?,''),trace_id),updated_at=?
                        WHERE episode_id=?""",
                        (trace_id, now, episode_id),
                    )
            conn.execute(
                """INSERT OR REPLACE INTO affect_episode_messages
                (episode_id,source_type,source_id,source_revision,role,content,occurred_at,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (episode_id, source_type, source_id, max(1, int(source_revision)), str(role or ""), str(content or "")[:2000], occurred, now),
            )
            # Keep a bounded episode by removing the oldest rows only.  Source
            # revisions and the authoritative conversation remain untouched.
            count_row = conn.execute("SELECT COUNT(*) AS n FROM affect_episode_messages WHERE episode_id=?", (episode_id,)).fetchone()
            excess = max(0, int(count_row["n"] if count_row else 0) - max(8, int(max_messages)))
            if excess:
                conn.execute(
                    """DELETE FROM affect_episode_messages WHERE rowid IN
                    (SELECT rowid FROM affect_episode_messages WHERE episode_id=? ORDER BY occurred_at ASC LIMIT ?)""",
                    (episode_id, excess),
                )
            row = conn.execute("SELECT * FROM affect_episodes WHERE episode_id=?", (episode_id,)).fetchone()
        return self._row_json(row, ("watermark_json",)) or {}

    def list_episode_messages(self, episode_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM affect_episode_messages WHERE episode_id=? ORDER BY occurred_at,created_at""", (episode_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_due_episodes(self, now: str = "") -> list[dict[str, Any]]:
        current = now or now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM affect_episodes AS episode
                   WHERE status='checkpoint_ready' OR (
                       status='dirty' AND quiet_due_at<=?
                       AND (SELECT MAX(rowid) FROM affect_episode_messages
                            WHERE episode_id=episode.episode_id AND role='assistant')
                         > (SELECT MAX(rowid) FROM affect_episode_messages
                            WHERE episode_id=episode.episode_id AND role IN ('user','人类伙伴'))
                   )
                   ORDER BY CASE WHEN status='checkpoint_ready' THEN 0 ELSE 1 END, quiet_due_at""", (current,)
            ).fetchall()
        return [self._row_json(row, ("watermark_json",)) or {} for row in rows]

    def claim_episode_for_settlement(self, episode_id: str, *, expected_last_message_id: str) -> Optional[dict[str, Any]]:
        """Atomically freeze one box before reading provider material."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                """UPDATE affect_episodes SET status='settling',updated_at=?
                   WHERE episode_id=? AND status IN ('dirty','checkpoint_ready') AND last_message_id=?""",
                (now_iso(), episode_id, expected_last_message_id),
            )
            if updated.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM affect_episodes WHERE episode_id=?", (episode_id,)).fetchone()
        return self._row_json(row, ("watermark_json",))

    def list_episodes(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Return bounded episode status rows without conversation material."""
        clause = " WHERE status=?" if status else ""
        values: list[Any] = [status] if status else []
        values.append(max(1, min(200, int(limit))))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM affect_episodes" + clause + " ORDER BY updated_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._row_json(row, ("watermark_json",)) or {} for row in rows]

    def mark_episode(self, episode_id: str, *, status: str, settlement_job_id: str = "", error: str = "") -> Optional[dict[str, Any]]:
        if status not in {"dirty", "settling", "accepted", "failed", "invalidated"}:
            raise ValueError("invalid episode status")
        diagnostic = _bounded_diagnostic(error)
        with self._connect() as conn:
            conn.execute(
                """UPDATE affect_episodes SET status=?,settled_at=?,
                settlement_job_id=COALESCE(NULLIF(?,''),settlement_job_id),
                error=CASE WHEN ?<>'' THEN ? ELSE error END,
                updated_at=? WHERE episode_id=?""",
                (status, now_iso() if status == "accepted" else "", settlement_job_id,
                 diagnostic, diagnostic, now_iso(), episode_id),
            )
            row = conn.execute("SELECT * FROM affect_episodes WHERE episode_id=?", (episode_id,)).fetchone()
        return self._row_json(row, ("watermark_json",))

    def add_episode_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        item = dict(observation or {})
        observation_id = str(item.get("observation_id") or make_id("observation"))
        idem = str(item.get("idempotency_key") or observation_id)
        now = now_iso()
        payload = item.get("payload", item.get("payload_json", {}))
        if isinstance(payload, str):
            payload = json_loads(payload, {})
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO affect_episode_observations
                (observation_id,episode_id,owner,source_type,source_id,source_revision,operation,payload_json,
                 status,diagnostic,idempotency_key,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (observation_id, str(item.get("episode_id") or ""), str(item.get("owner") or "k_self"),
                 str(item.get("source_type") or "conversation_message"), str(item.get("source_id") or ""),
                 max(1, int(item.get("source_revision", 1) or 1)), str(item.get("operation") or "add"),
                 json_dumps(payload or {}), str(item.get("status") or "pending"), str(item.get("diagnostic") or ""),
                 idem, str(item.get("created_at") or now), now),
            )
            row = conn.execute("SELECT * FROM affect_episode_observations WHERE idempotency_key=?", (idem,)).fetchone()
        return self._row_json(row, ("payload_json",)) or {}

    def list_episode_observations(self, episode_id: str, status: str = "") -> list[dict[str, Any]]:
        clause = " AND status=?" if status else ""
        values: list[Any] = [episode_id] + ([status] if status else [])
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM affect_episode_observations WHERE episode_id=?" + clause + " ORDER BY created_at",
                values,
            ).fetchall()
        return [self._row_json(row, ("payload_json",)) or {} for row in rows]

    def update_observation_status(self, observation_id: str, status: str, diagnostic: str = "") -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            conn.execute("UPDATE affect_episode_observations SET status=?,diagnostic=?,updated_at=? WHERE observation_id=?", (status, diagnostic[:500], now_iso(), observation_id))
            row = conn.execute("SELECT * FROM affect_episode_observations WHERE observation_id=?", (observation_id,)).fetchone()
        return self._row_json(row, ("payload_json",))


_store: Optional[AffectStore] = None


def get_affect_store() -> AffectStore:
    global _store
    if _store is None:
        _store = AffectStore()
    return _store


__all__ = ["AffectStore", "SnapshotConflict", "DEFAULT_DB_PATH", "DEFAULT_SETTINGS", "get_affect_store", "make_id", "now_iso"]
