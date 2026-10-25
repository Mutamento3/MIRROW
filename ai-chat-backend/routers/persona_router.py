"""人设演化建议 API 路由 v2 — 支持 add/modify/deprecate + 过期 + 迁移"""

from fastapi import APIRouter
from shared_state import get_latest_persona_prompt, set_latest_persona_prompt
from llm_client import call_llm_flash_for_text
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/persona", tags=["persona"])


def _normalize_suggestion(s: dict, idx: int) -> dict:
    """确保建议条目包含 v2 必需字段，兼容旧格式"""
    if "id" not in s:
        s["id"] = s.get("id", idx) if isinstance(s.get("id"), str) else idx
    if "status" not in s:
        if s.get("applied"):
            s["status"] = "applied"
        elif s.get("rejected"):
            s["status"] = "rejected"
        else:
            s["status"] = "pending"
    if "type" not in s or s["type"] in ("trait_adjust", "boundary_update", "voice_nuance"):
        s["type"] = "add"  # 旧类型统一映射为 add
    if "target_section" not in s:
        s["target_section"] = "全局"
    if "proposed_text" not in s:
        s["proposed_text"] = s.get("suggested_change", "")
    if "expires_days" not in s:
        s["expires_days"] = 7  # 默认 7 天
    if "title" not in s:
        s["title"] = s.get("title", "未命名")
    if "confidence" not in s:
        s["confidence"] = 0.5
    return s


def _find_suggestion(suggestion_id) -> tuple:
    """
    按 ID（字符串或数字索引）查找建议。
    返回 (index, suggestion, suggestions_list) 或 (None, None, [])。
    suggestions_list 用于后续 save——避免重读磁盘丢失修改。
    """
    from persona_evolution import get_suggestions
    suggestions = get_suggestions()

    # 先按稳定字符串 ID 匹配。只有旧数据没有 ID 时才回退到数字索引。
    # 旧实现先尝试数字索引，会让数字化的历史 ID 与列表位置产生歧义。
    sid = str(suggestion_id)
    for i, s in enumerate(suggestions):
        if str(s.get("id", "")) == sid:
            return i, s, suggestions

    try:
        idx = int(suggestion_id)
        if 0 <= idx < len(suggestions):
            return idx, suggestions[idx], suggestions
    except (ValueError, TypeError):
        pass

    return None, None, []


@router.get("/suggestions")
async def get_persona_suggestions():
    """获取全部建议（含 pending/applied/rejected/expired）"""
    try:
        from persona_evolution import get_suggestions, expire_outdated_evolutions
        expire_outdated_evolutions()
        items = get_suggestions()
        for i, s in enumerate(items):
            _normalize_suggestion(s, i)
        return {"suggestions": items}
    except Exception as e:
        return {"suggestions": [], "error": str(e)}


@router.get("/suggestions/active")
async def get_active_evolutions():
    """获取当前活跃的演化条目"""
    try:
        from persona_evolution import get_active_suggestions
        items = get_active_suggestions()
        return {"active": items}
    except Exception as e:
        return {"active": [], "error": str(e)}


