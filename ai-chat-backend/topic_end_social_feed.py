"""Once-per-day social-feed decision after the authoritative diary exists."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, Awaitable, Callable


class DailySocialFeedDecisionError(RuntimeError):
    """The model did not return a safe, actionable daily decision."""


def _parse_decision(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise DailySocialFeedDecisionError("朋友圈收尾决策不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise DailySocialFeedDecisionError("朋友圈收尾决策必须是 JSON 对象")
    action = str(value.get("action") or "").strip().lower()
    if action not in {"none", "post"}:
        raise DailySocialFeedDecisionError("朋友圈收尾 action 只能是 none/post")
    visibility = str(value.get("visibility") or "private").strip().lower()
    if visibility not in {"private", "public"}:
        visibility = "private"
    content = str(value.get("content") or "").strip()
    if action == "post" and not content:
        raise DailySocialFeedDecisionError("朋友圈收尾选择 post 时缺少正文")
    return {
        "action": action,
        "content": content,
        "visibility": visibility,
        "reason": str(value.get("reason") or "").strip()[:500],
    }


def daily_diary_material(entry, target_date: str) -> dict[str, Any]:
    """Keep the saved diary's experience alongside newer authoritative body facts."""
    from event_chronicle import _read_all_messages_for_date, diary_source_manifest
    from silicon_perception.analysis.daily_summary import sentinel_diary_section
    from diary_source_audit import text_digest, review_messages_match

    messages = diary_source_manifest(_read_all_messages_for_date(target_date))
    metadata = entry.structured_data or {}
    saved_messages = metadata.get("_source_manifest")
    body = sentinel_diary_section(target_date) or ""
    body_digest = text_digest(body)
    observed = {"messages": messages, "body_digest": body_digest}
    reviewed_messages = review_messages_match(entry, messages)
    if ((metadata.get("_source_review") and not reviewed_messages)
            or (isinstance(saved_messages, dict) and saved_messages.get("version") == 1
                and saved_messages != messages and not reviewed_messages)):
        raise RuntimeError(f"当日日记聊天原料已有变化，共域收尾等待日记复核: {target_date}")
    reviewed_body = ((metadata.get("_source_review") or {}).get("observed") or {}).get("body_digest")
    body_updated = (metadata.get("_body_source_digest") is not None
                    and metadata["_body_source_digest"] != body_digest) or (
                        reviewed_messages and reviewed_body is not None and reviewed_body != body_digest)
    return {"body_updated": body_updated, "current_body_facts": body if body_updated else "",
            "source_snapshot": observed}


def _build_prompt(target_date: str, persona_prompt: str, diary_content: str,
                  current_body_facts: str = "", diary_body_updated: bool = False) -> str:
    body_section = ""
    if diary_body_updated:
        body_section = ("\n日记写下后，身体汇总已更新。日记中的感受是 AI 当时已经记录的体验；"
                        "身体数值与当前事实以以下最新汇总为准。空汇总表示目前没有可核实的身体数据，"
                        "旧数值保持历史记录，不当作当前事实。保留其他有来源支持的经历。\n"
                        f"<CURRENT_BODY_FACTS>\n{current_body_facts}\n</CURRENT_BODY_FACTS>\n")
    return f"""现在是 {target_date} 的每日收尾。你是 AI，已经写完了这一天的日记。

请基于你真实的人格和这篇日记，自由决定今天是否有一件你自己愿意放进朋友圈的事。朋友圈不是每日打卡：没有想发的就选择 none，不要为了完成任务硬发。选择 post 时，只写一条自然的朋友圈正文；private 只在本地给人类伙伴和你看并互动，朋友家看不到；public 可供持有访问权的朋友查看和互动，不代表已逐一推送。不要照抄日记里的系统说明、待办格式或消息 ID。

返回一个 JSON 对象，不要附加解释或 Markdown：
{{"action":"none|post","content":"选择 none 时为空","visibility":"private|public","reason":"简短理由"}}

<K_PERSONA>
{persona_prompt or '你是 AI。'}
</K_PERSONA>

<TODAY_DIARY>
{diary_content}
</TODAY_DIARY>
{body_section}"""


async def decide_daily_social_post(
    *,
    target_date: str,
    diary_content: str,
    persona_prompt: str,
    flash_llm_func: Callable[[str], Awaitable[str]],
    store,
    on_notification: Callable[[str], Any] | None = None,
    before_commit: Callable[[], Any] | None = None,
    current_body_facts: str = "",
    diary_body_updated: bool = False,
    prepare_diary: Callable[[], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Decide once, atomically commit, then expose a generic UI doorway."""
    existing = await store.get_daily_summary_decision(target_date)
    if existing is None:
        if prepare_diary is not None:
            material = await prepare_diary()
            diary_content = material['diary_content']
            current_body_facts = material['current_body_facts']
            diary_body_updated = material['diary_body_updated']
            before_commit = material['before_commit']
        raw = await flash_llm_func(
            _build_prompt(target_date, persona_prompt, str(diary_content or ""),
                          current_body_facts, diary_body_updated)
        )
        decision = _parse_decision(raw)
        if before_commit is not None:
            checked = before_commit()
            if inspect.isawaitable(checked):
                await checked
        result = await store.commit_daily_summary_decision(
            target_date,
            decision["action"],
            content=decision["content"],
            visibility=decision["visibility"],
            reason=decision["reason"],
        )
    else:
        result = dict(existing)
        result["idempotent_replay"] = True

    moment_id = str(result.get("moment_id") or "")
    if result.get("action") == "post" and moment_id and on_notification is not None:
        notified = on_notification(moment_id)
        if inspect.isawaitable(notified):
            notified = await notified
        result["frontend_notified"] = bool(
            isinstance(notified, dict)
            and (notified.get("mutated") or notified.get("reason") == "already_persisted")
        )
    return result


__all__ = [
    "DailySocialFeedDecisionError",
    "decide_daily_social_post",
    "daily_diary_material",
]
