"""
对话摘要模块
负责检测话题结束、生成摘要、存储对话记录
支持分段摘要：将长对话按语义相似度分割成多个片段，每个片段生成独立摘要
"""
import json
import os
import asyncio
import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union
import httpx
from pydantic import BaseModel, Field
import hashlib
logger = logging.getLogger(__name__)
from conversation_manager_v2 import ConversationManagerV2, conversation_manager_v2
from message_roles import is_ui_only_role

class SummaryRecord(BaseModel):
    id: str = ''
    session_id: str = ''
    topic: str = ''
    summary_text: str = ''
    bucket: str = 'short_term'
    timestamp: str = ''
    message_ids: List[str] = Field(default_factory=list)
    importance_score: float = 0.0
    segment_index: int = 0
    segment_start_time: Optional[str] = None
    segment_end_time: Optional[str] = None
try:
    from segmentation import MessageSegmenter, SegmentationConfig, MessageSegment
    SEGMENTATION_AVAILABLE = True
except ImportError:
    SEGMENTATION_AVAILABLE = False
    logger.warning('分段模块不可用，将使用单摘要模式')

class Message(BaseModel):
    """消息模型"""
    id: str
    role: str
    content: str
    timestamp: str
    summarized: bool = False
    reasoning_content: Optional[str] = None
    tool_calls: Optional[str] = None
    tool_summary: Optional[str] = None
    is_wander: bool = False
    is_sentinel: bool = False
    is_reminder: bool = False
    event_type: str = ''
    long_text_card: Optional[str] = None

class Summary(BaseModel):
    """摘要模型"""
    id: str
    time: str
    topic: str
    participants: List[str]
    event: str
    emotion: str
    user_intent: str
    AI_action: str
    importance_score: float
    tags: List[str]
    message_ids: List[str]

class TopicState(BaseModel):
    """话题状态"""
    current_topic: str = ''
    last_topic_mention: int = 0
    last_activity_time: str = ''
    topic_similarity_score: float = 1.0
    message_count_since_topic: int = 0
    topic_start_time: Optional[str] = None
    topic_message_count: int = 0
    topic_round_count: int = 0
    is_topic_active: bool = False
    last_message_role: Optional[str] = None
    drift_detection_start: int = 0
    manually_ended: bool = False
    ended_at: Optional[str] = None

class ConversationData(BaseModel):
    """会话数据"""
    session_id: str
    messages: List[Message] = []
    summaries: List[Summary] = []
    topic_state: Optional[TopicState] = None

class SummaryRequest(BaseModel):
    """摘要请求"""
    session_id: str
    trigger_type: str = 'manual'
    last_message_id: Optional[str] = None

class SummaryResponse(BaseModel):
    """摘要响应"""
    summary_id: str
    topic: str
    status: str
    message: str

