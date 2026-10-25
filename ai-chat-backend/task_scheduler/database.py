"""
定时任务管理器 - SQLite 数据库层
"""
import sqlite3
import json
import os
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from .models import ScheduledTask, TaskCreateRequest, PendingReminder

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "events")
DB_PATH = os.path.join(DB_DIR, "tasks.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表"""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                ai_context TEXT DEFAULT '',
                task_type TEXT DEFAULT 'reminder',
                trigger_type TEXT NOT NULL,
                trigger_config TEXT NOT NULL,
                notify_method TEXT NOT NULL DEFAULT '["popup"]',
                alarm_enabled INTEGER DEFAULT 0,
                message_content TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                is_ai_generated INTEGER DEFAULT 0,
                created_by TEXT DEFAULT 'user',
                last_triggered_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # 兼容旧表：补充可能缺失的列
        for col in ("reason", "ai_context", "created_by"):
            try:
                conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # 列已存在
        # self_task 列迁移（2026-06-01）
        for col, col_def in [
            ("tool_name", "TEXT DEFAULT ''"),
            ("tool_params", "TEXT DEFAULT '{}'"),
            ("execution_prompt", "TEXT DEFAULT ''"),
            ("max_rounds", "INTEGER DEFAULT 2"),
            ("trigger_window_seconds", "INTEGER DEFAULT 300"),
            ("alarm_enabled", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_active
            ON scheduled_tasks(is_active)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_reminders (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_title TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                ai_context TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                original_message TEXT NOT NULL DEFAULT '',
                triggered_at TEXT NOT NULL,
                recheck_stage INTEGER DEFAULT 0,
                resolved INTEGER DEFAULT 0,
                reminder_type TEXT DEFAULT 'task',
                workflow_state TEXT DEFAULT 'pending',
                alarm_id TEXT DEFAULT '',
                alarm_scheduled_at TEXT DEFAULT '',
                alarm_trigger_at TEXT DEFAULT '',
                alarm_dismissed_at TEXT DEFAULT '',
                grace_due_at TEXT DEFAULT '',
                resolved_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                alarm_cancel_pending INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_reminders_resolved
            ON pending_reminders(resolved)
        """)
        # 迁移：为已有表添加 reminder_type 列（2026-05-18）
        try:
            conn.execute("ALTER TABLE pending_reminders ADD COLUMN reminder_type TEXT DEFAULT 'task'")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # 查岗手机闹钟工作流字段（2026-08-24）
        for col, col_def in [
            ("workflow_state", "TEXT DEFAULT 'pending'"),
            ("alarm_id", "TEXT DEFAULT ''"),
            ("alarm_scheduled_at", "TEXT DEFAULT ''"),
            ("alarm_trigger_at", "TEXT DEFAULT ''"),
            ("alarm_dismissed_at", "TEXT DEFAULT ''"),
            ("grace_due_at", "TEXT DEFAULT ''"),
            ("resolved_at", "TEXT DEFAULT ''"),
            ("last_error", "TEXT DEFAULT ''"),
            ("alarm_cancel_pending", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE pending_reminders ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.execute(
            "UPDATE pending_reminders SET workflow_state='pending' "
            "WHERE workflow_state IS NULL OR workflow_state=''"
        )
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_reminders_workflow
            ON pending_reminders(resolved, workflow_state, grace_due_at)
        """)


def _row_to_task(row: sqlite3.Row) -> ScheduledTask:
    return ScheduledTask(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        reason=row["reason"] or "",
        ai_context=row["ai_context"] or "",
        task_type=row["task_type"],
        trigger_type=row["trigger_type"],
        trigger_config=json.loads(row["trigger_config"]),
        notify_method=json.loads(row["notify_method"]),
        alarm_enabled=bool(row["alarm_enabled"]),
        message_content=row["message_content"],
        is_active=bool(row["is_active"]),
        is_ai_generated=bool(row["is_ai_generated"]),
        created_by=row["created_by"] or "user",
        tool_name=row["tool_name"] or "",
        tool_params=json.loads(row["tool_params"] or "{}"),
        execution_prompt=row["execution_prompt"] or "",
        max_rounds=row["max_rounds"] or 2,
        trigger_window_seconds=row["trigger_window_seconds"] or 300,
        last_triggered_at=row["last_triggered_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_task(req: TaskCreateRequest) -> ScheduledTask:
    """创建任务"""
    now = datetime.now().isoformat()
    task_id = str(uuid.uuid4())
    task = ScheduledTask(
        id=task_id,
        title=req.title,
        description=req.description,
        reason=req.reason,
        ai_context=req.ai_context,
        task_type=req.task_type,
        trigger_type=req.trigger_type,
        trigger_config=req.trigger_config,
        notify_method=req.notify_method,
        alarm_enabled=req.alarm_enabled,
        message_content=req.message_content,
        is_ai_generated=req.is_ai_generated,
        created_by=req.created_by,
        tool_name=req.tool_name,
        tool_params=req.tool_params,
        execution_prompt=req.execution_prompt,
        max_rounds=req.max_rounds,
        trigger_window_seconds=req.trigger_window_seconds,
        created_at=now,
        updated_at=now,
    )
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO scheduled_tasks
               (id, title, description, reason, ai_context,
                task_type, trigger_type, trigger_config,
                notify_method, alarm_enabled, message_content, is_active, is_ai_generated,
                created_by, tool_name, tool_params, execution_prompt,
                max_rounds, trigger_window_seconds,
                last_triggered_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id, task.title, task.description, task.reason, task.ai_context,
                task.task_type, task.trigger_type, json.dumps(task.trigger_config),
                json.dumps(task.notify_method), int(task.alarm_enabled), task.message_content,
                int(task.is_active), int(task.is_ai_generated),
                task.created_by, task.tool_name, json.dumps(task.tool_params),
                task.execution_prompt, task.max_rounds, task.trigger_window_seconds,
                task.last_triggered_at, task.created_at, task.updated_at,
            ),
        )
    return task


def update_task(task_id: str, updates: dict) -> Optional[ScheduledTask]:
    """更新任务"""
    updates["updated_at"] = datetime.now().isoformat()
    fields = []
    values = []
    for key, val in updates.items():
        if key in ("trigger_config", "tool_params"):
            val = json.dumps(val)
        elif key == "notify_method":
            val = json.dumps(val)
        elif key in ("is_active", "is_ai_generated", "alarm_enabled") and isinstance(val, bool):
            val = int(val)
        fields.append(f"{key}=?")
        values.append(val)
    values.append(task_id)
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id=?",
            values,
        )
    return get_task(task_id)


def delete_task(task_id: str) -> bool:
    """删除任务"""
    with _get_conn() as conn:
        cursor = conn.execute("DELETE FROM scheduled_tasks WHERE id=?", (task_id,))
        return cursor.rowcount > 0


def find_active_by_trigger(trigger_type: str, trigger_config: dict) -> Optional[ScheduledTask]:
    """按 trigger_type + trigger_config 查找活跃任务（用于去重）。"""
    import json as _json
    tc_json = _json.dumps(trigger_config, ensure_ascii=False, sort_keys=True)
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE trigger_type=? AND trigger_config=? AND is_active=1 LIMIT 1",
            (trigger_type, tc_json)
        ).fetchone()
        if row:
            return _row_to_task(row)
    return None


def get_task(task_id: str) -> Optional[ScheduledTask]:
    """获取单个任务"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
        return _row_to_task(row) if row else None


def list_tasks(include_inactive: bool = False) -> List[ScheduledTask]:
    """列出所有任务"""
    with _get_conn() as conn:
        if include_inactive:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE is_active=1 ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]


def get_proactive_schedule_snapshot(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read the current queue and recent checkup volume for proactive messages."""
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=24)).isoformat()
    with closing(_get_conn()) as conn:
        active_rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE is_active=1 ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        active_totals = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN task_type='self_task' AND title LIKE '查岗%' THEN 1 ELSE 0 END) AS checkups, "
            "SUM(CASE WHEN task_type IN ('reminder', 'message', 'inconsistency') THEN 1 ELSE 0 END) AS reminders "
            "FROM scheduled_tasks WHERE is_active=1"
        ).fetchone()
        recent_checkup_count = conn.execute(
            "SELECT COUNT(*) FROM scheduled_tasks "
            "WHERE created_by='ai' AND task_type='self_task' "
            "AND title LIKE '查岗%' AND created_at>=?",
            (cutoff,),
        ).fetchone()[0]
    return {
        "active_tasks": [_row_to_task(row) for row in active_rows],
        "active_count": active_totals["total"],
        "active_checkup_count": active_totals["checkups"] or 0,
        "active_reminder_count": active_totals["reminders"] or 0,
        "recent_checkup_count": recent_checkup_count,
    }


