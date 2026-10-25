"""
日历管理器 - SQLite 数据库层

数据库: events/calendar.db
表:
  - calendar_events: 纪念日和生理期事件
  - system_state: key-value 系统状态（last_active_at 等）

date_string 格式统一为 YYYY-MM-DD:
  - 纪念日使用虚拟年份 0000（如 0000-04-28），严禁传入 datetime 解析
  - 生理期使用实际年份（如 2026-04-28）
  - _parse_calendar_date() 统一处理两种格式
"""
import sqlite3
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from .models import CalendarEvent, CalendarEventCreateRequest

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "events")
DB_PATH = os.path.join(DB_DIR, "calendar.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_calendar_date(date_string: str) -> Dict[str, str]:
    """
    统一日期解析。0000 年份走手动字符串拆分（不走 datetime），
    其他年份走 datetime.fromisoformat()。

    Returns:
        {"year": "2026", "month": "04", "day": "28", "month_day": "04-28"}
    """
    parts = date_string.split("-")
    if len(parts) != 3:
        raise ValueError(f"date_string 格式必须为 YYYY-MM-DD，收到: {date_string}")
    year, month, day = parts[0], parts[1], parts[2]
    if year == "0000":
        # 虚拟年份，不经过 datetime 解析（Python datetime 不支持 year=0）
        pass
    else:
        # 验证可通过 datetime 解析（确保是合法日期）
        datetime.fromisoformat(date_string)
    return {"year": year, "month": month, "day": day, "month_day": f"{month}-{day}"}


def _is_leap_year(year: int) -> bool:
    """判断是否为润年"""
    if year == 0:
        # 虚拟年份 0000：按公历规则，0000 年不算润年
        # 实际触发靠日期匹配，这里返回 False 仅用于防护
        return False
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _today_mm_dd() -> str:
    """返回今天的 MM-DD 字符串"""
    return datetime.now().strftime("%m-%d")


def _today_iso() -> str:
    """返回今天的 YYYY-MM-DD 字符串"""
    return datetime.now().strftime("%Y-%m-%d")


# ── 数据库初始化 ──────────────────────────────────────

def init_db():
    """初始化数据库表（含 CHECK 约束迁移）"""
    with _get_conn() as conn:
        # 先建表（新数据库不带 CHECK）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                date_string TEXT NOT NULL,
                creator TEXT DEFAULT '人类伙伴',
                emotion TEXT DEFAULT '',
                last_scanned_at TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calendar_type_date
            ON calendar_events(event_type, date_string)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # 兼容旧表新增列
        for col in ("emotion",):
            try:
                conn.execute(f"ALTER TABLE calendar_events ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        # 迁移旧 CHECK 约束：允许 rest_day 类型（SQLite 不支持 ALTER CHECK，需重建）
        _migrate_check_constraint(conn)


def _migrate_check_constraint(conn: sqlite3.Connection):
    """迁移旧 CHECK 约束，增加 rest_day 类型支持"""
    # 尝试插入一条 rest_day 测试记录
    try:
        conn.execute("SAVEPOINT check_migration")
        conn.execute(
            "INSERT INTO calendar_events (id, title, event_type, date_string, created_at, updated_at) "
            "VALUES ('__migration_test__', 'test', 'rest_day', '0000-01-01', '', '')"
        )
        conn.execute("DELETE FROM calendar_events WHERE id='__migration_test__'")
        conn.execute("RELEASE check_migration")
    except sqlite3.IntegrityError:
        # CHECK 约束阻止了 rest_day，需要重建
        conn.execute("ROLLBACK")
        _rebuild_table_with_new_types(conn)


def _rebuild_table_with_new_types(conn: sqlite3.Connection):
    """重建 calendar_events 表以支持 rest_day 类型"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info("迁移 calendar_events CHECK 约束以支持 rest_day 类型...")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events_new (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            event_type TEXT NOT NULL,
            date_string TEXT NOT NULL,
            creator TEXT DEFAULT '人类伙伴',
            emotion TEXT DEFAULT '',
            last_scanned_at TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO calendar_events_new
        SELECT * FROM calendar_events
    """)
    conn.execute("DROP TABLE calendar_events")
    conn.execute("ALTER TABLE calendar_events_new RENAME TO calendar_events")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calendar_type_date
        ON calendar_events(event_type, date_string)
    """)
    logger.info("CHECK 约束迁移完成")


# ── 行 → 模型转换 ─────────────────────────────────────

def _row_to_event(row: sqlite3.Row) -> CalendarEvent:
    return CalendarEvent(
        id=row["id"],
        title=row["title"],
        description=row["description"] or "",
        event_type=row["event_type"],
        date_string=row["date_string"],
        creator=row["creator"] or "人类伙伴",
        emotion=row["emotion"] or "",
        last_scanned_at=row["last_scanned_at"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── CRUD ─────────────────────────────────────────────

def create_event(req: CalendarEventCreateRequest) -> CalendarEvent:
    """创建日历事件。生理期同日期去重：如已存在则返回已有事件。"""
    now = datetime.now().isoformat()
    event_id = str(uuid.uuid4())

    # 纪念日：年份部分替换为 0000（统一格式）
    date_string = req.date_string
    if req.event_type == "memorial":
        parts = date_string.split("-")
        if len(parts) == 3:
            date_string = f"0000-{parts[1].zfill(2)}-{parts[2].zfill(2)}"

    # 生理期同日期去重
    if req.event_type == "period":
        with _get_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM calendar_events WHERE event_type='period' AND date_string=?",
                (date_string,),
            ).fetchone()
            if existing:
                return _row_to_event(existing)

    event = CalendarEvent(
        id=event_id,
        title=req.title,
        description=req.description,
        event_type=req.event_type,
        date_string=date_string,
        creator=req.creator,
        emotion=req.emotion,
        is_active=req.is_active,
        created_at=now,
        updated_at=now,
    )
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO calendar_events
               (id, title, description, event_type, date_string, creator, emotion,
                last_scanned_at, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id, event.title, event.description, event.event_type,
                event.date_string, event.creator, event.emotion,
                event.last_scanned_at, int(event.is_active),
                event.created_at, event.updated_at,
            ),
        )
    return event


def update_event(event_id: str, updates: dict) -> Optional[CalendarEvent]:
    """更新日历事件"""
    updates["updated_at"] = datetime.now().isoformat()
    fields = []
    values = []
    for key, val in updates.items():
        if key in ("is_active",) and isinstance(val, bool):
            val = int(val)
        fields.append(f"{key}=?")
        values.append(val)
    values.append(event_id)
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE calendar_events SET {', '.join(fields)} WHERE id=?",
            values,
        )
    return get_event(event_id)


def delete_event(event_id: str) -> bool:
    """删除日历事件"""
    with _get_conn() as conn:
        cursor = conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
        return cursor.rowcount > 0


def get_event(event_id: str) -> Optional[CalendarEvent]:
    """获取单个日历事件"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM calendar_events WHERE id=?", (event_id,)
        ).fetchone()
        return _row_to_event(row) if row else None


