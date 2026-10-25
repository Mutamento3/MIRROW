"""Human-facing projections; authoritative AI/runner records remain private."""


def private(job):
    return job.get('visibility', 'public') == 'private'


def require_public(job):
    if private(job):
        raise KeyError('work_order_not_available')


def public_job(job):
    if not private(job):
        return {key: value for key, value in job.items() if key not in ('pending_text', 'thread_id', 'turn_id')}
    result = {key: job[key] for key in ('id', 'state', 'position', 'revision', 'model', 'tokens',
              'token_cap', 'usage_known', 'created_at', 'updated_at', 'budget_summary', 'review_progress') if key in job}
    # Older clients use quality_summary for labels. Only generic statuses leave
    # this boundary, never artifact IDs, report text or private error details.
    progress = job.get('review_progress') or {}
    result['quality_summary'] = [{'status':row['status'],
        'reason':progress.get('label','') if progress.get('phase') in ('blocked','resource_paused') else ''}
        for row in job.get('quality_summary', [])]
    return {**result, 'visibility': 'private', 'title': 'AI 的保密工单', 'brief': '', 'plan': '',
            'reason': '内容由 AI 保密；公开后可查看与补充。', 'waiting_reasons': []}


def public_card(job, receipt):
    if private(job):
        return {'receipt_id': receipt['id'], 'job_id': job['id'], 'kind': receipt['kind'],
                'title': 'AI 的保密工单', 'visibility': 'private', 'timestamp': receipt['created_at'],
                'summary': 'AI 的保密工单有进展；正文、讨论与作品暂未公开。',
                'tokens': job['tokens'], 'token_cap': job['token_cap']}
    return {'receipt_id': receipt['id'], 'job_id': job['id'], 'title': job['title'],
            'kind': receipt['kind'], 'timestamp': receipt['created_at'], 'visibility': 'public', **receipt['body']}
