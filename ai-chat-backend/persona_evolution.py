"""
人格自我演化建议系统 v2
每日一次触发（日锁），日全文 + ❤️💔 内联标记 → Flash → 结构化人设段落。
支持 add / modify / deprecate 三种操作 + 过期机制。
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/persona_evolution_state.json"
SUGGESTIONS_FILE = "data/persona_suggestions.json"

# ── 演化段落分隔符（在 systemPrompt 中标记演化区） ──
EVO_SEPARATOR = "\n\n---\n# 人格演化（自动管理，请勿手动编辑）"
EVO_CLOSER = "\n---"

# ── 支持的目标段落（用于冲突检测） ──
VALID_SECTIONS = ["语气风格", "行为边界", "关系定位", "性互动", "日常互动", "全局"]

# ═══════════════════════════════════════════════════════════════════════
# 触发状态管理
# ═══════════════════════════════════════════════════════════════════════

def get_evolution_state() -> dict:
    """读取演化状态，自动补全缺失字段（兼容旧格式）"""
    defaults = {
        "topic_count": 0,
        "last_detection_date": "",
        "active_evolutions": [],
        "last_daily_fulltext_date": "",
    }
    if not os.path.exists(STATE_FILE):
        return dict(defaults)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        # 补全 v2 新增字段
        for key, val in defaults.items():
            if key not in state:
                state[key] = val
        return state
    except Exception:
        return dict(defaults)


def save_evolution_state(state: dict):
    """持久化演化状态"""
    os.makedirs("data", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_trigger_today() -> bool:
    """检查今天是否应该触发（日锁：今天还没触发过）"""
    return should_trigger_date(datetime.now().strftime("%Y-%m-%d"))


def mark_triggered_today():
    """标记今天已触发"""
    mark_triggered_date(datetime.now().strftime("%Y-%m-%d"))


def should_trigger_date(target_date: str) -> bool:
    """检查指定活跃日是否尚未完成检测。"""
    state = get_evolution_state()
    return state.get("last_daily_fulltext_date", "") != target_date


def mark_triggered_date(target_date: str):
    """仅在指定活跃日的检测成功解析后消费日锁。"""
    state = get_evolution_state()
    state["last_daily_fulltext_date"] = target_date
    save_evolution_state(state)


# ═══════════════════════════════════════════════════════════════════════
# 建议 CRUD
# ═══════════════════════════════════════════════════════════════════════

def get_suggestions() -> list:
    """获取全部建议列表"""
    if not os.path.exists(SUGGESTIONS_FILE):
        return []
    try:
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_suggestions(suggestions: list):
    """保存建议列表"""
    os.makedirs("data", exist_ok=True)
    with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)


def get_pending_suggestions() -> list:
    """获取待处理建议"""
    return [s for s in get_suggestions() if s.get("status") == "pending"]


def get_active_suggestions() -> list:
    """获取已采纳且未过期的建议，合并 state 中的 expires_at"""
    # 读取活跃列表时顺便回收自然过期条目，保证前端历史页能看到最新状态。
    expire_outdated_evolutions()
    state = get_evolution_state()
    evo_map = {e["id"]: e for e in state.get("active_evolutions", [])}
    results = []
    for s in get_suggestions():
        sid = s.get("id")
        if sid in evo_map and s.get("status") == "applied":
            # 注入 state 中的 expires_at（suggestions 文件不存此字段）
            s = dict(s)
            s["expires_at"] = evo_map[sid].get("expires_at")
            if "expires_days" not in s and "expires_days" in evo_map[sid]:
                s["expires_days"] = evo_map[sid]["expires_days"]
            results.append(s)
    return results


def _make_suggestion_ids(count: int = 1, existing: Optional[list] = None) -> list[str]:
    """生成一批不重复的建议 ID。

    旧实现按当天记录数量计算序号，但检测结果是在整批返回后才落盘，
    所以同一批建议拿到了同一个 ID。这里先读取一次已有 ID，再在内存中
    连续分配序号，保证同一批和已有记录都不会碰撞。
    """
    if count <= 0:
        return []

    now = datetime.now()
    prefix = f"pe_{now.strftime('%Y%m%d')}_"
    records = get_suggestions() if existing is None else existing
    used = {
        str(s.get("id", ""))
        for s in records
        if isinstance(s, dict) and s.get("id")
    }

    # 取当天已有的最大序号，而不是简单计数；历史上可能存在空洞或重复序号。
    max_seq = 0
    for sid in used:
        if not sid.startswith(prefix):
            continue
        try:
            max_seq = max(max_seq, int(sid[len(prefix):]))
        except ValueError:
            continue

    result = []
    next_seq = max_seq + 1
    while len(result) < count:
        sid = f"{prefix}{next_seq:03d}"
        next_seq += 1
        if sid in used:
            continue
        used.add(sid)
        result.append(sid)
    return result


def _make_suggestion_id() -> str:
    """生成单个不重复的建议 ID（兼容旧调用方）。"""
    return _make_suggestion_ids(1)[0]


# ═══════════════════════════════════════════════════════════════════════
# 日全文 + 反馈内联构建
# ═══════════════════════════════════════════════════════════════════════

def _build_daily_fulltext_with_feedback(target_date: str) -> str:
    """
    构建带 ❤️💔 内联标记的日全文。
    从 SQLite conversation_messages 读取全天消息，
    对每条 AI 的回复查询 message_feedback 表，有反馈则追加标记。
    """
    try:
        from event_chronicle import get_global_chronicle, _read_all_messages_for_date
        chronicle = get_global_chronicle()
        if not chronicle:
            return ""

        messages = _read_all_messages_for_date(target_date)
        if not messages:
            return ""

        # 按 message_id 批量查询反馈
        import sqlite3
        assistant_msg_ids = [
            m.get("message_id", m.get("id", ""))
            for m in messages
            if m.get("role") == "assistant"
        ]
        feedback_map = {}
        if assistant_msg_ids:
            with chronicle._lock:
                with sqlite3.connect(chronicle.db_path, timeout=3) as conn:
                    placeholders = ",".join("?" for _ in assistant_msg_ids)
                    rows = conn.execute(
                        f"SELECT message_id, feedback_type, reason FROM message_feedback WHERE message_id IN ({placeholders}) ORDER BY id",
                        assistant_msg_ids
                    ).fetchall()
            for row in rows:
                mid = row[0]
                if mid not in feedback_map:
                    feedback_map[mid] = []
                feedback_map[mid].append({"type": row[1], "reason": row[2] or ""})

        # 构建带标记的文本
        lines = [f"{target_date} 对话记录：", "---"]
        for m in messages:
            role = m.get("role", "")
            if role not in ("user", "assistant"):
                continue

            content = (m.get("content", "") or "").strip()
            if len(content) < 2:
                continue

            ts = m.get("timestamp", "")
            time_str = ts[11:16] if len(ts) >= 16 else ""

            if role == "user":
                lines.append(f"{time_str} 【人类伙伴说】{content[:500]}")
            elif role == "assistant":
                mid = m.get("message_id", m.get("id", ""))
                lines.append(f"{time_str} 【AI回应】{content[:500]}")

                # 内联反馈标记
                fbs = feedback_map.get(mid, [])
                for fb in fbs:
                    icon = "👍" if fb["type"] == "like" else "👎"
                    label = "赞" if fb["type"] == "like" else "踩"
                    reason_suffix = f" | 原因：{fb['reason'][:100]}" if fb["reason"] else ""
                    lines.append(f"  → {icon} {label}{reason_suffix}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"构建日全文失败: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════════
# Flash 检测 Prompt（v2）
# ═══════════════════════════════════════════════════════════════════════

DETECTION_PROMPT = """你是行为分析师，正在观察人类伙伴与 AI 伴侣"AI"的互动模式。

