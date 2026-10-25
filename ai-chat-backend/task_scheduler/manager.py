"""
定时任务管理器 - 核心逻辑

管理定时任务的创建、查询、触发和通知推送。
支持 LLM 生成个性化消息（触发时调用 AI 生成自然语言提醒）。
支持回复复查机制（提醒发出后追踪用户是否回复，5min/10min 复查）。
"""
import asyncio
from api_sources.scope import routing_scope
from body_admission import ordinary
import logging
import uuid
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, List, Optional, Set

from fastapi import WebSocket

from .models import ScheduledTask, TaskCreateRequest, PendingReminder
from . import database as db
from .notifier import send_desktop_notification_async

logger = logging.getLogger(__name__)

# LLM 消息生成器类型：async (task) -> str | {message, thinking, band_touch_request}
LlmGenerator = Callable[[ScheduledTask], Coroutine]

# 复查 LLM 生成器类型：async (original_message, task_title, reason, ai_context, stage, reminder_type) -> str
RecheckLlmGenerator = Callable[[str, str, str, str, int, str], Coroutine]

# 用户回复时间查询器：sync () -> float（空闲秒数）
ReplyChecker = Callable[[], Optional[float]]


INCONSISTENCY_ALARM_AFTER_SECONDS = 5 * 60
MOBILE_ALARM_DEVICE_DELAY_SECONDS = 1


def _is_bedtime_reminder(task: ScheduledTask) -> bool:
    material = " ".join((task.title or "", task.description or "", task.reason or "")).lower()
    return any(word in material for word in ("睡觉", "睡眠", "晚安", "bedtime"))


