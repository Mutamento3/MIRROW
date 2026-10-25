"""Recover transport identity from official receipts, preserving AI's decisions."""
from .runner import AppServerRpcError


async def read_thread(runner, thread_id):
    await runner.start()
    try:
        return await runner.request('thread/read', {'threadId':thread_id, 'includeTurns':True})
    except AppServerRpcError as exc:
        if exc.category != 'thread_not_loaded':
            raise
    # A fresh management process has no loaded threads. Loading a persisted
    # thread is metadata only; it does not start a model turn.
    await runner.request('thread/resume', {'threadId':thread_id, 'excludeTurns':True,
                                         'approvalPolicy':'never', 'sandbox':'read-only'})
    return await runner.request('thread/read', {'threadId':thread_id, 'includeTurns':True})


def unstarted(store, job):
    if (not job['thread_id'] or job['turn_id'] or job['tokens'] or not job['usage_known']
            or job['plan'] or job['plan_version'] or job['confirmed_version']
            or job['resume_state'] not in ('', 'discussing')):
        return False
    with store.db() as db:
        return not (db.execute("SELECT 1 FROM messages WHERE job_id=? AND role='codex' LIMIT 1", (job['id'],)).fetchone()
                    or db.execute('SELECT 1 FROM artifacts WHERE job_id=? LIMIT 1', (job['id'],)).fetchone()
                    or db.execute("SELECT 1 FROM receipts WHERE job_id=? AND kind='input_delivered' LIMIT 1", (job['id'],)).fetchone())


async def recover_unstarted(service, job, source):
    if job['state'] != 'recovery_required' or not unstarted(service.store, job):
        return False
    if job['id'] in service.pool.active():
        raise ValueError('previous_execution_not_confirmed_stopped')
    await service.runner.start()
    try:
        await service.runner.request('thread/resume', {'threadId':job['thread_id'], 'excludeTurns':True,
                                                      'approvalPolicy':'never', 'sandbox':'read-only'})
    except AppServerRpcError as exc:
        if exc.category != 'thread_unmaterialized':
            raise
    else:
        # A real persisted turn may exist even if its original acknowledgement
        # was lost. Keep it for ordinary reconciliation, never replay blindly.
        return False
    from .store import now
    with service.store.db() as db:
        db.execute("UPDATE jobs SET thread_id='',turn_id='',state='queued',resume_state='',reason='technical_retry',updated_at=? WHERE id=?", (now(), job['id']))
        service.store._event(db, job['id'], 'reconciled', {
            'source':source, 'revision':job['revision'], 'previous_thread_id':job['thread_id'],
            'thread_status':'unmaterialized', 'resume_state':'discussing',
            'summary':'官方确认旧线程没有保存记录，首轮输入也未确认接收；保留原需求与全部补充，重建线程重新讨论。方案仍由 AI 确认。'})
    service.wake.set()
    return True


async def retry(service, job_id, revision):
    async with service.mutation:
        job=service.store.job(job_id)
        from .privacy import require_public
        require_public(job)
        if job['revision']!=revision:
            raise ValueError('工单版本已变化，请刷新')
        if job['state']=='queued' and job['reason']=='technical_retry':
            return {'saved':True,'state':'queued'}
        if await recover_unstarted(service, job, 'human_technical_retry'):
            return {'saved':True,'state':'queued','resume_state':'discussing'}
        if job['state']!='recovery_required' or job['reason']!='execution_no_delivery':
            raise ValueError('当前不是可核对续跑的制作卡点')
        if job_id in service.pool.active():
            raise ValueError('当前执行尚未结束')
        if not job['plan_version'] or job['plan_version']!=job['confirmed_version']:
            raise ValueError('当前方案需要 AI 确认')
        from .decisions import pending
        if pending(service.store,job_id):
            raise ValueError('还有问题等待 AI 回答')
        result=await read_thread(service.runner, job['thread_id'])
        turn=next((turn for turn in result.get('thread',{}).get('turns',[]) if turn.get('id')==job['turn_id']),None)
        if not turn or turn.get('status') not in ('completed','failed','interrupted'):
            raise ValueError('尚未取得原回合停止的真实回执')
        service.store.update(job_id,state='queued',resume_state='executing',reason='technical_retry')
        service.store.event(job_id,'execution_resumed',{'source':'human_technical_retry','revision':revision,
            'plan_version':job['plan_version'],'turn_status':turn['status'],
            'summary':'原制作回合已确认结束，技术核对后重新排队；沿用 AI 已确认方案、原线程与累计用量，没有新增 AI 决定。'})
        service.wake.set()
        return {'saved':True,'state':'queued'}
