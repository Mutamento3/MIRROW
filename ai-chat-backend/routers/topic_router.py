"""话题管理 API 路由 — 从 main.py 提取"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from session_manager import get_global_session_manager
from routers.shared_models import TopicStatusResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["topic"])


# ═══════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════

class TopicConfigRequest(BaseModel):
    """[废弃] 话题检测配置——被动检测已禁用，话题仅由"聊到这里"手动结束"""
    max_drift_rounds: Optional[int] = None
    timeout_minutes: Optional[int] = None


# ═══════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════

@router.get("/api/topic/status/{session_id}", response_model=TopicStatusResponse)
async def get_topic_status(session_id: str):
    """获取话题状态（供前端侧边栏显示）"""
    import sys
    from summary import topic_state_machine
    status = await topic_state_machine.get_topic_status(session_id)
    return TopicStatusResponse(**status)


@router.post("/api/topic/recalculate/{session_id}", response_model=TopicStatusResponse)
async def recalculate_topic_status(session_id: str):
    """重新计算话题状态（删除消息后调用）"""
    import sys
    from summary import topic_state_machine
    status = await topic_state_machine.recalculate_topic_status(session_id)
    return TopicStatusResponse(**status)


@router.get("/api/messages/summarized/{session_id}")
async def get_summarized_message_ids(session_id: str):
    """获取已总结的消息ID列表（summary_index 已废弃，统一走 OB_Rev）"""
    return {
        "session_id": session_id,
        "summarized_ids": [],
        "total_count": 0
    }


@router.post("/api/topic/start/{session_id}")
async def start_topic(session_id: str):
    """手动开启新话题"""
    import sys
    from summary import topic_state_machine
    await topic_state_machine.start_new_topic(session_id)
    return {
        "success": True,
        "topic_status": await topic_state_machine.get_topic_status(session_id)
    }


@router.post("/api/topic/end/{session_id}")
async def end_topic(session_id: str, reason: str = "manual"):
    """手动结束当前话题（不自动开启新话题，等下次发消息时自然建立）"""
    from day_end_scheduling import task_slot
    async with task_slot('topic_end:' + session_id, None):
        return await _end_topic_once(session_id, reason)


async def _end_topic_once(session_id: str, reason: str):
    from summary import topic_state_machine
    from shared_state import get_on_topic_ended_callback
    from conversation_covenant import get_conversation_covenant_service, get_late_night_hour

    pre_status = await topic_state_machine.get_topic_status(session_id)
    if not pre_status.get('is_active'):
        return {'success': True, 'already_ended': True, 'divider_index': -1,
                'divider_id': '', 'topic_status': pre_status}
    pre_start_time = pre_status.get('start_time', '')

    # 深夜"聊到这里"（≥ 配置的深夜窗口，默认 22:00）预约次日 00:00 结束——保证"一天一话题"：
    # 00:00 前话题保持活跃（AI 推送归当天话题），00:00 后结束开新话题归新一天。
    # 约定倒计时的到期已由 covenant._wait_and_expire 处理（reason="covenant" 不走此分支）。
    if reason == "manual" and datetime.now().hour >= get_late_night_hour():
        _covenant = get_conversation_covenant_service()
        if await _covenant.reserve_end(session_id):
            return {
                "success": True,
                "reserved": True,
                "message": "已预约次日 00:00 自动结束",
                "divider_index": -1,
                "divider_id": "",
                "topic_status": await topic_state_machine.get_topic_status(session_id),
            }

    # 在 end_topic 清理状态前捕获 start_time
    from shared_state import get_pending_tasks_lock

    # Never commit a divider if the continuation callback is unavailable.
    # Otherwise startup day-change recovery can silently skip cognition.
    continuation = get_on_topic_ended_callback() if pre_start_time else None
    if pre_start_time and continuation is None:
        raise HTTPException(status_code=503, detail="话题结束服务尚未就绪，请稍后重试")

    # 防重复：上一轮整理还在跑（running）时拒绝新的结束请求，避免 clear 冲掉在跑任务
    from shared_state import _pending_tasks as _pt
    async with get_pending_tasks_lock():
        busy = any(v.get("status") == "running" for v in _pt.values())
    if busy:
        logger.info(f"话题结束被拒：上一轮整理仍在进行 ({session_id[:20]}...)")
        return {
            "success": False,
            "busy": True,
            "message": "上一轮整理还没跑完，稍等再结束",
            "topic_status": await topic_state_machine.get_topic_status(session_id),
        }

    # 新一轮话题结束 → 清空旧的任务状态
    async with get_pending_tasks_lock():
        _pt.clear()

    success, divider_index = await topic_state_machine.end_topic(session_id, reason)

    # 在 session_manager 中添加 divider 消息
    divider_id = ""
    if success:
        try:
            sm = get_global_session_manager()
            session = await sm.get_session(session_id)
            if session and session.messages:
                divider_id = f"divider_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                divider_msg_dict = {
                    "id": divider_id,
                    "role": "divider",
                    "content": "-刚刚聊的话题结束了-",
                    "timestamp": datetime.now().isoformat(),
                }
                all_messages = [m.model_dump() for m in session.messages] + [divider_msg_dict]
                await sm.update_session(session_id, {"messages": all_messages})
                logger.info(
                    "话题结束，已添加 divider: session=%s divider_id=%s reason=%s",
                    session_id[:20], divider_id, reason,
                )
                # 同步写入 SQLite
                try:
                    from event_chronicle import get_global_chronicle
                    chronicle = get_global_chronicle()
                    from topic_end_maintenance import resolve_topic_date
                    _div_active_date = resolve_topic_date(pre_start_time)
                    chronicle.add_conversation_message(
                        active_date=_div_active_date,
                        calendar_date=datetime.now().strftime("%Y-%m-%d"),
                        session_id=session_id,
                        timestamp=divider_msg_dict["timestamp"],
                        role="divider",
                        content=divider_msg_dict["content"],
                        message_id=divider_id,
                        event_type="topic_end",
                    )
                except Exception as e2:
                    logger.error(f"Divider SQLite 写入失败: {e2}")
        except Exception as e:
            logger.error(f"添加 divider 消息到 session 失败: {e}")

    # 话题结束时的记忆整合 + 日记 + 统一分析 + 人格演化
    if success and pre_start_time:
        asyncio.create_task(continuation(session_id, reason, pre_start_time))

    return {
        "success": success,
        "divider_index": divider_index,
        "divider_id": divider_id,
        "reason": reason,
        "topic_status": await topic_state_machine.get_topic_status(session_id)
    }