def get_due_tasks() -> List[ScheduledTask]:
    """获取当前到期的活跃任务"""
    now = datetime.now()
    results = []
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE is_active=1"
        ).fetchall()
    for row in rows:
        task = _row_to_task(row)
        if _is_due(task, now):
            results.append(task)
    return results


def _is_due(task: ScheduledTask, now: datetime) -> bool:
    """判断任务是否到期"""
    config = task.trigger_config
    if task.trigger_type == "datetime":
        trigger_str = config.get("datetime", "")
        if not trigger_str:
            return False
        try:
            trigger_time = datetime.fromisoformat(trigger_str)
        except ValueError:
            return False
        # 已到时间且从未触发过
        if now >= trigger_time and task.last_triggered_at is None:
            return True
        # 已到时间但上次触发在设置更改之前（重新激活场景）
        if now >= trigger_time and task.last_triggered_at:
            last = datetime.fromisoformat(task.last_triggered_at)
            if trigger_time > last:
                return True
        return False
    elif task.trigger_type == "cron":
        cron_expr = config.get("cron", "")
        if not cron_expr:
            return False
        # 用简单检查：每分钟轮询，检查当前分钟是否匹配 cron
        # 只在从未触发过或上次触发不是本分钟内才触发
        parts = cron_expr.strip().split()
        if len(parts) < 5:
            return False
        if not _match_cron(parts, now):
            return False
        if task.last_triggered_at is None:
            return True
        # 同一分钟内不重复触发
        last = datetime.fromisoformat(task.last_triggered_at)
        if now.strftime("%Y%m%d%H%M") == last.strftime("%Y%m%d%H%M"):
            return False
        return True
    return False


