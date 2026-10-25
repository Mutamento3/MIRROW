"""Frozen receipt text for durable Memory V2 provenance (tool-source-v2).

This is not the provider presentation formatter. Keep bytes compatible with
existing batch/quote manifests; a wording change needs an explicit new source
version and cohort, not an import from mutable display helpers.
"""
from __future__ import annotations
import json
import re
from typing import Any, Mapping
TOOL_SOURCE_VERSION = 'tool-source-v2'
_SECRET_VALUE_RE = re.compile('(?i)\\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)\\s*[:=]\\s*([^\\s,;]+)')
_BEARER_RE = re.compile('(?i)\\bBearer\\s+[A-Za-z0-9._~+/=-]+')
_IPV4_RE = re.compile('(?<!\\d)(?:\\d{1,3}\\.){3}\\d{1,3}(?!\\d)')
_DATA_URL_RE = re.compile('data:[^;,\\s]+;base64,[A-Za-z0-9+/=]+', re.IGNORECASE)
_NON_CONTEXT_TOOLS = {'shell', 'http_request', 'file', 'advanced_file', 'code_runner', 'package_manager', 'documentation_check'}
_TOOL_LABELS = {'xiaohongshu_interactions': '查看或回复小红书评论与艾特', 'send_voice': '生成语音消息', 'web_search': '检索公开资料', 'get_weather': '查询天气', 'check_phone': '查看手机状态', 'eyes': '进行视觉观察', 'band': '读取健康数据', 'cloud_music': '操作音乐播放', 'manage_calendar': '处理日历事项', 'manage_ledger': '处理账本事项', 'create_scheduled_task': '安排提醒', 'manage_scheduled_task': '处理提醒事项', 'rift_read_archive': '翻阅虚构案件目录', 'rift_start_case': '开始一宗虚构互动案件', 'rift_get_state': '核对虚构案件局面', 'rift_take_action': '在虚构互动案件中执行一步行动'}

def _redact_tool_fact(value: Any) -> str:
    """把工具结果压成可注入的单行事实，并遮蔽常见密钥/IP/大字段。"""
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value or '')
    text = _DATA_URL_RE.sub('[图片数据已省略]', text)
    text = _BEARER_RE.sub('Bearer [已隐藏]', text)
    text = _SECRET_VALUE_RE.sub(lambda m: f'{m.group(1)}=[已隐藏]', text)
    text = _IPV4_RE.sub('[IP已隐藏]', text)
    return re.sub('\\s+', ' ', text).strip()

def _tool_fact_status(call: dict) -> str:
    """兼容同步工具与异步附件的语义状态。"""
    extra = call.get('extra_data') or {}
    attachment = extra.get('attachment_status') if isinstance(extra, dict) else ''
    raw = str(attachment or call.get('status') or '').lower()
    if raw in {'pending', 'processing', 'running'}:
        return '进行中'
    if raw in {'ready', 'success', 'completed', 'complete'}:
        return '成功'
    if raw in {'failed', 'error', 'cancelled'}:
        return '失败'
    if call.get('success') is True:
        return '成功'
    if call.get('success') is False:
        return '失败'
    return '已完成'
_RETRIEVAL_TOOLS = {'bookshelf', 'web_search', 'fetch_source_details'}
_SENSORY_TOOLS = {'eyes', 'check_phone'}
_INDEPENDENT_EVENT_TOOLS = {'ambient_event_link'}
_BINARY_RUN = re.compile('(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}(?![A-Za-z0-9+/])')
_QUOTED_SECRET = re.compile('(?i)(["\\\'](?:api[_-]?key|access[_-]?token|token|secret|password|authorization|cookie)["\\\']\\s*:\\s*["\\\'])[^"\\\']+')

def _safe_text(value: Any) -> str:
    """Retain textual results while excluding common inline binary payloads."""
    text = _redact_tool_fact(value)
    text = _QUOTED_SECRET.sub('\\1[已隐藏]', text)
    return _BINARY_RUN.sub('[二进制数据已省略]', text)

def _query_subject(params: Any) -> str:
    if not isinstance(params, Mapping):
        return ''
    for key in ('query', 'search_query', 'keyword', 'keywords', 'topic', 'source_id'):
        value = params.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return _safe_text(value)
    return ''

def project_tool_event(receipt: Mapping[str, Any] | Any) -> str:
    """Return one source-labelled fact, or empty for separately-owned events."""
    if not isinstance(receipt, Mapping):
        return ''
    name = str(receipt.get('tool') or receipt.get('name') or '').strip()
    if not name or name in _NON_CONTEXT_TOOLS or name in _INDEPENDENT_EVENT_TOOLS:
        return ''
    extra = receipt.get('extra_data') if isinstance(receipt.get('extra_data'), Mapping) else {}
    if name == 'xiaohongshu_interactions' and extra.get('xhs_interactions'):
        if extra['xhs_interactions'].get('context_version') == 2:
            from xhs_interactions.context import brief
            return brief(extra['xhs_interactions'])
        from xhs_interactions.projection import project
        return project(extra['xhs_interactions'])
    if name == 'publish_xiaohongshu' and extra.get('xhs_publication'):
        from xhs_publish.service import experience
        return experience(extra['xhs_publication'])
    if name == 'home_board':
        if extra.get('home_board_committed'):
            return ''
        return f'[工具事件] AI 查看本家公开留言板。结果：{_tool_fact_status(dict(receipt))}。原帖保留在阅读回执中。'
    if name == 'band_touch' and extra.get('band_notice_id'):
        return ''
    status = _tool_fact_status(dict(receipt))
    if name == 'send_voice':
        return f'[语音事件] AI发送了一段语音。结果：{status}。'
    label = _TOOL_LABELS.get(name, '执行一项操作')
    if name in _RETRIEVAL_TOOLS:
        subject = _query_subject(receipt.get('parameters'))
        description = _safe_text(receipt.get('description'))
        what = subject or description or '资料'
        retrieval_label = {'bookshelf': '翻阅书柜', 'web_search': '检索公开资料', 'fetch_source_details': '读取资料原文'}[name]
        return f'[工具事件] AI{retrieval_label}，查找：{what}。结果：{status}。'
    description = _safe_text(receipt.get('description'))
    result = _safe_text(receipt.get('result'))
    error = _safe_text(receipt.get('error'))
    if name in _SENSORY_TOOLS:
        detail = description or error or result
        return f'[感知事件] AI{label}。结果：{status}。' + (f'{detail}' if detail else '')
    parts = [f'[工具事件] AI{label}。结果：{status}。']
    if description:
        parts.append(f'行动：{description}。')
    if result:
        parts.append(f'回执：{result}。')
    if error and error != result:
        parts.append(f'错误：{error}。')
    return ''.join(parts)
