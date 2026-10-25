"""Shared execution receipts for existing tools; no selection or scheduling."""
from typing import Any

CHECKUP_TOOLS = frozenset({'eyes', 'check_phone', 'take_screenshot', 'band'})


async def execute_tool_with_receipt(tools: dict[str, Any], name: str, params: dict) -> tuple[str, dict]:
    """Preserve fresh capture material in memory for the common visual boundary."""
    try:
        tool = tools[name]
        execution_params = dict(params)
        if name in ('capture_camera', 'take_screenshot'):
            execution_params['capture_only'] = True
        result = await tool.execute(**execution_params)
        content = result.content if hasattr(result, 'content') else str(result)
        error = getattr(result, 'error', None)
        if error:
            content = f'工具错误: {error}'
        status = getattr(result, 'status', '')
        status = str(getattr(status, 'value', status)).lower()
        success = not error and status not in {'error', 'failed', 'failure', 'skipped', 'need_user_input'}
        receipt = {
            'tool': name,
            'description': getattr(tool, 'get_user_facing_description', lambda **_: name)(**params),
            'status': ('partial' if status == 'partial' else 'success') if success else 'failure',
            'success': success,
            'result': content,
            'error': str(error or ''),
        }
        extra = getattr(result, 'extra_data', None)
        if isinstance(extra, dict):
            receipt['extra_data'] = extra
        return content, receipt
    except Exception as exc:
        # Tool errors can contain private transport details. The execution
        # layer may log them locally; public receipts retain only their type.
        return '工具执行异常', {'tool': name, 'description': name, 'status': 'failure',
                           'success': False, 'error': type(exc).__name__}
