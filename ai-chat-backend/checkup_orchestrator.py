"""Shared checkup evidence boundary.

AI still calls the existing sensory tools (``eyes``, ``check_phone``,
``take_screenshot`` and ``band``).  This module is deliberately not a new
model-facing tool: it gives chat, scheduled tasks and Wander one definition of
what was attempted, captured, analysed and safe to claim.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from checkup_source_projection import source_item as _item, phone_items, screen_items, visual_item


CHECKUP_TOOL_GUIDANCE = (
    "可用感官及其来源：take_screenshot 提供电脑屏幕画面，"
    "check_phone 提供手机屏幕描述、当前前台应用、亮屏状态与使用统计，"
    "eyes 提供本人和周围环境的摄像头画面，band 提供健康数据。"
    "手机前台应用与截图分别是设备观测和视觉观测；摄像头画面不包含手机或电脑屏幕内容。"
)


def is_checkup_request_text(text: Any) -> bool:
    value = str(text or "")
    return bool(
        re.search(r"查(?:个|一下|下)?岗", value)
        or any(word in value for word in ("睡了没", "午睡没", "在干嘛"))
    )


def _analysis_error(details: dict[str, Any], fallback: str = "视觉分析失败") -> str:
    return str(
        details.get("analysis_error")
        or details.get("error")
        or fallback
    )


def _mobile_activity_valid(details: dict[str, Any], mobile: Any) -> bool:
    if details.get("mobile_activity_success") is not None:
        return details.get("mobile_activity_success") is True
    if not isinstance(mobile, dict) or mobile.get("success") is False:
        return False
    return any(
        key in mobile
        for key in ("foreground_app", "screen_on", "active_duration_s", "processes")
    )


def tracking_items(details: Any, activity: Any) -> list[dict[str, Any]]:
    """Project a Wander tracking result into the common evidence contract."""
    d = details if isinstance(details, dict) else {}
    out: list[dict[str, Any]] = []
    analysis_available = d.get("analysis_available") is True

    if d.get("route") == "away":
        out.append(_item(
            "behavior_state", "哨兵行为状态", "success",
            result="判定离席，未调用视觉", attempted=True,
            captured=False, analyzed=False,
        ))

    if d.get("screen_attempted"):
        captured = d.get("screen_available") is True
        if not captured:
            status, result, error = "failure", "", str(d.get("screen_error") or d.get("error") or "电脑屏幕采集失败")
        elif not analysis_available:
            status, result, error = "partial", "已取得电脑屏幕，但未完成分析", _analysis_error(d)
        else:
            status, result, error = "success", str(activity or "已分析电脑屏幕"), ""
        out.append(_item(
            "pc_screen", "电脑屏幕/活动分析", status,
            result=result, error=error, captured=captured,
            analyzed=captured and analysis_available,
        ))

    if d.get("phone_screen_attempted"):
        captured = d.get("phone_screen_available") is True
        if not captured:
            status, result, error = "failure", "", str(d.get("phone_screen_error") or d.get("error") or "手机屏幕采集失败")
        elif not analysis_available:
            status, result, error = "partial", "已取得手机画面，但未完成分析", _analysis_error(d)
        else:
            status, result, error = "success", str(activity or "已分析手机画面"), ""
        out.append(_item(
            "phone_screen", "手机屏幕", status,
            result=result, error=error, captured=captured,
            analyzed=captured and analysis_available,
        ))

    if d.get("camera_attempted"):
        captured = d.get("camera_available") is True
        if not captured:
            status, result, error = "failure", "", str(d.get("camera_error") or d.get("error") or "电脑摄像头采集失败")
        elif not analysis_available:
            status, result, error = "partial", "已取得电脑摄像头画面，但未完成分析", _analysis_error(d)
        else:
            status, result, error = "success", str(activity or "已分析电脑摄像头画面"), ""
        out.append(_item(
            "camera", "电脑摄像头", status,
            result=result, error=error, captured=captured,
            analyzed=captured and analysis_available,
        ))

    if d.get("mobile_activity_attempted"):
        mobile = d.get("mobile_activity")
        valid = _mobile_activity_valid(d, mobile)
        if valid:
            mobile = mobile or {}
            result = f"屏幕{'亮' if mobile.get('screen_on') else '灭'}；{mobile.get('foreground_app') or '前台应用未知'}"
            error = ""
        else:
            result = ""
            error = str(
                d.get("mobile_activity_error")
                or (mobile.get("error") if isinstance(mobile, dict) else "")
                or (mobile.get("content") if isinstance(mobile, dict) else "")
                or "手机未返回活动信息"
            )
        out.append(_item(
            "mobile_activity", "手机活动", "success" if valid else "failure",
            result=result, error=error, captured=valid, analyzed=valid,
        ))
    return out


def tracking_conclusion(details: Any, status_consistent: bool | None) -> str:
    d = details if isinstance(details, dict) else {}
    items = tracking_items(d, "")
    if (d.get("error") or not items or status_consistent is None
            or any(item.get("status") != "success" for item in items)):
        return "uncertain"
    return "confirmed" if status_consistent is True else "contradicted"


def scheduled_conclusion(items: Any) -> str:
    values = [item for item in (items or []) if isinstance(item, dict)]
    return "uncertain" if not values or any(item.get("status") != "success" for item in values) else "confirmed"


def sensory_tool_items(tool_calls: Any) -> list[dict[str, Any]]:
    """Flatten executed sensory calls, including Eyes' physical sources."""
    sensory = {"eyes", "check_phone", "band", "capture_camera", "take_screenshot"}
    items: list[dict[str, Any]] = []
    for call in tool_calls or []:
        if not isinstance(call, dict) or call.get("tool") not in sensory:
            continue
        error = str(call.get("error") or "")
        result = str(call.get("result") or "")
        success = call.get("success") is not False and not error
        parent_item = _item(
            str(call.get("tool") or "sensory"),
            str(call.get("description") or call.get("tool") or "感知工具"),
            "failure" if not success else ("partial" if call.get("status") == "partial" else ("success" if result else "partial")),
            result=result, error=error,
        )
        if call.get('tool') == 'check_phone':
            items.extend(phone_items(call, parent_item))
            continue
        if call.get('tool') in {'take_screenshot', 'capture_camera'}:
            items.extend(screen_items(call, parent_item))
            continue
        if call.get("tool") != "eyes":
            items.append(parent_item)
            continue
        extra = call.get("extra_data") or {}
        child_items: list[dict[str, Any]] = []
        for source in extra.get("sources") or []:
            if not isinstance(source, dict):
                continue
            child_items.append(visual_item(source, fallback_tool="eyes_source", error=error))
        for source_error in extra.get("errors") or []:
            label, _, detail = str(source_error).partition(":")
            child_items.append(_item(
                label.strip() or "eyes_source",
                label.strip() or "视觉来源",
                "failure",
                error=detail.strip() or str(source_error),
                captured=False, analyzed=False,
            ))
        items.extend(child_items or [parent_item])
    return items