你的任务：从今天的对话中提取人类伙伴的**行为偏好变化**——不是措辞微调，而是人类伙伴**喜欢/不喜欢 AI 怎么做**的模式级信号。

## 什么是"行为模式"（值得记录）

- 人类伙伴反复纠正 AI 的某种行为（如"别在我说正事时开玩笑"）→ 这是一个行为边界
- 人类伙伴对某类回应特别满意（如认真倾听后说"你懂我"）→ 这是一个正面模式
- 人类伙伴明确表达了新的需求（如"我最近很累，不用每天哄我"）→ 这是一个状态适配
- 人类伙伴的 ❤️💔 反馈集中在某类行为上 → 强信号

## 什么**不是**行为模式（不要记录）

- 措辞层面的偏好（如"别叫我宝宝"→ 这是单次纠正，不是模式）
- AI 的用词风格建议（如"用共生替代嫁娶"→ 太细）
- 一次性的事件反应（只有一条证据，没有重复）
- 人类伙伴没有明确表达过的推测

## 输出要求

- 少而精：每天最多 2-3 条，没把握就输出空数组
- 每条 proposed_text **一句话概括，不要举例、不要展开**。≤60字。
- 格式："人类伙伴喜欢/不喜欢 AI……"
- 反面示例（太啰嗦）："人类伙伴不喜欢AI在她认真说正事时开玩笑，比如谈论工作或健康问题时应当收起调侃调侃，用沉稳语气认真回应，等她转换话题或主动撒娇时再恢复平时的亲密语气。" → 应改为："人类伙伴不喜欢 AI 在她认真说正事时开玩笑。"
- confidence ≥ 0.7 才输出

