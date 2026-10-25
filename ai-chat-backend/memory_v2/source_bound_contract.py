"""Show the encoder the same per-batch actor eligibility as local validation.

This only specializes the output contract; it does not invent source facts,
classify episodes, or relax the authoritative parser.
"""
import json

from .encoding import build_encoding_json_schema, _is_human_action_source, _is_k_action_source


def actor_refs(messages):
    return {
        actor: [f's{i + 1}' for i, message in enumerate(messages) if eligible(message)]
        for actor, eligible in (('human', _is_human_action_source), ('k', _is_k_action_source))
    }


def encoding_schema(messages):
    schema = build_encoding_json_schema()
    item = schema['properties']['episodes']['items']
    conditions = []
    for actor, refs in actor_refs(messages).items():
        condition = {'properties': {'primary_subject_id': {'const': actor}},
                     'required': ['primary_subject_id']}
        if refs:
            conditions.append({'if': condition, 'then': {'properties': {
                'source_refs': {'contains': {'enum': refs}}}}})
        else:
            conditions.append({'not': condition})
    item['allOf'] = conditions
    return schema


def contract_context(messages):
    return (
        '\nBatch-local person-action evidence (exact s-refs):\n'
        + json.dumps(actor_refs(messages), separators=(',', ':'))
        + '\nEach focal person occurrence needs one of its own listed refs in that '
        'same episode, supporting that occurrence. A person with an empty list '
        'has no action evidence in this batch. Domain lifecycle/progress and '
        'generated summaries can remain non_event_source_refs when they supply '
        'background rather than an independently sourced occurrence; all such '
        'rows remain in the original record. Other explicitly sourced actors or '
        'matching resource receipts may still form episodes in their own right. '
        'A mentioned person or an unrelated eligible row is not action evidence.\n'
    )
