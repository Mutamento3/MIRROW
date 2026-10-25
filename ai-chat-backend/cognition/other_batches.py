"""Deterministic whole-statement batches with resumable day coverage receipts."""
import asyncio
import json
from . import books, maintenance

MAX_MESSAGES = 120
MAX_CHARS = 80_000
_lock = asyncio.Lock()


def partition(evidence):
    unique = {}
    for item in evidence:
        key = str(item['id'])
        if key in unique and unique[key] != item:
            raise ValueError(f'Conflicting other-book source: {key}')
        unique[key] = item
    ordered = sorted(unique.values(), key=lambda e: (str(e.get('timestamp') or ''), str(e['id'])))
    batches, batch, chars = [], [], 0
    for item in ordered:
        size = len(str(item.get('text') or ''))
        if batch and (len(batch) >= MAX_MESSAGES or chars + size > MAX_CHARS):
            batches.append(batch)
            batch, chars = [], 0
        # A single long statement remains whole; provider errors remain visible.
        batch.append(item)
        chars += size
    if batch:
        batches.append(batch)
    return batches


async def run(batches, run_single, *, source_date, session_id, progress_cb=None):
    rid = maintenance.digest({'pipeline': 'other-day-batches-v1', 'date': source_date,
                              'session': session_id, 'batches': batches})
    path = maintenance.folder() / 'other_day_runs' / (rid + '.json')
    async with _lock:
        state = json.loads(path.read_text('utf-8')) if path.exists() else {
            'id': rid, 'source_date': source_date, 'session_id': session_id,
            'batches': [{'source_ids': [e['id'] for e in b], 'digest': maintenance.digest(b)} for b in batches],
            'completed_batches': 0, 'items': [], 'rejected_proposal_count': 0,
            'validation_rejection_count': 0, 'review_rejection_count': 0,
        }
        if state.get('status') in {'completed', 'completed_with_rejections'}:
            return state
        def save():
            books.atomic_text(path, json.dumps(state, ensure_ascii=False))
        state.update(status='running')
        state.pop('error_type', None)
        save()
        try:
            for index in range(state['completed_batches'], len(batches)):
                if progress_cb:
                    progress_cb()
                # Earlier commits are visible in the next analysis's book snapshot.
                # One full prior turn accompanies references across a batch boundary.
                material = (batches[index - 1][-1:] if index else []) + batches[index]
                result = await run_single(material)
                if result.get('status') not in {'completed', 'completed_with_rejections'}:
                    raise RuntimeError('Other-book batch did not complete')
                state['items'].extend(result.get('items', []))
                state['rejected_proposal_count'] += result.get('rejected_proposal_count', 0)
                for key in ('validation_rejection_count', 'review_rejection_count', 'rejected_topic_annotation_count'):
                    state[key] = state.get(key, 0) + result.get(key, 0)
                state['completed_batches'] = index + 1
                save()
            state['status'] = 'completed_with_rejections' if state['rejected_proposal_count'] or state.get('rejected_topic_annotation_count') else 'completed'
            save()
            return state
        except BaseException as exc:
            state.update(status='error', error_type=type(exc).__name__)
            save()
            raise
