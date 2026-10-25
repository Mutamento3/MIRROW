"""Source-bound progress from the existing encoder, not a second fact ledger."""
import hashlib
from datetime import datetime

VERSION = 'state-changes-v1'
START_DATE = '2026-10-22'
SUFFIX = '+' + VERSION
MAX_CHANGES = 6


def enabled(prompt_version):
    return SUFFIX in prompt_version


def extend_schema(schema):
    fields = {
        'subject_id': {'type': 'string', 'maxLength': 120},
        'object': {'type': 'string', 'minLength': 1, 'maxLength': 80},
        'aspect': {'type': 'string', 'minLength': 1, 'maxLength': 40},
        'text': {'type': 'string', 'minLength': 1, 'maxLength': 180},
        'kind': {'type': 'string', 'enum': ['reported', 'planned', 'uncertain']},
        'source_ref': {'type': 'string'},
        'quote': {'type': 'string', 'minLength': 1, 'maxLength': 600},
        'occurred_at': {'type': 'string'},
        'temporal_basis': {'type': 'string', 'enum': ['contemporaneous_report', 'explicit_expression', 'unresolved']},
        'time_expression': {'type': 'string'},
    }
    episode = schema['properties']['episodes']['items']
    episode['properties']['state_changes'] = {'type': 'array', 'maxItems': MAX_CHANGES,
        'items': {'type': 'object', 'properties': fields, 'required': list(fields),
                  'additionalProperties': False}}
    if 'state_changes' not in episode['required']:
        episode['required'].append('state_changes')
    return schema


GUIDANCE = (
    ' In the same response, each episode includes state_changes (empty when no '
    'explicit progress is present). Preserve concrete reported changes, corrections, '
    'plans, cancellations and known outcomes separately from the episode narration. '
    'object names the specific matter and aspect the dimension being updated; text '
    'is a short attributed Chinese fact. kind distinguishes a report, a plan and '
    'an uncertain account. Resolve short replies from the supplied conversation '
    'without changing the speaker, body location or real/imagined context. '
    'source_ref belongs to this episode and quote is its exact contiguous supporting '
    'text. Human progress cites her own U row; AI restating it is not new confirmation. '
    'Use the event time convention: contemporaneous_report leaves occurred_at empty for the backend to fill from that source; '
    'explicit_expression retains an exact time_expression from its body and the '
    'corresponding occurrence time; unresolved keeps occurred_at empty. '
    'A past recollection retains its past occurrence clock. Changes are evidence '
    'inside this episode, not a habit, a decision to stop caring, or a new event '
    'per sentence. Keep distinct supported changes within the six-item bound; '
    'if needed retain further facts in the episode narration rather than inventing '
    'completion. An empty array does not certify semantic completeness.'
)


def parse(value, by_ref, episode_refs, participants):
    if not isinstance(value, list) or len(value) > MAX_CHANGES:
        raise ValueError('state_changes_invalid_array')
    result = []
    allowed = {'subject_id', 'object', 'aspect', 'text', 'kind', 'source_ref',
               'quote', 'occurred_at', 'temporal_basis', 'time_expression'}
    for item in value:
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError('state_change_fields')
        if any(not isinstance(v, str) for v in item.values()):
            raise ValueError('state_change_string_required')
        ref = item['source_ref']
        if ref not in episode_refs or ref not in by_ref:
            raise ValueError('state_change_source_outside_episode')
        source = by_ref[ref]
        if item['subject_id'] not in participants:
            raise ValueError('state_change_subject_outside_episode')
        if item['subject_id'] == 'human' and (source.role != 'user' or source.event_type):
            raise ValueError('state_change_human_requires_user')
        for key, maximum in [('object', 80), ('aspect', 40), ('text', 180), ('quote', 600)]:
            if not item[key].strip() or len(item[key]) > maximum:
                raise ValueError('state_change_invalid_' + key)
        if item['quote'] not in source.content:
            raise ValueError('state_change_quote_missing')
        if item['kind'] not in {'reported', 'planned', 'uncertain'}:
            raise ValueError('state_change_kind')
        basis, occurred, expression = item['temporal_basis'], item['occurred_at'], item['time_expression']
        if basis == 'contemporaneous_report':
            if expression or occurred not in ('', source.timestamp):
                raise ValueError('state_change_report_clock')
            item = {**item, 'occurred_at': source.timestamp}
            occurred = source.timestamp
        elif basis == 'explicit_expression':
            if not expression or expression not in source.content or not occurred:
                raise ValueError('state_change_time_source')
        elif basis == 'unresolved':
            if occurred or (expression and expression not in source.content):
                raise ValueError('state_change_unresolved_clock')
        else:
            raise ValueError('state_change_time_basis')
        if occurred:
            datetime.fromisoformat(occurred)
        result.append({k: v for k, v in item.items() if k != 'source_ref'} | {
            'source_message_id': source.message_id, 'source_role': source.role,
            'source_kind': source.source_kind, 'reported_at': source.timestamp,
            'source_row_id': source.row_id, 'source_session_id': source.session_id,
            'source_content_digest': hashlib.sha256(source.content.encode('utf8')).hexdigest(),
            'version': VERSION})
    return result