class TaskManager:
    """定时任务管理器"""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._llm_message_generator: Optional[LlmGenerator] = None
        self._chat_injector: Optional[Callable[..., Coroutine]] = None  # async (msg, is_recheck=False) -> bool
        self._reply_checker: Optional[ReplyChecker] = None
        self._recheck_llm_generator: Optional[RecheckLlmGenerator] = None
        self._current_session_id: Optional[str] = None
        self._persona_provider: Optional[Callable[[], str]] = None  # () -> persona_prompt
        self._self_task_executor: Optional[Callable[..., Coroutine]] = None  # async (task) -> str
        self._checkup_notifier: Optional[Callable[..., Coroutine]] = None
        self._due_check_lock = asyncio.Lock()  # 防并发：确保 check_due_tasks 单实例运行

    # ── 初始化 ──────────────────────────────────────────

    def initialize(self):
        """初始化数据库"""
        db.init_db()
        logger.info("定时任务管理器数据库已初始化")

    def set_llm_message_generator(self, generator: LlmGenerator):
        """
        设置 LLM 消息生成器。

        当任务触发时调用此生成器获取 AI 主动发送的消息内容。
        所有日程提醒（包括 popup-only）都会注入聊天消息。
        """
        self._llm_message_generator = generator

    def set_chat_injector(self, injector: Callable[..., Coroutine]):
        """设置聊天注入回调；附带稳定消息 ID、提醒标记和事件类型。"""
        self._chat_injector = injector

    def set_reply_checker(self, checker: ReplyChecker):
        """设置用户回复时间查询器：sync () -> float（空闲秒数）"""
        self._reply_checker = checker

    def set_recheck_llm_generator(self, generator: RecheckLlmGenerator):
        """设置复查/查岗结果 LLM 生成器。"""
        self._recheck_llm_generator = generator

    def set_persona_provider(self, provider: Callable[[], str]):
        """设置人设提供者：sync () -> str，用于生成不一致查岗消息"""
        self._persona_provider = provider

    def set_self_task_executor(self, executor: Callable[..., Coroutine]):
        """设置 self_task 执行器：async (task: ScheduledTask) -> str"""
        self._self_task_executor = executor

    def set_checkup_notifier(self, notifier: Callable[..., Coroutine]):
        """Inject the UI-only durable receipt writer for checkup workflows."""
        self._checkup_notifier = notifier

    def set_current_session_id(self, sid: str):
        """更新当前活跃会话 ID（由 main.py 在用户发消息/切换会话时调用）"""
        self._current_session_id = sid

    # ── WebSocket 连接管理 ──────────────────────────────

    def add_connection(self, ws: WebSocket):
        self._connections.add(ws)

    def remove_connection(self, ws: WebSocket):
        self._connections.discard(ws)

    def get_connections(self) -> Set[WebSocket]:
        return self._connections

    # ── 任务 CRUD ───────────────────────────────────────

    def create_task(self, req: TaskCreateRequest) -> ScheduledTask:
        """创建定时任务"""
        task = db.create_task(req)
        logger.info(f"定时任务已创建: {task.title} (id={task.id})")
        return task

    def update_task(self, task_id: str, updates: dict) -> Optional[ScheduledTask]:
        """更新任务"""
        task = db.update_task(task_id, updates)
        if task:
            logger.info(f"定时任务已更新: {task.title} (id={task.id})")
        return task

    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        result = db.delete_task(task_id)
        if result:
            logger.info(f"定时任务已删除: {task_id}")
        return result

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务"""
        return db.get_task(task_id)

    def list_tasks(self, include_inactive: bool = False) -> List[ScheduledTask]:
        """列出任务"""
        return db.list_tasks(include_inactive)

    # ── 到期检查与触发 ──────────────────────────────────

    async def check_due_tasks(self) -> List[ScheduledTask]:
        """
        检查所有到期的活跃任务（由 APScheduler 每 30 秒调用）

        Returns:
            本次新触发的任务列表
        """
        # 防并发：如果上一次检查还未完成，跳过本次（避免重叠的 LLM 调用和重复消息）
        if self._due_check_lock.locked():
            print("[TASK_SCHEDULER] check_due_tasks 上次未完成，跳过本次", flush=True)
            return []
        async with self._due_check_lock:
            return await self._check_due_tasks_impl()

    @ordinary
    async def _check_due_tasks_impl(self) -> List[ScheduledTask]:
        """check_due_tasks 的实际实现（由锁保护）"""
        due = db.get_due_tasks()
        if due:
            print(f"[TASK_SCHEDULER] check_due_tasks: 发现 {len(due)} 个到期任务", flush=True)
        triggered: List[ScheduledTask] = []
        message_coros = []
        datetime_task_ids = []

        if due:
            now_dt = datetime.now()
            for task in due:
                try:
                    # Self-task 保鲜检查：超时静默跳过
                    if task.task_type == "self_task":
                        config = task.trigger_config or {}
                        dt_str = config.get("datetime", "")
                        if dt_str:
                            try:
                                trigger_time = datetime.fromisoformat(dt_str)
                                window = config.get("trigger_window_seconds", task.trigger_window_seconds or 300)
                                elapsed = (now_dt - trigger_time).total_seconds()
                                if elapsed > window:
                                    print(
                                        f"[SELF_TASK] 过期跳过: {task.title} "
                                        f"(延迟{elapsed:.0f}s > 窗口{window}s)",
                                        flush=True
                                    )
                                    db.update_task(task.id, {"is_active": False})
                                    continue
                            except (ValueError, TypeError):
                                pass

                    now = now_dt.isoformat()
                    print(f"[TASK_SCHEDULER] 触发任务: {task.title} (type={task.trigger_type}, notify={task.notify_method})", flush=True)

                    # 始终注入聊天消息 + 广播 task_trigger（前端自行判断是否弹卡片）
                    message_coros.append(self._handle_message_task(task))

                    # 标记已触发
                    db.update_task(task.id, {"last_triggered_at": now})
                    triggered.append(task)

                    # datetime 一次性任务：收集待失活
                    if task.trigger_type == "datetime":
                        datetime_task_ids.append(task.id)
                except Exception as e:
                    logger.error(f"触发定时任务失败: {task.id} - {e}")

            # 等待所有消息推送完成
            if message_coros:
                await asyncio.gather(*message_coros, return_exceptions=True)

            # datetime 一次性任务触发后自动失活
            for tid in datetime_task_ids:
                db.update_task(tid, {"is_active": False})
                logger.info(f"一次性任务已自动失活: {tid}")

        # 附带检查 pending reminders（回复复查机制）
        await self._check_pending_reminders()

        return triggered

    @routing_scope
    async def _handle_message_task(self, task: ScheduledTask):
        """
        处理需要发消息的任务：

        reminder/message: LLM 文本 → 弹窗 → 注入聊天 → pending 复查 → 广播
        self_task:        Pro+tools → 注入聊天 → 广播（不弹窗、不复查、不手机推送）
                          ↑ 查岗是AI自己收集信息，单向通知，不要求用户回复
        """
        is_self = task.task_type == "self_task"
        thinking = ""
        model_source = None
        band_touch_request = None
        band_touch_parse_error = ""

        # A bedtime reminder has no purpose once the authoritative current state
        # already says 人类伙伴 is asleep. Suppress the whole delivery occurrence so
        # it cannot create chat messages, cards, phone alerts or a recheck debt.
        if not is_self and _is_bedtime_reminder(task):
            try:
                from wander_manager.user_status import UserStatus, get_user_status
                if get_user_status() == UserStatus.SLEEPING:
                    logger.info("[REMINDER] 人类伙伴已睡，静默跳过睡觉提醒: %s", task.title)
                    return
            except Exception:
                logger.debug("bedtime reminder sleep-state check failed", exc_info=True)

        # 1. 生成消息（self_task 走 Pro+tools 执行器）
        if is_self and self._self_task_executor:
            executed = await self._execute_self_task(task)
            if isinstance(executed, dict):
                message = str(executed.get("message") or task.message_content or task.title)
                thinking = str(executed.get("thinking") or "")
                model_source = executed.get("model_source")
                checkup = executed.get("checkup")
                if checkup and self._checkup_notifier:
                    try:
                        await self._checkup_notifier(
                            self._current_session_id or "", task.id,
                            event_type="checkup_observation", metadata=checkup,
                        )
                    except Exception as exc:
                        logger.warning("[CHECKUP] durable receipt failed: %s", exc)
            else:
                message = executed
        else:
            generated = await self._generate_ai_message(task)
            if isinstance(generated, dict):
                message = str(generated.get("message") or task.message_content or task.title)
                thinking = str(generated.get("thinking") or "")
                model_source = generated.get("model_source")
                band_touch_request = generated.get("band_touch_request")
                band_touch_parse_error = str(generated.get("band_touch_parse_error") or "")
            else:
                message = str(generated or task.message_content or task.title)

        from visible_reply import clean_visible_reply
        message = clean_visible_reply(message)
        if not message:
            logger.warning("[REMINDER] 没有可见正文，跳过本次投递")
            return

        # 2. Ordinary reminders get one occurrence identity before any delivery.
        # The same identity is used by chat, live cards and reconnect backfill.
        pending_record = None
        if not is_self:
            pending_record = self._record_pending_reminder(task, message, reminder_type="task")

        tool_calls = []
        if not is_self and (band_touch_request is not None or band_touch_parse_error):
            tool_calls = await self._execute_band_touch_request(
                band_touch_request, band_touch_parse_error,
            )

        # 3. 注入聊天管道（self_task 不带 ⏰ 前缀）
        injected = self._chat_injector is not None
        if self._chat_injector:
            try:
                occurrence_id = (pending_record or {}).get("id") or task.id
                injected = await self._chat_injector(
                    message,
                    is_recheck=False,
                    msg_id=occurrence_id,
                    is_reminder=not is_self,
                    thinking=thinking,
                    model_source=model_source,
                    tool_calls=tool_calls,
                )
            except Exception as e:
                logger.warning(f"聊天注入失败: {e}")
                injected = False
        if injected is False:
            if pending_record:
                db.resolve_pending_reminder(pending_record["id"], workflow_state="delivery_failed")
            logger.warning("[REMINDER] 权威聊天消息未落库，停止本次通知与复查: %s", task.id)
            return

        # 4. PC 弹窗也只在权威消息落库后发送一次。
        if not is_self and "popup" in (task.notify_method or []):
            try:
                asyncio.create_task(send_desktop_notification_async(
                    title=f"⏰ {task.title}",
                    message=message[:120],
                ))
            except Exception:
                pass

        # 5. 手机通知由 ReminderInjector 在权威消息落库后统一推送一次。

        # 6. 广播（仅 reminder/message 发 task_trigger 创卡片；self_task 已通过 _chat_injector 的 chat_reload 送达前端）
        if not is_self:
            payload = {
                "type": "task_trigger",
                "task": {
                    "id": (pending_record or {}).get("id") or task.id,
                    "task_id": task.id,
                    "triggered_at": (pending_record or {}).get("triggered_at") or datetime.now().isoformat(),
                    "title": task.title,
                    "description": task.description,
                    "reason": task.reason,
                    "ai_context": task.ai_context,
                    "notify_method": task.notify_method,
                    "alarm_enabled": task.alarm_enabled,
                    "message_content": message,
                    "task_type": task.task_type,
                    "tool_calls": tool_calls,
                    "thinking": thinking or None,
                },
            }
            await self._broadcast(payload)

        # 可选手机闹钟与提醒消息同一时刻触发。闹钟按掉后由手机维护
        # 5 分钟宽限期；期间任意聊天回复都会结束本次提醒。
        if not is_self and getattr(task, "alarm_enabled", False) and pending_record:
            await self._start_mobile_alarm(pending_record)

    def _record_pending_reminder(self, task: ScheduledTask, message: str, reminder_type: str = "task"):
        """记录待复查提醒"""
        try:
            pending = PendingReminder(
                id=str(uuid.uuid4()),
                task_id=task.id,
                task_title=task.title,
                reason=task.reason or "",
                ai_context=task.ai_context or "",
                session_id=self._current_session_id or "",
                original_message=message,
                triggered_at=datetime.now().isoformat(),
                recheck_stage=0,
                resolved=0,
                reminder_type=reminder_type,
            )
            db.create_pending_reminder(pending)
            print(f"[RECHECK] 已记录 pending: {task.title} (id={pending.id}) type={reminder_type} session={self._current_session_id}", flush=True)
            return db.get_pending_reminder(pending.id) or pending.model_dump()
        except Exception as e:
            print(f"[RECHECK] 记录失败: {e}", flush=True)
            logger.warning(f"记录 pending reminder 失败: {e}")
            return None

    async def _check_pending_reminders(self):
        """检查所有待处理提醒，决定是否触发复查。每次 check_due_tasks 末尾附带调用。"""
        now = datetime.now()
        stage0 = db.get_pending_reminders(stage=0, resolved=0)
        stage1 = db.get_pending_reminders(stage=1, resolved=0)
        if stage0 or stage1:
            print(f"[RECHECK] pending: stage0={len(stage0)} stage1={len(stage1)}", flush=True)

        # stage=0 → stage=1: 普通提醒复查；不一致查岗直接升级手机闹钟。
        for record in stage0:
            try:
                if (record.get("workflow_state") or "pending") != "pending":
                    continue
                triggered_at = datetime.fromisoformat(record["triggered_at"])
                elapsed = (now - triggered_at).total_seconds()
                print(f"[RECHECK] stage0 record: {record.get('task_title','?')} elapsed={elapsed:.0f}s", flush=True)
                if elapsed >= INCONSISTENCY_ALARM_AFTER_SECONDS:
                    if record.get("reminder_type") == "inconsistency":
                        print(f"[CHECKUP] → 5分钟无回复，升级手机闹钟 for {record.get('task_title','?')}", flush=True)
                        await self._start_inconsistency_alarm(record)
                    else:
                        print(f"[RECHECK] → 触发复查 stage=1 for {record.get('task_title','?')}", flush=True)
                        await self._send_recheck_message(record, stage=1)
            except Exception as e:
                logger.error(f"复查 stage0→1 失败: {e}")

        # stage=1 → stage=2: 总共 10 分钟未回复
        for record in stage1:
            try:
                if (record.get("workflow_state") or "pending") != "pending":
                    continue
                # 兼容升级前已经进入 stage=1 的不一致事件：不再发送第二条
                # 碎碎念，直接进入同一手机闹钟升级路径。
                if record.get("reminder_type") == "inconsistency":
                    await self._start_inconsistency_alarm(record)
                    continue
                triggered_at = datetime.fromisoformat(record["triggered_at"])
                elapsed = (now - triggered_at).total_seconds()
                if elapsed >= 600:
                    print(f"[RECHECK] → 触发复查 stage=2 for {record.get('task_title','?')}", flush=True)
                    await self._send_recheck_message(record, stage=2)
            except Exception as e:
                logger.error(f"复查 stage1→2 失败: {e}")

        # 手机端断线时，宽限期事件可能无法即时回传；后端按本地时间兜底处理。
        try:
            for record in db.get_due_grace_reminders(now.isoformat()):
                await self._finalize_mobile_alarm(record, "grace_expired")
        except Exception as e:
            logger.error(f"查岗闹钟宽限期处理失败: {e}")

    @ordinary
    async def _send_recheck_message(self, record: dict, stage: int):
        """发送复查消息；查岗第二次失败转入手机闹钟，不再记账。"""
        original_message = record.get("original_message", "")
        task_title = record.get("task_title", "")
        reason = record.get("reason", "")
        ai_context = record.get("ai_context", "")
        reminder_type = record.get("reminder_type", "task")
        print(f"[RECHECK] _send_recheck stage={stage} title={task_title} type={reminder_type}", flush=True)

        reminder_id = record.get("id", "")
        # 先领取状态，再生成/注入消息。旧代码在副作用之后才做乐观锁检查，
        # 两个调度轮次同时进入时会重复发消息甚至重复记账。
        target_state = "alarm_pending" if stage == 2 and reminder_type == "inconsistency" else "pending"
        if not db.claim_pending_state(
            reminder_id,
            "pending",
            target_state,
            {"recheck_stage": stage},
        ):
            print(f"[RECHECK] 跳过：事件已被其他轮次领取 (id={reminder_id}, stage={stage})", flush=True)
            return

        # 生成复查消息
        message = original_message  # fallback
        thinking = ""
        model_source = None
        band_touch_request = None
        band_touch_parse_error = ""
        if self._recheck_llm_generator:
            try:
                generated = await self._recheck_llm_generator(
                    original_message, task_title, reason, ai_context, stage, reminder_type
                )
                if isinstance(generated, dict):
                    message = str(generated.get("message") or message)
                    thinking = str(generated.get("thinking") or "")
                    model_source = generated.get("model_source")
                    band_touch_request = generated.get("band_touch_request")
                    band_touch_parse_error = str(generated.get("band_touch_parse_error") or "")
                else:
                    message = str(generated or message)
                print(f"[RECHECK] LLM生成复查消息: {message[:60]}...", flush=True)
            except Exception as e:
                print(f"[RECHECK] LLM生成失败: {e}", flush=True)
                logger.error(f"生成复查消息失败: {e}")

        # LLM 生成期间用户可能已经回复；状态已结束时丢弃这次过时复查。
        latest = db.get_pending_reminder(reminder_id)
        if not latest or latest.get("resolved"):
            return

        tool_calls = []
        if band_touch_request is not None or band_touch_parse_error:
            tool_calls = await self._execute_band_touch_request(
                band_touch_request, band_touch_parse_error,
            )

        # 注入聊天管道（is_recheck=True → 无 ⏰ 前缀，无卡片）
        if self._chat_injector:
            try:
                # task_id belongs to a recurring schedule and is reused every
                # day. The pending reminder ID identifies this exact occurrence.
                msg_id = f"{reminder_id}_recheck_{stage}"
                injected = await self._chat_injector(
                    message,
                    is_recheck=True,
                    msg_id=msg_id,
                    is_reminder=True,
                    event_type="inconsistency" if reminder_type == "inconsistency" else "reminder",
                    thinking=thinking,
                    model_source=model_source,
                    tool_calls=tool_calls,
                )
                if injected is False:
                    db.claim_pending_state(
                        reminder_id, target_state, "pending",
                        {"recheck_stage": stage - 1},
                    )
                    logger.warning("[RECHECK] 复查消息未落库，保留待重试: %s stage=%s", reminder_id, stage)
                    return
                print(f"[RECHECK] 复查消息已注入", flush=True)
            except Exception as e:
                print(f"[RECHECK] 注入失败: {e}", flush=True)
                logger.warning(f"复查消息注入失败: {e}")
                db.claim_pending_state(
                    reminder_id, target_state, "pending",
                    {"recheck_stage": stage - 1},
                )
                return
        else:
            db.claim_pending_state(
                reminder_id, target_state, "pending",
                {"recheck_stage": stage - 1},
            )
            logger.warning("[RECHECK] 没有聊天注入器，保留待重试: %s stage=%s", reminder_id, stage)
            return

        # 手机通知已由 ReminderInjector 在消息落库后统一推送一次。

        # stage==2：普通日程提醒保留原账本；不一致查岗进入手机闹钟链路。
        if stage == 2:
            if reminder_type == "inconsistency":
                await self._schedule_mobile_alarm(record)
            else:
                try:
                    from ledger_manager import get_global_ledger_manager
                    ledger = get_global_ledger_manager()
                    debt_type = "wander_check"
                    content = f"「{task_title}」提醒被忽略了"
                    ledger.add(
                        content=content,
                        debt_type=debt_type,
                        source_context=original_message[:200],
                        session_id=record.get("session_id", ""),
                    )
                    logger.info(f"已将复查记录到小账本: {task_title} (普通提醒)")
                except Exception as e:
                    logger.error(f"记录小账本失败: {e}")
                db.resolve_pending_reminder(reminder_id)

        # 静默通知前端刷新（无卡片）
        await self._broadcast({
            "type": "chat_reload",
            "reason": "recheck",
            "stage": stage,
        })

    async def on_user_replied(self):
        """用户回复时只结束最近一条事件，并取消该事件的手机闹钟。"""
        try:
            record = db.get_latest_pending_reminder(self._current_session_id)
            if not record:
                return
            if db.resolve_pending_reminder(record["id"]):
                state = record.get("workflow_state", "")
                if state in {"alarm_pending", "alarm_scheduled", "alarm_fired", "grace_wait"}:
                    await self._cancel_mobile_alarm(record)
                print(f"[RECHECK] 用户回复，已结束当前事件: {record.get('task_title', record['id'])}", flush=True)
        except Exception as e:
            logger.error(f"结束当前 pending 事件失败: {e}")

    async def _call_mobile_relay(self, request_id: str, tool: str, params: dict) -> dict:
        """调用手机中继；短暂断线时用同一事件 ID 重试一次。"""
        last_result: dict = {"success": False, "error": "mobile relay unavailable"}
        for attempt in range(2):
            try:
                from shared_state import get_mobile_relay_callback
                callback = get_mobile_relay_callback()
            except Exception:
                callback = None
            if callback:
                try:
                    result = await callback(request_id, tool, params)
                    if isinstance(result, dict):
                        last_result = result
                        if result.get("success"):
                            return result
                except Exception as e:
                    last_result = {"success": False, "error": str(e)}
            if attempt == 0:
                await asyncio.sleep(2.0)
        return last_result

    async def _start_mobile_alarm(self, record: dict):
        """Atomically move a reminder/checkup event into the native alarm path."""
        reminder_id = record.get("id", "")
        if not db.claim_pending_state(
            reminder_id,
            "pending",
            "alarm_pending",
            {"recheck_stage": 1},
        ):
            return
        latest = db.get_pending_reminder(reminder_id) or record
        await self._schedule_mobile_alarm(
            latest,
            delay_seconds=MOBILE_ALARM_DEVICE_DELAY_SECONDS,
        )

    async def _start_inconsistency_alarm(self, record: dict):
        """Compatibility entrypoint for the delayed inconsistency workflow."""
        await self._start_mobile_alarm(record)

    async def _schedule_mobile_alarm(self, record: dict, *, delay_seconds: int = 300):
        """Ask the phone to create a one-shot native reminder alarm."""
        reminder_id = record.get("id", "")
        is_checkup = record.get("reminder_type") == "inconsistency"
        trigger_at_ms = int((time.time() + max(1, int(delay_seconds))) * 1000)
        trigger_at = datetime.fromtimestamp(trigger_at_ms / 1000).isoformat()
        result = await self._call_mobile_relay(
            f"checkup_alarm_{reminder_id}",
            "mobile_alarm",
            {
                "event_id": reminder_id,
                "trigger_at_ms": trigger_at_ms,
                "title": "MIRROW查岗" if is_checkup else "MIRROW提醒",
                "body": record.get("task_title", "查岗提醒" if is_checkup else "日程提醒"),
            },
        )
        if result.get("success"):
            payload = result.get("result") if isinstance(result.get("result"), dict) else result
            scheduled_updates = {
                "alarm_id": str(payload.get("alarm_id") or reminder_id),
                "alarm_scheduled_at": datetime.now().isoformat(),
                "alarm_trigger_at": trigger_at,
                "last_error": "",
                "alarm_cancel_pending": 0,
            }
            # 用户可能在手机返回成功前已经回复。只有事件仍处于
            # alarm_pending 才能提交闹钟状态；否则立刻撤销刚创建的本地闹钟，
            # 避免“已回复但五分钟后仍响铃”的竞态。
            if not db.claim_pending_state(
                reminder_id, "alarm_pending", "alarm_scheduled", scheduled_updates
            ):
                current = db.get_pending_reminder(reminder_id)
                current_state = current.get("workflow_state", "") if current else ""
                if current and not current.get("resolved") and current_state in {
                    "alarm_scheduled", "alarm_fired", "grace_wait"
                }:
                    # 另一轮相同请求已经提交了同一个本地闹钟，保持它。
                    logger.info(f"[CHECKUP] 闹钟已由其他轮次提交，保留: event={reminder_id}")
                    return
                await self._call_mobile_relay(
                    f"checkup_alarm_cancel_{reminder_id}",
                    "mobile_alarm_cancel",
                    {
                        "event_id": reminder_id,
                        "alarm_id": scheduled_updates["alarm_id"],
                    },
                )
                if current and current.get("resolved"):
                    db.update_pending_reminder(reminder_id, {
                        "alarm_id": scheduled_updates["alarm_id"],
                        "alarm_cancel_pending": 1,
                    })
                logger.info(f"[CHECKUP] 闹钟返回时事件已结束，已请求撤销: event={reminder_id}")
                return
            logger.info(f"[CHECKUP] 手机闹钟已设置: event={reminder_id} trigger={trigger_at}")
            return

        error = str(result.get("error") or result.get("content") or "手机闹钟设置失败")
        db.update_pending_reminder(reminder_id, {"last_error": error})
        logger.warning(f"[CHECKUP] 手机闹钟设置失败: event={reminder_id} error={error}")
        await self._finalize_mobile_alarm(record, "alarm_setup_failed")

    async def schedule_test_alarm(self, delay_seconds: int = 30) -> dict:
        """Schedule an isolated native alarm without creating chat/domain state."""
        delay = max(5, min(int(delay_seconds), 300))
        event_id = f"test_checkup_alarm_{uuid.uuid4()}"
        trigger_at_ms = int((time.time() + delay) * 1000)
        result = await self._call_mobile_relay(
            f"{event_id}_schedule",
            "mobile_alarm",
            {
                "event_id": event_id,
                "trigger_at_ms": trigger_at_ms,
                "title": "MIRROW查岗闹钟测试",
                "body": "这是一条独立测试，不会建立查岗事件。",
            },
        )
        return {
            "success": bool(result.get("success")),
            "event_id": event_id,
            "trigger_at": datetime.fromtimestamp(trigger_at_ms / 1000).isoformat(),
            "error": str(result.get("error") or "") if not result.get("success") else "",
        }

    async def cancel_test_alarm(self, event_id: str) -> dict:
        """Cancel one isolated test alarm by its returned event id."""
        if not str(event_id or "").startswith("test_checkup_alarm_"):
            return {"success": False, "error": "invalid test alarm id"}
        result = await self._call_mobile_relay(
            f"{event_id}_cancel",
            "mobile_alarm_cancel",
            {"event_id": event_id, "alarm_id": event_id},
        )
        return {
            "success": bool(result.get("success")),
            "event_id": event_id,
            "error": str(result.get("error") or "") if not result.get("success") else "",
        }

    async def _cancel_mobile_alarm(self, record: dict):
        """用户已回复时取消尚未完成的手机闹钟/宽限计时。"""
        reminder_id = record.get("id", "")
        result = await self._call_mobile_relay(
            f"checkup_alarm_cancel_{reminder_id}",
            "mobile_alarm_cancel",
            {"event_id": reminder_id, "alarm_id": record.get("alarm_id", "")},
        )
        if result.get("success"):
            db.update_pending_reminder(reminder_id, {
                "alarm_cancel_pending": 0,
                "last_error": "",
            })
        else:
            db.update_pending_reminder(reminder_id, {
                "alarm_cancel_pending": 1,
                "last_error": str(result.get("error") or "手机闹钟取消未送达"),
            })
            logger.info(f"[CHECKUP] 取消手机闹钟未成功（手机可能已断线）: {result.get('error', '')}")

    async def flush_pending_alarm_cancels(self):
        """手机中继重连后补发断线期间未送达的闹钟撤销。"""
        for record in db.get_pending_alarm_cancellations():
            await self._cancel_mobile_alarm(record)

    async def _finalize_mobile_alarm(self, record: dict, outcome: str):
        """Generate one final message after alarm failure/grace expiry, then resolve."""
        reminder_id = record.get("id", "")
        current = db.get_pending_reminder(reminder_id)
        if not current or current.get("resolved"):
            return
        current_state = current.get("workflow_state") or "alarm_pending"
        if current_state != "finalizing":
            if not db.claim_pending_state(reminder_id, current_state, "finalizing"):
                return
            current = db.get_pending_reminder(reminder_id) or current

        original_message = current.get("original_message", "")
        task_title = current.get("task_title", "")
        reason = current.get("reason", "")
        ai_context = current.get("ai_context", "")
        outcome_hint = {
            "alarm_setup_failed": "手机闹钟设置失败，已改为即时反馈",
            "grace_expired": "手机闹钟已按掉，之后五分钟仍没有收到回复",
        }.get(outcome, outcome)
        reminder_type = current.get("reminder_type", "task")
        is_checkup = reminder_type == "inconsistency"
        message = (
            f"人类伙伴，我的查岗闹钟这次没能完成（{outcome_hint}）。"
            if is_checkup
            else f"人类伙伴，提醒到了：{task_title}。{outcome_hint}。"
        )
        thinking = ""
        model_source = None
        band_touch_request = None
        band_touch_parse_error = ""
        if self._recheck_llm_generator:
            try:
                generated = await self._recheck_llm_generator(
                    original_message,
                    task_title,
                    reason,
                    f"{ai_context}\n事件结果：{outcome_hint}",
                    3,
                    reminder_type,
                )
                if isinstance(generated, dict):
                    message = str(generated.get("message") or message)
                    thinking = str(generated.get("thinking") or "")
                    model_source = generated.get("model_source")
                    band_touch_request = generated.get("band_touch_request")
                    band_touch_parse_error = str(generated.get("band_touch_parse_error") or "")
                else:
                    message = str(generated or message)
            except Exception as e:
                logger.warning(f"[CHECKUP] 最终消息生成失败，使用兜底: {e}")

        # 生成消息期间用户可能已经回复并结束事件；不再把过时的最终反馈
        # 写进对话，也不把已结束事件重新推进。
        latest = db.get_pending_reminder(reminder_id)
        if not latest or latest.get("resolved"):
            return


        tool_calls = []
        if band_touch_request is not None or band_touch_parse_error:
            tool_calls = await self._execute_band_touch_request(
                band_touch_request, band_touch_parse_error,
            )

        if self._chat_injector and message:
            try:
                await self._chat_injector(
                    message,
                    is_recheck=True,
                    msg_id=f"{current.get('task_id') or reminder_id}_outcome",
                    is_reminder=True,
                    event_type="inconsistency" if is_checkup else "reminder",
                    thinking=thinking,
                    model_source=model_source,
                    tool_calls=tool_calls,
                )
            except Exception as e:
                logger.warning(f"[CHECKUP] 最终消息注入失败: {e}")

        db.resolve_pending_reminder(reminder_id)
        if outcome == "alarm_setup_failed":
            await self._broadcast({
                "type": "checkup_alarm_status",
                "status": "failed",
                "event_id": reminder_id,
                "message": "手机闹钟设置失败，已改为聊天提醒。",
            })
        await self._broadcast({
            "type": "chat_reload",
            "reason": "checkup_outcome",
            "event_id": reminder_id,
            "outcome": outcome,
        })

    async def handle_mobile_alarm_event(self, event: dict) -> dict:
        """处理手机端本地闹钟回传，WS 只负责同步状态，不负责计时。"""
        reminder_id = str(event.get("event_id") or "")
        state = str(event.get("state") or "")
        if reminder_id.startswith("test_checkup_alarm_"):
            await self._broadcast({
                "type": "checkup_alarm_status",
                "status": f"test_{state or 'event'}",
                "event_id": reminder_id,
                "message": "查岗闹钟测试事件已由手机回传。",
            })
            return {"success": True, "test": True, "state": state}
        record = db.get_pending_reminder(reminder_id)
        if not record:
            return {"success": False, "error": "unknown checkup event"}
        if record.get("resolved"):
            return {"success": True, "ignored": True}

        occurred_at = str(event.get("occurred_at") or datetime.now().isoformat())
        if state == "fired":
            current_state = record.get("workflow_state") or "alarm_pending"
            if current_state in {"alarm_scheduled", "alarm_pending"}:
                db.claim_pending_state(
                    reminder_id,
                    ["alarm_scheduled", "alarm_pending"],
                    "alarm_fired",
                    {
                        "alarm_trigger_at": str(
                            event.get("trigger_at")
                            or record.get("alarm_trigger_at")
                            or occurred_at
                        )
                    },
                )
            elif current_state not in {"alarm_fired", "grace_wait"}:
                return {"success": False, "error": f"alarm event state conflict: {current_state}"}
        elif state == "dismissed":
            grace_due_at = str(event.get("grace_due_at") or "")
            if not grace_due_at:
                grace_due_at = (datetime.fromisoformat(occurred_at) + timedelta(minutes=5)).isoformat()
            current_state = record.get("workflow_state") or "alarm_pending"
            if current_state in {"alarm_fired", "alarm_pending", "alarm_scheduled"}:
                db.claim_pending_state(
                    reminder_id,
                    ["alarm_fired", "alarm_pending", "alarm_scheduled"],
                    "grace_wait",
                    {
                        "alarm_dismissed_at": occurred_at,
                        "grace_due_at": grace_due_at,
                    },
                )
            elif current_state != "grace_wait":
                return {"success": False, "error": f"alarm event state conflict: {current_state}"}
        elif state == "grace_expired":
            await self._finalize_mobile_alarm(record, "grace_expired")
        elif state == "alarm_failed":
            await self._finalize_mobile_alarm(record, "alarm_setup_failed")
        elif state == "expired":
            # A phone restart/reinstall may restore a local alarm long after it
            # mattered. Expiry closes that event silently; it must never wake AI
            # into a stale follow-up or create a fresh debt message.
            db.resolve_pending_reminder(reminder_id, workflow_state="expired")
        else:
            return {"success": False, "error": f"unknown alarm state: {state}"}
        return {"success": True}

    async def _generate_ai_message(self, task: ScheduledTask) -> str | dict[str, Any]:
        """生成 AI 个性化消息"""
        if self._llm_message_generator:
            try:
                return await self._llm_message_generator(task)
            except Exception as e:
                logger.error(f"LLM 生成消息失败: {e}")

        # 兜底
        return task.message_content or task.description or f"⏰ {task.title}"

    @staticmethod
    async def _execute_band_touch_request(request, parse_error: str = "") -> list[dict[str, Any]]:
        """Execute one optional proactive band gesture without a model follow-up."""
        try:
            from behavior_scheduler.band_touch_tool import BandTouchTool
            from proactive_band_touch import execute_request

            record, _ = await execute_request(BandTouchTool(), request, parse_error)
            return [record] if record is not None else []
        except Exception as exc:
            logger.warning("主动提醒手环动作执行失败: %s", type(exc).__name__)
            return []

    async def _execute_self_task(self, task: ScheduledTask) -> str | dict[str, Any]:
        """执行 self_task：Pro + tools → 自然语言消息"""
        print(f"[SELF_TASK] 开始执行: {task.title} (tool={task.tool_name})", flush=True)
        try:
            if self._self_task_executor:
                message = await self._self_task_executor(task)
                preview = message.get("message", "") if isinstance(message, dict) else message
                print(f"[SELF_TASK] 执行完成: {str(preview)[:80]}...", flush=True)
                return message
            else:
                logger.warning("[SELF_TASK] 无执行器，使用兜底消息")
        except Exception as e:
            logger.error(f"[SELF_TASK] 执行失败: {e}")
        return task.message_content or f"⏰ {task.title}"

    # ── 不一致查岗（供 wander 调用）──────────────────────

    async def inject_inconsistency_check(
        self, status_text: str, activity_description: str,
        status_mismatch_detail: str, session_id: str
    ):
        """不一致查岗：创建可追踪事件并注入首条查岗消息。"""
        # 更新当前 session_id
        if session_id:
            self._current_session_id = session_id

        # 1. 生成 AI 的查岗消息
        generated = await self._generate_inconsistency_message(
            status_text, activity_description, status_mismatch_detail
        )
        message, thinking, band_touch_request, band_touch_parse_error = generated
        model_source = getattr(generated, "model_source", None)
        if not message:
            message = f"人类伙伴，你不是说你在{status_text}吗？我怎么看到{activity_description}..."

        from visible_reply import clean_visible_reply
        message = clean_visible_reply(message)
        if not message:
            return

        # 2. 先落事件，使用稳定 task_id 作为首条消息 ID，断线补发不会重复。
        effective_session_id = session_id or self._current_session_id or ""
        record, created = self._record_inconsistency_reminder(
            status_text, activity_description, status_mismatch_detail, message, effective_session_id
        )
        if not created:
            return

        tool_calls = []
        if band_touch_request is not None or band_touch_parse_error:
            tool_calls = await self._execute_band_touch_request(
                band_touch_request, band_touch_parse_error,
            )

        # 3. 注入聊天管道（不一致消息不带 ⏰，但保留 isReminder 元数据）
        if self._chat_injector:
            try:
                await self._chat_injector(
                    message,
                    is_recheck=True,
                    msg_id=record.get("task_id", record.get("id", "")),
                    is_reminder=True,
                    event_type="inconsistency",
                    thinking=thinking,
                    model_source=model_source,
                    tool_calls=tool_calls,
                )
                print(f"[INCONSISTENCY] 查岗消息已注入: {message[:60]}...", flush=True)
            except Exception as e:
                print(f"[INCONSISTENCY] 注入失败: {e}", flush=True)
                logger.warning(f"查岗消息注入失败: {e}")

        # 4. Every system-initiated checkup has the same durable visible receipt.
        if self._checkup_notifier:
            try:
                await self._checkup_notifier(
                    effective_session_id,
                    record.get("id", record.get("task_id", "")),
                    event_type="checkup_observation",
                    metadata={
                        "intent": "consistency",
                        "origin": "wander",
                        "conclusion": "contradicted",
                        "items": [{
                            "tool": "user_tracking",
                            "description": "行为追踪复判",
                            "status": "success",
                            "result": activity_description[:500],
                            "error": "",
                        }],
                    },
                )
            except Exception as exc:
                logger.warning("[CHECKUP] inconsistency receipt failed: %s", exc)

        # 5. 广播前端刷新
        await self._broadcast({"type": "chat_reload", "reason": "inconsistency_check"})

    async def _generate_inconsistency_message(
        self, status_text: str, activity_description: str, status_mismatch_detail: str
    ) -> tuple[str, str, Any, str]:
        """用 Flash 生成有 AI 人设的查岗消息（system 走 REMINDER_PUSH recipe，_scenario=查岗）。"""
        persona = ""
        if self._persona_provider:
            try:
                persona = self._persona_provider()
            except Exception:
                pass

        detail_line = ""
        if status_mismatch_detail:
            detail_line = f"\n细节：{status_mismatch_detail}"

        task_info = (
            f"查岗事由（系统检测，已连续两次确认，不是误判）：\n"
            f"- 人类伙伴说她{status_text}\n"
            f"- 但屏幕显示{activity_description}{detail_line}"
        )

        # REMINDER_PUSH：稳定身份前缀 + 对话历史 + 本次查岗事实尾段。
        system_ctx = ""
        messages = []
        built_result = None
        try:
            from context_builder import ContextBuilder
            result = await ContextBuilder.build("REMINDER_PUSH",
                _scenario="查岗",
                persona=persona,
                mood="",
                task_info=task_info,
                context_query=task_info,
                session_id=self._current_session_id or "",
            )
            system_ctx = result.system_content
            from context_builder.assembly import assemble_context_result
            messages = assemble_context_result(result)
            built_result = result
        except Exception as e:
            logger.warning(f"查岗 REMINDER_PUSH 构建失败，回退裸拼: {e}")
        if not messages:
            system_ctx = f"{persona}\n{task_info}"
            messages = [{"role": "system", "content": system_ctx}]

        prompt = """你注意到了最后一条查岗事件中的不一致。