class ConversationManager:
    """会话管理器：负责读写会话数据文件"""

    def __init__(self, data_dir: str='conversations'):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.v2_manager = ConversationManagerV2(data_dir)

    def _get_file_path(self, session_id: str) -> str:
        """获取会话文件路径"""
        filename = hashlib.md5(session_id.encode()).hexdigest() + '.json'
        return os.path.join(self.data_dir, filename)

    async def _load_messages_from_v2(self, session_id: str) -> List[Message]:
        """从 SQLite 加载消息（替代 V2 JSON），带墓碑安全网过滤。"""
        import asyncio
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        messages_data = await asyncio.to_thread(chronicle.get_messages_by_session_id, session_id, 10000, desc=True)
        messages = []
        for msg_data in messages_data:
            message = Message(id=msg_data.get('message_id') or f"msg_{msg_data.get('timestamp', '')}", role=msg_data.get('role', 'user'), content=msg_data.get('content', ''), timestamp=msg_data.get('timestamp', datetime.now().isoformat()), summarized=False, reasoning_content=msg_data.get('reasoning_content'), tool_calls=msg_data.get('tool_calls'), tool_summary=msg_data.get('tool_summary'), is_wander=bool(msg_data.get('is_wander')), is_sentinel=bool(msg_data.get('is_sentinel')), is_reminder=bool(msg_data.get('is_reminder')), event_type=str(msg_data.get('event_type') or ''), long_text_card=msg_data.get('long_text_card'))
            messages.append(message)
        return messages

    async def load_conversation(self, session_id: str) -> ConversationData:
        """加载会话数据（消息从 SQLite，话题状态从 SQLite topic_state 表，V1 JSON 作为回退）"""
        messages = await self._load_messages_from_v2(session_id)
        topic_state = None
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            ts_data = chronicle.get_topic_state(session_id)
            if ts_data:
                topic_state = TopicState(is_topic_active=bool(ts_data.get('is_active')), topic_start_time=ts_data.get('start_time') or None, topic_message_count=ts_data.get('message_count', 0), topic_round_count=ts_data.get('round_count', 0), last_activity_time=ts_data.get('last_activity_time') or datetime.now().isoformat(), last_message_role=ts_data.get('last_message_role') or None, drift_detection_start=ts_data.get('drift_detection_start', 0), manually_ended=bool(ts_data.get('manually_ended', 0)), ended_at=ts_data.get('ended_at') or None)
        except Exception as e:
            logger.warning(f'load_conversation: SQLite topic_state 读取失败 [{e}]')
        if not topic_state:
            filepath = self._get_file_path(session_id)
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if data.get('topic_state'):
                        topic_state = TopicState(**data['topic_state'])
                except Exception as e:
                    logger.warning(f'load_conversation: V1 JSON topic_state 读取失败 [{e}]')
        if not topic_state:
            topic_state = TopicState(last_activity_time=datetime.now().isoformat())
        summaries = []
        filepath = self._get_file_path(session_id)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                summaries = [Summary(**s) for s in data.get('summaries', [])]
            except Exception as e:
                logger.warning(f'load_conversation: summaries 读取失败 [{e}]')
        return ConversationData(session_id=session_id, messages=messages, summaries=summaries, topic_state=topic_state)

    def save_conversation(self, conversation: ConversationData) -> None:
        """保存会话数据（话题状态写 SQLite topic_state 表，V1 JSON 作为备份）"""
        if conversation.topic_state:
            try:
                from event_chronicle import get_global_chronicle
                chronicle = get_global_chronicle()
                ts = conversation.topic_state
                chronicle.save_topic_state(conversation.session_id, {'is_active': int(ts.is_topic_active), 'start_time': ts.topic_start_time or '', 'message_count': ts.topic_message_count, 'round_count': ts.topic_round_count, 'last_activity_time': ts.last_activity_time, 'last_message_role': ts.last_message_role or '', 'drift_detection_start': ts.drift_detection_start, 'manually_ended': int(ts.manually_ended), 'ended_at': ts.ended_at or ''})
            except Exception as e:
                logger.warning(f'save_topic_state SQLite 失败: {e}')
        filepath = self._get_file_path(conversation.session_id)
        try:
            data = {'session_id': conversation.session_id, 'summaries': [summary.dict() for summary in conversation.summaries], 'topic_state': conversation.topic_state.dict() if conversation.topic_state else None}
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'保存会话数据失败: {e}')

    async def add_message(self, session_id: str, role: str, content: str, message_id: Optional[str]=None, reasoning_content: Optional[str]=None, **kwargs) -> Message:
        """添加消息到会话（同时存储到新索引系统）

        Args:
            session_id: 会话ID
            role: 消息角色
            content: 消息内容
            message_id: 可选的消息ID，如果不提供则自动生成
            reasoning_content: 可选的思维链内容（DeepSeek V4 thinking模式）
            **kwargs: 透传给 v2_manager 的额外参数（is_wander, is_sentinel, tool_summary, active_date 等）
        """
        v2_message = await self.v2_manager.add_message(session_id, role, content, message_id, reasoning_content, **kwargs)
        message = Message(id=v2_message['id'], role=role, content=content, timestamp=v2_message['timestamp'], summarized=False, reasoning_content=reasoning_content, event_type=str(kwargs.get('event_type') or ''))
        conversation = await self.load_conversation(session_id)
        conversation.messages.append(message)
        if not conversation.topic_state:
            conversation.topic_state = TopicState(last_activity_time=datetime.now().isoformat())
        else:
            conversation.topic_state.last_activity_time = datetime.now().isoformat()
        self.save_conversation(conversation)
        return message

    async def add_messages_batch(self, session_id: str, messages_spec: List[Dict[str, Any]], **kwargs) -> List[Message]:
        """批量添加消息到会话（单次 read-modify-write，减少 I/O 次数）

        Args:
            session_id: 会话ID
            messages_spec: 消息规格列表，每项包含 role, content, 可选 message_id, reasoning_content
            **kwargs: 透传给 v2_manager 的额外参数（active_date 等）

        Returns:
            创建的 Message 对象列表
        """
        v2_results = await self.v2_manager.add_messages_batch(session_id, messages_spec, **kwargs)
        conversation = await self.load_conversation(session_id)
        results = []
        for i, v2_msg in enumerate(v2_results):
            spec = messages_spec[i]
            message = Message(id=v2_msg['id'], role=spec['role'], content=spec['content'], timestamp=v2_msg['timestamp'], summarized=False, reasoning_content=spec.get('reasoning_content'), event_type=str(spec.get('event_type') or kwargs.get('event_type') or ''))
            conversation.messages.append(message)
            results.append(message)
        if not conversation.topic_state:
            conversation.topic_state = TopicState(last_activity_time=datetime.now().isoformat())
        else:
            conversation.topic_state.last_activity_time = datetime.now().isoformat()
        self.save_conversation(conversation)
        return results

    async def get_unsummarized_messages(self, session_id: str, max_age_hours: int=24) -> List[Message]:
        """获取未总结的消息。

        max_age_hours: 超过此时间的消息直接排除，防止旧对话泄露到新上下文中。
                       设为 0 则不做时间过滤（向后兼容旧行为）。
        """
        from datetime import datetime, timedelta
        now = datetime.now()
        cutoff = now - timedelta(hours=max_age_hours) if max_age_hours > 0 else None
        all_messages = await self._load_messages_from_v2(session_id)
        unsummarized = []
        for msg in all_messages:
            if is_ui_only_role(msg.role):
                continue
            if cutoff is not None:
                try:
                    ts = msg.timestamp
                    if ts.endswith('Z'):
                        msg_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    else:
                        msg_dt = datetime.fromisoformat(ts)
                    if msg_dt.tzinfo:
                        msg_dt = msg_dt.astimezone().replace(tzinfo=None)
                    if msg_dt < cutoff:
                        continue
                except Exception:
                    pass
            unsummarized.append(msg)
        return unsummarized

    def mark_messages_summarized(self, session_id: str, message_ids: List[str]) -> None:
        """标记消息为已总结（在索引系统中，这个消息列表已包含在摘要中，无需额外操作）"""
        pass

    def add_summary(self, session_id: str, summary: Summary) -> None:
        """添加摘要到旧文件（新索引系统通过handle_summary_request_v2处理）"""
        filepath = self._get_file_path(session_id)
        summaries = []
        topic_state = None
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                summaries = [Summary(**item) for item in data.get('summaries', [])]
                if data.get('topic_state'):
                    topic_state = TopicState(**data['topic_state'])
            except Exception as e:
                print(f'加载旧摘要数据失败: {e}')
        summaries.append(summary)
        conversation = ConversationData(session_id=session_id, summaries=summaries, topic_state=topic_state or TopicState(last_activity_time=datetime.now().isoformat()))
        self.save_conversation(conversation)

    async def get_messages(self, session_id: str, date_range: Optional[Tuple[str, str]]=None) -> List[Dict[str, Any]]:
        """获取会话的原始消息数据（从 SQLite）。

        Args:
            session_id: 会话ID
            date_range: 可选的 (start_date, end_date) 日期范围，格式 YYYY-MM-DD

        Returns:
            消息字典列表
        """
        import asyncio
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        if date_range:
            start_date, end_date = date_range
            return await asyncio.to_thread(chronicle.get_messages_by_date_range, start_date, end_date)
        return await asyncio.to_thread(chronicle.get_messages_by_session_id, session_id, 10000, desc=True)

    async def delete_messages(self, session_id: str, message_ids: List[str]) -> Dict[str, Any]:
        """删除指定消息ID列表中的消息

        Args:
            session_id: 会话ID
            message_ids: 要删除的消息ID列表

        Returns:
            包含删除结果信息的字典
        """
        return await self.v2_manager.delete_messages(session_id, message_ids)

    async def edit_message(self, session_id: str, message_id: str, new_content: str, old_content: str='', old_role: str='') -> bool:
        """编辑 V2 JSON 日志中的一条消息。返回是否找到并更新。"""
        return await self.v2_manager.edit_message(session_id, message_id, new_content, old_content, old_role)

