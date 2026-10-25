"""Public access and sourced inspiration; external callers never impersonate AI."""
import json

from .privacy import public_job, require_public
from .store import now

SOURCES = {'human': '人类伙伴', 'desktop_codex': '桌面 Codex 的建议'}


def room(service, job_id, offset=0):
    require_public(service.store.job(job_id))
    result = service.store.room(job_id, offset)
    from .conversation_view import messages
    result['messages'] = messages(service.store, result['job'], result['messages'])
    from .budget import summary
    result['job']['budget_summary'] = summary(result['job'],limits_enabled=service.store.settings()['budget_limits_enabled'])
    from .quality import summary as quality_summary
    result['job']['quality_summary'] = quality_summary(service.store,result['job'])
    from .delivery_policy import job_status
    result['job'].update(job_status(service.store,result['job']))
    result['job'] = public_job(result['job'])
    return result


async def contribute(service, args, source):
    if source not in SOURCES:
        raise ValueError('invalid_inspiration_source')
    text, request_id = args.get('text'), args.get('request_id')
    if not isinstance(text, str) or not text.strip() or len(text) > 30000 or not isinstance(request_id, str) or not request_id or len(request_id) > 200:
        raise ValueError('bounded_inspiration_and_request_id_required')
    key = 'inspiration:' + source + ':' + request_id
    async with service.mutation:
        job = service.store.job(args['job_id'])
        require_public(job)
        with service.store.db() as db:
            old = db.execute('SELECT result FROM commands WHERE key=?', (key,)).fetchone()
            if old:
                saved = json.loads(old[0])
                if saved['job_id'] != job['id']:
                    raise ValueError('request_id_already_used')
                return saved
            if args.get('revision') != job['revision']:
                raise ValueError('revision_required_or_stale')
            revision = job['revision'] + 1
            pending = (job['pending_text'] + '\n[' + SOURCES[source] + '补充；非 AI 发言]\n' + text).strip()
            running = job['state'] in ('discussing', 'executing', 'stopping')
            state = job['state'] if running or job['state'] in ('paused', 'budget_paused', 'recovery_required') else 'queued'
            queue_seq = job['queue_seq']
            if job['state'] in ('accepted', 'canceled'):
                queue_seq = db.execute('SELECT coalesce(max(queue_seq),0)+1 FROM jobs').fetchone()[0]
            db.execute('INSERT INTO messages(job_id,role,body,created_at) VALUES(?,?,?,?)', (job['id'], source, text, now()))
            db.execute("UPDATE jobs SET revision=?,confirmed_version=0,plan='',pending_text=?,state=?,queue_seq=?,resume_state=?,updated_at=? WHERE id=?",
                       (revision, pending, state, queue_seq, 'discussing' if not running else job['resume_state'], now(), job['id']))
            receipt = service.store._event(db, job['id'], 'inspiration_added', {'source': source, 'text': text,
                'revision': revision, 'summary': SOURCES[source] + '补充了灵感；原方案保留在历史，新一轮方案由 AI 确认。',
                'execution_receipt': '当前执行回合结束后讨论' if running else '已记录，等待讨论；暂停或额度门槛仍有效'})
            result = {'accepted': True, 'job_id': job['id'], 'revision': revision, 'source': source, 'receipt_id': receipt}
            db.execute('INSERT INTO commands VALUES(?,?)', (key, json.dumps(result, ensure_ascii=False)))
    service.wake.set()
    return result


async def external_action(service, args):
    action = args.get('action')
    if action == 'list':
        return service.snapshot(args.get('offset', 0), audience='public')
    if action == 'room':
        return room(service, args['job_id'], args.get('offset', 0))
    if action == 'works':
        from .catalog import public_works
        return {'cabinet':public_works(service, args.get('offset',0))}
    if action == 'contribute':
        return await contribute(service, args, 'desktop_codex')
    if action == 'inspect':
        require_public(service.store.job(args['job_id']))
        return await service.action(args)
    raise ValueError('desktop_action_not_supported')
