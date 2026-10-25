"""书签 (Bookmark) API 路由 — 从 main.py 提取"""

import logging
from fastapi import APIRouter, HTTPException, Request
from time_utils import to_beijing_time

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bookmarks"])

# 书签上下文模糊匹配统一常量（消除各处散落的魔数）
FUZZY_THRESHOLD = 60   # 读取端点模糊命中阈值（原散落 60/70）
PREVIEW_LEN = 100      # content 预览截断长度（原散落 80/100/300）



@router.post("/settings/bookmark-context-window")
async def set_bookmark_context_window(request: Request):
    """设置漫想翻收藏夹时的上下文轮数（1-10）"""
    try:
        body = await request.json()
        value = int(body.get("value", 3))
        from wander_manager.event_handlers import set_bookmark_context_window
        set_bookmark_context_window(value)
        return {"status": "ok", "value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bookmarks")
async def get_bookmarks(collected_by: str = None, offset: int = 0, limit: int = 20):
    """获取收藏列表。collected_by: k | user"""
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        meta_list, total = chronicle.list_bookmarks(collected_by=collected_by if collected_by else None, offset=offset, limit=limit)
        items = []
        for m in meta_list:
            content = (m.get("content") or "")[:300]
            items.append({
                "id": m["bucket_id"],
                "content": content,
                "collected_by": m["collected_by"],
                "original_msg_id": m["original_msg_id"],
                "original_timestamp": to_beijing_time(m.get("original_timestamp", "")),
                "display_time": to_beijing_time(m.get("original_timestamp", "")),
                "session_id": m["session_id"],
                "created": m["created"],
            })
        return {"bookmarks": items, "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bookmarks/{message_id}")