class TopicStateMachine:
    """话题状态机：管理话题开始/结束状态"""

    def __init__(self, conversation_manager: ConversationManager):
        self.conversation_manager = conversation_manager
        self.max_drift_rounds = 5
        self.timeout_minutes = 15

    def set_config(self, max_drift_rounds: int=None, timeout_minutes: int=None):
        """设置配置参数"""
        if max_drift_rounds is not None:
            self.max_drift_rounds = max_drift_rounds
        if timeout_minutes is not None:
            self.timeout_minutes = timeout_minutes

    def _count_messages_since(self, messages: List[Message], since_ts: Optional[str]) -> Tuple[int, int]:
        """计数 timestamp >= since_ts 的消息（统一处理时区剥离）。

        返回 (message_count, round_count)。
        计入：user / assistant / wander / sentinel / reminder。
        排除：工具调用中间消息（msg_ 前缀 ID）+ UI-only 消息。
        时间戳解析失败的消息静默跳过。
        """
        if not since_ts:
            return (0, 0)
        try:
            start = datetime.fromisoformat(since_ts)
            if start.tzinfo is not None:
                start = start.replace(tzinfo=None)
        except Exception:
            return (0, 0)
        msg_count, round_count = (0, 0)
        for msg in messages:
            if msg.id.startswith('msg_'):
                continue
            if is_ui_only_role(msg.role):
                continue
            try:
                t = datetime.fromisoformat(msg.timestamp) if isinstance(msg.timestamp, str) else msg.timestamp
                if t.tzinfo is not None:
                    t = t.replace(tzinfo=None)
                if t >= start:
                    msg_count += 1
                    if msg.role == 'user':
                        round_count += 1
            except Exception:
                pass
        return (msg_count, round_count)

    async def get_topic_status(self, session_id: str) -> Dict[str, Any]:
        """获取话题状态（供前端显示）。

        消息计数直接从 V2 数据计算，而非依赖增量计数器。
        自动覆盖所有消息类型：user/assistant/wander/sentinel/reminder/im_。
        """
        conversation = await self.conversation_manager.load_conversation(session_id)
        if not conversation.topic_state:
            return {'is_active': False, 'start_time': '', 'message_count': 0, 'round_count': 0, 'last_activity_time': ''}
        state = conversation.topic_state
        if state.is_topic_active and state.topic_start_time:
            try:
                msg_count, round_count = self._count_messages_since(conversation.messages, state.topic_start_time)
            except Exception:
                msg_count = state.topic_message_count
                round_count = state.topic_round_count
        else:
            msg_count = state.topic_message_count
            round_count = state.topic_round_count
        return {'is_active': state.is_topic_active, 'start_time': state.topic_start_time or '', 'message_count': msg_count, 'round_count': round_count, 'last_activity_time': state.last_activity_time}
    DAY_CHANGE_HOUR = 6

    async def maybe_end_for_day_change(self, session_id: str) -> bool:
        """跨天自动结束：话题跨过完整日历日且已过凌晨 6 点 → 结束话题。

        处理"完全忘记结束"场景（没点"聊到这里"、没说约定词）：
        话题跨天且当前时刻 >= DAY_CHANGE_HOUR → 自动 end_topic，
        触发完整总结链（记忆整合 + 日记补全 + 统一分析）。

        跨午夜保护：当前时刻 < DAY_CHANGE_HOUR 时不结束
        （如 23:50-00:10 连续对话，00:10 的消息仍归前一天）。

        返回是否执行了结束。幂等：话题已结束后再调返回 False。
        """
        try:
            status = await self.get_topic_status(session_id)
            if not status.get('is_active') or not status.get('start_time'):
                return False
            from time_utils import to_beijing_time
            start_date = to_beijing_time(status['start_time'])[:10]
            now = datetime.now()
            if now.strftime('%Y-%m-%d') > start_date and now.hour >= self.DAY_CHANGE_HOUR:
                from routers.topic_router import end_topic
                await end_topic(session_id, reason='day_change')
                return True
        except Exception as e:
            logger.warning(f'跨天自动结束检查失败: {e}')
        return False

    async def start_new_topic(self, session_id: str, first_message: Optional[Message]=None) -> TopicState:
        """开启新话题"""
        conversation = await self.conversation_manager.load_conversation(session_id)
        if first_message and first_message.timestamp:
            ts = first_message.timestamp
        else:
            from time_utils import now_iso
            ts = now_iso()
        state = conversation.topic_state or TopicState(last_activity_time=ts)
        state.is_topic_active = True
        state.topic_start_time = ts
        state.topic_message_count = 0
        state.topic_round_count = 0
        state.last_message_role = None
        state.drift_detection_start = 0
        state.message_count_since_topic = 0
        state.last_activity_time = ts
        state.manually_ended = False
        state.ended_at = None
        conversation.topic_state = state
        self.conversation_manager.save_conversation(conversation)
        return state

    async def recalculate_topic_status(self, session_id: str) -> Dict[str, Any]:
        """
        根据当前消息重新计算话题状态
        用于删除/撤回消息后更新话题计数

        注意：消息数量从 conversation_manager (V2日志) 获取，
        但需要确保与 session_manager 同步
        """
        conversation = await self.conversation_manager.load_conversation(session_id)
        messages = conversation.messages
        if not conversation.topic_state:
            conversation.topic_state = TopicState(last_activity_time=datetime.now().isoformat())
        state = conversation.topic_state
        if len(messages) == 0:
            state.is_topic_active = False
            state.topic_start_time = None
            state.topic_message_count = 0
            state.topic_round_count = 0
            state.last_message_role = None
            state.drift_detection_start = 0
            state.last_activity_time = datetime.now().isoformat()
            logger.info(f'消息已清空，重置话题状态: session_id={session_id}')
            self.conversation_manager.save_conversation(conversation)
            return await self.get_topic_status(session_id)
        if not state.is_topic_active:
            return await self.get_topic_status(session_id)
        if state.topic_start_time:
            try:
                message_count, round_count = self._count_messages_since(messages, state.topic_start_time)
                state.topic_message_count = message_count
                state.topic_round_count = round_count
                state.drift_detection_start = 0
                if message_count == 0:
                    state.is_topic_active = False
                    state.topic_start_time = None
                    state.topic_message_count = 0
                    state.topic_round_count = 0
                    state.last_message_role = None
                    state.drift_detection_start = 0
                    logger.info(f'话题内消息已清空，重置话题状态: session_id={session_id}')
                if messages:
                    last_msg = messages[-1]
                    state.last_activity_time = last_msg.timestamp if isinstance(last_msg.timestamp, str) else last_msg.timestamp.isoformat()
                self.conversation_manager.save_conversation(conversation)
            except Exception as e:
                print(f'重新计算话题状态失败: {e}')
        return await self.get_topic_status(session_id)

    async def end_topic(self, session_id: str, reason: str='manual') -> Tuple[bool, int]:
        """
        结束当前话题
        返回: (是否成功结束, 摘要分界线消息索引)
        """
        conversation = await self.conversation_manager.load_conversation(session_id)
        if not conversation.topic_state:
            conversation.topic_state = TopicState(last_activity_time=datetime.now().isoformat())
        state = conversation.topic_state
        if reason == 'topic_drift' and state.drift_detection_start > 0:
            divider_index = state.drift_detection_start
        else:
            divider_index = len(conversation.messages) - 1
        if divider_index < 0 and len(conversation.messages) == 0:
            return (False, -1)
        if divider_index < 0:
            divider_index = len(conversation.messages) - 1
        state.is_topic_active = False
        state.topic_start_time = None
        state.topic_message_count = 0
        state.topic_round_count = 0
        state.last_message_role = None
        state.drift_detection_start = 0
        state.manually_ended = True
        from time_utils import now_iso
        state.ended_at = now_iso()
        self.conversation_manager.save_conversation(conversation)
        return (True, divider_index)

    async def process_message(self, session_id: str, message: Message, is_after_divider: bool=False) -> Dict[str, Any]:
        """
        处理新消息，更新话题状态
        返回: {
            "topic_started": bool,  # 是否开启了新话题
            "topic_ended": bool,    # 是否结束了话题
            "end_reason": str,      # 结束原因
            "divider_index": int,   # 分界线位置（用于前端显示divider）
            "topic_status": dict    # 当前话题状态
        }
        """
        conversation = await self.conversation_manager.load_conversation(session_id)
        state = conversation.topic_state
        messages = conversation.messages
        result = {'topic_started': False, 'topic_ended': False, 'end_reason': '', 'divider_index': -1, 'topic_status': {}}
        should_start_new = False
        if is_after_divider:
            if state and state.is_topic_active:
                divider_index = len(messages) - 1
                if divider_index >= 0:
                    state.is_topic_active = False
                    state.topic_start_time = None
                    state.topic_message_count = 0
                    state.topic_round_count = 0
                    state.last_message_role = None
                    state.drift_detection_start = 0
                    state.manually_ended = True
                    state.ended_at = datetime.now().isoformat()
                    result['topic_ended'] = True
                    result['end_reason'] = 'manual'
                    result['divider_index'] = divider_index
            if message.role == 'user':
                should_start_new = True
        elif not state or not state.is_topic_active:
            should_start_new = True
        elif not any((getattr(item, 'id', '') != message.id for item in messages)) and getattr(state, 'is_topic_active', False):
            logger.info(f'[PROCESS_MSG] BRANCH: recovery | role={message.role} msgs=0 is_active=True → reset + start_new')
            state.is_topic_active = False
            state.topic_start_time = None
            state.topic_message_count = 0
            state.topic_round_count = 0
            should_start_new = True
        if should_start_new:
            state = await self.start_new_topic(session_id, message)
            conversation.topic_state = state
            result['topic_started'] = True
        if not should_start_new and state and state.is_topic_active and state.topic_start_time:
            try:
                from time_utils import parse_ts
                from zoneinfo import ZoneInfo
                start_dt = parse_ts(state.topic_start_time)
                start_beijing = start_dt.astimezone(ZoneInfo('Asia/Shanghai'))
                ts_date = start_beijing.strftime('%Y-%m-%d')
                today_str = datetime.now().strftime('%Y-%m-%d')
                yesterday_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                if ts_date not in (today_str, yesterday_str):
                    state = await self.start_new_topic(session_id, message)
                    conversation.topic_state = state
                    result['topic_started'] = True
                    logger.info(f'[PROCESS_MSG] BRANCH: stale_start | old={ts_date} → reset')
            except Exception:
                pass
        if is_after_divider:
            pass
        elif message.role == 'user':
            state.last_message_role = 'user'
        elif message.role == 'assistant':
            state.last_message_role = 'assistant'
        if not is_after_divider or state.is_topic_active:
            state.last_activity_time = datetime.now().isoformat()
        self.conversation_manager.save_conversation(conversation)
        result['topic_status'] = await self.get_topic_status(session_id)
        return result

    async def _check_topic_end_internal(self, session_id: str, new_message: Message, state: TopicState, messages: List[Message]) -> Tuple[bool, str, int]:
        """
        内部方法：检查话题是否结束
        返回: (是否结束, 结束原因, 漂移起始索引)
        """
        if not state.is_topic_active:
            return (False, '', -1)
        if state.last_activity_time:
            try:
                last_activity = datetime.fromisoformat(state.last_activity_time)
                if datetime.now() - last_activity > timedelta(minutes=self.timeout_minutes):
                    return (True, 'timeout', -1)
            except Exception:
                pass
        if state.topic_round_count >= self.max_drift_rounds and new_message.role == 'user':
            if state.current_topic:
                drift_start_index = max(0, len(messages) - self.max_drift_rounds * 2)
                topic_mentioned = False
                for i in range(drift_start_index, len(messages)):
                    if messages[i].role == 'user':
                        if state.current_topic.lower() in messages[i].content.lower():
                            topic_mentioned = True
                            break
                if not topic_mentioned:
                    return (True, 'topic_drift', drift_start_index)
        return (False, '', -1)

    async def check_topic_end(self, session_id: str, new_message: Optional[Message]=None) -> Tuple[bool, str]:
        """
        兼容旧接口：检查话题是否结束
        返回：(是否结束, 结束原因)
        """
        conversation = await self.conversation_manager.load_conversation(session_id)
        if not conversation.topic_state:
            return (False, '')
        state = conversation.topic_state
        messages = conversation.messages
        should_end, reason, _ = await self._check_topic_end_internal(session_id, new_message or Message(id='', role='user', content='', timestamp=''), state, messages)
        return (should_end, reason)

    async def update_topic_state(self, session_id: str, message: Message, detected_topic: Optional[str]=None):
        """更新话题状态（兼容旧接口）"""
        conversation = await self.conversation_manager.load_conversation(session_id)
        if not conversation.topic_state:
            conversation.topic_state = TopicState(last_activity_time=datetime.now().isoformat())
        state = conversation.topic_state
        if detected_topic:
            state.current_topic = detected_topic
            state.last_topic_mention = len(conversation.messages) - 1
            state.message_count_since_topic = 0
        else:
            state.message_count_since_topic += 1
        state.last_activity_time = datetime.now().isoformat()
        self.conversation_manager.save_conversation(conversation)

