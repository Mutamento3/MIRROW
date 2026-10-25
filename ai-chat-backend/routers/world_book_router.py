"""世界书 API 路由 — 从 main.py 提取"""

import json
import os
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from cognition.books import revision
from dataclasses import asdict
from fastapi import APIRouter, Request

import frontmatter

from time_utils import now_iso
from llm_client import call_llm_flash_for_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/world-book", tags=["world-book"])


# ═══════════════════════════════════════════════════════════
# 世界书建议检测（fire-and-forget）
# ═══════════════════════════════════════════════════════════

async def _detect_world_book_suggestions(session_id: str = "", force: bool = False):
    """Compatibility entry: one evidence-backed maintenance pipeline."""
    from cognition.maintenance_router import analyse_current
    return await analyse_current()


# ═══════════════════════════════════════════════════════════
# 世界书路由
# ═══════════════════════════════════════════════════════════

@router.get("/entries")
async def get_world_book_entries():
    """Body and revision come from the same authoritative file snapshot."""
    from cognition import books
    from fastapi import HTTPException
    try:
        return {"entries": [{**e, "body": e["body_full"]} for e in books.catalog("world")]}
    except Exception:
        raise HTTPException(503, "词条暂时不可读取，请稍后刷新") from None


def _find_entry_file(name: str):
    """按词条名找到世界书词条对象和对应 Markdown 文件。"""
    try:
        from world_book import get_global_world_book
        wb = get_global_world_book()
        for entry in wb.get_all_entries():
            if entry.name == name:
                return entry, os.path.join(wb.entries_dir, entry.filename)
    except Exception as e:
        logger.warning(f"世界书查找词条失败: {e}")
    return None, None


@router.put("/entries/{name}")
async def update_world_book_entry(name: str, request: Request):
    """Legacy editor shares the metadata-preserving revision write boundary."""
    from cognition import books
    try:
        data = await request.json()
        if not data.get("revision"):
            return {"status": "error", "message": "缺少版本，请刷新新版页面后编辑"}
        current = next((e for e in books.catalog("world") if e["name"] == name), None)
        if current is None:
            return {"status": "not_found"}
        payload = {**current, "body": data.get("content") or data.get("body") or ""}
        if data.get("revision"):
            payload["revision"] = data["revision"]
        updated = books.save("world", name, payload)
        return {"status": "updated", "name": name, "revision": updated["revision"]}
    except books.BookError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception:
        return {"status": "error", "message": "保存结果未确认，请刷新核对"}


@router.delete("/entries/{name}")
async def delete_world_book_entry(name: str):
    """删除世界书词条。"""
    try:
        entry, fpath = _find_entry_file(name)
        if not entry or not fpath or not os.path.exists(fpath):
            return {"status": "not_found"}
        os.remove(fpath)
        try:
            from world_book import get_global_world_book
            get_global_world_book().reload()
        except Exception:
            pass
        return {"status": "deleted", "name": name}
    except Exception as e:
        logger.warning(f"删除世界书词条失败: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/suggestions")
async def get_world_book_suggestions():
    """返回待审建议列表"""
    try:
        from world_book.suggestions import SuggestionManager
        mgr = SuggestionManager()
        pending = mgr.get_pending()
        return {"suggestions": [asdict(s) for s in pending]}
    except ImportError:
        return {"suggestions": []}
    except Exception as e:
        logger.warning(f"获取世界书建议失败: {e}")
        return {"suggestions": []}


@router.post("/suggestions/{suggestion_id}/apply")
async def apply_world_book_suggestion(suggestion_id: str):
    """Old clients cannot bypass target/revision review."""
    return {"status": "review_required", "message": "请在新版前端预览归属与差异后采纳"}


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_world_book_suggestion(suggestion_id: str):
    """驳回建议"""
    try:
        from world_book.suggestions import SuggestionManager
        mgr = SuggestionManager()
        result = mgr.reject(suggestion_id)
        if result:
            return {"status": "rejected"}
        return {"status": "not_found"}
    except Exception as e:
        logger.warning(f"驳回世界书建议失败: {e}")
        return {"status": "error", "message": str(e)}


@router.put("/suggestions/{suggestion_id}")
async def update_world_book_suggestion(suggestion_id: str, request: Request):
    """编辑待审建议内容"""
    try:
        body = await request.json()
        allowed = {"proposed_name", "proposed_keywords", "proposed_body", "reason", "type", "target_entry"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            return {"status": "error", "message": "no valid fields"}
        from world_book.suggestions import SuggestionManager
        mgr = SuggestionManager()
        result = mgr.update(suggestion_id, updates)
        if result:
            from dataclasses import asdict
            return {"status": "updated", "suggestion": asdict(result)}
        return {"status": "not_found"}
    except Exception as e:
        logger.warning(f"更新世界书建议失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/flags")
async def create_world_book_flag(request: dict):
    """用户手动标记对话片段为世界书重要信息"""
    try:
        from world_book.suggestions import WorldBookFlag, add_flag
        today = datetime.now().strftime("%Y-%m-%d")
        flag = WorldBookFlag(
            id="flag_" + hashlib.md5(request.get("selected_text", "").encode()).hexdigest()[:8],
            selected_text=request.get("selected_text", ""),
            session_id=request.get("session_id", ""),
            date=today, marked_at=now_iso(),
        )
        add_flag(flag)
        return {"status": "flagged", "id": flag.id}
    except Exception as e:
        logger.warning(f"创建标记失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/suggestions/detect")
async def manual_detect_world_book():
    """手动触发世界书检测（跳过日期锁）"""
    result = await _detect_world_book_suggestions(force=True)
    try:
        from world_book.suggestions import SuggestionManager
        mgr = SuggestionManager()
        pending = mgr.get_pending()
        return {"detected": True, "suggestions_count": len(pending), "maintenance": result}
    except Exception as e:
        return {"detected": True, "maintenance": result, "message": "旧建议队列读取失败，请到认知整理查看自动维护结果"}
