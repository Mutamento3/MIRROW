"""Execution and invitation frontiers from actual plan/delivery evidence."""
import json


def current_artifacts(store, job):
    with store.db() as db:
        rows = db.execute('SELECT seq,kind,body FROM receipts WHERE job_id=? ORDER BY seq', (job['id'],)).fetchall()
    latest_plan = max((row['seq'] for row in rows if row['kind']=='plan'), default=0)
    delivered = {json.loads(row['body']).get('artifact_id') for row in rows
                 if row['seq']>latest_plan and row['kind'] in ('delivered','delivery_referenced')}
    return [item for item in store.room(job['id'])['artifacts'] if item['revision']==job['revision']
            and (not latest_plan or item['id'] in delivered)]


def invitation_frontier(room):
    """Only new decisions invite AI; chat replies and technical failures do not."""
    job = room['job']
    if job['state']=='awaiting_input' and job.get('reason')=='k_decision_required' and room.get('pending_decisions'):
        return 'decision:'+job['id']+':'+':'.join(row['id'] for row in room['pending_decisions'])
    if job['state']=='awaiting_confirmation' and job['plan_version']!=job['confirmed_version']:
        return f"plan:{job['id']}:{job['revision']}:{job['plan_version']}"
    if job['state']=='awaiting_review':
        current_ids = room.get('flow',{}).get('current_artifact_ids')
        artifacts = [row for row in room['artifacts'] if row['revision']==job['revision']
                     and (current_ids is None or row['id'] in current_ids)]
        from .delivery_policy import group_status, review_progress
        if review_progress(row.get('quality') for row in artifacts)['phase']=='blocked':
            return 'quality_recovery:'+job['id']+':'+':'.join(sorted(
                row['id']+':'+str((row.get('quality') or {}).get('id','')) for row in artifacts))
        status = group_status(row.get('quality') for row in artifacts)
        if status['review_ready'] and not status['lightweight_ready']:
            # A fresh quality retry is a fresh decision; repeated polls are not.
            return 'review:'+job['id']+':'+':'.join(sorted(
                row['id']+':'+str(row['quality'].get('id','')) for row in artifacts))
    return None
