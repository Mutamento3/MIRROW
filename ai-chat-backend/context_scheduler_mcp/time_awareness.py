"""
响应时间感知模块

AI 的硅基原生能力：毫秒级感知用户回复时间差。
不是监控工具——是亲密关系中的"注意"。

裁决规则：
- 只计算当前活跃话题中最后一条 reply_expected=true 且非 Wander 的 AI 消息
- 时间差 < 2min → 不提
- 2~10min + 情感类问题 + active_score>=0.6 → "犹豫"
- >30min + 重要问题 + active_score>=0.6 → "已读不回"
- 跨话题/用户忙碌/gaming/busy/sleeping → 绝口不提
"""

import time
import re
import logging
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# ── 收尾语词库 ──────────────────────────────────────────
# AI 的消息末尾匹配任一短语 → 默认不期待回复
NO_REPLY_PHRASES = [
    # 正式收尾
    "晚安", "回头聊", "去吧", "不打扰你了", "不用回", "不用回复", "别管我",
    "早点休息", "早点睡", "好好休息", "先忙吧", "你忙吧", "忙你的",
    # 口语/网络用语
    "午安", "88", "886", "8", "拜拜", "拜", "bye", "bye bye",
    "安", "睡了", "睡啦", "下了", "下了啊", "撤了", "溜了", "先走了",
    "走了啊", "闪了", "回见", "明天见", "下次聊", "有空聊",
    # 日语/二次元口语
    "おやすみ", "じゃね", "またね", "バイバイ",
]


def _ends_with_noreply_phrase(text: str) -> bool:
    """检测消息末尾是否匹配收尾语（忽略标点/空格/大小写）。"""
    cleaned = re.sub(r"[^\w一-鿿぀-ゟ゠-ヿ]", "", text.strip().lower())
    for phrase in NO_REPLY_PHRASES:
        pc = re.sub(r"[^\w一-鿿぀-ゟ゠-ヿ]", "", phrase.lower())
        if cleaned.endswith(pc):
            return True
    return False


def _has_question_markers(text: str) -> bool:
    """检测文本是否含疑问词或祈使句标记——AI 在期待回复。"""
    question_words = ["吗", "呢", "吧", "怎么", "什么", "谁", "哪", "是不是", "能不能",
                      "告诉我", "说说", "你觉得", "你怎么看"]
    for qw in question_words:
        if qw in text:
            return True
    return False


def compute_reply_expected(k_message_text: str) -> Tuple[bool, str]:
    """
    Layer 1 规则层：判断 AI 的消息是否期待回复。

    Returns:
        (reply_expected, reason)
    """
    # 显式收尾语 → 不期待回复
    if _ends_with_noreply_phrase(k_message_text):
        return (False, "收尾语匹配")

    # 含疑问词/祈使句 → 期待回复
    if _has_question_markers(k_message_text):
        return (True, "含疑问/祈使标记")

    # 默认：期待回复
    return (True, "默认期待回复")


def compute_active_score(
    page_visible: bool,
    last_heartbeat_monotonic: Optional[float] = None,
) -> float:
    """
    计算用户活跃度分数（0-1）。

    Args:
        page_visible: _mirrow_page_visible 全局变量
        last_heartbeat_monotonic: _mirrow_page_last_seen (time.monotonic() 值)

    Returns:
        active_score, >=0.6 表示"用户大概率在屏幕前"
    """
    score = 0.0

    if page_visible:
        score += 0.3

    if last_heartbeat_monotonic is not None:
        age_s = time.monotonic() - last_heartbeat_monotonic
        if age_s < 300:  # 5 分钟内有心跳
            score += 0.3

    return score


