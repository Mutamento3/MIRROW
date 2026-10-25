"""One persisted output-repair budget, shared by syntax and domain validation."""
import asyncio
import copy
import json
from . import books
from .prompt_request import CognitionPrompt, digest
from .provider_contract import IncompleteOutputError, require_json_object, not_dispatched


class ContractError(books.BookError):
    def __init__(self, code, message, *, issues=()):
        super().__init__(message)
        self.code = code
        self.issues = list(issues)
        self.stage = ''


def diagnostic(exc):
    return {'code': exc.code, 'stage': exc.stage, 'issues': exc.issues,
            'message': str(exc)}


def parse(raw, *, empty_array_key=None):
    if isinstance(raw, dict) and 'content' not in raw:
        raw = json.dumps(raw, ensure_ascii=False)
    response = raw if isinstance(raw, dict) else {'content': raw}
    return json.loads(require_json_object(response, empty_array_key=empty_array_key)['content'])


def repair_prompt(prompt, raw, error):
    from model_provenance import PROVENANCE_GUIDE
    payload = json.loads(prompt.payload_json)
    payload['output_repair'] = {'previous_output': raw, 'validation': diagnostic(error)}
    return CognitionPrompt(
        prompt.contract.removesuffix('\n' + PROVENANCE_GUIDE) +
        '\n上次输出未通过所列校验。这是本阶段唯一一次输出修复。'
        'output_repair 是失败输出及程序诊断，不是新的来源证据。'
        '保留完整来源和候选编号，针对诊断修正并返回本阶段完整 JSON。'
        '已有合格条目原样保留；真实证据不足仍如实拒绝或暂不入书，不为通过校验编造依据。',
        payload, stage=prompt.stage, source_date=prompt.source_date, run_id=prompt.run_id,
        shared_material_keys=prompt.shared_material_keys,
        preferred_thinking=prompt.preferred_thinking)


def captured_attempt(prompt, captured):
    """Import one already-paid capture without changing its original receipt."""
    expected = digest(json.dumps(prompt.wire(), ensure_ascii=False, sort_keys=True))
    if captured.get('binding') != expected:
        raise books.BookError('保存输出的来源或合同变化，保留原件等待核查')
    status = captured.get('status')
    if status == 'not_dispatched':
        return None
    returned = status == 'returned' and 'raw' in captured
    output_error = (status == 'failed' and isinstance(captured.get('raw'), str)
                    and captured.get('finish_reason') in {None, 'stop', 'length'}
                    and captured.get('error_type') in {
                        'ManagedJSONError', 'JSONDecodeError', 'IncompleteOutputError'})
    if not returned and not output_error:
        raise books.BookError('上次调用没有可恢复的确定输出，保留原件等待核查')
    attempt = {'status': 'returned', 'raw': copy.deepcopy(captured['raw']),
               'origin': 'saved_capture', 'capture_binding': expected}
    if captured.get('finish_reason') == 'length' or captured.get('error_type') == 'IncompleteOutputError':
        attempt['incomplete'] = True
    if captured.get('routing_phase'):
        attempt['routing_phase'] = list(captured['routing_phase'])
    return attempt


async def run(prompt, llm, validate, *, binding=None, checkpoint_date=None, captured=None):
    """Save before validation; repeat invocations reuse outputs and the two-attempt limit.

    Provider/network routing retains its own policy. Only explicitly typed output
    errors get a repair; an unknown interrupted request is never silently replayed.
    """
    from .diagnostics import emit
    # Derived book projections are bound to book/contract revisions, not the
    # date of a daily or startup caller. Audit still retains prompt.source_date.
    identity = {'stage': prompt.stage, 'source_date': prompt.source_date if checkpoint_date is None else checkpoint_date,
                'run_id': prompt.run_id, 'binding': binding,
                'request': digest(json.dumps(prompt.wire(), ensure_ascii=False))}
    path = books.ROOT / 'data/cognition_maintenance/output_attempts' / (
        digest(json.dumps(identity, sort_keys=True)) + '.json')
    exists = path.exists()
    imported = captured_attempt(prompt, captured) if captured is not None else None
    state = json.loads(path.read_text('utf-8')) if exists else {
        'identity': identity, 'attempts': [imported] if imported is not None else []}
    if state.get('identity') != identity or not isinstance(state.get('attempts'), list):
        raise books.BookError('输出检查点来源不匹配，保留原件')

    def save():
        books.atomic_text(path, json.dumps(state, ensure_ascii=False))

    def check(attempt):
        try:
            if attempt.get('incomplete'):
                raise IncompleteOutputError()
            return validate(copy.deepcopy(attempt['raw'])), None
        except (ContractError, json.JSONDecodeError, IncompleteOutputError) as exc:
            if not isinstance(exc, ContractError):
                exc = ContractError('output_truncated' if isinstance(exc, IncompleteOutputError) else 'invalid_json',
                                    '输出未完成' if isinstance(exc, IncompleteOutputError) else '输出不是完整 JSON 对象')
            exc.stage = prompt.stage
            attempt.update(status='invalid', error=diagnostic(exc))
            save()
            emit('output_validation_failed', **diagnostic(exc),
                 output_attempt=sum(a['status'] != 'not_dispatched' for a in state['attempts']))
            return None, exc

    error = None
    raw = None
    # Revalidation also restores a domain validator's good-item locks on resume.
    for attempt in state['attempts']:
        if (attempt['status'] in {'started', 'result_unknown', 'transport_error'}
                and 'raw' not in attempt):
            exc = ContractError('output_result_unknown', '上次调用中断且未保存结果，需核查后恢复')
            exc.stage = prompt.stage
            raise exc
        if 'raw' in attempt:
            raw = attempt['raw']
            result, error = check(attempt)
            if error is None:
                return result

    while sum(a['status'] != 'not_dispatched' for a in state['attempts']) < 2:
        request = repair_prompt(prompt, raw, error) if error else prompt
        # The existing transport helper must not add its own second attempt.
        request.output_attempt_limit = 1
        attempt = {'status': 'started'}
        state['attempts'].append(attempt)
        save()
        try:
            raw = await llm(request)
        except (json.JSONDecodeError, IncompleteOutputError) as exc:
            raw = getattr(exc, 'response', {}).get('content', '')
            attempt['incomplete'] = isinstance(exc, IncompleteOutputError)
            if getattr(exc, 'routing_phase', None):
                attempt['routing_phase'] = list(exc.routing_phase)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempt.update(status='not_dispatched' if not_dispatched(exc) else 'result_unknown',
                           error_type=type(exc).__name__)
            save()
            raise
        attempt.update(status='returned', raw=raw)
        save()
        result, error = check(attempt)
        if error is None:
            attempt['status'] = 'validated'
            save()
            from api_sources.scope import phase_status
            for prior in state['attempts']:
                if prior.get('routing_phase'):
                    route, phase_id = prior['routing_phase']
                    phase_status(route, 'ok', phase_id)
            return result
    if error is not None:
        raise error
    exc = ContractError('output_retry_exhausted', '本阶段调用预算已用尽，保留检查点等待核查')
    exc.stage = prompt.stage
    raise exc
