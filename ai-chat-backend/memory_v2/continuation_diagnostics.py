"""Fixed, text-free observations of rejected continuation JSON.

These receipts cannot be replayed as judgments and contain no model prose,
unknown keys, source references, prompt or hidden reasoning.
"""
import json

CHECKS = {'non_json', 'root_type', 'root_fields', 'judgments_type', 'judgment_contract'}
ROOT_TYPES = {'object', 'array', 'scalar', 'non_json'}
JUDGMENT_TYPES = {'array', 'object', 'string', 'other', 'missing'}
COUNT_FIELDS = {'unexpected_root_field_count', 'judgment_count',
                'non_object_judgment_count', 'judgment_field_mismatch_count'}
BOOL_FIELDS = {'has_thread_judgments', 'has_day_compact', 'has_scene_groups'}


def inspect_rejected_output(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {'check': 'non_json', 'root_type': 'non_json'}
    if not isinstance(raw, dict):
        return {'check': 'root_type', 'root_type': 'array' if isinstance(raw, list) else 'scalar'}
    rows = raw.get('thread_judgments')
    kind = ('missing' if 'thread_judgments' not in raw else 'array' if isinstance(rows, list)
            else 'object' if isinstance(rows, dict) else 'string' if isinstance(rows, str) else 'other')
    result = {'check': 'root_fields' if set(raw) != {'thread_judgments'}
              else 'judgments_type' if kind != 'array' else 'judgment_contract',
              'root_type': 'object', 'judgments_type': kind,
              'unexpected_root_field_count': len(set(raw) - {'thread_judgments'}),
              **{'has_' + key: key in raw for key in ('thread_judgments', 'day_compact', 'scene_groups')}}
    if isinstance(rows, list):
        fields = {'candidate_ref', 'outcome', 'confidence', 'reason_codes'}
        result.update(judgment_count=len(rows),
                      non_object_judgment_count=sum(not isinstance(row, dict) for row in rows),
                      judgment_field_mismatch_count=sum(isinstance(row, dict) and set(row) != fields for row in rows))
    return project(result)


def project(value):
    """Only fixed enums, bounded counts and booleans may enter a saved receipt."""
    if not isinstance(value, dict) or not isinstance(value.get('check'), str) or value['check'] not in CHECKS:
        return {}
    result = {'check': value['check']}
    for key, allowed in (('root_type', ROOT_TYPES), ('judgments_type', JUDGMENT_TYPES)):
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    for key in COUNT_FIELDS:
        if type(value.get(key)) is int and value[key] >= 0:
            result[key] = min(value[key], 1_000_000)
    for key in BOOL_FIELDS:
        if type(value.get(key)) is bool:
            result[key] = value[key]
    return result
