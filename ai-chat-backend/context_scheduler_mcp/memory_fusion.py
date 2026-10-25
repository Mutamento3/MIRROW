"""
记忆融合层（context_scheduler 子层）
位于检索完成后、上下文构建前。
聚类 + 时效标注 + 矛盾检测，不调 LLM。
"""

import logging
from typing import List, Dict, Optional

from truncation_config import get_truncation_limit

logger = logging.getLogger(__name__)


def fuse_memories(
    memories: list,
    world_book_entries: Optional[list] = None,
    diary_context: str = "",
) -> str:
    """
    融合多来源记忆，返回格式化文本。
    输入 < 5 条时直接返回线性拼接（无需融合）。

    Args:
        memories: MemoryItem 列表，需有 .content / .metadata
        world_book_entries: WorldBookEntry 列表（可选），需有 .name / .body
        diary_context: 日记上下文字符串（可选）

    Returns:
        融合后的格式化文本
    """
    all_items = _collect_items(memories, world_book_entries, diary_context)
    if len(all_items) < 3:
        return ""

    # 聚类：按关键词重叠度分组
    clusters = _cluster_by_keywords(all_items)

    # 每簇内：时效标注 + 矛盾检测
    fused_parts = []
    for cluster in clusters:
        fused = _fuse_cluster(cluster)
        if fused:
            fused_parts.append(fused)

    return "\n\n".join(fused_parts) if fused_parts else ""


def _collect_items(memories, world_book_entries, diary_context) -> list:
    """收集所有来源的记忆项，统一为 dict"""
    items = []

    # 共享检索返回的记忆，保留当前适配器的来源身份。
    for m in memories:
        # source 是 MemoryItem 的直接属性，不在 metadata 中
        if getattr(m, "source", "") == "conversation":
            continue  # 对话记忆不参与融合（已在 LLM 消息历史中）
        meta = getattr(m, "metadata", {}) or {}
        _mcap = get_truncation_limit("memory_inject_cap") or 500
        items.append({
            "source": getattr(m, "source", "") or "memory",
            "content": getattr(m, "content", "")[:_mcap],
            "time_label": meta.get("time_label", ""),
            "emotion_label": meta.get("emotion_label", ""),
            "domain": meta.get("domain", ""),
            "valence": meta.get("valence"),
            "created": meta.get("created", ""),
            "keywords": _extract_keywords(getattr(m, "content", "")[:_mcap]),
        })

    # 世界书条目
    if world_book_entries:
        for wb in world_book_entries:
            name = getattr(wb, "name", "")
            _wcap = get_truncation_limit("worldbook_display_cap") or 300
            body = getattr(wb, "body", "")[:_wcap]
            items.append({
                "source": "world_book",
                "content": f"{name}: {body}",
                "time_label": "长期",
                "emotion_label": "",
                "domain": "",
                "valence": None,
                "created": "",
                "keywords": _extract_keywords(f"{name} {body}"),
            })

    # 日记上下文
    if diary_context:
        _dcap = get_truncation_limit("diary_display_cap") or 500
        items.append({
            "source": "diary",
            "content": diary_context[:_dcap],
            "time_label": "昨天",
            "emotion_label": "",
            "domain": "",
            "valence": None,
            "created": "",
            "keywords": _extract_keywords(diary_context[:_dcap]),
        })

    return items


def _extract_keywords(text: str) -> set:
    """简单提取中文关键词（2-4 字词）"""
    import re
    words = set()
    # 提取中文片段
    for m in re.finditer(r'[一-鿿]{2,4}', text):
        w = m.group()
        # 过滤常见停用词
        if w not in {"一个", "一些", "这个", "那个", "什么", "怎么", "为什么"}:
            words.add(w)
    return words


def _cluster_by_keywords(items: list) -> List[list]:
    """按关键词重叠度贪心聚类"""
    if len(items) <= 1:
        return [items] if items else []

    remaining = list(items)
    clusters = []

    while remaining:
        current = remaining.pop(0)
        cluster = [current]
        current_kws = set(current.get("keywords", []))

        i = 0
        while i < len(remaining):
            other_kws = remaining[i].get("keywords", set())
            if current_kws and other_kws:
                overlap = len(current_kws & other_kws)
                union = len(current_kws | other_kws)
                jaccard = overlap / union if union > 0 else 0
                if jaccard > 0.15:  # 关键词重叠 > 15% → 同簇
                    cluster.append(remaining.pop(i))
                    current_kws |= other_kws
                    continue
            # 检查内容子串匹配
            c1 = current["content"][:100]
            c2 = remaining[i]["content"][:100]
            if _has_common_substring(c1, c2, min_len=4):
                cluster.append(remaining.pop(i))
                current_kws |= other_kws
                continue
            i += 1

        clusters.append(cluster)

    return clusters


def _has_common_substring(a: str, b: str, min_len: int = 4) -> bool:
    """检查两个字符串是否有长度 >= min_len 的公共子串"""
    if len(a) < min_len or len(b) < min_len:
        return False
    shorter = a if len(a) <= len(b) else b
    for size in range(min(min_len + 2, len(shorter)), min_len - 1, -1):
        for i in range(len(shorter) - size + 1):
            sub = shorter[i:i + size]
            if len(sub) >= min_len and sub in (b if shorter == a else a):
                return True
    return False


def _fuse_cluster(cluster: list) -> str:
    """融合单个簇：时效排序 + 矛盾检测"""
    _fcap = get_truncation_limit("fusion_cluster_cap") or 400
    if len(cluster) == 1:
        item = cluster[0]
        label = f"({item['time_label']})" if item["time_label"] else ""
        return f"- {label} {item['content'][:_fcap]}".strip()

    # 同一记忆来源优先级；旧档案标签仅兼容既有只读材料。
    source_order = {"memory_v2": 0, "memory": 0, "ob_rev": 0, "diary": 1, "world_book": 2}
    cluster.sort(key=lambda x: source_order.get(x["source"], 9))

    lines = []
    for item in cluster:
        source_tag = {"memory_v2": "记忆", "memory": "记忆", "ob_rev": "旧记忆",
                      "world_book": "世界书", "diary": "日记"}.get(item["source"], "记忆")
        time_tag = f" · {item['time_label']}" if item["time_label"] else ""
        emotion_tag = f" · {item['emotion_label']}" if item.get("emotion_label") else ""
        meta = f"[{source_tag}{time_tag}{emotion_tag}]"
        lines.append(f"- {meta} {item['content'][:_fcap]}")

    # 矛盾检测：同簇内情感效价相反
    valences = [item["valence"] for item in cluster if item["valence"] is not None]
    if len(valences) >= 2:
        high = [v for v in valences if v >= 0.6]
        low = [v for v in valences if v <= 0.4]
        if high and low:
            lines.append("（注意：以上信息存在情感矛盾，较新的信息可能更准确）")

    return "\n".join(lines)
