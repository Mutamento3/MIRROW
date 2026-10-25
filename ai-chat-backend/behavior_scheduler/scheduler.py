from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import json
import logging
import os
import re
import uuid as _uuid
from token_tracker import log_token_usage
from .affect_capture import _AFFECT_SIDECAR_CONTEXT, _AFFECT_PROVIDER_ATTEMPT, _AFFECT_PROVIDER_OK, _capture_affect_sidecar, _sanitize_provider_result, _strip_affect_sidecar_text, _mark_latest_affect_visible_text, begin_provider_attempt, finalize_affect_sidecars
from affect.self_contract import SELF_SIDECAR_INSTRUCTION as _AFFECT_SELF_SIDECAR_INSTRUCTION
from api_sources.main_route import main_route_scope, execute_main, current_source, message_source_snapshot, bind_message_source, manual_selection_active
from datetime import datetime, timedelta
from .base_tool import BaseTool, ToolResult, ToolStatus
from .tools import register_all_tools, get_tools_description, get_ledger_type_options
from .command_layer import CommandParser, CommandExecutor, CommandStatus, Command
from .agent_tools_adapter import AgentToolsAdapter
from .llm_wrapper import build_request_user_content
from .vision_envelope import VISION_RESPONSE_FORMAT, build_vision_contract, parse_vision_envelope, storage_description, vision_image_parts as _vision_parts, reserve_vision_source_key
from .tool_standardizer import ToolCallStandardizer
from .intent_parser import TOOL_KEYWORD_MAP, _REMOTE_FALLBACK_KEYWORDS, _NIGHT_GATED_KEYWORDS
from .action_candidate_gate import detect_action_candidate
from context_scheduler_log import write_log as _log_context_scheduler
from time_utils import now_iso
from truncation_config import get_truncation_limit

def _cap(key: str, fallback: int) -> int:
    """取截断上限，0=无限制 → 返回极大哨兵值使切片不截断"""
    v = get_truncation_limit(key)
    if v == 0:
        return 10 ** 9
    return v if v > 0 else fallback

def _append_tool_receipt(tool_calls: List[Dict[str, Any]], record: Dict[str, Any]) -> Dict[str, Any]:
    """Give each real execution/failed attempt one stable event identity at occurrence."""
    record.setdefault('event_id', f'tool_event:{_uuid.uuid4().hex}')
    record.setdefault('completed_at', now_iso())
    tool_calls.append(record)
    return record

def _xhs_failure_fallback(tool_calls: Any) -> str:
    """Produce an honest visible fallback when a failed XHS round is empty."""
    if not isinstance(tool_calls, list):
        return ''
    for record in reversed(tool_calls):
        if not isinstance(record, dict) or record.get('tool') != 'browse_xiaohongshu':
            continue
        if record.get('success'):
            continue
        raw = record.get('result')
        data: dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                data = {}
        barrier = str(data.get('barrier') or data.get('error') or record.get('error') or '')
        if barrier == 'no_feed_cards':
            return '页面没有识别到可读帖子，暂时没有完成本轮浏览。'
        if barrier == 'no_new_feed_cards':
            return '页面上的可读帖子都已经读过，本轮没有取得新的帖子。'
        try:
            target = int(data.get('target_count') or 0)
            success = int(data.get('success_count') or 0)
        except (TypeError, ValueError):
            target = success = 0
        if data.get('status') == 'partial_success' or (target and success < target):
            return f'本轮取得{success}/{target}篇，未取得的部分我不作推断。'
        return '本轮只读浏览没有取得可核实的帖子材料。'
    return ''

def _xhs_visible_fallback(current: Any, tool_calls: Any) -> str:
    """Avoid reducing a concrete XHS failure to the generic ``嗯。`` reply."""
    text = str(current or '').strip()
    fallback = _xhs_failure_fallback(tool_calls)
    if fallback and text in {'', '嗯', '嗯。', '嗯！', '嗯~', '嗯～'}:
        return fallback
    return text or fallback
logger = logging.getLogger(__name__)

def build_tool_execution_record(tool_name: str, exec_result: dict) -> str:
    """Render one truthful, source-labelled pre-tool execution fact."""
    raw_status = str(exec_result.get('status') or '').strip().lower()
    if raw_status in {'partial', 'partial_success'}:
        status = 'partial'
    elif exec_result.get('success') is True:
        status = 'success'
    else:
        status = 'failure'
    material = exec_result.get('content')
    if not material:
        material = exec_result.get('error') or '未返回材料'
    return f'【工具执行记录】\n工具：{tool_name}\n状态：{status}\n执行时间：{now_iso()}\n返回材料或错误：{material}\n本记录只证明上述执行状态；返回内容可能不完整或已经不足以支持当前结论。'

def build_visual_fallback_messages(system_prompt: str, llm_history: List[Dict[str, Any]], user_input: Any) -> List[Dict[str, Any]]:
    """Give the visual fallback the same normalized task/history envelope."""
    from context_builder.assembly import assemble_provider_messages
    return assemble_provider_messages(system_content=system_prompt, history=llm_history, turn=[{'role': 'user', 'content': user_input}])
