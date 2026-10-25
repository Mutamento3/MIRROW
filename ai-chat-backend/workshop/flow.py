"""Human-only, rebuildable progress view over immutable workshop evidence."""
import json

STEPS = ('queue', 'discuss', 'confirm', 'make', 'quality', 'accept', 'shelf')
KINDS = {'accepted':'queue', 'plan':'discuss', 'modified':'discuss', 'inspiration_added':'discuss',
         'decision_required':'discuss','decision_answered':'discuss',
         'confirmed':'confirm', 'delivered':'make', 'delivery_referenced':'make',
         'quality_queued':'quality', 'quality_blocked':'quality', 'quality_report':'quality',
         'quality_skipped':'quality', 'artifact_shared':'shelf',
         'inspected_browser':'accept', 'inspected_text':'accept', 'accepted_work':'accept'}
KINDS['archived']='archive'
KINDS['completion_decided']='shelf'
STATE = {'queued':'queue', 'account_paused':'queue', 'discussing':'discuss', 'awaiting_input':'discuss',
         'awaiting_confirmation':'confirm', 'executing':'make', 'awaiting_review':'quality', 'accepted':'shelf'}

def project(store, job_id, *, include_archive=False):
    from .privacy import require_public
    job = store.job(job_id)
    require_public(job)
    room = store.room(job_id)
    revision = job['revision']
    with store.db() as db:
        rows = db.execute('SELECT seq,id,kind,body,created_at FROM receipts WHERE job_id=? ORDER BY seq', (job_id,)).fetchall()
    steps=(*STEPS,'archive') if include_archive else STEPS
    if include_archive:
        from .journey import with_archive_basis
        rows=list(with_archive_basis(rows))
    groups = {step:[] for step in steps}
    history_count = 0
    history = {}
    current_revision = 1
    controls = []
    for row in rows:
        item = dict(row)
        body = json.loads(item['body'])
        if item['kind'] in ('modified','inspiration_added'):
            current_revision = body.get('revision', current_revision)
        item_revision = body.get('revision', current_revision)
        item['body'] = body
        step = KINDS.get(item['kind'])
        if item['kind']=='character_references_sent':
            step='make' if body.get('stage')=='executing' else 'discuss'
        if item['kind'] in ('source_evidence','source_read','tool_failed','failed') and body.get('stage')=='executing':
            step = 'make'
        if item_revision != revision:
            history_count += 1
            old_groups = history.setdefault(item_revision, {step:[] for step in steps})
            old_groups.setdefault(step or 'resources', []).append(item)
            continue
        if step:
            groups[step].append(item)
        else:
            controls.append(item)
    latest_plan = max((item['seq'] for item in groups['discuss'] if item['kind']=='plan'), default=0)
    current_deliveries = {item['body'].get('artifact_id') for item in groups['make'] if item['seq'] > latest_plan}
    artifacts = [item for item in room['artifacts'] if item['revision'] == revision and (
        not latest_plan or item['id'] in current_deliveries)]
    reports = [item['quality'] for item in artifacts if item.get('quality')]
    from .delivery_policy import group_status
    quality_state = group_status([item.get('quality') for item in artifacts])
    quality_complete = quality_state['review_ready']
    light = quality_state['lightweight_ready']
    accepted = any(item['kind']=='accepted_work' and item['seq'] > latest_plan for item in groups['accept']) or (
        job['state']=='accepted' and bool(artifacts))
    confirmed = bool(job['confirmed_version'] and job['confirmed_version']==job['plan_version'])
    complete = {'queue':True, 'discuss':bool(job['plan']), 'confirm':confirmed,
        'make':bool(artifacts), 'quality':quality_complete, 'accept':accepted, 'shelf':accepted,
        'archive':job['state']=='accepted'}
    stage = STATE.get(job['state'], STATE.get(job.get('resume_state'), 'discuss'))
    if include_archive and job['state']=='accepted':
        stage='archive'
    if job['state'] in ('queued','account_paused'):
        stage = STATE.get(job.get('resume_state'), 'make' if confirmed else 'queue')
    if (job['state']=='awaiting_review' or job.get('resume_state')=='awaiting_review') and quality_complete:
        stage = 'shelf' if light else 'accept'
    holding = job['state'] in ('budget_paused','account_paused','paused','recovery_required','stopping','canceled')
    if job['state']=='awaiting_input' and job['reason']=='k_decision_required':holding=True
    if job['state']=='awaiting_review' and any(item['status'] in ('failed','interrupted','budget_paused') for item in reports):
        holding = True
    nodes = []
    for step in steps:
        records = groups[step]
        status = 'done' if complete[step] else 'pending'
        if step==stage:
            status = 'blocked' if holding else ('done' if job['state']=='accepted' else 'current')
        if light and step in ('quality','accept') and not (step=='accept' and accepted):
            status = 'skipped'
        if light and step=='shelf':
            status = 'done'
        nodes.append({'id':step, 'status':status, 'count':len(records), 'records':records[-20:]})
    from .journey import visits
    archive_basis=(groups.get('archive') or [None])[-1]
    return {'revision':revision, 'current':stage, 'state':job['state'], 'reason':job['reason'], 'lightweight_ready':light,
            'flow_version':2 if include_archive else 1,
            'archive':{'archived':job['state']=='accepted','source':'工单当前state；归档依据来自已有AI验收回执，不补写历史事件',
                       'basis_receipt_id':archive_basis['body']['basis_receipt_id'] if archive_basis else None,
                       'basis_at':archive_basis['created_at'] if archive_basis else None},
            **visits(rows), 'current_artifact_ids':[item['id'] for item in artifacts],
            'nodes':nodes, 'controls':controls[-20:], 'control_count':len(controls),
            'history_count':history_count, 'updated_at':job['updated_at'], 'history_version_count':len(history),
            'history':[{'revision':version, 'nodes':[{'id':step,'count':len(records),'records':records[-20:]}
                 for step,records in history[version].items() if records]} for version in sorted(history,reverse=True)[:8]]}
