"""Pure source-backed projection of frozen workshop cards."""
import json
import re


def _status_cards(row):
    """Match the fixed producer receipt, independently of mutable side stores."""
    if row.get('role') != 'event' or row.get('event_type') != 'workshop_event':
        return []
    calls = row.get('tool_calls') or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            return []
    cards = []
    for call in calls if isinstance(calls, list) else []:
        extra = call.get('extra_data') if isinstance(call, dict) else None
        card = extra.get('workshop') if isinstance(extra, dict) else None
        if (isinstance(card, dict) and call.get('tool') == 'workshop'
                and isinstance(card.get('receipt_id'), str) and card['receipt_id']
                and row.get('message_id') == 'notification:workshop_event:' + card['receipt_id']
                and isinstance(card.get('job_id'), str)
                and re.fullmatch(r'[0-9a-f]{32}', card['job_id'])):
            cards.append(card)
    return cards


def is_technical_status(row):
    """Recognize frozen budget telemetry, without inferring behavior from prose."""
    cards = _status_cards(row)
    return bool(cards) and all(card.get('kind') in
                              {'budget_calibration', 'budget_paused', 'usage_reconciled'} for card in cards)


def resource_subject(row):
    """Identify a recorded job outcome, which does not establish a person action."""
    subjects = ['workshop_job:' + card['job_id'] for card in _status_cards(row)
                if card.get('kind') == 'failed']
    return subjects[0] if subjects and len(set(subjects)) == 1 else ''


def action_actors(row):
    """Actor evidence from this producer's immutable invitation visit card."""
    if row.get('role') != 'event' or row.get('event_type') != 'workshop_event':
        return ()
    if not str(row.get('message_id') or '').startswith('notification:workshop_event:'):
        return ()
    from .action_evidence import card_actors
    for card in _status_cards(row):
        actors = card_actors(card)
        if actors:
            return actors
    calls = row.get('tool_calls') or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            return ()
    for call in calls if isinstance(calls, list) else []:
        extra = call.get('extra_data') if isinstance(call, dict) else None
        card = extra.get('workshop') if isinstance(extra, dict) else None
        if (isinstance(card, dict) and card.get('kind') in
                {'visit_started', 'visit_finished', 'visit_failed', 'visit_interrupted'}
                and card.get('source') == 'ui_action' and card.get('id')
                and card.get('id') == card.get('invitation_message_id')):
            return ('k',)
    return ()


def event_fact(row):
    from context_builder.text_utils import _redact_tool_fact
    calls = row.get('tool_calls') or row.get('toolCalls') or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            return ''
    for call in calls if isinstance(calls, list) else []:
        extra = call.get('extra_data') if isinstance(call, dict) else None
        card = extra.get('workshop') if isinstance(extra, dict) else None
        if not isinstance(card, dict):
            continue
        if card.get('visibility') == 'private':
            card = {key: value for key, value in card.items() if key in ('kind', 'title', 'timestamp', 'job_id', 'tokens', 'token_cap', 'visibility')}
            card['title'] = 'AI 的保密工单'
            card['summary'] = '保密工单有进展，内容尚未公开。'
        fields = {key: card[key] for key in ('kind', 'title', 'timestamp', 'source', 'invitation_message_id',
                  'job_id', 'revision', 'summary', 'reason', 'brief', 'plan', 'actions', 'tokens', 'token_cap', 'budget_summary') if key in card}
        if card.get('kind') == 'artifact_shared' and card.get('visibility') != 'private':
            fields['artifacts'] = [{key: row[key] for key in ('id','name','revision','kind','summary') if key in row}
                                   for row in card.get('artifacts',[])[:8]]
        if isinstance(card.get('memory_action'), dict) and card['memory_action'].get('version') == 1:
            fields.update({key: card[key] for key in ('memory_action', 'text', 'message_seq', 'plan_version', 'artifact_id') if key in card})
        result = '[工作坊客观事件；来访与工单进度分别记录] ' + _redact_tool_fact(fields)
        if isinstance(card.get('experience'), dict):
            from .experience import project
            fact = project(card['experience'])
            if fact:
                result += '\n' + fact
        return result
    return ''
