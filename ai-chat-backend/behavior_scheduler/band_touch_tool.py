"""Explicit tactile action, separate from read-only band health queries."""
import asyncio
import uuid
import time
from .base_tool import BaseTool, ToolResult, ToolStatus


class BandTouchTool(BaseTool):
    name = 'band_touch'
    single_use = True
    ui_only_result = True  # Delivery receipt belongs on the attachment, not a second LLM reply.
    description = ('用手环吸引人类伙伴注意。不给 note 就只轻震一下；给 1–80 字 note 就发送'
                   '“小纸条＋震动”。这不是健康查询，也不代表人类伙伴已经看到。')
    flash_description = '用手环吸引人类伙伴注意：无 note 纯震动，有 note 发短消息并同时震动'
    parameter_help = ('note 可选：不填就是纯震动，填写 1–80 字就是小纸条＋震动。'
                      'pattern 可省略，默认 single 轻震，call 为呼叫震动；action 是旧格式兼容项，无需填写。')
    pro_guide = (
        '想吸引人类伙伴注意时使用；只说轻震就震一下，附上短消息则发送手环小纸条并同时震动。'
        '这是亲昵的提醒手段，不必每次主动消息都用。回执只确认蓝牙写入，不表示人类伙伴已看到。'
    )
    parameters_schema = {'type': 'object', 'properties': {
        # action 保留为可选旧协议字段；标准化层始终以 note 是否有正文为权威。
        'action': {'type': 'string', 'enum': ['vibrate', 'note'], 'description': '旧格式兼容项，无需填写'},
        'pattern': {'type': 'string', 'enum': ['single', 'call'], 'default': 'single', 'description': 'single轻震，call呼叫震动'},
        'note': {'type': 'string', 'maxLength': 80, 'description': '可选；1–80字时发送小纸条并同时震动'},
    }, 'required': []}

    def get_user_facing_description(self, **kwargs):
        note = kwargs.get('note')
        return '正在向手环发送纸条并震动…' if isinstance(note, str) and note.strip() else '正在轻触手环…'

    async def execute(self, **kwargs):
        from shared_state import get_mobile_relay_callback
        pattern = kwargs.get('pattern', 'single')
        note = kwargs.get('note', '')
        if not isinstance(note, str) or pattern not in ('single', 'call'):
            return ToolResult(status=ToolStatus.ERROR, content='手环参数无效，未发送。' + self.parameter_help)
        note = note.strip()
        if len(note) > 80:
            return ToolResult(status=ToolStatus.ERROR, content='手环参数无效，未发送。' + self.parameter_help)
        # 单一语义开关：有正文就是纸条＋震动，没有正文就是纯震动。
        # action 只为旧调用兼容，不再参与模型决策。
        action = 'note' if note else 'vibrate'
        relay = get_mobile_relay_callback()
        if relay is None:
            return ToolResult(status=ToolStatus.ERROR, content='手机未连接，未发送手环指令')
        try:
            command_id = 'band_'+uuid.uuid4().hex
            result = await asyncio.wait_for(relay(command_id, 'mobile_miband',
                {'action': action, 'pattern': pattern, 'note': note, 'command_id': command_id,
                 'expires_at_ms': int(time.time()*1000)+25000}), timeout=30)
            ok = bool(result and result.get('success'))
            return ToolResult(status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
                content=(result or {}).get('content') or (result or {}).get('error') or '未获得手环执行确认')
        except Exception:
            return ToolResult(status=ToolStatus.ERROR, content='手环回执中断，执行结果未知，未自动重试')