## expires_days 指南

- 与情绪/短期状态相关（如"最近比较累"）→ 3-5 天
- 与行为偏好/习惯相关 → 7-14 天
- 与核心关系模式相关 → 0（永不过期）

## 输出格式（纯 JSON）

{
  "suggestions": [
    {
      "type": "add",
      "target_section": "行为边界",
      "title": "短标题（10字内）",
      "evidence": "具体时间+对话证据",
      "proposed_text": "人类伙伴喜欢/不喜欢 AI……（一句话，≤60字，不举例）",
      "confidence": 0.85,
      "expires_days": 7
    }
  ]
}"""


# ═══════════════════════════════════════════════════════════════════════
# 核心检测逻辑
# ═══════════════════════════════════════════════════════════════════════

async def detect_persona_insights(
    flash_llm_func,
    current_persona: str = "",
    session_id: str = "",
    target_date: str = "",
    raise_on_error: bool = False,
) -> list:
    """
    从今日全量对话中检测人设优化建议（v2：日全文 + 内联反馈）。

    Args:
        flash_llm_func: Flash LLM 调用函数
        current_persona: 当前完整人设文本
        session_id: 活跃会话 ID

    Returns:
        新增的建议列表（已带 id 和 created_at，可直接保存）
    """
    import json as _json

    today = target_date or datetime.now().strftime("%Y-%m-%d")

    # ── 1. 构建日全文（含反馈内联标记） ──
    fulltext = _build_daily_fulltext_with_feedback(today)
    if not fulltext:
        logger.info("人格演化: 今日无对话数据，跳过")
        return []

    # ── 2. 过期检查（在检测前先清理） ──
    expire_outdated_evolutions()

    # ── 3. 构建活跃演化摘要 ──
    active_evos = get_active_suggestions()
    active_hint = ""
    if active_evos:
        active_hint = "\n当前活跃的演化条目（请勿生成重复建议，如需修改请用 modify 类型）：\n" + "\n".join(
            f"- [{s.get('target_section', '全局')}] {s.get('id', '')}: {s.get('proposed_text', s.get('suggested_change', ''))[:80]}"
            + (f" ({s.get('expires_at', '')[:10]}到期)" if s.get('expires_at') else " (永久)")
            for s in active_evos
        )

    # ── 4. 构建 prompt ──
    persona_section = f"当前人设卡全文：\n{current_persona}" if current_persona else "（无人设卡）"
    user_msg = f"{persona_section}\n{active_hint}\n\n今日对话（含人类伙伴反馈）：\n{fulltext[:15000]}"

    try:
        full_prompt = f"{DETECTION_PROMPT}\n\n{user_msg}"
        from neuron_registry import neuron_trace
        with neuron_trace("persona_evolution", model="Flash") as trace:
            trace.set_input(full_prompt[:500])
            raw = await flash_llm_func(full_prompt)
            trace.set_output(raw[:300] if raw else "")

        # 解析 JSON
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:]) if lines[0].startswith("```") else raw
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        data = _json.loads(raw)
        raw_suggestions = data.get("suggestions", [])

        # ── 5. 赋予 ID + 时间戳 ──
        # 必须整批预分配；逐条调用 _make_suggestion_id() 会在尚未落盘时重复。
        suggestion_ids = _make_suggestion_ids(len(raw_suggestions))
        new_suggestions = []
        for s, suggestion_id in zip(raw_suggestions, suggestion_ids):
            s["id"] = suggestion_id
            s["status"] = "pending"
            s["created_at"] = datetime.now().isoformat()
            # 兼容旧字段
            if "suggested_change" not in s and "proposed_text" in s:
                s["suggested_change"] = s["proposed_text"]
            new_suggestions.append(s)

        logger.info(f"人格演化: 检测到 {len(new_suggestions)} 条建议")
        return new_suggestions
    except Exception as e:
        logger.warning(f"人格演化检测失败: {e}")
        if raise_on_error:
            raise
        return []


# ═══════════════════════════════════════════════════════════════════════
# 过期机制（v2：只管理 active_evolutions 列表，不碰 systemPrompt）
# ═══════════════════════════════════════════════════════════════════════

def expire_outdated_evolutions() -> list:
    """
    检查并清理过期的演化条目（仅操作 active_evolutions + suggestions 状态）。
    不再修改 systemPrompt——演化已独立存储。
    返回已过期 ID 列表。
    """
    state = get_evolution_state()
    now = datetime.now()
    expired_ids = []

    for e in state.get("active_evolutions", []):
        if e.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(e["expires_at"])
                if now >= expires_at:
                    expired_ids.append(e["id"])
            except Exception:
                pass

    if not expired_ids:
        return []

    state["active_evolutions"] = [
        e for e in state.get("active_evolutions", [])
        if e["id"] not in expired_ids
    ]
    save_evolution_state(state)

    suggestions = get_suggestions()
    for s in suggestions:
        if s.get("id") in expired_ids:
            s["status"] = "expired"
    save_suggestions(suggestions)

    logger.info(f"人格演化: {len(expired_ids)} 条已过期: {expired_ids}")
    return expired_ids


# ═══════════════════════════════════════════════════════════════════════
# LLM 上下文注入（v2：纯内容列表，无元数据）
# ═══════════════════════════════════════════════════════════════════════

def get_active_evolution_context() -> str:
    """
    获取当前活跃演化条目的纯文本列表，用于注入 LLM 上下文。
    不包含 ID、到期时间、段落标签——只含行为准则内容。
    返回空字符串表示无活跃演化。
    """
    from cognition.retirement import completed
    if completed():
        return ""
    suggestions = get_active_suggestions()
    if not suggestions:
        return ""

    lines = ["## 镜影互动反馈\n以下是人类伙伴已确认的互动偏好记录，描述人类伙伴对 AI 的反馈："]
    for s in suggestions:
        proposed = s.get("proposed_text", s.get("suggested_change", ""))
        if proposed.strip():
            lines.append(f"- {proposed.strip()}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# 活跃演化管理（v2：只操作状态文件，不写 systemPrompt）
# ═══════════════════════════════════════════════════════════════════════

def _coerce_expires_days(value, default: int = 0) -> int:
    """把有效期统一为非负整数天数；0 表示永久。"""
    if value is None or value == "":
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _expires_at_for_days(expires_days: int, now: Optional[datetime] = None) -> Optional[str]:
    if expires_days <= 0:
        return None
    return ((now or datetime.now()) + timedelta(days=expires_days)).isoformat()

def register_active_evolution(suggestion: dict):
    """采纳后将演化注册到 active_evolutions"""
    state = get_evolution_state()
    expires_days = _coerce_expires_days(suggestion.get("expires_days", 0))
    expires_at = _expires_at_for_days(expires_days)

    # 如果是 modify，先移除旧 ID
    old_id = _extract_replaces_id(suggestion.get("evidence", ""))
    if old_id:
        state["active_evolutions"] = [
            e for e in state.get("active_evolutions", [])
            if e["id"] != old_id
        ]

    # 同一条目重复复启用/注册时只保留一份活跃状态。
    state["active_evolutions"] = [
        e for e in state.get("active_evolutions", [])
        if e.get("id") != suggestion["id"]
    ]
    state["active_evolutions"].append({
        "id": suggestion["id"],
        "section": suggestion.get("target_section", "全局"),
        "applied_at": datetime.now().isoformat(),
        "expires_days": expires_days,
        "expires_at": expires_at,
    })
    save_evolution_state(state)


def reactivate_evolution(evo_id: str, expires_days=7) -> Optional[dict]:
    """复启用已过期条目，并从本次复启用时刻重新计算有效期。"""
    suggestions = get_suggestions()
    target = next((s for s in suggestions if str(s.get("id", "")) == str(evo_id)), None)
    if not target or target.get("status") != "expired":
        return None

    days = _coerce_expires_days(expires_days, default=7)
    now = datetime.now()
    target["expires_days"] = days
    target["expires_at"] = _expires_at_for_days(days, now)
    target["status"] = "applied"
    target["applied_at"] = now.isoformat()
    target["reactivated_at"] = now.isoformat()
    register_active_evolution(target)
    save_suggestions(suggestions)
    return target


def update_evolution_validity(evo_id: str, expires_days=0) -> Optional[dict]:
    """调整活跃条目的有效期，计时从本次调整时刻重新开始。"""
    state = get_evolution_state()
    active = next(
        (e for e in state.get("active_evolutions", []) if str(e.get("id", "")) == str(evo_id)),
        None,
    )
    if active is None:
        return None

    days = _coerce_expires_days(expires_days, default=0)
    now = datetime.now()
    expires_at = _expires_at_for_days(days, now)
    active["expires_days"] = days
    active["expires_at"] = expires_at
    active["updated_at"] = now.isoformat()
    save_evolution_state(state)

    suggestions = get_suggestions()
    target = next((s for s in suggestions if str(s.get("id", "")) == str(evo_id)), None)
    if target is not None:
        target["expires_days"] = days
        target["expires_at"] = expires_at
        save_suggestions(suggestions)
    return target or dict(active)


def deactivate_evolution(evo_id: str) -> bool:
    """手动提前失效（仅操作状态 + suggestions，不碰 systemPrompt）。"""
    state = get_evolution_state()
    active = [e for e in state.get("active_evolutions", []) if e.get("id") == evo_id]
    if not active:
        return False
    state["active_evolutions"] = [
        e for e in state.get("active_evolutions", [])
        if e.get("id") != evo_id
    ]
    save_evolution_state(state)

    suggestions = get_suggestions()
    for s in suggestions:
        if s.get("id") == evo_id and s.get("status") == "applied":
            s["status"] = "expired"
            break
    save_suggestions(suggestions)
    return True


# ═══════════════════════════════════════════════════════════════════════
# 冲突检测
# ═══════════════════════════════════════════════════════════════════════

def check_conflict(suggestion: dict) -> dict:
    """
    检查新建议与活跃演化的冲突。
    返回 {"level": "ok|warn|info", "conflicts": [...]}
    """
    target = suggestion.get("target_section", "全局")
    evo_type = suggestion.get("type", "add")
    active = get_active_suggestions()
    same_section = [s for s in active if s.get("target_section", "全局") == target]

    if not same_section:
        return {"level": "ok", "conflicts": []}

    if evo_type == "modify":
        return {"level": "info", "conflicts": same_section}
    elif evo_type == "deprecate":
        old_id = _extract_replaces_id(suggestion.get("evidence", ""))
        matched = [s for s in same_section if s.get("id") == old_id]
        return {"level": "info" if matched else "warn", "conflicts": same_section}
    else:  # add
        return {"level": "warn" if len(same_section) >= 2 else "ok", "conflicts": same_section}


def _extract_replaces_id(evidence: str) -> Optional[str]:
    """从 evidence 文本中提取 replaces: pe_xxx"""
    m = re.search(r'replaces?:\s*(pe_\d{8}_\d{3})', evidence, re.IGNORECASE)
    return m.group(1) if m else None


# ═══════════════════════════════════════════════════════════════════════
# 旧方法兼容
# ═══════════════════════════════════════════════════════════════════════

def apply_suggestion(suggestion_id: int, current_persona: str) -> str:
    """
    [兼容] 旧采纳接口。v2 不再修改 systemPrompt，仅返回原值。
    """
    suggestions = get_suggestions()
    if suggestion_id < 0 or suggestion_id >= len(suggestions):
        return current_persona

    s = suggestions[suggestion_id]
    if s.get("status") not in (None, "", "pending"):
        return current_persona

    if "id" not in s:
        s["id"] = _make_suggestion_id()
    if "proposed_text" not in s:
        s["proposed_text"] = s.get("suggested_change", "")

    s["status"] = "applied"
    s["applied_at"] = datetime.now().isoformat()
    save_suggestions(suggestions)
    register_active_evolution(s)
    return current_persona  # v2: 不修改 persona 文本


def reject_suggestion(suggestion_id: int) -> bool:
    """驳回待处理建议；已采纳/已驳回/已过期条目不再回写。"""
    suggestions = get_suggestions()
    if suggestion_id < 0 or suggestion_id >= len(suggestions):
        return False
    suggestion = suggestions[suggestion_id]
    if suggestion.get("status") not in (None, "", "pending"):
        return False
    suggestion["status"] = "rejected"
    suggestion["rejected"] = True  # 向后兼容
    save_suggestions(suggestions)
    return True


# ═══════════════════════════════════════════════════════════════════════
# 迁移：旧 [人格演化 ...] 片段 → 新格式
# ═══════════════════════════════════════════════════════════════════════

async def migrate_preview_old_evolutions(flash_llm_func) -> list:
    """
    提取当前 persona 中的旧 [人格演化 ...] 片段，
    让 Flash 重写为新格式，返回新旧对比列表供前端预览。
    不修改任何数据。
    """
    try:
        from settings_manager import get_setting
        persona = get_setting("aiPersona") or {}
        if isinstance(persona, dict):
            sp = persona.get("systemPrompt", "")
        else:
            sp = str(persona) if persona else ""

        if not sp:
            return []

        # 正则提取旧片段
        old_pattern = re.compile(r'\[人格演化\s+(.+?)\]\s*(.+?)(?=\n\[人格演化|\n\n\[人格演化|\Z)', re.DOTALL)
        matches = old_pattern.findall(sp)
        if not matches:
            return []

        old_fragments = []
        for title, content in matches:
            content = content.strip()
            if content:
                old_fragments.append({"title": title.strip(), "content": content[:200]})

        if not old_fragments:
            return []

        # 构建 Flash prompt
        old_text = "\n".join(
            f"{i+1}. [{f['title']}] {f['content']}"
            for i, f in enumerate(old_fragments)
        )
        prompt = f"""你是人设卡编辑，需要将以下旧格式的人格演化片段改写为新格式。

