"""Rebuildable workshop entry facts; no scheduling or model decisions."""

LABELS = {'workshop_create':'工作坊·酝酿新想法',
          'workshop_history':'工作坊·欣赏历史作品',
          'workshop_continue':'工作坊·继续未完成工单'}


def describe(service, *, audience='k'):
    from .delivery_policy import job_status
    from .collection_inventory import inventory
    collections = inventory(service.store, audience='public')
    ongoing = []
    for job in service.store.jobs():
        if job['state'] in ('accepted', 'canceled'):
            continue
        delivery = job_status(service.store, job)
        review = delivery.get('review_progress', {}) if job['state']=='awaiting_review' else {}
        if delivery['lightweight_ready'] and job['visibility']=='public':
            continue
        decision = ('finish' if delivery['lightweight_ready'] and job['visibility']=='private' else
                    'answer' if job['state']=='awaiting_input' else
                    'confirm' if job['state'] in ('awaiting_confirmation','queued','account_paused','paused','budget_paused') and job['plan'] and job['plan_version']!=job['confirmed_version'] else
                    review.get('pending_k_action'))
        waiting_for = ('k' if decision else 'technical_recovery' if job['state']=='recovery_required' else
                       'resources_or_resume' if job['state'] in ('budget_paused','account_paused','paused','stopping') else
                       review.get('waiting_for') or 'codex')
        private = audience=='public' and job.get('visibility')=='private'
        ongoing.append({'job_id':job['id'], 'title':'AI 的保密工单' if private else job['title'],
            'revision':job['revision'], 'state':job['state'], 'waiting_for':waiting_for,
            'review_phase':review.get('phase'),
            'pending_k_action':decision, 'plan_version':None if private else job['plan_version'],
            'confirmed_version':None if private else job['confirmed_version']})
    with service.store.db() as db:
        # A work entry must have a displayable file; reports alone are not exhibits.
        from .presentation import entries
        history_count=len(entries([dict(row) for row in db.execute(
            'SELECT a.name FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE j.deleted=0')]))
    options=[{'event_type':'workshop_create','title':LABELS['workshop_create'],
              'scope':'提出独立的小作品想法、讨论需求；可选创作起点见creative_options'}]
    if history_count:
        options.append({'event_type':'workshop_history','title':LABELS['workshop_history'],
                        'scope':'查看已交付作品、实际欣赏或体验；具体升级沿原工单另起需求版本'})
    if ongoing:
        options.append({'event_type':'workshop_continue','title':LABELS['workshop_continue'],
                        'scope':'处理下列未完成工单；pending_k_action为当前待AI的决定，waiting_for为目前等待方'})
    needs_k=sum(row['pending_k_action'] is not None for row in ongoing)
    return {'source':'本次读取的工单当前状态与已交付文件；是候选入口和执行事实，不是AI已经行动',
            'status':'idle' if not ongoing else 'needs_k' if needs_k else 'waiting',
            'summary':('工作坊空闲：没有未完成工单，也没有待AI确认、回答或验收的任务。' if not ongoing else
                       f'当前有{len(ongoing)}张未完成工单，其中{needs_k}张有待AI处理的决定。'),
            'ongoing_jobs_count':len(ongoing),'needs_k_count':needs_k,
            'private_collections':collections,
            'displayable_files_count':history_count,'options':options,'ongoing_jobs':ongoing[:20],
            'scope':'数量覆盖全部未删除工单；未完成详情按接受顺序展示前20张；归档作品在作品柜查询'}


def available(event_type):
    from .autonomy import available as visit_available
    from .service import get_service
    if not visit_available():
        return False
    return any(row['event_type']==event_type for row in describe(get_service())['options'])
