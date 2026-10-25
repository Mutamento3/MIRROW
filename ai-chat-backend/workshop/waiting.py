"""A bounded local wait for an explicit AI choice; no model polling or timer job."""
from __future__ import annotations

import asyncio
import time

MAX_WAIT_SECONDS = 45


async def reply(service, job_id, *, timeout=MAX_WAIT_SECONDS):
    started = time.monotonic()
    deadline = started + min(MAX_WAIT_SECONDS, max(0, timeout))
    status, reasons = 'timeout', []
    service.store.event(job_id, 'K_wait_requested', {'source': '本次 AI 结构化行动选择', 'max_seconds': min(MAX_WAIT_SECONDS, max(0, timeout))})
    try:
        while True:
            job = service.store.job(job_id)
            if job['state'] in ('awaiting_confirmation', 'awaiting_input', 'awaiting_review'):
                status = 'ready'
                break
            if job['state'] not in ('queued', 'discussing'):
                status, reasons = 'blocked', [job['reason'] or job['state']]
                break
            if job['state'] == 'queued':
                snapshot = service.snapshot()
                row = next((row for row in snapshot['jobs'] if row['id'] == job_id), None)
                reasons = (row or {}).get('waiting_reasons', [])
                if reasons or row is None or (row.get('position') or 0) > 1:
                    status = 'blocked'
                    reasons = reasons or ['前面还有排队工单']
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(.2, remaining))
        return {'status': status, 'reasons': reasons, 'room': service.store.room(job_id),
                'mehuman': '仅等待执行器返回，方案仍由 AI 阅读、讨论和结构化确认；超时或排队则留待下次来访。'}
    except asyncio.CancelledError:
        status = 'interrupted'
        raise
    finally:
        service.store.event(job_id, 'K_wait_finished', {'status': status, 'seconds': round(time.monotonic()-started, 2), 'reasons': reasons})
