"""One durable model attempt: save the raw return before any domain filtering."""
import asyncio
from . import books
from .prompt_request import digest
from .provider_contract import not_dispatched
import json


async def capture(state, key, prompt, llm, checkpoint):
    binding = digest(json.dumps(prompt.wire(), ensure_ascii=False, sort_keys=True))
    previous = state.get(key)
    if previous is not None:
        if previous.get('binding') != binding:
            raise books.BookError('保存输出的来源或合同变化，保留原件等待核查')
        if previous.get('status') == 'returned' and 'raw' in previous:
            return previous['raw']
        if previous.get('status') != 'not_dispatched':
            raise books.BookError('已有调用未保存返回，不能自动重复付费')
    attempt = {'status': 'started', 'binding': binding}
    state[key] = attempt
    checkpoint()
    prompt.output_attempt_limit = 1
    try:
        raw = await llm(prompt)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        attempt.update(status='not_dispatched' if not_dispatched(exc) else 'failed',
                       error_type=type(exc).__name__)
        response = getattr(exc, 'response', None)
        if isinstance(response, dict) and isinstance(response.get('content'), str):
            attempt['raw'] = response['content']
            attempt['finish_reason'] = response.get('finish_reason')
        if getattr(exc, 'routing_phase', None):
            attempt['routing_phase'] = list(exc.routing_phase)
        checkpoint()
        raise
    attempt.update(status='returned', raw=raw)
    checkpoint()
    return raw
