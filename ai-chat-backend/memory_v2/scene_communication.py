"""Source-bound communication summaries for scene context, not memory events."""
from copy import deepcopy


GUIDANCE = (
    '另外用 communication_summaries 简述双方各条消息实际传达的内容，保留说话人、建议、'
    '更正、承诺和未回应的事。每项 source_ref 对应一条消息；只有摘要已承接其通信内容才列入，'
    '仍需看原话的消息不列入。消息是不是长期事件，与这里是否保留通信是两回事。'
)


def eligible(message):
    return message.role in {'user', 'assistant'} and (
        not message.event_type or message.source_kind in {'wander', 'sentinel', 'reminder'})


def extend_schema(schema, messages):
    schema = deepcopy(schema)
    refs = [f's{i}' for i, row in enumerate(messages, 1) if eligible(row)]
    schema['properties']['communication_summaries'] = {
        'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'required': ['source_ref', 'summary'], 'properties': {
                'source_ref': {'type': 'string', 'enum': refs} if refs else {'type': 'string'},
                'summary': {'type': 'string', 'minLength': 1}}}}
    if not refs:
        schema['properties']['communication_summaries']['maxItems'] = 0
    schema['required'] = [*schema['required'], 'communication_summaries']
    return schema


def parse_summaries(items, messages):
    from .encoding import EncodingContractError
    by_ref = {f's{i}': row for i, row in enumerate(messages, 1) if eligible(row)}
    if not isinstance(items, list):
        raise EncodingContractError('scene_communication_contract')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'source_ref', 'summary'}:
            raise EncodingContractError('scene_communication_contract')
        ref, summary = item['source_ref'], item['summary']
        if (not isinstance(ref, str) or ref not in by_ref or ref in seen
                or not isinstance(summary, str) or not summary.strip()):
            raise EncodingContractError('scene_communication_source')
        seen.add(ref)
        result.append({'message_id': by_ref[ref].message_id, 'summary': summary.strip()})
    return tuple(result)


def select_summaries(items, message_ids):
    """Keep the original prose and identity when a scene is split or regrouped."""
    selected = set(message_ids)
    return [item for item in items if item['message_id'] in selected]


def merge_summaries(items):
    """A shard/rewrite may add prose but must not replace earlier source notes."""
    grouped = {}
    for item in items:
        parts = grouped.setdefault(item['message_id'], [])
        for line in item['summary'].split('\n'):
            if line not in parts:
                parts.append(line)
    return tuple({'message_id': mid, 'summary': '\n'.join(parts)} for mid, parts in grouped.items())
