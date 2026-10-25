"""
工具调用标准化层

在 agent_tools 流程中复用 command_layer 的 ParameterValidator 和 ParameterGenerator，
加一层 JSON key 归一化，确保 extract_tool_call() 提取的原始 JSON 在进入 CommandExecutor 前
经过完整的校验和补齐。

使用方式：
    standardizer = ToolCallStandardizer(tools_dict)
    result = standardizer.standardize({"tool": "get_weather", "parameters": {"city": "北京"}})
    if result.is_valid:
        # result.tool_name, result.parameters 可用
    else:
        # result.error_message 包含错误描述
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import logging
import re

from .command_layer import ParameterValidator, ParameterGenerator, CommandStatus

logger = logging.getLogger(__name__)


@dataclass
class StandardizedToolCall:
    """标准化后的工具调用结果"""
    tool_name: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = True
    error_message: str = ""


class ToolCallStandardizer:
    """
    工具调用标准化器

    负责三层处理：
    1. normalize_keys — 将 LLM 可能输出的不同 key 风格映射为标准格式
    2. fill_defaults  — 复用 ParameterGenerator 填充 schema 默认值
    3. validate       — 复用 ParameterValidator 校验 required/type/enum
    """

    # key 别名映射表：常见变体 → 标准 key
    KEY_ALIASES = {
        "name": "tool",
        "function": "tool",
        "input_schema": "parameters",
        "arguments": "parameters",
        "params": "parameters",
        "args": "parameters",
    }

    # Tool-local parameter aliases are intentionally narrow. They repair
    # common provider spelling drift without guessing missing semantic values.
    PARAMETER_ALIASES = {
        "send_voice": {
            "content": "text",
        },
        "bookshelf": {
            "q": "query",
            "keyword": "query",
            "search": "query",
        },
        "eyes": {
            # Historical prompts/providers used this name; EyesTool's current
            # public contract calls the same free-form focus analysis_prompt.
            "analysis_focus": "analysis_prompt",
        },
        "band_touch": {
            "content": "note",
            "message": "note",
            "text": "note",
        },
    }

    # Exact, tool-local semantic aliases observed from providers.  Keep this
    # list narrow: unlike unique-prefix repair, arbitrary fuzzy enum matching
    # can silently turn one real action into another.
    ENUM_VALUE_ALIASES = {
        "cloud_music": {
            "action": {
                "add_to_playlist": "playlist_add",
            },
        },
    }

    def __init__(self, tools: Dict[str, Any]):
        self._tools = tools
        self._generator = ParameterGenerator(tools)
        self._validator = ParameterValidator(tools)

    def update_tools(self, tools: Dict[str, Any]):
        """工具列表更新时同步内部状态"""
        self._tools = tools
        self._generator.update_tools(tools)
        self._validator.update_tools(tools)

    @staticmethod
    def _is_single_edit(left: str, right: str) -> bool:
        """Whether two protocol identifiers differ by exactly one edit."""
        if left == right or abs(len(left) - len(right)) > 1:
            return False
        if len(left) == len(right):
            return sum(a != b for a, b in zip(left, right)) == 1
        shorter, longer = (left, right) if len(left) < len(right) else (right, left)
        short_i = long_i = differences = 0
        while short_i < len(shorter) and long_i < len(longer):
            if shorter[short_i] == longer[long_i]:
                short_i += 1
                long_i += 1
                continue
            differences += 1
            long_i += 1
            if differences > 1:
                return False
        return True

    def _normalize_unique_enum_typos(
        self, tool_name: str, parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Repair unique case, 3+ character prefix, or one-edit enum drift."""
        tool = self._tools.get(tool_name)
        schema = getattr(tool, "parameters_schema", None) or getattr(tool, "input_schema", None) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        normalized = dict(parameters or {})
        identifier = re.compile(r"^[A-Za-z0-9_-]{3,}$")
        for param_name, value in list(normalized.items()):
            definition = properties.get(param_name, {})
            enum_values = definition.get("enum") if isinstance(definition, dict) else None
            if not isinstance(value, str) or not isinstance(enum_values, list) or value in enum_values:
                continue
            alias = (
                self.ENUM_VALUE_ALIASES
                .get(tool_name, {})
                .get(param_name, {})
                .get(value.lower())
            )
            if alias in enum_values:
                normalized[param_name] = alias
                logger.info(
                    "tool_standardizer: corrected enum alias %s.%s %r -> %r",
                    tool_name, param_name, value, alias,
                )
                continue
            if not identifier.fullmatch(value):
                continue
            ascii_options = [
                option for option in enum_values
                if isinstance(option, str) and identifier.fullmatch(option)
            ]
            case_matches = [option for option in ascii_options if option.lower() == value.lower()]
            prefix_matches = [
                option for option in ascii_options
                if len(value) >= 3 and option.lower().startswith(value.lower())
            ]
            candidates = case_matches or prefix_matches or [
                option for option in ascii_options
                if self._is_single_edit(value.lower(), option.lower())
            ]
            if len(candidates) != 1:
                continue
            corrected = candidates[0]
            normalized[param_name] = corrected
            logger.info(
                "tool_standardizer: corrected unique enum typo %s.%s %r -> %r",
                tool_name, param_name, value, corrected,
            )
        return normalized

    def _normalize_tool_name(self, value: Any) -> str:
        """Accept only an exact or uniquely determined 3+ character tool name."""
        name = str(value or "").strip()
        if name in self._tools:
            return name
        case_matches = [candidate for candidate in self._tools if candidate.lower() == name.lower()]
        if len(case_matches) == 1:
            return case_matches[0]
        if len(name) >= 3:
            prefix_matches = [
                candidate for candidate in self._tools
                if candidate.lower().startswith(name.lower())
            ]
            if len(prefix_matches) == 1:
                logger.info("tool_standardizer: expanded unique tool prefix %r -> %r", name, prefix_matches[0])
                return prefix_matches[0]
        return name

    def _unknown_parameters(self, tool_name: str, parameters: Dict[str, Any]) -> list[str]:
        """Unknown model fields fail closed when a tool publishes a schema."""
        tool = self._tools.get(tool_name)
        schema = getattr(tool, "parameters_schema", None) or getattr(tool, "input_schema", None) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if not properties:
            return []
        return sorted(str(key) for key in set(parameters) - set(properties))

    # ----- 内部归一化方法 -----

    def normalize_keys(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 LLM 输出的 key 变体映射为标准格式。

        支持的输入格式举例：
            {"tool": "xxx", "parameters": {...}}         — 标准格式
            {"name": "xxx", "arguments": {...}}           — OpenAI function calling 风格
            {"tool": "xxx", "input_schema": {...}}        — agent_tools 内部格式
            {"function": "xxx", "params": {...}}          — 其他常见变体
        """
        if not isinstance(raw, dict):
            logger.warning("tool_standardizer: raw is not a dict (%s)", type(raw).__name__)
            return {"tool": "", "parameters": {}}

        normalized = {}
        for key, value in raw.items():
            std_key = self.KEY_ALIASES.get(key, key)
            # 如果同一个标准 key 出现多次，后面的覆盖前面的
            normalized[std_key] = value

        return normalized

    # ----- 委托给 command_layer -----

    def fill_defaults(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """填充 schema 默认值"""
        return self._generator.generate(tool_name, parameters)

    def normalize_parameters(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize only explicitly supported aliases for one tool contract."""
        normalized = dict(parameters or {})
        # Older prompts told AI to supply this internal discriminator.  Accept
        # only its correct value for this one tool; the tool itself remains
        # authoritative for the scheduled task type.
        if tool_name == "schedule_self_task" and normalized.get("task_type") == "self_task":
            normalized.pop("task_type")
        for alias, canonical in self.PARAMETER_ALIASES.get(tool_name, {}).items():
            if canonical not in normalized and alias in normalized:
                normalized[canonical] = normalized[alias]
            if alias != canonical:
                normalized.pop(alias, None)
        if tool_name == "band_touch":
            note = normalized.get("note")
            if isinstance(note, str):
                note = note.strip()
                if note:
                    normalized["note"] = note
                else:
                    normalized.pop("note", None)
            # note 是唯一语义开关；旧 action 输入仍接受，但不能与正文互相矛盾。
            normalized["action"] = "note" if "note" in normalized else "vibrate"
        return normalized

    def validate(self, tool_name: str, parameters: Dict[str, Any]) -> "ValidationResult":
        """参数校验（required / type / enum）"""
        return self._validator.validate(tool_name, parameters)

    # ----- 全流程标准化 -----

    def standardize(self, raw_tool_call: Optional[Dict[str, Any]]) -> StandardizedToolCall:
        """
        全流程标准化：normalize → fill_defaults → validate

        Args:
            raw_tool_call: extract_tool_call() 返回的原始 dict，或 None

        Returns:
            StandardizedToolCall
        """
        if not raw_tool_call:
            return StandardizedToolCall(
                is_valid=False,
                error_message="工具调用为空"
            )

        # 1. key 归一化
        normalized = self.normalize_keys(raw_tool_call)
        tool_name = self._normalize_tool_name(normalized.get("tool", ""))
        parameters = normalized.get("parameters", {})

        if not tool_name:
            return StandardizedToolCall(
                is_valid=False,
                error_message="工具调用中缺少 tool/name 字段"
            )

        tool = self._tools.get(tool_name)
        if tool is not None and not getattr(tool, "visible_to_pro", True):
            return StandardizedToolCall(
                tool_name=tool_name,
                is_valid=False,
                error_message=f"工具不可由对话调用: {tool_name}",
            )

        if not isinstance(parameters, dict):
            return StandardizedToolCall(
                tool_name=tool_name,
                parameters={},
                is_valid=False,
                error_message="工具 parameters/arguments 必须是对象",
            )

        # 2. 工具局部参数别名归一化，再填充默认值
        parameters = self.normalize_parameters(tool_name, parameters)
        unknown = self._unknown_parameters(tool_name, parameters)
        if unknown:
            return StandardizedToolCall(
                tool_name=tool_name,
                parameters=parameters,
                is_valid=False,
                error_message=f"未知参数: {', '.join(unknown)}",
            )
        parameters = self.fill_defaults(tool_name, parameters)

        # 2.5. Provider output can truncate a short protocol identifier by one
        # character (e.g. hol -> hold). Repair only a unique one-edit ASCII
        # enum match; ambiguity and semantic substitutions still fail closed.
        parameters = self._normalize_unique_enum_typos(tool_name, parameters)

        # 3. 参数校验
        validation = self.validate(tool_name, parameters)
        if not validation.is_valid:
            return StandardizedToolCall(
                tool_name=tool_name,
                parameters=parameters,
                is_valid=False,
                error_message=validation.error_message + (
                    '。参数用法：' + getattr(self._tools.get(tool_name), 'parameter_help', '')
                    if getattr(self._tools.get(tool_name), 'parameter_help', '') else ''
                ),
            )

        return StandardizedToolCall(
            tool_name=tool_name,
            parameters=validation.parameters,
            is_valid=True,
        )
