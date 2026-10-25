"""Shared conversational vision boundary; observers/OCR keep domain schemas.

Provider IO, shape validation and fallback live here. Evidence never becomes
visible prose, and the caller remains the authority for available tool actions.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from behavior_scheduler.vision_envelope import (
    VISION_RESPONSE_FORMAT, build_vision_contract, parse_vision_envelope, vision_image_parts, reserve_vision_source_key,
)

logger = logging.getLogger(__name__)


def tool_visual_images(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract only fresh, unanalyzed tool images, retaining exact source IDs."""
    extra = receipt.get("extra_data") or {}
    if extra.get("vision_analyzed") is True:
        return []
    images = [item for item in extra.get("images") or []
              if isinstance(item, dict) and item.get("image_url")]
    if not images and extra.get("image_base64"):
        tool = str(receipt.get("tool") or "tool")
        images = [{
            "source_key": tool, "label": receipt.get("description") or tool,
            "image_url": "data:image/jpeg;base64," + extra["image_base64"],
        }]
    return images


async def generate_visual_dialogue(
    messages: list[dict[str, Any]], images: list[dict[str, Any]], *,
    turn_text: str = "", allow_tools: bool = True,
) -> dict[str, Any]:
    """Keep full source context and append one source-labelled visual turn.

    A source omitted by the model is partial, not a reason to regenerate good
    prose. Empty/invalid envelopes or transport failures use one fallback.
    """
    from llm_client import call_llm_api_visual

    sources, content = [], []
    seen: set[str] = set()
    for index, image in enumerate(images, 1):
        if not image.get("image_url"):
            continue
        key = reserve_vision_source_key(image, seen, f"visual_image_{index}")
        seen.add(key)
        label = str(image.get("label") or key)
        sources.append({"key": key, "label": label})
        content.extend(vision_image_parts(image["image_url"], key, label))
    if not sources:
        raise ValueError("No visual images supplied")
    content.append({"type": "text", "text": (
        "【后端视觉材料与本轮运行合同，不是人类伙伴的新发言】\n" + turn_text
        + "\n" + build_vision_contract(sources, allow_tools=allow_tools)
    )})
    request = [*messages, {"role": "user", "content": content}]
    for model in ("flash-vision", "glm"):
        try:
            raw = await call_llm_api_visual(deepcopy(request), model, response_format=VISION_RESPONSE_FORMAT)
            envelope = parse_vision_envelope(raw.get("content", ""), [s["key"] for s in sources])
            if envelope is None:
                raise ValueError("Invalid visual dialogue envelope")
            if not allow_tools and envelope["tool_calls"]:
                raise ValueError("Tool actions returned in final-expression stage")
            return {
                "content": envelope["natural_language"],
                "reasoning": raw.get("reasoning") or "",
                "tool_calls": envelope["tool_calls"], "vision_envelope": envelope,
            }
        except Exception as exc:
            # No provider body, network address or personal evidence in logs.
            logger.warning("Visual dialogue %s failed (%s)", model, type(exc).__name__)
    raise ValueError("视觉模型未返回有效的对话响应")


def apply_visual_receipts(records: list[dict[str, Any]], envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Consume exact source analyses without replacing unrelated tool facts."""
    completed = []
    for record in records:
        images = tool_visual_images(record)
        extra = record.get("extra_data") or {}
        if not images and record.get("tool") == "eyes" and not extra.get("vision_analyzed"):
            images = [{"source_key": row.get("source"), "label": row.get("label")}
                      for row in extra.get("sources") or [] if isinstance(row, dict)]
        if not images:
            continue
        source_rows = extra.get("sources")
        # check_phone owns a structured state dictionary; Eyes owns a list.
        rows = source_rows if isinstance(source_rows, list) else []
        missing, summaries = [], []
        for index, image in enumerate(images, 1):
            key = str(image.get("source_key") or f"visual_image_{index}")
            label = str(image.get("label") or key)
            physical_key = image.get("physical_source_key") or key
            row = next((r for r in rows if isinstance(r, dict) and r.get("source") in (key, physical_key)), None)
            if row is None:
                row = {"source": key, "label": label}
                rows.append(row)
            row["source"] = key
            row.pop("image_url", None)
            row["captured"] = True
            attachment = image.get("attachment") or (extra.get("attachment") if len(images) == 1 else None)
            if attachment:
                row["attachment"] = attachment
            analysis = envelope.get("sources", {}).get(key) or {}
            row["analysis"] = analysis
            row["confidence"] = analysis.get("confidence", "pending")
            summary = str(analysis.get("summary") or analysis.get("visible_evidence") or "").strip()
            if summary:
                summaries.append(f"{label}：{summary}")
            else:
                missing.append(label)
            if record.get("tool") == "check_phone" and isinstance(source_rows, dict):
                source_rows.setdefault("screenshot", {}).update({
                    "analysis": summary, "analysis_available": bool(summary),
                })
        if isinstance(source_rows, list) or record.get("tool") == "eyes":
            extra["sources"] = rows
        else:
            extra["visual_sources"] = rows
        if record.get("tool") == "eyes":
            record["result"] = str(envelope.get("fused_summary") or "；".join(summaries) or "已取得画面，尚无可用分析")
        elif summaries:
            record["result"] = str(record.get("result") or "") + "\n" + "；".join(summaries)
        if record.get("tool") == "check_phone":
            record["result"] = str(record.get("result") or "").replace(
                "已取得手机截屏，原图随本次工具结果提供，尚待统一视觉分析。",
                "已完成统一视觉分析。" if summaries else "已取得画面，本轮未返回可用图片分析。",
            )
        extra["vision_analyzed"] = True
        extra["vision_analysis_partial"] = bool(missing)
        if missing:
            extra["analysis_missing_sources"] = missing
        else:
            extra.pop("analysis_missing_sources", None)
        record["status"] = "partial" if missing or extra.get("errors") else "success"
        extra.pop("images", None)
        extra.pop("image_base64", None)
        record["extra_data"] = extra
        completed.append(record)
    return completed


def fail_visual_receipts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Finish every pending capture truthfully when visual providers fail."""
    pending = [record for record in records if tool_visual_images(record)]
    expected = [image.get("source_key") for record in pending for image in tool_visual_images(record)]
    apply_visual_receipts(pending, {"sources": {}, "missing_sources": expected})
    for record in pending:
        record["status"] = "partial"
        record["error"] = "统一视觉分析失败"
        record["result"] = str(record.get("result") or "") + "\n统一视觉分析失败。"
    return pending
