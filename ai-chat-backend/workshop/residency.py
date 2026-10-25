"""One persistent cooperation; only real AI turns hold conversation focus."""
import asyncio
import contextlib
import logging
from . import residency_store as store
from .store import now
logger = logging.getLogger(__name__)


def frontier(service, record):
    if not service.store.settings()['enabled']:return '', 'blocked', '工作坊已关闭'
    if not record['job_id']:
        return 'opening', 'opening', ''
    try:
        job = service.store.job(record['job_id'],include_deleted=True)
    except KeyError:
        return '', 'blocked', '原工单已不存在'
    if job['deleted'] or job['state']=='canceled':return '', 'blocked', '原工单已取消'
    if record['segment']==0 and job['state']=='accepted':return 'opening', 'opening', ''
    if not service.store.settings()['enabled']:return '', 'blocked', '工作坊已关闭'
    if job['state'] in ('paused','budget_paused','recovery_required','stopping'):
        return '', 'blocked', {'budget_paused':'工单预算暂停','recovery_required':'工具手现场需要核对',
            'paused':'工单已暂停','stopping':'工具手正在保存停止现场'}[job['state']]
    if job['state'] in ('queued','account_paused'):
        if job['reason']=='quota_unknown':return '', 'blocked', '账号额度暂无法核对，开工条件未满足'
        remaining=service.quota.get('remaining')
        if remaining is not None and remaining < service.store.settings()['remaining_floor']:
            return '', 'blocked', '账号剩余额度低于当前开工门槛'
    from .decisions import pending
    questions = pending(service.store,job['id'])
    if questions and job['state']=='awaiting_input':
        return 'questions:'+':'.join(q['id'] for q in questions), 'discussing', ''
    if job['state']=='awaiting_input':
        with service.store.db() as db:
            row=db.execute("SELECT max(seq) FROM messages WHERE job_id=? AND role='codex'",(job['id'],)).fetchone()
        if row and row[0]:return f"reply:{job['revision']}:{row[0]}", 'discussing', ''
        return '', 'blocked', '工具手回合结束，尚无可处理的方案或回复'
    if job['plan'] and job['plan_version']!=job['confirmed_version'] and job['state'] not in ('discussing','executing'):
        return f"plan:{job['revision']}:{job['plan_version']}", 'discussing', ''
    if job['state'] in ('awaiting_review','accepted'):
        from .delivery_policy import job_status
        from .progress import current_artifacts
        state=job_status(service.store,job)
        if (state.get('review_progress') or {}).get('phase')=='resource_paused':
            return '', 'blocked', '质检预算暂停'
        artifacts=current_artifacts(service.store,job)
        if state['review_ready'] or (state.get('review_progress') or {}).get('phase')=='blocked':
            return 'delivery:'+str(job['revision'])+':'+':'.join(a['id'] for a in artifacts), 'reviewing', ''
        return '', 'checking', ''
    return '', 'making' if job['confirmed_version'] else 'discussing', ''


def result(record):
    actions=record['actions']
    message=('这次工作坊合作已完成。' if any(a['action']=='finish' for a in actions) else
             '这次工作坊来访已结束；实际动作按原记录保留。' if actions else
             '这次工作坊来访已结束，没有提交新工单或新增确认、验收决定。')
    return {'status':'success','message':(message if record['state']=='completed'
        else '这次工作坊合作已暂停：'+record['reason']+'。工单与现场保留。'),
        'job_id':record['job_id'],'actions':actions,'action_failures':record['action_failures'],
        'experience':record['experience'],'collaboration':{'id':record['id'],'state':record['state'],'reason':record['reason']},
        'evidence':{'action_count':len(actions),
            'inspected_job_ids':[a['job_id'] for a in actions if a['action']=='inspect'],
            'new_plan_confirmation_job_ids':[a['job_id'] for a in actions if a['action']=='confirm' and a.get('confirmation_status') not in ('already_confirmed','answer_required')],
            'new_acceptance_job_ids':[a['job_id'] for a in actions if a['action'] in ('accept','finish')]}}