def _match_cron(parts: list, dt: datetime) -> bool:
    """简易 cron 匹配（仅支持 5 位标准 cron 表达式）"""
    # minute hour day month weekday
    # 标准 cron: 0=Sunday, Python weekday(): 0=Monday
    cron_weekday = (dt.weekday() + 1) % 7
    fields = [dt.minute, dt.hour, dt.day, dt.month, cron_weekday]
    for i, (part, val) in enumerate(zip(parts, fields)):
        if not _cron_part_match(part, val):
            return False
    return True


def _cron_part_match(pattern: str, value: int) -> bool:
    """匹配 cron 字段的单个值"""
    if pattern == "*":
        return True
    if "/" in pattern:
        base, step = pattern.split("/", 1)
        base_val = int(base) if base != "*" else 0
        step_val = int(step)
        if step_val == 0:
            return False
        return (value - base_val) % step_val == 0 and value >= base_val
    if "," in pattern:
        return value in (int(p) for p in pattern.split(","))
    if "-" in pattern:
        low, high = (int(x) for x in pattern.split("-", 1))
        return low <= value <= high
    return int(pattern) == value


# ── Pending Reminders CRUD ──────────────────────────────────


def create_pending_reminder(pending: PendingReminder) -> str:
    """创建待复查提醒记录，返回 id"""
    if not pending.id:
        pending.id = str(uuid.uuid4())
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO pending_reminders
               (id, task_id, task_title, reason, ai_context,
                session_id, original_message, triggered_at,
                recheck_stage, resolved, reminder_type,
                workflow_state, alarm_id, alarm_scheduled_at,
                alarm_trigger_at, alarm_dismissed_at, grace_due_at,
                resolved_at, last_error, alarm_cancel_pending)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pending.id, pending.task_id, pending.task_title,
                pending.reason, pending.ai_context,
                pending.session_id, pending.original_message,
                pending.triggered_at, pending.recheck_stage,
                pending.resolved, pending.reminder_type,
                pending.workflow_state, pending.alarm_id,
                pending.alarm_scheduled_at, pending.alarm_trigger_at,
                pending.alarm_dismissed_at, pending.grace_due_at,
                pending.resolved_at, pending.last_error,
                pending.alarm_cancel_pending,
            ),
        )
    return pending.id


