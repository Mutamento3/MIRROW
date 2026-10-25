"""Current workshop room facts for AI, separate from historical notifications."""


def current_room(service, job_id=None):
    from .delivery_policy import job_status
    jobs = [service.store.job(job_id)] if job_id else [job for job in service.store.jobs() if job['state'] not in ('accepted', 'canceled')
                                                    and (job['visibility']=='private' or not job_status(service.store,job)['lightweight_ready'])]
    if not jobs:
        return None
    def priority(job):
        return (0 if job['plan'] and job['confirmed_version'] != job['plan_version'] else
                1 if job['state'] == 'awaiting_review' else 2, job['queue_seq'])
    job = min(jobs, key=priority)
    room = service.store.room(job['id'])
    if room['total_messages'] > 50:
        room = service.store.room(job['id'], max(0, room['total_messages']-100), 100)
    room['job'].update(job_status(service.store, job))
    from .decisions import answered
    from .presentation import facts as delivery_facts
    return {**room,
            'actual_delivery':delivery_facts(service,job['id']),
            'answered_decisions':answered(service.store,job['id']),
            'source': '本次来访重新读取的真实工单房间；job 是当前状态，messages 和 receipts 保留历史原话与历史回执',
            'current_revision_artifact_ids': [row['id'] for row in room['artifacts'] if row['revision'] == job['revision']],
            'current_plan_available': bool(job['plan']),
            'plan_confirmation_recorded': bool(job['confirmed_version'] and job['confirmed_version'] == job['plan_version'])}


def material(service, invocation=None, *, focus_job_id=None, **extra):
    from .autonomy import facts
    result = {'current_workshop': facts(), 'current_room': current_room(service,focus_job_id), **extra}
    if invocation:
        result['invitation'] = invocation
        entry=invocation.get('workshop_entry')
        if entry:
            result['entry_context']={'event_type':entry, 'activity_purpose':invocation.get('activity_purpose',''),
                'source':'AI本轮漫想计划选择的来访目的；行动结果另见实际工具回执'}
        if not focus_job_id and entry in ('workshop_create','workshop_history'):
            result['current_room']=None
        if entry=='workshop_history':
            from .catalog import works
            from .presentation import kind
            result['cabinet_entries']=[{key:row[key] for key in ('id','job_id','job_title','visibility','revision','name','accepted_by_k','job_archived') if key in row}
                for row in works(service)['artifacts'] if kind(row)!='supporting']
        from .invited_request import starts_new_work
        if not focus_job_id and starts_new_work(invocation):
            # A new gift request does not implicitly select the oldest night lamp.
            result['current_room'] = None
    return result