def list_events(year: Optional[int] = None, pending_only: bool = False) -> List[CalendarEvent]:
    """列出日历事件。year 为 None 时返回所有，pending_only=True 返回待确认的"""
    with _get_conn() as conn:
        # 普通列表只展示当前有效事件；待确认列表则只展示 inactive 候选。
        # 旧实现普通列表没有 is_active 条件，导致被拒绝/停用的纪念日重新出现在
        # 书柜和工具结果中。
        conditions = ["is_active=0" if pending_only else "is_active=1"]
        params = []
        if year is not None:
            # 纪念日（0000 年份）始终返回；生理期和休息日只返回指定年份。
            # 旧条件把所有年份的 rest_day 都混进任意年份列表。
            conditions.append(
                "(event_type='memorial' OR ((event_type='rest_day' OR event_type='period') "
                "AND date_string LIKE ?))"
            )
            params.append(f"{year}-%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM calendar_events {where} ORDER BY date_string ASC",
            params,
        ).fetchall()
        return [_row_to_event(r) for r in rows]


def get_pending_confirmations() -> List[CalendarEvent]:
    """获取待用户确认的纪念日（is_active=False）"""
    return list_events(pending_only=True)


# ── 月度查询 ─────────────────────────────────────────

def get_events_by_month(year: int, month: int) -> Dict[int, List[CalendarEvent]]:
    """
    返回当月每天的事件映射 {day_number: [events]}。
    仅返回 is_active=True 的事件。生理期事件附加 period_day_number（emotion 字段暂存）。
    """
    result: Dict[int, List[CalendarEvent]] = {}
    month_str = f"{month:02d}"
    year_str = f"{year:04d}"
    with _get_conn() as conn:
        # 纪念日：匹配 0000-MM 的 date_string
        memorial_rows = conn.execute(
            """SELECT * FROM calendar_events
               WHERE event_type='memorial' AND is_active=1 AND date_string LIKE ?""",
            (f"%-{month_str}-%",),
        ).fetchall()
        for row in memorial_rows:
            ev = _row_to_event(row)
            try:
                day = int(ev.date_string.split("-")[2])
            except (IndexError, ValueError):
                continue
            if ev.date_string == "0000-02-29" and not _is_leap_year(year):
                continue
            result.setdefault(day, []).append(ev)

        # 生理期：跨月连续段检测。拉取前月后7天+后月前7天的数据，正确计算跨月 streak
        from datetime import date, timedelta
        period_start = (date(year, month, 1) - timedelta(days=7)).isoformat()
        period_end = (date(year, month, 28) + timedelta(days=14)).isoformat()  # 足够覆盖月末
        period_rows = conn.execute(
            """SELECT * FROM calendar_events
               WHERE event_type='period' AND is_active=1
                 AND date_string >= ? AND date_string <= ?
               ORDER BY date_string ASC""",
            (period_start, period_end),
        ).fetchall()

        if period_rows:
            all_period_events = [_row_to_event(r) for r in period_rows]
            all_period_dates = [ev.date_string for ev in all_period_events]
            # 构建 day_number 映射
            day_number_map: Dict[str, int] = {}
            i = 0
            while i < len(all_period_dates):
                streak_start = i
                i += 1
                while i < len(all_period_dates):
                    prev_dt = datetime.fromisoformat(all_period_dates[i - 1])
                    curr_dt = datetime.fromisoformat(all_period_dates[i])
                    if (curr_dt - prev_dt).days == 1:
                        i += 1
                    else:
                        break
                for j in range(streak_start, i):
                    day_number_map[all_period_dates[j]] = j - streak_start + 1

            # 只返回当月的事件
            month_prefix = f"{year_str}-{month_str}-"
            for ev in all_period_events:
                if not ev.date_string.startswith(month_prefix):
                    continue
                try:
                    day = int(ev.date_string.split("-")[2])
                except (IndexError, ValueError):
                    continue
                ev.emotion = str(day_number_map.get(ev.date_string, 0))
                result.setdefault(day, []).append(ev)

        # 休息日：匹配 YYYY-MM-DD 的 date_string
        rest_rows = conn.execute(
            """SELECT * FROM calendar_events
               WHERE event_type='rest_day' AND is_active=1 AND date_string LIKE ?""",
            (f"{year_str}-{month_str}-%",),
        ).fetchall()
        for row in rest_rows:
            ev = _row_to_event(row)
            try:
                day = int(ev.date_string.split("-")[2])
            except (IndexError, ValueError):
                continue
            result.setdefault(day, []).append(ev)

    return result


