"""Bounded correction of derived JSON; invalid paid candidates remain auditable."""
import json
from .scene_reconstruction_contract import identity

FORMAT_CODES = {'scene_regroup_contract', 'scene_regroup_fields', 'scene_regroup_partition',
                'scene_regroup_explicit_boundary', 'scene_regroup_incomplete', 'scene_regroup_summary_budget'}


def projection(raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict) or not isinstance(value.get('segments'), list):
        return {}
    return {'segments': [{key: part[key] for key in ('end_segment', 'summary') if key in part}
                         if isinstance(part, dict) else None for part in value['segments']]}


async def validated_output(*, receipt, key, refs, source, messages, call, schema,
                           max_tokens, report, validate, explicit_retry, max_requests):
    prior = receipt.step(key, refs, source)
    feedback = ''
    if prior is not None:
        try:
            return validate(prior)
        except ValueError as exc:
            if str(exc) not in FORMAT_CODES or not explicit_retry:
                raise
            receipt.save_step(identity('regroup-rejected', refs, [key, prior]), refs, prior, source)
            feedback = str(exc)
    if max_requests is not None and report['request_count'] >= max_requests:
        return None
    if not receipt.claim(key, explicit_retry=explicit_retry):
        raise ValueError('scene_reconstruction_retry_backoff')
    for attempt in range(2):
        if max_requests is not None and report['request_count'] >= max_requests:
            return None
        corrective = [{'role': 'user', 'content':
            '上次派生结果未通过输出合同：' + feedback + '。根据完整原材料重新生成；'
            '每段末编号严格递增并完整覆盖所有片段，不跨 boundary_block，'
            'summary 为字符串，所有 summary 的总字符数在给定 summary_character_budget 内。'}] if feedback else []
        report['request_count'] += 1
        raw, usage = await call(messages=[*messages, *corrective], schema=schema,
                               schema_name='memory_v2_scene_regroup', max_tokens=max_tokens)
        report['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
        report['completion_tokens'] += int(usage.get('completion_tokens') or 0)
        try:
            value = projection(raw)
        except json.JSONDecodeError:
            value = {}  # Failed provider prose cannot enter a derived source view.
        # Keep an invalid candidate for zero-call diagnosis and explicit recovery.
        receipt.save_step(key, refs, value, source)
        try:
            return validate(value)
        except ValueError as exc:
            if str(exc) not in FORMAT_CODES:
                raise
            receipt.save_step(identity('regroup-rejected', refs, [key, value]), refs, value, source)
            if attempt:
                raise
            feedback = str(exc)