请用第一人称自然地问人类伙伴——带一点调侃和小心思，不是质问也不是生气。
像日常聊天一样自然，20-50字。直接输出消息内容，不要引号不要解释。"""
        from proactive_band_touch import output_instruction, split_output
        messages.append({
            "role": "system",
            "content": prompt + "\n" + output_instruction(),
        })

        try:
            from llm_client import call_llm_api_flash
            result = await call_llm_api_flash(messages, enable_thinking=True, usage_tag="alarm_closure")
            raw_content = result.get("content", "") if isinstance(result, dict) else str(result or "")
            content, band_request, band_parse_error = split_output(raw_content)
            if built_result is not None:
                from context_builder import commit_context_result
                commit_context_result(built_result)
            reasoning = str(result.get("reasoning") or "") if isinstance(result, dict) else ""
            from api_sources.provenance import GeneratedTuple
            return GeneratedTuple((content.strip(), reasoning.strip(), band_request, band_parse_error), result.get("model_source") if isinstance(result, dict) and content.strip() else None)
        except Exception as e:
            logger.error(f"生成查岗消息失败: {e}")
            return "", "", None, ""

    def _record_inconsistency_reminder(
        self, status_text: str, activity_description: str,
        status_mismatch_detail: str, message: str, session_id: str
    ):
        """记录不一致查岗事件，返回 ``(record, created)``。"""
        try:
            # 检查是否已有同一个状态的未解决不一致记录
            existing = db.get_pending_reminders(resolved=0)
            for rec in existing:
                if rec.get("reminder_type") == "inconsistency" and status_text in rec.get("reason", ""):
                    print(f"[INCONSISTENCY] 跳过：已有未解决的查岗 pending (stage={rec.get('recheck_stage')})", flush=True)
                    return rec, False
            event_id = str(uuid.uuid4())
            pending = PendingReminder(
                id=event_id,
                task_id=f"inconsistency_{event_id}",
                task_title=f"查岗：声称{status_text} vs 实际{activity_description}",
                reason=status_mismatch_detail or f"声称{status_text}但实际{activity_description}",
                ai_context=f"status={status_text} activity={activity_description}",
                session_id=session_id or self._current_session_id or "",
                original_message=message,
                triggered_at=datetime.now().isoformat(),
                recheck_stage=0,
                resolved=0,
                reminder_type="inconsistency",
            )
            db.create_pending_reminder(pending)
            print(f"[INCONSISTENCY] 已记录 pending: status={status_text} id={pending.id}", flush=True)
            return db.get_pending_reminder(pending.id) or pending.model_dump(), True
        except Exception as e:
            print(f"[INCONSISTENCY] 记录失败: {e}", flush=True)
            logger.warning(f"记录 inconsistency reminder 失败: {e}")
            return {}, False

    async def _broadcast(self, payload: dict):
        """广播消息到所有 WebSocket 客户端"""
        ptype = payload.get("type", "?")
        if not self._connections:
            print(f"[TASK_WS] _broadcast({ptype}): 无连接，消息丢弃", flush=True)
            return

        print(f"[TASK_WS] _broadcast({ptype}) → {len(self._connections)} 个客户端", flush=True)

        stale = set()
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.add(ws)

        self._connections.difference_update(stale)

    async def broadcast_update(self):
        """广播任务列表变更通知"""
        payload = {"type": "task_list_changed"}
        await self._broadcast(payload)


# ── 全局单例 ────────────────────────────────────────────

_global_task_manager: Optional[TaskManager] = None


def get_initialized_task_manager() -> Optional[TaskManager]:
    """Read the existing manager without constructing storage or starting work."""
    return _global_task_manager


def get_global_task_manager() -> TaskManager:
    global _global_task_manager
    if _global_task_manager is None:
        _global_task_manager = TaskManager()
    return _global_task_manager


def init_global_task_manager() -> TaskManager:
    manager = get_global_task_manager()
    manager.initialize()
    return manager
