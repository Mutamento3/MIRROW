"""Source-labelled textual checkup receipts; no inference, collection or image storage."""
from typing import Any


def source_item(tool, description, status, *, result='', error='', attempted=True,
                captured=None, analyzed=None):
    from context_builder.text_utils import _redact_tool_fact
    item = {'tool': tool, 'description': description, 'status': status,
            'result': _redact_tool_fact(str(result or '')),
            'error': _redact_tool_fact(str(error or '')), 'attempted': bool(attempted)}
    if captured is not None:
        item['captured'] = bool(captured)
    if analyzed is not None:
        item['analyzed'] = bool(analyzed)
    return item


def analysis_text(analysis: Any) -> str:
    if not isinstance(analysis, dict):
        return str(analysis or '').strip()
    parts = []
    for key in ('summary', 'visible_evidence'):
        text = str(analysis.get(key) or '').strip()
        if text and text not in parts:
            parts.append(text)
    return '\n'.join(parts)


def visual_item(source, *, fallback_tool='', error=''):
    tool = str(source.get('source') or fallback_tool)
    text = analysis_text(source.get('analysis'))
    captured = bool(source.get('captured') or source.get('image_url') or source.get('attachment'))
    item = source_item(tool, str(source.get('label') or tool),
        'success' if text else 'partial' if captured else 'failure',
        result=text or ('已取得画面，但未完成分析' if captured else ''),
        error='' if text else (error or ('画面未返回分析' if captured else '未取得画面')),
        captured=captured, analyzed=bool(text))
    if isinstance(source.get('analysis'), dict):
        # The envelope's factual fields only; never carry image bytes/provider bodies.
        from context_builder.text_utils import _redact_tool_fact
        if source['analysis'].get('confidence') is not None:
            item['confidence'] = _redact_tool_fact(str(source['analysis']['confidence']))
    return item


def phone_items(call, parent):
    extra = call.get('extra_data') or {}
    sources = extra.get('sources')
    if not isinstance(sources, dict):
        return [parent]  # Legacy/custom tools without structured sources keep their real text.
    nonvisual = {key: value for key, value in sources.items()
                 if key != 'screenshot' and isinstance(value, dict)}
    items = []
    if nonvisual:
        states = {}
        for key, value in nonvisual.items():
            # Sentinel usage is a cache projection without a success flag.
            success = value.get('success')
            states[key] = {'status': 'success' if success is True else
                          'failure' if success is False else 'unknown'}
            data = value.get('data')
            if isinstance(data, dict):
                for field in ('available', 'observed_at_ms', 'screen_on', 'foreground_available'):
                    if field in data:
                        states[key][field] = data[field]
        successes = [state['status'] == 'success' for state in states.values()]
        status = 'success' if any(successes) else 'failure'
        if any(state['status'] == 'failure' or state.get('available') is False for state in states.values()):
            status = 'partial' if any(successes) else 'failure'
        item = source_item('mobile_activity', '手机活动与使用记录', status,
            result=extra.get('phone_activity_text') or parent['result'],
            error=parent['error'] if status == 'failure' else '')
        item['source_states'] = states
        items.append(item)
    screenshot = sources.get('screenshot')
    if isinstance(screenshot, dict):
        rows = extra.get('visual_sources') or []
        row = next((row for row in rows if isinstance(row, dict)
                    and str(row.get('source') or '').startswith('phone_screen')), None)
        if row is None:
            row = {'source': 'phone_screen', 'label': '手机屏幕',
                   'captured': screenshot.get('captured') is True or bool(screenshot.get('image_url')),
                   'analysis': screenshot.get('analysis') or ''}
        error = screenshot.get('error') or screenshot.get('content') or call.get('error') or ''
        items.append(visual_item(row, fallback_tool='phone_screen', error=str(error)))
    return items or [parent]


def screen_items(call, parent):
    extra = call.get('extra_data') or {}
    rows = extra.get('visual_sources')
    if isinstance(rows, list) and rows:
        return [visual_item(row, fallback_tool=call['tool'], error=call.get('error') or '')
                for row in rows if isinstance(row, dict)]
    if extra.get('images') or extra.get('image_base64'):
        return [source_item('pc_screen' if call['tool'] == 'take_screenshot' else 'camera',
                            parent['description'], 'partial', result='已取得画面，但未完成分析',
                            error=call.get('error') or '', captured=True, analyzed=False)]
    return [parent]
