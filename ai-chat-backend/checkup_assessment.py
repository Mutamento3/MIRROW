"""Keep source execution and AI's existing node-review judgment independent."""


def source_status(items):
    states = [item.get('status') for item in items or [] if isinstance(item, dict)]
    if not states:
        return 'not_attempted'
    if all(state == 'success' for state in states):
        return 'success'
    if all(state == 'failure' for state in states):
        return 'failure'
    return 'partial'


def normalize_goal_assessment(value, items):
    """Validate attribution, not whether a scene semantically answers a question."""
    unknown = {'status': 'not_assessed'}
    if not isinstance(value, dict) or value.get('status') not in {'confirmed', 'uncertain'}:
        return unknown
    from context_builder.text_utils import _redact_tool_fact
    reason = _redact_tool_fact(str(value.get('reason') or '')).strip()
    refs = value.get('evidence_sources', [])
    if not reason or not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        return unknown
    known = {str(item.get('tool')): item for item in items or [] if isinstance(item, dict)}
    if any(ref not in known for ref in refs):
        return unknown
    if value['status'] == 'confirmed' and (not refs or any(
        known[ref].get('status') == 'failure' or not known[ref].get('result')
        or known[ref].get('analyzed') is False for ref in refs
    )):
        return unknown
    return {'status': value['status'], 'reason': reason,
            'evidence_sources': list(dict.fromkeys(refs)), 'assessed_by': 'k_node_review'}


def goal_assessment_text(value):
    if not isinstance(value, dict):
        return ''  # Old records have no invented retrospective judgment.
    from context_builder.text_utils import _redact_tool_fact
    status = value.get('status')
    if status == 'not_assessed':
        return '查岗目标：尚无 AI 的目标确认记录。'
    if status not in {'confirmed', 'uncertain'}:
        return ''
    label = '已确认' if status == 'confirmed' else '仍不确定'
    return f'查岗目标（AI 本次复核）：{label}。依据：' + _redact_tool_fact(str(value.get('reason') or ''))
