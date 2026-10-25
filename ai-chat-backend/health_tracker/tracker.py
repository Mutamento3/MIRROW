"""
健康追踪主协调器

职责：
- 监听状态切换事件
- 触发步数/睡眠数据采集
- 管理缓存
- 提供 band_tool 查询接口
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Any

from .status_log import get_status_log

# 持久化文件（所有健康数据统一存这里，不再有独立文件互相覆盖）
_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "health_cache.json")
# 需要持久化的缓存 key（重启后恢复，前端登录就能看到上次值）
_PERSIST_KEYS = {"steps", "sleep", "spo2", "sleep_last", "outing_steps_diff", "step_tracker"}

logger = logging.getLogger(__name__)

# 缓存 TTL
CACHE_TTL = {
    "steps": 1800,      # 30 分钟
    "sleep": 36000,     # 到次日 10:00 (实际用 check 函数)
    "spo2": 7200,       # 2 小时
    "heart_rate": 300,  # 5 分钟（BLE 优先，缓存仅兜底）
}

# 内存缓存
_cache: Dict[str, Dict[str, Any]] = {}


def _is_cache_valid(key: str) -> bool:
    """检查缓存是否有效。"""
    entry = _cache.get(key)
    if not entry:
        return False
    ts = entry.get("ts")
    if not ts:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
    except (ValueError, TypeError):
        return False

    ttl = CACHE_TTL.get(key, 1800)

    # 睡眠特殊处理：到次日 10:00 前都有效
    if key == "sleep":
        now = datetime.now()
        next_10am = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= next_10am:
            next_10am += timedelta(days=1)
        return now < next_10am

    return age < ttl


def _cache_freshness(key: str) -> Optional[str]:
    """返回缓存新鲜度的可读描述。"""
    entry = _cache.get(key)
    if not entry or not entry.get("ts"):
        return None
    try:
        age_sec = (datetime.now() - datetime.fromisoformat(entry["ts"])).total_seconds()
        if age_sec < 60:
            return "刚刚"
        elif age_sec < 3600:
            return f"{int(age_sec / 60)} 分钟前"
        else:
            return f"{int(age_sec / 3600)} 小时前"
    except Exception:
        return None


def put_cache(key: str, value: Any, ts: str = None):
    """写入缓存（内存 + 磁盘持久化）。"""
    _cache[key] = {
        "value": value,
        "ts": ts or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }
    _persist_cache()
    logger.info(f"[HealthTracker] 缓存更新: {key} = {str(value)[:80]}")


def _persist_cache():
    """将需要持久化的缓存 key 写入 JSON 文件。"""
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        data = {}
        for k in _PERSIST_KEYS:
            if k in _cache:
                data[k] = _cache[k]
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[HealthTracker] 缓存持久化失败: {e}")


def _load_persisted_cache():
    """从文件恢复持久化的缓存（进程重启后调用）。含损坏检测+SQLite回退恢复。"""
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # schema 校验：确保是 dict 且含预期 key
            if not isinstance(data, dict):
                raise ValueError("缓存文件格式错误（非 dict）")
            for k, v in data.items():
                if k in _PERSIST_KEYS:
                    _cache[k] = v
            logger.info(f"[HealthTracker] 从文件恢复缓存: {list(data.keys())}")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[HealthTracker] 缓存文件损坏({e})，尝试从 daily_health_summary 回退恢复...")
        _recover_cache_from_db()
    except Exception as e:
        logger.warning(f"[HealthTracker] 缓存文件读取失败: {e}")
        _recover_cache_from_db()


def _recover_cache_from_db():
    """缓存文件损坏时，从 daily_health_summary 恢复今日步数等关键数据。"""
    try:
        from silicon_perception.health_store import get_store
        today = datetime.now().strftime("%Y-%m-%d")
        hs = get_store()
        summaries = hs.get_daily_summaries(today, today)
        if summaries:
            s = summaries[0]
            if s.get("steps"):
                _cache["steps"] = {"value": s["steps"], "ts": datetime.now().timestamp()}
                logger.info(f"[HealthTracker] 从DB恢复今日步数: {s['steps']}")
            if s.get("sleep_min"):
                _cache["sleep"] = {"value": {"total_sleep_min": s["sleep_min"]}, "ts": datetime.now().timestamp()}
        # step_tracker 无法恢复，下次传感器读取会自动重建
        logger.info("[HealthTracker] DB回退恢复完成")
    except Exception as e:
        logger.warning(f"[HealthTracker] DB回退恢复也失败: {e}，将从零开始")


def get_cache(key: str) -> Optional[Dict[str, Any]]:
    """读取缓存（含新鲜度）。"""
    if not _is_cache_valid(key):
        return None
    entry = _cache[key]
    return {
        "value": entry["value"],
        "ts": entry["ts"],
        "freshness": _cache_freshness(key) or "未知",
        "expired": False
    }


def get_cache_or_stale(key: str) -> Dict[str, Any]:
    """读取缓存，即使过期也返回（标记 expired=True）。"""
    entry = _cache.get(key)
    if not entry:
        return {"value": None, "ts": None, "freshness": None, "expired": True}
    return {
        "value": entry["value"],
        "ts": entry["ts"],
        "freshness": _cache_freshness(key) or "未知",
        "expired": not _is_cache_valid(key)
    }


class HealthTracker:
    """健康追踪协调器（模块级单例）。"""

    def __init__(self):
        self._log = get_status_log()
        # 这些回调由 main.py 注入
        self._chat_injector = None  # 聊天注入回调
        self._step_callback = None  # 步数传感器读取回调 (async () -> Optional[int])
        self._sleep_prompt_callback = None  # 睡眠弹窗推送回调 (async (sleep_type) -> None)

    def set_sleep_prompt_callback(self, callback):
        self._sleep_prompt_callback = callback

    def set_chat_injector(self, injector):
        self._chat_injector = injector

    def set_step_callback(self, callback):
        """设置步数传感器读取回调。callback: async () -> Optional[int] (累计步数)"""
        self._step_callback = callback

    # ─── 传感器读取 ───────────────────────────────────

    async def _read_sensor_now(self) -> Optional[int]:
        """读取当前传感器累计值（不缓存）。"""
        if not self._step_callback:
            return None
        try:
            return await asyncio.wait_for(self._step_callback(), timeout=10.0)
        except Exception:
            return None

    # ─── 状态切换事件 ───────────────────────────────────

    def on_status_change(self, old_status: str, new_status: str, custom_text: str = ""):
        """
        状态切换时调用（同步，不阻塞调用方）。
        - 记录日志
        - 触发相关数据采集（异步 fire-and-forget）
        """
        if old_status == new_status:
            return  # 状态没变，跳过
        self._log.record(old_status, new_status, custom_text)
        logger.info(f"[HealthTracker] 状态切换: {old_status} → {new_status}")

        # 异步触发采集（不阻塞状态切换）
        asyncio.create_task(self._on_status_change_async(old_status, new_status))

    async def _on_status_change_async(self, old_status: str, new_status: str):
        """异步处理状态切换触发。"""
        # 外出触发
        if new_status == "out" and old_status != "out":
            await self._on_outing_start()
        elif old_status == "out" and new_status != "out":
            await self._on_outing_end()

        # 睡眠触发
        if new_status in ("sleeping", "napping") and old_status not in ("sleeping", "napping"):
            await self._on_sleep_start(new_status)
        elif old_status in ("sleeping", "napping") and new_status not in ("sleeping", "napping"):
            await self._on_sleep_end(old_status, new_status)

    async def _on_outing_start(self):
        """外出开始：记录步数起点。"""
        logger.info(f"[HealthTracker:OUT] 外出开始 — step_callback={'已设置' if self._step_callback else '❌未设置'}")
        if self._step_callback:
            try:
                steps = await asyncio.wait_for(self._step_callback(), timeout=10.0)
                if steps is not None:
                    put_cache("outing_start_steps", steps)
                    logger.info(f"[HealthTracker:OUT] ✅ 外出步数起点: {steps}")
                else:
                    logger.warning("[HealthTracker:OUT] ⚠️ 步数传感器返回空（手机未连接或传感器不可用）")
            except asyncio.TimeoutError:
                logger.warning("[HealthTracker:OUT] ⚠️ 外出步数记录超时(10s)")
            except Exception as e:
                logger.warning(f"[HealthTracker:OUT] ⚠️ 外出步数记录失败: {e}")
        else:
            logger.warning("[HealthTracker:OUT] ⚠️ step_callback未设置，外出步数起点未记录")

    async def _on_outing_end(self):
        """外出结束：计算步数差值。"""
        logger.info(f"[HealthTracker:OUT_END] 外出结束 — step_callback={'已设置' if self._step_callback else '❌未设置'}")
        if self._step_callback:
            try:
                end_steps = await asyncio.wait_for(self._step_callback(), timeout=10.0)
                if end_steps is None:
                    logger.warning("[HealthTracker:OUT_END] ⚠️ 步数传感器返回空，差值计算失败")
                    return
                start_entry = _cache.get("outing_start_steps")
                start_steps = start_entry["value"] if start_entry else 0
                diff = max(0, end_steps - start_steps)
                # 累加当天多次外出的步数差值（而非覆盖）
                existing = _cache.get("outing_steps_diff", {})
                prev_diff = existing.get("value", 0) if existing else 0
                put_cache("outing_steps_diff", prev_diff + diff)
                # 写入今日步数（基线减法），而非累计值 — 防止缓存污染
                daily = self._track_steps(end_steps)
                put_cache("steps", daily)
                logger.info(f"[HealthTracker:OUT_END] ✅ 外出步数差值: {diff} (总: {end_steps}, 今日: {daily})")
            except asyncio.TimeoutError:
                logger.warning("[HealthTracker:OUT_END] ⚠️ 外出步数计算超时(10s)")
            except Exception as e:
                logger.warning(f"[HealthTracker:OUT_END] ⚠️ 外出步数计算失败: {e}")
        else:
            logger.warning("[HealthTracker:OUT_END] ⚠️ step_callback未设置，外出步数差值未计算")

    async def _on_sleep_start(self, sleep_type: str):
        """睡眠开始：记录时间。"""
        put_cache("sleep_start", {
            "type": sleep_type,
            "start_ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        })
        logger.info(f"[HealthTracker] 睡眠开始 ({sleep_type})")

    async def _on_sleep_end(self, sleep_type: str, new_status: str):
        """睡眠结束：记录区间 + 大睡觉(sleeping)时推送睡眠弹窗。"""
        logger.info(f"[HealthTracker:SLEEP_END] 睡眠结束: {sleep_type}→{new_status}")
        today = datetime.now().strftime("%Y-%m-%d")
        start_entry = _cache.get("sleep_start")
        if start_entry:
            start_ts = start_entry["value"].get("start_ts")
            put_cache("sleep_last", {
                "type": sleep_type,
                "start_ts": start_ts,
                "end_ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            })

        # 大睡觉(sleeping)结束时推送弹窗——napping 不弹
        if sleep_type == "sleeping" and self._sleep_prompt_callback:
            # 清除旧状态，设置为 pending
            put_cache("sleep_data_status", {"status": "pending", "date": today})
            try:
                await self._sleep_prompt_callback(sleep_type)
            except Exception as e:
                logger.warning(f"[HealthTracker] 睡眠弹窗推送失败: {e}")

    async def trigger_manual_capture(self, data_type: str) -> Dict[str, Any]:
        """手动触发采集（供 API 调用）。"""
        from .sync_manager import get_sync_manager
        sm = get_sync_manager()

        # 步数：传感器差值法（当前累计值 − 今日基线 = 今日步数）
        if data_type == "steps":
            if self._step_callback:
                try:
                    data = await self.get_steps()
                    steps = data.get("steps")
                    if steps is None:
                        return {"success": False, "error": "步数传感器不可用（手机未连接或传感器无数据）。请确认手机已连接且已授权活动识别权限。"}
                    await sm.on_capture_success("steps")
                    return {"success": True, "data": {"steps": steps, "value": steps}, "source": "sensor", "freshness": data.get("freshness")}
                except asyncio.TimeoutError:
                    return {"success": False, "error": "步数传感器读取超时"}
                except Exception as e:
                    return {"success": False, "error": f"步数传感器失败: {e}"}
            return {"success": False, "error": "步数传感器不可用（手机未连接）"}

        # 睡眠：手动上传截图 → GLM-4V 分析（唯一路径）
        if data_type == "sleep":
            # 清除之前的 skip 状态 + 重新推送弹窗
            put_cache("sleep_data_status", {"status": "pending", "date": datetime.now().strftime("%Y-%m-%d")})
            if self._sleep_prompt_callback:
                try:
                    await self._sleep_prompt_callback("sleeping")
                except Exception as e:
                    logger.warning(f"[HealthTracker] 手动采集睡眠弹窗推送失败: {e}")
            return {"success": False, "error": "请在弹出的对话框中上传睡眠报告截图。如未弹出，请检查手机MIRROW是否在前台。"}

    # ─── 步数计算核心（唯一入口，所有路径必须经此计算今日步数）────────

    def _track_steps(self, sensor_value: int) -> int:
        """今日步数 = 当前传感器累计 - 今日起点累计。
        存 (last_sensor, today_date, day_start, reboot_offset)。
        不做增量累加——防双 WS 源值跳变导致漂移（今日>累计的超人 bug）。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        state = _cache.get("step_tracker")
        if state:
            st = state.get("value", {})
            last_sensor = st.get("last_sensor", sensor_value)
            today_date = st.get("today_date", "")
            day_start = st.get("day_start", st.get("last_sensor", sensor_value))
            reboot_offset = st.get("reboot_offset", 0)
        else:
            last_sensor, today_date, day_start, reboot_offset = sensor_value, today, sensor_value, 0

        if today_date != today:
            # 跨天：以当前累计为今日起点
            day_start = sensor_value
            reboot_offset = 0
            today_date = today
        elif sensor_value < last_sensor:
            # 传感器下降 = 手机重启归零 → 把重启前的今日步数固化进 offset，起点重置
            reboot_offset += max(0, last_sensor - day_start)
            day_start = sensor_value

        today_steps = max(0, sensor_value - day_start) + reboot_offset

        put_cache("step_tracker", {"last_sensor": sensor_value, "today_date": today_date,
                                   "day_start": day_start, "reboot_offset": reboot_offset,
                                   "today_steps": today_steps})
        return today_steps

    def sync_today_steps(self, cumulative: int, phone_today: int):
        """手机端权威今日步数 — 仅更新显示值 + last_sensor。

        day_start / reboot_offset 由 _track_steps() 独立维护（含重启检测），
        不在此处反推覆写——避免手机重启后 phone_today=0 或膨胀值覆盖正常缓存。
        """
        # 合理性上限：超过 100K 视为异常（手环终身累计值被误报为今日步数）
        if phone_today > 100000:
            import logging
            logging.getLogger(__name__).warning(
                f"sync_today_steps: 拒绝异常步数 phone_today={phone_today} cumulative={cumulative}"
            )
            return
        today = datetime.now().strftime("%Y-%m-%d")
        # 保留 _track_steps 维护的 day_start / reboot_offset，只更新 today_steps + last_sensor
        state = _cache.get("step_tracker")
        if state:
            st = state.get("value", {})
            day_start = st.get("day_start", cumulative)
            reboot_offset = st.get("reboot_offset", 0)
        else:
            day_start = cumulative
            reboot_offset = 0
        put_cache("step_tracker", {
            "last_sensor": cumulative,
            "today_date": today,
            "day_start": day_start,
            "reboot_offset": reboot_offset,
            "today_steps": phone_today,
        })

    _last_daily_summary_date: str = ""

    def _maybe_write_daily_summary(self, today: str):
        """跨天时把昨天的全量数据刷入 daily_health_summary。
        委托 DailyAggregator 统一聚合（替代旧的内联 SQL）。"""
        if today == self._last_daily_summary_date:
            return
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            from silicon_perception.analysis.daily_aggregator import DailyAggregator
            from silicon_perception.recording.health_store import get_store
            hs = get_store()
            aggregator = DailyAggregator(hs)
            fields = aggregator.aggregate(yesterday)

            # 同步 7 日外出步数
            if not fields.get("avg_outing_steps_7d"):
                try:
                    seven_days_ago = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
                    summaries = hs.get_daily_summaries(seven_days_ago, yesterday)
                    out_vals = [s.get("outing_steps", 0) or 0 for s in summaries if s.get("outing_steps")]
                    if out_vals:
                        hs.upsert_daily_summary(yesterday, avg_outing_steps_7d=round(sum(out_vals) / len(out_vals), 1))
                except Exception:
                    pass

            if fields:
                logger.info(f"[HealthTracker] daily summary 已写入 {yesterday}: {list(fields.keys())}")
            self._last_daily_summary_date = today
        except Exception as e:
            logger.warning(f"[HealthTracker] daily summary 写入失败: {e}")

    # ─── band_tool 查询接口 ──────────────────────────

    async def get_steps(self) -> Dict[str, Any]:
        """获取今日步数（增量追踪，手机重启自动适配）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        freshness = "未知"
        daily_steps = 0

        sensor_now = await self._read_sensor_now()
        if sensor_now is not None:
            daily_steps = self._track_steps(sensor_now)
            put_cache("steps", daily_steps)
            freshness = _cache_freshness("steps") or "传感器"
            expired = not _is_cache_valid("steps")
            self._maybe_write_daily_summary(today)
        else:
            # 传感器不可用 → 旧缓存
            cached = get_cache_or_stale("steps")
            daily_steps = cached["value"]
            freshness = cached.get("freshness", "未知")
            expired = cached.get("expired", True)

        outing_diff = _cache.get("outing_steps_diff", {}).get("value", 0)

        result = {
            "steps": daily_steps,
            "freshness": freshness,
            "expired": expired,
            "outing_diff": outing_diff,
            "home_steps": max(0, (daily_steps or 0) - (outing_diff or 0)),
        }

        # 从状态日志计算今日外出时长
        transitions = self._log.get_log(today)
        outing_minutes = self._calc_outing_duration(transitions)
        result["outing_minutes"] = outing_minutes

        # 判断工作日/休息日
        import calendar as _cal
        weekday = datetime.now().weekday()
        result["day_type"] = "休息日" if weekday >= 5 else "工作日"
        result["weekday"] = ["一", "二", "三", "四", "五", "六", "日"][weekday]

        # 近7日统计
        try:
            from silicon_perception.health_store import get_store
            hs = get_store()
            seven_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            summaries = hs.get_daily_summaries(seven_ago, yesterday)
            if summaries:
                steps_vals = [s.get("steps", 0) or 0 for s in summaries]
                result["avg_steps_7d"] = round(sum(steps_vals) / len(steps_vals), 1) if steps_vals else 0
                out_vals = [s.get("outing_steps", 0) or 0 for s in summaries]
                result["avg_outing_steps_7d"] = round(sum(out_vals) / len(out_vals), 1) if out_vals else 0
        except Exception:
            result["avg_steps_7d"] = 0
            result["avg_outing_steps_7d"] = 0

        return result

    async def get_sleep(self) -> Dict[str, Any]:
        """获取睡眠数据。"""
        sleep_last = _cache.get("sleep_last", {}).get("value")
        cached = get_cache_or_stale("sleep")
        return {
            "last_sleep": sleep_last,
            "data": cached["value"],
            "freshness": cached["freshness"],
            "expired": cached["expired"],
        }

    @staticmethod
    def _calc_outing_duration(transitions: list) -> int:
        """从状态日志计算今日外出总时长（分钟）。"""
        total_minutes = 0
        out_start = None
        for t in transitions:
            from_s = t.get("from_status", "")
            to_s = t.get("to_status", "")
            ts = t.get("timestamp", "")

            if to_s == "out" and from_s != "out":
                out_start = ts
            elif from_s == "out" and to_s != "out" and out_start:
                try:
                    start_dt = datetime.fromisoformat(out_start)
                    end_dt = datetime.fromisoformat(ts)
                    total_minutes += int((end_dt - start_dt).total_seconds() / 60)
                except Exception:
                    pass
                out_start = None

        # 如果当前仍在外面
        if out_start:
            try:
                start_dt = datetime.fromisoformat(out_start)
                total_minutes += int((datetime.now() - start_dt).total_seconds() / 60)
            except Exception:
                pass

        return total_minutes


# 模块级单例
# 模块加载时恢复持久化缓存
_load_persisted_cache()

_tracker_instance: Optional[HealthTracker] = None


def get_health_tracker() -> HealthTracker:
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = HealthTracker()
    return _tracker_instance
