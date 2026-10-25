"""Read-only music overviews; source records remain the detailed authority.

Grouping uses recorded identity, never similarity of feelings. First/last
reactions are explicitly samples; intermediate prose is not declared redundant.
"""
from collections import Counter
import json

from .music_phases import experience_phases


_MODES = {
    "listening": "听歌体验",
    "analysis": "歌曲材料分析（本记录不证明实际播放）",
    "observed_playback": "有播放观察的体验",
    "manual_note": "手动音乐笔记",
}
_COMPLETIONS = {
    "listening_finished": "听歌完成",
    "analysis_complete": "分析完成，未播放",
    "playback_finished": "播放完成",
}


def experience_overview(items: list[dict], subject_label: str, *, provided_node_reflections=None) -> str:
    """Project an already selected chronological window without writing it.

    Legacy records have title/artist, not a verified recording ID. Their exact
    labels can be grouped for presentation, but never establish version identity.
    Post-experience dimensions retain chronological values, including a return
    to an earlier value. Selection motives live in the original event/record,
    not in the extra experience ingredient as apparent musical developments.
    """
    if not items:
        return ""
    groups = {}
    for index, item in enumerate(items):
        title, artist = item["title"], item["artist"]
        key = (item["subject_id"], title, artist)
        if not title or not artist:
            key = ("unresolved", index)
        groups.setdefault(key, []).append((index + 1, item))

    lines = [
        f"{subject_label}的近期音乐经历概览（所选窗口共 {len(items)} 条记录）：",
        "归组依据为原记录中相同的曲名、歌手与体验主体，歌曲版本未另行核对。",
        "次数是记录次数，不等于实际播放次数或稳定偏好。感受属于各自发生时间；"
        "首末原述是样本，中间原述仍保存在来源记录中，并未被判定为同义。",
        "本段回看已结束的音乐经历。选歌前的起意与体验后感受分开存放；"
        "起意仍在逐次来源记录，本概览不将其列作体验维度或当前待办。",
    ]
    # Last occurrence orders groups, not their first appearance in the window.
    for group in sorted(groups.values(), key=lambda group: group[-1][0]):
        first, last = group[0][1], group[-1][1]
        lines.append(f"歌曲：《{last['title']}》— {last['artist']}；记录 {len(group)} 次；"
                     f"时间：{first['created_at']} 至 {last['created_at']}")
        modes = Counter(item["mode"] for _, item in group)
        lines.append("材料构成：" + "；".join(
            f"{_MODES.get(mode, '未确认材料类型')} {count} 条"
            for mode, count in modes.items()))

        provided = provided_node_reflections or {}
        represented = {index for index, item in group
                       if item.get("source") == "AI 漫想节点反思"
                       and item["source_id"].startswith("wander-node:")
                       and item["reaction"]
                       and provided.get(item["source_id"][len("wander-node:"):]) == item["reaction"]}
        if represented:
            lines.append("原述感受已在本事件段对应节点材料中提供：" + "、".join(
                f"e{index}" for index in sorted(represented)))
        reactions = [(index, item["reaction"]) for index, item in group
                     if item["reaction"] and index not in represented]
        if reactions:
            first_reaction, last_reaction = reactions[0], reactions[-1]
            if first_reaction[1] == last_reaction[1]:
                refs = f"e{first_reaction[0]}"
                if first_reaction[0] != last_reaction[0]:
                    refs += f"、e{last_reaction[0]}（首末原述逐字相同）"
                lines.append(f"原述感受样本 [{refs}]：{first_reaction[1]}")
            else:
                lines.append(f"首次原述感受 [e{first_reaction[0]}]：{first_reaction[1]}")
                lines.append(f"最近原述感受 [e{last_reaction[0]}]：{last_reaction[1]}")
            sample_indices = {first_reaction[0], last_reaction[0]}
            remaining = [index for index, _ in reactions if index not in sample_indices]
            if remaining:
                lines.append("中间原述未在此展开：" + "、".join(f"e{index}" for index in remaining))

        dimensions = {}
        for index, item in group:
            for dimension, value in experience_phases(item)["dimensions"].items():
                if not value:
                    continue
                changes = dimensions.setdefault(dimension, [])
                if changes and changes[-1]["value"] == value:
                    changes[-1]["refs"].append(f"e{index}")
                else:
                    changes.append({"value": value, "refs": [f"e{index}"]})
        if dimensions:
            lines.append("体验/笔记维度原述沿时间的取值（仅连续逐字相同项归并，非语义总结）：")
            for dimension, changes in dimensions.items():
                lines.append(dimension + "：" + " → ".join(
                    f"[{','.join(change['refs'])}] {change['value']}" for change in changes))

        lines.append("逐次来源索引：")
        for index, item in group:
            lines.append(f"e{index}：{item['created_at']}；music-note:{item['id']}；"
                         f"原来源：{item['source_id']}；{item['source']}；材料：{item['mode']}")
    return "\n".join(lines)


def recent_node_overview(facts: list[dict]) -> str:
    """Group selected successful node facts by exact fingerprint and labels.

    The caller's window is unchanged. Counts describe only these supplied facts,
    not a full three-day total; all completion times and statuses remain visible.
    """
    groups = {}
    for index, fact in enumerate(facts):
        fingerprint = fact.get("fingerprint")
        key = (fingerprint, fact.get("title"), fact.get("artist")) if fingerprint else (index,)
        groups.setdefault(key, []).append(fact)
    projected = []
    for group in groups.values():
        song = group[0]
        projected.append({
            "fingerprint": song.get("fingerprint"),
            "title": song.get("title"), "artist": song.get("artist"),
            "record_count": len(group),
            "occurrences": [{
                "node_id": fact.get("node_id"), "activity_id": fact.get("activity_id"),
                "completed_at": fact.get("completed_at"),
                "completion_signal": fact.get("completion_signal"),
                "experience": _COMPLETIONS.get(fact.get("completion_signal"), "节点完成，播放状态未确认"),
            } for fact in reversed(group)],
        })
    return json.dumps({"window_record_count": len(facts), "songs": projected}, ensure_ascii=False)