def get_active_period_today() -> Optional[CalendarEvent]:
    """获取今天的活跃生理期事件"""
    today = _today_iso()
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM calendar_events
               WHERE event_type='period' AND is_active=1 AND date_string=?""",
            (today,),
        ).fetchone()
        return _row_to_event(row) if row else None


def get_period_info_today() -> dict:
    """
    获取今天的生理期信息，包括：是否在生理期、第几天、提示词。

    通过查出本年度所有活跃生理期日期，找到包含今天的连续区间，
    计算今天在区间中的位置（从 1 开始）。

    Returns:
        {"active": bool, "day_number": int|None, "is_first_two_days": bool, "note": str|None}
    """
    today = _today_iso()
    today_dt = datetime.fromisoformat(today)

    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT date_string FROM calendar_events
               WHERE event_type='period' AND is_active=1 AND date_string LIKE ?
               ORDER BY date_string ASC""",
            (f"{today_dt.year}-%",),
        ).fetchall()

    period_dates = sorted([r["date_string"] for r in rows])
    if today not in period_dates:
        return {"active": False, "day_number": None, "is_first_two_days": False, "note": None}

    # 找出包含 today 的连续日期区间
    from datetime import timedelta
    # 找到 today 所在连续段的起点
    idx = period_dates.index(today)
    start_idx = idx
    while start_idx > 0:
        prev_date = datetime.fromisoformat(period_dates[start_idx - 1])
        curr_date = datetime.fromisoformat(period_dates[start_idx])
        if (curr_date - prev_date).days == 1:
            start_idx -= 1
        else:
            break

    day_number = idx - start_idx + 1
    is_first_two_days = day_number <= 2

    if day_number == 1:
        note = "人类伙伴今天生理期第1天，肚子疼得厉害，整个人不太舒服。你别撩她，多关心她的身体，少整那些暧昧的。"
    elif day_number == 2:
        note = "人类伙伴今天生理期第2天，肚子还是疼，还是不太舒服。你别撩她，多关心她的身体，少整那些暧昧的。"
    else:
        note = f"人类伙伴生理期第{day_number}天了，好一些了但还没完全恢复。稍微收着点，别太撩她，温和一点就好。"

    return {"active": True, "day_number": day_number, "is_first_two_days": is_first_two_days, "note": note}


