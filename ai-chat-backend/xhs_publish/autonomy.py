"""Full-context AI composition with explicit, source-labelled bookshelf turns."""
import json
from .store import PublishStore, account_config
from .service import request
CONTRACT = '你正在准备自己账号的小红书图文笔记。根据本次活动缘由决定想表达什么。可以先查书柜，收到结果后继续创作；介绍自身结构能力可查source=body、detail=true，具体认识可查cognition，经历可查memory/experiences。读取资料的正文是参考材料，和你的创作原文、实际发布回执各有来源。图片采用小红书原生文字卡片。发表时完整给出标题（20 UTF16单位以内）、正文（1000以内）和封面文字（180字以内）。publish会在绑定账号真实公开发送；preview仅预览；finish结束本次准备。仅输出JSON：{"action":"bookshelf|publish|preview|finish","parameters":{},"reflection":"本次自己的感想"}。bookshelf的parameters符合下述书柜查询合同；publish/preview的parameters为title、body、cover_text。'

async def compose(runtime_store, activity, context, *, node_id='', llm=None, builder=None, bookshelf=None, submit=None, publication_store=None):
    if not context or not context.persona.strip() or (not context.session_id):
        raise ValueError('publication_context_required')
    run = runtime_store.get_run(activity['run_id'])
    if not run or run['session_id'] != context.session_id:
        raise ValueError('session_id_mismatch')
    from behavior_scheduler.bookshelf_tool import BookshelfTool
    from wander_manager.flash_structured import call_flash_json_detailed
    from context_builder.builder import ContextBuilder
    from context_builder.assembly import assemble_context_result
    from context_builder.channel_projection import backend_turn
    from wander_manager.context_projection import activity_history_scope
    from behavior_scheduler.execution_context import set_request_identity, reset_request_identity
    llm = llm or call_flash_json_detailed
    builder = builder or ContextBuilder.build
    bookshelf = bookshelf or BookshelfTool()
    submit = submit or request
    store = publication_store or PublishStore()
    key = 'wander:' + activity['activity_id']
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS compositions (source_key TEXT PRIMARY KEY,session_id TEXT NOT NULL,record TEXT NOT NULL)')
        previous = db.execute('SELECT record FROM compositions WHERE source_key=? AND session_id=?', (key, context.session_id)).fetchone()
    data = json.loads(previous[0]) if previous else {'stage': 'ready', 'turns': []}
    if data['stage'] == 'done':
        return data['result']
    if data['stage'] == 'deciding':
        raise ValueError('composition_result_unknown')
    if data['stage'] == 'publishing':
        result = await submit(session_id=context.session_id, source_key=key, action=data['action'], draft=data['draft'], store=store)
        return {'xhs_publication': result, 'reflection': data.get('reflection', '')}

    def save():
        with store.connect() as db:
            db.execute('INSERT INTO compositions VALUES (?,?,?) ON CONFLICT(source_key) DO UPDATE SET record=excluded.record', (key, context.session_id, json.dumps(data, ensure_ascii=False)))
    for _ in range(len(data['turns']), 8):
        runtime_text = '[本次小红书创作任务]\n' + json.dumps({'reason': activity.get('reason', ''), 'account': account_config(), 'steps': data['turns'], 'image_capability': '原生文字卡片'}, ensure_ascii=False)
        built = await builder('WANDER_ACTIVITY', persona=context.persona, session_id=context.session_id, context_query=activity.get('reason', ''), wander_runtime_text=runtime_text, **activity_history_scope(runtime_store, activity))
        messages = assemble_context_result(built) + [backend_turn(CONTRACT + '\n' + json.dumps(bookshelf.parameters_schema, ensure_ascii=False))]
        data['stage'] = 'deciding'
        save()
        reply = await llm(messages=messages, temperature=0.0, max_tokens=16384, route_label='wander_xhs_publication', result_validator=lambda result: '' if valid_action(result.parsed) else 'invalid_publication_action')
        raw = reply.parsed
        if not valid_action(raw):
            raise ValueError('composition_invalid_result')
        action = raw.get('action')
        params = raw.get('parameters') or {}
        data['stage'] = 'ready'
        if action == 'bookshelf':
            token = set_request_identity(context.session_id, '', key)
            try:
                read = await bookshelf.execute(**params)
            finally:
                reset_request_identity(token)
            data['turns'].append({'kind': 'bookshelf_result', 'request': params, 'content': read.content, 'error': read.error, 'status': read.status.value})
            save()
            continue
        if action in {'publish', 'preview'}:
            from .store import validate_draft
            data.update(stage='publishing', action=action, draft=validate_draft(params), reflection=str(raw.get('reflection') or ''))
            save()
            publication = store.create(context.session_id, key, data['draft'], account_config())
            if node_id:
                runtime_store.merge_node_side_effect_refs(node_id, {'publication_task_id': publication['task_id']})
            result = await submit(session_id=context.session_id, source_key=key, action=action, draft=data['draft'], store=store)
            data.update(stage='done', result={'xhs_publication': result, 'reflection': data['reflection']})
            save()
            return data['result']
        if action == 'finish':
            data.update(stage='done', result={'status': 'composition_finished', 'reflection': str(raw.get('reflection') or ''), 'material_reads': len(data['turns'])})
            save()
            return data['result']
        data['turns'].append({'kind': 'execution_error', 'error': 'unknown_action'})
        save()
    return {'status': 'composition_budget_reached', 'material_reads': len(data['turns'])}

def valid_action(value):
    if not isinstance(value, dict) or value.get('action') not in {'bookshelf', 'publish', 'preview', 'finish'}:
        return False
    if not isinstance(value.get('parameters', {}), dict) or not isinstance(value.get('reflection', ''), str):
        return False
    if value['action'] in {'publish', 'preview'}:
        from .store import validate_draft
        try:
            validate_draft(value['parameters'])
        except (ValueError, KeyError):
            return False
    return True
