"""Single account observation per quota bucket for concurrent workshop lanes."""
import asyncio
import json
import time
from .protocol import quota_bucket

PREFIX = 'workshop_parallel:'


def records(store):
    with store.db() as db:
        return [(row['key'].removeprefix('account_meter:'), json.loads(row['result']))
                for row in db.execute("SELECT key,result FROM commands WHERE key LIKE 'account_meter:workshop_parallel:%'")]


async def observe(service, *, finishing=None):
    cfg = service.store.settings()
    if cfg['max_parallel'] < 2 and not any(getattr(lane['runner'], '_workshop_parallel_meter', False) for lane in service.pool.active().values()):
        return
    if not hasattr(service, '_parallel_meter_lock'):
        service._parallel_meter_lock = asyncio.Lock()
    async with service._parallel_meter_lock:
        models = set()
        for job_id, lane in service.pool.active().items():
            if job_id != finishing and not lane.get('finishing'):
                models.add(lane['model'])
        stamp = tuple(sorted(models))
        if finishing is None and getattr(service, '_parallel_meter_models', None) == stamp and time.time()-getattr(service, '_parallel_meter_checked', 0) < 15:
            return
        service._parallel_meter_models, service._parallel_meter_checked = stamp, time.time()
        from .account_meter import sample, disable
        from .account_activity import external_active
        if not cfg['desktop_idle_only'] or external_active():
            disable(service.store)
            return
        if not models and not any(row.get('active') for _, row in records(service.store)):
            return
        try:
            await service.runner.start()
            payload = await service.runner.request('account/rateLimits/read', {}, timeout=6)
        except Exception:
            payload = {}
        buckets = {quota_bucket(payload, model)[0]: model for model in models}
        for key, record in records(service.store):
            bucket = key.removeprefix(PREFIX)
            if record.get('active') and bucket not in buckets:
                sample(service.store, key, record['model'], payload, active=False, aggregate=True)
        for bucket, model in buckets.items():
            sample(service.store, PREFIX+bucket, model, payload, aggregate=True)
            with service.store.db() as db:
                key = 'account_meter:'+PREFIX+bucket
                row = json.loads(db.execute('SELECT result FROM commands WHERE key=?', (key,)).fetchone()[0])
                row['model'] = model
                db.execute('UPDATE commands SET result=? WHERE key=?', (json.dumps(row), key))


def summary(store):
    from .account_meter import summary as meter_summary
    return [dict(meter_summary(store, key, aggregate=True), bucket=key.removeprefix(PREFIX))
            for key, _ in records(store)]
