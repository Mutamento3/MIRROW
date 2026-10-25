"""
简化的 LLM Wrapper 实现

基于 agent_tools 项目设计，但移除了复杂的相对导入。
提供统一的工具调用抽象层。

核心思想：通过系统提示词强制LLM输出特定格式，而非依赖LLM"智能识别"。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import json
from .affect_capture import _strip_affect_sidecar_text
import re
import asyncio
import time
from token_tracker import log_token_usage
import os

from context_builder.assembly import assemble_provider_messages
from .tool_guidance import build_stable_tool_guidance


_JSON_PUNCTUATION = str.maketrans({
    "｛": "{", "｝": "}", "［": "[", "］": "]",
    "：": ":", "，": ",", "＂": '"', "“": '"', "”": '"',
})


def _normalize_json_separators(text: str) -> str:
    """Normalize provider punctuation, changing semicolons only outside strings."""
    value = str(text or "").translate(_JSON_PUNCTUATION)
    output = []
    quote = ""
    escaped = False
    for char in value:
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\" and quote:
            output.append(char)
            escaped = True
            continue
        if char in {'"', "'"}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            output.append(char)
            continue
        output.append("," if char in {";", "；"} and not quote else char)
    return "".join(output)


def _loads_tool_object(text: str) -> Optional[Dict[str, Any]]:
    """Parse a tool object with bounded syntax tolerance, never semantic guessing."""
    normalized = _normalize_json_separators(text)
    try:
        value = json.loads(normalized)
    except json.JSONDecodeError:
        # Safe fallback for common provider output using single quotes or a
        # trailing comma. literal_eval evaluates literals only, not code.
        try:
            import ast

            value = ast.literal_eval(normalized)
        except (SyntaxError, ValueError):
            return None
    return value if isinstance(value, dict) else None


def build_request_user_content(user_input: Any, current_time: str | None = None) -> Any:
    """Build the exact timestamped user content sent to the provider."""
    from datetime import datetime
    timestamp = current_time or datetime.now().strftime("%Y-%m-%d %H:%M")
    if isinstance(user_input, list):
        return [{"type": "text", "text": f"当前时间: {timestamp}\n\n"}] + list(user_input)
    return f"当前时间: {timestamp}\n\n{user_input}"


def _append_jsonl(path: str, entry: dict):
    """同步追加一行 JSON 到日志文件（由 asyncio.to_thread 调用）"""
    import json as _json
    from prompt_ledger import rotate_jsonl_if_needed
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rotate_jsonl_if_needed(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(entry, ensure_ascii=False) + "\n")


class LLMWrapper(ABC):
    """
    LLM包装器抽象基类

    核心职责：
    1. 工具注册（暴露NL schema）
    2. 系统提示词构建（强制格式输出）
    3. Provider特定的执行逻辑
    """

    def __init__(self) -> None:
        self.tools: Dict[str, Dict[str, Any]] = {}

    def _convert_schema_to_nl(self, schema: Dict[str, Any]) -> str:
        """将JSONSchema转换为自然语言描述"""
        nl_desc = []
        props = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])

        for name, details in props.items():
            desc = details.get("description", "无描述")
            type_info = details.get("type", "any")
            req_mark = "*" if name in required else ""
            default_val = details.get("default")
            enum_vals = details.get("enum")

            line = f"- {name} ({type_info}{req_mark}): {desc}"
            if enum_vals:
                line += f" [可选值: {', '.join(map(str, enum_vals))}]"
            if default_val is not None:
                line += f" (默认: {default_val})"
            nl_desc.append(line)

        return "\n".join(nl_desc)

    def register_tool(self, tool: Any) -> None:
        """注册工具"""
        schema = getattr(tool, 'input_schema', {}) or getattr(tool, 'parameters_schema', {}) or {}
        self.tools[tool.name] = {
            "tool": tool,
            "description": tool.description,
            "schema_nl": self._convert_schema_to_nl(schema),
            "schema_raw": schema,
        }

    def register_tools(self, tools: List[Any]) -> None:
        """批量注册工具"""
        for tool in tools:
            self.register_tool(tool)

    def _create_system_prompt(self) -> str:
        """构建系统提示词：工具可用性描述 + 强制规则（行为约束）。

        AI 的使用说明只读注册工具 pro_guide，内部参数合同不在这里展开。
        """
        tools_block = build_stable_tool_guidance(self.tools)

        # 注意：当前时间不放在系统提示词中（避免破坏 DeepSeek prompt caching），
        # 而是附加在 execute() 的 user_input 末尾。

        return f"""## 可用工具

