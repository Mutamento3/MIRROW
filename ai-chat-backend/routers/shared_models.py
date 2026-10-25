"""共享 Pydantic Models — 供 main.py 和 routers/* 共同使用，避免循环导入"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from session_manager import ThinkingStep


# ═══════════════════════════════════════════════════════════
# 工具调用记录（被 chat / session / message 使用）
# ═══════════════════════════════════════════════════════════

class ToolCallRecord(BaseModel):
    tool: str
    description: str = ""
    parameters: Dict[str, Any] = {}
    result: Optional[Any] = None           # 实际数据是 str（工具输出文本），也可能是 dict
    extra_data: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None
    delivery: str = ""
    timestamp: str = ""
    status: str = ""
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# 话题状态（被 topic / chat / session 使用）
# ═══════════════════════════════════════════════════════════

class TopicStatusResponse(BaseModel):
    is_active: bool = False
    start_time: str = ""
    message_count: int = 0
    round_count: int = 0
    last_activity_time: str = ""
    current_date: str = ""
    remaining_rounds: int = 0
    display_text: str = ""


# ═══════════════════════════════════════════════════════════
# 前端消息 / 会话（被 session / chat 使用）
# ═══════════════════════════════════════════════════════════

class FrontendMessage(BaseModel):
    id: str
    role: str
    content: str
    timestamp: str
    rowOrder: Optional[int] = None
    modelSource: Optional[Dict[str, Any]] = None
    thinkingChain: Optional[List[ThinkingStep]] = None
    toolCalls: Optional[List[ToolCallRecord]] = None
    isWander: Optional[bool] = None
    isSentinel: Optional[bool] = None
    isReminder: Optional[bool] = None
    isMusicCochlea: Optional[bool] = None
    eventType: Optional[str] = None
    eventParentId: Optional[str] = None
    isMusic: Optional[bool] = None
    images: Optional[List[Dict[str, Any]]] = None
    voiceAttachment: Optional[Dict[str, Any]] = None
    reasoning_content: Optional[str] = None
    longTextCard: Optional[Dict[str, str]] = None


class FrontendSession(BaseModel):
    id: str
    title: str
    messages: List[FrontendMessage]
    createdAt: str
    updatedAt: str
    locked: bool = False
    isPrimary: bool = False
    # 消息窗口元数据：messages 可能只是「最近 N 条」的窗口而非全量。
    # 全部带默认值，保证不带这些字段的旧响应/旧客户端仍可解析。
    hasMore: bool = False          # True = 服务端还有更早的消息未返回
    oldestId: str = ""             # 翻页游标：窗口内最早一条的 message_id
    oldestTs: str = ""             # 游标 message_id 失效时的兜底比较值
    oldestRowId: int = 0           # 同 timestamp 下稳定翻页的 SQLite 次序
    total: Optional[int] = None    # 仅单会话端点返回；列表端点不返回（避免 N 次 COUNT）
    cursorStale: bool = False      # 请求的游标已失效，前端应重置游标并重拉尾窗


class SessionListResponse(BaseModel):
    sessions: List[FrontendSession]
    current_session_id: str = ""


class SessionCreateRequest(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None
    messages: Optional[List[FrontendMessage]] = None


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    messages: Optional[List[FrontendMessage]] = None


class ClearSessionsRequest(BaseModel):
    confirm: bool = False


# ═══════════════════════════════════════════════════════════
# 消息操作请求（被 message 路由使用）
# ═══════════════════════════════════════════════════════════

class RevokeMessageRequest(BaseModel):
    session_id: str
    user_message_id: str
    delete_assistant_reply: bool = True


class DeleteMessageRequest(BaseModel):
    session_id: str
    message_id: str


class ClearSessionMessagesRequest(BaseModel):
    session_id: str


class EditMessageRequest(BaseModel):
    session_id: str
    message_id: str
    new_content: str

class EditLongTextCardRequest(BaseModel):
    session_id: str
    message_id: str
    long_text_card: Dict[str, str]


# ═══════════════════════════════════════════════════════════
# 系统监控模型（被 monitor / system 路由使用）
# ═══════════════════════════════════════════════════════════

class ModelApiStatus(BaseModel):
    deepseek: bool
    glm: bool
    summary_model: str


class NeuronTraceRecord(BaseModel):
    """神经元执行记录（API 返回用）"""
    trace_id: str = ""
    neuron_id: str = ""
    timestamp: str = ""
    status: str = "ok"
    duration_ms: float = 0.0
    input_preview: str = ""
    output_preview: str = ""
    thinking_preview: str = ""
    input_full: str = ""
    output_full: str = ""
    thinking_full: str = ""
    error_message: str = ""
    recipe_used: str = ""
    model_used: str = ""


class NeuronStatusItem(BaseModel):
    """单个神经元状态（API 返回用）"""
    neuron_id: str
    name: str
    category: str
    subsystem: str
    consumer: str = ""
    trigger_desc: str = ""
    recipe: str = ""
    is_llm: bool = False
    llm_model: str = ""
    description: str = ""
    emoji: str = "🧩"
    last_trace: Optional[Dict[str, Any]] = None
    execution_count: int = 0
    success_count: int = 0
    last_ok_at: Optional[str] = None


class SystemStatus(BaseModel):
    """系统监控状态"""
    timestamp: str
    backend_status: str
    ombre_brain_status: str
    sentence_transformer_status: str
    vector_index_stats: Dict[str, Any] = {}
    bucket_stats: Dict[str, Any] = {}
    migration_status: Dict[str, Any] = {}
    model_api_status: ModelApiStatus
    scheduler_status: Dict[str, Any] = {}
    external_services: Dict[str, Any] = {}
    context_pipeline: Dict[str, Any] = {}
    post_topic_health: Dict[str, Any] = {}
    diary_health: Dict[str, Any] = {}
    cron_health: Dict[str, Any] = {}
    memory_health: Dict[str, Any] = {}
    reminders_health: Dict[str, Any] = {}
    components_health: Dict[str, Any] = {}
    sessions_health: Dict[str, Any] = {}
    keyword_pool_stats: Dict[str, Any] = {}
    token_stats: Dict[str, Any] = {}
    context_snapshot: Dict[str, Any] = {}
    group_chat_contexts: Dict[str, Any] = {}
    recipe_call_log: Dict[str, Any] = {}
    recipe_call_log_observation: Dict[str, Any] = {}
    keyword_pools: Dict[str, Any] = {}
    neuron_summary: Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def convert_msg_to_frontend(msg_dict: dict) -> FrontendMessage:
    """将消息 dict 转换为 FrontendMessage（供 session 路由使用）。
    同时规范化 SQLite snake_case 字段名为前端 camelCase，确保双数据源兼容。
    """
    # ── 字段规范化：SQLite snake_case / JSON 字符串 → 前端 camelCase / 原生类型 ──
    # 仅在目标键缺失时补位，不覆盖 sessions JSON 已有值。

    # id: SQLite 用 message_id
    if not msg_dict.get("id") and msg_dict.get("message_id"):
        msg_dict["id"] = msg_dict["message_id"]

    # snake_case boolean → camelCase
    for sql_key, js_key in [("is_wander", "isWander"),
                             ("is_sentinel", "isSentinel"),
                             ("is_reminder", "isReminder"),
                             ("is_music_cochlea", "isMusicCochlea")]:
        if sql_key in msg_dict and js_key not in msg_dict:
            msg_dict[js_key] = bool(msg_dict[sql_key])

    # event_type → eventType
    if "event_type" in msg_dict and "eventType" not in msg_dict:
        msg_dict["eventType"] = msg_dict["event_type"]
    if "event_parent_id" in msg_dict and "eventParentId" not in msg_dict:
        msg_dict["eventParentId"] = msg_dict["event_parent_id"]
    raw_card = msg_dict.get("longTextCard") or msg_dict.get("long_text_card")
    if isinstance(raw_card, str) and raw_card.strip():
        try:
            import json
            raw_card = json.loads(raw_card)
        except Exception:
            raw_card = None
    if isinstance(raw_card, dict):
        msg_dict["longTextCard"] = raw_card

    # tool_calls: SQLite JSON 字符串 → 原生数组
    raw_tc = msg_dict.get("toolCalls") or msg_dict.get("tool_calls")
    if isinstance(raw_tc, str) and raw_tc.strip():
        try:
            import json
            parsed = json.loads(raw_tc)
            if isinstance(parsed, list):
                msg_dict["toolCalls"] = parsed
        except Exception:
            pass

    # voiceAttachment: 优先从 voice_attachment JSON 列恢复，其次从旧分离列组装
    if not msg_dict.get("voiceAttachment"):
        va = msg_dict.get("voice_attachment")
        if isinstance(va, str) and va.strip():
            try:
                import json as _json
                msg_dict["voiceAttachment"] = _json.loads(va)
            except Exception:
                pass
    if not msg_dict.get("voiceAttachment"):
        vp = msg_dict.get("voice_path")
        vt = msg_dict.get("voice_text")
        vd = msg_dict.get("voice_duration")
        if vp or vt or vd is not None:
            msg_dict["voiceAttachment"] = {
                "path": vp or "",
                "text": vt or "",
                "duration": vd or 0,
            }

    # images: SQLite JSON 字符串 → 原生数组
    raw_img = msg_dict.get("images")
    if isinstance(raw_img, str) and raw_img.strip():
        try:
            import json as _json2
            msg_dict["images"] = _json2.loads(raw_img)
        except Exception:
            pass

    # thinkingChain: 优先从 thinking_chain_json 恢复（结构化数组），其次从 reasoning_content 构建
    if not msg_dict.get("thinkingChain"):
        tcj = msg_dict.get("thinking_chain_json")
        if isinstance(tcj, str) and tcj.strip():
            try:
                import json as _json3
                msg_dict["thinkingChain"] = _json3.loads(tcj)
            except Exception:
                pass
    if not msg_dict.get("thinkingChain") and msg_dict.get("reasoning_content"):
        msg_dict["thinkingChain"] = [{
            "content": msg_dict["reasoning_content"],
            "timestamp": msg_dict.get("timestamp", "")
        }]

    # ── 原有逻辑 ──
    tc_list = None
    raw_tc = msg_dict.get("toolCalls") or msg_dict.get("tool_calls")
    if raw_tc:
        try:
            if isinstance(raw_tc, list):
                tc_list = [ToolCallRecord(**t) if isinstance(t, dict) else t for t in raw_tc]
        except Exception:
            pass

    tc_raw = msg_dict.get("thinkingChain") or msg_dict.get("thinking_chain")
    if tc_raw and isinstance(tc_raw, list):
        thinking_chain = [ThinkingStep(**t) if isinstance(t, dict) else t for t in tc_raw]
    else:
        thinking_chain = None

    # isMusic: 音乐消息 id 前缀 music_（纯 UI 样式，无独立列）。
    _mid = str(msg_dict.get("id") or msg_dict.get("message_id") or "")
    is_music = msg_dict.get("isMusic")
    if is_music is None:
        is_music = _mid.startswith("music_")
    is_music_cochlea = _mid.startswith("mco_")

    content = msg_dict.get("content", "")
    if msg_dict.get("role") == "assistant" and isinstance(content, str):
        # Reload uses the same body boundary as live delivery; SQL stays intact.
        from visible_reply import clean_visible_reply
        content = clean_visible_reply(content)

    return FrontendMessage(
        id=msg_dict.get("id", ""),
        role=msg_dict.get("role", ""),
        content=content,
        timestamp=msg_dict.get("timestamp", ""),
        rowOrder=msg_dict.get("row_id"),
        modelSource=msg_dict.get("modelSource"),
        thinkingChain=thinking_chain,
        toolCalls=tc_list,
        isWander=msg_dict.get("isWander"),
        isSentinel=msg_dict.get("isSentinel"),
        isReminder=msg_dict.get("isReminder"),
        eventType=msg_dict.get("eventType"),
        eventParentId=msg_dict.get("eventParentId"),
        isMusic=is_music,
        isMusicCochlea=is_music_cochlea,  # 派生自 ID 前缀 mco_（音乐耳蜗）
        images=msg_dict.get("images"),
        voiceAttachment=msg_dict.get("voiceAttachment"),
        reasoning_content=msg_dict.get("reasoning_content"),
        longTextCard=raw_card if isinstance(raw_card, dict) else None,
    )
