"""上下文注释生成器 — 快照行 + 时间线注解

为 context_scheduler 提供两个输出：
1. 哨兵快照行（每轮上下文始终注入）
2. 哨兵时间线注解（查询今日 sentinel_events）
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class ContextAnnotator:
    """生成哨兵相关的上下文注入文本"""

    def __init__(self, health_store):
        self._store = health_store

    # ── 快照行（统一数据过期模型：上次值 + 时间 + 连接状态）──

    def build_snapshot_line(self) -> str:
        """生成哨兵快照行（纯健康传感器：心率 | 步数 | 生理期）。

        行为状态/GPS/应用会话已移入 activity_summary。始终返回。
        """
        parts = []

        # 心率
        hr_stats = self._store.get_hr_stats_recent(minutes=5)
        if hr_stats["count"] > 0:
            parts.append(f"哨兵心率源近5分钟采样均值{hr_stats['avg']}bpm")
        else:
            parts.append("哨兵心率源:近5分钟未取得采样")

        # 步数（带过期信息 + 基线对比）
        try:
            from silicon_perception.sentinel import get_sentinel
            s = get_sentinel()
            if s and s._step_source:
                ss = s._step_source
                snap = getattr(s, "_last_snapshot", None)
                observed_age = snap.age_of("steps") if snap and hasattr(snap, "age_of") else None
                age_min = int(observed_age / 60) if observed_age is not None else None
                steps = ss.today_steps
                steps_str = f"{steps}" if steps else "?"
                # 基线对比（偏离 >20% 时标注）
                baseline_note = ""
                if steps and isinstance(steps, int) and steps > 0:
                    try:
                        from datetime import datetime
                        today = datetime.now().strftime("%Y-%m-%d")
                        summary = s._store.get_daily_summaries(today, today)
                        if summary:
                            avg7 = summary[0].get("avg_steps_7d")
                            if avg7 and avg7 > 0:
                                pct = int(steps / avg7 * 100)
                                if pct < 80 or pct > 120:
                                    baseline_note = f"(7日均值{int(avg7)}的{pct}%)"
                    except Exception:
                        pass
                if ss.connection_state == "connected":
                    age_str = "采集时间未知" if age_min is None else f"{age_min}min前采集" if age_min >= 5 else "刚刚"
                    parts.append(f"哨兵步数源:{steps_str}{baseline_note}({age_str})")
                else:
                    age_str = "采集时间未知" if age_min is None else f"{age_min}min前采集"
                    parts.append(f"哨兵步数源:{steps_str}{baseline_note}({age_str}，当前暂未连接)")
            else:
                parts.append("哨兵步数源:未连接")
        except Exception:
            parts.append("哨兵步数源:—")


        # 生理期
        try:
            from calendar_manager.database import get_period_info_today
            info = get_period_info_today()
            if info and info.get("active"):
                day = info.get("day_number", "?")
                parts.append(f"生理期第{day}天")
            else:
                parts.append("非生理期")
        except Exception:
            pass

        return f"[哨兵] {' | '.join(parts)}"

    # ── 时间线注解 ────────────────────────────────────

    def _timeline_rows(self) -> list[dict]:
        """Read eligible events without changing their state."""
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._store._get_conn()
        rows = conn.execute(
            """SELECT * FROM sentinel_events
               WHERE date(timestamp) = ? AND pushed = 0 AND injected_to_context = 0 AND event_type != 'L1'
               ORDER BY timestamp ASC""",
            (today,),
        ).fetchall()
        rows = [dict(r) for r in rows]

        # 同日同类型去重（只取第一条），收集注入 ID 用于标记
        seen_types = set()
        deduped = []
        injected_ids = []
        for r in rows:
            etype = r.get("event_type", "instant")
            if etype in seen_types:
                continue
            seen_types.add(etype)
            deduped.append(r)
            injected_ids.append(r["id"])

        return deduped

    def peek_timeline_annotations(self) -> List[Dict[str, str]]:
        """Return timeline annotations without consuming one-shot events.

        The caller receives private ``_event_ids`` only for a request-local
        claim.  They are stripped before a prompt is sent to the provider.
        """
        annotations = []
        for ev in self._timeline_rows():
            ts = ev["timestamp"]
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = ts
            content = f"{time_str} [哨兵] {ev['message'] or ev['rule_id']}"
            annotations.append({"time": time_str, "content": content, "_event_id": ev["id"]})
        return annotations

    def commit_timeline_annotations(self, event_ids: list) -> int:
        """Consume exactly the claimed event IDs, idempotently."""
        ids = [str(value) for value in (event_ids or []) if value not in (None, "")]
        if not ids:
            return 0
        conn = self._store._get_conn()
        placeholders = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE sentinel_events SET injected_to_context = 1 "
            f"WHERE id IN ({placeholders}) AND injected_to_context = 0",
            ids,
        )
        conn.commit()
        return int(cur.rowcount or 0)

    def build_timeline_annotations(self) -> List[Dict[str, str]]:
        """Compatibility alias with peek semantics.

        Historically this method marked rows immediately.  Builder and
        diagnostics now use request-level claim+commit; retaining the name
        avoids breaking legacy callers while preventing premature loss.
        """
        return self.peek_timeline_annotations()

    def consume_timeline_annotations(self) -> List[Dict[str, str]]:
        """Legacy explicit consume helper for callers that truly need it."""
        annotations = self.peek_timeline_annotations()
        self.commit_timeline_annotations([item.get("_event_id") for item in annotations])
        return annotations
