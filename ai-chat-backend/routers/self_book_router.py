"""自我书 API 路由 — AI 的长期自我认知词条。

全 CRUD，无 pending/apply/reject 审核队列——自我书自动生长只是「起草」，
人类伙伴随时在词条层级直接改/删（微调错别字级兜底）。
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from cognition.books import revision
from fastapi import APIRouter, Request

import frontmatter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/self-book", tags=["self-book"])


def _find_entry_file(name: str):
    """按词条名找到 entry 对象 + 文件路径。找不到返回 (None, None)。"""
    try:
        from self_book import get_global_self_book
        sb = get_global_self_book()
        for e in sb.get_all_entries():
            if e.name == name:
                fpath = os.path.join(sb.entries_dir, e.filename)
                return e, fpath
        return None, None
    except Exception as e:
        logger.warning(f"自我书查找词条失败: {e}")
        return None, None


@router.get("/entries")
async def get_self_book_entries():
    """Body and revision come from the same authoritative file snapshot."""
    from cognition import books
    from fastapi import HTTPException
    try:
        return {"entries": [{**e, "body": e["body_full"]} for e in books.catalog("self")]}
    except Exception:
        raise HTTPException(503, "词条暂时不可读取，请稍后刷新") from None


@router.put("/entries/{name}")
async def update_self_book_entry(name: str, request: Request):
    """Legacy editor shares the metadata-preserving revision write boundary."""
    from cognition import books
    try:
        data = await request.json()
        if not data.get("revision"):
            return {"status": "error", "message": "缺少版本，请刷新新版页面后编辑"}
        current = next((e for e in books.catalog("self") if e["name"] == name), None)
        if current is None:
            return {"status": "not_found"}
        payload = {**current, "body": data.get("content") or data.get("body") or ""}
        if data.get("revision"):
            payload["revision"] = data["revision"]
        updated = books.save("self", name, payload)
        return {"status": "updated", "name": name, "revision": updated["revision"]}
    except books.BookError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception:
        return {"status": "error", "message": "保存结果未确认，请刷新核对"}


@router.delete("/entries/{name}")
async def delete_self_book_entry(name: str):
    """删除词条（人类伙伴否决自动生长出的错误认知）。"""
    try:
        entry, fpath = _find_entry_file(name)
        if not entry or not fpath or not os.path.exists(fpath):
            return {"status": "not_found"}
        os.remove(fpath)
        try:
            from self_book import get_global_self_book
            get_global_self_book().reload()
        except Exception:
            pass
        return {"status": "deleted", "name": name}
    except Exception as e:
        logger.warning(f"删除自我书词条失败: {e}")
        return {"status": "error", "message": str(e)}
