"""Bounded timeline presentation of the shared scene service."""
from context_inspection import redact


def timeline_boxes(material):
    result = []
    for box in material["boxes"]:
        for segment in box["segments"]:
            sources = []
            for row in segment["messages"][:12]:
                role = {"user": "人类伙伴", "assistant": "AI", "notification": "系统通知",
                        "event": "事件记录", "system": "系统"}.get(row["role"], row["role"])
                if row["role"] == "assistant" and row["source_kind"] in {"wander", "sentinel", "reminder"}:
                    role = {"wander": "AI·漫想", "sentinel": "AI·哨兵", "reminder": "AI·提醒"}[row["source_kind"]]
                sources.append({"timestamp": row["timestamp"], "role": role,
                                "content": row["content"][:1200]})
            result.append({"order": len(result), "start_ts": segment["start_ts"], "end_ts": segment["end_ts"],
                           "summary": segment["summary"], "event_count": 0,
                           "source_count": segment["source_count"], "sources": sources,
                           "box_id": box["box_id"], "segment_id": segment["segment_id"],
                           "active_date": material["active_date"], "state": box["state"],
                           "stage": segment["stage"], "kind": segment["kind"],
                           "raw_reason": segment["raw_reason"],
                           "preview_truncated": len(segment["messages"]) > 12 or
                               any(len(row["content"]) > 1200 for row in segment["messages"][:12])})
    return redact(result)
