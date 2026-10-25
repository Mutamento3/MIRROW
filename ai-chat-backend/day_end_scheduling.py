"""Process-local exclusion and pending requests for day-end orchestration."""
import asyncio
import inspect
import weakref
from contextlib import asynccontextmanager

_slots = weakref.WeakKeyDictionary()


class WorkActivation:
    """A background scan becomes a visible task only when actual work starts.

    The callback runs synchronously on the owning event loop, before the first
    provider request. Same-name exclusion still belongs to task_slot.
    """
    def __init__(self, label):
        self.metadata = {'label': label, 'purpose': 'memory_recovery'}
        self.started = False
        self.on_start = None

    def start(self, source_date):
        self.metadata['purpose'] = 'memory_recovery'
        self.metadata['source_date'] = source_date
        self.metadata['label'] = f'Memory V2 记忆补漏（{source_date}）'
        self.started = True
        if self.on_start:
            self.on_start()

    def start_continuation(self, source_date):
        self.metadata.update(source_date=source_date, purpose='memory_continuation',
                             label=f'Memory V2 历史事件关联（{source_date}）')
        self.started = True
        if self.on_start:
            self.on_start()


class DayEndAnalysisError(RuntimeError):
    def __init__(self, stages):
        self.failure_stage = 'cognition' if stages == ['cognition'] else 'auxiliary'
        super().__init__('话题分析部分失败，请查看任务与认知维护记录')


async def stop_tracked_tasks(registry):
    """Finish cancellation and receipt cleanup before shared clients close."""
    current = asyncio.current_task()
    tasks = list(dict.fromkeys(task for task in registry.values()
                               if task is not current and not task.done()))
    for task in tasks:
        task.cancel('backend_shutdown')
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def task_slot(name, awaitable):
    """Same-name runs share one UI/cancel slot, so execute them in order."""
    locks = _slots.setdefault(asyncio.get_running_loop(), {})
    lock = locks.setdefault(name, asyncio.Lock())
    acquired = False
    try:
        await lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            lock.release()
        elif inspect.iscoroutine(awaitable):
            awaitable.close()


def merge_ingest_request(previous, trigger, closed_active_date, after_tasks):
    """Keep the latest closed boundary and every prerequisite for one follow-up."""
    previous = previous or {}
    # A later app startup must not turn queued day-end maintenance into a scan.
    if trigger == 'startup' and previous.get('trigger') and previous['trigger'] != 'startup':
        trigger = previous['trigger']
    return {
        'trigger': str(trigger or 'unspecified')[:80],
        'closed_active_date': max(previous.get('closed_active_date', ''), closed_active_date),
        'after_tasks': tuple(dict.fromkeys((*previous.get('after_tasks', ()), *after_tasks))),
    }
