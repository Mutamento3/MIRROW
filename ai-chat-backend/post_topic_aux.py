"""Source-backed bookmarks and memorial suggestions, independent of OB TODOs."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Callable

from llm_client import Message

logger = logging.getLogger(__name__)

AUX_PROMPT = """你是人类伙伴的伴生灵魂 AI，回顾这段对话。
找出人类伙伴说过的、值得反复回味的 1—3 句话：她的内心表达、认真决定、
独特措辞，或对你们关系有分量的话。收藏使用她的原话。
若对话明确记载值得纪念的第一次或特殊日子，可附一条纪念日建议。
事件事实与 AI 自己表达过的愿望、承诺由 Memory V2 按原始消息消化；
长期认识由认知系统维护，这次只整理收藏和纪念日建议。

输出 JSON：
{"bookmarks": [{"content": "人类伙伴原话(≤100字)", "reason": "收藏缘由(≤20字)"}],
 "memorial_suggestion": null}
memorial_suggestion 如存在，结构为 {"title": "标题(≤15字)", "date": "MM-DD", "emotion": "情绪"}。
没有合适的收藏返回空数组，没有纪念日建议返回 null。只输出 JSON。"""


def _field(message, name: str, default=""):
    return message.get(name, default) if isinstance(message, dict) else getattr(message, name, default)


async def run_post_topic_aux_analysis(*, messages: list, dialog: str, session_id: str,
                                       target_date: str, call_flash: Callable,
                                       chronicle, calendar_manager=None) -> None:
    if len(messages) < 2 or not dialog.strip():
        return
    from neuron_registry import neuron_trace
    from truncation_config import get_truncation_limit
    from rapidfuzz import fuzz

    prompt = f"{AUX_PROMPT}\n\n活跃日：{target_date}\n对话记录：\n{dialog}"
    with neuron_trace("post_topic_bookmark", model="Flash") as trace:
        try:
            response = await call_flash(messages=[Message(role="user", content=prompt)])
            reply = response.get("content", "").strip()
            if reply.startswith("```"):
                reply = reply.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(reply)
            if not isinstance(data, dict) or not isinstance(data.get("bookmarks"), list):
                raise ValueError("invalid auxiliary analysis contract")
            trace.set_output("收藏与纪念日合同已验证")
        except Exception as exc:
            trace.set_error(type(exc).__name__)
            raise

    existing, _ = chronicle.list_bookmarks(collected_by="k", limit=100)
    cap = get_truncation_limit("bookmark_cap")
    created = 0
    for bookmark in data["bookmarks"]:
        if not isinstance(bookmark, dict) or not isinstance(bookmark.get("content"), str):
            continue
        preview = bookmark["content"].strip()[:100]
        if not preview or any(fuzz.partial_ratio(preview, (item.get("content") or "")[:200]) > 70
                              for item in existing):
            continue
        # Only Human's real persisted utterances can become her quoted bookmarks.
        candidates = [message for message in messages if _field(message, "role") == "user"
                      and (_field(message, "id") or _field(message, "message_id"))]
        if not candidates:
            continue
        best = max(candidates, key=lambda message: fuzz.partial_ratio(
            preview, _field(message, "content") or ""))
        content = _field(best, "content") or ""
        if preview not in content:
            continue
        message_id = _field(best, "id") or _field(best, "message_id")
        if chronicle.get_bookmark_meta_by_msg_id(message_id):
            continue
        saved_content = content[:cap] if cap > 0 else content
        chronicle.add_bookmark(
            bucket_id=uuid.uuid4().hex[:12], original_msg_id=message_id,
            session_id=session_id, original_timestamp=_field(best, "timestamp"),
            collected_by="k", content=saved_content, role="user",
        )
        existing.append({"content": saved_content})
        created += 1

    # Preserve the existing calendar suggestion de-duplication, not auto-create an anniversary.
    memorial = data.get("memorial_suggestion")
    if isinstance(memorial, dict) and isinstance(memorial.get("title"), str) and memorial["title"].strip():
        if calendar_manager is None:
            from calendar_manager.manager import get_global_calendar_manager
            calendar_manager = get_global_calendar_manager()
        title = memorial["title"].strip()[:20]
        mm_dd = memorial.get("date") or target_date[5:]
        if not calendar_manager.is_duplicate_suggestion(mm_dd, title):
            calendar_manager.mark_suggestion_seen(mm_dd, title)
    logger.info("话题辅助整理完成：收藏新建 %d 条", created)