@router.post("/suggestions/{suggestion_id}/apply")
async def apply_persona_suggestion(suggestion_id: str, body: dict = None):
    """
    采纳一条人设建议（suggestion_id 可以是数字索引或字符串 ID）。
    可选 body: {"expires_days": 7} 覆盖 Flash 建议的过期天数
    """
    try:
        from copy import deepcopy
        from persona_evolution import (
            get_suggestions, save_suggestions,
            get_evolution_state, save_evolution_state,
            register_active_evolution, check_conflict,
        )
        from datetime import datetime as _dt

        idx, s, suggestions = _find_suggestion(suggestion_id)
        if s is None:
            return {"status": "error", "message": "建议不存在"}

        # 规范化旧格式
        _normalize_suggestion(s, idx)

        status = s.get("status") or "pending"
        if status != "pending":
            messages = {
                "applied": "已采纳过",
                "rejected": "已驳回，不能再次采纳",
                "expired": "条目已过期，请先复启用",
            }
            return {"status": "error", "message": messages.get(status, "当前状态不能采纳")}

        # 确保新字段存在
        if not isinstance(s.get("id"), str) or not s.get("id", "").startswith("pe_"):
            from persona_evolution import _make_suggestion_id
            s["id"] = _make_suggestion_id()
        if "type" not in s:
            s["type"] = "add"
        if "target_section" not in s:
            s["target_section"] = "全局"
        if "proposed_text" not in s:
            s["proposed_text"] = s.get("suggested_change", "")

        # 允许前端覆盖 expires_days
        if body and "expires_days" in body:
            s["expires_days"] = body["expires_days"]

        # 冲突检测
        conflict = check_conflict(s)

        # 标记 + 注册。两个 JSON 文件是同一状态变更；第二步失败时回滚第一步，
        # 避免 suggestions 已采纳但 active_evolutions 没注册的半状态。
        suggestions_before = deepcopy(suggestions)
        state_before = deepcopy(get_evolution_state())
        s["status"] = "applied"
        s["applied_at"] = _dt.now().isoformat()
        try:
            save_suggestions(suggestions)
            register_active_evolution(s)
        except Exception:
            save_suggestions(suggestions_before)
            save_evolution_state(state_before)
            raise

        logger.info(f"人格演化: 采纳 {s['id']} ({s.get('title', '')})")
        return {
            "status": "applied",
            "conflict": conflict,
            "id": s["id"],
            "expires_days": s.get("expires_days", 0),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_persona_suggestion(suggestion_id: str):
    """驳回一条人设建议"""
    try:
        from persona_evolution import reject_suggestion
        idx, s, _ = _find_suggestion(suggestion_id)
        if s is None:
            return {"status": "error", "message": "建议不存在"}
        _normalize_suggestion(s, idx)
        if s.get("status") != "pending":
            return {"status": "error", "message": "只有待处理建议可以驳回"}
        if not reject_suggestion(idx):
            return {"status": "error", "message": "驳回失败"}
        return {"status": "rejected"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/suggestions/{suggestion_id}/expire")
async def expire_evolution(suggestion_id: str):
    """手动提前失效一条已采纳的演化"""
    try:
        from persona_evolution import deactivate_evolution
        idx, s, _ = _find_suggestion(suggestion_id)
        if s is None:
            return {"status": "error", "message": "演化条目不存在"}
        _normalize_suggestion(s, idx)
        if s.get("status") != "applied":
            return {"status": "error", "message": "只有活跃条目可以提前失效"}
        evo_id = s.get("id", str(idx))
        if not deactivate_evolution(evo_id):
            return {"status": "error", "message": "活跃演化条目不存在"}
        return {"status": "expired", "id": evo_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/suggestions/{suggestion_id}/reactivate")
async def reactivate_evolution_route(suggestion_id: str, body: dict = None):
    """重新激活一条已过期的演化；expires_days=0 表示永久。"""
    try:
        from persona_evolution import reactivate_evolution
        idx, suggestion, _ = _find_suggestion(suggestion_id)
        if suggestion is None:
            return {"status": "error", "message": "建议不存在"}
        evo_id = suggestion.get("id", str(idx))
        expires_days = body.get("expires_days", 7) if body else 7
        result = reactivate_evolution(evo_id, expires_days)
        if result is None:
            return {"status": "error", "message": "只能重新激活已过期的条目"}
        return {
            "status": "reactivated",
            "id": result.get("id"),
            "expires_days": result.get("expires_days", 0),
            "expires_at": result.get("expires_at"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/suggestions/{suggestion_id}/validity")
async def update_evolution_validity_route(suggestion_id: str, body: dict = None):
    """调整活跃演化的有效期；计时从本次调整开始，0 表示永久。"""
    try:
        if not body or "expires_days" not in body:
            return {"status": "error", "message": "请提供 expires_days"}
        from persona_evolution import update_evolution_validity
        idx, suggestion, _ = _find_suggestion(suggestion_id)
        if suggestion is None:
            return {"status": "error", "message": "建议不存在"}
        evo_id = suggestion.get("id", str(idx))
        result = update_evolution_validity(evo_id, body.get("expires_days"))
        if result is None:
            return {"status": "error", "message": "活跃演化条目不存在"}
        return {
            "status": "updated",
            "id": suggestion_id,
            "expires_days": result.get("expires_days", 0),
            "expires_at": result.get("expires_at"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/suggestions/detect")
async def trigger_persona_detection():
    """手动触发人格演化检测（force，跳过期锁）"""
    try:
        from persona_evolution import (
            detect_persona_insights, get_suggestions, save_suggestions, mark_triggered_today,
        )
        suggestions = await detect_persona_insights(
            flash_llm_func=call_llm_flash_for_text,
            current_persona=get_latest_persona_prompt() or "",
        )
        if suggestions:
            existing = get_suggestions()
            existing.extend(suggestions)
            save_suggestions(existing)
            mark_triggered_today()
            return {"status": "ok", "new_suggestions": len(suggestions), "total": len(existing)}
        return {"status": "ok", "new_suggestions": 0, "total": len(get_suggestions())}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/migrate/preview")
async def migrate_preview():
    """生成旧 [人格演化 ...] 片段的迁移预览（新旧对比）"""
    try:
        from persona_evolution import migrate_preview_old_evolutions
        preview = await migrate_preview_old_evolutions(call_llm_flash_for_text)
        return {"status": "ok", "preview": preview, "count": len(preview)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/migrate/apply")
async def migrate_apply(body: dict):
    """
    应用迁移：替换旧 [人格演化 ...] 片段为新格式。
    body: {"items": [{"old_title":..., "old_content":..., "new_title":..., ...}, ...]}
    """
    try:
        from persona_evolution import apply_migration

        # 备份
        from settings_manager import get_setting, set_setting
        import json as _json
        current = get_setting("aiPersona")
        backup_path = "data/settings.json.bak_persona_migration"
        with open(backup_path, "w", encoding="utf-8") as f:
            _json.dump(current, f, ensure_ascii=False, indent=2)
        logger.info(f"人格演化迁移: 已备份 settings.json → {backup_path}")

        items = body.get("items", [])
        applied = apply_migration(items)
        return {"status": "ok", "applied": applied, "backup": backup_path}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/conflict/{suggestion_id}")
async def check_suggestion_conflict(suggestion_id: str):
    """检查一条建议与活跃演化的冲突"""
    try:
        from persona_evolution import check_conflict
        idx, s, _ = _find_suggestion(suggestion_id)
        if s is None:
            return {"status": "error", "message": "建议不存在"}
        _normalize_suggestion(s, idx)
        conflict = check_conflict(s)
        return {"status": "ok", "conflict": conflict}
    except Exception as e:
        return {"status": "error", "message": str(e)}
