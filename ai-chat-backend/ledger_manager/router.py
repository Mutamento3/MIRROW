"""
记账本 - FastAPI 路由
前缀: /api/ledger
"""
from fastapi import APIRouter, HTTPException
from .manager import get_global_ledger_manager
from .models import LedgerCreateRequest, LedgerUpdateCommentRequest, LedgerEntryWithContext, LedgerDetailResponse

router = APIRouter(prefix="/api/ledger", tags=["ledger"])


@router.get("/entries")
async def list_entries(debt_type: str = None, limit: int = 20, offset: int = 0):
    """列出记账条目（分页）"""
    mgr = get_global_ledger_manager()
    entries = mgr.list_entries(debt_type=debt_type, limit=limit, offset=offset)
    total = mgr.count_entries(debt_type=debt_type)
    return {"entries": [e.model_dump() for e in entries], "total": total}


@router.post("/entries")
async def add_entry(req: LedgerCreateRequest):
    """手动添加记账条目"""
    mgr = get_global_ledger_manager()
    entry = mgr.add(
        content=req.content,
        debt_type=req.debt_type,
        source_context=req.source_context
    )
    stats = mgr.get_stats(req.debt_type)
    return {
        "entry": entry.model_dump(),
        "stats": stats.model_dump()
    }


@router.put("/entries/{entry_id}")
async def update_entry(entry_id: str, content: str = None, debt_type: str = None):
    """修改记账条目"""
    mgr = get_global_ledger_manager()
    entry = mgr.update(entry_id, content=content, debt_type=debt_type)
    if entry:
        return {"entry": entry.model_dump()}
    raise HTTPException(status_code=404, detail="条目不存在")


@router.get("/entries/{entry_id}/detail")
async def get_entry_detail(entry_id: str):
    """获取单条账目详情（含对话上下文）"""
    mgr = get_global_ledger_manager()
    result = mgr.get_entry_with_context(entry_id)
    if not result:
        raise HTTPException(status_code=404, detail="条目不存在")
    return LedgerDetailResponse(
        entry=LedgerEntryWithContext(
            **result["entry"].model_dump(),
            context_messages=result["context_messages"],
        )
    ).model_dump()


@router.put("/entries/{entry_id}/comment")
async def update_comment(entry_id: str, req: LedgerUpdateCommentRequest):
    """更新用户备注"""
    mgr = get_global_ledger_manager()
    ok = mgr.update_comment(entry_id, req.comment)
    if not ok:
        raise HTTPException(status_code=404, detail="条目不存在")
    return {"status": "updated", "id": entry_id, "comment": req.comment[:50]}


@router.get("/stats")
async def get_stats(debt_type: str = None):
    """获取统计"""
    mgr = get_global_ledger_manager()
    if debt_type:
        return mgr.get_stats(debt_type).model_dump()
    return mgr.get_all_stats()


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: str):
    """删除记账条目（软删除）"""
    mgr = get_global_ledger_manager()
    ok, _ = mgr.delete(entry_id=entry_id)
    if ok:
        return {"status": "deleted", "id": entry_id}
    raise HTTPException(status_code=404, detail="条目不存在")


@router.post("/entries/{entry_id}/settle")
async def settle_entry(entry_id: str):
    """结算条目（归档）。不可逆——归档后不能复活。"""
    mgr = get_global_ledger_manager()
    ok = mgr.settle(entry_id)
    if ok:
        return {"status": "settled", "id": entry_id}
    raise HTTPException(status_code=404, detail="条目不存在或已归档")
