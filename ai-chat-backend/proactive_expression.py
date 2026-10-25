"""Shared, one-shot expression attachments for Sentinel and Wander.

Models choose voice and song-sharing actions in their existing response. Pending
receipts belong to the same authoritative message as its visible text.
"""
from tool_action_protocol import extract_tool_actions, strip_tool_protocol_text
from behavior_scheduler.attachment_delivery import AttachmentDeliveryGate
EXPRESSION_TOOLS = ('send_voice', 'cloud_music')
_ALL_TOOLS = ('band_touch', 'schedule_checkup', *EXPRESSION_TOOLS)

def stored_content(message, calls):
    """Use the ordinary chat voice transcript contract, without UI duplication."""
    from tool_call_utils import apply_voice_marker, build_voice_marker
    return apply_voice_marker(message, build_voice_marker(calls))

async def registered_tools():
    from behavior_scheduler import get_global_scheduler
    scheduler = await get_global_scheduler()
    return {name: scheduler.tools[name] for name in EXPRESSION_TOOLS if name in scheduler.tools}

def output_instruction(tools):
    from behavior_scheduler.tool_guidance import build_stable_tool_guidance
    entries = {}
    for name in EXPRESSION_TOOLS:
        tool = tools.get(name)
        if tool is None:
            continue
        entries[name] = {'pro_guide': getattr(tool, 'share_guide', '') if name == 'cloud_music' else tool.pro_guide}
    guide = build_stable_tool_guidance(entries)
    return '\n你也可以用以下工具主动表达。正文后按调用顺序另起行写 @工具名 自然语言内容@；各工具本条最多一次，可组合使用。附件异步交付，提交请求不等于已经生成或人类伙伴已看到。\n' + guide if guide else ''

def split_output(content, *, schedule_enabled=True):
    from proactive_checkup import split_output as split_checkup
    from proactive_band_touch import split_output as split_band
    directives = extract_tool_actions(str(content or ''), _ALL_TOOLS)
    if not directives:
        visible, band, checkup, error = split_checkup(content)
        return (visible, band, checkup if schedule_enabled else None, [], error)
    visible = strip_tool_protocol_text(content, _ALL_TOOLS)
    names = [item.tool for item in directives]
    if len(names) != len(set(names)):
        return (visible, None, None, [], 'multiple_actions')
    existing = '\n'.join((f'@{item.tool} {item.instruction}@' for item in directives if item.tool in {'band_touch', 'schedule_checkup'}))
    if schedule_enabled:
        _, band, checkup, error = split_checkup(existing)
    else:
        _, band, error = split_band(existing)
        checkup = None
    requests = [{'tool': item.tool, 'instruction': item.instruction} for item in directives if item.tool in EXPRESSION_TOOLS]
    return (visible, band, checkup, requests if not error else [], error)

class ExpressionDelivery:
    """The caller finishes this gate after commit or on failure/cancellation."""

    def __init__(self):
        self.gate = AttachmentDeliveryGate()
        self.calls = []

    def finish(self, accepted):
        self.gate.finish(accepted)

    async def prepare(self, requests, session_id, *, scheduler=None):
        if not requests or not session_id:
            return self.calls
        if scheduler is None:
            from behavior_scheduler import get_global_scheduler
            scheduler = await get_global_scheduler()
        from behavior_scheduler.tool_standardizer import ToolCallStandardizer
        validator = ToolCallStandardizer({name: tool for name, tool in scheduler.tools.items() if name in EXPRESSION_TOOLS})
        seen = set()
        for request in requests:
            name = request.get('tool')
            if name not in EXPRESSION_TOOLS or name in seen:
                continue
            seen.add(name)
            text = str(request.get('instruction') or '').strip()
            record = {'tool': name, 'description': {'send_voice': '发送语音', 'cloud_music': '分享歌曲'}[name], 'delivery': 'ui_only', 'success': False, 'status': 'failure'}
            try:
                if not text:
                    raise ValueError('empty_expression')
                raw = {'tool': name, 'parameters': {'text': text} if name == 'send_voice' else {'action': 'share', 'query': text}}
                normalized = validator.standardize(raw)
                if not normalized.is_valid:
                    raise ValueError('invalid_expression_parameters')
                params = {key: value for key, value in normalized.parameters.items() if not key.startswith('_')}
                tool = scheduler.tools[name]
                result = await tool.execute(**params, _session_id=session_id, _delivery_gate=self.gate)
                success = getattr(result.status, 'value', result.status) == 'success'
                record.update(parameters=params, result=str(result.content or ''), success=success, status='success' if success else 'failure')
                if result.extra_data:
                    record['extra_data'] = result.extra_data
            except Exception as exc:
                record.update(result='表达附件未完成，请查看失败状态；未自动重试', error=type(exc).__name__)
            self.calls.append(record)
        return self.calls