_DURATION_CN_NUM = {'半': 0.5, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

class SchedulerState(Enum):
    IDLE = 'idle'
    THINKING = 'thinking'
    TOOL_EXECUTING = 'tool_executing'
    WAITING_USER = 'waiting_user'
    COMPLETE = 'complete'

@dataclass
class SchedulerResult:
    """调度器返回结果"""
    final_response: str
    tool_calls: List[Dict[str, Any]]
    state: SchedulerState
    need_user_input: bool
    thinking_chain: List[Dict[str, str]]
    pending_intent_check: bool = False
    failure_reason: str = ''
    intermediate_messages: List[Dict[str, str]] = field(default_factory=list)
    image_description: Optional[str] = None
    affect_sidecars: List[Dict[str, Any]] = field(default_factory=list)
    model_source: Optional[Dict[str, Any]] = field(default_factory=current_source)
    message_sources: Dict[str, Any] = field(default_factory=message_source_snapshot)

    def __post_init__(self):
        from reply_metadata_filter import strip_generated_model_label
        self.final_response = strip_generated_model_label(self.final_response)
        for item in self.intermediate_messages or []:
            if isinstance(item, dict) and isinstance(item.get('content'), str):
                item['content'] = strip_generated_model_label(item['content'])
        self.final_response, self.affect_sidecars = finalize_affect_sidecars(self.final_response, self.intermediate_messages, self.affect_sidecars, failed=bool(getattr(self, 'failure_reason', '')))
        for item in self.thinking_chain or []:
            if isinstance(item, dict) and item.get('content'):
                item['content'] = _strip_affect_sidecar_text(item['content'])

def _extract_search_query(msg: str) -> str:
    """从用户消息中提取搜索关键词。"""
    for prefix in ['帮我搜一下', '帮我搜', '搜一下', '搜索一下', '帮我查一下', '上网查一下', '帮我查查', '帮我搜搜', '搜一搜', '查一查', '搜搜看', '查查看']:
        if prefix in msg:
            rest = msg.split(prefix, 1)[1].strip()
            return re.sub('[？?！!。，,、\\s]+$', '', rest)[:50] if rest else ''
    cleaned = re.sub('[？?！!。，,、\\s]+$', '', msg)
    cleaned = re.sub('^(那个|帮我|我想|给我|你帮我|那你帮我|那帮我)\\s*', '', cleaned)
    return cleaned[:50] if cleaned else msg[:50]

def _extract_ledger_keyword(msg: str) -> str:
    """从删除请求中提取关键词。'划掉零点前睡觉那一笔'→'零点前睡觉'"""
    for prefix in ['划掉', '划账', '删掉', '删除', '销账', '删账', '划', '消账', '清账', '消了', '销了']:
        if prefix in msg:
            rest = msg.split(prefix, 1)[1].strip()
            rest = re.sub('(那.?笔|那.?条|这.?笔|这.?条|那个|这个).*$', '', rest)
            rest = rest.strip('，。；！？、\n\r ')
            return rest[:15] if rest else ''
    return ''

def _extract_voice_text(msg: str) -> str:
    """从用户消息中提取要用语音说的内容。"""
    for prefix in ['我想用语音说', '让我用语音说', '说句话', '念出来', '念给我听', '说给你听', '听我说']:
        if prefix in msg:
            rest = msg.split(prefix, 1)[1].strip()
            if rest:
                rest = re.sub('^[：:，。；！？、\\s]+', '', rest)
                return rest[:60] if rest else ''
    return ''

class BehaviorScheduler:
    """
    行为调度器 - 协调决策层、命令层和执行层

    架构说明：
    1. 决策层：Pro LLM 生成自然语言回复（可附闭合自然语言动作行）
    2. 参数/意图层：编译显式动作；未输出动作时 Flash 可从 NL 补漏
    3. 标准化层：规范化 JSON → 填充默认值 → 校验 schema
    4. 执行层：具体工具的实现

    双路径设计：
    - 快速路径：Pro 输出闭合动作行 → 转为内部合同后执行
    - Flash 路径：Pro 未输出 → 关键词预筛 → Flash 提取参数 → 执行
    """
    MAX_ROUNDS = 5
    NIGHT_FALLBACK_THRESHOLD = 3

    def __init__(self, call_llm_func: Callable, glm_api_key: str='', glm_api_url: str='', glm_model: str='glm-4v-flash', chrome_user_data: str=None, target_qq: str=None, use_agent_tools: bool=True, llm_provider: str='deepseek', llm_api_key: str=None, llm_base_url: str=None, llm_model: str=None, camera_device_name: str='', final_reply_caller: Callable=None, pro_api_key: str=None, pro_model: str=None, pro_base_url: str=None, pro_provider: str=None, pro_profile_id: str='deepseek-pro', pro_profile_label: str='DS Pro', pro_supports_vision: bool=False):
        self.call_llm = call_llm_func
        self.tools = register_all_tools(glm_api_key=glm_api_key, glm_api_url=glm_api_url, glm_model=glm_model, chrome_user_data=chrome_user_data, target_qq=target_qq, camera_device_name=camera_device_name)
        self.tools_description = get_tools_description(self.tools)
        self._pending_mobile_requests: Dict[str, asyncio.Future] = {}
        self._mobile_sender: Optional[Callable] = None
        from .tools import set_mobile_relay_callback as _set_relay
        from shared_state import dispatch_mobile_tool, get_mobile_relay_callback

        async def _relay_to_phone(request_id: str, tool_name: str, params: dict) -> dict:
            """向手机发送工具执行请求，等待结果返回"""
            sender = self._mobile_sender
            if sender is None:
                _log_context_scheduler(f'MOBILE_RELAY ❌ _mobile_sender is None')
                raise RuntimeError('手机 WebSocket 未连接')
            _log_context_scheduler(f'MOBILE_RELAY → 发送: tool={tool_name} request_id={request_id[:8]}... pending={len(self._pending_mobile_requests)}')
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            self._pending_mobile_requests[request_id] = future
            try:
                dispatched = await sender({'type': 'mobile_tool_request', 'request_id': request_id, 'tool': tool_name, 'params': params})
                if isinstance(dispatched, dict) and ('success' in dispatched or 'error' in dispatched):
                    return dispatched
                _log_context_scheduler(f'MOBILE_RELAY → 已发送到前端，等待回传...')
                result = await asyncio.wait_for(future, timeout=30.0)
                _log_context_scheduler(f"MOBILE_RELAY ✅ 收到回传: tool={tool_name} content={str(result.get('content', ''))[:80]}")
                return result
            except asyncio.TimeoutError:
                _log_context_scheduler(f'MOBILE_RELAY ⏰ 超时(30s): tool={tool_name}')
                raise
            except Exception as e:
                _log_context_scheduler(f'MOBILE_RELAY ❌ 异常: tool={tool_name} error={e}')
                if self._mobile_sender is sender and ('close message' in str(e).lower() or 'closed' in str(e).lower()):
                    self._mobile_sender = None
                    _log_context_scheduler(f'MOBILE_RELAY ⚠️ 已清除失效的 _mobile_sender（WebSocket已关闭）')
                raise
            finally:
                self._pending_mobile_requests.pop(request_id, None)
        if get_mobile_relay_callback() is None:
            _set_relay(_relay_to_phone)
        self._relay_to_phone = _relay_to_phone
        from .tools import inject_eyes_dependencies as _inject_eyes
        _inject_eyes(self.tools, flash_llm_func=self._call_flash_for_fusion, mobile_relay=dispatch_mobile_tool)
        self.command_parser = CommandParser(self.tools, call_llm_func=call_llm_func)
        self.command_executor = CommandExecutor(self.tools)
        self.tool_standardizer = ToolCallStandardizer(self.tools)
        self.use_agent_tools = use_agent_tools
        self.agent_tools_adapter = None
        self.pro_adapter = None
        self.main_model_profile_id = pro_profile_id
        self.main_model_label = pro_profile_label
        self.main_model_supports_vision = pro_supports_vision
        if use_agent_tools:
            self._init_agent_tools_adapter(provider=llm_provider, api_key=llm_api_key, base_url=llm_base_url, model=llm_model)
            if pro_api_key and pro_base_url and pro_model:
                self._init_pro_adapter(provider=pro_provider or llm_provider, api_key=pro_api_key, base_url=pro_base_url, model=pro_model)
        self._state = SchedulerState.IDLE
        self._status_callback: Optional[Callable] = None
        self._night_rounds_without_toy_mention = 0
        self._pushed_preview_nl = ''
        self._pushed_preview_msg_id = ''
        self.final_reply_caller = final_reply_caller

    def _init_agent_tools_adapter(self, provider: str='deepseek', api_key: str=None, base_url: str=None, model: str=None):
        """初始化 agent_tools 适配器"""
        try:
            logger.info(f'Initializing agent_tools adapter: provider={provider}, model={model}')
            if provider == 'deepseek':
                actual_model = model or 'deepseek-v4-pro'
                logger.info(f'Creating DeepSeek wrapper with model: {actual_model}')
                self.agent_tools_adapter = AgentToolsAdapter.for_deepseek(api_key=api_key, model=actual_model, base_url=base_url)
            elif provider == 'glm':
                self.agent_tools_adapter = AgentToolsAdapter.for_glm(api_key=api_key, model=model or 'glm-4')
            elif provider == 'ollama':
                self.agent_tools_adapter = AgentToolsAdapter.for_ollama(model=model or 'llama3.1', base_url=base_url or 'http://localhost:11434/v1')
            else:
                self.agent_tools_adapter = AgentToolsAdapter.for_openai_compatible(api_key=api_key, base_url=base_url, model=model)
            self.agent_tools_adapter.register_tools(list(self.tools.values()))
            logger.info(f'agent_tools adapter initialized with provider: {provider}')
        except Exception as e:
            logger.warning(f'Failed to init agent_tools adapter: {e}, falling back to legacy mode')
            self.use_agent_tools = False
            self.agent_tools_adapter = None

    def _init_pro_adapter(self, provider: str='deepseek', api_key: str=None, base_url: str=None, model: str=None):
        """初始化 Pro adapter（首轮聊天+工具决策，支持 thinking）"""
        try:
            logger.info(f'Initializing Pro adapter: provider={provider}, model={model}')
            actual_model = model or 'deepseek-v4-pro'
            if provider == 'deepseek':
                print(f"[SCHEDULER] 创建 Pro adapter | model={actual_model} | api_key={('已设置' if api_key else '未设置')}")
                self.pro_adapter = AgentToolsAdapter.for_deepseek(api_key=api_key, model=actual_model, base_url=base_url)
            else:
                self.pro_adapter = AgentToolsAdapter.for_openai_compatible(api_key=api_key, base_url=base_url, model=actual_model, compact_flash_responses=False)
            self.pro_adapter.register_tools(list(self.tools.values()))
            print(f'[SCHEDULER] Pro adapter 初始化成功 | model={actual_model}')
            logger.info(f'Pro adapter initialized with model: {actual_model}')
        except Exception as e:
            print(f'[SCHEDULER] Pro adapter 初始化失败: {e}')
            logger.warning(f'Failed to init Pro adapter: {e}, will fall back to Flash for first round')
            self.pro_adapter = None

    def build_main_model_adapter(self, provider: str, api_key: str, base_url: str, model: str) -> AgentToolsAdapter:
        """Build and validate a replacement main adapter without changing live routing."""
        if not api_key or not base_url or (not model):
            raise ValueError('主聊天模型的 key、base_url 或模型名未配置完整')
        if provider == 'deepseek':
            adapter = AgentToolsAdapter.for_deepseek(api_key=api_key, model=model, base_url=base_url)
        else:
            adapter = AgentToolsAdapter.for_openai_compatible(api_key=api_key, base_url=base_url, model=model, compact_flash_responses=False)
        adapter.register_tools(list(self.tools.values()))
        return adapter

    def activate_main_model_adapter(self, adapter: AgentToolsAdapter, *, profile_id: str, label: str, supports_vision: bool) -> Optional[AgentToolsAdapter]:
        """Atomically swap the adapter used by subsequent provider calls."""
        previous = self.pro_adapter
        self.pro_adapter = adapter
        self.main_model_profile_id = profile_id
        self.main_model_label = label
        self.main_model_supports_vision = supports_vision
        logger.info('Main chat profile activated: id=%s model=%s vision=%s', profile_id, getattr(getattr(adapter, '_wrapper', None), 'model', ''), supports_vision)
        return previous

    def _select_chat_adapter(self, vision_required: bool):
        """Select an endpoint-owning adapter; never mix a key/model with another base URL."""
        if vision_required and (not self.main_model_supports_vision):
            return (self.agent_tools_adapter, 'FlashVision')
        if self.pro_adapter:
            return (self.pro_adapter, self.main_model_label or 'Main')
        return (self.agent_tools_adapter, 'Flash')

    async def _call_flash_for_fusion(self, prompt: str) -> str:
        """用 Flash 模型（温度=0）处理单条文本融合任务，返回纯文本。供 Eyes 等感官工具调用。"""
        adapter = self.agent_tools_adapter
        if not adapter:
            raise RuntimeError('Flash adapter 未初始化')
        result = await adapter.execute(user_input=prompt, conversation_history=[], system_prompt='你是数据分析模块，请直接输出分析结果，不要使用工具调用格式。')
        return result.get('natural_language') or result.get('content', '')

    def set_status_callback(self, callback: Callable):
        self._status_callback = callback

    def clear_status_callback_if(self, expected: Callable):
        """A closing connection must not clear a newer turn's status sink."""
        if self._status_callback is expected:
            self._status_callback = None

    def set_image_model(self, model: str):
        """切换所有视觉工具的图像分析模型"""
        for tool in self.tools.values():
            if hasattr(tool, 'set_image_model'):
                tool.set_image_model(model)

    async def _notify_status(self, status: str, data: Dict=None):
        if status == 'llm_reply' and data and data.get('content'):
            data = dict(data)
            bind_message_source(data)
            data['content'] = _capture_affect_sidecar(data.get('content'), origin='stream', message_id=str(data.get('message_id') or ''))
            from visible_reply import clean_visible_reply
            data['content'] = clean_visible_reply(data['content'])
            if data.get('reasoning'):
                data['reasoning'] = _strip_affect_sidecar_text(data['reasoning'])
            data['affect_sidecar_metadata'] = [{'valid': bool(item.get('valid')), 'diagnostics': list(item.get('diagnostics') or []), 'origin': item.get('origin', 'stream'), 'message_id': item.get('message_id', '')} for item in _AFFECT_SIDECAR_CONTEXT.get() or [] if item.get('message_id') == str(data.get('message_id') or '')]
        if self._status_callback:
            try:
                await self._status_callback(status, data or {})
            except Exception:
                pass
        if status == 'llm_reply' and data and data.get('content'):
            content = data['content'].strip()
            if content and len(content) > 2:
                try:
                    from .tools import push_mobile_notification as _pmn
                    import asyncio as _asyncio_pmn
                    _asyncio_pmn.ensure_future(_pmn('AI', content[:120]))
                except Exception:
                    pass

    async def _push_llm_reply(self, content: str, reasoning: str=None):
        """推送 llm_reply 到前端（内容推送的唯一通道）。"""
        if not content or not content.strip():
            return
        sid = getattr(self, '_current_session_id', '')
        msg_id = f'msg_{sid}_{now_iso()}' if sid else f'msg_{now_iso()}'
        self._last_push_message_id = msg_id
        data = {'content': content.strip(), 'message_id': msg_id}
        if reasoning:
            data['reasoning'] = reasoning
        await self._notify_status('llm_reply', data)

    @staticmethod
    def _extract_reasoning(content: str) -> str:
        """提取 TOOL_CALL: 之前的自然语言部分（用于清除格式标记）"""
        nl_pattern = 'NATURAL[_\\s]LANGUAGE\\s*:\\s*\\n?'
        nl_match = re.search(nl_pattern, content)
        if nl_match:
            content = content[nl_match.end():].strip()
        marker = 'TOOL_CALL:'
        if marker in content:
            return content[:content.find(marker)].strip()
        pattern = '\\[TOOL_CALL\\]'
        match = re.search(pattern, content)
        if match:
            return content[:match.start()].strip()
        from tool_action_protocol import strip_tool_protocol_text
        return strip_tool_protocol_text(content)

    @classmethod
    def _safe_nl(cls, natural_language: str, assistant_content: str) -> str:
        """获取清洗后的自然语言：优先用 NL，为空时从 raw content 提取并清洗语音标记"""
        nl = cls._clean_voice_markers(natural_language)
        if nl:
            return cls._clean_frontend_content(nl)
        extracted = cls._extract_reasoning(assistant_content)
        return cls._clean_frontend_content(cls._clean_voice_markers(extracted))

    @staticmethod
    def _build_tool_narrative(tools_label: str, multi: bool=False) -> str:
        """工具轮情境锚定（单/多工具路径共用，trap 47 同步保障）

        续写模式：Pro 最擅长续写，引导"接着往下说"比"收尾/补充"更自然。
        配合 COMPLETE 路径段落级 fuzzy 去重砍掉重叠段落。
        """
        _tools = tools_label
        _results_loc = '各工具结果在下一条工具执行回执里' if multi else '结果在下一条工具执行回执里'
        return f'你刚刚已经回复了人类伙伴——她都看到了。{_tools} 刚执行完，{_results_loc}。\n接着你刚才的话往下说；如果这是感知或查询工具，回复必须包含至少一个由最新结果支持的具体判断，不要逐字段播报或照抄原始结果，证据不足或失败时明确说不确定。如果还需要其他工具，在末尾输出闭合的 @工具名 自然语言动作@。别输出「我看看」「我查查」这类没带工具动作行的预告。'

    @staticmethod
    def _clean_rejected_tool_nl(content: str) -> str:
        """Drop protocol-loop placeholders that must never become chat history."""
        cleaned = str(content or '').strip()
        return '' if cleaned in {'嗯', '嗯。'} else cleaned

    @staticmethod
    def _is_hanging_tool_preview(content: str) -> bool:
        """Whether a would-be final reply still promises an already-finished action."""
        text = str(content or '').strip()
        return any((marker in text for marker in ('我先查', '我再查', '我查下', '我查一下', '我先看看', '我再看看', '让我再看看', '我去查')))

    @staticmethod
    def _grounded_result_fallback(tool_calls: List[Dict[str, Any]]) -> str:
        """Do not turn raw tool payloads into AI's dialogue on finalization failure."""
        from .result_notice import result_notice
        return result_notice(tool_calls)

    @classmethod
    def _recover_unverified_result_reply(cls, content: str, *, has_prior_reply: bool) -> str:
        """Keep AI's natural reply when only the hidden result footer is missing.

        The result-consumption footer is a diagnostic contract, not user-facing
        dialogue.  A missing footer must not replace an otherwise natural model
        response with an internal receipt.  If the response is only a protocol
        placeholder or a stale pre-tool promise, suppress it when an earlier
        visible reply can carry the tool attachment.
        """
        text = str(content or '').strip()
        if has_prior_reply and (text in {'嗯', '嗯。'} or cls._is_hanging_tool_preview(text)):
            return ''
        return text

    @staticmethod
    def _split_result_refs(content: str) -> tuple[str, set[str]]:
        """Remove the hidden result-consumption footer and return declared tool names."""
        text = str(content or '')
        pattern = re.compile('(?:^|\\n)RESULTS_USED\\s*[:：]\\s*(\\[[^\\n]*\\])\\s*$', re.IGNORECASE)
        match = pattern.search(text.strip())
        if not match:
            return (text.strip(), set())
        refs: set[str] = set()
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                refs = {str(item).strip() for item in parsed if str(item).strip()}
        except (TypeError, ValueError, json.JSONDecodeError):
            refs = set()
        cleaned = pattern.sub('', text.strip()).strip()
        return (cleaned, refs)

    @staticmethod
    def _required_model_result_tools(tool_calls: List[Dict[str, Any]]) -> set[str]:
        return {str(record.get('tool') or '').strip() for record in tool_calls or [] if isinstance(record, dict) and record.get('delivery', 'model') != 'ui_only' and str(record.get('tool') or '').strip()}

    @staticmethod
    def _tool_limit_count(tool_calls: List[Dict[str, Any]], tool_name: str, tool_instance: Any) -> int:
        success_only = bool(getattr(tool_instance, 'single_use_success_only', False))
        return sum((1 for record in tool_calls if isinstance(record, dict) and record.get('tool') == tool_name and (not success_only or record.get('success') is True)))

    @staticmethod
    def _dedup_paragraphs(text: str, messages: list, threshold: float=0.92) -> str:
        """Only trim leading near-identical paragraphs already said in this user turn.

        Older assistant turns are not a deletion pool: a story may deliberately
        revisit a motif.  Never keep a duplicate last paragraph as a fallback.
        """
        if not text or not messages:
            return text
        import difflib as _difflib
        latest_user = max((index for index, item in enumerate(messages) if item.get('role') == 'user'), default=-1)
        _pool = []
        for _m in messages[latest_user + 1:]:
            if _m.get('role') == 'assistant':
                _mc = (_m.get('content') or '').strip()
                if _mc and len(_mc) >= 20:
                    _pool.extend((p.strip() for p in _mc.split('\n\n') if p.strip()))
        if not _pool:
            return text
        _paras = [p.strip() for p in text.split('\n\n') if p.strip()]
        if not _paras:
            return text
        first_new = 0
        for _fp in _paras:
            _best = max((_difflib.SequenceMatcher(None, _fp, _ip).ratio() for _ip in _pool), default=0)
            if _best < threshold:
                break
            first_new += 1
        return '\n\n'.join(_paras[first_new:])

    def _build_system_prompt(self, night_mode_enabled: bool=False) -> str:
        """构建决策层系统提示词"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M')
        return f"""你是一个智能助手，可以使用以下工具：\n\n当前时间: {current_time}\n\n{self.tools_description}\n\n## 工具调用规则\n\n当需要使用工具时，先告诉用户你要做什么，然后输出工具调用：\n[TOOL_CALL]\n{{"tool": "工具名", "parameters": {{"参数名": "参数值"}}}}\n[/TOOL_CALL]\n\n### 重要规则：\n1. 先说话，再调用工具\n2. 每次只能调用一个工具\n3. 工具名必须是上述列表中存在的工具\n4. 不需要工具时直接回复用户\n5. 工具返回结果后，根据结果继续调用工具或直接回复用户\n6. 明确指定摄像头、拍照或自拍时使用 eyes；泛问人类伙伴在做什么时结合新鲜事实和工具能力选择感官\n7. {('需要控制甜焰时，调用 toy2 并只选命名模式' if night_mode_enabled else '需要控制甜焰时，提醒用户开启深夜模式（点击页面爱心图标）')}\n\n### 示例：\n用户："帮我搜一下Python教程"\n助手：让我搜一下~\n[TOOL_CALL]\n{{"tool": "web_search", "parameters": {{"query": "Python教程"}}}}\n[/TOOL_CALL]"""

    def _extract_camera_prompt(self, user_message: str) -> Optional[str]:
        """从用户消息中识别明确的摄像头查看意图。"""
        text = (user_message or '').strip()
        lowered = text.lower()
        has_camera_word = any((word in lowered for word in ['摄像头', 'camera', 'webcam', '镜头']))
        has_view_intent = any((word in lowered for word in ['看', '看看', '打开', '开一下', '试试', '试试看', '拍', '捕获', 'scan', 'capture', 'open']))
        implicit_view = any((phrase in lowered for phrase in ['看看我', '看一下我', '看看桌面', '看看房间', '看看环境', '看到空的桌面', '能看到']))
        if not (has_camera_word and has_view_intent or implicit_view):
            return None
        if any((word in lowered for word in ['桌面', 'desk', 'table'])):
            return '请通过摄像头观察画面，重点描述桌面上有什么、是否有人在画面中。'
        if any((word in lowered for word in ['我', '脸', '表情', '本人'])):
            return '请通过摄像头观察用户是否在画面中，并描述可见的人物、表情和环境。'
        return '请描述摄像头画面中的内容，包括人物、环境、物品等。'

    def set_mobile_sender(self, sender: Optional[Callable]):
        """Refresh the legacy sender; sensory tools read the current shared relay."""
        self._mobile_sender = sender
        from shared_state import dispatch_mobile_tool
        from .tools import inject_eyes_dependencies
        inject_eyes_dependencies(self.tools, flash_llm_func=self._call_flash_for_fusion, mobile_relay=dispatch_mobile_tool)

    def handle_mobile_tool_result(self, request_id: str, success: bool, result: dict, error: str):
        """处理手机回传的工具执行结果"""
        rid = request_id[:8] if request_id else '?'
        _log_context_scheduler(f"MOBILE_RELAY ← 收到回传: request_id={rid} success={success} content={(str(result.get('content', ''))[:80] if result else 'N/A')} pending_count={len(self._pending_mobile_requests)}")
        future = self._pending_mobile_requests.get(request_id)
        if future and (not future.done()):
            if success:
                _log_context_scheduler(f'MOBILE_RELAY ← future.set_result (success)')
                future.set_result(result)
            else:
                extra = result.get('content', '') if result else ''
                detail = error or extra or '未知错误'
                _log_context_scheduler(f'MOBILE_RELAY ← future.set_exception: {detail[:200]}')
                future.set_exception(RuntimeError(detail))
        elif future:
            _log_context_scheduler(f'MOBILE_RELAY ← future already done, ignoring')
        else:
            _log_context_scheduler(f'MOBILE_RELAY ← no matching future for {rid} (可能已超时清理)')

    @main_route_scope
    async def process_message(self, user_message: str, conversation_history: List[Dict]=None, system_prompt: str=None, night_mode_enabled: bool=False, platform: str='pc', session_id: str='', images: Optional[List[str]]=None, message_id: str='', turn_id: str='', raw_user_message: Optional[str]=None, inspection_call_id: str='') -> SchedulerResult:
        """
        处理用户消息，Pro 生成 NL → 关键词预筛 → Flash 提取意图+参数 → 执行。

        Args:
            user_message: 用户消息
            conversation_history: 对话历史
            system_prompt: 自定义系统提示词
            night_mode_enabled: 是否启用深夜模式
            platform: "pc" | "mobile" — 手机端排除 PC 专属工具
            images: 用户直接发图的 base64/dataURL 列表。非空时本轮切 flash-vision 看图（可调工具）。
            message_id: 当前用户消息的稳定身份，用于请求与回执定位
            turn_id: 当前轮次身份；为空时复用 message_id
            raw_user_message: 未附加视觉/语音系统说明的原始人类消息，供工具执行边界读取

        Returns:
            SchedulerResult: 调度结果
        """
        from .execution_context import reset_request_identity, reset_request_platform, reset_request_user_message, set_request_identity, set_request_platform, set_request_user_message, set_request_protected_texts, reset_request_protected_texts
        platform_token = set_request_platform(platform)
        user_message_token = set_request_user_message(user_message if raw_user_message is None else raw_user_message)
        request_identity_tokens = set_request_identity(session_id, message_id, turn_id or message_id)
        protected_texts_token = set_request_protected_texts([system_prompt or '', *(item.get('content') for item in conversation_history or [] if isinstance(item, dict) and (item.get('role') == 'system' or item.get('name') in {'mirrow_inner_record', 'mirrow_inner_snapshot'}))])
        from context_inspection import bind_build, reset_build
        inspection_token = bind_build(inspection_call_id)
        affect_sidecar_token = _AFFECT_SIDECAR_CONTEXT.set([])
        affect_attempt_token = _AFFECT_PROVIDER_ATTEMPT.set(0)
        affect_provider_ok_token = _AFFECT_PROVIDER_OK.set(False)
        try:
            self._maybe_detect_memorial(user_message)
            self._current_session_id = session_id
            self._current_user_message_id = str(message_id or '')
            self._last_push_message_id = ''
            if self.use_agent_tools and self.agent_tools_adapter:
                return await self._process_with_agent_tools(user_message, conversation_history, system_prompt, night_mode_enabled, platform, session_id, images, message_id=message_id)
            return await self._process_legacy(user_message, conversation_history, system_prompt, session_id=session_id)
        finally:
            _AFFECT_PROVIDER_OK.reset(affect_provider_ok_token)
            _AFFECT_PROVIDER_ATTEMPT.reset(affect_attempt_token)
            _AFFECT_SIDECAR_CONTEXT.reset(affect_sidecar_token)
            reset_build(inspection_token)
            reset_request_identity(request_identity_tokens)
            reset_request_protected_texts(protected_texts_token)
            reset_request_user_message(user_message_token)
            reset_request_platform(platform_token)

    def _maybe_detect_memorial(self, user_message: str):
        """
        纪念日自动检测（决策 F）：在用户原始消息上检测纪念日意图。
        与 LLM 正常回复并行执行，不阻塞用户看到回复。

        检测流程：关键词预筛 → LLM Flash 意图确认 → 创建待确认日历事件 → WS 推送
        """
        from .intent_parser import has_memorial_signal
        if not has_memorial_signal(user_message):
            return
        today_mm_dd = datetime.now().strftime('%m-%d')
        title_prefix = user_message[:20].strip()
        dedup_key = f'{today_mm_dd}_{title_prefix}'
        try:
            from calendar_manager.manager import get_global_calendar_manager
            cal_mgr = get_global_calendar_manager()
            if cal_mgr.is_duplicate_suggestion(today_mm_dd, title_prefix):
                return
            cal_mgr.mark_suggestion_seen(today_mm_dd, title_prefix)
        except Exception:
            pass

        async def _background_detection():
            try:
                confirm_prompt = f'判断以下用户消息是否表达了"想把今天作为一个纪念日，以后每年都要记下来"的意图。\n\n用户消息："{user_message[:300]}"\n\n请只回复 JSON（不要 markdown）：\n- 如果是纪念日意图：{{"intent":"memorial","title":"纪念日标题（简短）","description":"备注描述（1句话）","emotion":"情绪（开心/感动/怀念/珍惜等）"}}\n- 如果不是：{{"intent":"not_memorial"}}\n\n注意：\n- 仅当用户明确表达"今天很重要/要记住/每年都要/第一次/纪念"等纪念含义时才判定 memorial\n- 普通聊天、日常对话判定为 not_memorial\n- title 要简短（不超过15字），description 要自然'
                result = await self._call_flash_for_memorial(confirm_prompt)
                if not result or result.get('intent') != 'memorial':
                    return
                title = result.get('title', '纪念日')[:30]
                description = result.get('description', '')[:100]
                emotion = result.get('emotion', '')
                from calendar_manager.manager import get_global_calendar_manager
                from calendar_manager.models import CalendarEventCreateRequest
                cal_mgr = get_global_calendar_manager()
                req = CalendarEventCreateRequest(title=title, description=description, event_type='memorial', date_string=f'{datetime.now().year:04d}-{today_mm_dd}', creator='AI', emotion=emotion, is_active=False)
                event = cal_mgr.create_event(req)
                await cal_mgr.broadcast_memorial_suggestion(event)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f'纪念日后台检测失败: {e}')
        import asyncio as _asyncio
        try:
            _asyncio.create_task(_background_detection())
        except Exception:
            pass

    async def _call_flash_for_memorial(self, prompt: str) -> dict:
        """调用 Flash LLM 进行纪念日意图确认"""
        import os
        api_key = os.getenv('DEEPSEEK_FLASH_API_KEY') or os.getenv('DEEPSEEK_API_KEY', '')
        raw_url = os.getenv('DEEPSEEK_FLASH_API_URL') or os.getenv('DEEPSEEK_API_URL') or 'https://api.deepseek.com/v1'
        api_url = re.sub('/(chat/completions|v1/chat/completions)$', '', raw_url.rstrip('/'))
        if not api_url.endswith('/v1'):
            api_url += '/v1'
        print(f'[FLASH_MEMORIAL] api_url={api_url}')
        model = os.getenv('DEEPSEEK_FLASH_MODEL', 'deepseek-v4-flash')
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=api_url, timeout=15.0, max_retries=1)
            from context_builder.channel_projection import prepare, backend_turn
            messages, _ = prepare([backend_turn(prompt)])
            response = await client.chat.completions.create(model=model, messages=messages, temperature=0.0, max_tokens=256)
            content = response.choices[0].message.content
            asyncio.create_task(log_token_usage('memorial_detect', model, response.usage if hasattr(response, 'usage') else None))
            if not content:
                return None
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end > start:
                return json.loads(content[start:end + 1])
            return None
        except Exception:
            return None

    def _build_flash_extraction_tools_desc(self, platform: str='pc') -> str:
        """构建 Flash 提取用的精简工具描述。≤3000 字符。"""
        lines = []
        for name, tool in self.tools.items():
            if not getattr(tool, 'visible_to_pro', True):
                continue
            schema = getattr(tool, 'parameters_schema', None) or {}
            if not schema.get('properties'):
                schema = getattr(tool, 'input_schema', None) or {}
            props = schema.get('properties', {})
            required = schema.get('required', [])
            param_names = [pname for pname in required if pname in props]
            for pname in props:
                if pname not in param_names and len(param_names) < 3:
                    param_names.append(pname)
            param_parts = []
            for pname in param_names:
                pdef = props[pname]
                req = '' if pname in required else '?'
                desc_cap = 72 if pname in required else 12
                desc = pdef.get('description', '')[:desc_cap]
                if 'enum' in pdef:
                    desc = '/'.join((str(v) for v in pdef['enum']))
                param_parts.append(f'{req}{pname}:{desc}')
            param_str = ' '.join(param_parts) if param_parts else ''
            fd = (getattr(tool, 'flash_description', '') or '').strip()
            desc = (fd or tool.description)[:40].replace('\n', ' ')
            lines.append(f'{name}: {desc} | {param_str}')
        result = '\n'.join(lines)
        _tool_cap = _cap('flash_tools_cap', 3000)
        if len(result) > _tool_cap:
            logger.warning(f'Flash 工具描述超过 {_tool_cap} 字符 ({len(result)})，已截断，末尾工具可能不可见')
            return result[:_tool_cap]
        return result
    _CN_NUM = _DURATION_CN_NUM
    _TIME_PATTERNS = [(re.compile('(\\d+)\\s*小时后'), lambda m: timedelta(hours=int(m.group(1)))), (re.compile('(\\d+)\\s*分钟后'), lambda m: timedelta(minutes=int(m.group(1)))), (re.compile('半个小时后|半小时后'), lambda m: timedelta(minutes=30)), (re.compile('([一两二三四五六七八九十])\\s*分钟后'), lambda m: timedelta(minutes=_DURATION_CN_NUM.get(m.group(1), 1))), (re.compile('([半一两二三四五六七八九十]+)\\s*小时后'), lambda m: timedelta(hours=_DURATION_CN_NUM.get(m.group(1), 1))), (re.compile('等会儿|过会儿|过一阵|一会后|待会儿|待会'), lambda m: timedelta(minutes=5)), (re.compile('晚点'), lambda m: timedelta(minutes=30))]

    @classmethod
    def _infer_future_time(cls, text: str) -> Optional[str]:
        """从自然语言文本推断未来时间，返回 ISO 8601 datetime 字符串或 None。"""
        if not text:
            return None
        for pattern, delta_fn in cls._TIME_PATTERNS:
            m = pattern.search(text)
            if m:
                try:
                    return (datetime.now() + delta_fn(m)).isoformat(timespec='seconds')
                except Exception:
                    continue
        return None

    def _normalize_tool_params(self, tool_name: str, params: dict) -> None:
        """标准化前归一化工具参数，修复 Flash 提取的常见格式偏差。"""
        if tool_name in ('create_scheduled_task', 'schedule_self_task'):
            tc = params.get('trigger_config')
            if isinstance(tc, str) and tc.strip():
                today = datetime.now().strftime('%Y-%m-%d')
                match = re.search('(\\d{1,2}):?(\\d{2})?', tc.strip())
                if match:
                    h, m = (int(match.group(1)), int(match.group(2) or 0))
                    params['trigger_config'] = {'datetime': f'{today}T{h:02d}:{m:02d}:00'}
                    if 'trigger_type' not in params:
                        params['trigger_type'] = 'datetime'
            elif isinstance(tc, dict) and 'trigger_type' not in params:
                if tc.get('datetime'):
                    params['trigger_type'] = 'datetime'
                elif tc.get('cron'):
                    params['trigger_type'] = 'cron'
        if tool_name == 'schedule_self_task':
            tc = params.get('trigger_config')
            if not isinstance(tc, dict) or not (tc.get('datetime') or tc.get('cron')):
                text = params.get('execution_prompt', '') or params.get('title', '')
                dt = self._infer_future_time(text)
                if dt:
                    params['trigger_config'] = {'datetime': dt}
                    params.setdefault('trigger_type', 'datetime')

    def _execution_tool_params(self, tool_name: str, params: dict) -> dict:
        """Add trusted runtime-only fields after public contract validation."""
        execution_params = dict(params or {})
        if tool_name in ('capture_camera', 'take_screenshot'):
            execution_params['capture_only'] = True
        if tool_name == 'xhs_posts':
            execution_params['_message_id'] = getattr(self, '_current_user_message_id', '')
        return execution_params
    _TOOL_FEEDBACK = {'capture_camera': '摄像头拍摄完成，画面在上方工具返回材料中。像真正在看一样自然地回应。此工具已完成，不要再重复调用。', 'take_screenshot': '截屏完成，内容在上方工具返回材料中。请用与当前问题有关的屏幕证据作出具体判断，不要逐项复述画面。此工具已完成，不要再重复调用。', 'web_search': '搜索完成，结果在上方工具返回材料中。像你查到了东西在跟她分享一样自然地说。此工具已完成，不要再重复调用。', 'browse_xiaohongshu': '小红书只读材料已返回，结果在上方工具返回材料中。自然地先说每篇帖子各自的要点，再根据这些真实材料做横向总结；只有材料确实支持时，才说明与AI偏好有关的理由。若只完成了部分或页面没有识别到帖子，要把已取得数、目标数和具体障碍说清楚，不要把未读内容说成已经看过。若回执can_continue_now为true，可携task_id继续下一批；有具体障碍时按实际进度说明。', 'publish_xiaohongshu': '小红书创作回执已返回。published表示已在平台核验发布，previewed只是预览，submitted_unverified/submitting表示提交结果未知，可以核验原task_id，不能新建同文任务重复发。结合真实状态自然回应。', 'xiaohongshu_interactions': '小红书互动回执已返回。scan只是通知发现，read才包含实际原帖评论；previewed只预览，published才表示平台核验回复，提交未知沿原reply_id核验。结合真实外部原文和AI自己的完整回复自然回应。', 'cloud_music': '音乐操作已执行，结果在上方工具返回材料中。此工具已完成，除非她明确要你再操作，否则不要再调用同一action。', 'manage_ledger': '记账本操作已完成，结果在上方工具返回材料中。此工具已完成，不要再重复调用同一action。', 'manage_calendar': '日历操作已完成，结果在上方工具返回材料中。此工具已完成，不要再重复调用。', 'create_scheduled_task': '提醒已创建，结果在上方工具返回材料中。此工具已完成，不要再重复调用。', 'schedule_self_task': '自动任务已创建，结果在上方工具返回材料中。此工具已完成，不要再重复调用。', 'manage_scheduled_task': '提醒管理操作已完成，结果在上方工具返回材料中。此工具已完成，不要再重复调用。', 'toy2': 'toy2 指令已发送，不要再重复调用此工具。此工具已完成。', 'web_browser': '网页内容已获取，在上方工具返回材料中。此工具已完成，不要再重复调用。', 'send_voice': '语音消息已生成，不要再重复调用此工具，不要再写 [voice:...] 标记。', 'eyes': '视觉采集和融合分析完成，画面在上方工具返回材料中。像一个真正在看的人那样自然回应：说出与当前问题有关的证据和判断，不要复述整体画面；证据不足时明确说不确定。此工具已完成，不要再重复调用。', 'mobile_camera': '手机摄像头拍照和视觉分析完成，画面在上方工具返回材料中。像一个真正在看的人那样自然回应。此工具已完成，不要再重复调用。', 'band': '哨兵健康数据已查询完成，在上方工具返回材料中。请基于最新数据给出与当前问题有关的具体判断，不要逐字段播报。像关心她一样自然回应。此工具已完成，不要再重复调用。', 'check_phone': '手机状态已查询完成，在上方工具返回材料中。请基于手机屏幕、活动或可用状态证据回答当前问题并给出具体判断；不要逐字段播报，证据不足时明确说不确定。像关心她一样自然回应。此工具已完成，不要再重复调用。'}

    @classmethod
    def _clean_voice_markers(cls, text: str) -> str:
        """清洗语音协议及已定义展示前缀，保留自然语言正文。"""
        if not text:
            return ''
        from visible_reply import strip_voice_markers
        text = strip_voice_markers(text)
        from context_builder.display_markers import strip_display_markers
        return strip_display_markers(text)

    @classmethod
    def _clean_frontend_content(cls, text: str) -> str:
        """清洗推送到前端的内容：去除所有内部格式标记"""
        if not text:
            return ''
        before_capture_count = len(_AFFECT_SIDECAR_CONTEXT.get() or [])
        text = _capture_affect_sidecar(text, origin='content', message_id='')
        captured_affect = len(_AFFECT_SIDECAR_CONTEXT.get() or []) > before_capture_count
        from visible_reply import clean_visible_reply
        text = clean_visible_reply(text)
        if captured_affect or any((item.get('origin') == 'provider' and (not item.get('visible_hash')) for item in _AFFECT_SIDECAR_CONTEXT.get() or [])):
            _mark_latest_affect_visible_text(text)
        return text
    _DEFAULT_TOOL_FEEDBACK = '此工具已完成，结果在上方工具返回材料中。自然接着聊；查询或感知结果必须用于回答当前问题，至少给出一个有证据支撑的具体判断，不要逐字段播报或照抄原始数据，结果不足或失败时明确说不确定。此工具已完成执行，除非用户明确要求重新操作，否则不要重复调用同一工具。'
    _DEFAULT_TOOL_FAILURE_FEEDBACK = '请用你的语气告诉用户操作出了什么问题，语气要自然。此工具已尝试但失败，除非有明确的补救方案，否则不要重复调用。'
    _RESULT_SIGNAL_WORDS = {'看到了', '找到了', '搜到了', '已经记', '记的是', '结果是', '查到了', '拍到了', '截到了', '翻到了', '账本上写', '账本记', '写着', '记录了', '拍得很', '截得很', '摄像头也', '搜了下', '搜一搜', '帮你查了', '搜到', '查到', '搜索到', '查了下', '查一查', '搜搜', '记了一', '记了笔', '笔了', '账本里有', '账上记', '记着了', '我可记', '我给你记', '记你一笔', '罪成立', '根据搜索', '搜索显示', '网上说', '删掉了', '已经删', '已删了', '已消了', '消掉了', '账消了', '账清了', '账删了', '划掉了', '把账消掉', '放了首', '在播着', '正在播', '给你放了首', '切到', '删了', '删一下', '去删', '重新设', '重设', '设了', '设了新的', '设成', '加一个', '改了', '修改了', '更新了', '换成了', '分钟后', '小时后'}
    _SIGNAL_TOOL_MAP = {'删了': 'manage_scheduled_task(delete)/manage_ledger(delete)', '删掉了': 'manage_scheduled_task(delete)/manage_ledger(delete)', '删一下': 'manage_scheduled_task(delete)/manage_ledger(delete)', '去删': 'manage_scheduled_task(delete)/manage_ledger(delete)', '已经删': 'manage_scheduled_task(delete)/manage_ledger(delete)', '已删了': 'manage_scheduled_task(delete)/manage_ledger(delete)', '账删了': 'manage_ledger(delete)', '划掉了': 'manage_ledger(delete)', '重新设': 'create_scheduled_task', '重设': 'create_scheduled_task', '设了': 'create_scheduled_task', '设了新的': 'create_scheduled_task', '设成': 'create_scheduled_task', '加一个': 'create_scheduled_task', '分钟后': 'create_scheduled_task', '小时后': 'create_scheduled_task', '改了': 'manage_scheduled_task(edit)', '修改了': 'manage_scheduled_task(edit)', '更新了': 'manage_scheduled_task(edit)', '换成了': 'manage_scheduled_task(edit)', '记了一': 'manage_ledger(add)', '记了笔': 'manage_ledger(add)', '我给你记': 'manage_ledger(add)', '放了首': 'cloud_music(search_play)', '在播着': 'cloud_music(now_playing)'}
    _THINKING_ACTION_KEYWORDS = {'删除', '删了', '删掉', '删一下', '创建', '新建', '添加', '加上', '列出', '查看', '查询', '搜索', '修改', '更改', '编辑', '更新', '取消', '撤销', '移除', '执行', '调用', '触发', '运行', '获取', '读取', '检查', '检测', '打开', '关闭', '停止', '启动', '切换', '调整', '设置', 'delete', 'create', 'list', 'add', 'edit'}

    @classmethod
    def _build_flash_context(cls, user_message: str, pro_nl: str, reasoning: str, pro_tool_call: Optional[Dict]=None, executed_tools: Optional[List[Dict]]=None, signal_hits: Optional[List[str]]=None, signal_tools: Optional[set]=None, think_hits: Optional[List[str]]=None, candidate_reasons: Optional[List[str]]=None) -> str:
        """构建 Flash 的结构化输入：包含工具调用状态 + 已执行历史 + 分层检测结果。"""
        parts = []
        if pro_tool_call:
            tc_list = pro_tool_call if isinstance(pro_tool_call, list) else [pro_tool_call]
            tools_used = ','.join((tc.get('tool', '?') for tc in tc_list))
            parts.append(f'[状态] Pro本轮调用: {tools_used}')
        else:
            risk = '高' if signal_hits and signal_tools else '无'
            parts.append(f'[状态] Pro本轮未调用工具（编造风险: {risk}）')
        if executed_tools:
            prev = [(t.get('tool', '?'), t.get('parameters', {}), t.get('result', '')) for t in executed_tools]
            prev_str = '; '.join((f"{t}({p.get('action', '?')})" for t, p, _ in prev[-3:]))
            parts.append(f'[状态] 本轮已执行: {prev_str}')
            if prev:
                last_result = prev[-1][2]
                if last_result:
                    parts.append(f'[状态] 上一步结果: {last_result[:150]}')
        if signal_hits and signal_tools:
            parts.append(f'[编造信号] 检测到AI声称已执行但未调工具!')
            for w in signal_hits[:6]:
                tool_hint = cls._SIGNAL_TOOL_MAP.get(w, '')
                parts.append(f'  · "{w}" → {tool_hint}' if tool_hint else f'  · "{w}"')
        elif signal_hits:
            parts.append(f"[编造信号] {','.join(signal_hits[:5])} (无对应工具映射)")
        if candidate_reasons:
            parts.append('[行动候选] 确定性预筛发现可能存在真实行动，但尚未选择任何工具: ' + ','.join(candidate_reasons))
        user_kw = [kw for kw in _REMOTE_FALLBACK_KEYWORDS if kw in (user_message or '').lower()]
        if user_kw:
            parts.append(f"[用户意图] 关键词: {','.join(sorted(set(user_kw))[:8])}")
        else:
            parts.append('[关键词预筛] 未命中预设关键词；这只表示确定性规则未发现候选，不能单独证明用户没有提出相关请求。')
        if think_hits:
            parts.append(f"[思考动作] {','.join(think_hits[:5])}")
        _fuc = _cap('flash_user_cap', 300)
        _fac = _cap('flash_ai_cap', 400)
        _frc = _cap('flash_reasoning_cap', 200)
        parts.append(f'[对话]\n用户: {user_message[:_fuc]}\nAI回复: {pro_nl[:_fac]}')
        if reasoning:
            parts.append(f'AI思考: {reasoning[:_frc]}')
        return '\n'.join(parts)

    async def _compile_tool_actions_with_flash(self, directives: List[Dict[str, Any]], platform: str='pc') -> List[Dict[str, Any]]:
        """Compile AI-facing natural actions into the canonical tool contract.

        Tool-owned literal text, empty and unambiguous single-string schemas
        are deterministic. Remaining actions use one Flash extraction call,
        then restored to AI's original order and exact tool selection.
        """
        from collections import defaultdict
        from tool_action_protocol import resolve_tool_name
        compiled: list[Optional[Dict[str, Any]]] = [None] * len(directives)
        unresolved: list[tuple[int, Dict[str, Any]]] = []
        for index, directive in enumerate(directives):
            tool_name = resolve_tool_name(str(directive.get('tool') or ''), self.tools)
            if not tool_name:
                continue
            instruction = str(directive.get('instruction') or '').strip()
            tool = self.tools.get(tool_name)
            compile_text = getattr(tool, 'compile_action_text', None)
            parameters = compile_text(instruction) if callable(compile_text) else None
            if parameters is not None:
                compiled[index] = {'tool': tool_name, 'parameters': parameters}
                continue
            schema = getattr(tool, 'parameters_schema', None) or getattr(tool, 'input_schema', None) or {}
            properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
            if not instruction or not properties:
                compiled[index] = {'tool': tool_name, 'parameters': {}}
                continue
            if len(properties) == 1:
                parameter_name, definition = next(iter(properties.items()))
                if isinstance(definition, dict) and definition.get('type') == 'string' and (not definition.get('enum')):
                    compiled[index] = {'tool': tool_name, 'parameters': {parameter_name: instruction}}
                    continue
            unresolved.append((index, {'tool': tool_name, 'instruction': instruction}))
        if unresolved:
            forced = [item for _, item in unresolved]
            extracted = await self._compile_action_parameters_with_flash(forced, platform)
            raw_calls = extracted.get('tool_calls') or extracted.get('tool_call') or []
            if isinstance(raw_calls, dict):
                raw_calls = [raw_calls]
            by_tool: dict[str, list[Dict[str, Any]]] = defaultdict(list)
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                resolved = resolve_tool_name(str(raw.get('tool') or raw.get('name') or ''), self.tools)
                if resolved:
                    normalized = dict(raw)
                    normalized['tool'] = resolved
                    by_tool[resolved].append(normalized)
            for index, action in unresolved:
                candidates = by_tool.get(action['tool'], [])
                compiled[index] = candidates.pop(0) if candidates else {'tool': action['tool'], 'parameters': {}}
        result = [item for item in compiled if isinstance(item, dict)]
        _log_context_scheduler('自然语言动作已编译 | tools: ' + ','.join((str(item.get('tool') or '?') for item in result)))
        return result

    async def _compile_action_parameters_with_flash(self, actions: List[Dict[str, Any]], platform: str='pc') -> Dict[str, Any]:
        """Compile an authoritative action batch without re-deciding intent."""
        contracts: dict[str, Any] = {}
        for action in actions:
            name = str(action.get('tool') or '')
            tool = self.tools.get(name)
            if not tool:
                continue
            schema = getattr(tool, 'parameters_schema', None) or getattr(tool, 'input_schema', None) or {}
            contracts[name] = {'description': (getattr(tool, 'flash_description', '') or getattr(tool, 'description', ''))[:240], 'schema': schema}
        now_str = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        from .tool_compiler_prompt import action_compiler_prompt
        prompt = action_compiler_prompt(contracts, actions, now_str)
        try:
            from neuron_registry import neuron_trace
            from wander_manager.flash_structured import call_flash_json_detailed
            with neuron_trace('tool_action_compiler', model='Flash') as trace:
                trace.set_input(prompt)
                response = await call_flash_json_detailed([{'role': 'user', 'content': prompt}], temperature=0.0, max_tokens=4096, json_mode=True, enable_thinking=False, route_label='behavior_tool_compiler')
                content = response.raw_content or ''
                trace.set_output(content[:300])
            if response.status != 'ok' or not isinstance(response.parsed, dict):
                _log_context_scheduler(f'自然语言动作参数编译失败: {response.status}/{response.error}')
                return {'tool_calls': None}
            parsed = response.parsed
            if not isinstance(parsed, dict) or not parsed.get('tool_calls'):
                _log_context_scheduler('自然语言动作参数编译未返回调用: ' + content[:240].replace('\n', ' '))
            return parsed if isinstance(parsed, dict) else {'tool_calls': None}
        except Exception as exc:
            _log_context_scheduler(f'自然语言动作参数编译失败: {type(exc).__name__}: {str(exc)[:120]}')
            return {'tool_calls': None}

    async def _extract_intent_with_flash(self, nl: str, platform: str='pc') -> Dict[str, Any]:
        """用 Flash（温度=0）从 Pro 的自然语言中提取工具意图和参数。"""
        import os
        api_key = os.getenv('DEEPSEEK_FLASH_API_KEY') or os.getenv('DEEPSEEK_API_KEY', '')
        raw_url = os.getenv('DEEPSEEK_FLASH_API_URL') or os.getenv('DEEPSEEK_API_URL') or 'https://api.deepseek.com/v1'
        api_url = re.sub('/(chat/completions|v1/chat/completions)$', '', raw_url.rstrip('/'))
        if not api_url.endswith('/v1'):
            api_url += '/v1'
        model = os.getenv('DEEPSEEK_FLASH_MODEL', 'deepseek-v4-flash')
        print(f'[FLASH_EXTRACT] api_url={api_url} model={model} key_len={len(api_key)}')
        tools_desc = self._build_flash_extraction_tools_desc(platform)
        from datetime import datetime
        now_str = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        ledger_types = get_ledger_type_options()
        prompt = f'分析AI的自然语言，判断是否需要补调用工具。\n\n阅读AI的回复：用户读到这句话后，是否会自然期待某个工具被调用？如果会，那可能就是AI表达了调工具的意图，但忘了写 TOOL_CALL。\n\n参考规则:\n0. AI的自然语言可能隐含工具调用意图，两种情况：\n   - AI描述了已完成的结果（如"看到了""记下了""搜到了"），但实际没有调用工具。\n     用户会以为工具已执行，但实际上AI在描述不存在的结果。需要补调用。\n   - AI表达了将要做的动作（如"让我看看""睁眼看看""我去查""瞄一眼"），\n     用户会等待这个动作发生。如果AI没有同时输出 TOOL_CALL，需要补调用。\n   判断方式：把自己放在用户的位置——读到这句话，你会不会等着AI去执行某个工具？\n   多个意图 → 每个意图对应一个工具，全部放入 tool_calls 数组。\n1. 【最重要-防重复】先看 [状态] 本轮已执行 列表！\n   - 查询类工具（天气/手环/摄像头/截图/搜索/查手机/手机数据等）：同一个工具本轮已成功执行过 → 绝对不重复，跳过\n   - 操作类工具（记账/日历/提醒/云音乐/玩具等）：同一 action 已执行成功 → 不重复（如 list 过了不再 list）\n     例外：两步操作允许（如先 list 再 delete、先删旧再建新）\n   - [状态] 上一步结果 如果显示操作已成功（"已删除""已创建""已修改"）→ 该操作完成，不重复\n2. Pro未调用 + 检测到工具意图（上述两种情况之一） → 补调用\n3. Pro已调用 + 检测到还有遗漏的工具意图 → 补调用缺失的工具\n4. Pro未调用 + 没有上述两种信号、但用户消息中含动作词 → 仅当用户明确要求时才调用\n5. 没有任何工具意图 → tool_calls: null\n6. AI一句话可能表达多个工具意图（如"删了A再设B"），tool_calls 返回数组 [{...},{...}]，按执行顺序排列（先删后建）\n\n常见工具意图的触发场景（参考，不限于此——即使措辞不同，只要语义上表达了工具意图就补调用）:\n- 明确的摄像头请求: 看摄像头/打开摄像头/拍照/自拍/看看周围 → eyes；泛问“看看人类伙伴在干嘛”由 AI 根据目标和来源能力选感官。\n- 账本列表: 查一下账本/翻翻账本/账本记了什么/有哪些账 → manage_ledger action=list\n- 记账: 记一笔/记下来/给我记 → manage_ledger action=add\n  debt_type优先从已有类型选({ledger_types})，无法归类再自创2-4字\n- 查总账: 查总账/统计/多少次/排名 → manage_ledger action=query\n- 查详细/质疑: 查一下这条/看看这条/这条怎么回事/为什么记/当时怎么回事/详细账目/前因后果/乱记账/假账/错账/瞎记账 → manage_ledger action=detail\n  detail 用 keyword（从用户消息提取）或 entry_id（从上一步 list 结果获取）。质疑词("没做过""你记错了"等)有上下文时也用 detail\n- 搜索: 普通互联网搜索 → web_search；小红书专用机浏览或搜索 → browse_xiaohongshu\n- 小红书浏览: browse_xiaohongshu，instruction=AI已决定的自然语言只读目标（首页、主题搜索、账号主页或带task_id续读）；xhs_posts只查已读帖，不浏览新帖\n- 小红书发帖: publish_xiaohongshu；从AI行动完整提取标题、正文、封面原文和预览或公开发布意图，不代写、不截断。已有task_id沿原稿继续或查询。资料查询仍用bookshelf。\n- 小红书互动: xiaohongshu_interactions；scan发现通知，read按interaction_id读取原帖评论，preview/reply忠实提取AI完整原回复；status/reconcile沿已有reply_id查询或核验，不代写或重发未知提交。\n- 提醒: 提醒我/叫我/喊我/帮我设/设一个 → create_scheduled_task task_type="reminder"\n  参数: title=简短标题, reason=为什么设, trigger_type="datetime"/"cron"\n- 立即查岗: "现在查岗/查一下/看她在干嘛/午睡没/睡了没" → 可用感官：check_phone 提供手机屏幕及使用状态，take_screenshot 提供电脑屏幕画面，eyes 提供本人和周围环境画面，band 提供健康数据。摄像头画面不代表手机屏幕；不要创建定时任务，也不要把它称作 inconsistency。\n- 延时查岗: "半小时后查岗/晚点查" → schedule_self_task，execution_prompt 写明届时要核验的问题，tool_name 留空，由到点后的你根据新鲜事实选择感官。任务类型由工具内部确定，无须填写 task_type。\n  inconsistency 只用于系统已经连续确认的矛盾复核，绝不是用户请求或普通定时任务。\n- 删除提醒: 删除提醒/取消提醒/删了那个/删了查岗/删了错误查岗/把.*删了 → manage_scheduled_task。参数: action="delete", task_title=从上下文提取的关键词(如时间"0:56"或"查岗")\n# get_weather 已移除——天气由 Sentinel WeatherSource 自动注入上下文，无需 LLM 调用\n- 截图: 手机屏幕用 check_phone，电脑屏幕用 take_screenshot；目标不明时结合对话判断。\n- 写日历/纪念日: 记在纪念日/写进日历/设个纪念日 → manage_calendar action=add_memorial\n  需要从上下文提取 title date_string description emotion\n- 查看纪念日: 看看纪念日 → manage_calendar action=list\n- 删改账本/消账/销账: 先用 manage_ledger action=list + keyword=用户提到的关键词搜索，得到精确的 entry_id 后再 delete/edit\n  消账/销账/清账/划账/把账消了/把账销了 → 先 list 再定位 delete\n- 删除指定账本条目: 删第一笔/把这个删了/删掉这笔/删了它/就删这个/嗯删 → manage_ledger action=delete\n  必须从上一步 list 结果或上下文中获取准确的 entry_id\n- 删除最新一笔: 删刚刚新加的/删刚加的/删新加的/删最新的/把新加的删了/刚记的删了 → manage_ledger action=delete delete_last=true\n  无需先 list，直接 delete_last=true\n- 网易云推歌/歌曲卡片/推荐首歌 → cloud_music action=share（只分享，不启动播放器）\n- 网易云明确点播/放首歌/放点音乐 → cloud_music action=search_play；明确要单曲循环时带 mode=one，否则 mode=single\n- 网易云歌单：先 playlists 看已绑定歌单及 ID，再 playlist_tracks(playlist_id，分页 offset/limit) 读取人类伙伴/AI/共有歌单的真实曲目用于推荐；分享可一起听的歌单卡片=playlist_share，新建歌单=playlist_create，改名=playlist_rename，删除 AI 自己的网易云歌单=playlist_delete，加入歌单=playlist_add，移出歌单=playlist_remove，播放歌单=playlist_play；删除必须提取明确的 playlist_id，歌曲优先提取 song_id、否则 query 保留明确歌名和歌手，归属 subject=human/k/shared\n- 当前共听播放方式调整 → cloud_music action=session_mode，mode=single/list/loop/one；持续一起听开关 → action=session_follow，follow_external=true/false。辨认否定与引用，不把讨论“单曲循环”当成设置命令。\n- 安静陪听 → cloud_music action=quiet，quiet=true；结束共同播放 → action=end\n  query 从AI回复或用户消息中提取"歌手 歌名"格式，不要只填歌名\n- 网易云恢复当前暂停的共听会话 → cloud_music action=resume；指定歌曲点播 → action=search_play 并提供 song_id 或明确 query\n- 网易云切换: 下一首/切歌/换一首/跳过/不听这首 → cloud_music action=next\n- 网易云暂停: 暂停/别放了/停一下 → cloud_music action=pause\n- 网易云查询: 什么歌/在放什么/现在在放 → cloud_music action=now_playing\n- 小红书已读帖子/本地留帖: 查 AI 实际读过的帖、留在 MIRROW、移出、分享卡片、邀请人类伙伴手动评论 → xhs_posts；先 recent/list 取真实 node_id，再执行动作。此工具不浏览新帖、不操作平台点赞收藏或评论。\n\t- 发语音: "发语音""语音说""说句话"等关键词 → send_voice。每轮只调用一次，已执行过则跳过\n\t- 玩具控制: 甜焰 → toy2。只选命名模式：轻吟、激奏、微澜、潮起、深吮、酥麻、炽焰、巅峰、停歇。\n\n- 手环数据: 心率/心跳/看看手环/测心率/睡眠/步数/血氧/睡了多久 → band action=heart_rate/spo2/sleep/steps/all_health\n- 给自己安排延时自动任务: 等会儿看/过会儿/待会儿/等一下再/半小时后/过一阵/晚点/过X分钟/过X小时/下次 → schedule_self_task\n  查岗感官: take_screenshot(电脑屏幕)/eyes(本人及环境的摄像头)/check_phone(手机屏幕和使用状态)/band(健康)\n- 查手机: 看看手机/查手机/电量/屏幕时间 → check_phone\n- 主动深搜记忆（bookshelf）: 自动召回没想起、候选含混、相似往事很多或需要列举时，source=memory 按大意搜索 Memory V2 摘要；只有需要原话/原因/过程才加 detail=true。已知日期的整段原始对话再用 source=conversation\n- 旧记忆原文复核: 上下文明确给出旧 OB 的 bucket_id 或 scene_id，且需要完整 scene 时 → fetch_source_details；只能原样使用已有标识\n- 共域（旧称朋友圈）: 人类伙伴明确提到共域或朋友圈并要查看/发布/评论/点赞/删除/改可见性或共域网名时 → manage_social_feed。一次最多调用一次；review 可查看指定日期和 AI 未读互动并酌情最多做一个 post/comment/like/delete/set_visibility/nickname；delete/set_visibility 只允许 AI 自己的真实帖子，nickname 只改 AI 的共域网名。public 动态供有访问权的朋友查看和互动，private 只在本地给人类伙伴和 AI 查看与互动、朋友家看不到；公开不代表逐一推送。工具结果只挂载到前端，不回注模型。\n- 裂隙档案目录/开局: 想看案件与开局 → rift_read_archive；明确选定 case_id + opening 后可用 rift_start_case 提案。只有人类伙伴当前原始消息明确同意开局，或她在两小时内对同一提案回答“好/可以/开吧”，后端才会真实创建\n- 裂隙档案讨论/继续: 需要最新局面、线索或 availableActions → rift_get_state\n- 裂隙档案行动: 已进入游玩且 AI 明确决定走某个最新 actionId → rift_take_action action_id=该 actionId。只讨论推测时不行动\n\n{tools_desc}\n\n输出JSON(不要markdown):\n无工具: {{"natural_language":"","tool_calls":null}}\n有工具: {{"natural_language":"","tool_calls":[{{"tool":"工具名","parameters":{{...}}}}]}}\n时间格式（trigger_config 必须为对象）：今晚→trigger_type=datetime, trigger_config={{"datetime":"今天日期T22:00:00"}}；明早→trigger_config={{"datetime":"今天日期T08:00:00"}}；明晚→trigger_config={{"datetime":"明天日期T22:00:00"}}\n\n当前时间: {now_str}\n[本轮已有上下文与待提取表达]\n{nl}'
        try:
            from neuron_registry import neuron_trace
            from llm_client import call_llm_api_flash
            with neuron_trace('flash_intent_extractor', model='Flash') as trace:
                trace.set_input(prompt)
                response = await call_llm_api_flash([{'role': 'user', 'content': prompt}], temperature=0.0, max_tokens=2048, enable_thinking=False, json_mode=True, request_timeout=25.0, max_attempts=1, usage_tag='intent_extract')
                content = response.get('content', '')
                reasoning = response.get('reasoning')
                trace.set_output(content[:300] if content else '')
                if reasoning:
                    trace.set_thinking(reasoning[:300])
            if not content:
                finish = response.get('finish_reason', '?')
                usage_info = ''
                reason_tail = f' reasoning={reasoning[-100:]}'.replace('\n', ' ') if reasoning else ''
                print(f'[FLASH_EXTRACT] empty response | finish={finish}{usage_info}{reason_tail} | NL: {nl[:80]}')
                return {'natural_language': nl, 'tool_calls': None}
            start, end = (content.find('{'), content.rfind('}'))
            if start != -1 and end > start:
                result = json.loads(content[start:end + 1])
                tcs = result.get('tool_calls') or result.get('tool_call')
                if isinstance(tcs, dict):
                    tcs = [tcs]
                tools_str = ','.join((tc.get('tool', '?') for tc in tcs)) if tcs else 'None'
                print(f'[FLASH_EXTRACT] tools=[{tools_str}] | raw={content[:150]}')
                return result
            print(f'[FLASH_EXTRACT] JSON parse failed | raw: {content[:200]}')
            return {'natural_language': nl, 'tool_calls': None}
        except Exception as e:
            print(f'[FLASH_EXTRACT] {type(e).__name__}: {e} | NL: {nl[:80]}')
            return {'natural_language': nl, 'tool_calls': None}

    def _has_tool_keywords(self, text: str, night_mode_enabled: bool=False) -> bool:
        """关键词预筛：无匹配词时跳过 Flash 调用。

        Args:
            text: 要检测的文本
            night_mode_enabled: 深夜模式是否开启。关闭时跳过 toy2 门控关键词。
        """
        if not text:
            return False
        text_lower = text.lower()
        for kw in _REMOTE_FALLBACK_KEYWORDS:
            if kw in text_lower:
                if not night_mode_enabled and kw in _NIGHT_GATED_KEYWORDS:
                    continue
                return True
        return False

    @staticmethod
    def _explicit_tool_categories(text: str, night_mode_enabled: bool=False) -> set[str]:
        """Return distinct explicit tool families present in the raw user turn."""
        lowered = str(text or '').lower()
        categories = set()
        for tool_name, keywords in TOOL_KEYWORD_MAP.items():
            if tool_name.startswith('_'):
                continue
            if tool_name == 'toy2' and (not night_mode_enabled):
                continue
            if any((str(keyword).lower() in lowered for keyword in keywords)):
                categories.add(tool_name)
        return categories

    @staticmethod
    def _tool_plan_family(tool_name: str) -> str:
        """Collapse aliases that represent one physical action family during completion."""
        return 'toy_control' if tool_name == 'toy2' else str(tool_name or '')
    _DIRECT_INTENT_PATTERNS = [(['看看纪念日', '有哪些纪念日', '日历上有什么', '查日历', '翻日历'], 'manage_calendar', lambda msg: {'action': 'list'}), (['查一下账本', '看看账本', '翻翻账本', '账本上记了', '账本记了', '有哪些账', '看看有什么账', '查查账本', '翻一下账本'], 'manage_ledger', lambda msg: {'action': 'list'}), (['划账', '划掉', '销账', '删账', '把账', '清账', '消账', '把账消了', '把账销了', '消了账', '销了账', '删了账', '清了账', '清一下账', '消一下账'], 'manage_ledger', lambda msg: {'action': 'list', 'keyword': _extract_ledger_keyword(msg)}), (['删刚刚新加', '删刚加', '删新加', '删最新', '删最后一笔', '删刚才那笔', '删刚记', '删刚刚记', '删最后一条', '删最新那条', '划刚刚新加', '划刚加', '消刚刚新加', '把刚刚那笔记的删了', '把新加的删了'], 'manage_ledger', lambda msg: {'action': 'delete', 'delete_last': True}), (['查总账', '统计一下账', '多少次了', '哪种最多', '哪类最多'], 'manage_ledger', lambda msg: {'action': 'query'}), (['查详细', '看看这条', '这条怎么回事', '为什么记这笔', '这笔怎么回事', '详细账目', '前因后果', '当时怎么回事', '查一下这条账', '这笔记的是什么', '乱记账', '假账', '错账', '瞎记账'], 'manage_ledger', lambda msg: {'action': 'detail', 'keyword': _extract_ledger_keyword(msg)}), (['帮我搜一下', '帮我搜', '搜一下', '搜索一下', '帮我查一下', '上网查一下', '帮我查查', '帮我搜搜', '搜一搜', '查一查', '搜搜看', '查查看'], 'web_search', lambda msg: {'query': _extract_search_query(msg)}), (['下一首', '切歌', '跳过', '换首歌', '不听这首'], 'cloud_music', lambda msg: {'action': 'next'}), (['暂停音乐', '暂停播放', '别放了', '停一下音乐'], 'cloud_music', lambda msg: {'action': 'pause'}), (['继续放当前歌曲', '继续播放当前歌曲'], 'cloud_music', lambda msg: {'action': 'resume'}), (['现在在放什么歌', '这是什么歌', '在听什么'], 'cloud_music', lambda msg: {'action': 'now_playing'}), (['安静陪听'], 'cloud_music', lambda msg: {'action': 'quiet', 'quiet': True}), (['结束共同播放', '结束一起听'], 'cloud_music', lambda msg: {'action': 'end'}), (['收藏这首歌', '喜欢这首歌', '点个红心'], 'cloud_music', lambda msg: {'action': 'like'}), (['我想用语音说', '让我用语音说', '说句话', '念出来', '念给我听', '说给你听'], 'send_voice', lambda msg: {'text': _extract_voice_text(msg)}), (['测心率', '看看心率', '测一下心率', '看一下心率', '心率多少', '我的心率', '检查心率', '查心率'], 'band', lambda msg: {'action': 'heart_rate'}), (['监测心率', '连续心率', '持续测心率'], 'band', lambda msg: {'action': 'heart_rate_continuous', 'duration': 15}), (['今天走了多少', '看看步数', '看步数', '今日步数', '走了几步', '查步数'], 'band', lambda msg: {'action': 'steps'}), (['睡眠数据', '昨晚睡', '昨晚睡眠', '看看睡眠', '查睡眠', '睡得好吗', '睡眠怎么样'], 'band', lambda msg: {'action': 'sleep'}), (['测血氧', '看看血氧', '血氧多少', '查血氧'], 'band', lambda msg: {'action': 'spo2'}), (['全部健康', '健康数据', '健康汇总', '看看健康', '查健康'], 'band', lambda msg: {'action': 'all_health'})]

    @classmethod
    def _detect_direct_intents(cls, user_message: str) -> List[Dict[str, Any]]:
        """检测全部互不重叠的高置信工具意图，保留原文中的先后顺序。"""
        if not user_message:
            return []
        msg = user_message.strip()
        candidates = []
        for keywords, tool_name, params_fn in cls._DIRECT_INTENT_PATTERNS:
            for kw in keywords:
                start = msg.find(kw)
                if start >= 0:
                    candidates.append((start, start + len(kw), -len(kw), tool_name, params_fn))
        chosen = []
        occupied: List[tuple[int, int]] = []
        for start, end, neg_len, tool_name, params_fn in sorted(candidates, key=lambda item: (item[2], item[0])):
            if any((start < used_end and end > used_start for used_start, used_end in occupied)):
                continue
            occupied.append((start, end))
            chosen.append((start, tool_name, params_fn))
        intents: List[Dict[str, Any]] = []
        signatures = set()
        for start, tool_name, params_fn in sorted(chosen, key=lambda item: item[0]):
            try:
                parameters = params_fn(msg)
            except Exception:
                parameters = {}
            intent = {'tool': tool_name, 'parameters': parameters}
            signature = json.dumps(intent, ensure_ascii=False, sort_keys=True)
            if signature in signatures:
                continue
            signatures.add(signature)
            intents.append(intent)
        return intents

    @classmethod
    def _detect_direct_intent(cls, user_message: str) -> Optional[Dict[str, Any]]:
        """兼容旧调用方：返回第一个高置信工具意图。"""
        intents = cls._detect_direct_intents(user_message)
        return intents[0] if intents else None

    async def _process_with_agent_tools(self, user_message: str, conversation_history: List[Dict]=None, system_prompt: str=None, night_mode_enabled: bool=False, platform: str='pc', session_id: str='', images: Optional[List[str]]=None, message_id: str='') -> SchedulerResult:
        """
        处理用户消息，Pro 生成 NL → Flash 提取意图 → 执行工具。

        - Pro 生成自然语言回复（可能附带 TOOL_CALL 快速路径）
        - Pro 未输出 TOOL_CALL 时，Flash 从 NL 提取意图+参数
        - NL 立即推送到前端，工具执行不阻塞回复
        - platform: "pc" | "mobile" — 手机端排除 PC 专属工具
        - session_id: 用于预生成 im_ 消息 ID，使实时推送和 session 持久化共享同一 ID
        - images: 用户直接发图列表。非空时本轮/后续轮切 flash-vision 看图生成（图片进上下文多模态）
        """
        _log_context_scheduler(f'开始处理消息 | 平台: {platform} | 用户消息: {user_message[:100]}... | 对话历史: {len(conversation_history or [])} 条')
        self._state = SchedulerState.THINKING
        tool_calls = []
        messages = list(conversation_history or [])
        tool_call_attempts: Dict[str, int] = {}
        tool_call_results: Dict[str, Dict[str, Any]] = {}
        thinking_chain: List[Dict[str, str]] = []
        intermediate_messages: List[Dict[str, str]] = []
        last_round_reasoning: Optional[str] = None
        image_description: Optional[str] = None
        _vision_active = bool(images)
        _vision_image_parts: List[Dict] = []
        _vision_sources: List[Dict[str, str]] = []
        if images:
            for _idx, _img in enumerate(images, 1):
                _img_url = _img if isinstance(_img, str) and _img.startswith('data:') else f'data:image/jpeg;base64,{_img}'
                _key = f'user_image_{_idx}'
                _label = f'人类伙伴发送的图片{_idx}'
                _vision_sources.append({'key': _key, 'label': _label})
                _vision_image_parts.extend(_vision_parts(_img_url, _key, _label))
        _TOOL_IMAGE_LABELS = {'capture_camera': '电脑摄像头', 'take_screenshot': 'PC截屏', 'mobile_camera': '手机摄像头', 'mobile_screenshot': '手机截屏'}

        async def _record_nonexecution(tool_name: str, parameters: Optional[Dict[str, Any]], reason: str, *, status: str='failure', delivery: str='model') -> Dict[str, Any]:
            """Persist and surface a receipt for every planned item that did not execute."""
            safe_name = str(tool_name or 'invalid_tool_call').strip() or 'invalid_tool_call'
            record = {'tool': safe_name, 'parameters': parameters if isinstance(parameters, dict) else {}, 'description': f'{safe_name} 未执行', 'result': reason, 'success': False, 'status': status, 'delivery': delivery, 'error': reason}
            _append_tool_receipt(tool_calls, record)
            await self._notify_status('tool_complete', record)
            _log_context_scheduler(f'工具计划回执 | tool: {safe_name} | status: {status} | reason: {reason[:160]}')
            return record
        pre_tool_executed = False
        direct_intents = self._detect_direct_intents(user_message)
        _turn_user_committed = False
        _has_model_pretool = any((self.tools.get(intent.get('tool')) is not None and self.tools[intent['tool']].get_result_delivery(**intent.get('parameters') or {}) != 'ui_only' for intent in direct_intents))
        if _has_model_pretool and (not images):
            messages.append({'role': 'user', 'content': build_request_user_content(user_message)})
            _turn_user_committed = True
        for direct_intent in direct_intents:
            direct_intent = dict(direct_intent)
            t_name = direct_intent['tool']
            t_params = direct_intent['parameters']
            _direct_tool = self.tools.get(t_name)
            if _direct_tool is not None and hasattr(_direct_tool, 'get_result_delivery') and (_direct_tool.get_result_delivery(**t_params) == 'ui_only'):
                continue
            if t_name in self.tools:
                _log_context_scheduler(f'Pre-tool 直接执行 | tool: {t_name}')
                direct_params = direct_intent.get('parameters')
                if isinstance(direct_params, dict):
                    self._normalize_tool_params(t_name, direct_params)
                std = self.tool_standardizer.standardize(direct_intent)
                if std.is_valid:
                    tool_inst = self.tools.get(t_name)
                    desc = tool_inst.get_user_facing_description(**std.parameters) if tool_inst else f'正在使用{t_name}'
                    await self._notify_status('tool_executing', {'tool': std.tool_name, 'parameters': std.parameters, 'description': desc})
                    command = Command(tool=std.tool_name, parameters=self._execution_tool_params(std.tool_name, std.parameters))
                    exec_result = await self.command_executor.execute(command)
                    result_content = exec_result.get('content') or exec_result.get('error') or ''
                    if t_name == 'eyes':
                        _err = exec_result.get('error')
                        _log_context_scheduler(f"Pre-tool eyes 完成 | 成功: {exec_result.get('success')} | 内容长度: {(len(result_content) if result_content else 0)} | 错误: {(_err or '')[:100]}")
                    result_msg = build_tool_execution_record(std.tool_name, exec_result)
                    messages.append({'role': 'system', 'content': result_msg, '_material_kind': 'tool_receipt'})
                    tool_call_record = {'tool': std.tool_name, 'parameters': std.parameters, 'description': desc, 'result': result_content, 'success': exec_result.get('success', False), 'status': 'success' if exec_result.get('success', False) else 'failure', 'delivery': exec_result.get('delivery', 'model'), **({'extra_data': exec_result.get('extra_data')} if exec_result.get('extra_data') else {})}
                    if exec_result.get('error'):
                        tool_call_record['error'] = exec_result['error']
                    _append_tool_receipt(tool_calls, tool_call_record)
                    _pre_images = (exec_result.get('extra_data') or {}).get('images') or []
                    if exec_result.get('success') and isinstance(_pre_images, list):
                        for _im in _pre_images:
                            if not isinstance(_im, dict) or not _im.get('image_url'):
                                continue
                            _key = reserve_vision_source_key(_im, [s['key'] for s in _vision_sources], f'{std.tool_name}_{len(_vision_sources) + 1}')
                            _label = str(_im.get('label') or _key)
                            _vision_sources.append({'key': _key, 'label': _label})
                            _vision_image_parts.extend(_vision_parts(_im['image_url'], _key, _label))
                        if _pre_images:
                            _vision_active = True
                    _pre_extra = exec_result.get('extra_data') or {}
                    _pre_single = _pre_extra.get('image_base64')
                    if exec_result.get('success') and _pre_single and (not _pre_images) and (not _pre_extra.get('vision_analyzed', False)):
                        _url = _pre_single if str(_pre_single).startswith('data:') else f'data:image/jpeg;base64,{_pre_single}'
                        _key = f'{std.tool_name}_{len(_vision_sources) + 1}'
                        _label = _TOOL_IMAGE_LABELS.get(std.tool_name, std.tool_name)
                        _vision_sources.append({'key': _key, 'label': _label})
                        _vision_image_parts.extend(_vision_parts(_url, _key, _label))
                        _vision_active = True
                    if not _pre_images:
                        await self._notify_status('tool_complete', tool_call_record)
                    tool_signature = json.dumps({'tool': std.tool_name, 'parameters': std.parameters}, ensure_ascii=False, sort_keys=True)
                    tool_call_attempts[tool_signature] = tool_call_attempts.get(tool_signature, 0) + 1
                    tool_call_results[tool_signature] = exec_result
                    pre_tool_executed = True
                    print(f'[SCHEDULER] Pre-tool 执行完成 | result_len={len(result_content)}')
                else:
                    reason = f'工具请求无效，未执行：{std.error_message}'
                    await _record_nonexecution(std.tool_name or t_name, std.parameters or t_params, reason, status='rejected')
                    messages.append({'role': 'system', 'content': reason, '_material_kind': 'runtime_contract'})
                    pre_tool_executed = True
            else:
                reason = f'工具未注册，未执行：{t_name}'
                await _record_nonexecution(t_name, t_params, reason, status='rejected')
                messages.append({'role': 'system', 'content': reason, '_material_kind': 'runtime_contract'})
                pre_tool_executed = True
        round_count = 0
        _std_fail_tool = None
        _std_fail_count = 0
        self._pushed_preview_nl = ''
        self._pushed_preview_msg_id = ''
        _im_content_seen = set()
        _mk_im_id = lambda ts: f'msg_{session_id}_{ts}' if session_id else ''
        _pending_im_ts = ''
        _result_finalization_only = False
        while round_count < self.MAX_ROUNDS:
            round_count += 1
            _nl_pushed_this_round = False
            _declared_result_refs: set[str] = set()
            flash_tool_call = None
            provider_tool_call = False
            self._state = SchedulerState.THINKING
            await self._notify_status('thinking', {'round': round_count})
            try:
                is_first_round = round_count == 1
                _vision_request = bool(_vision_active and _vision_image_parts)
                adapter, model_tag = self._select_chat_adapter(_vision_request)
                _log_context_scheduler(f'第{round_count}轮 | 使用 {model_tag} 模型 | 开始调用...')
                print(f'[SCHEDULER] 第{round_count}轮 {model_tag} 调用开始: {user_message[:50]}...')
                if not adapter:
                    await self._push_llm_reply('LLM适配器未初始化')
                    return SchedulerResult(final_response='LLM适配器未初始化', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description)
                dynamic_instruction_parts: List[str] = []
                if is_first_round:
                    dynamic_instruction_parts.append('\n\n【本轮工具阶段】固定前缀已经给出可用工具、动作协议与长期边界。根据当前事实自然回复；确需行动时按前述协议在末尾输出工具动作行。工具动作可以与完整自然语言同时存在，真实执行状态以本轮回注记录为准。')
                    if pre_tool_executed:
                        _pre_done = sorted({str(tc.get('tool') or '') for tc in tool_calls if isinstance(tc, dict) and tc.get('delivery', 'model') != 'ui_only'})
                        dynamic_instruction_parts.append('\n\n【工具已自动尝试】上方工具执行回执已包含这些工具各自的执行状态与返回材料：' + ', '.join((name for name in _pre_done if name)) + '。请区分 success、partial 与 failure；失败不等于目标已经完成。根据已取得的材料给人类伙伴一句完整、自然的回复，不要只说预告；成功且材料足够的工具不要重复调用，失败或不足时仅在确有需要时补救；若原始请求还明确要求了尚未执行的其他动作，必须继续输出对应的闭合工具动作行。如果本轮直接收尾，在自然语言最后另起一行输出 RESULTS_USED: ["实际使用过的工具名"]，必须列全上述工具；该行只用于系统校验，不会展示给人类伙伴。')
                else:
                    if _vision_request:
                        dynamic_instruction_parts.append('本次工具返回的原图随下一条后端视觉材料提供，表达与工具动作按共用视觉输出合同返回。')
                    elif _result_finalization_only:
                        dynamic_instruction_parts.append('\n\n【最终答复阶段】所有需要的工具均已执行，上方工具执行回执包含真实结果。本轮只能输出直接给人类伙伴的自然语言，禁止输出任何工具动作行，禁止再说『我先查』『我再看看』等预告。可见回复必须包含至少一个由现有结果支持的具体判断；证据不足或失败时明确说不确定或失败。不逐字段播报，也不能省略结果。在自然语言最后另起一行输出 RESULTS_USED: ["实际使用过的工具名"]，必须列全所有回注给你的工具结果；该行只用于系统校验，不会展示给人类伙伴。')
                    else:
                        dynamic_instruction_parts.append('\n\n【后续轮次】工具已执行完毕，上方工具返回材料是执行结果。\n先判断：还需要其他工具吗？\n- 需要 → 在末尾输出 @工具名 自然语言动作@\n- 不需要 → 直接用自然语言回复人类伙伴\n\n回复注意：自然接着聊，不必逐项播报，但不能完全省略工具结果。可见回复必须包含至少一个由最新结果支持的具体判断；证据不足或执行失败时明确说不确定或失败。若本轮不再调用工具，在自然语言最后另起一行输出 RESULTS_USED: ["实际使用过的工具名"]，列全所有回注给你的结果；该行不会展示给人类伙伴。你上一轮的回复人类伙伴已经看到了。别重复预告，别提「系统反馈」。没带工具动作行的预告不会执行任何工具，只会让人类伙伴干等一句空话。')
                    if tool_calls:
                        _turn_start = max((index for index, item in enumerate(messages) if item.get('role') == 'user'), default=-1)
                        _visible_turn_text = [str(item.get('content') or '').strip() for item in messages[_turn_start + 1:] if item.get('role') == 'assistant' and str(item.get('content') or '').strip()]
                        if _visible_turn_text:
                            dynamic_instruction_parts.append(f'【本轮续接锚点】你已经对人类伙伴显示了 {len(_visible_turn_text)} 段回复；这些 assistant 消息是已经说过的正文，不是草稿。本次只补充尚未说出的内容。最近一段的末尾是：\n' + _visible_turn_text[-1][-1200:])
                        _done = sorted(set((tc.get('tool', '') for tc in tool_calls if tc.get('status') in {'success', 'completed', 'ready'})))
                        if _done:
                            dynamic_instruction_parts.append(f"⚠️ 以下工具本轮已执行完毕、结果已在上下文中，不要再调用：{', '.join(_done)}")
                        _not_done = sorted(set((tc.get('tool', '') for tc in tool_calls if tc.get('status') in {'failure', 'failed', 'error', 'partial', 'partial_success'})))
                        if _not_done:
                            dynamic_instruction_parts.append('以下工具本轮未完整成功：' + ', '.join(_not_done) + '。不要表述为已经完成；是否补救取决于错误与当前需要。')
                        _required_refs = sorted(self._required_model_result_tools(tool_calls))
                        if _required_refs and (not _vision_request):
                            dynamic_instruction_parts.append('【结果消费校验】若本轮输出最终自然语言，末行 RESULTS_USED 必须恰好覆盖：' + json.dumps(_required_refs, ensure_ascii=False))
                    if night_mode_enabled:
                        dynamic_instruction_parts.append('【深夜模式提醒】你仍在深夜模式中。根据工具结果评估是否需要调整玩具状态。')
                if night_mode_enabled and self._night_rounds_without_toy_mention >= self.NIGHT_FALLBACK_THRESHOLD:
                    dynamic_instruction_parts.append('⚠️ 系统检测到你已连续多轮未关注玩具状态。本轮请务必评估并输出决策。')
                dynamic_instruction_parts.append(_AFFECT_SELF_SIDECAR_INSTRUCTION)
                enhanced_system_prompt = system_prompt or ''
                _llm_history = list(messages)
                _llm_history.append({'role': 'assistant', 'name': 'mirrow_inner_snapshot', 'content': '[当前能力状态]\n记账已有类型：' + get_ledger_type_options() + ('\n❤️深夜模式已开启，甜焰 toy2 控制可用。' if night_mode_enabled else '\n❤️深夜模式已关闭。如果需要控制甜焰toy2，再邀请她开启❤️深夜模式开关。')})
                if dynamic_instruction_parts:
                    _llm_history.append({'role': 'system', 'content': '\n\n'.join(dynamic_instruction_parts), '_material_kind': 'runtime_contract'})
                _vision_contract = build_vision_contract(_vision_sources, allow_tools=not _result_finalization_only) if _vision_request else ''
                _user_input_for_llm = user_message
                if _vision_request:
                    if not _turn_user_committed:
                        _vision_turn_text = user_message or '请结合图片内容回复'
                    else:
                        _vision_turn_text = '以下图片是本轮工具刚返回的视觉证据，不是人类伙伴发送的新消息；请结合上方原始请求与工具结果继续回复。'
                    _user_input_for_llm = [*_vision_image_parts, {'type': 'text', 'text': _vision_turn_text}, {'type': 'text', 'text': '【后端本轮运行合同，不是人类伙伴的新发言】\n' + _vision_contract}]
                _append_user_input = not _turn_user_committed or _vision_request
                begin_provider_attempt()
                result = await execute_main(self, adapter, vision=_vision_request, user_input=_user_input_for_llm, conversation_history=_llm_history, system_prompt=enhanced_system_prompt, response_format=VISION_RESPONSE_FORMAT if _vision_request else None, vision_expected_keys=[s['key'] for s in _vision_sources] if _vision_request else None, append_user_input=_append_user_input)
                result = _sanitize_provider_result(result)
                if result.get('routing_failed'):
                    await self._notify_status('routing_failure', {'reason': 'all_sources_unavailable'})
                    return SchedulerResult(final_response='', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description, failure_reason='all_sources_unavailable')
                if result.get('refusal'):
                    await self._notify_status('routing_refusal', {'reason': 'model_refusal'})
                    return SchedulerResult(final_response='', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description, failure_reason='model_refusal')
                if result.get('error') and _vision_request and (not manual_selection_active()):
                    _log_context_scheduler(f"[VISION] 视觉模型 {model_tag} 失败: {result['error'][:100]} | 整批降级 GLM")
                    try:
                        from llm_client import call_llm_api_visual
                        _fallback_messages = build_visual_fallback_messages(enhanced_system_prompt, _llm_history, _user_input_for_llm)
                        begin_provider_attempt()
                        _fallback_raw = await call_llm_api_visual(_fallback_messages, 'glm', response_format=VISION_RESPONSE_FORMAT)
                        _fallback_raw = _sanitize_provider_result(_fallback_raw)
                        _fallback_envelope = parse_vision_envelope(_fallback_raw.get('content', ''), [s['key'] for s in _vision_sources])
                        if not _fallback_envelope:
                            raise ValueError('GLM 未返回有效的结构化视觉响应')
                        _fallback_calls = _fallback_envelope.get('tool_calls') or []
                        result = {'content': _fallback_raw.get('content', ''), 'natural_language': _fallback_envelope['natural_language'], 'reasoning': _fallback_raw.get('reasoning', ''), 'tool_call': _fallback_calls[0] if _fallback_calls else None, 'tool_calls': _fallback_calls, 'vision_envelope': _fallback_envelope, 'error': None}
                    except Exception as _vision_exc:
                        _log_context_scheduler(f'[VISION] GLM 整批降级也失败: {_vision_exc}')
                result = _sanitize_provider_result(result)
                _vision_envelope = result.get('vision_envelope') if isinstance(result, dict) else None
                if _vision_envelope:
                    from visual_dialogue import apply_visual_receipts
                    for _completed_visual in apply_visual_receipts(tool_calls, _vision_envelope):
                        await self._notify_status('tool_complete', _completed_visual)
                    if any((str(s.get('key') or '').startswith('user_image_') for s in _vision_sources)):
                        image_description = storage_description(_vision_envelope)
                    _vision_active = False
                    _vision_image_parts = []
                    _vision_sources = []
                print(f"[SCHEDULER] 第{round_count}轮 {model_tag} 调用完成 | error={bool(result.get('error'))} | content_len={len(result.get('content', '') or '')}")
                if result.get('error'):
                    if _vision_request:
                        from visual_dialogue import fail_visual_receipts
                        _failed_visuals = fail_visual_receipts(tool_calls)
                        _failed_eyes = _failed_visuals[-1] if _failed_visuals else None
                        for _failed_visual in _failed_visuals:
                            await self._notify_status('tool_complete', _failed_visual)
                        _has_user_image = any((str(source.get('key') or '').startswith('user_image_') for source in _vision_sources))
                        if _failed_eyes:
                            visible_failure = '画面已经拍到了，但这次没能可靠完成统一视觉分析。原图还在视觉感知工具条里，我先不乱猜。'
                        elif _has_user_image:
                            visible_failure = '图片已经收到了，但这次没能可靠完成视觉分析，我先不乱猜。'
                        else:
                            visible_failure = '画面已经取得，但这次没能可靠完成视觉分析，我先不乱猜。'
                        await self._push_llm_reply(visible_failure)
                        return SchedulerResult(final_response=visible_failure, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description)
                    await self._push_llm_reply(f"LLM调用失败：{result['error']}")
                    return SchedulerResult(final_response=f"LLM调用失败：{result['error']}", tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description)
                if not _turn_user_committed:
                    _committed_user_content = result.get('request_user_content')
                    if _committed_user_content is None:
                        _committed_user_content = _user_input_for_llm
                    messages.append({'role': 'user', 'content': _committed_user_content})
                    _turn_user_committed = True
                assistant_content = result['content']
                if _vision_envelope:
                    messages.append({'role': 'system', '_material_kind': 'tool_receipt', 'content': '【本轮原图的已分析视觉事实】\n' + json.dumps({'sources': _vision_envelope.get('sources', {}), 'fused_summary': _vision_envelope.get('fused_summary', ''), 'missing_sources': _vision_envelope.get('missing_sources', [])}, ensure_ascii=False)})
                natural_language = result.get('natural_language', '')
                if result.get('vision_envelope'):
                    assistant_content = natural_language
                reasoning = result.get('reasoning', '')
                tool_call = result.get('tool_call')
                provider_tool_call = tool_call is not None
                tool_calls_pro = result.get('tool_calls', []) or []
                action_directives = result.get('action_directives', []) or []
                if action_directives:
                    action_calls = await self._compile_tool_actions_with_flash(action_directives, platform)
                    if action_calls:
                        merged_provider_calls = list(tool_calls_pro)
                        signatures = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in merged_provider_calls if isinstance(item, dict)}
                        for action_call in action_calls:
                            signature = json.dumps(action_call, ensure_ascii=False, sort_keys=True)
                            if signature not in signatures:
                                merged_provider_calls.append(action_call)
                                signatures.add(signature)
                        tool_calls_pro = merged_provider_calls
                        tool_call = tool_calls_pro if len(tool_calls_pro) > 1 else tool_calls_pro[0]
                        provider_tool_call = True
                natural_language, _nl_refs = self._split_result_refs(natural_language)
                assistant_content, _raw_refs = self._split_result_refs(assistant_content)
                _declared_result_refs = _nl_refs | _raw_refs
                if len(tool_calls_pro) > 1:
                    tool_call = tool_calls_pro
                _explicit_categories = self._explicit_tool_categories(user_message, night_mode_enabled) if is_first_round else set()
                _explicit_families = {self._tool_plan_family(name) for name in _explicit_categories}
                if isinstance(tool_call, dict) and len(_explicit_families) >= 2:
                    try:
                        _completion_input = self._build_flash_context(user_message=user_message, pro_nl=natural_language or assistant_content or '', reasoning=(reasoning or '')[-200:], pro_tool_call=tool_call)
                        _completion = await self._extract_intent_with_flash(_completion_input, platform)
                        _completion_calls = _completion.get('tool_calls') or _completion.get('tool_call') or []
                        if isinstance(_completion_calls, dict):
                            _completion_calls = [_completion_calls]
                        _merged_calls = [tool_call]
                        _merged_names = {self._tool_plan_family(str(tool_call.get('tool') or tool_call.get('name') or ''))}
                        _merged_signatures = {json.dumps(tool_call, ensure_ascii=False, sort_keys=True)}
                        for _candidate in _completion_calls:
                            if not isinstance(_candidate, dict):
                                continue
                            _candidate_name = str(_candidate.get('tool') or _candidate.get('name') or '')
                            if _candidate_name not in _explicit_categories:
                                continue
                            _candidate_family = self._tool_plan_family(_candidate_name)
                            if _candidate_family in _merged_names:
                                continue
                            _candidate_signature = json.dumps(_candidate, ensure_ascii=False, sort_keys=True)
                            if _candidate_signature in _merged_signatures:
                                continue
                            _merged_signatures.add(_candidate_signature)
                            _merged_names.add(_candidate_family)
                            _merged_calls.append(_candidate)
                        if len(_merged_calls) > 1:
                            tool_call = _merged_calls
                            tool_calls_pro = _merged_calls
                            _log_context_scheduler('显式多意图补全 | tools: ' + ','.join((str(item.get('tool') or item.get('name') or '?') for item in _merged_calls)))
                    except Exception as _completion_exc:
                        _log_context_scheduler(f'显式多意图补全失败，保留 Pro 原调用: {str(_completion_exc)[:160]}')
                if _result_finalization_only and tool_call:
                    _log_context_scheduler('结果最终答复阶段忽略模型重复 TOOL_CALL，仅保留自然语言通道')
                    tool_call = None
                    tool_calls_pro = []
                if not natural_language and (not tool_call) and (not is_first_round):
                    natural_language = assistant_content.strip()
                logger.info(f'[{model_tag}] Result: reasoning_len={(len(reasoning) if reasoning else 0)}, natural_language_len={(len(natural_language) if natural_language else 0)}')
                last_round_reasoning = reasoning if reasoning and reasoning != assistant_content else None
                if last_round_reasoning:
                    if is_first_round:
                        thinking_chain.append({'content': reasoning, 'timestamp': now_iso()})
                        logger.info(f'Added Pro reasoning to thinking_chain, total steps: {len(thinking_chain)}')
                    elif not tool_call:
                        thinking_chain.append({'content': reasoning, 'timestamp': now_iso()})
                        logger.info(f'Added reasoning to thinking_chain (non-first round, no tool_call), total steps: {len(thinking_chain)}')
                    else:
                        last_round_reasoning = reasoning
                logger.info('[%s] round=%s has_tool_call=%s natural_language_len=%s', model_tag, round_count, tool_call is not None, len(natural_language) if natural_language else 0)
            except Exception as e:
                error_detail = str(e)
                logger.error(f'agent_tools execute error (round={round_count}): {error_detail}')
                if round_count == 1 and self.pro_adapter and self.agent_tools_adapter and (not manual_selection_active()):
                    logger.warning('Pro adapter failed, falling back to Flash for remaining rounds')
                    print(f'[SCHEDULER] Pro 调用异常，回退 Flash: {e}')
                    self.pro_adapter = None
                    continue
                await self._push_llm_reply(f'LLM调用失败：{error_detail}')
                return SchedulerResult(final_response=f'LLM调用失败：{error_detail}', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description)
            _is_first_round = round_count == 1
            _reasoning_tail = (reasoning or '')[-150:] if _is_first_round and reasoning else ''
            if not tool_call:
                flash_tool_call = None
                nl_for_flash = natural_language or assistant_content or ''
                signal_hits = [w for w in self._RESULT_SIGNAL_WORDS if w in (nl_for_flash or '')]
                signal_tools = {self._SIGNAL_TOOL_MAP.get(w, '') for w in signal_hits} - {''}
                has_user_kw = self._has_tool_keywords(user_message, night_mode_enabled)
                think_hits = [w for w in self._THINKING_ACTION_KEYWORDS if w in _reasoning_tail.lower()] if _reasoning_tail else []
                has_nl_kw = self._has_tool_keywords(nl_for_flash, night_mode_enabled)
                has_reason_kw = self._has_tool_keywords(_reasoning_tail, night_mode_enabled) if _is_first_round else False
                candidate = None
                if _is_first_round and (not tool_calls):
                    candidate = detect_action_candidate(user_text=user_message, assistant_text=nl_for_flash, reasoning_tail=_reasoning_tail, conversation_history=conversation_history)
                should_flash = has_user_kw or bool(think_hits) or has_nl_kw or has_reason_kw or bool(candidate and candidate.should_review)
                if _result_finalization_only:
                    should_flash = False
                if not tool_calls:
                    should_flash = should_flash or bool(signal_hits)
                if should_flash:
                    _flash_reasoning = _reasoning_tail
                    flash_input = self._build_flash_context(user_message=user_message, pro_nl=nl_for_flash, reasoning=_flash_reasoning, pro_tool_call=tool_call, executed_tools=tool_calls if tool_calls else None, signal_hits=signal_hits, signal_tools=signal_tools, think_hits=think_hits, candidate_reasons=list(candidate.reasons) if candidate else None)
                    trigger_reason = 'signal' if signal_hits else 'think' if think_hits else 'candidate' if candidate and candidate.should_review else 'kw'
                    print(f'[SCHEDULER] Flash 提取触发 | round={round_count} | trigger={trigger_reason} | NL: {nl_for_flash[:80]}')
                    extracted = await self._extract_intent_with_flash(flash_input, platform)
                    flash_tool_call = extracted.get('tool_calls') or extracted.get('tool_call')
                    if isinstance(flash_tool_call, dict):
                        flash_tool_call = [flash_tool_call]
                    if flash_tool_call and isinstance(flash_tool_call, list) and (len(flash_tool_call) > 0):
                        tools_str = ','.join((tc.get('tool', '?') for tc in flash_tool_call))
                        _log_context_scheduler(f'Flash 提取工具意图 | tools: [{tools_str}]')
                if flash_tool_call and isinstance(flash_tool_call, list):
                    _filtered = []
                    for _tc in flash_tool_call:
                        _tn = _tc.get('tool', '')
                        _ti = self.tools.get(_tn)
                        if _ti:
                            _max = getattr(_ti, 'max_calls_per_round', 0)
                            _single = getattr(_ti, 'single_use', False)
                            _count = self._tool_limit_count(tool_calls, _tn, _ti)
                            if _max and _count >= _max:
                                _log_context_scheduler(f'Flash 提取过滤: {_tn} 已达上限({_count}/{_max})，跳过')
                                continue
                            if _single and _count > 0:
                                _log_context_scheduler(f'Flash 提取过滤: {_tn} 本轮已执行(single_use)，跳过')
                                continue
                        _filtered.append(_tc)
                    if _filtered:
                        flash_tool_call = _filtered
                    else:
                        flash_tool_call = None
                if not flash_tool_call or (isinstance(flash_tool_call, list) and len(flash_tool_call) == 0):
                    nl_check = natural_language or assistant_content or ''
                    kw_nl = self._has_tool_keywords(nl_check, night_mode_enabled)
                    kw_reason = bool(reasoning and self._has_tool_keywords(reasoning, night_mode_enabled))
                    kw_user = self._has_tool_keywords(user_message, night_mode_enabled)
                    print(f'[SCHEDULER] Flash 未触发 | round={round_count} kw_nl={kw_nl} kw_reason={kw_reason} kw_user={kw_user} | NL: {nl_check[:80]}')
                    self._state = SchedulerState.COMPLETE
                    final_response = assistant_content or natural_language
                    final_response = _xhs_visible_fallback(self._clean_frontend_content(final_response), tool_calls) or '嗯。'
                    _required_result_tools = self._required_model_result_tools(tool_calls)
                    _missing_result_refs = _required_result_tools - _declared_result_refs
                    _vision_grounded = bool(result.get('vision_envelope'))
                    if _required_result_tools and _missing_result_refs and (not _vision_grounded):
                        _has_prior_reply = bool(intermediate_messages or getattr(self, '_pushed_preview_nl', ''))
                        final_response = self._recover_unverified_result_reply(final_response, has_prior_reply=_has_prior_reply)
                        _log_context_scheduler('结果消费声明缺失，仅保留自然语言并记录诊断 | missing=' + ','.join(sorted(_missing_result_refs)))
                    elif _result_finalization_only and self._is_hanging_tool_preview(final_response):
                        _has_prior_reply = bool(intermediate_messages or getattr(self, '_pushed_preview_nl', ''))
                        final_response = self._recover_unverified_result_reply(final_response, has_prior_reply=_has_prior_reply)
                    pushed_preview = getattr(self, '_pushed_preview_nl', '')
                    should_push = bool(final_response)
                    if pushed_preview and final_response == pushed_preview:
                        should_push = False
                        if final_response in _im_content_seen:
                            final_response = ''
                        elif getattr(self, '_pushed_preview_msg_id', ''):
                            self._last_push_message_id = self._pushed_preview_msg_id
                    elif pushed_preview:
                        common_len = 0
                        min_len = min(len(pushed_preview), len(final_response))
                        while common_len < min_len and pushed_preview[common_len] == final_response[common_len]:
                            common_len += 1
                        if common_len >= 10 and common_len >= len(pushed_preview) * 0.5:
                            final_response = final_response[common_len:].lstrip('，。；！？、\n\r ')
                            if not final_response:
                                should_push = False
                    if should_push and final_response:
                        final_response = self._dedup_paragraphs(final_response, messages)
                        should_push = bool(final_response)
                    self._pushed_preview_nl = ''
                    self._pushed_preview_msg_id = ''
                    if should_push:
                        im_id = _mk_im_id(now_iso())
                        self._last_push_message_id = im_id
                        llm_reply_data = {'content': final_response, 'message_id': im_id}
                        if last_round_reasoning:
                            llm_reply_data['reasoning'] = last_round_reasoning
                        await self._notify_status('llm_reply', llm_reply_data)
                    return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages, image_description=image_description)
                if isinstance(flash_tool_call, list) and len(flash_tool_call) == 1:
                    tool_call = flash_tool_call[0]
                else:
                    tool_call = flash_tool_call
            if isinstance(tool_call, list):
                all_results = []
                executed_any = False
                for tc in tool_call:
                    tc_dict = tc if isinstance(tc, dict) else {}
                    tc_params = tc_dict.get('parameters')
                    if isinstance(tc_params, dict):
                        self._normalize_tool_params(tc_dict.get('tool', ''), tc_params)
                    std = self.tool_standardizer.standardize(tc)
                    if not std.is_valid:
                        rejected_name = str(tc_dict.get('tool') or tc_dict.get('name') or 'invalid_tool_call')
                        reason = f'工具请求无效，未执行：{std.error_message}'
                        await _record_nonexecution(rejected_name, tc_params, reason, status='rejected')
                        all_results.append(f'[{rejected_name}]: {reason}')
                        executed_any = True
                        continue
                    t_name = std.tool_name
                    t_params = std.parameters
                    if t_name not in self.tools:
                        reason = f'工具未注册，未执行：{t_name}'
                        await _record_nonexecution(t_name, t_params, reason, status='rejected')
                        all_results.append(f'[{t_name}]: {reason}')
                        executed_any = True
                        continue
                    tool_inst = self.tools.get(t_name)
                    if tool_inst:
                        _max = getattr(tool_inst, 'max_calls_per_round', 0)
                        _single = getattr(tool_inst, 'single_use', False)
                        _count = self._tool_limit_count(tool_calls, t_name, tool_inst)
                        if _single and _count > 0:
                            reason = f'{t_name} 本轮已执行，重复计划已跳过'
                            await _record_nonexecution(t_name, t_params, reason, status='skipped')
                            all_results.append(f'[{t_name}]: {reason}')
                            executed_any = True
                            continue
                        if _max and _count >= _max:
                            reason = f'{t_name} 已达到本轮调用上限（{_count}/{_max}），未再次执行'
                            await _record_nonexecution(t_name, t_params, reason, status='skipped')
                            all_results.append(f'[{t_name}]: {reason}')
                            executed_any = True
                            continue
                    if t_name == 'toy2' and (not night_mode_enabled):
                        desc = tool_inst.get_user_facing_description(**t_params) if tool_inst else f'正在使用{t_name}'
                        await self._notify_status('tool_executing', {'tool': t_name, 'parameters': t_params, 'description': desc})
                        blocked_record = {'tool': t_name, 'parameters': t_params, 'description': desc, 'result': '深夜模式未开启，请在页面点击❤️开启', 'success': False, 'status': 'failure', 'delivery': 'model', 'error': '深夜模式未激活'}
                        _append_tool_receipt(tool_calls, blocked_record)
                        await self._notify_status('tool_complete', blocked_record)
                        all_results.append(f'[{t_name}]: 深夜模式未开启，未执行')
                        executed_any = True
                        continue
                    command = Command(tool=t_name, parameters=self._execution_tool_params(t_name, t_params))
                    desc = tool_inst.get_user_facing_description(**t_params) if tool_inst else f'正在使用{t_name}'
                    await self._notify_status('tool_executing', {'tool': t_name, 'parameters': t_params, 'description': desc})
                    exec_r = await self.command_executor.execute(command)
                    _multi_signature = json.dumps({'tool': t_name, 'parameters': t_params}, ensure_ascii=False, sort_keys=True)
                    tool_call_attempts[_multi_signature] = tool_call_attempts.get(_multi_signature, 0) + 1
                    tool_call_results[_multi_signature] = exec_r
                    success = exec_r.get('success')
                    content = exec_r.get('content', '') or exec_r.get('error', '')
                    delivery = exec_r.get('delivery', 'model')
                    record = {'tool': t_name, 'parameters': t_params, 'description': desc, 'result': content, 'success': success, 'status': 'success' if success else 'failure', 'delivery': delivery, **({'extra_data': exec_r.get('extra_data')} if exec_r.get('extra_data') else {})}
                    if exec_r.get('error'):
                        record['error'] = exec_r['error']
                    _append_tool_receipt(tool_calls, record)
                    _multi_images = (exec_r.get('extra_data') or {}).get('images') or []
                    if success and isinstance(_multi_images, list):
                        for _im in _multi_images:
                            if not isinstance(_im, dict) or not _im.get('image_url'):
                                continue
                            _key = reserve_vision_source_key(_im, [s['key'] for s in _vision_sources], f'{t_name}_{len(_vision_sources) + 1}')
                            _label = str(_im.get('label') or _key)
                            _vision_sources.append({'key': _key, 'label': _label})
                            _vision_image_parts.extend(_vision_parts(_im['image_url'], _key, _label))
                        if _multi_images:
                            _vision_active = True
                    if not _multi_images:
                        await self._notify_status('tool_complete', record)
                    executed_any = True
                    if delivery != 'ui_only':
                        all_results.append(f'[{t_name}]: {content}')
                    _log_context_scheduler(f'多工具执行 | tool: {t_name} | success: {success}')
                if all_results:
                    _std_fail_count = 0
                    _multi_im_ts = now_iso()
                    clean_content = self._safe_nl(natural_language, assistant_content)
                    if clean_content and last_round_reasoning and (clean_content not in _im_content_seen):
                        _im_content_seen.add(clean_content)
                        intermediate_messages.append({'content': clean_content, 'thinking': last_round_reasoning, 'id': _mk_im_id(_multi_im_ts), 'timestamp': _multi_im_ts})
                    if clean_content:
                        if not (messages and messages[-1].get('role') == 'assistant' and (messages[-1].get('content') == clean_content)):
                            messages.append({'role': 'assistant', 'content': clean_content})
                        _tools_done = ', '.join((r.split(']:')[0].strip('[]') for r in all_results))
                        messages.append({'role': 'system', 'content': self._build_tool_narrative(_tools_done, multi=True), '_material_kind': 'tool_contract'})
                    multi_result = '\n---\n'.join(all_results)
                    _result_names = {item.split(']:', 1)[0].removeprefix('[') for item in all_results}
                    multi_result += '\n\n' + self._DEFAULT_TOOL_FEEDBACK
                    messages.append({'role': 'system', 'content': multi_result, '_material_kind': 'tool_receipt'})
                    if _nl_pushed_this_round == False and clean_content:
                        pushed_preview = getattr(self, '_pushed_preview_nl', '')
                        if natural_language.strip() != pushed_preview.strip():
                            clean_multi_nl = clean_content
                            llm_reply_data = {'content': clean_multi_nl, 'message_id': _mk_im_id(_multi_im_ts)}
                            if last_round_reasoning:
                                llm_reply_data['reasoning'] = last_round_reasoning
                            await self._notify_status('llm_reply', llm_reply_data)
                            self._pushed_preview_nl = clean_multi_nl
                            self._pushed_preview_msg_id = _mk_im_id(_multi_im_ts)
                        _nl_pushed_this_round = True
                elif executed_any:
                    self._state = SchedulerState.COMPLETE
                    final_response = _xhs_visible_fallback(self._clean_frontend_content(self._safe_nl(natural_language, assistant_content)), tool_calls) or '嗯。'
                    if not _nl_pushed_this_round:
                        im_id = _mk_im_id(now_iso())
                        self._last_push_message_id = im_id
                        llm_reply_data = {'content': final_response, 'message_id': im_id}
                        if last_round_reasoning:
                            llm_reply_data['reasoning'] = last_round_reasoning
                        await self._notify_status('llm_reply', llm_reply_data)
                    return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages, image_description=image_description)
                else:
                    self._state = SchedulerState.COMPLETE
                    final_response = assistant_content or natural_language
                    final_response = _xhs_visible_fallback(self._clean_frontend_content(final_response), tool_calls) or '嗯。'
                    await self._push_llm_reply(final_response, last_round_reasoning)
                    return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages, image_description=image_description)
                continue
            if isinstance(tool_call, dict):
                tc_params = tool_call.get('parameters')
                if isinstance(tc_params, dict):
                    self._normalize_tool_params(tool_call.get('tool', ''), tc_params)
            standardized = self.tool_standardizer.standardize(tool_call)
            if not standardized.is_valid:
                _attempted_name = str(tool_call.get('tool') or tool_call.get('name') or 'invalid_tool_call') if isinstance(tool_call, dict) else 'invalid_tool_call'
                _attempted_params = tool_call.get('parameters') if isinstance(tool_call, dict) else {}
                await _record_nonexecution(_attempted_name, _attempted_params if isinstance(_attempted_params, dict) else {}, f'工具请求无效，未执行：{standardized.error_message}', status='rejected')
                clean_err = self._clean_frontend_content(natural_language) or self._clean_frontend_content(self._extract_reasoning(assistant_content))
                if clean_err and (not _nl_pushed_this_round):
                    rejected_ts = now_iso()
                    rejected_msg_id = _mk_im_id(rejected_ts)
                    self._last_push_message_id = rejected_msg_id
                    await self._notify_status('llm_reply', {'content': clean_err, 'message_id': rejected_msg_id})
                    _nl_pushed_this_round = True
                if flash_tool_call or provider_tool_call:
                    logger.info(f"工具调用 standardize 失败，放弃执行并以当前 NL 收尾 （source={('provider' if provider_tool_call else 'fallback')}; error={standardized.error_message}）")
                    return SchedulerResult(final_response=clean_err or '嗯。', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages, image_description=image_description)
                attempted_tool = tool_call.get('tool', '') if isinstance(tool_call, dict) else ''
                if attempted_tool == _std_fail_tool:
                    _std_fail_count += 1
                else:
                    _std_fail_tool = attempted_tool
                    _std_fail_count = 1
                if _std_fail_count >= 2:
                    print(f'[SCHEDULER] 标准化连续失败 {_std_fail_count} 次 | tool={attempted_tool} | 放弃循环')
                    self._state = SchedulerState.COMPLETE
                    return SchedulerResult(final_response=clean_err, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages, image_description=image_description)
                if last_round_reasoning and clean_err not in _im_content_seen:
                    _im_content_seen.add(clean_err)
                    intermediate_messages.append({'content': clean_err, 'thinking': last_round_reasoning, 'id': _mk_im_id(now_iso()), 'timestamp': now_iso()})
                messages.append({'role': 'assistant', 'content': clean_err})
                messages.append({'role': 'system', '_material_kind': 'runtime_contract', 'content': f"工具调用格式有误：{standardized.error_message}。可用工具：{', '.join((name for name, tool in self.tools.items() if getattr(tool, 'visible_to_pro', True)))}。请保留原意，重新输出闭合的 @工具名 自然语言动作@；不要写 JSON 或参数名。"})
                continue
            _std_fail_count = 0
            tool_name = standardized.tool_name
            parameters = standardized.parameters
            _log_context_scheduler(f'工具调用 | 工具: {tool_name} | 参数: {parameters}')
            if tool_name not in self.tools:
                await _record_nonexecution(tool_name, parameters, f'工具未注册，未执行：{tool_name}', status='rejected')
                clean_content = self._safe_nl(natural_language, assistant_content)
                if last_round_reasoning and clean_content not in _im_content_seen:
                    _im_content_seen.add(clean_content)
                    intermediate_messages.append({'content': clean_content, 'thinking': last_round_reasoning, 'id': _mk_im_id(now_iso()), 'timestamp': now_iso()})
                messages.append({'role': 'assistant', 'content': clean_content})
                messages.append({'role': 'system', '_material_kind': 'runtime_contract', 'content': f"工具 '{tool_name}' 未注册。可用工具：{', '.join((name for name, tool in self.tools.items() if getattr(tool, 'visible_to_pro', True)))}。请用正确的工具名重试。"})
                continue
            if tool_name == 'toy2' and (not night_mode_enabled):
                tool_inst = self.tools.get(tool_name)
                desc = tool_inst.get_user_facing_description(**parameters) if tool_inst else f'正在使用{tool_name}'
                await self._notify_status('tool_executing', {'tool': tool_name, 'parameters': parameters, 'description': desc})
                blocked_record = _append_tool_receipt(tool_calls, {'tool': tool_name, 'parameters': parameters, 'description': desc, 'result': '深夜模式未开启，请在页面点击❤️开启', 'success': False, 'status': 'failure', 'delivery': 'model', 'error': '深夜模式未激活'})
                await self._notify_status('tool_complete', blocked_record)
                mock_result = json.dumps({'status': 'blocked', 'reason': '深夜模式未激活', 'suggestion': '请用温柔调皮的语气告诉用户：想玩玩具的话，先点一下页面上方的小心心开启深夜模式哦~'}, ensure_ascii=False)
                clean_content = self._safe_nl(natural_language, assistant_content)
                if last_round_reasoning and clean_content not in _im_content_seen:
                    _im_content_seen.add(clean_content)
                    intermediate_messages.append({'content': clean_content, 'thinking': last_round_reasoning, 'id': _mk_im_id(now_iso()), 'timestamp': now_iso()})
                messages.append({'role': 'assistant', 'content': clean_content})
                messages.append({'role': 'system', 'content': mock_result, '_material_kind': 'tool_receipt'})
                continue
            if night_mode_enabled:
                has_toy_mention = natural_language and ('玩具' in natural_language or '震' in natural_language) or str(tool_call).find('toy') >= 0
                if has_toy_mention:
                    self._night_rounds_without_toy_mention = 0
                else:
                    self._night_rounds_without_toy_mention += 1
            tool_signature = json.dumps({'tool': tool_name, 'parameters': parameters}, ensure_ascii=False, sort_keys=True)
            previous_result = tool_call_results.get(tool_signature)
            if previous_result:
                duplicate_result = previous_result.get('content') or previous_result.get('error', '')
                duplicate_status = '成功' if previous_result.get('success') else '失败'
                messages.append({'role': 'system', '_material_kind': 'runtime_contract', 'content': f'工具 {tool_name} 已用相同参数执行过（结果{duplicate_status}）：{duplicate_result}。下一轮进入最终答复阶段：直接根据现有结果回复人类伙伴，不得再调用任何工具。'})
                _result_finalization_only = True
                continue
            tool_instance = self.tools.get(tool_name)
            if tool_instance:
                _max = getattr(tool_instance, 'max_calls_per_round', 0)
                _single = getattr(tool_instance, 'single_use', False)
                _count = self._tool_limit_count(tool_calls, tool_name, tool_instance)
                already_executed = _single and _count > 0 or (_max and _count >= _max)
                if already_executed:
                    _existing_record = next((item for item in reversed(tool_calls) if isinstance(item, dict) and item.get('tool') == tool_name), None)
                    _existing_result = ''
                    if _existing_record:
                        _existing_result = str(_existing_record.get('result') or _existing_record.get('error') or '').strip()
                    messages.append({'role': 'system', '_material_kind': 'runtime_contract', 'content': f'工具 {tool_name} 本轮已执行过，不要再重复调用。' + (f'现有真实结果：{_existing_result}。' if _existing_result else '') + '下一轮进入最终答复阶段：直接根据现有结果回复人类伙伴，不得再调用任何工具。'})
                    _result_finalization_only = True
                    continue
            if natural_language and (not _nl_pushed_this_round):
                pushed_preview = getattr(self, '_pushed_preview_nl', '')
                if natural_language.strip() != pushed_preview.strip():
                    clean_nl = self._clean_frontend_content(natural_language)
                    if clean_nl:
                        _pending_im_ts = now_iso()
                        llm_reply_data = {'content': clean_nl, 'message_id': _mk_im_id(_pending_im_ts)}
                        if last_round_reasoning:
                            llm_reply_data['reasoning'] = last_round_reasoning
                        await self._notify_status('llm_reply', llm_reply_data)
                        self._pushed_preview_nl = clean_nl
                        self._pushed_preview_msg_id = _mk_im_id(_pending_im_ts)
                    _nl_pushed_this_round = True
            command = Command(tool=tool_name, parameters=self._execution_tool_params(tool_name, parameters))
            self._state = SchedulerState.TOOL_EXECUTING
            tool_instance = self.tools.get(tool_name)
            user_facing = tool_instance.get_user_facing_description(**parameters) if tool_instance else f'正在使用{tool_name}'
            await self._notify_status('tool_executing', {'tool': tool_name, 'parameters': parameters, 'description': user_facing})
            exec_result = await self.command_executor.execute(command)
            tool_call_attempts[tool_signature] = tool_call_attempts.get(tool_signature, 0) + 1
            tool_call_results[tool_signature] = exec_result
            exec_success = exec_result.get('success')
            exec_content = exec_result.get('content', '')
            exec_error = exec_result.get('error', '')
            _log_context_scheduler(f'工具执行完成: {tool_name} | 成功: {exec_success} | 结果长度: {(len(exec_content) if exec_content else 0)}' + (f' | 错误: {exec_error[:200]}' if exec_error else ''))
            logger.info('agent_tools round=%s executed tool=%s success=%s', round_count, tool_name, exec_success)
            tool_call_record = {'tool': tool_name, 'parameters': parameters, 'description': user_facing, 'result': exec_content or exec_error, 'success': exec_success, 'status': 'success' if exec_success else 'failure', 'delivery': exec_result.get('delivery', 'model'), **({'error': exec_error} if exec_error else {}), **({'extra_data': exec_result.get('extra_data')} if exec_result.get('extra_data') else {})}
            _append_tool_receipt(tool_calls, tool_call_record)
            _defer_visual_complete = bool((exec_result.get('extra_data') or {}).get('images'))
            if not _defer_visual_complete:
                await self._notify_status('tool_complete', tool_call_record)
            if exec_result.get('delivery', 'model') == 'ui_only':
                self._state = SchedulerState.COMPLETE
                final_response = self._safe_nl(natural_language, assistant_content) or '嗯。'
                final_response = self._clean_frontend_content(final_response) or '嗯。'
                pushed_id = getattr(self, '_pushed_preview_msg_id', '')
                if pushed_id:
                    self._last_push_message_id = pushed_id
                elif not _nl_pushed_this_round:
                    im_id = _mk_im_id(now_iso())
                    self._last_push_message_id = im_id
                    llm_reply_data = {'content': final_response, 'message_id': im_id}
                    if last_round_reasoning:
                        llm_reply_data['reasoning'] = last_round_reasoning
                    await self._notify_status('llm_reply', llm_reply_data)
                self._pushed_preview_nl = ''
                self._pushed_preview_msg_id = ''
                return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages, image_description=image_description)
            if exec_success:
                feedback = self._TOOL_FEEDBACK.get(tool_name, self._DEFAULT_TOOL_FEEDBACK)
                tool_result_msg = f'工具 {tool_name} 执行成功。\n结果：{exec_content}\n\n{feedback}'
            else:
                feedback = self._TOOL_FEEDBACK.get(tool_name, self._DEFAULT_TOOL_FAILURE_FEEDBACK)
                tool_result_msg = f'工具 {tool_name} 执行失败。\n错误：{exec_error}\n\n结果：{exec_content}\n\n{feedback}'
            clean_content = self._safe_nl(natural_language, assistant_content)
            if clean_content and last_round_reasoning and (clean_content not in _im_content_seen):
                _im_content_seen.add(clean_content)
                im_ts = _pending_im_ts or now_iso()
                intermediate_messages.append({'id': _mk_im_id(im_ts), 'content': clean_content, 'thinking': last_round_reasoning, 'timestamp': im_ts})
                _pending_im_ts = ''
            if clean_content:
                if not (messages and messages[-1].get('role') == 'assistant' and (messages[-1].get('content') == clean_content)):
                    messages.append({'role': 'assistant', 'content': clean_content})
            if clean_content:
                messages.append({'role': 'system', 'content': self._build_tool_narrative(tool_name), '_material_kind': 'tool_contract'})
            messages.append({'role': 'system', 'content': tool_result_msg, '_material_kind': 'tool_receipt'})
            _tool_extra = exec_result.get('extra_data') or {}
            _tool_images_meta = _tool_extra.get('images') or []
            if exec_success and _tool_images_meta and isinstance(_tool_images_meta, list):
                for _im in _tool_images_meta:
                    _u = _im.get('image_url') or ''
                    if _u:
                        _key = reserve_vision_source_key(_im, [s['key'] for s in _vision_sources], f'{tool_name}_{len(_vision_sources) + 1}')
                        _label = str(_im.get('label') or _key)
                        _vision_sources.append({'key': _key, 'label': _label})
                        _vision_image_parts.extend(_vision_parts(_u, _key, _label))
                        _log_context_scheduler(f"[VISION] {tool_name} 产图注入 | 来源: {_im.get('label', '?')} | 后续轮切 flash-vision")
                if _tool_images_meta:
                    _vision_active = True
            else:
                _tool_img_raw = _tool_extra.get('image_base64')
                if exec_success and _tool_img_raw and (not _tool_extra.get('vision_analyzed', False)):
                    _tool_img_url = _tool_img_raw if _tool_img_raw.startswith('data:') else f'data:image/jpeg;base64,{_tool_img_raw}'
                    _src_label = _TOOL_IMAGE_LABELS.get(tool_name, tool_name)
                    _src_key = f'{tool_name}_{len(_vision_sources) + 1}'
                    _vision_sources.append({'key': _src_key, 'label': _src_label})
                    _vision_image_parts.extend(_vision_parts(_tool_img_url, _src_key, _src_label))
                    _vision_active = True
                    _log_context_scheduler(f'[VISION] 工具 {tool_name} 产图注入 user 消息 | 来源标签: {_src_label} | 后续轮切 flash-vision')
        if self._required_model_result_tools(tool_calls):
            self._state = SchedulerState.COMPLETE
            final_response = self._grounded_result_fallback(tool_calls)
            await self._push_llm_reply(final_response)
            return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description)
        self._state = SchedulerState.WAITING_USER
        await self._push_llm_reply('人类伙伴，已经试了好几次了，但还是没能完全搞定。你记得有空修一下bug。')
        return SchedulerResult(final_response='人类伙伴，已经试了好几次了，但还是没能完全搞定。你记得有空修一下bug。', tool_calls=tool_calls, state=SchedulerState.WAITING_USER, need_user_input=True, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages, image_description=image_description)

    async def _execute_direct_tool(self, tool_name: str, parameters: Dict[str, Any], tool_calls: List, thinking_chain: List) -> SchedulerResult:
        """直接执行工具（用于明确的意图如天气查询、摄像头）"""
        try:
            command = Command(tool=tool_name, parameters=self._execution_tool_params(tool_name, parameters))
            self._state = SchedulerState.TOOL_EXECUTING
            tool_instance = self.tools.get(command.tool)
            user_facing = tool_instance.get_user_facing_description(**command.parameters) if tool_instance else f'正在使用{command.tool}'
            await self._notify_status('tool_executing', {'tool': command.tool, 'parameters': command.parameters, 'description': user_facing})
            _log_context_scheduler(f'执行工具: {tool_name} | 参数: {parameters}')
            exec_result = await self.command_executor.execute(command)
            _log_context_scheduler(f"工具执行完成: {tool_name} | 成功: {exec_result.get('success')} | 结果长度: {(len(exec_result.get('content', '')) if exec_result.get('content') else 0)}")
            tool_call_record = {'tool': command.tool, 'parameters': command.parameters, 'description': user_facing, 'result': exec_result.get('content') or exec_result.get('error', '')}
            _append_tool_receipt(tool_calls, tool_call_record)
            await self._notify_status('tool_complete', tool_call_record)
            if exec_result.get('success'):
                await self._push_llm_reply(exec_result.get('content', ''))
                return SchedulerResult(final_response=exec_result.get('content', ''), tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain)
            await self._push_llm_reply(exec_result.get('error') or '工具执行失败，请稍后重试。')
            return SchedulerResult(final_response=exec_result.get('error') or '工具执行失败，请稍后重试。', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain)
        except Exception as e:
            error_msg = f'工具执行异常: {str(e)}'
            _log_context_scheduler(f'ERROR: {error_msg}', 'ERROR')
            logger.error(f'_execute_direct_tool error: {e}', exc_info=True)
            await self._push_llm_reply(f'工具执行失败：{error_msg}')
            return SchedulerResult(final_response=f'工具执行失败：{error_msg}', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain)

    async def _process_legacy(self, user_message: str, conversation_history: List[Dict]=None, system_prompt: str=None, session_id: str='') -> SchedulerResult:
        """
        传统模式：使用意图识别处理消息（兼容旧逻辑）
        """
        _mk_im_id = lambda ts: f'msg_{session_id}_{ts}' if session_id else ''
        self._state = SchedulerState.THINKING
        tool_calls = []
        messages = conversation_history or []
        tool_call_attempts: Dict[str, int] = {}
        tool_call_results: Dict[str, Dict[str, Any]] = {}
        thinking_chain: List[Dict[str, str]] = []
        intermediate_messages: List[Dict[str, str]] = []
        base_system = self._build_system_prompt()
        if system_prompt:
            full_system = f'{system_prompt}\n\n{base_system}'
        else:
            full_system = base_system
        if not messages or messages[0].get('role') != 'system':
            messages = [{'role': 'system', 'content': full_system}] + messages
        should_add_user_message = True
        if messages and len(messages) > 0:
            last_msg = messages[-1]
            if last_msg.get('role') == 'user' and last_msg.get('content') == user_message:
                should_add_user_message = False
                logger.info('Skipping duplicate user message (already in conversation history)')
        if should_add_user_message:
            messages.append({'role': 'user', 'content': user_message})
        round_count = 0
        while round_count < self.MAX_ROUNDS:
            round_count += 1
            self._state = SchedulerState.THINKING
            await self._notify_status('thinking', {'round': round_count})
            try:
                llm_response = await self.call_llm(messages)
                assistant_content = llm_response.get('content', '')
                reasoning = llm_response.get('reasoning')
                if reasoning:
                    thinking_chain.append({'content': reasoning, 'timestamp': now_iso()})
                logger.info('behavior round=%s llm_output=%s', round_count, assistant_content[:500])
            except Exception as e:
                error_detail = getattr(e, 'detail', None) or str(e)
                await self._push_llm_reply(f'LLM调用失败：{error_detail}')
                return SchedulerResult(final_response=f'LLM调用失败：{error_detail}', tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain)
            parse_result = await self.command_parser.parse_async(assistant_content)
            logger.info('behavior round=%s parse is_command=%s should_execute=%s clean_content=%s', round_count, parse_result.is_command, parse_result.should_execute, (parse_result.clean_content or '')[:200])
            if not parse_result.should_execute:
                self._state = SchedulerState.COMPLETE
                final_response = parse_result.clean_content or assistant_content or '抱歉，我没有理解您的请求，请再说一次。'
                await self._push_llm_reply(final_response)
                return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain)
            command = parse_result.command
            tool_signature = json.dumps({'tool': command.tool, 'parameters': command.parameters}, ensure_ascii=False, sort_keys=True)
            if command.status != CommandStatus.VALID:
                error_msg = command.error_message
                messages.append({'role': 'assistant', 'content': self._clean_voice_markers(assistant_content)})
                messages.append({'role': 'user', 'content': f'系统反馈：命令解析错误：{error_msg}。请根据这个反馈继续处理用户请求。'})
                continue
            previous_attempts = tool_call_attempts.get(tool_signature, 0)
            previous_result = tool_call_results.get(tool_signature)
            logger.info('behavior round=%s parsed_tool=%s parameters=%s previous_attempts=%s', round_count, command.tool, command.parameters, previous_attempts)
            if previous_result:
                duplicate_result = previous_result.get('content') or previous_result.get('error', '')
                duplicate_status = '成功' if previous_result.get('success') else '失败'
                messages.append({'role': 'assistant', 'content': parse_result.clean_content or f'[重复调用工具: {command.tool}]'})
                duplicate_feedback = f'系统反馈：工具 {command.tool} 使用相同参数已经执行过一次，且执行{duplicate_status}：{duplicate_result}。不要再次调用相同工具和相同参数。请直接根据现有结果回答用户；如果现有结果不足，请换一种方式帮助用户。'
                messages.append({'role': 'system' if command.tool in {'bookshelf', 'fetch_source_details'} else 'user', 'content': duplicate_feedback})
                await self._notify_status('tool_complete', {'tool': command.tool, 'parameters': command.parameters, 'description': f'正在使用{command.tool}', 'result': f'检测到重复调用，已跳过再次执行，沿用上次{duplicate_status}结果。'})
                logger.warning('behavior round=%s duplicate tool=%s parameters=%s skipped=true', round_count, command.tool, command.parameters)
                continue
            pushed_nl_id = ''
            if parse_result.clean_content:
                pushed_nl_id = _mk_im_id(now_iso())
                await self._notify_status('llm_reply', {'content': parse_result.clean_content, 'message_id': pushed_nl_id})
            self._state = SchedulerState.TOOL_EXECUTING
            tool_instance = self.tools.get(command.tool)
            user_facing = tool_instance.get_user_facing_description(**command.parameters) if tool_instance else f'正在使用{command.tool}'
            await self._notify_status('tool_executing', {'tool': command.tool, 'parameters': command.parameters, 'description': user_facing})
            exec_result = await self.command_executor.execute(command)
            tool_call_attempts[tool_signature] = previous_attempts + 1
            tool_call_results[tool_signature] = exec_result
            logger.info('behavior round=%s executed tool=%s success=%s result=%s', round_count, command.tool, exec_result.get('success'), (exec_result.get('content') or exec_result.get('error') or '')[:300])
            tool_call_record = {'tool': command.tool, 'parameters': command.parameters, 'description': user_facing, 'result': exec_result.get('content') or exec_result.get('error', ''), 'success': exec_result.get('success', False), 'delivery': exec_result.get('delivery', 'model'), 'extra_data': exec_result.get('extra_data')}
            _append_tool_receipt(tool_calls, tool_call_record)
            await self._notify_status('tool_complete', tool_call_record)
            if exec_result.get('delivery', 'model') == 'ui_only':
                self._state = SchedulerState.COMPLETE
                final_response = self._clean_frontend_content(parse_result.clean_content) or '嗯。'
                if pushed_nl_id:
                    self._last_push_message_id = pushed_nl_id
                else:
                    im_id = _mk_im_id(now_iso())
                    self._last_push_message_id = im_id
                    await self._notify_status('llm_reply', {'content': final_response, 'message_id': im_id})
                return SchedulerResult(final_response=final_response, tool_calls=tool_calls, state=SchedulerState.COMPLETE, need_user_input=False, thinking_chain=thinking_chain, pending_intent_check=False, intermediate_messages=intermediate_messages)
            if not exec_result.get('success') and exec_result.get('error'):
                feedback = self._TOOL_FEEDBACK.get(command.tool, self._DEFAULT_TOOL_FAILURE_FEEDBACK)
                tool_result_msg = f"系统反馈：工具 {command.tool} 执行失败：{exec_result.get('error')}。\n{feedback}"
            else:
                feedback = self._TOOL_FEEDBACK.get(command.tool, self._DEFAULT_TOOL_FEEDBACK)
                tool_result_msg = f"系统反馈：工具 {command.tool} 执行成功，结果：{exec_result.get('content')}。\n{feedback}"
            messages.append({'role': 'assistant', 'content': parse_result.clean_content or f'[调用工具: {command.tool}]'})
            messages.append({'role': 'system' if command.tool in {'bookshelf', 'fetch_source_details'} else 'user', 'content': tool_result_msg})
        self._state = SchedulerState.WAITING_USER
        await self._push_llm_reply('人类伙伴，已经试了好几次了，但还是没能完全搞定。你记得有空修一下bug。')
        return SchedulerResult(final_response='人类伙伴，已经试了好几次了，但还是没能完全搞定。你记得有空修一下bug。', tool_calls=tool_calls, state=SchedulerState.WAITING_USER, need_user_input=True, thinking_chain=thinking_chain, intermediate_messages=intermediate_messages)