def visible_chat_tool_calls(tool_calls: Any, *, hide_sensory: bool = False) -> list[Any]:
    calls = list(tool_calls or [])
    if not hide_sensory:
        return calls
    sensory = {"eyes", "check_phone", "band", "capture_camera", "take_screenshot"}
    return [call for call in calls if not isinstance(call, dict) or call.get("tool") not in sensory]


def grounded_tracking_fact(payload: Any) -> str:
    """Return the strongest claim supported by one user-tracking node only."""
    p = payload if isinstance(payload, dict) else {}
    if p.get('checkup_version') == 1:
        items = [item for item in p.get('checkup_items', []) if isinstance(item, dict)]
        facts = [f"{item.get('description') or item.get('tool')}：{item['result']}"
                 for item in items if item.get('status') == 'success' and item.get('result')]
        incomplete = [str(item.get('description') or item.get('tool') or '感知来源')
                      for item in items if item.get('status') != 'success']
        text = '；'.join(facts) if facts else '本次查岗没有取得足够的实时感知信息，暂时不能判断人类伙伴在做什么。'
        if incomplete:
            text += '；未完成取得或分析的来源：' + '、'.join(incomplete) + '。'
        return text
    tracking = p.get("tracking_result") if isinstance(p.get("tracking_result"), dict) else {}
    details = tracking.get("details") if isinstance(tracking.get("details"), dict) else {}
    mobile = p.get("mobile_activity") if p.get("mobile_activity_success") is True else None
    mobile_fact = ""
    if isinstance(mobile, dict):
        screen_on = mobile.get("screen_on")
        app = str(mobile.get("foreground_app") or "").strip()
        if screen_on is False:
            mobile_fact = "手机本次观测为熄屏。"
        elif screen_on is True:
            mobile_fact = f"手机本次观测为亮屏，前台应用为{app}。" if app else "手机本次观测为亮屏，前台应用未知。"
        if mobile_fact and p.get("mobile_inconsistency") is True:
            mobile_fact += "这与休息声明需复核，亮屏本身不证明人类伙伴正在操作。"
    route = str(details.get("route") or "")
    if route == "away":
        return "哨兵行为状态判定当前未在设备前；本次没有调用视觉。" + mobile_fact

    activity = str(tracking.get("activity_description") or p.get("activity_description") or "").strip()
    analysis_available = details.get("analysis_available") is True and not details.get("error")
    if analysis_available and activity:
        sources = []
        if details.get("screen_available"):
            sources.append("电脑屏幕")
        if details.get("phone_screen_available"):
            sources.append("手机屏幕")
        if details.get("camera_available"):
            sources.append("电脑摄像头")
        source_text = "、".join(sources) or "实时感知"
        return f"依据{source_text}分析：{activity}。" + mobile_fact

    captured = []
    if details.get("screen_available"):
        captured.append("电脑屏幕")
    if details.get("phone_screen_available"):
        captured.append("手机屏幕")
    if details.get("camera_available"):
        captured.append("电脑摄像头")
    if captured:
        return f"本次取得了{'、'.join(captured)}，但视觉分析没有成功，暂时不能判断人类伙伴在做什么。" + mobile_fact
    if mobile_fact:
        return mobile_fact
    return "本次查岗没有取得足够的实时感知信息，暂时不能判断人类伙伴在做什么。"


def grounded_tracking_summary(nodes: Iterable[dict[str, Any]]) -> str:
    facts: list[str] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        fact = grounded_tracking_fact(node.get("source_payload"))
        if fact and fact not in facts:
            facts.append(fact)
    return "本次查岗：" + (" ".join(facts) if facts else "没有形成可确认的实时证据。")


def grounded_tracking_reflection(payload: Any) -> str:
    """A node-review fallback that cannot invent unattempted sensors."""
    return grounded_tracking_fact(payload)
