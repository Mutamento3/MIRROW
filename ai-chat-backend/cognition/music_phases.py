"""Read-only stage attribution for music records, including the known old writer.

Only that writer's provenance establishes that its 情境 field was a selection
reason. Ordinary/manual notes keep their original dimensions. No text semantics
or similarity are used to infer a phase, preference, or unfinished intention.
"""


def experience_phases(item: dict) -> dict:
    dimensions = dict(item.get("dimensions") or {})
    reason = str(item.get("selection_reason") or "")
    legacy_selection = (
        "selection_reason" not in item
        and item.get("source") == "AI 漫想节点反思"
        and str(item.get("source_id") or "").startswith(("wander-node:", "wander-legacy:"))
    )
    if legacy_selection:
        reason = dimensions.pop("情境", "")
    return {
        "selection_reason": reason,
        "selection_reason_phase": "当次选歌前的起意" if reason else "未记录选歌起意",
        "reaction": str(item.get("reaction") or ""),
        "dimensions": dimensions,
    }