_global_scheduler: Optional[BehaviorScheduler] = None

async def get_global_scheduler() -> BehaviorScheduler:
    """获取全局调度器实例"""
    global _global_scheduler
    if _global_scheduler is None:
        raise RuntimeError('调度器未初始化，请先调用 init_global_scheduler')
    return _global_scheduler

def init_global_scheduler(call_llm_func: Callable, glm_api_key: str='', glm_api_url: str='', glm_model: str='glm-4v-flash', chrome_user_data: str=None, target_qq: str=None, use_agent_tools: bool=True, llm_provider: str='deepseek', llm_api_key: str=None, llm_base_url: str=None, llm_model: str=None, camera_device_name: str='', final_reply_caller: Callable=None, pro_api_key: str=None, pro_model: str=None, pro_base_url: str=None, pro_provider: str=None, pro_profile_id: str='deepseek-pro', pro_profile_label: str='DS Pro', pro_supports_vision: bool=False) -> BehaviorScheduler:
    """
    初始化全局调度器

    Args:
        call_llm_func: LLM调用函数（传统模式使用）
        glm_api_key: GLM API密钥
        glm_api_url: GLM API URL
        glm_model: GLM模型名
        chrome_user_data: Chrome用户数据目录
        target_qq: 目标QQ号
        use_agent_tools: 是否使用 LLM 适配器（Pro 对话 + Flash 意图提取）
        llm_provider: LLM提供商（deepseek/glm/ollama/openai）
        llm_api_key: LLM API密钥（Flash 意图提取 + 后续轮次）
        llm_base_url: LLM API URL（Flash）
        llm_model: LLM模型名（Flash）
        camera_device_name: 摄像头设备名称
    final_reply_caller: 已废弃，保留兼容（新流程首轮 Pro 已出 NL）
        pro_api_key: Pro API密钥（首轮聊天+工具决策）
        pro_model: Pro模型名（如 deepseek-v4-pro）
        pro_base_url: Pro API URL
        pro_provider: 主聊天模型 provider；与内部 Flash provider 相互独立
        pro_profile_id: 当前主聊天体验档的稳定 ID
        pro_profile_label: 前端可见标签
        pro_supports_vision: 当前主聊天模型是否直接接受图片

    Returns:
        BehaviorScheduler: 调度器实例
    """
    global _global_scheduler
    _global_scheduler = BehaviorScheduler(call_llm_func=call_llm_func, glm_api_key=glm_api_key, glm_api_url=glm_api_url, glm_model=glm_model, chrome_user_data=chrome_user_data, target_qq=target_qq, use_agent_tools=use_agent_tools, llm_provider=llm_provider, llm_api_key=llm_api_key, llm_base_url=llm_base_url, llm_model=llm_model, camera_device_name=camera_device_name, final_reply_caller=final_reply_caller, pro_api_key=pro_api_key, pro_model=pro_model, pro_base_url=pro_base_url, pro_provider=pro_provider, pro_profile_id=pro_profile_id, pro_profile_label=pro_profile_label, pro_supports_vision=pro_supports_vision)
    return _global_scheduler
