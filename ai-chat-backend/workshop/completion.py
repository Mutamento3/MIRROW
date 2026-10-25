"""AI's single disposition records acceptance and sharing as separate facts."""


def accept(service, job, artifact_id):
    if job['state'] not in ('awaiting_review', 'accepted') or not artifact_id:
        raise ValueError('current_artifact_review_required')
    artifact = service.artifact(artifact_id)
    from .progress import current_artifacts
    if artifact['job_id'] != job['id'] or artifact['revision'] != job['revision'] or artifact_id not in {
            row['id'] for row in current_artifacts(service.store, job)}:
        raise ValueError('stale_artifact')
    service.artifact_path(artifact)
    from .delivery_policy import job_status
    if not job_status(service.store, job)['review_ready']:
        raise ValueError('delivery_review_required')
    if job['state'] != 'accepted':
        from .action_evidence import k_action
        service.store.update(job['id'], state='accepted')
        service.store.event(job['id'], 'accepted_work', {'artifact_id':artifact_id,'revision':job['revision'],
            **k_action('accept', job['revision'], artifact_id=artifact_id)})
    return artifact


def finish(service, job, args):
    disposition = args.get('disposition')
    if disposition not in ('share', 'keep', 'keep_private'):
        raise ValueError('explicit_completion_disposition_required')
    if disposition == 'keep_private' and job['visibility'] != 'private':
        raise ValueError('published_work_cannot_be_retracted')
    import hashlib,json
    key='completion:'+hashlib.sha256(json.dumps([job['id'],job['revision'],args.get('artifact_id'),disposition,args.get('text','')],ensure_ascii=False).encode()).hexdigest()
    with service.store.db() as db:
        old=db.execute('SELECT result FROM commands WHERE key=?',(key,)).fetchone()
    if old:return json.loads(old[0])
    artifact = accept(service, job, args.get('artifact_id'))
    result = {'disposition':disposition, 'accepted':True, 'job_id':job['id'], 'revision':job['revision']}
    if disposition == 'share':
        from .sharing import share
        result['share'] = share(service, service.store.job(job['id']), [artifact], source='k', text=args.get('text',''))
    service.store.event(job['id'], 'completion_decided', {'revision':job['revision'],
        'artifact_id':artifact['id'],'disposition':disposition,'source':'k'})
    service.wake.set()
    outcome={'completion':result, **service.snapshot()}
    with service.store.db() as db:db.execute('INSERT OR IGNORE INTO commands VALUES(?,?)',(key,json.dumps(outcome,ensure_ascii=False)))
    return outcome
