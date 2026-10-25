"""Public delivery cards through the existing durable notification outbox."""
import hashlib
import json


def share(service, job, artifacts, *, source='workshop_delivery', text='', delivery_key='', session_id=''):
    from .privacy import require_public
    if source != 'k':
        require_public(job)
    if not artifacts:
        raise ValueError('current_artifact_review_required')
    if not isinstance(text,str) or len(text)>2000:
        raise ValueError('invalid_share_text')
    signature = json.dumps([source, sorted(row['id'] for row in artifacts), text],ensure_ascii=False)
    key = delivery_key or 'share:'+job['id']+':'+hashlib.sha256(signature.encode()).hexdigest()
    with service.store.db() as db:
        old = db.execute('SELECT result FROM commands WHERE key=?',(key,)).fetchone()
        if old:
            return json.loads(old[0])
    from .presentation import kind, entries
    if not entries(artifacts):
        raise ValueError('displayable_entry_required')
    cards = []
    for artifact in artifacts:
        if artifact['job_id'] != job['id'] or artifact['revision'] != job['revision']:
            raise ValueError('stale_artifact')
        path = service.artifact_path(artifact)
        cards.append({key:artifact[key] for key in ('id','name','revision','summary')})
        with service.store.db() as db:
            references=db.execute("SELECT body FROM receipts WHERE job_id=? AND kind='delivery_referenced' ORDER BY seq DESC",(job['id'],))
            for row in references:
                body=json.loads(row['body'])
                if body.get('artifact_id')==artifact['id'] and body.get('revision')==job['revision']:
                    cards[-1]['summary']=body.get('summary') or artifact['summary']
                    break
        cards[-1]['kind'] = kind(artifact)
    # No mutable URL, path, token or raw runner trace becomes chat content.
    with service.store.db() as db:
        old = db.execute('SELECT result FROM commands WHERE key=?',(key,)).fetchone()
        if old:
            return json.loads(old[0])
        current = db.execute('SELECT visibility,revision,deleted FROM jobs WHERE id=?',(job['id'],)).fetchone()
        if not current or current['deleted']:
            raise KeyError('work_order_not_available')
        if current['revision'] != job['revision']:
            raise ValueError('stale_artifact')
        published = current['visibility'] == 'private'
        if published:
            if source != 'k':
                require_public(dict(current))
            from .store import now
            db.execute("UPDATE jobs SET visibility='public',updated_at=? WHERE id=?",(now(),job['id']))
            service.store._event(db,job['id'],'published',{
                'source':'AI 的主动分享决定','via':'share','revision':job['revision'],
                'summary':'AI 分享了这份作品，同时公开原工单；现在可查看讨论、作品并补充灵感。'})
        body = {'source':source,'revision':job['revision'],'artifacts':cards,
                'summary':'工作坊送来一份新交付，可打开欣赏或保存；工单中的其他交付和未完成项仍以制作记录为准。'}
        if source == 'k':
            from .action_evidence import k_action
            from .store import now
            message = db.execute('INSERT INTO messages(job_id,role,body,created_at) VALUES(?,?,?,?)',
                                 (job['id'],'k',text or '把这份作品分享到主聊天。',now())).lastrowid
            body.update(text=text, session_id=session_id, **k_action('share',job['revision'],message_seq=message,artifact_id=cards[0]['id']))
        receipt_id = service.store._event(db,job['id'],'artifact_shared',body)
        result = {'saved':True,'receipt_id':receipt_id,'artifact_ids':[row['id'] for row in cards]}
        if published:
            result['published'] = True
        db.execute('INSERT INTO commands VALUES(?,?)',(key,json.dumps(result)))
    service.wake.set()
    return result


def auto_share(service, job):
    from .residency_store import automatic_delivery_allowed
    if not automatic_delivery_allowed(service,job['id']):return
    if job.get('visibility') != 'public' or job['state'] not in ('awaiting_review','accepted'):
        return
    from .progress import current_artifacts
    from .quality import latest
    from .delivery_policy import group_status
    artifacts = current_artifacts(service.store,job)
    from .presentation import entries
    artifacts = entries(artifacts)
    if not artifacts:
        return
    reports = [latest(service.store,row['id']) for row in artifacts]
    if not group_status(reports)['review_ready']:
        return
    for report in reports if job['state'] != 'accepted' else []:
        if report['status'] == 'skipped':
            continue
        body = report['body']
        if (body.get('browser_status') != 'passed'
                and body.get('image_review',{}).get('status') != 'passed'):
            return
        if body.get('page_errors') or body.get('console_errors'):
            return
    try:
        share(service,job,artifacts)
    except (ValueError,OSError):
        # An unavailable file is not a share; other work orders can still progress.
        return


def card(service, job, receipt):
    from .privacy import require_public, public_card
    require_public(job)
    for row in receipt['body']['artifacts']:
        artifact = service.artifact(row['id'])
        if artifact['job_id'] != job['id']:
            raise KeyError('artifact_not_found')
        service.artifact_path(artifact)
    return public_card(job,receipt)
