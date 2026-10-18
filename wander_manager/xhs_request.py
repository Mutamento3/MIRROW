"""Explicit read-only XHS tasks for host integration; no chat or device authority."""
from copy import deepcopy

def validate_task(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_xhs_task')
    operation = value.get('operation')
    if operation not in {'recommend', 'search', 'profile', 'continue'}:
        raise ValueError('unsupported_xhs_operation')
    query = value.get('query') or ''
    task_id = value.get('task_id') or ''
    scope = value.get('scope', 'count')
    count = value.get('count', 3)
    goal_count = value.get('goal_count') or count
    if (not isinstance(query, str) or len(query) > 120 or
            not isinstance(task_id, str) or len(task_id) > 80 or
            scope not in {'count', 'all'} or isinstance(count, bool) or
            not isinstance(count, int) or not 1 <= count <= 5 or isinstance(goal_count, bool) or
            not isinstance(goal_count, int) or not 1 <= goal_count <= 500):
        raise ValueError('invalid_xhs_task')
    if operation in {'search', 'profile'} and not query.strip():
        raise ValueError('target_required')
    if scope == 'all' and operation not in {'profile', 'continue'}:
        raise ValueError('all_requires_profile')
    if operation not in {'profile', 'continue'} and goal_count > count:
        raise ValueError('long_count_requires_profile')
    if operation == 'continue' and not task_id:
        raise ValueError('task_id_required')
    return {'version': 1, 'operation': operation, 'query': query.strip(),
            'scope': scope, 'count': count, 'goal_count': goal_count, 'task_id': task_id}

async def compile_task(instruction, *, compiler=None):
    """Compile AI's chosen action; no keyword intent inference or new decisions."""
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
        raise ValueError('instruction_required')
    if compiler is None:
        from .flash_structured import call_flash_json_detailed
        compiler = call_flash_json_detailed
    result = await compiler(messages=[
        {'role': 'system', 'content': (
            '把AI已经决定的小红书只读行动编译为JSON，保持原目标和范围。可用operation：'
            'recommend首页、search搜索主题、profile搜索账号并读主页笔记、continue按已有task_id续读。'
            'query原样保留目标账号/主题（含符号）；scope=count或all（只有明确全部主页才用all）；'
            'count是本批篇数1至5默认3，goal_count保留要求的总篇数（未指定则等于count），超过5篇仅支持主页续读。'
            'continue须原样提取回执的task_id。输出{operation,query,scope,count,goal_count,task_id}。'
            '不支持发布、评论、下单、视频完整播放或指定链接直读；'
            '这些请求输出{"operation":"unsupported"}，不降级成搜索。')},
        {'role': 'user', 'content': instruction}], temperature=0, max_tokens=700)
    if result.status != 'ok':
        raise ValueError('xhs_compile_failed')
    return validate_task(result.parsed)

def latest_cursor(nodes, initial=None):
    cursor = deepcopy(initial or {})
    for node in nodes:
        payload = node.get('source_payload') or {}
        if node.get('execution_status') == 'succeeded' and isinstance(payload.get('cursor_after'), dict):
            cursor = deepcopy(payload['cursor_after'])
    return cursor

def material_for_k(payload):
    """Exactly the same bounded article material at foreground/background delivery."""
    result = {key: deepcopy(payload[key]) for key in (
        'title', 'source_id', 'source_provider', 'retrieved_at', 'article_text',
        'image_observations', 'coverage', 'limitations', 'content_summary') if key in payload}
    if 'image_observations' in result:
        result['image_observations'] = [{key: item[key] for key in ('page', 'observation', 'omitted_chars') if key in item}
                                        for item in result['image_observations'] if isinstance(item, dict)]
    return result
