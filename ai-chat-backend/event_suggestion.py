"""
事件建议引擎 — 检测值得记录为重要事件的对话内容

两阶段过滤：
  1. 关键词粗筛（零成本，挡掉日常对话）
  2. Flash LLM 评分（加权打分，精判）
"""
import re
import json
import hashlib
import logging
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── 第一阶段关键词 ──────────────────────────────

MILESTONE_KEYWORDS = re.compile(
    r'第一次|终于|成功了|失败了|入职|离职|毕业|搬家|'
    r'分手|在一起|结婚|生日|旅行|买了|实现了|'
    r'确诊|出院|考试|比赛|得奖|辞职|拿到'
)

EMOTIONAL_KEYWORDS = re.compile(
    r'太开心了|好难过|太高兴了|好激动|好兴奋|崩溃|'
    r'难忘|永远不会忘|很重要|值得纪念|开心的要死'
)

TIME_ANCHOR_KEYWORDS = re.compile(
    r'上周|这个月|去年|今年|明年|本周|本月|昨天|今天|明天|'
    r'20\d{2}年|星期[一二三四五六日]'
)

# ── 防骚扰状态 ──────────────────────────────────

_suggest_cooldowns: Dict[str, datetime] = {}
_suggestion_hashes: set = set()
_suggestion_counts: Dict[str, int] = {}
_suppressed_sessions: set = set()

MIN_INTERVAL_SEC = 300       # 同 session 最小间隔 5 分钟
MAX_PER_DAY = 5              # 每日上限
CONTENT_HASH_PREFIX_LEN = 8  # 去重哈希长度


@dataclass
class SuggestionResult:
    should_suggest: bool
    confidence: float
    suggested_text: str
    suggested_date: str
    suggested_emotion: str
    reason: str


def _keyword_filter(text: str) -> bool:
    """第一阶段：关键词粗筛"""
    if MILESTONE_KEYWORDS.search(text):
        return True
    if EMOTIONAL_KEYWORDS.search(text):
        return True
    if TIME_ANCHOR_KEYWORDS.search(text):
        return True
    return False


def _should_suppress(session_id: str) -> bool:
    """检查是否应该抑制建议"""
    if session_id in _suppressed_sessions:
        return True
    now = datetime.now()
    last = _suggest_cooldowns.get(session_id)
    if last and (now - last).total_seconds() < MIN_INTERVAL_SEC:
        return True
    today_count = _suggestion_counts.get(session_id, 0)
    if today_count >= MAX_PER_DAY:
        return True
    return False


def _dedup(content: str) -> bool:
    """检查内容是否已建议过，返回 True 表示重复"""
    h = hashlib.md5(content.encode()).hexdigest()[:CONTENT_HASH_PREFIX_LEN]
    if h in _suggestion_hashes:
        return True
    _suggestion_hashes.add(h)
    return False


def _extract_date_from_text(text: str) -> str:
    """只提取原文明确写出的完整有效日期；无法确认时留空。"""
    for match in re.finditer(r'(?<!\d)(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})(?:日)?(?!\d)', text):
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ""


async def evaluate_event_suggestion(
    user_text: str,
    flash_llm_func,
    session_id: str = "",
) -> SuggestionResult:
    """评估用户消息是否值得记录为重要事件

    Args:
        user_text: 用户消息
        flash_llm_func: Flash LLM 调用函数 async (prompt: str) -> str
        session_id: 会话 ID，用于频率抑制

    Returns:
        SuggestionResult
    """
    # 第一阶段：关键词粗筛
    if not _keyword_filter(user_text):
        return SuggestionResult(
            should_suggest=False, confidence=0.0,
            suggested_text="", suggested_date="", suggested_emotion="",
            reason="未命中关键词"
        )

    # 防骚扰
    if _should_suppress(session_id):
        return SuggestionResult(
            should_suggest=False, confidence=0.0,
            suggested_text="", suggested_date="", suggested_emotion="",
            reason="频率限制或用户已抑制"
        )

    if _dedup(user_text):
        return SuggestionResult(
            should_suggest=False, confidence=0.0,
            suggested_text="", suggested_date="", suggested_emotion="",
            reason="内容重复"
        )

    _suggest_cooldowns[session_id] = datetime.now()
    _suggestion_counts[session_id] = _suggestion_counts.get(session_id, 0) + 1

    # 第二阶段：Flash LLM 评分
    extracted_date = _extract_date_from_text(user_text)

    prompt = f"""分析以下用户消息，判断是否值得记录为"重要事件"。

评分标准 (0-10)：
- 有明确日期或时间点: /10
- 情感强度: /10
- 人生里程碑类型（工作/感情/健康/搬家/旅行）: /10
- 用户主动标记重要（如"这件事很重要"）: /10
- 非日常性（不是"吃了什么""今天天气"）: /10

用户消息: {user_text}

返回 JSON 格式（不要其他内容）：
{{"scores": {{"has_date": N, "emotion": N, "milestone": N, "marked_important": N, "non_routine": N}}, "event_text": "简短事件描述", "emotion": "情感标签"}}"""

    try:
        raw = await flash_llm_func(prompt)
        raw = raw.strip()
        # 容错：去掉可能的 markdown 代码块包裹
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(raw)
        scores = data.get("scores", {})

        weights = {"has_date": 0.25, "emotion": 0.25, "milestone": 0.25,
                   "marked_important": 0.15, "non_routine": 0.10}
        confidence = sum(
            weights.get(k, 0) * scores.get(k, 0) / 10.0 for k in weights
        )

        if confidence >= 0.7:
            return SuggestionResult(
                should_suggest=True, confidence=confidence,
                suggested_text=data.get("event_text", user_text[:50]),
                suggested_date=extracted_date,
                suggested_emotion=data.get("emotion", ""),
                reason=f"评分 {confidence:.2f} >= 0.7"
            )
        elif confidence >= 0.4:
            return SuggestionResult(
                should_suggest=False, confidence=confidence,
                suggested_text=data.get("event_text", ""),
                suggested_date=extracted_date,
                suggested_emotion=data.get("emotion", ""),
                reason=f"评分 {confidence:.2f} 在弱提示区间"
            )
        else:
            return SuggestionResult(
                should_suggest=False, confidence=confidence,
                suggested_text="", suggested_date="", suggested_emotion="",
                reason=f"评分 {confidence:.2f} < 0.4"
            )
    except Exception as e:
        logger.warning(f"事件建议 Flash 评分失败: {e}")
        return SuggestionResult(
            should_suggest=False, confidence=0.0,
            suggested_text="", suggested_date="", suggested_emotion="",
            reason=f"Flash 评分异常: {e}"
        )


def suppress_session(session_id: str):
    """用户点击"不再提示"后调用"""
    _suppressed_sessions.add(session_id)


def reset_daily_counts():
    """每日重置（由 cron 调用）"""
    _suggestion_counts.clear()
    _suggestion_hashes.clear()
    logger.info("事件建议每日计数器已重置")
