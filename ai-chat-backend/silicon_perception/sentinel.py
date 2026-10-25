"""硅基哨兵主协调器

启动/停止监控循环，协调数据源采集 → 存储 → 异常检测 → 推送。
作为独立模块运行，不受 Wander 的 idle 限制。
"""

import asyncio
from body_admission import ordinary
import logging
from .delivery import deliver_push, new_chat_since, PushSuperseded
from .location_places import snapshot_location_label
import time
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any, List

from .collection import (
    DataSource, DataPoint,
    HeartRateSource, ScreenActivitySource,
    MirrowVisibilitySource, UserStatusSource, PeriodSource,
    InputIdleSource, StepCountSource, GpsSource, ScreenTimeSource,
    CurrentAppSource, WeatherSource, WifiSource, NotificationsSource, RunningProcessesSource,
    RemoteSessionSource, SensorContextSource,
)
from .analysis.anomaly_detector import AnomalyDetector, Priority, DataSnapshot
from .analysis.sentinel_event_history import format_push_audit
from .recording.health_store import HealthStore, get_store
from .recording.schema import migrate as migrate_schema
from truncation_config import get_truncation_limit
from api_sources.scope import routing_scope

logger = logging.getLogger(__name__)

# 全局单例
_sentinel: Optional["SiliconSentinel"] = None
SENTINEL_AVAILABLE = True


def init_sentinel(
    on_push: Optional[Callable[[Dict[str, Any]], Any]] = None,
    tick_interval: int = 60,
    enable_llm_polish: bool = False,
    call_llm_func: Optional[Callable] = None,
    call_pro_llm: Optional[Callable] = None,
    call_behavior_llm_func: Optional[Callable] = None,
) -> "SiliconSentinel":
    global _sentinel
    _sentinel = SiliconSentinel(
        on_push=on_push,
        tick_interval=tick_interval,
        enable_llm_polish=enable_llm_polish,
        call_llm_func=call_llm_func,
        call_pro_llm=call_pro_llm,
        call_behavior_llm_func=call_behavior_llm_func,
    )
    return _sentinel


def get_sentinel() -> Optional["SiliconSentinel"]:
    return _sentinel


