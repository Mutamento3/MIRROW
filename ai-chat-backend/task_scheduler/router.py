"""
定时任务管理器 - FastAPI 路由

提供 RESTful CRUD 接口和 WebSocket 推送端点。
"""
import json
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .models import ScheduledTask, TaskCreateRequest, TaskUpdateRequest
from .manager import get_global_task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["task_scheduler"])


# ── REST CRUD ───────────────────────────────────────────


@router.get("", response_model=List[ScheduledTask])
async def list_tasks(include_inactive: bool = False):
    """获取所有定时任务"""
    from .database import DB_PATH
    manager = get_global_task_manager()
    tasks = manager.list_tasks(include_inactive=include_inactive)
    # 在最后一个任务的 description 里附加 DB_PATH，方便前端调试
    if tasks:
        import copy
        tasks = [copy.copy(t) for t in tasks]
        tasks[-1].description = f"{tasks[-1].description} [DB:{DB_PATH}]"
    return tasks


@router.post("", response_model=ScheduledTask)
async def create_task(req: TaskCreateRequest):
    """创建定时任务"""
    manager = get_global_task_manager()
    task = manager.create_task(req)
    await manager.broadcast_update()
    return task


@router.get("/pending")
async def get_pending_reminders():
    """查询当前未处理的提醒（供前端 WebSocket 重连后补发）"""
    from .database import get_pending_reminders as db_get_pending
    try:
        pending = db_get_pending(resolved=0)
        return {"success": True, "pending": pending, "count": len(pending)}
    except Exception as e:
        return {"success": False, "error": str(e), "pending": []}


@router.post("/debug/checkup-alarm")
async def schedule_checkup_alarm_test(delay_seconds: int = 30):
    """Create a short-lived native alarm test without a pending reminder."""
    result = await get_global_task_manager().schedule_test_alarm(delay_seconds)
    if not result.get("success"):
        raise HTTPException(status_code=503, detail=result.get("error") or "手机闹钟测试创建失败")
    return result


@router.delete("/debug/checkup-alarm/{event_id}")
async def cancel_checkup_alarm_test(event_id: str):
    result = await get_global_task_manager().cancel_test_alarm(event_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "手机闹钟测试取消失败")
    return result


@router.get("/{task_id}", response_model=ScheduledTask)
async def get_task(task_id: str):
    """获取单个定时任务"""
    manager = get_global_task_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.put("/{task_id}", response_model=ScheduledTask)
async def update_task(task_id: str, req: TaskUpdateRequest):
    """更新定时任务"""
    manager = get_global_task_manager()
    existing = manager.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="任务不存在")

    updates = {k: v for k, v in req.model_dump(exclude_none=True).items() if v is not None}
    if not updates:
        return existing

    task = manager.update_task(task_id, updates)
    await manager.broadcast_update()
    return task


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """删除定时任务"""
    manager = get_global_task_manager()
    if not manager.delete_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    await manager.broadcast_update()
    return {"success": True}


@router.post("/{task_id}/trigger")
async def trigger_task_manual(task_id: str):
    """手动触发定时任务"""
    manager = get_global_task_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    from .database import update_task as db_update
    now = datetime.now().isoformat()

    # 触发通知
    from .notifier import send_desktop_notification
    if "popup" in task.notify_method:
        send_desktop_notification(
            title=f"⏰ {task.title}",
            message=task.message_content or task.description or task.title,
        )

    if "message" in task.notify_method or task.notify_method:
        payload = {
            "type": "task_trigger",
            "task": {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "notify_method": task.notify_method,
                "message_content": task.message_content,
                "task_type": task.task_type,
            },
        }
        await manager._broadcast(payload)

    # 先通知，再标记已触发（避免通知失败导致任务被跳过）
    db_update(task_id, {"last_triggered_at": now})

    return {"success": True, "message": f"任务 '{task.title}' 已触发"}




# ── WebSocket ───────────────────────────────────────────


@router.websocket("/ws")
async def task_websocket(websocket: WebSocket):
    """定时任务推送 WebSocket（持久连接）"""
    await websocket.accept()
    manager = get_global_task_manager()
    manager.add_connection(websocket)
    logger.info(f"任务 WebSocket 客户端已连接，当前连接数: {len(manager.get_connections())}")

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"任务 WebSocket 异常: {e}")
    finally:
        manager.remove_connection(websocket)
        logger.info(f"任务 WebSocket 客户端已断开")