def search_text(summary, changes):
    return '\n'.join([summary, *(f"{x['object']} {x['aspect']} {x['text']}"
        for x in changes if isinstance(x, dict) and x.get('version') == VERSION)])


def render(changes):
    """Dated prose only; a report remains a report and a plan remains a plan."""
    from behavior_scheduler.bookshelf_search import sanitize_archive_text
    parts = []
    for item in changes:
        if not isinstance(item, dict) or item.get('version') != VERSION:
            continue
        when = str(item.get('occurred_at') or '')[:10]
        report = str(item.get('reported_at') or '')[:10]
        clock = when or f'{report}提起，发生时间未确定'
        if when and report != when:
            clock += f'（{report}提起）'
        kind = {'reported': '自述进展', 'planned': '计划', 'uncertain': '尚不确定'}.get(item.get('kind'), '记录')
        parts.append(sanitize_archive_text(f"{clock}，{kind}：{item.get('text', '')}", limit=350))
    return '\n'.join(parts)


def validated_changes(changes, messages, source_ids):
    """Read-time proof; absent/edited sources withdraw only this derived material."""
    result = []
    for item in changes:
        if not isinstance(item, dict) or item.get('version') != VERSION:
            continue
        mid = item.get('source_message_id')
        message = messages.get(mid)
        if message is None or mid not in source_ids:
            continue
        if (item.get('source_content_digest') != hashlib.sha256(message.content.encode('utf8')).hexdigest()
                or item.get('source_row_id') != message.row_id
                or item.get('source_session_id') != message.session_id
                or item.get('source_role') != message.role
                or item.get('source_kind') != message.source_kind
                or item.get('reported_at') != message.timestamp
                or not item.get('quote') or item['quote'] not in message.content):
            continue
        result.append(item)
    return tuple(result)


def current_messages(authority_path, changes):
    ids = tuple(dict.fromkeys(x.get('source_message_id') for x in changes
                             if isinstance(x, dict) and x.get('source_message_id')))
    if not ids or authority_path is None:
        return {}
    from .conversation_source import ConversationSource, ConversationSourceError
    import sqlite3
    try:
        return {m.message_id: m for m in ConversationSource(authority_path,
            canonical_sessions_only=True).read_message_ids(ids)}
    except (OSError, ValueError, sqlite3.Error, ConversationSourceError):
        return {}


def cache_current(documents, authority_path):
    nodes = [n for d in documents for n in d.event_nodes if n.state_changes]
    messages = current_messages(authority_path, [x for n in nodes for x in n.state_changes])
    return all(validated_changes(n.state_changes, messages, n.source_message_ids) == n.state_changes
               for n in nodes)


def latest_progress_nodes(nodes):
    """Keep each aspect's most recent dated evidence; equal-time conflicts coexist."""
    from .source_time import instant
    groups = {}
    for node in nodes:
        for item in node.state_changes:
            if item.get('version') != VERSION:
                continue
            key = tuple(item.get(k, '') for k in ('subject_id', 'object', 'aspect', 'kind'))
            try:
                when = instant(item.get('occurred_at') or '').timestamp()
            except ValueError:
                continue
            current = groups.setdefault(key, [])
            if not current or when > current[0][0]:
                groups[key] = [(when, node)]
            elif when == current[0][0]:
                current.append((when, node))
    return {node.event_id for values in groups.values() for _, node in values}