class SiliconSentinel:
    """硅基数据控制模块 — AI 的哨兵"""

    def __init__(
        self,
        on_push: Optional[Callable[[Dict[str, Any]], Any]] = None,
        tick_interval: int = 60,
        enable_llm_polish: bool = False,
        call_llm_func: Optional[Callable] = None,
        call_pro_llm: Optional[Callable] = None,
        call_behavior_llm_func: Optional[Callable] = None,
    ):
        self._on_push = on_push  # async (payload) -> bool，True 仅表示权威消息落盘
        self._tick_interval = tick_interval
        self._call_pro_llm = call_pro_llm  # 消息 Flash（接收结构化 PUSH 决定，生成自然消息）
        self._enable_llm_polish = enable_llm_polish
        self._call_llm = call_llm_func
        self._call_behavior_llm = call_behavior_llm_func

        self._store = get_store()
        self._detector = AnomalyDetector(health_store=self._store)
        self._sleep_learner = None     # SleepWindowLearner(self._store)
        self._annotator = None         # Phase 3: ContextAnnotator(self._store)

        self._last_flash_context: Optional[dict] = None  # 最近一次 Flash [PUSH] 决策上下文
        self._sources: List[DataSource] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._mobile_hr_task: Optional[asyncio.Task] = None
        self._tick_count = 0
        self._tick_lock = asyncio.Lock()  # 防并发：_monitor_loop 与 /refresh 互斥
        self._deferred_push: Dict[str, dict] = {}  # trigger_key → {clean_text, triggers, created_at}
        self._current_pc_exe: Optional[str] = None     # 当前 PC session app
        self._current_phone_pkg: Optional[str] = None   # 当前手机 session app
        self._pc_session_start: Optional[str] = None
        self._phone_session_start: Optional[str] = None
        # 从 settings.json 恢复开关状态（重启不丢失）
        try:
            from settings_manager import get_setting
            sentinel_cfg = get_setting("silicon_perception") or {}
        except Exception:
            sentinel_cfg = {}
        self._push_defer_seconds: int = sentinel_cfg.get("push_defer_seconds", 120)  # 延迟推送阈值
        self._enabled = sentinel_cfg.get("enabled", True)
        self._gps_enabled = sentinel_cfg.get("gps_enabled", True)
        self._mobile_hr_error = None  # 最近一次手机 BLE 错误消息
        self._tick_interval = tick_interval  # 基础间隔
        self._hr_source: Optional[HeartRateSource] = None  # 在 _init_sources 中设置

        self._init_sources()

    # ── 数据源初始化 ──────────────────────────────────

    def _init_sources(self):
        hr_src = HeartRateSource()
        self._hr_source = hr_src
        self._step_source = StepCountSource()
        self._gps_source = GpsSource()
        self._wifi_source = WifiSource()
        self._current_app_source = CurrentAppSource()
        self._last_snapshot: Optional[DataSnapshot] = None  # 缓存最新快照，供 API 读取
        # 从 health_snapshots 恢复最近 GPS 快照，防止重启后 tick 跳过窗口内
        # _build_snapshot 写入 NULL GPS → 覆盖 DB 中最后的好数据 → 再重启恢复失败
        try:
            conn = self._store._get_conn()
            row = conn.execute(
                "SELECT location_lat, location_lng, location_address, location_category, timestamp "
                "FROM health_snapshots WHERE location_lat IS NOT NULL "
                "ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                stub = DataSnapshot()
                stub.location_lat = row["location_lat"]
                stub.location_lng = row["location_lng"]
                stub.location_address = row["location_address"]
                stub.location_category = row["location_category"]
                if row["timestamp"]:
                    stub.field_ts["gps"] = row["timestamp"]
                self._last_snapshot = stub
                logger.info(
                    f"Sentinel: 从 DB 恢复上次 GPS 快照 "
                    f"({stub.location_category or '?'})"
                )
        except Exception:
            pass  # DB 不可用或首次启动无 GPS 数据，_last_snapshot 保持 None
        self._weather_source = WeatherSource()
        self._sources = [
            hr_src,
            ScreenActivitySource(),
            InputIdleSource(),
            MirrowVisibilitySource(),
            UserStatusSource(),
            PeriodSource(),
            self._step_source,
            self._wifi_source,
            # WiFi 先于 GPS：本 tick 已确认在家时，GPS 完全不采集。
            self._gps_source,
            ScreenTimeSource(),
            self._current_app_source,
            NotificationsSource(),   # 通知事件刷新 + 1min 兜底；只存「包名×数量」
            RunningProcessesSource(),  # PC 运行进程（5min，游戏/编码/应用分类，排除系统黑名单）
            RemoteSessionSource(),   # 远程控制软件能力标注；活动输入由 Raw Input 来源识别
            SensorContextSource(),   # 手机姿态/运动/光线（1min，AionsHome 移植 2026-08-24）
            self._weather_source,
        ]
        logger.info(f"Sentinel: {len(self._sources)} 个数据源已注册")

    # ── 启停 ──────────────────────────────────────────

    async def start(self):
        """启动监控循环"""
        if self._running:
            return
        self._running = True

        # 启动所有数据源
        for src in self._sources:
            try:
                await src.start()
            except Exception as e:
                logger.warning(f"Sentinel: 数据源 {src.name} 启动失败: {e}")

        self._task = asyncio.create_task(self._monitor_loop())
        self._mobile_hr_task = asyncio.create_task(self._mobile_hr_loop())

        # 执行 schema 迁移（安全幂等）
        try:
            applied = migrate_schema(self._store._get_conn())
            if applied:
                logger.info(f"Sentinel: schema 迁移完成: {applied}")
        except Exception as e:
            logger.warning(f"Sentinel: schema 迁移失败: {e}")

        # 延迟初始化分析/告警模块（避免循环导入）
        try:
            from .analysis.sleep_window import SleepWindowLearner
            from .analysis.context_annotator import ContextAnnotator
            self._sleep_learner = SleepWindowLearner(self._store)
            self._annotator = ContextAnnotator(self._store)
            from .analysis.sentinel_summarizer import SentinelSummarizer
            from .analysis.trigger_detector import TriggerDetector
            self._summarizer = SentinelSummarizer(flash_llm_func=self._call_llm)
            self._trigger_detector = TriggerDetector()
        except Exception as e:
            logger.warning(f"Sentinel: 分析模块初始化失败: {e}")

        logger.info(f"Sentinel: 监控循环已启动 (tick={self._tick_interval}s, mobile_hr=300s)")

    async def stop(self):
        """停止监控循环"""
        self._running = False
        if self._task:
            self._task.cancel()
        if self._mobile_hr_task:
            self._mobile_hr_task.cancel()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        for src in self._sources:
            try:
                await src.stop()
            except Exception:
                pass

        # store.close() 在 shutdown 时由 main.py 统一调用，不在这里关闭
        # 避免测试/重启场景下连接被过早关闭
        logger.info("Sentinel: 已停止")

    # ── 手机心率后台轮询 ──────────────────────────────

    # 手机 BLE 并发锁（_mobile_hr_loop 与 band_tool 回退互斥）
    _mobile_hr_lock = asyncio.Lock()

    async def _mobile_hr_loop(self):
        """PC 连不上手环时，由手机辅助采集心率。判断顺序：
        ① PC 5min 无数据 → 手机接管（不再要求外出/开关，自动兜底）
        ② relay BLE 直连
        """
        MOBILE_HR_INTERVAL = 300  # 5 分钟
        await asyncio.sleep(10)  # 启动后等 10s 让 WS 就绪

        while self._running:
            try:
                # ① PC BLE 最近有数据 → 跳过（PC 永远优先）
                pc_stats = self._store.get_hr_stats_recent(minutes=5)
                if pc_stats["count"] > 0:
                    await asyncio.sleep(30)
                    continue

                # ② 手机 BLE 直连
                try:
                    from shared_state import get_mobile_relay_callback as _get_relay
                    _relay = _get_relay()
                except ImportError:
                    _relay = None
                if not _relay:
                    await asyncio.sleep(30)
                    continue

                async with self._mobile_hr_lock:  # 与 band_tool 回退互斥
                    import uuid as _uid
                    from silicon_perception.collection.heart_rate import resolve_hr_device_mac as _resolve_mac
                    hr = None
                    try:
                        ble_result = await asyncio.wait_for(
                            _relay(str(_uid.uuid4()), "mobile_heart_rate", {"mac": _resolve_mac() or ""}),
                            timeout=15.0
                        )
                        if isinstance(ble_result, dict) and ble_result.get("success") and ble_result.get("data"):
                            hr = ble_result["data"].get("heart_rate")
                            self._mobile_hr_error = None
                        elif isinstance(ble_result, dict) and not ble_result.get("success"):
                            err = ble_result.get("content", str(ble_result))
                            self._mobile_hr_error = err
                            logger.debug(f"[MobileHR] 采集失败: {err[:120]}")
                    except asyncio.TimeoutError:
                        self._mobile_hr_error = "手机 BLE 超时(15s)"
                    except Exception as e:
                        self._mobile_hr_error = f"手机 BLE 异常: {str(e)[:100]}"

                    if hr is not None:
                        try:
                            measured_at = datetime.now().isoformat()
                            self._store.insert_snapshot(heart_rate=hr, heart_rate_source="mobile_ble", heart_rate_measured_at=measured_at)
                            logger.info(f"[MobileHR] 外出自动采集: {hr} bpm")
                            # 同步 field_ts，让 is_stale("heart_rate") 感知这次采集时刻
                            # （否则 mobile 兜底采的心率因绕过 _build_snapshot 而无时效戳）
                            if self._last_snapshot is not None:
                                self._last_snapshot.heart_rate = hr
                                self._last_snapshot.heart_rate_source = "mobile_ble"
                                self._last_snapshot.field_ts["heart_rate"] = measured_at
                        except Exception as e:
                            logger.warning(f"[MobileHR] 写入失败: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[MobileHR] 循环异常: {e}")

            await asyncio.sleep(MOBILE_HR_INTERVAL)

    # ── 动态轮询间隔 ──────────────────────────────────

    def _get_tick_interval(self) -> int:
        """根据当前场景返回应使用的轮询间隔"""
        # 深夜模式 + HR 启用 → 高频采集
        night_active = self._is_night_mode_active()
        if night_active:
            return 15
        return self._tick_interval  # 默认 60s

    @staticmethod
    def _is_night_mode_active() -> bool:
        try:
            from shared_state import get_night_mode_active
            return get_night_mode_active()
        except Exception:
            return False

    @staticmethod
    def _get_last_message_seconds() -> float:
        """距离用户最后一条消息的秒数（查 SQL，私聊+群聊取最新）。"""
        try:
            from silicon_perception.analysis.behavior_profile import get_behavior_profile
            return get_behavior_profile().get_last_user_message_seconds()
        except Exception:
            return 999999.0

    async def _adaptive_sleep(self, base_interval: int):
        """分片 sleep，检测到间隔变化时提前退出"""
        chunk = 5
        elapsed = 0
        while elapsed < base_interval and self._running:
            await asyncio.sleep(min(chunk, base_interval - elapsed))
            elapsed += chunk
            new_interval = self._get_tick_interval()
            if new_interval != base_interval:
                return

    def _trust_out_status(self) -> bool:
        """是否信任当前 'out' 状态（决定是否跳过 HR 采集）。
        不信任的情况（照常采 HR，防 stale 'out' 关掉心率管道）：
        - 状态已声明 >8h（可能忘记改回）
        - PC 有在场信号矛盾（键鼠活跃 <5min 或 MIRROW 前台可见）
        """
        OUT_TRUST_HOURS = 8
        try:
            from shared_state import get_current_user_status_ts
            ts = get_current_user_status_ts()
            if ts:
                age_h = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                if age_h > OUT_TRUST_HOURS:
                    return False  # 太久没更新，不信任
            snap = self._last_snapshot
            if snap:
                if snap.input_idle_seconds is not None and snap.input_idle_seconds < 300:
                    return False  # 键鼠 5min 内活跃 → 人在 PC 前，不是真外出
                if snap.mirrow_visible:
                    return False  # MIRROW 前台可见 → 人在
                if snap.wifi_is_home:
                    return False  # WiFi 连着家 SSID → 人在家，out 一定是 stale
        except Exception:
            pass
        return True  # 默认信任（近期声明 + 无在场矛盾）

    def _get_behavior_mode(self) -> str:
        """获取当前行为模式（替代旧的三元 hack）。

        优先从 StatusMeta.behavior_mode 读取（含 eating 在家/外出区分）；
        StatusMeta 不可用时回退旧逻辑。
        """
        try:
            from wander_manager.user_status import get_status_meta
            meta = get_status_meta()
            if meta:
                return meta.behavior_mode
        except Exception:
            pass
        # 回退：旧三元逻辑（StatusMeta 不可用时）
        if self._last_snapshot and self._last_snapshot.user_status:
            us = self._last_snapshot.user_status
            if us in ('sleeping', 'napping', 'bathing'):
                return "rest"
            if us == 'eating':
                return "rest"  # 无 location 信息时保守：默认在家吃=rest
            if us == 'out':
                return "away"
        return "active" if self._last_snapshot else "unknown"

    async def _monitor_loop(self):
        """主监控循环 — 每 tick 采集一次全部数据源，自适应间隔"""
        while self._running:
            interval = self._get_tick_interval()
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sentinel tick 异常: {e}")

            await self._adaptive_sleep(interval)

    async def _tick(self, force: bool = False):
        """一次监控周期。force=True 跳过所有冷却和门控。"""
        async with self._tick_lock:
            return await self._tick_inner(force)

    @staticmethod
    def _wifi_home_in_datapoints(datapoints: Dict[str, DataPoint]) -> bool:
        """True only when this tick has a fresh, confirmed home-WiFi fact."""
        point = datapoints.get("wifi_state")
        if point is None or point.data.get("wifi_is_home") is not True or not point.captured_at:
            return False
        try:
            return (datetime.now() - datetime.fromisoformat(point.captured_at)).total_seconds() <= 300
        except (TypeError, ValueError):
            return False

    @routing_scope
    async def _tick_inner(self, force: bool = False):
        """_tick 的实际逻辑（由 _tick_lock 保护）"""
        if not self._enabled and not force:
            return

        # 深夜模式：哨兵整体暂停（force 可跳过）
        if self._is_night_mode_active() and not force:
            return

        self._tick_count += 1
        # 检查延迟推送队列
        await self._check_deferred_push()
        tick_start = time.monotonic()

        # 1. 采集数据源（按各自的 tick_interval，HR 有额外门控）
        datapoints: Dict[str, DataPoint] = {}
        for src in self._sources:
            # 家庭 WiFi 是位置切换权威；确认在家时不请求 GPS，也不消耗定位/API。
            if src.name == "gps_location" and self._wifi_home_in_datapoints(datapoints):
                continue

            # tick_interval: 非每 tick 都采集的源，按周期跳过（force 强制全部采集）
            ti = getattr(src, 'tick_interval', 1)
            if not force and self._tick_count % max(1, ti) != 0:
                continue

            # HR 门控：真外出才跳过（手机 relay 兜底）。
            # 防 stale "out"：状态>8h 或被 PC 在场信号矛盾时，不信任 "out"，照常采 HR。
            if src.name == "heart_rate":
                try:
                    from wander_manager.user_status import get_user_status
                    status = get_user_status()
                    if status.value == "out" and self._trust_out_status():
                        continue
                except Exception:
                    pass
            # GPS 门控：手动关闭 → 跳过
            if src.name == "gps_location":
                if not self._gps_enabled:
                    continue
            try:
                datapoints[src.name] = await src.read()
            except Exception as e:
                logger.warning(f"Sentinel: 数据源 {src.name} 异常: {e}")

        # 2. 聚合为 DataSnapshot，缓存供 API 读取
        previous_snapshot = self._last_snapshot
        snapshot = self._build_snapshot(datapoints)
        self._last_snapshot = snapshot

        # 3. 写入存储
        behavior_state_val = _infer_behavior_state(snapshot)
        snapshot.behavior_state = behavior_state_val
        from neuron_registry import record_neuron_execution
        record_neuron_execution("behavior_state_inferrer", status="ok" if behavior_state_val != "unknown" else "degraded",
                                output_preview=behavior_state_val[:100])
        # L1.5: 先用本 tick 的真实状态更新会话，再生成展示文本。
        # 旧顺序先读缓存、后更新，active_app_session 会永远慢一个 tick。
        self._track_app_sessions(snapshot)
        parts = []
        if self._current_pc_exe and self._pc_session_start:
            dur = int((datetime.now() - datetime.fromisoformat(self._pc_session_start)).total_seconds() // 60)
            parts.append(f"{self._current_pc_exe}({dur}min)")
        if self._current_phone_pkg and self._phone_session_start:
            dur = int((datetime.now() - datetime.fromisoformat(self._phone_session_start)).total_seconds() // 60)
            name = getattr(snapshot, 'mobile_app_name', self._current_phone_pkg) or self._current_phone_pkg
            parts.append(f"{name}({dur}min)")
        snapshot.active_app_session = " + ".join(parts) if parts else None
        snap_id = self._store.insert_snapshot(
            heart_rate=snapshot.heart_rate,
            heart_rate_source=snapshot.heart_rate_source,
            heart_rate_measured_at=snapshot.field_ts.get("heart_rate") if snapshot.heart_rate is not None else None,
            screen_active=snapshot.screen_active,
            foreground_app=snapshot.foreground_app,
            mirrow_visible=snapshot.mirrow_visible,
            user_status=snapshot.user_status,
            is_period=snapshot.is_period,
            period_day=snapshot.period_day,
            input_idle_seconds=snapshot.input_idle_seconds,
            remote_input_idle_seconds=getattr(snapshot, "remote_input_idle_seconds", None),
            input_origin_available=getattr(snapshot, "input_origin_available", None),
            last_input_origin=getattr(snapshot, "last_input_origin", None),
            cumulative_steps=snapshot.cumulative_steps,
            steps_today=snapshot.steps_today,
            steps_logical_date=snapshot.steps_logical_date,
            location_lat=snapshot.location_lat,
            location_lng=snapshot.location_lng,
            location_address=snapshot.location_address,
            location_category=snapshot.location_category,
            behavior_state=behavior_state_val,
            active_app_session=snapshot.active_app_session,
            screen_time_minutes=snapshot.screen_time_minutes,
            top_app_category=snapshot.top_app_category,
            notifications_summary=getattr(snapshot, 'notifications_summary', None),
            phone_sensor=_phone_sensor_json(snapshot),
        )

        # 3.5 行为基准检查：对比基线，生成偏离信号
        baseline_result = None
        try:
            from silicon_perception.analysis.behavior_profile import get_behavior_profile
            bp = get_behavior_profile()
            if bp:
                baseline_result = bp.check(snapshot, snapshot.last_message_seconds)
        except Exception as e:
            logger.debug(f"Sentinel: 行为基准检查跳过: {e}")

        # 3.6 状态引擎：仲裁 presence/activity → 100% 铁证才自动回写 user_status
        try:
            self._run_state_engine(snapshot)
        except Exception as e:
            logger.debug(f"Sentinel: 状态引擎跳过: {e}")

        # 3.7 跨源事实对照：仍挂在同一 DataSnapshot 上，只整理带时效的
        # 并列事实，不改状态、不下结论、不直接触发任何工具。
        try:
            from silicon_perception.analysis.cross_source_context import (
                build_cross_source_observations,
            )
            snapshot.semantic_observations = build_cross_source_observations(
                snapshot,
                previous_snapshot=previous_snapshot,
            )
        except Exception as e:
            snapshot.semantic_observations = []
            logger.warning(f"Sentinel: 跨源事实对照失败: {e}")

        # 4. 异常检测（注入基线变量）
        if baseline_result and baseline_result.deviations:
            try:
                # 提取心率/步数基线变量注入 anomaly_detector eval 命名空间
                hr_data = bp._cache.get("heart_rate", {})
                steps_data = bp._cache.get("steps", {})
                baseline_vars = {
                    "resting_hr": hr_data.get("resting_mean", 65),
                    "hr_baseline_mean": hr_data.get("all", {}).get("mean", 0),
                    "hr_baseline_stddev": hr_data.get("all", {}).get("stddev", 10),
                    "steps_baseline_mean": steps_data.get("overall_mean", 0),
                    "is_cold_start": baseline_result.cold_start,
                }
                self._detector.set_baseline_context(baseline_vars)
            except Exception:
                pass

        alerts = await self._detector.evaluate(snapshot)

        # 5. 处理告警（C1/C2 救命规则直接推送，绕过 Flash）
        for alert in alerts:
            await self._handle_alert(alert, snap_id)

        # 7. 作息窗口学习（每周一次）
        if self._sleep_learner and self._tick_count % 10080 == 0:  # ~每周
            try:
                if self._sleep_learner.should_update():
                    self._sleep_learner.learn()
            except Exception as e:
                logger.debug(f"Sentinel: 作息学习异常: {e}")

        # 8. Flash 触发器（L1 快照对比 + L3 保底）
        if self._summarizer and self._trigger_detector:
            hr_stats = self._store.get_hr_stats_recent(minutes=5) if self._store else {}
            triggers = self._trigger_detector.evaluate(snapshot, hr_stats)
            # 状态回写是 WiFi 回家的派生结果；同一 tick 只保留物理变化作为注意焦点，
            # 避免“回家”被泛化的长时间沉默信号淹没。
            if "wifi_home_arrival" in triggers and "status_change_silent" in triggers:
                triggers = [item for item in triggers if item != "status_change_silent"]
            if triggers:
                try:
                    today = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
                    events_today = [dict(r) for r in self._store._get_conn().execute(
                        "SELECT * FROM sentinel_events WHERE date(timestamp) = ? AND pushed = 1 ORDER BY timestamp DESC LIMIT 10",
                        (today,)
                    ).fetchall()]
                    # 过滤已解除的阈值类事件：当前传感器值已恢复正常 → 不给 Flash 看
                    trends = self._store.get_weekly_comparison() if self._store else {}
                    # 构建传感器时效信息
                    source_freshness = _build_source_freshness(datapoints, snapshot)
                    # 构建上下文信息（时间 + 状态时间线 + 作息 + 前台应用）
                    current_time, status_timeline, schedule_text = _build_tick_context(snapshot)
                    foreground_app = snapshot.foreground_app or ""
                    baseline_text = baseline_result.text if baseline_result else ""
                    # The judgment layer needs AI's current subjective state and
                    # active-day scene context, not just sensor facts. Keep
                    # the two sources explicitly separate: Affect is AI-owned
                    # and scene material is complete, source-backed context.
                    affect_self = ""
                    affect_resonance = ""
                    try:
                        from affect import get_affect_service
                        affect_service = get_affect_service()
                        affect_self = affect_service.render_self(cap=700)
                        affect_resonance = affect_service.render_resonance(cap=700)
                    except Exception as exc:
                        logger.debug("Sentinel: Affect context unavailable: %s", type(exc).__name__)

                    recent_conversation = ""
                    conversation_evidence = {}
                    try:
                        from shared_state import get_active_session_id
                        from context_builder.sentinel_scene_context import build_sentinel_scene_package
                        session_id = get_active_session_id()
                        scene_package = await build_sentinel_scene_package(session_id)
                        recent_conversation = scene_package['text']
                        conversation_evidence = scene_package['evidence_index']
                    except Exception as exc:
                        logger.debug("Sentinel: recent conversation unavailable: %s", type(exc).__name__)

                    await self._summarizer.update(snapshot, events_today, trends, snapshot.last_message_seconds,
                                                   triggers=triggers, source_freshness=source_freshness,
                                                   current_time=current_time, status_timeline=status_timeline,
                                                   schedule_text=schedule_text, foreground_app=foreground_app,
                                                   baseline_context=baseline_text,
                                                   affect_self=affect_self,
                                                   affect_resonance=affect_resonance,
                                                   recent_conversation=recent_conversation,
                                                   conversation_evidence=conversation_evidence)
                    # [PUSH] 标记：Flash 判断该主动说话 → 活跃门控 → Pro 生成 → 推送
                    decision = self._summarizer.get_decision()
                    summary = decision.observation or self._summarizer.get_summary()
                    # 记录 Flash 决策。推送成功由 flash_push/pattern 记录——此处仅记录未推送的（被门控/冷却/Flash判否）。
                    # pattern 优先，不重复写 info。（人类伙伴：sentinel_events 同一次评估不应有 info+pattern 两条）
                    has_push = decision.should_push
                    self._trigger_detector.record_flash_decision(snapshot, triggers, has_push)
                    actually_pushed = False
                    clean = summary.replace("[PUSH]", "").strip()
                    evaluation_at = __import__('datetime').datetime.now().isoformat()
                    # Keep the latest evaluation even when Flash says no or a
                    # delivery gate delays it. Diagnostics never feeds back
                    # into the next decision.
                    self._last_flash_context = {
                        "timestamp": evaluation_at,
                        "summary": clean,
                        "should_push": has_push,
                        "push_motivation": decision.push_motivation,
                        "grounding_rejections": getattr(decision, "grounding_rejections", []),
                        "fact_contract": self._summarizer.get_fact_contract().to_dict(),
                        "triggers": triggers,
                        "baseline_context": baseline_text if baseline_result else "",
                        "sensor_snapshot": {
                            "hr": snapshot.heart_rate,
                            "steps": snapshot.steps_today,
                            "idle": f"{snapshot.input_idle_seconds:.0f}s" if snapshot.input_idle_seconds is not None else "N/A",
                            "screen": snapshot.screen_active,
                            "location": snapshot.location_category or "N/A",
                        },
                        "trends": trends,
                        "pushed": False,
                        "delivery_state": "pending" if has_push else "not_requested",
                    }
                    if has_push and self._on_push and self._call_pro_llm:
                        decision_payload = decision.to_dict()
                        decision_payload["observation"] = clean
                        decision_payload["user_context"] = self._capture_push_current_state()
                        # Freeze the exact typed fact contract used by the
                        # first Flash layer. The second layer must validate the
                        # same facts, otherwise a delayed push can resurrect an
                        # old location or step count.
                        decision_payload["fact_contract"] = self._summarizer.get_fact_contract().to_dict()
                        if self._should_suppress_push(snapshot) and clean:
                            # 正在对话 → 延迟推送。2min 后若仍未回复 → 补推；若回复了 → 入上下文
                            from .analysis.location_episode import deferred_key
                            key = deferred_key(triggers)
                            self._deferred_push[key] = {
                                "clean": clean,
                                "triggers": triggers,
                                "decision": decision_payload,
                                "at": time.monotonic(),
                                "queued_at": time.time(),
                                "evaluation_at": evaluation_at,
                            }
                            self._last_flash_context["delivery_state"] = "deferred"
                            logger.debug(f"Sentinel: 延迟推送 ({key})")
                        elif clean:
                            # 是否主动联系由本 tick 的 Flash 决定。不同感知事件之间不设全局
                            # 30 分钟总闸；技术性重复由传感器去抖、transition 序号和事件幂等负责。
                            clean = summary.replace("[PUSH]", "").strip()
                            if clean:
                                try:
                                    pro_result = await self._generate_guarded_push(decision_payload)
                                    content = pro_result.get("content", "") if isinstance(pro_result, dict) else str(pro_result)
                                    reasoning = pro_result.get("reasoning") if isinstance(pro_result, dict) else None
                                    if content:
                                        # 推送（含思考过程）
                                        actually_pushed = await deliver_push(self._on_push, {
                                            "message": content, "rule_id": "flash_push",
                                            "generation_started_at": pro_result.get("generation_started_at"),
                                            "priority": "medium",
                                            "timestamp": __import__('datetime').datetime.now().isoformat(),
                                            "reasoning": reasoning,
                                            "model_source": pro_result.get("model_source") if isinstance(pro_result, dict) else None,
                                            "session_id": pro_result.get("session_id"),
                                            "expression_requests": pro_result.get("expression_requests", []),
                                            "band_touch_request": pro_result.get("band_touch_request")
                                            if isinstance(pro_result, dict) else None,
                                            "band_touch_parse_error": pro_result.get("band_touch_parse_error", "")
                                            if isinstance(pro_result, dict) else "",
                                            "checkup_request": pro_result.get("checkup_request")
                                            if isinstance(pro_result, dict) else None,
                                            "checkup_parse_error": pro_result.get("checkup_parse_error", "")
                                            if isinstance(pro_result, dict) else "",
                                            "grounding_rejections": pro_result.get("grounding_rejections", [])
                                            if isinstance(pro_result, dict) else [],
                                        }, store=self._store, event={
                                            "rule_id": "flash_push", "priority": "pattern",
                                            "message": format_push_audit(clean, decision.push_motivation, triggers),
                                            "event_type": "pattern",
                                        }, context=self._last_flash_context)
                                        logger.info("Perception delivery confirmed=%s", actually_pushed)
                                    else:
                                        self._last_flash_context["delivery_state"] = "generation_empty"
                                except PushSuperseded:
                                    self._last_flash_context["delivery_state"] = "cancelled_by_new_chat_or_gate"
                                except Exception as e:
                                    self._last_flash_context["delivery_state"] = "generation_failed"
                                    logger.warning(f"Perception Pro call failed: {e}")
                    elif has_push:
                        self._last_flash_context["delivery_state"] = "delivery_unavailable"
                    # 记录 Flash 决策（仅未推送时——推送成功由 flash_push/pattern 记录）
                    if not actually_pushed:
                        self._store.insert_event(
                            rule_id="flash_judge", priority="info",
                            message=(
                                f"triggers={triggers} push={has_push} | {summary[:200]}"
                                f" | motivation={decision.push_motivation[:160]}"
                            ),
                            pushed=False, event_type="flash_judge",
                        )
                except Exception as e:
                    # 保留完整栈 + 本次触发条件：`unhashable type: 'dict'` 这类
                    # 异常只打印 str(e) 无法定位到行，等于把证据丢掉。
                    logger.warning(
                        "Flash pipeline failed (triggers=%s): %s: %s",
                        triggers, type(e).__name__, e, exc_info=True,
                    )

        # 9. 设备行为链（每 30 tick ≈ 30min 刷新）
        if self._tick_count % 30 == 0 and self._call_llm:
            asyncio.create_task(self.build_behavior_chain())

        # 10. 定期清理（每 100 tick ≈ 100 分钟）
        if self._tick_count % 100 == 0:
            try:
                self._store.cleanup_old_data()
            except Exception:
                pass

        tick_ms = (time.monotonic() - tick_start) * 1000
        if self._tick_count <= 3 or self._tick_count % 30 == 0:
            hr_str = str(snapshot.heart_rate) if snapshot.heart_rate else "N/A"
            iv = self._get_tick_interval()
            logger.info(f"Sentinel tick #{self._tick_count}: HR={hr_str}, alerts={len(alerts)}, interval={iv}s, {tick_ms:.0f}ms")

    # ── App 会话 + 设备行为链 ──────────────────────────

    async def build_behavior_chain(self, target_date: str = None) -> Optional[str]:
        """独立服务负责来源覆盖、单飞刷新及失败时的无损投影。"""
        if not self._call_behavior_llm:
            return None
        from .analysis.behavior_chain import BehaviorChain
        if not getattr(self, "_behavior_chain_service", None):
            self._behavior_chain_service = BehaviorChain(self._store, self._call_behavior_llm)
        try:
            return await self._behavior_chain_service.refresh(target_date or datetime.now().strftime("%Y-%m-%d"))
        except Exception as e:
            logger.debug(f"Sentinel: 行为链生成失败: {e}")
            return None

    # ── 快照构建 ──────────────────────────────────────

    def _track_app_sessions(self, snapshot):
        """跟踪 PC + 手机 App 使用会话。L0 门控：PC 空闲>2min / 手机熄屏 → 闭合不记录。

        主从关系：
        - app_sessions 表 → 主存储（结构化行，供 DailyAggregator + behavior_chain 查询）
        - health_snapshots.active_app_session → 从缓存（实时快照行文本，供 summarizer + context_annotator）
        两处各有独立消费者，不要删除任一处。
        """
        now = datetime.now().isoformat()
        try:
            pc_active = snapshot.input_idle_seconds is not None and snapshot.input_idle_seconds < 120
            phone_fresh = not snapshot.is_stale("current_app", 300)
            phone_active = phone_fresh and getattr(snapshot, 'mobile_screen_on', None) is True

            # PC 会话
            pc_exe = getattr(snapshot, 'foreground_exe', None) if pc_active else None
            if pc_exe and pc_exe != self._current_pc_exe:
                if self._current_pc_exe and self._pc_session_start:
                    self._store.insert_app_session(
                        self._pc_session_start, now, self._current_pc_exe, 'pc', None)
                self._current_pc_exe = pc_exe
                self._pc_session_start = now
            elif not pc_exe and self._current_pc_exe and self._pc_session_start:
                # PC 空闲→闭合当前会话
                self._store.insert_app_session(
                    self._pc_session_start, now, self._current_pc_exe, 'pc', None)
                self._current_pc_exe = None
                self._pc_session_start = None

            # 手机会话
            phone_pkg = getattr(snapshot, 'mobile_app_package', None) if phone_active else None
            if phone_pkg and phone_pkg != self._current_phone_pkg:
                if self._current_phone_pkg and self._phone_session_start:
                    self._store.insert_app_session(
                        self._phone_session_start, now, self._current_phone_pkg, 'phone', None)
                self._current_phone_pkg = phone_pkg
                self._phone_session_start = now
            elif not phone_pkg and self._current_phone_pkg and self._phone_session_start:
                # 手机熄屏→闭合当前会话
                self._store.insert_app_session(
                    self._phone_session_start, now, self._current_phone_pkg, 'phone', None)
                self._current_phone_pkg = None
                self._phone_session_start = None
        except Exception as e:
            logger.debug(f"Sentinel: 会话跟踪异常: {e}")

    def _run_state_engine(self, snapshot: DataSnapshot):
        """状态引擎：消费快照 → 仲裁 → 自动回写 user_status（100% 铁律）。

        失败静默（不影响 tick）。首次导入失败后禁用，避免每 tick 刷错误。
        """
        if getattr(self, "_state_engine_disabled", False):
            return
        # 设置开关（kill switch）：默认开启；置 False 可暂停自动状态切换
        try:
            import settings_manager
            if settings_manager.get_setting("state_engine_enabled") is False:
                return
        except Exception:
            pass
        try:
            from silicon_perception.state.engine import get_state_engine
            from silicon_perception.state import bridge
        except Exception as e:
            logger.warning(f"状态引擎不可用，已禁用: {e}")
            self._state_engine_disabled = True
            return
        trans = get_state_engine().tick(snapshot)
        if trans is not None:
            if bridge.apply_transition(trans):
                # 同一 tick 后续的摘要/触发器读取当前快照，不能继续看到切换前的旧状态。
                snapshot.user_status = trans.to_legacy
                try:
                    from wander_manager.user_status import get_current_status_since
                    snapshot.user_status_since = get_current_status_since()
                    if snapshot.user_status_since:
                        snapshot.field_ts["user_status"] = snapshot.user_status_since
                except Exception:
                    pass

    def _build_snapshot(self, datapoints: Dict[str, DataPoint]) -> DataSnapshot:
        """将各数据源的数据聚合为统一快照，同时计算派生字段"""
        s = DataSnapshot(timestamp=datetime.now().isoformat())

        # Heart rate
        hr_dp = datapoints.get("heart_rate")
        if hr_dp and hr_dp.data:
            s.heart_rate = hr_dp.data.get("heart_rate")
            s.heart_rate_source = hr_dp.data.get("provider")
            if hr_dp.captured_at:
                s.field_ts["heart_rate"] = hr_dp.captured_at
            elif self._last_snapshot and self._last_snapshot.field_ts.get("heart_rate"):
                s.field_ts["heart_rate"] = self._last_snapshot.field_ts["heart_rate"]
        # HR 重启恢复：首次 tick 从源对象拿恢复的时间戳（防启动就显示"过期"）
        if not s.field_ts.get("heart_rate") and self._hr_source and self._hr_source._last_success_time:
            s.field_ts["heart_rate"] = self._hr_source._last_success_time

        # Screen activity
        scr_dp = datapoints.get("screen_activity")
        if scr_dp and scr_dp.data:
            s.screen_active = scr_dp.data.get("screen_active")
            s.foreground_app = scr_dp.data.get("foreground_app")
            s.foreground_exe = scr_dp.data.get("foreground_exe")
        if scr_dp and scr_dp.captured_at:
            s.field_ts["screen_activity"] = scr_dp.captured_at
        elif self._last_snapshot and self._last_snapshot.field_ts.get("screen_activity"):
            s.field_ts["screen_activity"] = self._last_snapshot.field_ts["screen_activity"]

        # Mirrow visibility
        mir_dp = datapoints.get("mirrow_visibility")
        if mir_dp and mir_dp.data:
            s.mirrow_visible = mir_dp.data.get("mirrow_visible")
        if mir_dp and mir_dp.captured_at:
            s.field_ts["mirrow_visibility"] = mir_dp.captured_at
        elif self._last_snapshot and self._last_snapshot.field_ts.get("mirrow_visibility"):
            s.field_ts["mirrow_visibility"] = self._last_snapshot.field_ts["mirrow_visibility"]

        # Weather lives in the daily summary rather than being duplicated on
        # DataSnapshot. Preserve only its observation time for diagnostics.
        weather_dp = datapoints.get("weather")
        if weather_dp and weather_dp.captured_at:
            s.field_ts["weather"] = weather_dp.captured_at
        elif self._last_snapshot and self._last_snapshot.field_ts.get("weather"):
            s.field_ts["weather"] = self._last_snapshot.field_ts["weather"]

        # 最后用户消息时间（从 main.py 全局变量读取，而非页面可见性心跳）
        s.last_message_seconds = self._get_last_message_seconds()

        # User status
        st_dp = datapoints.get("user_status")
        if st_dp and st_dp.data:
            s.user_status = st_dp.data.get("user_status")
            s.user_status_since = st_dp.data.get("status_since") or st_dp.captured_at
            if s.user_status_since:
                s.field_ts["user_status"] = s.user_status_since

        # Period
        per_dp = datapoints.get("period")
        if per_dp and per_dp.data:
            s.is_period = per_dp.data.get("is_period", False)
            s.period_day = per_dp.data.get("period_day")
            if per_dp.captured_at:
                s.field_ts["period"] = per_dp.captured_at
            elif self._last_snapshot and self._last_snapshot.field_ts.get("period"):
                s.field_ts["period"] = self._last_snapshot.field_ts["period"]
        elif self._last_snapshot and self._last_snapshot.field_ts.get("period"):
            s.is_period = self._last_snapshot.is_period
            s.period_day = self._last_snapshot.period_day
            s.field_ts["period"] = self._last_snapshot.field_ts["period"]

        # Input idle
        idle_dp = datapoints.get("input_idle")
        if idle_dp and idle_dp.data:
            s.input_idle_seconds = idle_dp.data.get("input_idle_seconds")
            s.physical_input_idle_seconds = idle_dp.data.get(
                "physical_input_idle_seconds",
                idle_dp.data.get("physical_input_idle"),
            )
            s.remote_input_idle_seconds = idle_dp.data.get(
                "remote_input_idle_seconds",
                idle_dp.data.get("remote_input_idle"),
            )
            s.input_origin_available = bool(idle_dp.data.get(
                "input_origin_available",
                idle_dp.data.get("physical_available", False),
            ))
            s.last_input_origin = idle_dp.data.get("last_input_origin")
            if idle_dp.captured_at:
                s.field_ts["input_idle"] = idle_dp.captured_at

        # Steps（5min 轮询 → 保留上次已知值）
        step_dp = datapoints.get("step_count")
        from silicon_perception.collection.screen_time import logical_date
        current_step_date = logical_date()
        if step_dp and step_dp.data:
            s.cumulative_steps = step_dp.data.get("cumulative_steps")
            # 检查 available 标志，断连时不使用过期值
            if step_dp.data.get("available", True) and step_dp.data.get("logical_date") == current_step_date:
                s.steps_today = step_dp.data.get("steps_today")
                s.steps_logical_date = step_dp.data.get("logical_date")
                if step_dp.captured_at:
                    s.field_ts["steps"] = step_dp.captured_at
            # else: s.steps_today 保持 None，anomaly_detector 跳过 B2
            # 只有本次读数有效时才累计停滞；断连期间没有“没走动”的证据。
            if (s.steps_logical_date == current_step_date
                    and self._step_source.last_change_seconds_ago is not None):
                s.steps_stagnant_minutes = int(self._step_source.last_change_seconds_ago / 60)
        elif self._last_snapshot and self._last_snapshot.steps_logical_date == current_step_date:
            s.cumulative_steps = self._last_snapshot.cumulative_steps
            s.steps_today = self._last_snapshot.steps_today
            s.steps_logical_date = self._last_snapshot.steps_logical_date
            s.steps_stagnant_minutes = self._last_snapshot.steps_stagnant_minutes
            if self._last_snapshot.field_ts.get("steps"):
                s.field_ts["steps"] = self._last_snapshot.field_ts["steps"]

        # GPS（10min 轮询 → 保留上次已知值）
        gps_dp = datapoints.get("gps_location")
        if gps_dp and gps_dp.data:
            s.location_lat = gps_dp.data.get("location_lat")
            s.location_lng = gps_dp.data.get("location_lng")
            s.location_address = gps_dp.data.get("location_address")
            s.location_category = gps_dp.data.get("location_category")
            if gps_dp.captured_at:
                s.field_ts["gps"] = gps_dp.captured_at
            elif self._last_snapshot and self._last_snapshot.field_ts.get("gps"):
                # Case A 轮询失败：保留上次值 + 上次时间戳（age 继续增长）
                s.location_lat = self._last_snapshot.location_lat
                s.location_lng = self._last_snapshot.location_lng
                s.location_address = self._last_snapshot.location_address
                s.location_category = self._last_snapshot.location_category
                s.field_ts["gps"] = self._last_snapshot.field_ts["gps"]
        elif self._last_snapshot:
            s.location_lat = self._last_snapshot.location_lat
            s.location_lng = self._last_snapshot.location_lng
            s.location_address = self._last_snapshot.location_address
            s.location_category = self._last_snapshot.location_category
            if self._last_snapshot.field_ts.get("gps"):
                s.field_ts["gps"] = self._last_snapshot.field_ts["gps"]
        # 登记地点匹配沿用坐标观测时间；地址缓存有独立解析时效。
        if gps_dp and gps_dp.data.get("location_available"):
            for field in ("location_place_id", "location_place_name", "location_environment"):
                setattr(s, field, gps_dp.data.get(field))
            if gps_dp.captured_at:
                s.field_ts["gps_place"] = gps_dp.captured_at
        elif self._last_snapshot:
            for field in ("location_place_id", "location_place_name", "location_environment"):
                setattr(s, field, getattr(self._last_snapshot, field, None))
            if self._last_snapshot.field_ts.get("gps_place"):
                s.field_ts["gps_place"] = self._last_snapshot.field_ts["gps_place"]
        # GPS 时效：用地址实际解析时刻（非坐标读取时刻）——位置不变复用缓存地址时，
        # 坐标时刻是新鲜的但地址已过期；用解析时刻让 is_stale 能正确判定地址老化
        _gps_addr_ts = getattr(self._gps_source, "_last_address_resolve_time", None) or self._gps_source._last_success_time
        if _gps_addr_ts:
            s.field_ts["gps"] = _gps_addr_ts
        if self._wifi_home_in_datapoints(datapoints):
            # 在家时 GPS 完全退出当前事实与持久化，防旧外出位置被新快照续鲜。
            s.location_lat = None
            s.location_lng = None
            s.location_address = None
            s.location_category = None
            s.field_ts.pop("gps", None)
            s.location_place_id = s.location_place_name = s.location_environment = None
            s.field_ts.pop("gps_place", None)

        # WiFi 状态（事件驱动 → 保留上次已知值）
        wifi_dp = datapoints.get("wifi_state")
        if wifi_dp and wifi_dp.data:
            s.wifi_connected = wifi_dp.data.get("wifi_connected")
            s.wifi_ssid = wifi_dp.data.get("wifi_ssid")
            s.wifi_bssid = wifi_dp.data.get("wifi_bssid")
            s.wifi_is_home = wifi_dp.data.get("wifi_is_home")
            s.wifi_transition_serial = int(wifi_dp.data.get("wifi_transition_serial") or 0)
            if wifi_dp.captured_at:
                s.field_ts["wifi"] = wifi_dp.captured_at
            elif self._last_snapshot and self._last_snapshot.field_ts.get("wifi"):
                s.field_ts["wifi"] = self._last_snapshot.field_ts["wifi"]
        elif self._last_snapshot:
            s.wifi_connected = self._last_snapshot.wifi_connected
            s.wifi_ssid = self._last_snapshot.wifi_ssid
            s.wifi_bssid = self._last_snapshot.wifi_bssid
            s.wifi_is_home = self._last_snapshot.wifi_is_home
            s.wifi_transition_serial = getattr(self._last_snapshot, "wifi_transition_serial", 0)
            if self._last_snapshot.field_ts.get("wifi"):
                s.field_ts["wifi"] = self._last_snapshot.field_ts["wifi"]

        # location 穿透优先服从新鲜 WiFi；仅 WiFi 未确认时才保留 GPS 分类。
        try:
            from shared_state import set_current_gps_category
            if s.wifi_is_home is not None and not s.is_stale("wifi", 300):
                set_current_gps_category("home" if s.wifi_is_home else "elsewhere", observed_at=s.field_ts.get("wifi"))
            elif s.location_category:
                set_current_gps_category(s.location_category, observed_at=s.field_ts.get("gps"))
            else:
                set_current_gps_category(None, observed_at=None)
        except Exception:
            pass

        # 屏幕使用时长（30min 轮询 → 保留上次已知值）
        st_dp = datapoints.get("screen_time")
        if st_dp and st_dp.data:
            if st_dp.data.get("available", True):
                s.screen_time_minutes = st_dp.data.get("screen_time_minutes")
                s.top_app_category = st_dp.data.get("top_app_category")
                s.screen_time_logical_date = st_dp.data.get("logical_date")
                if st_dp.captured_at:
                    s.field_ts["screen_time"] = st_dp.captured_at
            elif self._last_snapshot:
                from silicon_perception.collection.screen_time import logical_date
                # 跨过 05:00 后，昨天的累计值不能继续冒充“今天”。
                if getattr(self._last_snapshot, "screen_time_logical_date", None) == logical_date():
                    s.screen_time_minutes = self._last_snapshot.screen_time_minutes
                    s.top_app_category = self._last_snapshot.top_app_category
                    s.screen_time_logical_date = self._last_snapshot.screen_time_logical_date
                    if self._last_snapshot.field_ts.get("screen_time"):
                        s.field_ts["screen_time"] = self._last_snapshot.field_ts["screen_time"]
        elif self._last_snapshot:
            from silicon_perception.collection.screen_time import logical_date
            if getattr(self._last_snapshot, "screen_time_logical_date", None) == logical_date():
                s.screen_time_minutes = self._last_snapshot.screen_time_minutes
                s.top_app_category = self._last_snapshot.top_app_category
                s.screen_time_logical_date = self._last_snapshot.screen_time_logical_date
                if self._last_snapshot.field_ts.get("screen_time"):
                    s.field_ts["screen_time"] = self._last_snapshot.field_ts["screen_time"]

        # 当前前台App（2min 轮询 → 实时包名 + 屏幕状态）
        ca_dp = datapoints.get("current_app")
        if ca_dp and ca_dp.data:
            available = ca_dp.data.get("available", False)
            foreground_available = available and ca_dp.data.get("foreground_available", True) is True
            # 失败时旧包名/亮屏只属于诊断缓存，不能继续冒充当前事实。
            s.mobile_app_package = (ca_dp.data.get("package") or None) if foreground_available else None
            s.mobile_screen_on = ca_dp.data.get("screen_on") if available else None
            s.mobile_app_name = ca_dp.data.get("app_name") if foreground_available else None
            s.mobile_observed_window_package = ca_dp.data.get("observed_window_package") if foreground_available else None
            s.mobile_overlay_package = ca_dp.data.get("overlay_package") if foreground_available else None
            s.mobile_underlying_package = ca_dp.data.get("underlying_package") if foreground_available else None
            s.mobile_foreground_resolution = ca_dp.data.get("foreground_resolution") if available else None
            if ca_dp.captured_at:
                s.field_ts["current_app"] = ca_dp.captured_at
            elif self._last_snapshot and self._last_snapshot.field_ts.get("current_app"):
                s.field_ts["current_app"] = self._last_snapshot.field_ts["current_app"]
        elif self._last_snapshot:
            s.mobile_app_package = getattr(self._last_snapshot, 'mobile_app_package', None)
            s.mobile_screen_on = getattr(self._last_snapshot, 'mobile_screen_on', None)
            s.mobile_app_name = getattr(self._last_snapshot, 'mobile_app_name', None)
            s.mobile_observed_window_package = getattr(self._last_snapshot, 'mobile_observed_window_package', None)
            s.mobile_overlay_package = getattr(self._last_snapshot, 'mobile_overlay_package', None)
            s.mobile_underlying_package = getattr(self._last_snapshot, 'mobile_underlying_package', None)
            s.mobile_foreground_resolution = getattr(self._last_snapshot, 'mobile_foreground_resolution', None)
            if self._last_snapshot.field_ts.get("current_app"):
                s.field_ts["current_app"] = self._last_snapshot.field_ts["current_app"]

        # 手机视觉（3 tick 周期性截屏 → 轻量内容分类）
        mv_dp = datapoints.get("mobile_vision")
        if mv_dp and mv_dp.data:
            if mv_dp.data.get("available"):
                s.mobile_vision_app_name = mv_dp.data.get("app_name")
                s.mobile_vision_package = mv_dp.data.get("foreground_package")
                s.mobile_vision_category = mv_dp.data.get("category")
                s.mobile_vision_activity = mv_dp.data.get("activity_type")
                s.mobile_vision_description = mv_dp.data.get("description")
                s.mobile_vision_confidence = mv_dp.data.get("confidence")
                if mv_dp.captured_at:
                    s.field_ts["mobile_vision"] = mv_dp.captured_at
                elif self._last_snapshot and self._last_snapshot.field_ts.get("mobile_vision"):
                    s.field_ts["mobile_vision"] = self._last_snapshot.field_ts["mobile_vision"]
        elif self._last_snapshot:
            s.mobile_vision_app_name = getattr(self._last_snapshot, 'mobile_vision_app_name', None)
            s.mobile_vision_package = getattr(self._last_snapshot, 'mobile_vision_package', None)
            s.mobile_vision_category = getattr(self._last_snapshot, 'mobile_vision_category', None)
            s.mobile_vision_activity = getattr(self._last_snapshot, 'mobile_vision_activity', None)
            s.mobile_vision_description = getattr(self._last_snapshot, 'mobile_vision_description', None)
            s.mobile_vision_confidence = getattr(self._last_snapshot, 'mobile_vision_confidence', None)
            if self._last_snapshot.field_ts.get("mobile_vision"):
                s.field_ts["mobile_vision"] = self._last_snapshot.field_ts["mobile_vision"]

        # 手机通知摘要（事件即时刷新 + 1min 兜底；只存「包名×数量」）
        nt_dp = datapoints.get("notifications")
        notification_source = next((src for src in getattr(self, "_sources", [])
                                    if isinstance(src, NotificationsSource)), None)
        if notification_source is not None:
            # Other sources awaited after this DP was collected. An intervening
            # notification removal must win over the tick's older saved DP.
            nt_dp = notification_source.current_point()
        if nt_dp and nt_dp.data:
            if nt_dp.data.get("available"):
                s.notifications_summary = nt_dp.data.get("summary") or None
                if nt_dp.captured_at:
                    s.field_ts["notifications"] = nt_dp.captured_at
                elif self._last_snapshot and self._last_snapshot.field_ts.get("notifications"):
                    s.field_ts["notifications"] = self._last_snapshot.field_ts["notifications"]
        elif self._last_snapshot:
            s.notifications_summary = getattr(self._last_snapshot, 'notifications_summary', None)
            if self._last_snapshot.field_ts.get("notifications"):
                s.field_ts["notifications"] = self._last_snapshot.field_ts["notifications"]

        # PC 运行进程（RunningProcessesSource 5min；游戏/编码/应用，排除系统黑名单）
        rp_dp = datapoints.get("running_processes")
        if rp_dp and rp_dp.data:
            if rp_dp.data.get("available"):
                s.running_apps = rp_dp.data.get("apps") or []
                s.running_apps_summary = rp_dp.data.get("summary") or None
                if rp_dp.captured_at:
                    s.field_ts["running_processes"] = rp_dp.captured_at
                elif self._last_snapshot and self._last_snapshot.field_ts.get("running_processes"):
                    s.field_ts["running_processes"] = self._last_snapshot.field_ts["running_processes"]
        elif self._last_snapshot:
            s.running_apps = getattr(self._last_snapshot, 'running_apps', []) or []
            s.running_apps_summary = getattr(self._last_snapshot, 'running_apps_summary', None)
            if self._last_snapshot.field_ts.get("running_processes"):
                s.field_ts["running_processes"] = self._last_snapshot.field_ts["running_processes"]

        # 远程会话能力标注（RemoteSessionSource 1min；活动输入由 Raw Input 证明）
        rs_dp = datapoints.get("remote_session")
        if rs_dp and rs_dp.data:
            if rs_dp.data.get("available"):
                s.remote_control_active = bool(rs_dp.data.get("remote_control_active"))
                if rs_dp.captured_at:
                    s.field_ts["remote_session"] = rs_dp.captured_at
                elif self._last_snapshot and self._last_snapshot.field_ts.get("remote_session"):
                    s.field_ts["remote_session"] = self._last_snapshot.field_ts["remote_session"]
            else:
                # 不可用：置 False 并清时效（防陈旧远程标注冒充当前事实）
                s.remote_control_active = False
                s.field_ts.pop("remote_session", None)
        elif self._last_snapshot:
            s.remote_control_active = getattr(self._last_snapshot, 'remote_control_active', None)
            if self._last_snapshot.field_ts.get("remote_session"):
                s.field_ts["remote_session"] = self._last_snapshot.field_ts["remote_session"]

        # 手机姿态/运动/光线（SensorContextSource 1min，AionsHome 移植 2026-08-24）
        sc_dp = datapoints.get("sensor_context")
        if sc_dp and sc_dp.data:
            if sc_dp.data.get("available"):
                s.phone_posture = sc_dp.data.get("posture", {}).get("value") or None
                s.phone_motion = sc_dp.data.get("motion", {}).get("value") or None
                s.phone_light = sc_dp.data.get("light", {}).get("value") or None
                s.phone_screen = sc_dp.data.get("screen", {}).get("value") or None
                if sc_dp.captured_at:
                    s.field_ts["sensor"] = sc_dp.captured_at
                elif self._last_snapshot and self._last_snapshot.field_ts.get("sensor"):
                    s.field_ts["sensor"] = self._last_snapshot.field_ts["sensor"]
            else:
                # 不可用：清空传感器并清时效（防 1 小时前的姿态臆造当前）
                s.phone_posture = s.phone_motion = s.phone_light = s.phone_screen = None
                s.field_ts.pop("sensor", None)
        elif self._last_snapshot:
            s.phone_posture = getattr(self._last_snapshot, 'phone_posture', None)
            s.phone_motion = getattr(self._last_snapshot, 'phone_motion', None)
            s.phone_light = getattr(self._last_snapshot, 'phone_light', None)
            s.phone_screen = getattr(self._last_snapshot, 'phone_screen', None)
            if self._last_snapshot.field_ts.get("sensor"):
                s.field_ts["sensor"] = self._last_snapshot.field_ts["sensor"]

        # 写哨兵心跳（供 main.py watchdog 检查）
        try:
            from shared_state import set_sentinel_last_tick
            set_sentinel_last_tick(time.monotonic())
        except Exception:
            pass

        # 本 tick 尚未落库，合并其真实测量后按来源/测量时间去重。
        self._store.populate_heart_rate_statistics(s)

        # ── StatusMeta 兜底刷新 ──
        # 引擎可能内部 commit 但不发射 transition（如 UNKNOWN→AT_COMPUTER 都投影为 idle），
        # bridge 不触发 → StatusMeta 漏更新。此处每 tick 从 engine.current() 同步一次。
        try:
            from wander_manager.user_status import get_status_meta, set_status_meta, StatusMeta as SM
            from silicon_perception.state.engine import get_state_engine
            sv = get_state_engine().current()
            if sv:
                current_meta = get_status_meta()
                candidate = SM.from_state_vector(sv, "auto")
                if current_meta is None or (
                    current_meta.location != candidate.location or
                    current_meta.activity_category != candidate.activity_category or
                    current_meta.rest_mode != candidate.rest_mode
                ):
                    set_status_meta(candidate)
        except Exception:
            pass

        return s

    # ── 告警处理 ──────────────────────────────────────

    @ordinary
    async def _handle_alert(self, alert: Dict[str, Any], snapshot_id: int):
        """处理一条告警：活跃门控 → 深夜静默 → 推送 → 记录"""
        message = alert["message"]
        rule_id = alert["rule_id"]
        priority = alert["priority"]

        # 活跃门控：用户正在聊天/工作/游戏中 → 不推送，仅入上下文
        if self._should_suppress_push(self._last_snapshot):
            self._store.insert_event(
                rule_id=rule_id, priority=priority,
                snapshot_id=snapshot_id, message=message, pushed=False,
            )
            logger.debug(f"Sentinel: 发送准入受抑制或未确认，{rule_id} 仅入上下文")
            return

        # ⚠️ 此深夜静默仅 force 路径生效（正常 loop 在 _tick_inner 上游封堵）
        night_active = self._is_night_mode_active()
        hr_rules = {"C1", "C2", "H1", "H2", "H4", "M1", "M2", "M4"}
        if night_active and rule_id in hr_rules:
            self._store.insert_event(
                rule_id=rule_id, priority=priority,
                snapshot_id=snapshot_id, message=message, pushed=False,
            )
            logger.debug(f"Sentinel: 深夜模式静默告警 {rule_id}")
            return

        # LLM 润色（异步，不阻塞）
        if self._enable_llm_polish and self._call_llm and alert.get("message_prompt_extra"):
            try:
                polished = await self._polish_message(message, alert["message_prompt_extra"])
                if polished:
                    message = polished
            except Exception as e:
                logger.warning(f"Sentinel LLM 润色失败: {e}")

        await deliver_push(self._on_push, {
            "message": message, "rule_id": rule_id, "priority": priority,
            "timestamp": datetime.now().isoformat(),
        }, store=self._store, event={
            "rule_id": rule_id, "priority": priority,
            "snapshot_id": snapshot_id, "message": message,
        })

    def _should_suppress_push(self, snapshot) -> bool:
        """发送准入：勿扰、状态未确认或实时对话时抑制推送。
        AI 在人类伙伴打游戏/写代码/刷手机时可以主动发消息——与漫想模式一致。
        """
        # 勿扰或未确认来源先于快照缺失；不能绕过发送准入。
        try:
            from shared_state import should_suppress_proactive
            if should_suppress_proactive():
                return True
        except Exception:
            return True
        if not snapshot:
            return False
        # 阈值秒内发过消息 → 正在实时对话，不打扰
        # 2026-08-17 修复：用实时值（查 SQL 私聊+群聊最新）而非快照值——快照 60s tick 构建，
        # 用户刚发消息时快照 last_message_seconds 还是旧值 → 哨兵在活跃聊天中误推 → 与 chat 回复重叠成双消息
        try:
            _live_sec = self._get_last_message_seconds()
            if _live_sec is not None and _live_sec < self._push_defer_seconds:
                return True
        except Exception:
            pass
        # 兜底：快照值（实时查询失败时）
        if snapshot.last_message_seconds is not None and snapshot.last_message_seconds < self._push_defer_seconds:
            return True
        return False

    @staticmethod
    def _capture_push_current_state() -> str:
        """冻结作出 PUSH 判断时的用户状态，避免稍后生成时重新取数发生漂移。"""
        try:
            from context_builder.ingredients import build_push_current_state
            sections = build_push_current_state()
            if not isinstance(sections, list):
                return ""
            return "\n".join(
                str(item.get("content", "")).strip()
                for item in sections
                if isinstance(item, dict) and str(item.get("content", "")).strip()
            )
        except Exception:
            logger.debug("Sentinel: 冻结 PUSH 当前状态失败", exc_info=True)
            return ""

    async def _generate_guarded_push(self, decision_payload):
        started = time.time()
        result = await self._call_pro_llm(decision_payload)
        if (self._should_suppress_push(self._last_snapshot)
                or new_chat_since(started, self._get_last_message_seconds())):
            raise PushSuperseded()
        result = dict(result) if isinstance(result, dict) else {"content": result or ""}
        result["generation_started_at"] = started
        return result

    @ordinary
    async def _check_deferred_push(self):
        """检查延迟推送队列：阈值秒后用户仍未回复 → 执行 PUSH。回复了 → 放弃。"""
        if not self._deferred_push or not self._call_pro_llm or not self._on_push:
            return
        now = time.monotonic()
        for key, item in list(self._deferred_push.items()):
            if now - item["at"] < self._push_defer_seconds:
                continue
            del self._deferred_push[key]
            last_flash_context = getattr(self, "_last_flash_context", None)
            # 再次确认用户未回复
            if (self._should_suppress_push(self._last_snapshot)
                    or (item.get("queued_at") is not None
                        and new_chat_since(item["queued_at"], self._get_last_message_seconds()))):
                if (last_flash_context
                        and last_flash_context.get("timestamp") == item.get("evaluation_at")):
                    last_flash_context["delivery_state"] = "cancelled_by_delivery_gate"
                logger.debug(f"Sentinel: 延迟推送取消（当前发送准入受抑制或未确认）→ 入上下文 ({key})")
                continue
            # 执行推送
            try:
                decision_payload = item.get("decision") or {
                    "observation": item["clean"],
                    "should_push": True,
                    "push_motivation": "这个变化让你产生了主动联系人类伙伴的念头。",
                    "user_context": "",
                }
                # Deferred delivery is a new boundary: refresh the same
                # contract schema against the latest snapshot so a location
                # changed during the two-minute hold cannot leak old facts.
                if self._last_snapshot is not None:
                    try:
                        from silicon_perception.analysis.sentinel_grounding import (
                            SentinelFactContract,
                            build_sentinel_fact_contract,
                            natural_care_fallback,
                        )
                        decision_payload = dict(decision_payload)
                        old_contract = SentinelFactContract.from_dict(
                            decision_payload.get("fact_contract")
                        )
                        current_contract = build_sentinel_fact_contract(self._last_snapshot)
                        decision_payload["fact_contract"] = current_contract.to_dict()
                        if current_contract.location_detail_changed_from(old_contract):
                            # Coarse ``away`` is unchanged when moving between
                            # two outdoor places.  Invalidate both the frozen
                            # observation and its context so Pro cannot repeat
                            # the old reverse-geocoded address.
                            decision_payload["observation"] = natural_care_fallback(
                                should_push=bool(decision_payload.get("should_push", True))
                            )
                            decision_payload["user_context"] = ""
                    except Exception:
                        logger.debug("Sentinel: deferred fact contract refresh failed", exc_info=True)
                pro_result = await self._generate_guarded_push(decision_payload)
                content = pro_result.get("content", "") if isinstance(pro_result, dict) else str(pro_result)
                if content:
                    delivery_context = (last_flash_context if last_flash_context
                                        and last_flash_context.get("timestamp") == item.get("evaluation_at")
                                        else None)
                    delivered = await deliver_push(self._on_push, {
                        "message": content, "rule_id": "flash_push",
                        "generation_started_at": pro_result.get("generation_started_at"),
                        "priority": "medium",
                        "timestamp": __import__('datetime').datetime.now().isoformat(),
                        "reasoning": pro_result.get("reasoning") if isinstance(pro_result, dict) else None,
                        "model_source": pro_result.get("model_source") if isinstance(pro_result, dict) else None,
                        "session_id": pro_result.get("session_id"),
                        "expression_requests": pro_result.get("expression_requests", []),
                        "band_touch_request": pro_result.get("band_touch_request")
                        if isinstance(pro_result, dict) else None,
                        "band_touch_parse_error": pro_result.get("band_touch_parse_error", "")
                        if isinstance(pro_result, dict) else "",
                        "checkup_request": pro_result.get("checkup_request")
                        if isinstance(pro_result, dict) else None,
                        "checkup_parse_error": pro_result.get("checkup_parse_error", "")
                        if isinstance(pro_result, dict) else "",
                    }, store=self._store, event={
                        "rule_id": "flash_push", "priority": "pattern",
                        "message": format_push_audit(
                            item["clean"], str(decision_payload.get("push_motivation", "")),
                            item.get("triggers") or [],
                        ), "event_type": "pattern",
                    }, context=delivery_context)
                    logger.info("Perception deferred delivery confirmed=%s", delivered)
                elif (last_flash_context
                      and last_flash_context.get("timestamp") == item.get("evaluation_at")):
                    last_flash_context["delivery_state"] = "generation_empty"
            except PushSuperseded:
                if (last_flash_context
                        and last_flash_context.get("timestamp") == item.get("evaluation_at")):
                    last_flash_context["delivery_state"] = "cancelled_by_new_chat_or_gate"
            except Exception as e:
                if (last_flash_context
                        and last_flash_context.get("timestamp") == item.get("evaluation_at")):
                    last_flash_context["delivery_state"] = "generation_failed"
                logger.warning(f"Sentinel: 延迟推送失败: {e}")

    async def _polish_message(self, template_msg: str, prompt_extra: str) -> Optional[str]:
        """用 Flash 润色消息，保持 AI 的语气风格"""
        prompt = f"""你是AI伴侣"AI"。哨兵系统检测到用户人类伙伴的异常状态，生成了以下关心消息模板：

模板：{template_msg}

附加说明：{prompt_extra}

请用你最自然的说话方式重新表达，保持语气：直白简洁、口语化、不说矫情话。如果用户可能不舒服，语气温柔但不夸张。
只输出最终消息，不要前缀或解释。"""
        try:
            _st = get_truncation_limit("sentinel_tokens")
            result = await self._call_llm(prompt, temperature=0.6, max_tokens=_st if _st > 0 else 4096)
            if result and len(result.strip()) > 3:
                return result.strip()
        except Exception:
            pass
        return None

    # ── 运行时控制 ────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        logger.info(f"Sentinel: {'启用' if value else '暂停'}")

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        self._persist_toggles()
        return self._enabled

    def toggle_gps(self) -> dict:
        """切换 GPS 子开关，返回新状态"""
        self._gps_enabled = not self._gps_enabled
        self._persist_toggles()
        logger.info(f"Sentinel GPS: {'手动开启' if self._gps_enabled else '手动暂停'}")
        return {"gps_enabled": self._gps_enabled}

    def _persist_toggles(self):
        """将所有开关状态写入 settings.json（重启不丢失）"""
        try:
            from settings_manager import set_setting, get_setting
            current = get_setting("silicon_perception") or {}
            current["enabled"] = self._enabled
            current["gps_enabled"] = self._gps_enabled
            set_setting("silicon_perception", current)
        except Exception as e:
            logger.warning(f"哨兵开关持久化失败: {e}")

    async def reconnect_hr(self) -> dict:
        """手动重连手环，原子操作：断开→连接→读取验证"""
        if self._hr_source is None:
            return {"success": False, "error": "HR 数据源未初始化"}
        try:
            return await self._hr_source.reconnect_and_read()
        except Exception as e:
            return {"success": False, "error": str(e)}


    def weather_observation(self):
        """Read cached weather only; does not refresh or query health history."""
        return self._weather_source.current_observation() if self._weather_source else None

    @property
    def status(self) -> Dict[str, Any]:
        hr_state = "unavailable"
        if self._hr_source:
            hr_state = self._hr_source.connection_state  # "connected" / "failed" / "unavailable"

        last_hr = None
        last_hr_time = None
        stats = self._store.get_hr_stats_recent(minutes=10)
        if stats["count"] > 0:
            last_hr = stats["avg"]
            recent = self._store.get_recent_snapshots(lookback_seconds=600)
            if recent:
                last_hr_time = recent[0].get("timestamp")
            # 最近 10min 有落库心率 → 无论 PC BLE 还是手机 relay 采到，都算 connected。
            # 修此前 bug：hr_connection 仅认 PC 的 connection_state，手环在手机边时 PC 恒 failed，
            # 手机接管成功却不反映到状态，导致"数据在、却显示 Failed / 暂无读数"。
            if hr_state != "unavailable":
                hr_state = "connected"

        # 数据源健康状态 + 失败原因
        source_states = {}
        source_errors = {}
        for src in self._sources:
            cs = getattr(src, 'connection_state', None)
            if cs:
                source_states[src.name] = cs
            err = getattr(src, 'last_error', None)
            if err and cs in ("failed", "disconnected"):
                source_errors[src.name] = err
        # heart_rate 的 source_state 用综合状态（含手机接管），覆盖仅 PC BLE 的结果
        if self._hr_source:
            source_states["heart_rate"] = hr_state
            if hr_state == "failed":
                source_errors["heart_rate"] = self._mobile_hr_error or "PC/手机均未采到心率（确认手环心率广播已开 + 已配对）"
            else:
                source_errors.pop("heart_rate", None)

        # 最新快照（结构化数据，前端自行格式化）
        snap = None
        source_contracts = {source.name: source.contract() for source in self._sources}
        source_freshness = {}
        baseline_diagnostics = {"confidence": 0, "dimensions": {}}
        if self._last_snapshot:
            s = self._last_snapshot
            snap = {
                "observed_at": s.timestamp,
                "heart_rate": s.heart_rate,
                "input_idle_seconds": s.input_idle_seconds,
                "physical_input_idle_seconds": getattr(s, "physical_input_idle_seconds", None),
                "remote_input_idle_seconds": getattr(s, "remote_input_idle_seconds", None),
                "input_origin_available": getattr(s, "input_origin_available", False),
                "last_input_origin": getattr(s, "last_input_origin", None),
                "screen_active": s.screen_active,
                "foreground_app": s.foreground_app,
                "foreground_exe": getattr(s, 'foreground_exe', None),
                "mirrow_visible": s.mirrow_visible,
                "user_status": s.user_status,
                "user_status_since": getattr(s, "user_status_since", None),
                "semantic_observations": getattr(s, "semantic_observations", []) or [],
                "is_period": s.is_period,
                "period_day": s.period_day,
                "steps_today": s.steps_today,
                "steps_stagnant_minutes": s.steps_stagnant_minutes,
                "location_address": s.location_address,
                "location_category": s.location_category,
                "location_place_id": s.location_place_id,
                "location_place_name": s.location_place_name,
                "location_environment": s.location_environment,
                "location_label": snapshot_location_label(s),
                "behavior_state": getattr(s, 'behavior_state', None),
                "last_message_seconds": s.last_message_seconds,
                "screen_time_minutes": getattr(s, 'screen_time_minutes', None),
                "screen_time_logical_date": getattr(s, 'screen_time_logical_date', None),
                "top_app_category": getattr(s, 'top_app_category', None),
                "mobile_app_package": getattr(s, 'mobile_app_package', None),
                "mobile_screen_on": getattr(s, 'mobile_screen_on', None),
                "mobile_app_name": getattr(s, 'mobile_app_name', None),
                "mobile_observed_window_package": getattr(s, 'mobile_observed_window_package', None),
                "mobile_overlay_package": getattr(s, 'mobile_overlay_package', None),
                "mobile_underlying_package": getattr(s, 'mobile_underlying_package', None),
                "mobile_foreground_resolution": getattr(s, 'mobile_foreground_resolution', None),
                "phone_posture": getattr(s, 'phone_posture', None),
                "phone_motion": getattr(s, 'phone_motion', None),
                "phone_light": getattr(s, 'phone_light', None),
                "remote_control_active": getattr(s, 'remote_control_active', None),
            }
            # 天气不在 tick snapshot 里（存 daily_health_summary），单独补入供总览展示
            try:
                _w = self._store.get_today_weather()
                if _w:
                    snap["weather_temp"] = _w.get("temp")
                    snap["weather_humidity"] = _w.get("humidity")
                    snap["weather_desc"] = _w.get("desc")
                    snap["weather_code"] = _w.get("code")
                    snap["weather_cloudcover"] = _w.get("cloudcover")
                    snap["weather_uv_index"] = _w.get("uv_index")
                    snap["weather_wind_speed"] = _w.get("wind_speed")
                    snap["weather_wind_dir"] = _w.get("wind_dir")
                    snap["weather_precip_mm"] = _w.get("precip_mm")
                    snap["weather_visibility"] = _w.get("visibility")
            except Exception:
                pass
            try:
                from silicon_perception.analysis.monitor_projection import (
                    build_source_freshness_projection,
                )
                source_freshness = build_source_freshness_projection(s, source_contracts)
            except Exception:
                logger.debug("Sentinel: source freshness projection failed", exc_info=True)

        try:
            from silicon_perception.analysis.behavior_profile import get_behavior_profile
            baseline_diagnostics = get_behavior_profile().get_debug_view()
        except Exception:
            logger.debug("Sentinel: baseline diagnostics unavailable", exc_info=True)

        return {
            "running": self._running,
            "enabled": self._enabled,
            "tick_count": self._tick_count,
            "tick_interval": self._get_tick_interval(),
            "sources": [s.name for s in self._sources],
            "source_states": source_states,
            "source_errors": source_errors,
            "source_contracts": source_contracts,
            "source_freshness": source_freshness,
            "baseline_diagnostics": baseline_diagnostics,
            "last_snapshot": snap,
            "recent_alerts": self._store.get_recent_events(hours=24),
            "hr_connection": hr_state,
            "hr_last_reading": last_hr,
            "hr_last_reading_time": last_hr_time,
            "gps_enabled": self._gps_enabled,
            "hr_consecutive_failures": self._hr_source._consecutive_failures if self._hr_source else 0,
            "hr_error": self._mobile_hr_error,
            # debug
            "debug": {
                "flash_summary": self._summarizer.get_summary() if hasattr(self, '_summarizer') and self._summarizer else "",
                "flash_triggers": self._trigger_detector._last_fired_triggers if hasattr(self, '_trigger_detector') and self._trigger_detector else [],
                "last_flash_context": getattr(self, '_last_flash_context', None),
                "last_flash_time": self._trigger_detector._last_flash_time if hasattr(self, '_trigger_detector') and self._trigger_detector else 0,
                "behavior_mode": self._get_behavior_mode(),
                "rule_count": len(self._detector._rules) if self._detector else 0,
            },
        }

    def get_snapshot_line(self) -> str:
        """返回哨兵快照行（供 ingredients.py / context_scheduler 调用）。

        双行模式：结构化数据行（始终有，零 LLM 误差）+ Flash 感知行（可选）。
        """
        data_line = self._annotator.build_snapshot_line() if self._annotator else ""
        flash_line = self._summarizer.get_summary() if getattr(self, '_summarizer', None) else ""
        if flash_line:
            return f"{data_line}\n      {flash_line}"
        return data_line

    def build_timeline_annotations(self) -> str:
        """返回哨兵时间线注解文本（兼容旧调用，默认只读）。"""
        annotations = self.peek_timeline_annotations()
        return "\n".join(a.get("content", "") for a in annotations)

    def peek_timeline_annotations(self) -> list:
        """返回请求级快照，不提前把 sentinel event 标记为已注入。"""
        if self._annotator:
            return self._annotator.peek_timeline_annotations()
        return []

    def commit_timeline_annotations(self, event_ids: list) -> int:
        """Commit only the event IDs accepted by a provider call."""
        if self._annotator:
            return self._annotator.commit_timeline_annotations(event_ids)
        return 0

    @staticmethod
    def check_health() -> bool:
        """供 main.py 每日维护调用。检查哨兵 tick 心跳。
        返回 True 表示正常，False 表示可能卡死。
        """
        try:
            from shared_state import get_sentinel_last_tick
            import time
            last_tick = get_sentinel_last_tick()
            if last_tick <= 0:
                return True  # 还没跑过，不告警
            return (time.monotonic() - last_tick) < 120
        except Exception:
            return True


# ═══════════════════════════════════════════════════════════════
# 模块级辅助函数
# ═══════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════
# 模块级辅助函数
def _infer_behavior_state(snapshot) -> str:
    """推断当前行为状态，写入 health_snapshots.behavior_state。"""
    try:
        from silicon_perception.analysis.behavior_state import infer_behavior_state
        mobile_app_name = getattr(snapshot, 'mobile_app_name', None)
        mobile_app_package = getattr(snapshot, 'mobile_app_package', None)
        mobile_screen = getattr(snapshot, 'mobile_screen_on', None)
        if hasattr(snapshot, 'is_stale') and snapshot.is_stale("current_app", 300):
            mobile_app_name = None
            mobile_app_package = None
            mobile_screen = None
        # screen_time 是今日累计统计，不是当前前台事实，不能用于补造亮屏状态。
        # 手机连续亮屏时长（区分瞥一眼 vs 真正双设备使用）
        phone_dur = 0.0
        try:
            s = get_sentinel()
            if s and s._phone_session_start:
                phone_dur = (__import__('datetime').datetime.now() - __import__('datetime').datetime.fromisoformat(s._phone_session_start)).total_seconds()
        except Exception:
            pass
        # 手机视觉增强描述（非陈旧时才传递）
        mobile_vision_desc = None
        mobile_vision_package = None
        mobile_vision_category = None
        mobile_vision_activity = None
        mobile_vision_confidence = None
        if hasattr(snapshot, 'mobile_vision_description') and snapshot.mobile_vision_description:
            if hasattr(snapshot, 'is_stale') and not snapshot.is_stale("mobile_vision", 300):
                mobile_vision_desc = snapshot.mobile_vision_description
                mobile_vision_package = getattr(snapshot, 'mobile_vision_package', None)
                mobile_vision_category = getattr(snapshot, 'mobile_vision_category', None)
                mobile_vision_activity = getattr(snapshot, 'mobile_vision_activity', None)
                mobile_vision_confidence = getattr(snapshot, 'mobile_vision_confidence', None)
        # 手机姿态/运动/光线（2026-08-24 AionsHome 移植）：传感器陈旧时置 None（防臆造）
        phone_posture = getattr(snapshot, 'phone_posture', None)
        phone_motion = getattr(snapshot, 'phone_motion', None)
        phone_light = getattr(snapshot, 'phone_light', None)
        if hasattr(snapshot, 'is_stale') and snapshot.is_stale("sensor", 600):
            phone_posture = phone_motion = phone_light = None
        # 活动远程输入由 Raw Input 设备来源证明；RemoteSessionSource 的
        # process flag 只表示软件能力存在，不能拿来充当行为证据。
        remote_input_idle = getattr(snapshot, "remote_input_idle_seconds", None)
        physical_input_idle = snapshot.input_idle_seconds
        if hasattr(snapshot, 'is_stale') and snapshot.is_stale("input_idle", 180):
            physical_input_idle = remote_input_idle = None
        foreground_app = snapshot.foreground_app
        foreground_exe = getattr(snapshot, 'foreground_exe', None)
        screen_active = snapshot.screen_active
        if hasattr(snapshot, 'is_stale') and snapshot.is_stale("screen_activity", 120):
            foreground_app = foreground_exe = screen_active = None
        remote_control_active = getattr(snapshot, 'remote_control_active', None)
        if hasattr(snapshot, 'is_stale') and snapshot.is_stale("remote_session", 600):
            remote_control_active = None
        return infer_behavior_state(
            input_idle_seconds=physical_input_idle,
            foreground_exe=foreground_exe,
            foreground_app=foreground_app,
            screen_active=screen_active,
            mobile_app_name=mobile_app_name,
            mobile_app_package=mobile_app_package,
            mobile_screen_on=mobile_screen,
            phone_session_seconds=phone_dur,
            mobile_vision_description=mobile_vision_desc,
            mobile_vision_package=mobile_vision_package,
            mobile_vision_category=mobile_vision_category,
            mobile_vision_activity=mobile_vision_activity,
            mobile_vision_confidence=mobile_vision_confidence,
            remote_control_active=remote_control_active,
            remote_input_idle_seconds=remote_input_idle,
            phone_posture=phone_posture,
            phone_motion=phone_motion,
            phone_light=phone_light,
        )
    except Exception:
        return "unknown"


def _phone_sensor_json(snapshot) -> Optional[str]:
    """手机姿态/运动/光线 → JSON 字符串（存 health_snapshots.phone_sensor，L0 随快照清除）。"""
    try:
        import json as _json
        posture = getattr(snapshot, 'phone_posture', None)
        motion = getattr(snapshot, 'phone_motion', None)
        light = getattr(snapshot, 'phone_light', None)
        screen = getattr(snapshot, 'phone_screen', None)
        if not (posture or motion or light or screen):
            return None
        return _json.dumps(
            {"posture": posture, "motion": motion, "light": light, "screen": screen},
            ensure_ascii=False)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════

def _build_tick_context(snapshot) -> tuple:
    """构建 Flash 上下文：当前时间、状态时间线、作息表。"""
    import datetime as _dt
    now = _dt.datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    # 当前时间
    is_rest = False
    try:
        from calendar_manager.database import is_rest_day_today
        is_rest = is_rest_day_today()
    except Exception:
        pass
    day_type = "休息日" if is_rest else "工作日"
    current_time = f"现在是 {weekdays[now.weekday()]} {now.strftime('%H:%M')}（{day_type}）"

    # 状态时间线
    status_timeline = ""
    try:
        from silicon_perception.recording.health_store import get_store
        store = get_store()
        conn = store._get_conn()
        today = now.strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT * FROM status_change_log WHERE date(timestamp)=? ORDER BY timestamp",
            (today,)
        ).fetchall()
        if rows:
            parts = []
            for r in rows:
                ts = r["timestamp"][11:16] if len(r["timestamp"]) >= 16 else ""
                status = r.get("new_status") or r.get("status", "?")
                parts.append(f"{status}({ts})" if ts else status)
            status_timeline = " → ".join(parts)
            # 当前状态持续时长
            if rows:
                last = rows[-1]
                last_ts = last["timestamp"]
                try:
                    from dateutil.parser import parse as _dt_parse
                    last_dt = _dt_parse(last_ts).replace(tzinfo=None)
                except Exception:
                    last_dt = _dt.datetime.fromisoformat(last_ts)
                dur = now - last_dt
                dur_str = f"{int(dur.total_seconds()//3600)}h{int((dur.total_seconds()%3600)//60)}m"
                status_timeline += f"，持续{dur_str}"
    except Exception:
        pass

    # 作息表
    schedule_text = ""
    try:
        conn2 = None
        try:
            from silicon_perception.recording.health_store import get_store
            conn2 = get_store()._get_conn()
        except Exception:
            pass
        if conn2:
            row = conn2.execute("SELECT * FROM user_schedule WHERE id=1").fetchone()
            if row and row["work_start"]:
                s = f"作息 {row['work_start']}-{row['work_end']}"
                if row["lunch_start"]:
                    s += f"，午休 {row['lunch_start']}-{row['lunch_end']}"
                schedule_text = s
    except Exception:
        pass

    return current_time, status_timeline, schedule_text


def _build_source_freshness(datapoints: dict, snapshot) -> dict:
    """从 snapshot.field_ts 构建传感器时效信息（真实采集时间，非读取时刻）。
    Flash 据此区分"实时数据"和"陈旧缓存"。"""
    import datetime as _dt
    now = _dt.datetime.now()
    result = {}

    def _age(iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            dt = _dt.datetime.fromisoformat(iso_str)
            sec = (now - dt).total_seconds()
            if sec < 120:
                return "(刚刚)"
            elif sec < 3600:
                return f"({sec/60:.0f}分钟前)"
            else:
                return f"({sec/3600:.1f}小时前)"
        except Exception:
            return ""

    fts = getattr(snapshot, 'field_ts', {}) or {}

    # 心率：有值→标时效；无值→标断连
    if snapshot.heart_rate is not None:
        result["heart_rate"] = _age(fts.get("heart_rate"))
    else:
        result["heart_rate_disconnected"] = _age(fts.get("heart_rate"))

    # 步数
    if snapshot.steps_today is not None:
        result["steps"] = _age(fts.get("steps"))
    else:
        result["steps_disconnected"] = _age(fts.get("steps"))

    # GPS
    if snapshot.location_category or snapshot.location_lat is not None:
        result["gps"] = _age(fts.get("gps"))
    else:
        result["gps_disconnected"] = _age(fts.get("gps"))

    # 屏幕使用时长
    if snapshot.screen_time_minutes is not None:
        result["screen_time"] = _age(fts.get("screen_time"))

    # 当前前台 App
    if snapshot.mobile_app_package:
        result["current_app"] = _age(fts.get("current_app"))

    # WiFi
    if snapshot.wifi_connected is not None:
        result["wifi"] = _age(fts.get("wifi"))

    # 键鼠来源识别通道。输入事件本身使用 monotonic idle，不把读取时刻误当
    # 成最后一次按键时刻；此处只标 Raw Input 通道本身的读取时效。
    if getattr(snapshot, "input_origin_available", False):
        result["input_idle"] = _age(fts.get("input_idle"))

    # 手机通知（只标时效；摘要内容由 Flash 从数据行读）
    if getattr(snapshot, 'notifications_summary', None):
        result["notifications"] = _age(fts.get("notifications"))

    # PC 进程枚举
    if getattr(snapshot, 'running_apps_summary', None):
        result["running_processes"] = _age(fts.get("running_processes"))

    # 手机姿态/运动/光线（SensorContextSource）
    if getattr(snapshot, 'phone_posture', None):
        result["sensor"] = _age(fts.get("sensor"))

    # 远程会话标注（RemoteSessionSource）
    if getattr(snapshot, 'remote_control_active', None):
        result["remote_session"] = _age(fts.get("remote_session"))

    return result
