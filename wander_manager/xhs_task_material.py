"""Public task material boundary: observations only, no raw frames or opinions."""
from datetime import datetime


async def fetch_material(handler, task, *, cursor=None, exclude_ids=None):
    if task['operation'] == 'continue':
        return {'status': 'resume_requires_runtime'}
    try:
        raw = await handler._device_source.fetch_task(task=task, cursor=cursor or {},
                                                     exclude_ids=set(exclude_ids or ()))
    except Exception as exc:
        return {'status': 'device_error', 'error': type(exc).__name__}
    if not isinstance(raw, dict):
        return {'status': 'invalid_device_result'}
    result = dict(raw)
    frames = result.pop('frames', [])
    screenshot = result.pop('screenshot_base64', '')
    result.pop('home_screenshot_base64', None)
    result.update(source_provider='xiaohongshu_android_adb',
                  execution_mode='dedicated_device_direct', material_version=1,
                  task_spec=task, retrieved_at=datetime.now().astimezone().isoformat())
    if result.get('status') != 'success':
        # No failed acquisition may advance the host's successful cursor.
        result.pop('cursor_after', None)
        return result
    if not frames and screenshot:
        frames = [{'label': 'visible_screen', 'screenshot_base64': screenshot,
                   'ui_text': result.get('ui_text', '')}]
    if not frames or not result.get('source_id'):
        result.pop('cursor_after', None)
        return dict(result, status='insufficient_device_evidence')
    observations, calls = [], []
    for frame in frames:
        vision, error = await handler._run_device_vision(frame['screenshot_base64'],
            query=task['query'], ui_text=frame.get('ui_text', ''))
        if error:
            result.pop('cursor_after', None)
            return dict(result, status='vision_unavailable', error=error)
        raw_content = handler._vision_content(vision)
        parsed = handler._parse_vision_json(raw_content)
        summary = str(parsed.get('summary') or raw_content).strip()
        if not summary:
            result.pop('cursor_after', None)
            return dict(result, status='vision_empty')
        observations.append({'page': frame['label'], 'observation': summary[:4000],
                             'omitted_chars': max(0, len(summary)-4000)})
        calls.extend(handler._vision_audit(vision, raw_content)['calls'])
    ui_text = str(result.pop('ui_text', '') or '')
    result.update(content_summary='\n'.join(x['observation'] for x in observations)[:2000],
        image_observations=observations, evidence_excerpt=handler._evidence_excerpt(ui_text),
        source_ui_text=ui_text[:3000], media_observation='android_screens_and_accessibility_text',
        reflection='', agent_provider='flash-vision', agent_audit={'calls': calls})
    result.setdefault('coverage', {'text': 'visible_excerpt', 'images': 'visible_screen', 'video': 'not_played'})
    result.setdefault('limitations', '仅取得可见屏幕；未证明全文或视频完整读取。')
    return result
