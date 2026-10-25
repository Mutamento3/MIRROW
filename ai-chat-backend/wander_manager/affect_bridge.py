"""Project real persisted Wander settlement evidence into independent Affect."""
import asyncio
from datetime import datetime
import json
import logging

from affect.feeling_contract import normalize_wander_feelings
from affect.sidecar import OPEN, CLOSE, parse_affect_sidecar

logger = logging.getLogger(__name__)


async def persist_settlement_emotion(store, activity_id: str, *, service=None) -> None:
    """Optional observation after settlement; never replay or reopen an activity."""
    try:
        activity = store.get_activity(activity_id)
        if not activity or activity.get('state') not in {'completed', 'interrupted', 'aborted'}:
            return
        reason = str(activity.get('settlement_reason') or '')
        if not reason or reason == 'execution_error' or reason.startswith('not_started'):
            return
        run = store.get_run(str(activity.get('run_id') or ''))
        if not run or run.get('run_id') != activity.get('run_id'):
            return
        ended_at = activity.get('ended_at')
        if not isinstance(ended_at, str) or not ended_at:
            return
        datetime.fromisoformat(ended_at.replace('Z', '+00:00'))
        effect = activity.get('emotion_effect')
        if not isinstance(effect, dict) or effect.get('changed') is not True:
            return
        normalized = normalize_wander_feelings(effect)
        if not normalized:
            logger.warning('wander affect rejected: invalid_effect activity=%s', activity_id)
            return
        payload = {key: normalized[key] for key in ('family', 'intensity', 'target', 'persistence')}
        if 'operation' in effect:
            payload['operation'] = effect['operation']
        payload.update(operation=payload.get('operation', 'add'),
                       feeling=normalized['to_emotion'], reason=normalized['delta'])
        parsed = parse_affect_sidecar(OPEN + json.dumps(payload, ensure_ascii=False) + CLOSE)
        if not parsed.accepted:
            logger.warning('wander affect rejected: invalid_contract activity=%s', activity_id)
            return
        if service is None:
            from affect import get_affect_service
            service = get_affect_service()
        result = await asyncio.to_thread(
            service.submit_self_sidecar, source_type='wander_settlement', source_id=activity_id,
            self_state=parsed.payload, reason=normalized['delta'],
            session_id=str(run.get('session_id') or ''), occurred_at=ended_at,
        )
        if not isinstance(result, dict) or result.get('accepted') is not True:
            logger.warning('wander affect submission not accepted activity=%s', activity_id)
    except Exception as exc:
        logger.warning('wander affect submission failed activity=%s error_type=%s', activity_id, type(exc).__name__)
