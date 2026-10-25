"""Shared output contract for every conversational multimodal entry.

The provider returns one JSON object.  The UI receives ``natural_language``;
objective per-image observations stay available for tool receipts and image
message storage without a second vision request.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


VISION_RESPONSE_FORMAT = {"type": "json_object"}


def _decode_json_object(content: Any) -> dict[str, Any] | None:
    """Decode the visual envelope without requiring the JSON to fill the body.

    Some providers obey a higher-level receipt/sidecar instruction by adding a
    short line before or after the JSON object.  The image analysis is still
    usable in that case, so scan for the first envelope-shaped object instead
    of discarding the whole response.
    """
    if isinstance(content, Mapping):
        return dict(content)

    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
        return dict(payload) if isinstance(payload, Mapping) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[index:])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(candidate, Mapping):
            continue
        if any(key in candidate for key in ("natural_language", "sources", "fused_summary")):
            return dict(candidate)
    return None


def vision_image_parts(image_url: str, source_key: str, label: str) -> list[dict[str, Any]]:
    """The same source binding for every conversational visual entry."""
    return [
        {"type": "text", "text": f"来源标签：{label}；唯一键：{source_key}。下一项就是该来源图片。"},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]


def reserve_vision_source_key(image: dict[str, Any], used_keys: Iterable[str], default: str = "visual_image") -> str:
    """Distinct captures of the same physical source retain distinct identities."""
    used = set(used_keys)
    original = str(image.get("source_key") or default)
    key, count = original, 2
    while key in used:
        key = f"{original}__{count}"
        count += 1
    if key != original:
        image["physical_source_key"] = image.get("physical_source_key") or original
    image["source_key"] = key
    return key


def build_vision_contract(sources: Iterable[Mapping[str, Any]], *, allow_tools: bool = True) -> str:
    sources = list(sources)
    rows = []
    keys = []
    for source in sources:
        key = str(source.get("key") or "").strip()
        if not key:
            continue
        keys.append(key)
        rows.append(f'- "{key}": {str(source.get("label") or key).strip()}')
    source_text = "\n".join(rows) or "- no_image: 未提供可用图片"
    allowed = ", ".join(keys) or "no_image"
    sample_sources = {
        key: {
            "label": next(
                (str(source.get("label") or key).strip() for source in sources
                 if str(source.get("key") or "").strip() == key),
                key,
            ),
            "summary": "该路画面的简要观察；看不清时写看不清",
            "visible_evidence": "支持判断的可见细节；没有则留空",
            "confidence": "high/medium/low",
        }
        for key in keys
    }
    sample = json.dumps({
        "natural_language": "结合完整对话和本次证据，直接对人类伙伴说的话",
        "sources": sample_sources,
        "fused_summary": "仅综合确实看见的内容",
        "tool_calls": [],
    }, ensure_ascii=False, separators=(",", ":"))
    action_contract = (
        "本入口开放的工具仍按各自能力与参数合同使用；下一步动作放入 tool_calls。"
        if allow_tools else "本轮是最终表达阶段，没有可执行工具；tool_calls 必须为 []。"
    )
    return f"""【本轮视觉输出合同】
你正在直接查看多张有明确来源的图片。本轮最终响应必须且只能是一个合法 JSON 对象；
不要输出 NATURAL_LANGUAGE/TOOL_CALL 标记、Markdown 代码块或 JSON 外文字。
本合同覆盖本轮普通回复格式：不输出 RESULTS_USED；工具动作只能放入 tool_calls。
若上方要求可选的 AI 自身情绪 sidecar，把完整 sidecar 放在 natural_language 字符串末尾，
不要放在 JSON 对象之外。

实际提供的图片来源：
{source_text}

JSON 顶层字段：
- natural_language: 可直接发给人类伙伴的自然中文回复。结合完整对话、本次任务与真实证据自行决定
  关注什么、怎样表达；逐路图片详情放入 sources，不要求在聊天正文复述每路分析或写检查报告。
  对图片相关的判断保持证据支持的确定程度；仅提交下一步工具动作时，此字段可为空。