# ── 扫描相关 ─────────────────────────────────────────

def get_memorial_events_for_today() -> List[CalendarEvent]:
    """
    获取今天需要扫描的纪念日事件（MM-DD 匹配，且 last_scanned_at 不是今天）。
    润年 Feb 29 在非润年不返回。
    """
    today = datetime.now()
    mm_dd = today.strftime("%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM calendar_events
               WHERE event_type='memorial' AND is_active=1 AND date_string=?""",
            (f"0000-{mm_dd}",),
        ).fetchall()

    events = []
    for row in rows:
        ev = _row_to_event(row)
        # 润年 Feb 29 检查
        if ev.date_string == "0000-02-29" and not _is_leap_year(today.year):
            continue
        # 今天已扫描过则跳过
        if ev.last_scanned_at and ev.last_scanned_at[:10] == today_str:
            continue
        events.append(ev)
    return events


def get_missed_memorial_events(days_absent: int) -> List[dict]:
    """
    获取缺席期间错过的纪念日事件（用于兜底提示词）。
    Returns: [{title, date_string, days_ago}, ...]
    """
    if days_absent <= 0:
        return []
    today = datetime.now()
    missed = []
    for d in range(1, min(days_absent + 1, 31)):  # 最多回溯 31 天
        past_date = today - timedelta(days=d)
        mm_dd = past_date.strftime("%m-%d")

        # 润年 Feb 29 只在润年检查
        if mm_dd == "02-29" and not _is_leap_year(past_date.year):
            continue

        with _get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM calendar_events
                   WHERE event_type='memorial' AND is_active=1 AND date_string=?""",
                (f"0000-{mm_dd}",),
            ).fetchall()
            for row in rows:
                ev = _row_to_event(row)
                missed.append({
                    "title": ev.title,
                    "date_string": ev.date_string,
                    "days_ago": d,
                    "creator": ev.creator,
                })
    return missed


def batch_update_last_scanned(event_ids: List[str]) -> bool:
    """事务中批量更新 last_scanned_at（仅对 LLM 生成成功的事件调用）"""
    if not event_ids:
        return True
    now = datetime.now().isoformat()
    placeholders = ", ".join("?" for _ in event_ids)
    try:
        with _get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"UPDATE calendar_events SET last_scanned_at=? WHERE id IN ({placeholders})",
                [now] + event_ids,
            )
            conn.execute("COMMIT")
        return True
    except Exception:
        return False


