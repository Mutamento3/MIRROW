# 哨兵健康数据召回工具
# 所有数据统一走哨兵 HealthStore（SQLite），不再依赖 health_tracker 缓存或手机 BLE 中继
# 哨兵已通过 60s 循环持续采集心率/步数/睡眠/血氧等数据，本工具只做查询出口
import logging
from datetime import datetime, timedelta

from .base_tool import BaseTool, ToolResult, ToolStatus

logger = logging.getLogger(__name__)




# ─── 哨兵查询（通过 HealthStore 单例）─────────────────


def _read_latest_hr_from_sentinel() -> tuple | None:
    """从哨兵 SQLite 读最近一次心率: (hr_value, timestamp_iso) 或 None。
    回看窗口 10min——超窗视为无最新数据（手环没戴/断连不吐数据），
    防把几小时前旧值当"最新"报出。"""
    try:
        from silicon_perception.recording.health_store import get_store
        snaps = get_store().get_recent_snapshots(lookback_seconds=600)
        for row in snaps:
            if row.get("heart_rate") is not None:
                return (int(row["heart_rate"]), row["timestamp"])
        return None
    except Exception as e:
        logger.warning(f"哨兵心率读取失败: {e}")
        return None


def _read_hr_stats_from_sentinel(seconds: int = 15) -> dict | None:
    """从哨兵 SQLite 读最近 N 秒心率统计: {avg, min, max, count, latest, time}"""
    try:
        from silicon_perception.recording.health_store import get_store
        stats = get_store().get_heart_rate_stats(seconds)
        if stats and stats.get("count", 0) > 0:
            return stats
        return None
    except Exception as e:
        logger.warning(f"哨兵心率统计失败: {e}")
        return None