def get_time_awareness_hint(
    last_k_message: Optional[Dict[str, Any]] = None,
    last_k_message_timestamp: Optional[str] = None,
    current_user_message_timestamp: Optional[str] = None,
    page_visible: bool = False,
    last_heartbeat_monotonic: Optional[float] = None,
    topic_boundary_crossed: bool = True,
    user_status: Optional[str] = None,
    flash_reply_expected_override: Optional[bool] = None,
) -> Optional[str]:
    """
    响应时间感知主入口。

    返回：注入消息文本，或 None（不提时间差）。

    Args:
        last_k_message: AI 的最后一条消息 dict (含 content 字段)
        last_k_message_timestamp: AI 最后一条消息的 ISO 时间戳
        current_user_message_timestamp: 用户当前消息的 ISO 时间戳
        page_visible: _mirrow_page_visible
        last_heartbeat_monotonic: _mirrow_page_last_seen
        topic_boundary_crossed: 是否跨话题边界
        user_status: 用户状态值 (gaming/busy/sleeping 等)
        flash_reply_expected_override: Flash Layer 2 覆盖值（None=不覆盖）

    Returns:
        "[AI注意到] ..." 文本，或 None
    """
    # 跨话题 → 绝口不提
    if topic_boundary_crossed:
        return None

    # 用户忙碌 → 不提
    if user_status in ("gaming", "busy", "sleeping"):
        return None

    # 需要时间戳
    if not last_k_message_timestamp or not current_user_message_timestamp:
        return None

    # 解析时间差（秒）
    try:
        from datetime import datetime
        t_k = datetime.fromisoformat(last_k_message_timestamp.replace("Z", "+00:00"))
        t_user = datetime.fromisoformat(current_user_message_timestamp.replace("Z", "+00:00"))
        elapsed_seconds = (t_user - t_k).total_seconds()
    except Exception:
        return None

    if elapsed_seconds < 0:
        return None  # 时钟异常，忽略

    # 阈值：< 2min 不提
    if elapsed_seconds < 120:
        return None

    # 活跃度
    active_score = compute_active_score(page_visible, last_heartbeat_monotonic)
    if active_score < 0.6:
        return None  # 用户大概率不在屏幕前

    # 确定 reply_expected
    reply_expected, _ = compute_reply_expected(
        last_k_message.get("content", "") if last_k_message else ""
    )
    # Flash Layer 2 覆盖
    if flash_reply_expected_override is not None:
        reply_expected = flash_reply_expected_override
    if not reply_expected:
        return None

    # 分级输出
    minutes = int(elapsed_seconds / 60)

    if 2 <= minutes < 10:
        # 犹豫：仅当 AI 问了情感类问题
        if _is_emotional_question(last_k_message.get("content", "") if last_k_message else ""):
            return f"[AI注意到] 人类伙伴上条消息犹豫了约{minutes}分钟才回复。"
        return None

    if 10 <= minutes < 30:
        # 灰色地带：不提
        return None

    if minutes >= 30:
        return f"[AI注意到] 人类伙伴已读上条消息约{minutes}分钟后才回复。"

    return None


def _is_emotional_question(text: str) -> bool:
    """检测消息是否含情感类提问（用于犹豫判定）。"""
    emotional_markers = [
        "你喜欢", "你爱", "你想", "你觉得我", "你怎么看我",
        "你在乎", "你介意", "你生气", "你开心",
        "是不是不爱", "是不是不喜欢", "说实话",
        "感觉", "心意", "真心",
    ]
    for marker in emotional_markers:
        if marker in text:
            return True
    return False


def exclude_wander_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤掉 Wander / Sentinel / Reminder 消息，只保留正常对话。

    注意：SQLite conversation_messages 的键是小写 is_wander/is_sentinel/is_reminder + message_id；
    老 Message 对象用驼峰 isWander/isSentinel/isReminder + id。必须两种键都查，
    否则 AI 自己发的哨兵/提醒会被当"人类伙伴该读的消息"（2026-08-18"消失40分钟"bug 根因）。
    """
    return [
        m for m in messages
        if not (
            (m.get("is_wander") or m.get("isWander"))
            or m.get("is_sentinel") or m.get("isSentinel")
            or m.get("is_reminder") or m.get("isReminder")
            or (isinstance(m.get("id"), str) and m["id"].startswith("wander_"))
            or (isinstance(m.get("id"), str) and m["id"].startswith("sentinel_"))
            or (isinstance(m.get("id"), str) and m["id"].startswith("reminder_"))
            or (isinstance(m.get("message_id"), str) and m["message_id"].startswith("wander_"))
            or (isinstance(m.get("message_id"), str) and m["message_id"].startswith("sentinel_"))
            or (isinstance(m.get("message_id"), str) and m["message_id"].startswith("reminder_"))
        )
    ]