下面是你实际拥有的工具。按想完成的事选择工具；需要外部行动或新证据时调用，不能把想象当作已经执行的结果。

{tools_block}

## 工具调用规则

1. 需要实时数据时使用目录中的相应工具；天气等已由感知上下文提供的事实以其来源和时效为准，不凭记忆编造
2. 文件操作仅在可用工具目录明确提供相应能力时执行；否则说明当前没有该能力
3. 理解人类伙伴的实际意图：请求执行外部操作时调用对应工具；聊起、引用或讨论这些动作本身不触发执行
4. 先确认已有材料是否足够；仍有需要确认的问题时选择能回答该问题的工具，能力不足或目的不清时据实说明
5. 工具记录或哨兵材料只说明一次执行或观察，不自动保证内容正确、完整或仍然有效；直接测量、人类伙伴陈述、模型分析和历史摘要必须保留来源区别
6. 已有材料与本轮相关、未过期且足够时直接使用，不重复调用；失败、冲突或证据不足时据实说明，再判断是否需要补救
7. 需要工具时，在回复末尾另起一个动作块，以 `@工具名` 开始，以 `@` 结束。两个 @ 之间写完整自然语言动作；可以单行，也可以在块内分行列出内容。无补充动作可写 `@工具名@`
8. 多个工具或连续操作各写一个闭合动作块，顺序就是执行顺序。
9. 工具名必须是上述列表中存在的工具；动作内容直接说清目的、对象、时间和必要细节，不写 JSON、参数名或 key=value
10. 你可以根据自己的关注主动观察、查阅、创作、表达和安排后续行动；感官包括：eyes 看本人和环境，check_phone 看手机屏幕和状态，take_screenshot 看电脑屏幕，band 看健康；schedule_self_task 可安排未来查岗。根据想确认的问题及新鲜证据自行选择，不需要等人类伙伴开口要求。