class BandTool(BaseTool):
    name = "band"
    single_use = True  # 每轮对话只允许一次调用，防止 Pro 循环
    description = (
        "查询哨兵已持续监测的健康数据。操作：heart_rate（心率）| heart_rate_continuous（连续心率）| "
        "steps（步数）| sleep（睡眠）| spo2（血氧）| all_health（全部）| daily（今日健康概览）"
    )
    flash_description = "查健康数据——心率/步数/睡眠/血氧（哨兵已采集）"
    pro_guide = ("查哨兵持续监测的心率、步数、睡眠、血氧等健康数据。已有新鲜且足够的感知可以直接使用；"
                 "需要连续监测、精确查询或数据缺失时再说明要查的指标与时段。")

    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "heart_rate", "heart_rate_continuous",
                    "steps", "sleep", "spo2", "stress", "pai", "respiratory_rate", "all_health", "daily",
                    # Flash 模糊匹配别名
                    "heart", "heartrate", "heartbeat", "pulse",
                    "步数", "睡眠", "血氧", "全部", "每日", "今天怎么样",
                ],
                "description": (
                    "操作类型。heart_rate=当前心率, heart_rate_continuous=连续监测, "
                    "steps=今日步数, sleep=昨晚睡眠, spo2=血氧, "
                    "all_health=全部, daily=今日健康概览（含周对比和基线）"
                ),
            },
            "duration": {
                "type": "integer",
                "description": "连续监测时长（秒），仅 heart_rate_continuous 需要，默认 15",
                "default": 15,
            },
        },
        "required": ["action"],
    }

    _ACTION_MAP = {
        "heart": "heart_rate", "heartrate": "heart_rate",
        "heartbeat": "heart_rate", "pulse": "heart_rate",
        "步数": "steps", "睡眠": "sleep", "血氧": "spo2",
        "全部": "all_health", "每日": "daily", "今天怎么样": "daily",
    }

    def __init__(self):
        pass  # 不再需要 _adb_forwarded 状态

    def get_user_facing_description(self, **kwargs) -> str:
        action = kwargs.get("action", "")
        if action == "heart_rate":
            return "正在查询人类伙伴的心率..."
        elif action == "heart_rate_continuous":
            dur = kwargs.get("duration", 15)
            return f"正在查询人类伙伴最近 {dur} 秒心率..."
        elif action == "steps":
            return "正在查询人类伙伴的今日步数..."
        elif action == "sleep":
            return "正在查询人类伙伴的睡眠数据..."
        elif action == "spo2":
            return "正在查询人类伙伴的血氧数据..."
        elif action == "all_health":
            return "正在查询人类伙伴的全部健康数据..."
        elif action == "daily":
            return "正在查询人类伙伴的今日健康概览..."
        return "正在查询人类伙伴的健康数据..."

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action", "heart_rate")
        action = self._ACTION_MAP.get(action, action)
        from miband.context import query as band_query
        if action == 'heart_rate_continuous':
            from miband.store import heart_stats
            import json
            stats = heart_stats(kwargs.get('duration', 15))
            if stats:
                return ToolResult(status=ToolStatus.SUCCESS, content=json.dumps(stats, ensure_ascii=False))
        if action in ('stress', 'pai', 'respiratory_rate'):
            return ToolResult(status=ToolStatus.ERROR, content='该手环指标的数据源尚未接通，目前没有真实测量数据。')
        # Explicit source/time; existing phone/health-store path remains when absent.
        if action in ('heart_rate', 'steps', 'sleep'):
            observation = band_query(action)
            if observation:
                return ToolResult(status=ToolStatus.SUCCESS, content=observation)

        # ── 心率操作：读哨兵 SQLite（PC BLE 实时数据）──
        if action == "heart_rate":
            result = _read_latest_hr_from_sentinel()
            if result:
                hr, ts = result
                try:
                    hr_time = datetime.fromisoformat(ts)
                    hr_age_sec = (datetime.now() - hr_time).total_seconds()
                    freshness = f"{int(hr_age_sec)} 秒前" if hr_age_sec < 120 else f"{int(hr_age_sec / 60)} 分钟前"
                except Exception:
                    freshness = ts
                stats = _read_hr_stats_from_sentinel(seconds=30)
                extra = ""
                if stats and stats["count"] > 1:
                    extra = (
                        f" | 最近 30s: 最低 {stats['min']}, 最高 {stats['max']}, "
                        f"平均 {stats['avg']} ({stats['count']} 次)"
                    )
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    content=f"人类伙伴的心率: {hr} bpm（{freshness}）{extra}",
                )
            return ToolResult(
                status=ToolStatus.ERROR,
                content="哨兵暂无心率数据——手环可能不在连接范围。最近一次记录已超过 24 小时。",
                error="哨兵心率缓存为空",
            )

        if action == "heart_rate_continuous":
            duration = int(kwargs.get("duration", 15))
            duration = max(5, min(300, duration))
            stats = _read_hr_stats_from_sentinel(seconds=duration)
            if stats:
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    content=(
                        f"人类伙伴最近 {duration} 秒的心率 ({stats['count']} 次): "
                        f"最低 {stats['min']}, 最高 {stats['max']}, 平均 {stats['avg']} bpm"
                    ),
                )
            return ToolResult(
                status=ToolStatus.ERROR,
                content=f"哨兵最近 {duration} 秒无心率数据。手环可能不在连接范围。",
                error="哨兵无连续心率数据",
            )

        # ── 步数/睡眠/血氧/每日：从哨兵 HealthStore 读取 ──
        if action == "steps":
            return await self._get_steps_from_store()
        elif action == "sleep":
            return await self._get_sleep_from_store()
        elif action == "spo2":
            return await self._get_spo2_from_store()
        elif action == "all_health":
            return await self._get_all_health_from_store()
        elif action == "daily":
            return await self._get_daily_overview()

        return ToolResult(
            status=ToolStatus.ERROR, content="", error=f"未知操作: {action}"
        )

    # ─── 哨兵 HealthStore 数据读取方法 ───────────────────

    async def _get_steps_from_store(self) -> ToolResult:
        """从哨兵 HealthStore 读取今日步数。"""
        try:
            from silicon_perception.recording.health_store import get_store
            store = get_store()
            # 优先：health_snapshots 最新一条（取最近值，不翻找旧的非零值）
            snaps = store.get_recent_snapshots(lookback_seconds=600)
            for row in snaps:
                steps = row.get("steps_today")
                if steps is not None:
                    ts = row.get("timestamp", "")
                    try:
                        t = datetime.fromisoformat(ts)
                        age_sec = (datetime.now() - t).total_seconds()
                        freshness = f"{int(age_sec)} 秒前" if age_sec < 120 else f"{int(age_sec / 60)} 分钟前"
                    except Exception:
                        freshness = ts
                    return ToolResult(
                        status=ToolStatus.SUCCESS,
                        content=f"今日步数 {int(steps)} 步（{freshness}）",
                    )
            # 回退：daily_health_summary
            today = datetime.now().strftime("%Y-%m-%d")
            summaries = store.get_daily_summaries(today, today)
            if summaries:
                s = summaries[0]
                ds = s.get("steps")
                if ds is not None:
                    return ToolResult(status=ToolStatus.SUCCESS, content=f"今日步数 {int(ds)} 步（今日汇总）")
            return ToolResult(status=ToolStatus.ERROR, content="哨兵暂无今日步数数据。手机连接后会通过传感器自动采集。", error="无步数数据")
        except Exception as e:
            logger.warning(f"[BandTool] 步数读取失败: {e}")
            return ToolResult(status=ToolStatus.ERROR, content="步数数据读取失败。", error=str(e))

    async def _get_sleep_from_store(self) -> ToolResult:
        """从哨兵 daily_health_summary 读取昨日睡眠数据。"""
        try:
            from silicon_perception.recording.health_store import get_store
            store = get_store()
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            summaries = store.get_daily_summaries(yesterday, yesterday)
            if summaries and summaries[0].get("sleep_min") is not None:
                sleep_min = int(summaries[0]["sleep_min"])
                hours = sleep_min // 60
                minutes = sleep_min % 60
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    content=f"昨晚睡眠约 {hours} 小时 {minutes} 分钟",
                )
            return ToolResult(status=ToolStatus.ERROR, content="哨兵暂无昨晚睡眠数据。睡眠数据需通过手机上传。", error="无睡眠数据")
        except Exception as e:
            logger.warning(f"[BandTool] 睡眠读取失败: {e}")
            return ToolResult(status=ToolStatus.ERROR, content="睡眠数据读取失败。", error=str(e))

    async def _get_spo2_from_store(self) -> ToolResult:
        """从哨兵 daily_health_summary 读取血氧数据。"""
        try:
            from silicon_perception.recording.health_store import get_store
            store = get_store()
            today = datetime.now().strftime("%Y-%m-%d")
            summaries = store.get_daily_summaries(today, today)
            if summaries:
                s = summaries[0]
                spo2_min = s.get("spo2_min")
                spo2_max = s.get("spo2_max")
                if spo2_min is not None and spo2_max is not None:
                    return ToolResult(
                        status=ToolStatus.SUCCESS,
                        content=f"今日血氧: {spo2_min}% ~ {spo2_max}%",
                    )
            return ToolResult(status=ToolStatus.ERROR, content="哨兵暂无血氧数据。请通过手机上传健康报告。", error="无血氧数据")
        except Exception as e:
            logger.warning(f"[BandTool] 血氧读取失败: {e}")
            return ToolResult(status=ToolStatus.ERROR, content="血氧数据读取失败。", error=str(e))

    async def _get_all_health_from_store(self) -> ToolResult:
        """汇总所有健康数据（全部从 HealthStore）。"""
        steps_r = await self._get_steps_from_store()
        sleep_r = await self._get_sleep_from_store()
        spo2_r = await self._get_spo2_from_store()

        hr_result = _read_latest_hr_from_sentinel()
        hr_line = ""
        if hr_result:
            hr, ts = hr_result
            try:
                age = int((datetime.now() - datetime.fromisoformat(ts)).total_seconds())
                hr_line = f"❤️ 心率: {hr} bpm（{age}秒前）"
            except Exception:
                hr_line = f"❤️ 心率: {hr} bpm"
        else:
            hr_line = "❤️ 心率: 暂无数据"

        lines = ["健康数据汇总:", hr_line]
        from miband.context import facts as band_facts
        band_observation = band_facts()
        if band_observation:
            lines.append(band_observation)
        if steps_r.status == ToolStatus.SUCCESS:
            lines.append(f"🚶 {steps_r.content}")
        if sleep_r.status == ToolStatus.SUCCESS:
            lines.append(f"😴 {sleep_r.content}")
        if spo2_r.status == ToolStatus.SUCCESS:
            lines.append(f"🩸 {spo2_r.content}")

        return ToolResult(status=ToolStatus.SUCCESS, content="\n".join(lines))

    async def _get_daily_overview(self) -> ToolResult:
        """今日健康概览：每日汇总 + 周对比 + 7天行为基线。"""
        try:
            from silicon_perception.recording.health_store import get_store
            store = get_store()
            today = datetime.now().strftime("%Y-%m-%d")
            parts = ["今日健康概览:"]

            # 1. 今日汇总
            summaries = store.get_daily_summaries(today, today)
            if summaries:
                s = summaries[0]
                if s.get("steps"):
                    parts.append(f"步数: {int(s['steps'])} 步")
                if s.get("hr_avg"):
                    parts.append(f"心率: {int(s['hr_min'])}~{int(s['hr_max'])} bpm（平均 {int(s['hr_avg'])}）")
                if s.get("sleep_min"):
                    h, m = int(s["sleep_min"]) // 60, int(s["sleep_min"]) % 60
                    parts.append(f"睡眠: {h}h{m}m")
                if s.get("pc_active_min"):
                    parts.append(f"PC活跃: {int(s['pc_active_min'])} 分钟")
                if s.get("screen_time_total_min") is not None:
                    parts.append(f"手机屏幕: {int(s['screen_time_total_min'])} 分钟")
                if s.get("reply_count"):
                    parts.append(f"今日回复: {int(s['reply_count'])} 条")
                if s.get("behavior_chain"):
                    parts.append(f"行为链: {s['behavior_chain']}")

            # 2. 周对比
            try:
                weekly = store.get_weekly_comparison()
                if weekly:
                    sd = weekly.get("steps_delta")
                    wp = weekly.get("sleep_delta")
                    trend_parts = []
                    if sd is not None and sd != 0:
                        direction = "↑" if sd > 0 else "↓"
                        trend_parts.append(f"步数{direction}{abs(sd):.0f}%")
                    if wp is not None and wp != 0:
                        direction = "↑" if wp > 0 else "↓"
                        trend_parts.append(f"睡眠{direction}{abs(wp):.0f}%")
                    if trend_parts:
                        parts.append(f"本周 vs 上周: {', '.join(trend_parts)}")
            except Exception:
                pass

            # 3. 7天行为基线
            try:
                from silicon_perception.analysis.behavior_profile import BehaviorProfile
                bp = BehaviorProfile(store)
                baseline_text = bp.get_context()
                if baseline_text:
                    parts.append(f"7天基线: {baseline_text}")
            except Exception:
                pass

            from miband.context import facts as band_facts
            band_observation = band_facts()
            if band_observation:
                parts.append(band_observation)
            if len(parts) == 1:
                parts.append("暂无今日汇总数据。")

            return ToolResult(status=ToolStatus.SUCCESS, content="\n".join(parts))
        except Exception as e:
            logger.warning(f"[BandTool] 每日概览失败: {e}")
            return ToolResult(status=ToolStatus.ERROR, content="健康概览数据读取失败。", error=str(e))