# ── 去重 ─────────────────────────────────────────────

def check_duplicate_memorial(mm_dd: str, title_prefix: str = "") -> bool:
    """
    检查是否已存在同日期同类型的纪念日事件。
    用于自动检测去重。
    """
    date_string = f"0000-{mm_dd}"
    with _get_conn() as conn:
        if title_prefix:
            row = conn.execute(
                """SELECT id FROM calendar_events
                   WHERE event_type='memorial' AND date_string=? AND title LIKE ?""",
                (date_string, f"{title_prefix}%"),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id FROM calendar_events
                   WHERE event_type='memorial' AND date_string=?""",
                (date_string,),
            ).fetchone()
        return row is not None


# ── 年度归档 ─────────────────────────────────────────

def delete_period_events_by_year(year: int) -> int:
    """删除指定年份的生理期事件，返回删除数量"""
    year_prefix = f"{year:04d}-"
    with _get_conn() as conn:
        cursor = conn.execute(
            """DELETE FROM calendar_events
               WHERE event_type='period' AND date_string LIKE ?""",
            (f"{year_prefix}%",),
        )
        return cursor.rowcount


def archive_old_events() -> dict:
    """
    年度归档：删除上个年份及更早的生理期事件，纪念日保留。
    在每年 1 月 1 日 00:01 调用。
    Returns: {"deleted_period_count": int}
    """
    current_year = datetime.now().year
    deleted = 0
    for year in range(current_year - 5, current_year):  # 清理过去 5 年的
        deleted += delete_period_events_by_year(year)
    return {"deleted_period_count": deleted}


# ── 系统状态 ─────────────────────────────────────────

def get_system_state(key: str) -> Optional[str]:
    """获取系统状态值"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_system_state(key: str, value: str):
    """设置系统状态值（upsert）"""
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO system_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )


# ── 休息日同步 ─────────────────────────────────────────

def sync_rest_days_for_year(year: int) -> int:
    """
    根据中国法定节假日（双休版）生成全年休息日事件。
    每年 1 月 1 日调用。返回创建的事件数量。

    逻辑：
    - 普通周六/周日 → 休息日
    - 法定节假日当天 → 休息日
    - 调休工作日（周末补班）→ 不标记为休息日
    """
    from datetime import date, timedelta

    try:
        from chinese_calendar import is_workday, is_holiday
    except ImportError:
        import logging
        logging.getLogger(__name__).warning("chinese-calendar 未安装，跳过休息日同步")
        return 0

    # 删除旧的本年度休息日事件
    with _get_conn() as conn:
        conn.execute(
            "DELETE FROM calendar_events WHERE event_type='rest_day' AND date_string LIKE ?",
            (f"{year:04d}-%",),
        )

    created = 0
    today = date(year, 1, 1)
    end = date(year, 12, 31)
    while today <= end:
        date_str = today.strftime("%Y-%m-%d")
        if not is_workday(today):  # 周六日 + 法定节假日，排除调休
            holiday_name = ""
            if is_holiday(today):
                holiday_name = " 法定节假日" if not (today.weekday() >= 5) else ""
            title = "休息日" + holiday_name
            desc = f"{date_str} 人类伙伴休息"
            _create_rest_day_event(date_str, title, desc)
            created += 1
        today += timedelta(days=1)

    return created


def _create_rest_day_event(date_string: str, title: str, description: str):
    """创建单条休息日事件（内部使用，不走去重检查）"""
    import uuid
    now = datetime.now().isoformat()
    event_id = str(uuid.uuid4())
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO calendar_events
               (id, title, description, event_type, date_string, creator,
                last_scanned_at, is_active, created_at, updated_at)
               VALUES (?, ?, ?, 'rest_day', ?, 'system', NULL, 1, ?, ?)""",
            (event_id, title, description, date_string, now, now),
        )


def is_rest_day_today() -> bool:
    """检查今天是否为休息日（日历标记）"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM calendar_events WHERE event_type='rest_day' AND is_active=1 AND date_string=?",
            (today_str,),
        ).fetchone()
        return row is not None