- sources: JSON 对象；键只能来自 [{allowed}]，尽量逐路返回。每项包含 label、summary、visible_evidence、confidence。
- fused_summary: 对全部图片的客观综合描述，供后续上下文使用。
- tool_calls: 数组；无需工具时为 []，需要时每项为 {{"tool":"工具名","parameters":{{...}}}}。
{action_contract}

规则：保持每张图片与紧邻其前的来源标签对应；缺少或看不清的证据写入对应 summary/confidence，
不得补造未提供的来源。某一路确实无法分析时也应保留该键并说明不确定；意外漏掉一路时，
系统会把该路标为未分析，不要因此放弃其余结果。必须保留 natural_language、sources、
fused_summary、tool_calls 四个顶层字段。

严格按这个形状输出：
{sample}"""


def parse_vision_envelope(content: Any, expected_keys: Iterable[str] = ()) -> dict[str, Any] | None:
    """Validate and retain every usable part of a provider JSON response.

    The expected source list is authoritative.  Unknown model-created sources
    are dropped, while omitted or malformed expected sources become a partial
    receipt instead of invalidating the natural reply and the other images.
    """
    payload = _decode_json_object(content)
    if payload is None:
        return None
    natural = str(payload.get("natural_language") or "").strip()
    fused = str(payload.get("fused_summary") or "").strip()
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, Mapping):
        raw_sources = {}

    expected = list(dict.fromkeys(str(key).strip() for key in expected_keys if str(key).strip()))
    allowed = set(expected)
    source_keys = expected or [str(key) for key in raw_sources]
    missing_sources: list[str] = []
    unexpected_sources = [str(key) for key in raw_sources if allowed and str(key) not in allowed]

    sources: dict[str, dict[str, Any]] = {}
    for key in source_keys:
        raw = raw_sources.get(key)
        if not isinstance(raw, Mapping):
            missing_sources.append(key)
            continue
        item = dict(raw)
        item["label"] = str(item.get("label") or key).strip()
        item["summary"] = str(item.get("summary") or "").strip()
        item["visible_evidence"] = str(item.get("visible_evidence") or "").strip()
        confidence = item.get("confidence", "unknown")
        item["confidence"] = confidence if isinstance(confidence, (int, float)) else str(confidence or "unknown")
        sources[str(key)] = item

    derived = "；".join(item["summary"] for item in sources.values() if item["summary"])
    fused = fused or derived
    # Visible prose and objective visual evidence are separate channels. A
    # fused summary must never escape as AI's reply when prose is omitted.
    calls = payload.get("tool_calls")
    if not isinstance(calls, list):
        calls = []
    tool_calls = []
    for call in calls:
        if not isinstance(call, Mapping) or not str(call.get("tool") or "").strip():
            continue
        params = call.get("parameters")
        tool_calls.append({
            "tool": str(call.get("tool")).strip(),
            "parameters": dict(params) if isinstance(params, Mapping) else {},
        })

    if not natural and not tool_calls:
        return None

    return {
        "natural_language": natural,
        "sources": sources,
        "fused_summary": fused,
        "tool_calls": tool_calls,
        "missing_sources": missing_sources,
        "unexpected_sources": unexpected_sources,
    }


def storage_description(envelope: Mapping[str, Any] | None, prefix: str = "user_image_") -> str:
    """Compact objective descriptions for the persisted user image marker."""
    if not isinstance(envelope, Mapping):
        return ""
    sources = envelope.get("sources")
    if not isinstance(sources, Mapping):
        return ""
    parts: list[tuple[str, str]] = []
    for key, item in sources.items():
        if not str(key).startswith(prefix) or not isinstance(item, Mapping):
            continue
        summary = str(item.get("summary") or "").strip()
        evidence = str(item.get("visible_evidence") or "").strip()
        detail = summary or evidence
        if detail:
            label = str(item.get("label") or "").strip()
            parts.append((label, detail))
    if len(parts) == 1:
        return parts[0][1]
    return "；".join(
        f"{label or f'图片{index}'}：{detail}"
        for index, (label, detail) in enumerate(parts, start=1)
    )


__all__ = [
    "VISION_RESPONSE_FORMAT",
    "build_vision_contract",
    "vision_image_parts",
    "reserve_vision_source_key",
    "parse_vision_envelope",
    "storage_description",
]
