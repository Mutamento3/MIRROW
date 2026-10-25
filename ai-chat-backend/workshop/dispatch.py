"""Admit production and independent review into the shared execution pool."""
from .execution_pool import ReviewContext


async def tick(service):
    from .quality import candidate, enqueue, execute, admission
    from .quality_wait import waiting
    from .progress import current_artifacts
    service.pool.reap()
    cfg = service.store.settings()
    for job in service.store.jobs():
        if job['state'] == 'awaiting_review':
            for artifact in current_artifacts(service.store, job):
                enqueue(service, artifact)
            from .sharing import auto_share
            auto_share(service,job)
        elif job['state'] == 'accepted':
            # AI can accept between a finished review and the next dispatch tick.
            # Presentation archival must not cancel the idempotent delivery outbox.
            from .sharing import auto_share
            auto_share(service,job)
    while cfg['enabled'] and service.pool.available():
        active = service.pool.active()
        review = candidate(service, active)
        if cfg['desktop_idle_only'] and not service.desktop_activity['ready']:
            if review:
                waiting(service.store, review, '桌面 Codex 正在执行任务，质检等候空闲' if service.desktop_activity['status']=='busy' else '桌面运行状态待核对，质检等候')
            break
        if review:
            reason = await admission(service, review)
            if reason:
                waiting(service.store, review, reason)
            else:
                cfg = service.store.settings()
                if not cfg['enabled'] or not service.pool.available() or cfg['desktop_idle_only'] and not service.desktop_activity['ready']:
                    break
                fresh = candidate(service, service.pool.active())
                if not fresh or fresh.get('id', fresh['job_id']) != review.get('id', review['job_id']):
                    continue
                service.pool.launch(review['job_id'], 'review',
                    lambda runner, row=review: execute(ReviewContext(service, runner, row['job_id']), row), model=review.get('model') or cfg['review_model'])
                continue
        jobs = service.store.jobs()
        if not any(row['state'] in ('queued', 'account_paused') and row['id'] not in active for row in jobs):
            break
        head = next((row for row in jobs if row['state'] in ('queued','account_paused') and row['id'] not in active), None)
        await service.refresh((head or {}).get('model') or cfg['model'])
        cfg = service.store.settings()
        if not cfg['enabled'] or service.quota['remaining'] is None or service.quota['remaining'] < cfg['remaining_floor']:
            break
        # Settings may change during the metadata RPC. Recheck all admission facts.
        if not service.pool.available() or cfg['desktop_idle_only'] and not service.desktop_activity['ready']:
            break
        for row in jobs:
            if row['state'] == 'account_paused' and row['id'] not in active:
                service.store.update(row['id'], state='queued', reason='')
        head = next((row for row in service.store.jobs() if row['state']=='queued' and row['id'] not in active), None)
        if not head or (head['model'] or cfg['model']) not in service.quota['models']:
            break
        job = service.store.claim(cfg, active)
        if not job:
            break
        service.pool.launch(job['id'], 'production', lambda runner, row=job: service.execute(row, runner=runner))
    from .parallel_meter import observe
    await observe(service)
