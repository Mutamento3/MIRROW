"""Append-only attempt diagnostics; no evidence, prompts or exception text."""
import asyncio
import json
import logging
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from llm_usage_scope import usage_metadata_scope, usage_metadata
LOG_PATH = Path(__file__).resolve().parents[1] / 'logs' / 'cognition_attempts.jsonl'
_lock = threading.Lock()
logger = logging.getLogger(__name__)

def identifiers(source_date='', run_id=''):
    values = {}
    if re.fullmatch('\\d{4}-\\d{2}-\\d{2}', source_date or ''):
        values['source_date'] = source_date
    if re.fullmatch('[0-9a-f]{64}', run_id or ''):
        values['cognition_run_id'] = run_id
    return values

def emit(event, **values):
    record = {'ts': datetime.now().astimezone().isoformat(), 'event': event, **usage_metadata(), **values}
    try:
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        logger.warning('Cognition attempt diagnostics unavailable')

@contextmanager
def lane_attempt(source_date, run_id, lane):
    with usage_metadata_scope(**identifiers(source_date, run_id), cognition_daily_run_id=run_id, cognition_lane=lane, cognition_attempt_id=uuid.uuid4().hex):
        emit('lane_started')
        try:
            yield
        except asyncio.CancelledError:
            emit('lane_interrupted')
            raise

async def call_request(prompt, flash, **kwargs):
    _, messages = prompt.wire()
    with usage_metadata_scope(**identifiers(prompt.source_date, prompt.run_id), **prompt.transport_metadata(), cognition_call_id=uuid.uuid4().hex):
        from .provider_contract import require_complete, require_json_object, IncompleteOutputError
        from .autonomous import safe_failure_code
        from .prompt_request import digest
        output_failure_phases = []
        attempt_limit = getattr(prompt, 'output_attempt_limit', 2)
        for attempt in range(attempt_limit):
            response = {}
            request_hash = digest(json.dumps(messages, ensure_ascii=False))
            emit('model_started', output_attempt=attempt + 1, max_tokens=kwargs.get('max_tokens'), cognition_request_hash=request_hash)
            try:
                with usage_metadata_scope(cognition_request_hash=request_hash):
                    response = await flash(messages, **kwargs)
                require_complete(response)
                response = require_json_object(response, empty_array_key=None)
            except asyncio.CancelledError:
                emit('model_interrupted')
                raise
            except (json.JSONDecodeError, IncompleteOutputError) as exc:
                response = getattr(exc, 'response', response)
                if getattr(exc, 'routing_phase', None):
                    output_failure_phases.append(exc.routing_phase)
                code = safe_failure_code(exc)
                emit('model_failed', error_code=code, output_attempt=attempt + 1, output_chars=len(response.get('content') or ''), json_error_position=exc.pos if isinstance(exc, json.JSONDecodeError) else None, cognition_request_hash=request_hash)
                if attempt + 1 >= attempt_limit:
                    exc.response = response
                    raise
                feedback = '上次输出未完成。本次在输出额度内组织完整 JSON 对象，保留合同所需事实、来源引用和字段。' if isinstance(exc, IncompleteOutputError) else '上次输出不是有效 JSON 对象。本次返回可被 JSON 解析器读取的完整对象；字符串中的引号、换行和反斜线使用 JSON 转义。'
                messages = [*messages[:-1], {**messages[-1], 'content': messages[-1]['content'] + '\n' + feedback}]
            except Exception as exc:
                emit('model_failed', error_code=safe_failure_code(exc))
                raise
            else:
                from api_sources.scope import phase_status
                for route, phase_id in output_failure_phases:
                    phase_status(route, 'ok', phase_id)
                emit('model_returned', output_attempt=attempt + 1)
                return response