class SummaryGenerator:
    """摘要生成器：调用LLM生成摘要"""

    def __init__(self, llm_caller):
        self.llm_caller = llm_caller

    def _compute_importance_score(self, features: Dict[str, float]) -> float:
        """基于特征计算重要性评分

        使用非线性聚合公式：
        - base = 0.2
        - G = 1 - ∏(1-g_i)  # 非线性聚合
        - S = base + (1-base)*G - penalty
        - identity保底: max(score, 0.9) if personal >= 0.9
        """
        base = 0.2
        personal = features.get('personal', 0)
        emotion_intensity = features.get('emotion_intensity', 0)
        habit = features.get('habit', 0)
        utility = features.get('utility', 0)
        specificity = features.get('specificity', 0)
        novelty = features.get('novelty', 0)
        g_list = []
        g_list.append(personal * 0.5)
        g_list.append(emotion_intensity * 0.3)
        g_list.append(habit * 0.2)
        g_list.append(utility * 0.4)
        if specificity >= 0.7:
            g_list.append(0.3)
        elif specificity >= 0.3:
            g_list.append(0.15)
        else:
            g_list.append(0.05)
        g_list.append(novelty * 0.25)
        product = 1.0
        for g in g_list:
            product *= 1.0 - g
        G = 1.0 - product
        score = base + (1.0 - base) * G
        emotion = features.get('emotion', 'neutral')
        if emotion == 'negative' and utility < 0.3:
            score -= 0.05
        score = max(score, base)
        if personal >= 0.9:
            score = max(score, 0.9)
        return max(0.0, min(1.0, score))

    async def generate_summary(self, messages: List[Message], session_id: str) -> Summary:
        """生成摘要"""
        prompt = self._build_summary_prompt(messages)
        summary_text = await self._call_llm_for_summary(prompt)
        import uuid
        summary_id = f"summary_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
        message_ids = [msg.id for msg in messages]
        try:
            import re
            json_match = re.search('\\{.*\\}', summary_text, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
            else:
                data = json.loads(summary_text)
        except Exception as e:
            print(f'解析摘要JSON失败: {e}, 使用默认值')
            data = {}
        features = {'personal': float(data.get('personal', 0)), 'emotion_intensity': float(data.get('emotion_intensity', 0)), 'habit': float(data.get('habit', 0)), 'utility': float(data.get('utility', 0)), 'specificity': float(data.get('specificity', 0)), 'novelty': float(data.get('novelty', 0)), 'emotion': data.get('emotion', 'neutral')}
        importance_score = self._compute_importance_score(features)
        summary = Summary(id=summary_id, time=data.get('time', datetime.now().isoformat()), topic=data.get('topic', '对话主题'), participants=data.get('participants', ['user', 'AI']), event=data.get('event', '对话事件'), emotion=data.get('emotion', 'neutral'), user_intent=data.get('user_intent', '用户意图'), AI_action=data.get('AI_action', 'AI行动'), importance_score=importance_score, tags=data.get('tags', []), message_ids=message_ids)
        return summary

    def _build_summary_prompt(self, messages: List[Message]) -> str:
        """构建摘要提示词"""
        conversation_text = ''
        for msg in messages:
            if msg.role == 'user' and msg.event_type == 'ui_action:visit_workshop':
                from workshop.ui_action import invitation_fact as workshop_invitation_fact
                conversation_text += f'系统事实: {workshop_invitation_fact(msg.content)}\n'
                continue
            if msg.role == 'user' and msg.event_type == 'ui_action:play_game':
                from wander_manager.game_invitation import invitation_fact as game_invitation_fact
                conversation_text += f'系统事实: {game_invitation_fact(msg.content)}\n'
                continue
            role = '用户' if msg.role == 'user' else '助手'
            if msg.event_type == 'rift_game':
                role += '（裂隙档案虚构共同游玩）'
            conversation_text += f'{role}: {msg.content}\n'
        prompt = f'请对以下对话生成摘要，要求必须严格按照以下格式输出json文件：\n1. time: 时间（当前时间）\n2. topic: 对话主题（简短概括）\n3. participants: 参与者（列表）\n4. event: 主要事件或讨论内容\n5. emotion: 整体情感倾向（positive/neutral/negative）\n6. user_intent: 用户的主要意图或目标\n7. AI_action: AI的主要行动或回应\n8. tags: 相关标签（列表，如["work", "programming"]）\n\n【事实域】标有“裂隙档案虚构共同游玩”的人物、地点、遭遇和行动属于游戏剧情，不能概括成现实经历、现实身份或用户画像；可以概括为用户与助手共同游玩的经历和游戏进度。\n\n【重要性特征评估】请评估以下特征（每项0-1分）：\n- personal: 是否涉及用户个人信息、偏好、身份（0=纯客观话题，1=深度个人内容）\n- emotion_intensity: 情感强度（0=平淡，1=强烈情绪波动）\n- habit: 是否涉及用户习惯、例行事务（0=一次性事件，1=重复性习惯）\n- utility: 实用价值（0=闲聊，1=重要决策/解决方案）\n- specificity: 信息具体程度（0=模糊，0.5=中等，1=非常具体如人名/日期/地点）\n- novelty: 新信息量（0=重复已知，1=全新重要信息）\n\n【对话内容】\n{conversation_text}\n\n请以JSON格式返回，确保字段名与上述一致。'
        return prompt

    async def _call_llm_for_summary(self, prompt: str) -> str:
        """调用LLM生成摘要（需要集成现有LLM调用）"""
        try:
            response = await self.llm_caller(prompt)
            return response
        except Exception as e:
            print(f'调用LLM生成摘要失败: {e}')
            return '{"topic": "对话主题", "participants": ["user", "AI"], "event": "对话", "emotion": "neutral", "user_intent": "未识别", "AI_action": "回应", "importance_score": 0.5, "tags": []}'
conversation_manager = ConversationManager()
topic_state_machine = TopicStateMachine(conversation_manager)
summary_generator = None

async def ensure_topic_exists(session_id: str) -> None:
    """安全网：确保 session 有活跃话题存在。

    DB 恢复/迁移后 conversations/<md5>.json 可能缺失，
    但 event_chronicle.db (SQLite) 中有消息。此时 load_conversation()
    创建 TopicState(is_topic_active=False)，导致上下文调度器返回降级上下文。

    修复 is_topic_active + topic_start_time：
    - topic_start_time 取今天第一条消息的时间戳（优先）或最近一条消息的时间戳（回退）
    - 之后由 process_message() 在消息落盘后接管计数
    """
    conversation = await conversation_manager.load_conversation(session_id)
    if not conversation.messages:
        return
    if conversation.topic_state and conversation.topic_state.is_topic_active:
        return
    if conversation.topic_state and getattr(conversation.topic_state, 'manually_ended', False):
        return
    state = conversation.topic_state or TopicState()
    state.is_topic_active = True
    try:
        import sqlite3 as _sqlite3
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        conn = _sqlite3.connect(chronicle.db_path)
        row = conn.execute("SELECT timestamp FROM conversation_messages WHERE role='divider' AND session_id=? ORDER BY timestamp DESC LIMIT 1", (session_id,)).fetchone()
        row2 = None
        if row:
            row2 = conn.execute("SELECT timestamp FROM conversation_messages WHERE session_id=? AND timestamp > ? AND role IN ('user','assistant') ORDER BY timestamp ASC LIMIT 1", (session_id, row[0])).fetchone()
        conn.close()
        if row:
            if row2:
                state.topic_start_time = row2[0]
            else:
                state.topic_start_time = datetime.now().isoformat()
        elif conversation.messages:
            state.topic_start_time = conversation.messages[0].timestamp
        else:
            state.topic_start_time = datetime.now().isoformat()
    except Exception:
        if conversation.messages:
            state.topic_start_time = conversation.messages[0].timestamp
        else:
            state.topic_start_time = datetime.now().isoformat()
    state.last_activity_time = conversation.messages[-1].timestamp if conversation.messages else datetime.now().isoformat()
    conversation.topic_state = state
    conversation_manager.save_conversation(conversation)

async def initialize_summary_generator(llm_caller):
    """初始化摘要生成器"""
    global summary_generator
    summary_generator = SummaryGenerator(llm_caller)

async def handle_summary_request(request: SummaryRequest) -> SummaryResponse:
    """处理摘要请求"""
    global summary_generator
    unsummarized = await conversation_manager.get_unsummarized_messages(request.session_id)
    if not unsummarized:
        return SummaryResponse(summary_id='', topic='', status='no_messages', message='没有未总结的消息')
    summary = await summary_generator.generate_summary(unsummarized, request.session_id)
    conversation_manager.add_summary(request.session_id, summary)
    conversation_manager.mark_messages_summarized(request.session_id, [msg.id for msg in unsummarized])
    return SummaryResponse(summary_id=summary.id, topic=summary.topic, status='success', message='摘要生成成功')

async def check_and_trigger_summary(session_id: str, new_message: Optional[Message]=None) -> Optional[SummaryResponse]:
    """检查并自动触发摘要（使用新索引系统）"""
    should_end, reason = await topic_state_machine.check_topic_end(session_id, new_message)
    if should_end:
        print(f'话题结束检测: {reason}')
        request = SummaryRequest(session_id=session_id, trigger_type='auto', last_message_id=new_message.id if new_message else None)
        return await handle_summary_request_v2(request)
    return None

async def create_summary_record(session_id: str, messages: List[Message], llm_summary: Summary) -> SummaryRecord:
    """创建摘要记录并添加到索引系统

    Args:
        session_id: 会话ID
        messages: 原始消息列表
        llm_summary: LLM生成的摘要

    Returns:
        SummaryRecord: 创建的摘要记录
    """
    from datetime import datetime
    v2_manager = conversation_manager.v2_manager
    if messages:
        first_msg_time = datetime.fromisoformat(messages[0].timestamp.replace('Z', '+00:00'))
        date_str = first_msg_time.strftime('%Y-%m-%d')
    else:
        date_str = datetime.now().strftime('%Y-%m-%d')
    log_file_path, _, bucket = v2_manager.get_log_file_for_summary(session_id, date_str)
    import uuid
    summary_id = f"summary_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
    tags = llm_summary.tags
    if isinstance(tags, str):
        tags = [tag.strip() for tag in tags.split(',') if tag.strip()]
    keywords = None
    summary_record = SummaryRecord(id=summary_id, session_id=session_id, topic=llm_summary.topic[:100], timestamp=datetime.now().isoformat(), importance_score=llm_summary.importance_score, tags=tags, bucket=bucket, log_file_path=log_file_path, message_ids=llm_summary.message_ids, embedding=None, keywords=keywords, ombre_brain_id=None, created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat())
    return summary_record

async def handle_summary_request_v2(request: SummaryRequest, frontend_message_ids: List[str]=None) -> Union[SummaryResponse, List[SummaryResponse]]:
    """处理摘要请求（新索引系统版本，支持分段摘要）

    Args:
        request: 摘要请求
        frontend_message_ids: 前端会话中的消息ID列表（用于摘要存储，解决ID格式不匹配问题）

    Returns:
        单个SummaryResponse或列表（分段摘要时返回列表）
    """
    global summary_generator
    if summary_generator is None:
        return SummaryResponse(summary_id='disabled', topic='', status='disabled', message='摘要功能已禁用')
    unsummarized_frontend_ids = []
    if frontend_message_ids:
        unsummarized_frontend_ids = list(frontend_message_ids)
    print(f'[摘要] 前端消息总数: {(len(frontend_message_ids) if frontend_message_ids else 0)}, 未总结: {len(unsummarized_frontend_ids)}')
    if not unsummarized_frontend_ids:
        return SummaryResponse(summary_id='', topic='', status='no_messages', message='没有未总结的消息')
    unsummarized = await conversation_manager.get_unsummarized_messages(request.session_id)
    if not unsummarized and unsummarized_frontend_ids:
        print(f'[摘要] conversation_manager为空，但前端有{len(unsummarized_frontend_ids)}条未总结消息')
        from datetime import datetime
        now = datetime.now().isoformat()
        unsummarized = [Message(id=mid, role='user', content='[消息内容已清理]', timestamp=now) for mid in unsummarized_frontend_ids]
    if SEGMENTATION_AVAILABLE and len(unsummarized) > 50:
        print(f'[摘要] 消息数{len(unsummarized)}超过阈值，启用分段摘要')
        return await generate_segmented_summaries(request.session_id, unsummarized, unsummarized_frontend_ids)
    llm_summary = await summary_generator.generate_summary(unsummarized, request.session_id)
    summary_record = await create_summary_record(request.session_id, unsummarized, llm_summary)
    summary_record.message_ids = unsummarized_frontend_ids
    print(f'[摘要] 使用前端消息ID: {unsummarized_frontend_ids[:3]}... (共{len(unsummarized_frontend_ids)}个)')
    return await handle_summary_request(request)

async def generate_segmented_summaries(session_id: str, messages: List[Message], frontend_message_ids: List[str]) -> List[SummaryResponse]:
    """生成分段摘要

    Args:
        session_id: 会话ID
        messages: 未总结的消息列表
        frontend_message_ids: 前端消息ID列表

    Returns:
        摘要响应列表
    """
    global summary_generator
    config = SegmentationConfig(similarity_threshold=0.5, min_segment_size=3, max_segment_size=50, timeout_minutes=30.0, enable_time_factor=True, window_size=3)
    segmenter = MessageSegmenter(config)
    try:
        segments = await segmenter.segment(messages)
    except Exception as e:
        print(f'[分段摘要] 分段失败: {e}，回退到单摘要模式')
        llm_summary = await summary_generator.generate_summary(messages, session_id)
        summary_record = await create_summary_record(session_id, messages, llm_summary)
        summary_record.message_ids = frontend_message_ids
        summary_id = ''
        return [SummaryResponse(summary_id=summary_id, topic=summary_record.topic, status='success', message=f'摘要生成成功（回退单摘要模式）')]
    print(f'[分段摘要] 将 {len(messages)} 条消息分为 {len(segments)} 个片段')
    responses = []
    all_summary_ids = []
    all_topics = []
    for idx, segment in enumerate(segments):
        try:
            llm_summary = await summary_generator.generate_summary(segment.messages, session_id)
            summary_record = await create_summary_record(session_id, segment.messages, llm_summary)
            summary_record.segment_index = idx
            summary_record.segment_start_time = segment.messages[0].timestamp if segment.messages else None
            summary_record.segment_end_time = segment.messages[-1].timestamp if segment.messages else None
            if frontend_message_ids and len(frontend_message_ids) == len(messages):
                segment_msg_ids = frontend_message_ids[segment.start_index:segment.end_index + 1]
                summary_record.message_ids = segment_msg_ids
            else:
                summary_record.message_ids = [msg.id for msg in segment.messages]
            summary_id = ''
            all_summary_ids.append(summary_id)
            all_topics.append(summary_record.topic)
            responses.append(SummaryResponse(summary_id=summary_id, topic=summary_record.topic, status='success', message=f'片段{idx + 1}摘要生成成功 ({len(segment.messages)}条消息, 原因: {segment.break_reason})'))
            print(f'[分段摘要] 片段{idx + 1}: {summary_record.topic[:30]}... ({len(segment.messages)}条消息)')
        except Exception as e:
            print(f'[分段摘要] 片段{idx + 1}生成失败: {e}')
            responses.append(SummaryResponse(summary_id='', topic='', status='error', message=f'片段{idx + 1}生成失败: {e}'))
    conversation_manager.mark_messages_summarized(session_id, [msg.id for msg in messages])
    all_success = all((r.status == 'success' for r in responses))
    if all_success and frontend_message_ids:
        try:
            await conversation_manager.v2_manager.delete_messages(session_id, frontend_message_ids)
        except Exception as e:
            print(f'从日志删除已总结消息失败（非关键错误）: {e}')
    return responses