def get_pending_reminders(stage: int = None, resolved: int = 0) -> List[dict]:
    """获取待复查提醒列表"""
    with _get_conn() as conn:
        if stage is not None:
            rows = conn.execute(
                """SELECT * FROM pending_reminders
                   WHERE resolved=? AND recheck_stage=?
                   ORDER BY triggered_at ASC""",
                (resolved, stage),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM pending_reminders
                   WHERE resolved=?
                   ORDER BY triggered_at ASC""",
                (resolved,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_pending_reminder(reminder_id: str) -> Optional[dict]:
    """按事件 ID 读取单条待处理记录。"""
    if not reminder_id:
        return None
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_reminders WHERE id=?", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None


def get_latest_pending_reminder(session_id: str = None) -> Optional[dict]:
    """取最近一条未解决事件，用户新消息只结束这一个事件。"""
    with _get_conn() as conn:
        if session_id:
            row = conn.execute(
                """SELECT * FROM pending_reminders
                   WHERE resolved=0 AND (session_id=? OR session_id='')
                   ORDER BY triggered_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM pending_reminders
                   WHERE resolved=0
                   ORDER BY triggered_at DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None


def get_due_grace_reminders(now_iso: str) -> List[dict]:
    """读取已按掉闹钟且宽限期已到的查岗事件，供后端轮询兜底。"""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM pending_reminders
               WHERE resolved=0 AND workflow_state='grace_wait'
                 AND grace_due_at != '' AND grace_due_at <= ?
               ORDER BY grace_due_at ASC""",
            (now_iso,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_alarm_cancellations() -> List[dict]:
    """读取断线期间未送达手机的闹钟撤销请求。"""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM pending_reminders
               WHERE resolved=1 AND alarm_cancel_pending=1
               ORDER BY resolved_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def claim_pending_state(
    reminder_id: str,
    expected_states,
    new_state: str,
    updates: Optional[dict] = None,
) -> bool:
    """以状态条件原子领取一条事件，避免复判/最终消息重复发送。"""
    if isinstance(expected_states, str):
        expected_states = [expected_states]
    expected_states = list(expected_states or [])
    if not reminder_id or not expected_states:
        return False
    fields = ["workflow_state=?"]
    values = [new_state]
    for key, val in (updates or {}).items():
        fields.append(f"{key}=?")
        values.append(val)
    placeholders = ",".join("?" for _ in expected_states)
    values.extend([reminder_id, *expected_states])
    with _get_conn() as conn:
        cur = conn.execute(
            f"""UPDATE pending_reminders
                SET {', '.join(fields)}
                WHERE id=? AND resolved=0 AND workflow_state IN ({placeholders})""",
            values,
        )
        return cur.rowcount == 1


def resolve_pending_reminder(reminder_id: str, workflow_state: str = "resolved") -> bool:
    """只解决指定事件，不影响其他 pending。"""
    if not reminder_id:
        return False
    with _get_conn() as conn:
        cur = conn.execute(
            """UPDATE pending_reminders
               SET resolved=1, workflow_state=?, resolved_at=?
               WHERE id=? AND resolved=0""",
            (workflow_state, datetime.now().isoformat(), reminder_id),
        )
        return cur.rowcount == 1


def update_pending_reminder(reminder_id: str, updates: dict):
    """更新待复查提醒"""
    fields = []
    values = []
    for key, val in updates.items():
        fields.append(f"{key}=?")
        values.append(val)
    values.append(reminder_id)
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE pending_reminders SET {', '.join(fields)} WHERE id=?",
            values,
        )


def resolve_all_pending_reminders(session_id: str = None):
    """标记所有（或指定会话的）待复查提醒为已解决"""
    with _get_conn() as conn:
        if session_id:
            conn.execute(
                """UPDATE pending_reminders SET resolved=1
                   WHERE resolved=0 AND session_id=?""",
                (session_id,),
            )
        else:
            conn.execute(
                "UPDATE pending_reminders SET resolved=1 WHERE resolved=0"
            )
