"""Opt-in single prose record for communication and source-bound progress."""
from copy import deepcopy

SUFFIX = '+source-notes-v4'
START_DATE = '2026-10-23'
START_SOURCE_AT = '2026-10-23T08:05:00+08:00'
AUTO_TARGET = '@unique-source'


def enabled(version):
    return SUFFIX[1:] in version.split('+')


GUIDANCE = (
    '先沿原话顺序写source_notes，再归纳episodes。每项用source_ref指向一条消息，text用简短中文'
    '完整表达这句话实际传达的内容，明确是谁的陈述、建议或猜测；每项text最多180字，长通信或多件事分项保留。'
    'text是交流和事实共用的唯一正文，事实不必再重复写进经历摘要。'
    '更正、结果、计划、取消、明确未变的状态都属于更新，即使只有一句接话；'
    '在写这项text时直接填写update，普通交流则update=null。按上下文补足指代，'
    '保留人物、现实/虚构、部位、时间和状态；纠正旧时间与报告新结果分别记。'
    'update只填写quote、object、aspect、kind及发生时间依据，不填主体或事件编号。'
    'quote是本条原话的逐字连续引文；更新只来自普通私聊说话人本人的进展，主体和报告时间由来源取得。'
    '说话人转述第三人的情况仍保留text，update=null；说话角色本身不证明其他人的状态。'
    'AI复述人类伙伴的情况、派生推送和未确认猜测仍可记为交流，不能算她的新事实确认。'
    'kind为reported/planned/uncertain；直接报告当前状态用contemporaneous_report，occurred_at和'
    'time_expression留空。明确的过去时间用explicit_expression，time_expression引用原话的时间表达，'
    'occurred_at按这条消息日期还原。发生时间不能确定用unresolved并留空occurred_at。'
    'communication_complete_refs仅列同源notes合起来已承接整条消息通信的来源；'
    '有更新之外的请求或其他交流时也须用update=null的note保留，仍需看原话的来源不列入覆盖。'
    '每批最多16项update，其余交流不占更新名额；更多更新留给既有overflow。'
    'episodes只负责经历分组，non_event_source_refs只表示没有独立经历，并不排除source_notes中的更新。'
)


def apply_prompt(system, version):
    from .state_change_encoding import _OLD_RESPONSIBILITY, _RESPONSIBILITY
    if '+state-changes-v1' not in version or '+source-updates-v3' in version or '+state-extraction-first-v2' in version:
        raise ValueError('source_notes_incompatible_version')
    if system.count(_OLD_RESPONSIBILITY) != 1:
        raise ValueError('source_notes_responsibility_template_changed')
    return system.replace(_OLD_RESPONSIBILITY, _RESPONSIBILITY) + '\n' + GUIDANCE


def schema_for(schema, messages=None):
    result = deepcopy(schema)
    episode = result['properties']['episodes']['items']
    change = episode['properties'].pop('state_changes')['items']
    episode['required'].remove('state_changes')
    fields = ('quote', 'object', 'aspect', 'kind', 'temporal_basis', 'time_expression', 'occurred_at')
    update = dict(type='object', additionalProperties=False,
        properties={k: change['properties'][k] for k in fields}, required=list(fields))
    note = dict(type='object', additionalProperties=False, required=['source_ref', 'text', 'update'],
        properties={'source_ref': {'type': 'string'}, 'text': {'type': 'string', 'minLength': 1, 'maxLength': 180},
                    'update': {'anyOf': [update, {'type': 'null'}]}})
    result['properties'].pop('communication_summaries', None)
    result['properties'] = {'source_notes': {'type': 'array', 'items': note},
        'communication_complete_refs': {'type': 'array', 'items': {'type': 'string'}}, **result['properties']}
    result['required'] = ['source_notes', 'communication_complete_refs', *(k for k in result['required'] if k != 'communication_summaries')]
    if messages is not None:
        from .scene_communication import eligible
        refs = [f's{i}' for i, m in enumerate(messages, 1) if eligible(m)]
        if refs:
            note['properties']['source_ref']['enum'] = refs
            result['properties']['communication_complete_refs']['items']['enum'] = refs
        else:
            result['properties']['source_notes']['maxItems'] = 0
            result['properties']['communication_complete_refs']['maxItems'] = 0
    return result


def prepare(value, plan, messages):
    from .source_updates import SourceUpdateDraft, MAX_UPDATES, _TARGET
    from .state_changes import parse
    from .scene_communication import eligible
    value = deepcopy(value)
    notes = value.pop('source_notes', None)
    complete = value.pop('communication_complete_refs', None)
    if ('source_updates' in value or 'communication_summaries' in value
            or not isinstance(notes, list) or len(notes) > max(1, len(messages)) * 4):
        raise ValueError('source_notes_shape')
    by_ref = {f's{i}': m for i, m in enumerate(messages, 1)}
    communication, drafts, seen = {}, [], set()
    for note in notes:
        if not isinstance(note, dict) or set(note) != {'source_ref', 'text', 'update'}:
            raise ValueError('source_notes_fields')
        ref, text, update = note['source_ref'], note['text'], note['update']
        source = by_ref.get(ref) if isinstance(ref, str) else None
        if source is None or not eligible(source) or not isinstance(text, str) or not text.strip() or len(text) > 180:
            raise ValueError('source_notes_source')
        text = text.strip()
        if (ref, text) not in seen:
            communication.setdefault(ref, []).append(text)
            seen.add((ref, text))
        if update is None:
            continue
        fields = {'quote', 'object', 'aspect', 'kind', 'temporal_basis', 'time_expression', 'occurred_at'}
        if not isinstance(update, dict) or set(update) != fields:
            raise ValueError('source_notes_update_fields')
        if source.event_type or source.source_kind != 'chat':
            raise ValueError('source_notes_update_speaker')
        subject = 'human' if source.role == 'user' else 'k'
        change = parse([{**update, 'source_ref': ref, 'text': text, 'subject_id': subject}], by_ref, [ref], [subject])[0]
        drafts.append(SourceUpdateDraft(change, source.as_source_ref(), AUTO_TARGET, tuple(source.source_scope_ids)))
        if len(drafts) > MAX_UPDATES:
            raise ValueError('source_notes_update_limit')
    if (not isinstance(complete, list) or any(not isinstance(ref, str) or ref not in communication for ref in complete)
            or len(set(complete)) != len(complete)):
        raise ValueError('source_notes_communication_coverage')
    episodes = value.get('episodes')
    if not isinstance(episodes, list):
        raise ValueError('source_notes_episodes')
    for episode in episodes:
        if not isinstance(episode, dict) or 'state_changes' in episode or _TARGET in episode:
            raise ValueError('source_notes_duplicate_representation')
        episode['state_changes'] = []
    if '+scene-digest-v1' in plan.batch.prompt_version:
        value['communication_summaries'] = [{'source_ref': ref, 'summary': '\n'.join(parts)}
            for ref, parts in communication.items() if ref in complete]
    return value, tuple(drafts)