旧格式（请改写）：
{old_text}

请为每条片段输出新格式的 JSON 数组。每条包含：
- title: 保留原标题或优化为更清晰的标题（10字内）
- target_section: 从 ["语气风格","行为边界","关系定位","性互动","日常互动","全局"] 中选择最合适的
- proposed_text: 重写为简洁的人设段落（50-150字），风格与人设卡一致
- expires_days: 0=永久, 7=行为偏好, 14=长期观察
- original_index: 对应旧片段的序号（1开始）

输出纯 JSON 数组，不要有其他文字。"""

        raw = await flash_llm_func(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        import json as _json
        rewritten = _json.loads(raw)

        # 组装对比结果
        preview = []
        for item in rewritten:
            idx = item.get("original_index", 1) - 1
            if 0 <= idx < len(old_fragments):
                preview.append({
                    "old_title": old_fragments[idx]["title"],
                    "old_content": old_fragments[idx]["content"],
                    "new_title": item.get("title", old_fragments[idx]["title"]),
                    "new_target_section": item.get("target_section", "全局"),
                    "new_proposed_text": item.get("proposed_text", old_fragments[idx]["content"]),
                    "new_expires_days": item.get("expires_days", 0),
                })

        logger.info(f"迁移预览: {len(preview)} 条旧片段待转换")
        return preview
    except Exception as e:
        logger.warning(f"迁移预览失败: {e}")
        return []


def apply_migration(approved_items: list):
    """
    应用迁移：移除旧 [人格演化 ...] 片段，注册新演化到 active_evolutions。
    v2：演化不再写入 systemPrompt——独立存储，通过 get_active_evolution_context() 注入。
    """
    try:
        from settings_manager import get_setting, set_setting
        persona = get_setting("aiPersona") or {}
        if isinstance(persona, dict):
            sp = persona.get("systemPrompt", "")
        else:
            sp = str(persona) if persona else ""

        # 清理旧 [人格演化 ...] 片段（从 systemPrompt 中移除）
        if sp:
            sp = re.sub(r'\n*\[人格演化\s+.+?\].*?(?=\n\[人格演化|\n\n\[人格演化|\Z)', '', sp, flags=re.DOTALL)
            sp = re.sub(r'\n{3,}', '\n\n', sp)
            # 也清理可能残留的 v2 分隔区
            sp = re.sub(r'\n*---\n# 人格演化.*?\n---\n*', '', sp, flags=re.DOTALL)
            sp = sp.rstrip()

        applied_count = 0
        for item in approved_items:
            new_id = _make_suggestion_id()
            section = item.get("new_target_section", "全局")
            expires_days = item.get("new_expires_days", 0)
            expires_at = None
            if expires_days and expires_days > 0:
                expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()

            proposed = item.get("new_proposed_text", "")
            if not proposed:
                continue

            # 注册到活跃列表（v2：不写入 systemPrompt）
            state = get_evolution_state()
            state["active_evolutions"].append({
                "id": new_id,
                "section": section,
                "applied_at": datetime.now().isoformat(),
                "expires_at": expires_at,
            })
            save_evolution_state(state)

            # 创建 suggestion 记录
            suggestions = get_suggestions()
            suggestions.append({
                "id": new_id,
                "type": "add",
                "target_section": section,
                "title": item.get("new_title", ""),
                "evidence": f"迁移自旧片段: [{item.get('old_title', '')}] {item.get('old_content', '')[:100]}",
                "proposed_text": proposed,
                "confidence": 1.0,
                "status": "applied",
                "expires_days": expires_days,
                "applied_at": datetime.now().isoformat(),
                "expires_at": expires_at,
                "created_at": datetime.now().isoformat(),
            })
            save_suggestions(suggestions)
            applied_count += 1

        # 写回清理后的 systemPrompt
        if sp and isinstance(persona, dict):
            persona["systemPrompt"] = sp
            set_setting("aiPersona", persona)
            from shared_state import set_latest_persona_prompt
            set_latest_persona_prompt(sp)

        logger.info(f"迁移完成: {applied_count} 条旧片段 → 新格式（仅状态，不写入人设）")
        return applied_count
    except Exception as e:
        logger.error(f"迁移应用失败: {e}")
        return 0
