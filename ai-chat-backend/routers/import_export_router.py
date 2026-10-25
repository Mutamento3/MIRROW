"""导入导出 API 路由 — 从 main.py 提取"""

import os
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse

from time_utils import now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["import_export"])


@router.post("/export/sqlite")
async def export_sqlite():
    """生成 SQLite 导出文件，包含 conversation_messages + bookmarks + conversation_days。"""
    from import_export import export_to_sqlite
    try:
        file_path = await export_to_sqlite()
        filename = f"mirrow_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error(f"SQLite export failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/upload")
async def upload_import_file(file: UploadFile = File(...)):
    """上传 SQLite 导入文件，校验后返回 import_id + 可用日期列表。"""
    from import_export import IMPORTS_DIR, _active_imports, validate_import_file, get_import_dates
    import shutil, uuid as _uuid

    if not file.filename or not file.filename.endswith('.db'):
        raise HTTPException(status_code=400, detail="仅支持 .db SQLite 文件")

    os.makedirs(IMPORTS_DIR, exist_ok=True)
    import_id = str(_uuid.uuid4())
    file_path = os.path.join(IMPORTS_DIR, f"{import_id}.db")

    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    meta = validate_import_file(file_path)
    if not meta:
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="无效的 MIRROW 导出文件：缺少必要的表结构")

    _active_imports[import_id] = {"path": file_path, "created_at": now_iso()}
    dates = get_import_dates(import_id)
    return {"import_id": import_id, "available_dates": dates, "meta": meta}


@router.get("/import/dates")
async def list_import_dates(import_id: str):
    """列出已上传导入文件的可用日期及消息计数。"""
    from import_export import _active_imports, get_import_dates

    if import_id not in _active_imports:
        raise HTTPException(status_code=404, detail="导入会话已过期，请重新上传文件")
    dates = get_import_dates(import_id)
    return {"import_id": import_id, "available_dates": dates}


@router.post("/import/execute")
async def execute_import(request: Request):
    """执行导入。Body: {import_id, dates: ["2026-05-20",...], session_id?}"""
    from import_export import _active_imports, import_dates

    body = await request.json()
    import_id = body.get("import_id")
    dates = body.get("dates", [])
    target_session_id = body.get("session_id")

    if import_id not in _active_imports:
        raise HTTPException(status_code=404, detail="导入会话已过期，请重新上传文件")
    if not dates or not isinstance(dates, list):
        raise HTTPException(status_code=400, detail="请提供要导入的日期列表")

    try:
        result = await import_dates(import_id, dates, target_session_id)
        return result
    except Exception as e:
        logger.error(f"Import failed for dates {dates}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
