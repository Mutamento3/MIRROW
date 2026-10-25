"""Ordered visits to workshop stages, derived only from immutable receipts."""
import json

ORDER = ('queue', 'discuss', 'confirm', 'make', 'quality', 'accept', 'shelf', 'archive')
STAGE = {'accepted':'queue', 'plan':'discuss', 'modified':'discuss', 'inspiration_added':'discuss',
         'decision_required':'discuss','decision_answered':'discuss',
         'confirmed':'confirm', 'delivered':'make', 'delivery_referenced':'make',
         'quality_queued':'quality', 'quality_blocked':'quality', 'quality_report':'quality',
         'quality_skipped':'quality', 'artifact_shared':'shelf',
         'inspected_browser':'accept', 'inspected_text':'accept', 'accepted_work':'accept', 'archived':'archive'}


def with_archive_basis(rows):
    """A view of the existing acceptance receipt, never a persisted new event."""
    for raw in rows:
        row=dict(raw)
        yield row
        if row['kind']=='accepted_work':
            body=json.loads(row['body'])
            yield {**row,'id':row['id']+':archive','kind':'archived','body':json.dumps({
                **({'revision':body['revision']} if 'revision' in body else {}),'basis_receipt_id':row['id'],'derived':True,
                'summary':'此版本已有AI验收回执，收进作品柜历史；时间为验收依据时间。'},ensure_ascii=False)}


def visits(rows):
    result = []
    revision = 1
    confirmed = False
    for raw in rows:
        item = dict(raw)
        body = json.loads(item['body'])
        if item['kind'] in ('modified', 'inspiration_added'):
            revision = body.get('revision', revision)
            confirmed = False
        current_revision = body.get('revision', revision)
        stage = STAGE.get(item['kind'])
        if stage=='shelf' and result and result[-1]['stage']=='archive' and result[-1]['revision']==current_revision:
            stage='archive'
        if item['kind'] == 'input_delivered':
            stage = 'make' if confirmed else 'discuss'
        if item['kind'] in ('reply','source_evidence','source_read','tool_failed','failed') and result:
            stage = result[-1]['stage']
        if item['kind'] in ('source_evidence','tool_failed','failed') and body.get('stage') in ('executing','discussing'):
            stage = 'make' if body['stage']=='executing' else 'discuss'
        if not stage:
            continue
        prior = result[-1] if result else None
        force = bool(prior and (
            item['kind'] == 'plan' and any(r['kind']=='plan' for r in prior['records']) or
            item['kind'] == 'input_delivered' and any(r['kind']=='input_delivered' for r in prior['records']) or
            item['kind'] == 'quality_queued' and any(r['kind'] in ('quality_queued','quality_report') for r in prior['records'])))
        if not prior or prior['stage'] != stage or prior['revision'] != current_revision or force:
            prior_stage = prior['stage'] if prior else None
            returning = bool(prior and ORDER.index(stage) <= ORDER.index(prior_stage))
            result.append({'id':item['id'], 'number':len(result)+1, 'revision':current_revision,
                'stage':stage, 'from_stage':prior_stage, 'returning':returning,
                'trigger':item['kind'], 'started_at':item['created_at'], 'records':[], 'count':0})
        item['body'] = body
        result[-1]['records'].append(item)
        result[-1]['count'] += 1
        if item['kind'] == 'plan':
            confirmed = False
        elif item['kind'] == 'confirmed':
            confirmed = True
    for visit in result:
        visit['records'] = visit['records'][-20:]
    return {'visits':result[-40:], 'visit_count':len(result)}
