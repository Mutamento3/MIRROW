"""Batch incoming mentions without an LLM; the host supplies idle admission.

Notifications stay unread until actual source content is read. A completed
attention attempt is not repeated merely because the AI chose to leave.
"""
import asyncio
import hashlib
import json
import logging
import time

logger = logging.getLogger(__name__)


class MentionWatcher:
    def __init__(self, collect, dispatch, *, clock=time.monotonic, quiet_seconds=30, max_wait=120):
        self.collect, self.dispatch, self.clock = collect, dispatch, clock
        self.quiet_seconds, self.max_wait = quiet_seconds, max_wait
        self.seen = set()
        self.pending = {}
        self.first_at = self.changed_at = None
        self.retry_at = 0

    async def poll(self):
        result = await self.collect()
        # Scope IDs to the source home; identical remote IDs are distinct.
        current = {json.dumps([row.get('site_id',''), row['id']]): row
                   for row in result.get('items', []) if row.get('kind') == 'mention' and row.get('id')}
        now = self.clock()
        fresh = {key: row for key, row in current.items() if key not in self.seen}
        # Keep pending rows from temporarily unreachable sources. Removed/read
        # rows from successfully fetched sources no longer need attention.
        unavailable = {row['site_id'] for row in result.get('unavailable', [])
                       if row.get('status') != 'limited'}
        self.pending = {key: row for key,row in self.pending.items()
                        if key in fresh or row.get('site_id','') in unavailable}
        additions = fresh.keys() - self.pending.keys()
        if additions:
            self.pending.update(fresh)
            self.changed_at = now
            if self.first_at is None: self.first_at = now
        if not self.pending:
            self.first_at = self.changed_at = None
            return 'empty'
        if now < self.retry_at or (now-self.changed_at < self.quiet_seconds and now-self.first_at < self.max_wait):
            return 'waiting'
        # Retry only reachable sources; no stale source fallback to another home.
        rows = [row for row in self.pending.values() if row.get('site_id','') not in unavailable]
        if not rows: return 'unavailable'
        keys = sorted(json.dumps([row.get('site_id',''),row['id']]) for row in rows)
        batch_id = hashlib.sha256(('social-mentions:'+json.dumps(keys)).encode()).hexdigest()
        outcome = await self.dispatch(rows, batch_id)
        status = outcome.get('status') if isinstance(outcome,dict) else ''
        if status in {'success','partial_success','already_completed'}:
            handled = {json.dumps([row.get('site_id',''),row['id']])
                       for row in outcome.get('handled_mentions',[]) if row.get('id')}
            handled.intersection_update(keys)
            if not handled:
                self.retry_at = now+300
                return 'no_progress'
            self.seen.update(handled)
            for key in handled: self.pending.pop(key,None)
            # Bounded memory; durable host run identity remains the restart guard.
            if len(self.seen)>4096: self.seen=set(sorted(self.seen)[-2048:])
            if not self.pending: self.first_at = self.changed_at = None
            return 'dispatched'
        if status not in {'busy','already_running'}: self.retry_at = now+300
        return status or 'failed'

    async def run(self):
        while True:
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('social mention scan unavailable: %s', type(exc).__name__)
            await asyncio.sleep(30)