def finish(service, record, reason=''):
    record.update(state='blocked' if reason else 'completed',phase='blocked' if reason else 'completed',
                  reason=reason,finished_at=now())
    store.save(service,record)
    outcome=result(record)
    invocation=record['invocation']
    if invocation.get('source')=='ui_action':
        from .invitation import _event
        import json
        with service.store.db() as db:
            old=db.execute('SELECT result FROM commands WHERE key=?',('invitation:'+record['id'],)).fetchone()
        if old:
            visit=json.loads(old[0])
            if visit['state'] not in ('finished','failed','interrupted'):
                visit.update(state='finished',summary=outcome['message'],finished_at=now(),
                    job_id=record['job_id'],actions=record['actions'],experience=record['experience'])
                _event(service,visit,'visit_finished')
    else:
        from .visit_observation import capture
        outcome['observation']=capture(service)
        from .invitation import _event
        _event(service, {'id':'cooperation:'+record['id'], 'session_id':invocation.get('session_id',''),
            'state':'finished','summary':outcome['message'],'source':'workshop_cooperation',
            'started_at':record['created_at'],'finished_at':record['finished_at'],
            'job_id':record['job_id'],'actions':record['actions'],'experience':record['experience']},'visit_finished')
    with service.store.db() as db:
        import json
        db.execute('INSERT OR REPLACE INTO commands VALUES(?,?)',('visit:'+record['id'],json.dumps(outcome,ensure_ascii=False)))
    service.wake.set()
    return outcome


async def run(service, identity):
    import body_admission as focus
    from . import autonomy
    record=store.load(service,identity)
    if record['state'] not in store.ACTIVE:return result(record)
    while True:
        generation=getattr(service.wake,'generation',0)
        target,phase,reason=frontier(service,record)
        if reason:return finish(service,record,reason)
        if not target:
            if record['state']!='waiting' or record['phase']!=phase:
                record.update(state='waiting',phase=phase)
                store.save(service,record)
            if hasattr(service.wake,'changed'):
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(service.wake.changed(generation),2)
            else:await asyncio.sleep(.2)
            continue
        occupied=focus.status()
        from wander_manager import game_chat_gate
        if occupied['busy'] or occupied['ordinary_work_count'] or game_chat_gate.status()['busy']:
            if record['phase']!='chatting':
                record.update(state='waiting',phase='chatting');store.save(service,record)
            await asyncio.sleep(.2)
            continue
        try:
            lease=focus.reserve(require_idle=True,direction='workshop')
        except RuntimeError:
            await asyncio.sleep(.2)
            continue
        try:
            with focus.owned(lease):
                record.update(state='reviewing' if phase=='reviewing' else 'thinking',phase=phase)
                store.save(service,record)
                invocation={**record['invocation'],'residency':True,'focus_job_id':record['job_id'] or None}
                if phase=='reviewing':
                    from .delivery_brief import prepare
                    invocation['_initial_preview']=await prepare(service,service.store.job(record['job_id']))
                outcome=await autonomy.visit(record['id']+':segment:'+str(record['segment']),invocation)
        except asyncio.CancelledError:
            # Unknown model/tool turns are never replayed as if no action occurred.
            finish(service,record,'本次协作回应被中断，先核对已保存的实际动作')
            raise
        except Exception:
            logger.exception('Workshop cooperation segment incomplete; collaboration=%s', identity)
            return finish(service,record,'工具或回应通路未完成，已保留现场供核对')
        finally:
            focus.release(lease)
        record['segment']+=1
        record['actions'].extend(outcome.get('actions') or [])
        record['action_failures'].extend(outcome.get('action_failures') or [])
        for key in ('received','actions'):
            record['experience'][key].extend((outcome.get('experience') or {}).get(key) or [])
        record['job_id']=outcome.get('job_id') or record['job_id']
        record.update(state='waiting',last_frontier=target)
        store.save(service,record)
        if any(a['action']=='finish' for a in outcome.get('actions') or []):return finish(service,record)
        if outcome.get('status')!='success':return finish(service,record,'本轮未取得实际回应，现场保留')
        if not record['job_id']:return finish(service,record)
        if target=='opening' and service.store.job(record['job_id'])['state']=='accepted':
            return finish(service,record)
        fresh,_,blocked=frontier(service,record)
        if blocked:return finish(service,record,blocked)
        if fresh==target:
            return finish(service,record,'本次尚未形成下一步决定；原方案与作品保留')


def schedule(service):
    task=getattr(service,'collaboration_task',None)
    if task and not task.done():return
    record=next((r for r in reversed(store.records(service)) if r['state']=='waiting'),None)
    if record and service.store.settings()['enabled']:
        service.collaboration_task=asyncio.create_task(run(service,record['id']))


async def close(service):
    task=getattr(service,'collaboration_task',None)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):await task