async def add_bookmark(message_id: str, request: Request):
    """收藏一条消息。body: {content, role, session_id, timestamp, collected_by:k|user}"""
    try:
        from event_chronicle import get_global_chronicle
        import uuid
        body = await request.json()
        chronicle = get_global_chronicle()
        content = body.get("content", "").strip()
        collected_by = body.get("collected_by", "user")
        session_id = body.get("session_id", "")
        original_timestamp = body.get("timestamp", "")
        role = body.get("role", "assistant")
        is_voice = content.startswith("[语音")
        if not content and not is_voice:
            return {"status": "skipped", "reason": "empty content"}
        existing = chronicle.get_bookmark_meta_by_msg_id(message_id)
        if existing:
            return {"status": "already_bookmarked", "message_id": message_id, "bucket_id": existing["bucket_id"]}
        bucket_id = uuid.uuid4().hex[:12]

        resolved_msg_id = message_id
        resolved_ts = original_timestamp
        resolved_content = content[:500]
        if session_id:
            try:
                all_msgs = chronicle.get_messages_by_session_id(session_id, limit=500)
                target_idx = None
                for i, m in enumerate(all_msgs):
                    mid = m.get("message_id", "") or m.get("id", "")
                    if mid == message_id:
                        target_idx = i
                        break
                if target_idx is None and content:
                    preview = content[:80].strip()
                    if preview:
                        from rapidfuzz import fuzz as rfuzz
                        best_score = 0
                        for i, m in enumerate(all_msgs):
                            msg_text = (m.get("content", "") or "")[:300]
                            score = rfuzz.partial_ratio(preview, msg_text)
                            if score > best_score:
                                best_score = score
                                target_idx = i
                        if best_score < 70:
                            target_idx = None
                if target_idx is not None:
                    matched = all_msgs[target_idx]
                    resolved_msg_id = matched.get("message_id", "") or matched.get("id", "") or message_id
                    resolved_ts = matched.get("timestamp", "") or original_timestamp
                    resolved_content = (matched.get("content", "") or "")[:500]
                    if resolved_msg_id != message_id and chronicle.get_bookmark_meta_by_msg_id(resolved_msg_id):
                        return {"status": "already_bookmarked", "message_id": resolved_msg_id, "bucket_id": existing["bucket_id"] if existing else ""}
            except Exception as e:
                logger.warning(f"书签上下文自动匹配失败: {e}")

        chronicle.add_bookmark(
            bucket_id=bucket_id, original_msg_id=resolved_msg_id,
            session_id=session_id, original_timestamp=resolved_ts,
            collected_by=collected_by, content=resolved_content, role=role,
        )
        return {"status": "bookmarked", "message_id": message_id, "bucket_id": bucket_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/bookmarks/{bucket_id}")
async def delete_bookmark(bucket_id: str):
    """取消 SQLite 收藏；旧记忆档案保持只读"""
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        meta_deleted = chronicle.delete_bookmark_meta(bucket_id)
        return {"status": "deleted" if meta_deleted else "not_found", "id": bucket_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bookmarks/{bucket_id}/context")
async def get_bookmark_context(bucket_id: str, before: int = 3, after: int = 3):
    """获取书签的对话上下文快照。"""
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        meta = chronicle.get_bookmark_meta_by_bucket(bucket_id)
        if not meta:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        session_id = meta.get("session_id", "")
        original_msg_id = meta.get("original_msg_id", "")
        raw_content = (meta.get("content") or "").strip()

        _KEEP_KEYS = {"role", "content", "timestamp", "message_id", "is_wander", "is_sentinel", "is_reminder", "tool_summary"}

        def _clean_message(m):
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                return None
            if m.get("is_wander") or m.get("is_sentinel") or m.get("is_reminder"):
                return None
            if m.get("timestamp"):
                m["timestamp"] = to_beijing_time(m["timestamp"])
            return {k: v for k, v in m.items() if k in _KEEP_KEYS}

        def filter_deleted(msgs):
            if not msgs:
                return []
            return [c for m in msgs if (c := _clean_message(m)) is not None]

        if session_id and original_msg_id:
            before_msgs, target, after_msgs = chronicle.get_message_context_by_id(
                session_id, original_msg_id, before=before, after=after
            )
            if target is not None:
                target_clean = _clean_message(target)
                if target_clean is None:
                    logger.warning(
                        f"[bookmark-context] target 被 _clean_message 过滤成 None: "
                        f"bucket={bucket_id} session={session_id} msg_id={original_msg_id} "
                        f"role={target.get('role')} wander={target.get('is_wander')} "
                        f"sentinel={target.get('is_sentinel')} reminder={target.get('is_reminder')}"
                    )
                return {
                    "messages_before": filter_deleted(before_msgs),
                    "target_message": target_clean,
                    "messages_after": filter_deleted(after_msgs),
                    "has_more_before": len(before_msgs) == before,
                    "has_more_after": len(after_msgs) == after,
                }
            else:
                logger.warning(
                    f"[bookmark-context] 精确路径未命中 target（msg_id 在库中不存在），转模糊回退: "
                    f"bucket={bucket_id} session={session_id} msg_id={original_msg_id}"
                )

        clean_content = raw_content.replace("[收藏] ", "").replace("[[", "").replace("]]", "")
        preview = clean_content[:PREVIEW_LEN]

        def fuzzy_search_in_messages(msgs, query):
            from rapidfuzz import fuzz as rfuzz
            best_score = 0
            best_idx = None
            for i, m in enumerate(msgs):
                msg_text = (m.get("content", "") or "").replace("[收藏] ", "")[:300]
                score = rfuzz.partial_ratio(query, msg_text)
                if score > best_score:
                    best_score = score
                    best_idx = i
            if best_score >= FUZZY_THRESHOLD:
                return best_idx, best_score
            return None, best_score

        target_idx = None
        all_messages = []
        _same_session_score = -1   # 埋点：同 session 模糊最高分（-1 表示未搜索）
        _cross_session_score = -1  # 埋点：跨 session 模糊最高分

        if session_id and preview:
            all_messages = chronicle.get_messages_by_session_id(session_id, limit=500, desc=True)
            idx, score = fuzzy_search_in_messages(all_messages, preview)
            _same_session_score = score
            if idx is not None:
                target_idx = idx

        if target_idx is None and preview:
            recent_sessions = chronicle.get_recent_active_sessions(limit=30)
            best_score = 0
            best_sid = None
            best_msgs = None
            best_idx = None
            _seen_max = 0  # 埋点：所有 session 中见过的最高分（不论是否达阈值）
            for sid in recent_sessions:
                msgs = chronicle.get_messages_by_session_id(sid, limit=500, desc=True)
                idx, score = fuzzy_search_in_messages(msgs, preview)
                if score > _seen_max:
                    _seen_max = score
                if idx is not None and score > best_score:
                    best_score = score
                    best_idx = idx
                    best_sid = sid
                    best_msgs = msgs
            _cross_session_score = _seen_max
            if best_idx is not None and best_msgs:
                target_idx = best_idx
                all_messages = best_msgs
                session_id = best_sid

        if target_idx is None:
            logger.warning(
                f"[bookmark-context] 全空返回（未定位到 target）: bucket={bucket_id} "
                f"session={meta.get('session_id','')} msg_id={original_msg_id!r} "
                f"preview_len={len(preview)} same_session_fuzzy={_same_session_score} "
                f"cross_session_fuzzy={_cross_session_score} threshold={FUZZY_THRESHOLD}"
            )
            return {"messages_before": [], "target_message": None, "messages_after": [],
                    "has_more_before": False, "has_more_after": False}

        before_start = max(0, target_idx - before)
        messages_before = all_messages[before_start:target_idx]
        after_end = min(len(all_messages), target_idx + after + 1)
        messages_after = all_messages[target_idx + 1:after_end]
        target_clean = _clean_message(all_messages[target_idx])
        return {
            "messages_before": filter_deleted(messages_before),
            "target_message": target_clean,
            "messages_after": filter_deleted(messages_after),
            "has_more_before": before_start > 0,
            "has_more_after": after_end < len(all_messages),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bookmarks/{bucket_id}/ensure-context")
async def ensure_bookmark_context(bucket_id: str):
    """为书签补全 original_msg_id 和 session_id，通过内容模糊匹配。"""
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        meta = chronicle.get_bookmark_meta_by_bucket(bucket_id)
        if not meta:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        session_id = meta.get("session_id", "")
        content = meta.get("content", "")
        if not content:
            return {"success": False, "reason": "书签内容为空，无法补全"}
        if meta.get("original_msg_id", "") and session_id:
            existing = chronicle.get_messages_by_session_id(session_id, limit=1)
            if existing:
                return {"success": True, "reason": "已有 original_msg_id，无需补全",
                        "original_msg_id": meta["original_msg_id"]}

        from rapidfuzz import fuzz as rfuzz
        clean_content = content.replace("[收藏] ", "").replace("[[", "").replace("]]", "")
        preview = clean_content[:100]

        def find_best_match(msgs, content_preview):
            best_score = 0
            best_idx = None
            for i, m in enumerate(msgs):
                msg_text = (m.get("content", "") or "").replace("[收藏] ", "")[:300]
                score = rfuzz.partial_ratio(content_preview, msg_text)
                if score > best_score:
                    best_score = score
                    best_idx = i
            return best_idx, best_score

        target_idx = None
        best_sid = session_id
        # Search in same session first
        if session_id:
            msgs = chronicle.get_messages_by_session_id(session_id, limit=500, desc=True)
            idx, score = find_best_match(msgs, preview)
            if idx is not None and score >= 70:
                target_idx = idx
            else:
                best_sid = ""
        # Cross-session fallback
        if target_idx is None:
            recent = chronicle.get_recent_active_sessions(limit=30)
            for sid in recent:
                if sid == session_id:
                    continue
                msgs = chronicle.get_messages_by_session_id(sid, limit=500, desc=True)
                idx, score = find_best_match(msgs, preview)
                if idx is not None and score >= 70:
                    target_idx = idx
                    best_sid = sid
                    break

        if target_idx is not None and best_sid:
            msgs = chronicle.get_messages_by_session_id(best_sid, limit=500, desc=True)
            matched = msgs[target_idx]
            new_msg_id = matched.get("message_id", "") or matched.get("id", "")
            chronicle.update_bookmark_meta(bucket_id, {
                "original_msg_id": new_msg_id,
                "session_id": best_sid,
                "original_timestamp": matched.get("timestamp", ""),
            })
            return {"success": True, "original_msg_id": new_msg_id, "session_id": best_sid}
        return {"success": False, "reason": "全库未找到匹配（阈值 ≥70）"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
