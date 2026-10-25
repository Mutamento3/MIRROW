"""Bounded independent app-server turns; one active lane per work order."""
import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)


class ReviewContext:
    def __init__(self, owner, runner, job_id):
        self.owner, self.runner, self.reviewing_job = owner, runner, job_id

    def __getattr__(self, name):
        return getattr(self.owner, name)


class ExecutionPool:
    def __init__(self, owner, factory):
        self.owner, self.factory = owner, factory
        self.lanes = {}

    def active(self):
        return {key: lane for key, lane in self.lanes.items() if not lane['task'].done()}

    def snapshot(self):
        return {'capacity': self.owner.store.settings()['max_parallel'],
                'running': [{'job_id': key, 'kind': lane['kind']} for key, lane in self.active().items()]}

    def available(self):
        return len(self.active()) < self.owner.store.settings()['max_parallel']

    def launch(self, job_id, kind, operation, *, model=None):
        if job_id in self.active() or not self.available():
            raise ValueError('execution_slot_unavailable')
        runner = self.factory()
        runner._workshop_parallel_meter = self.owner.store.settings()['max_parallel'] > 1
        lane = {'runner': runner, 'kind': kind, 'model': model or self.owner.store.job(job_id)['model'] or self.owner.store.settings()['model']}
        self.lanes[job_id] = lane
        lane['task'] = asyncio.create_task(self._run(job_id, lane, operation))

    async def _run(self, job_id, lane, operation):
        try:
            from .parallel_meter import observe
            await observe(self.owner)
            await operation(lane['runner'])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('workshop lane failed: %s', job_id)
            job = self.owner.store.job(job_id, include_deleted=True)
            if lane['kind']=='production' and job['state'] in ('discussing','executing','stopping'):
                self.owner.store.update(job_id, state='recovery_required', resume_state=job['state'], reason='execution_lane_failed')
                self.owner.store.event(job_id, 'failed', {'stage':job['state'], 'reason':'execution_lane_failed'})
        finally:
            lane['finishing'] = True
            from .parallel_meter import observe
            with contextlib.suppress(Exception):
                await observe(self.owner, finishing=job_id)
            with contextlib.suppress(Exception):
                await lane['runner'].close()
            self.owner.wake.set()

    async def interrupt(self, job_id):
        lane = self.active().get(job_id)
        if lane:
            await lane['runner'].interrupt()

    async def close(self):
        lanes = list(self.active().values())
        await asyncio.gather(*(lane['runner'].interrupt() for lane in lanes), return_exceptions=True)
        tasks = [lane['task'] for lane in lanes]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=10)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def reap(self):
        self.lanes = self.active()