"""

    def extract_natural_language(self, content: str) -> str:
        """从LLM输出中提取自然语言部分"""
        content = _strip_affect_sidecar_text(content)
        # 格式: NATURAL_LANGUAGE:\n[内容]\n\nTOOL_CALL:...
        # 也兼容 "NATURAL LANGUAGE:" (空格) 的变体
        import re
        marker_pattern = r'NATURAL[_\s]LANGUAGE\s*:\s*\n?'
        match = re.search(marker_pattern, content)
        if match:
            try:
                # 提取 marker 之后的内容
                nl_start = match.end()
                remaining = content[nl_start:].strip()

                # 查找 TOOL_CALL 标记（支持有/无冒号），如果存在则截取之前的内容
                tool_marker = re.search(r'\n\s*TOOL_CALL\s*[:：\n]', remaining)
                if tool_marker:
                    natural_language = remaining[:tool_marker.start()].strip()
                else:
                    natural_language = remaining.strip()

                from tool_action_protocol import strip_tool_protocol_text
                return strip_tool_protocol_text(natural_language, tuple(self.tools))
            except Exception:
                pass

        # 兼容旧格式：如果没有 NATURAL_LANGUAGE: 标记，使用 extract_reasoning 逻辑
        return self.extract_reasoning(content)

    def extract_tool_actions(self, content: str) -> List[Dict[str, Any]]:
        """Parse AI's closed natural-language action lines.

        Parameter compilation deliberately happens in the scheduler, where the
        registered BaseTool schemas and the existing Flash extractor live.
        """
        content = _strip_affect_sidecar_text(content)
        from tool_action_protocol import extract_tool_actions
        return [
            {
                "tool": item.tool,
                "instruction": item.instruction,
                "closed": item.closed,
            }
            for item in extract_tool_actions(
                content,
                tuple(name for name, info in self.tools.items()
                      if getattr(info.get("tool"), "visible_to_pro", True)),
            )
        ]

    def extract_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """从LLM输出中提取所有工具调用（支持多个 TOOL_CALL）"""
        content = _strip_affect_sidecar_text(content)
        content = _normalize_json_separators(content)
        def _extract_json(text: str) -> Optional[Dict[str, Any]]:
            """从文本中提取第一个完整的 JSON 对象"""
            start = text.find("{")
            if start == -1:
                return None
            brace_count = 0
            in_string = False
            escape = False
            for i, ch in enumerate(text[start:], start):
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_string = not in_string
                    continue
                if not in_string:
                    if ch == "{":
                        brace_count += 1
                    elif ch == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            try:
                                return _loads_tool_object(text[start:i + 1])
                            except (TypeError, ValueError):
                                return None
            return None

        def _normalize_tool_call(raw: dict) -> dict:
            tc = dict(raw)
            if "tool_name" in tc and "tool" not in tc:
                tc["tool"] = tc.pop("tool_name")
            if "arguments" in tc and "parameters" not in tc:
                tc["parameters"] = tc.pop("arguments")
            return tc

        results = []

        # 格式1: 多个 TOOL_CALL: 块（"TOOL_CALL:"后接JSON，允许多个）
        remaining = content
        for marker in ["TOOL_CALL:", "TOOL_CALL\n", "TOOL_CALL "]:
            while marker in remaining:
                idx = remaining.find(marker)
                after_marker = remaining[idx + len(marker):].strip()
                for fence in ["```json", "```"]:
                    if after_marker.startswith(fence):
                        after_marker = after_marker[len(fence):].strip()
                if after_marker.endswith("```"):
                    after_marker = after_marker[:-3].strip()
                result = _extract_json(after_marker)
                if result:
                    results.append(_normalize_tool_call(result))
                    # 跳过已解析的部分，继续查找下一个
                    json_end = after_marker.find("}") + 1
                    remaining = after_marker[json_end:]
                else:
                    break
            if results:
                break  # 只用第一个匹配的 marker 格式

        # 格式2: [TOOL_CALL]...[/TOOL_CALL] (兼容旧格式)
        if not results:
            pattern = r'\[TOOL_CALL\]\s*(\{.*?\})\s*\[/TOOL_CALL\]'
            for match in re.finditer(pattern, content, re.DOTALL):
                try:
                    parsed = _loads_tool_object(match.group(1))
                    if parsed:
                        results.append(_normalize_tool_call(parsed))
                except (TypeError, ValueError):
                    pass

        # 格式3: 兜底 — 从全文提取第一个 JSON
        if not results:
            result = _extract_json(content)
            if result and ("tool" in result or "name" in result or "tool_name" in result):
                results.append(_normalize_tool_call(result))

        return results

    def extract_tool_call(self, content: str) -> Optional[Dict[str, Any]]:
        """从LLM输出中提取工具调用

        支持多种格式：
        - TOOL_CALL: 后接 JSON（带冒号）
        - TOOL_CALL\n 后接 JSON（无冒号，换行）
        - [TOOL_CALL]...[/TOOL_CALL] 旧格式
        - 纯 JSON 对象（兜底）
        """
        content = _strip_affect_sidecar_text(content)
        content = _normalize_json_separators(content)

        def _extract_json(text: str) -> Optional[Dict[str, Any]]:
            """从文本中提取第一个完整的 JSON 对象"""
            start = text.find("{")
            if start == -1:
                return None
            brace_count = 0
            in_string = False
            escape = False
            for i, ch in enumerate(text[start:], start):
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_string = not in_string
                    continue
                if not in_string:
                    if ch == "{":
                        brace_count += 1
                    elif ch == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            try:
                                return _loads_tool_object(text[start:i + 1])
                            except (TypeError, ValueError):
                                return None
            return None

        def _normalize_tool_call(raw: dict) -> dict:
            """归一化 tool_name→tool, arguments→parameters"""
            tc = dict(raw)
            if "tool_name" in tc and "tool" not in tc:
                tc["tool"] = tc.pop("tool_name")
            if "arguments" in tc and "parameters" not in tc:
                tc["parameters"] = tc.pop("arguments")
            return tc

        # 格式1: TOOL_CALL 后接 JSON（支持冒号、空格、换行等分隔符）
        for marker in ["TOOL_CALL:", "TOOL_CALL\n", "TOOL_CALL "]:
            if marker in content:
                after_marker = content.split(marker, 1)[1].strip()
                # 去掉可能的代码块标记
                for fence in ["```json", "```"]:
                    if after_marker.startswith(fence):
                        after_marker = after_marker[len(fence):].strip()
                if after_marker.endswith("```"):
                    after_marker = after_marker[:-3].strip()
                result = _extract_json(after_marker)
                if result:
                    return _normalize_tool_call(result)

        # 格式2: [TOOL_CALL]...[/TOOL_CALL] (兼容旧格式)
        pattern = r'\[TOOL_CALL\]\s*(\{.*?\})\s*\[/TOOL_CALL\]'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            try:
                parsed = _loads_tool_object(match.group(1))
                return _normalize_tool_call(parsed) if parsed else None
            except (TypeError, ValueError):
                pass

        # 格式3: 兜底 — 从全文提取第一个 JSON（模型有时直接输出 JSON 不加标记）
        result = _extract_json(content)
        if result and ("tool" in result or "name" in result or "tool_name" in result):
            return _normalize_tool_call(result)

        return None

    def extract_reasoning(self, content: str) -> str:
        """提取推理部分（工具调用之前的自然语言）"""
        content = _strip_affect_sidecar_text(content)
        # 先尝试提取 NATURAL_LANGUAGE: 之后的内容
        nl_pattern = r'NATURAL[_\s]LANGUAGE\s*:\s*\n?'
        nl_match = re.search(nl_pattern, content)
        if nl_match:
            content = content[nl_match.end():].strip()

        for marker in ["TOOL_CALL:", "TOOL_CALL\n", "TOOL_CALL "]:
            if marker in content:
                idx = content.find(marker)
                return content[:idx].strip()

        pattern = r'\[TOOL_CALL\]'
        match = re.search(pattern, content)
        if match:
            return content[:match.start()].strip()

        from tool_action_protocol import strip_tool_protocol_text
        return strip_tool_protocol_text(content, tuple(self.tools))

    @abstractmethod
    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict] = None,
        system_prompt: str = None,
        append_user_input: bool = True,
    ) -> Dict[str, Any]:
        """执行LLM调用"""
        raise NotImplementedError("子类必须实现execute()")


class OpenAICompatibleWrapper(LLMWrapper):
    """
    OpenAI兼容的LLM包装器

    支持 DeepSeek、GLM、Ollama 等OpenAI兼容API
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__()

        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("GLM_API_KEY") or "none"
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or os.getenv("GLM_BASE_URL")
        self.model = model or os.getenv("OPENAI_MODEL") or os.getenv("DEEPSEEK_MODEL") or os.getenv("GLM_MODEL") or "deepseek-v4-pro"

        self.temperature = kwargs.get("temperature", 0.7)
        self.max_tokens = kwargs.get("max_tokens", 4096)
        # Internal Flash calls are deliberately short.  A Flash-class model
        # selected as AI's main conversational brain must keep the full reply
        # budget instead of being silently clipped to 1024 tokens.
        self.compact_flash_responses = kwargs.get("compact_flash_responses", True)

        # DeepSeek V4 thinking模式配置
        self.thinking_enabled = kwargs.get("thinking_enabled", os.getenv("DEEPSEEK_THINKING_ENABLED", "true").lower() == "true")
        self.reasoning_effort = kwargs.get("reasoning_effort", os.getenv("DEEPSEEK_REASONING_EFFORT", "medium"))

        self._client = None

    def _get_client(self):
        """延迟初始化OpenAI client"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                import httpx
                self._client = AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=httpx.Timeout(90.0, connect=10.0),
                    max_retries=1,
                    http_client=httpx.AsyncClient(
                        timeout=httpx.Timeout(90.0, connect=10.0),
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                        proxy=None,
                        trust_env=False,
                    ),
                )
            except ImportError:
                raise ImportError("请安装openai: pip install openai")
        return self._client

    async def execute(
        self,
        user_input: str,
        conversation_history: List[Dict] = None,
        system_prompt: str = None,
        model: str = None,
        api_key: str = None,
        response_format: Dict[str, Any] = None,
        vision_expected_keys: List[str] = None,
        append_user_input: bool = True,
    ) -> Dict[str, Any]:
        """执行LLM调用

        Args:
            user_input: 用户输入。可为 str，或 OpenAI 多模态 content list
                （[{"type":"image_url","image_url":{...}}, {"type":"text","text":...}]，用于 flash-vision 带图轮次）
            model: 按次模型覆盖（如图片轮次覆盖为 flash-vision），None 时用 self.model
            api_key: 按次 API key 覆盖（视觉轮 flash-vision 用 flash key，保证计费分离），None 时用 self.api_key
            append_user_input: 当前输入尚未按正确时序存在于 history 时才追加。
                工具后续轮由调度器将本轮 user 固定在首轮 assistant 之前，因此必须关闭。
        """
        import logging
        logger = logging.getLogger(__name__)
        actual_model = model or self.model
        inspection_handle = None
        _override_client = None
        logger.info(f"OpenAICompatibleWrapper.execute() using model: {actual_model}")
        try:
            client = self._get_client()
            _override_client = None
            if api_key and api_key != self.api_key:
                # 模型/key 覆盖（视觉轮 flash-vision 用 flash key）→ 临时 client，用完即关
                try:
                    from openai import AsyncOpenAI
                    import httpx as _hx
                    _override_client = AsyncOpenAI(
                        api_key=api_key,
                        base_url=self.base_url,
                        timeout=_hx.Timeout(90.0, connect=10.0),
                        max_retries=1,
                        http_client=_hx.AsyncClient(
                            timeout=_hx.Timeout(90.0, connect=10.0),
                            limits=_hx.Limits(max_connections=20, max_keepalive_connections=5),
                            proxy=None, trust_env=False,
                        ),
                    )
                    client = _override_client
                except ImportError:
                    pass  # 走默认 client（key 不正确时调用会失败，由错误处理兜底）

            base_system = self._create_system_prompt()
            if system_prompt:
                full_system = f"{system_prompt}\n\n{base_system}"
            else:
                full_system = base_system

            from context_builder.channel_projection import copy_messages
            history = conversation_history or []
            history_messages = copy_messages(history)
            source_kinds = ["context_bundle"] + [
                msg.get("_material_kind", "context_bundle") for msg in history
            ]

            request_user_content = None
            turn_messages = []
            if append_user_input:
                # 将当前时间附加到用户消息（不放在 system prompt 中以避免破坏缓存）
                request_user_content = build_request_user_content(user_input)
                turn_messages.append({"role": "user", "content": request_user_content})

            messages = assemble_provider_messages(
                system_content=full_system,
                history=history_messages,
                turn=turn_messages,
            )
            from .pro_request_adapter import prepare_pro_request
            from api_sources.model_context import with_current_model
            baseline_messages = with_current_model(messages, actual_model)
            source_kinds.append('runtime_model')
            messages, request_projection = prepare_pro_request(
                baseline_messages, model=actual_model,
                base_url=str(getattr(client, "base_url", None) or self.base_url or ""),
                source_kinds=source_kinds,
                allow_compatible=bool(getattr(self, "_routing_materials", False)),
            )
            if request_projection.get('reason') in {'other_provider_or_model', 'unsupported_shape'}:
                from context_builder.channel_projection import prepare
                messages, request_projection = prepare(baseline_messages)
            logger.info("Provider context variant=%s, projected_systems=%s",
                        request_projection["variant"], request_projection.get("projected_system_count", 0))

            # 构建请求参数
            # Flash 模型不需要高 max_tokens（响应通常很短）
            is_flash = "flash" in actual_model.lower()
            actual_max_tokens = (
                min(self.max_tokens, 1024)
                if is_flash and self.compact_flash_responses
                else self.max_tokens
            )
            if response_format and is_flash:
                # A multi-image envelope contains the visible reply plus one
                # objective report per source; 1024 can truncate valid JSON.
                actual_max_tokens = min(self.max_tokens, 4096)
            request_params = {
                "model": actual_model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": actual_max_tokens,
            }
            if response_format:
                request_params["response_format"] = response_format

            # DeepSeek V4 thinking模式：
            # - Flash 模型用 "low"（够推理出工具调用，但不拖慢响应）
            # - Pro/非Flash 模型用配置的 reasoning_effort（默认 "medium"）
            is_deepseek_v4 = "deepseek" in actual_model.lower() and ("v4" in actual_model.lower() or "pro" in actual_model.lower())
            is_flash_model = "flash" in actual_model.lower()
            if is_deepseek_v4 and self.thinking_enabled:
                if is_flash_model:
                    request_params["reasoning_effort"] = "medium"
                    request_params["extra_body"] = {"thinking": {"type": "enabled"}}
                    logger.info("DeepSeek Flash thinking模式: reasoning_effort=medium")
                else:
                    request_params["reasoning_effort"] = self.reasoning_effort
                    request_params["extra_body"] = {"thinking": {"type": "enabled"}}
                    logger.info(f"DeepSeek thinking模式已启用: reasoning_effort={self.reasoning_effort}")
            if getattr(self, "_routing_profile", "") == "siliconflow-deepseek" and self.thinking_enabled:
                request_params["extra_body"] = {"enable_thinking": True}
                request_params["reasoning_effort"] = "high"

            # ── 调度器上下文日志（仅 Pro 模型，不记 Flash）──
            if not is_flash_model:
                try:
                    from datetime import datetime as _dt
                    import hashlib as _hl
                    _sys_len = len(full_system)
                    # 诊断：完整 messages 的 hash + 每条消息首尾预览（定位缓存前缀在何处断裂）
                    _msgs_hash = _hl.md5(
                        "|||".join(f"{m.get('role')}:{str(m.get('content'))[:200]}" for m in messages).encode()
                    ).hexdigest()[:12]
                    _msgs_preview = [
                        {"i": i, "role": m.get("role"), "c": str(m.get("content"))[:60]}
                        for i, m in enumerate(messages)
                    ]
                    _log = {
                        "timestamp": _dt.now().isoformat(),
                        "model": actual_model,
                        "message_count": len(messages),
                        "system_prompt_preview": full_system[:3000],
                        "system_prompt_tail": full_system[-2000:] if _sys_len > 5000 else "",
                        "total_system_chars": _sys_len,
                        "messages_hash": _msgs_hash,
                        "messages_preview": _msgs_preview,
                        "thinking_enabled": is_deepseek_v4 and self.thinking_enabled,
                        "source": "behavior_scheduler",
                    }
                    _log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
                    os.makedirs(_log_dir, exist_ok=True)
                    await asyncio.to_thread(_append_jsonl, os.path.join(_log_dir, "llm_context_log.jsonl"), _log)
                except Exception:
                    pass  # 日志失败不应影响主流程

            from context_inspection import observe_provider, finish_provider
            import uuid as _uuid
            provider_request_id = _uuid.uuid4().hex
            from datetime import datetime as _provider_dt
            provider_started_at = _provider_dt.now().astimezone().isoformat()
            inspection_handle = observe_provider(
                messages, model=actual_model, provider='behavior_scheduler',
                source_messages=baseline_messages, request_projection=request_projection,
                request_id=provider_request_id,
            )
            provider_started = time.perf_counter()
            from api_sources.sdk_audit import call_sdk
            response = await call_sdk(client, request_params, api_url=self.base_url,
                api_key=api_key or self.api_key)
            finish_provider(inspection_handle, 'response_received')

            message = response.choices[0].message
            content = message.content or ""
            provider_refusal = bool(getattr(message, "refusal", None)) or getattr(response.choices[0], "finish_reason", "") in {"content_filter", "refusal"}

            vision_envelope = None
            if response_format:
                from .vision_envelope import parse_vision_envelope
                vision_envelope = parse_vision_envelope(content, vision_expected_keys or [])
                if vision_envelope is None:
                    raise ValueError("视觉模型未返回有效的结构化响应")
                if not vision_envelope.get("natural_language") and not vision_envelope.get("tool_calls"):
                    raise ValueError("视觉模型未返回可见自然语言")

            # DeepSeek V4 thinking模式：reasoning_content 字段
            # 使用多种方式提取，兼容不同版本 OpenAI SDK 的 Pydantic 行为
            reasoning_content = None
            # 方法1：直接属性访问（适用于 Pydantic extra="allow"）
            try:
                rc = getattr(message, 'reasoning_content', None)
                if rc:
                    reasoning_content = rc
            except Exception:
                pass
            # 方法2：model_extra 字典（Pydantic v2 extra 字段存储）
            if not reasoning_content:
                try:
                    me = getattr(message, 'model_extra', None)
                    if me and isinstance(me, dict):
                        reasoning_content = me.get('reasoning_content')
                except Exception:
                    pass
            # 方法3：model_dump 全量导出
            if not reasoning_content:
                try:
                    dumped = message.model_dump()
                    reasoning_content = dumped.get('reasoning_content')
                except Exception:
                    pass
            # 方法4：从 raw_response 直接解析 JSON（终极兜底）
            if not reasoning_content:
                try:
                    raw = response.model_dump()
                    rc = raw.get('choices', [{}])[0].get('message', {}).get('reasoning_content')
                    if rc:
                        reasoning_content = rc
                except Exception:
                    pass

            logger.info(f"Model response: model={actual_model}, has_reasoning_content={reasoning_content is not None}, reasoning_len={len(reasoning_content) if reasoning_content else 0}")

            tool_call = self.extract_tool_call(content)
            tool_calls = self.extract_tool_calls(content)
            action_directives = self.extract_tool_actions(content)

            # 推理链处理：
            # DeepSeek V4 thinking模式返回:
            #   - reasoning_content: 推理过程（思维链）
            #   - content: 最终输出（可能包含 TOOL_CALL）
            #
            # 对于前端显示:
            #   - reasoning: 推理过程（显示在折叠的思维链中）
            #   - content: 最终输出（显示在消息气泡中）
            #
            # 如果没有 reasoning_content（非 deepseek-v4-pro 模型）:
            #   - 从 content 中提取 TOOL_CALL 之前的自然语言部分作为推理
            #   - 但如果没有工具调用，则推理为空（避免重复）
            if reasoning_content:
                # DeepSeek V4 thinking模式: reasoning_content 是推理，content 是最终输出
                reasoning = reasoning_content
            else:
                # 其他模型: 从 content 中提取工具调用前的部分
                reasoning = self.extract_reasoning(content)
                # 如果没有工具调用，避免推理和内容重复
                if tool_call is None and not action_directives:
                    reasoning = ""

            response_usage = response.usage if hasattr(response, 'usage') else None
            if inspection_handle:
                from prompt_ledger import update_prompt_usage
                update_prompt_usage(
                    inspection_handle[0], response_usage,
                    model=actual_model, provider="behavior_scheduler",
                    billing_provider=getattr(self, "_billing_provider", "deepseek" if actual_model.lower().startswith("deepseek") else "unknown"),
                    latency_ms=(time.perf_counter() - provider_started) * 1000,
                    status="completed", messages=messages,
                    request_options={
                        key: value for key, value in request_params.items()
                        if key != "messages"
                    },
                    provider_request_id=provider_request_id,
                    provider_started_at=provider_started_at,
                    request_projection=request_projection,
                )
            asyncio.create_task(log_token_usage(
                "agent_tools", actual_model, response_usage,
                provider=getattr(self, "_billing_provider", "deepseek" if actual_model.lower().startswith("deepseek") else "unknown"),
                provider_variant=request_projection["variant"], request_id=provider_request_id,
            ))
            return {
                "content": content,
                "refusal": provider_refusal,
                "reasoning": reasoning,
                "tool_call": tool_call,
                "tool_calls": tool_calls,
                "action_directives": action_directives,
                "vision_envelope": vision_envelope,
                # 调度器用真实发送内容固定本轮 user 的位置，避免二轮重新追加到工具结果之后。
                "request_user_content": request_user_content,
                "raw_response": response
            }

        except Exception as e:
            if inspection_handle:
                from context_inspection import finish_provider
                finish_provider(inspection_handle, 'provider_error', type(e).__name__)
            return {
                "content": "",
                "reasoning": "",
                "tool_call": None,
                "error": str(e),
                "refusal": bool(getattr(e, "provider_refusal", False)),
                "request_user_content": None,
                "raw_response": None
            }
        finally:
            if inspection_handle:
                from context_inspection import finish_provider
                finish_provider(inspection_handle, 'finished_without_response')
            if _override_client:
                try:
                    await _override_client.aclose()
                except Exception:
                    pass


class DeepSeekWrapper(OpenAICompatibleWrapper):
    """DeepSeek专用包装器（支持V4 thinking模式）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs
    ) -> None:
        actual_model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"DeepSeekWrapper initialized with model: {actual_model}, thinking_enabled: {kwargs.get('thinking_enabled', os.getenv('DEEPSEEK_THINKING_ENABLED', 'true'))}")
        if actual_model.lower() == "deepseek-v4-pro":
            logger.info("Pro context adapter configured: mode=%s", os.getenv("MIRROW_PRO_MATERIAL_MODE", "materials"))
        super().__init__(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=actual_model,
            **kwargs
        )


class GLMWrapper(OpenAICompatibleWrapper):
    """智谱GLM专用包装器"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("GLM_API_KEY"),
            base_url=os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            model=model or os.getenv("GLM_MODEL", "glm-4"),
            **kwargs
        )
