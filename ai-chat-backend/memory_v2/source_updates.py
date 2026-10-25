"""Opt-in source updates, assembled into the existing event ledger.

Updates survive episode admission and overflow. Pending drafts are not a second
fact store: only a complete encoding may materialize reports or attach changes.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json

from .models import EventDraft, SourceRef
from .state_changes import MAX_CHANGES, parse as parse_changes

SUFFIX = '+source-updates-v3'
MAX_UPDATES = 16
_TARGET = '_source_update_target'
REPORT_FLAG = 'source_update_report'


@dataclass(frozen=True)
class SourceUpdateDraft:
    change: dict
    source: SourceRef
    target: str = ''
    source_scope_ids: tuple[str, ...] = ()


def enabled(version):
    from .source_notes import enabled as notes_enabled
    return SUFFIX[1:] in version.split('+') or notes_enabled(version)


GUIDANCE = (
    '同一次返回先用source_updates保留原话明确给出的事实更新，再用episodes归纳独立经历。'
    '更正、结果、计划、取消和明确未变的状态，即使只是一句接话，也先按来源保留；'
    '是否值得另写一段经历，是后一步的分组问题。普通问候、重复建议或未得到回答的猜测仍留原话。'
    '每项先给source_ref与逐字连续quote，再填主体、具体object、aspect、时间和有归属的简短中文text。'
    '短答的指代依完整相邻原话理解，保留现实/虚构、人物、部位和不确定性。'
    '本版更新只取普通私聊U/AI原话：人类伙伴的更新引用她自己的U行，AI的更新引用AI自己的AI行。'
    'kind为reported/planned/uncertain；当时报告用contemporaneous_report且发生时间与时间引文留空；'
    '明确过去时间用explicit_expression，time_expression逐字引用本条原话并按其报告日期还原occurred_at；'
    '无法确定发生时间用unresolved，occurred_at留空。更正旧时间与报告新结果分别保留。'
    'target_episode为该更新所属的episodes数组序号（从1开始），且该段须包含这条来源与主体；'
    '没有合适段落时填0，后台会保留“本次报告了什么”，不把它当又发生了一次。'
    'episodes不再重复输出state_changes。non_event_source_refs只表示未形成独立经历，'
    '仍可被source_updates引用；它不证明没有事实更新。每批最多16项更新，超出容量时'
    '把未处理来源留给既有overflow，而不是宣称没有更新。通信摘要不替代事实更新。'
)


def apply_prompt(system, version):
    from . import source_notes
    if source_notes.enabled(version):
        return source_notes.apply_prompt(system, version)
    from .state_changes import SUFFIX as storage_suffix
    from .state_change_encoding import _OLD_RESPONSIBILITY, _RESPONSIBILITY, SUFFIX as rejected_suffix
    if storage_suffix[1:] not in version.split('+') or rejected_suffix[1:] in version.split('+'):
        raise ValueError('source_updates_incompatible_prompt_version')
    if system.count(_OLD_RESPONSIBILITY) != 1:
        raise ValueError('source_updates_responsibility_template_changed')
    return system.replace(_OLD_RESPONSIBILITY, _RESPONSIBILITY) + '\n' + GUIDANCE


def schema_for(schema, version, messages=None):
    from . import source_notes
    if source_notes.enabled(version):
        return source_notes.schema_for(schema, messages)
    if not enabled(version):
        return schema
    result = deepcopy(schema)
    episode = result['properties']['episodes']['items']
    change = episode['properties'].pop('state_changes')['items']
    episode['required'].remove('state_changes')
    fields = change['properties']
    first = ('source_ref', 'quote', 'subject_id', 'object', 'aspect', 'kind',
             'temporal_basis', 'time_expression', 'occurred_at', 'text')
    change['properties'] = {k: fields[k] for k in first}
    change['properties']['subject_id'] = {'type': 'string', 'enum': ['human', 'k']}
    change['properties']['target_episode'] = {'type': 'integer', 'minimum': 0, 'maximum': 16}
    change['required'] = [*first, 'target_episode']
    result['properties'] = {'source_updates': {'type': 'array', 'maxItems': MAX_UPDATES, 'items': change},
                            **result['properties']}
    result['required'] = ['source_updates', *result['required']]
    return result


def _key(plan, index):
    return hashlib.sha256(json.dumps([plan.batch.batch_id, plan.batch.source_digest,
        plan.batch.prompt_version, index], separators=(',', ':')).encode()).hexdigest()


def prepare(value, plan, messages):
    """Validate updates before narrative normalization; keep raw output intact."""
    from . import source_notes
    if source_notes.enabled(plan.batch.prompt_version):
        return source_notes.prepare(value, plan, messages)
    value = deepcopy(value)
    updates = value.pop('source_updates', None)
    episodes = value.get('episodes')
    if not isinstance(updates, list) or len(updates) > MAX_UPDATES or not isinstance(episodes, list):
        raise ValueError('source_updates_shape')
    by_ref = {f's{i}': m for i, m in enumerate(messages, 1)}
    drafts = []
    for item in updates:
        if not isinstance(item, dict):
            raise ValueError('source_updates_item')
        item = dict(item)
        target = item.pop('target_episode', None)
        if type(target) is not int or not 0 <= target <= min(len(episodes), 16):
            raise ValueError('source_updates_target')
        source = by_ref.get(item.get('source_ref'))
        subject = item.get('subject_id')
        if (source is None or subject not in {'human', 'k'} or source.event_type
                or source.source_kind != 'chat'
                or source.role != ('user' if subject == 'human' else 'assistant')):
            raise ValueError('source_updates_speaker')
        if target:
            episode = episodes[target - 1]
            if (not isinstance(episode, dict) or item['source_ref'] not in episode.get('source_refs', [])
                    or subject not in episode.get('participant_ids', [])):
                raise ValueError('source_updates_target_evidence')
        change = parse_changes([item], by_ref, [item['source_ref']], [subject])[0]
        drafts.append(SourceUpdateDraft(change, source.as_source_ref(),
            _key(plan, target) if target else '', tuple(source.source_scope_ids)))
    for index, episode in enumerate(episodes, 1):
        if not isinstance(episode, dict) or 'state_changes' in episode or _TARGET in episode:
            raise ValueError('source_updates_duplicate_representation')
        episode['state_changes'] = []
        episode[_TARGET] = _key(plan, index)
    return value, tuple(drafts)


def signature(change):
    return json.dumps(change, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def finalize(parsed, plan):
    """Materialize only a complete tree; exact source updates survive regrouping."""
    if parsed.overflow:
        return parsed
    from .source_notes import enabled as notes_enabled, AUTO_TARGET
    if notes_enabled(plan.batch.prompt_version) and plan.parent_batch_id:
        return parsed  # Resolve unique membership only after the entire tree.
    if not parsed.source_updates and not any(_TARGET in e.attributes for e in parsed.events):
        return parsed
    events = list(parsed.events)
    targets = {e.attributes.get(_TARGET): i for i, e in enumerate(events) if e.attributes.get(_TARGET)}
    seen = {signature(c) for e in events for c in e.attributes.get('state_changes', [])}
    report_groups = {}
    root_ids = {s.message_id for s in plan.batch_sources}
    for draft in parsed.source_updates:
        change = draft.change
        if draft.source.message_id not in root_ids or change.get('source_message_id') != draft.source.message_id:
            raise ValueError('source_updates_outside_root')
        sig = signature(change)
        if sig in seen:
            continue
        seen.add(sig)
        index = targets.get(draft.target)
        if draft.target == AUTO_TARGET:
            candidates = [i for i, e in enumerate(events)
                if not e.attributes.get(REPORT_FLAG) and change['subject_id'] in e.participant_ids
                and draft.source.message_id in {s.message_id for s in e.sources}]
            index = candidates[0] if len(candidates) == 1 else None
        if index is not None:
            event = events[index]
            changes = list(event.attributes.get('state_changes', []))
            if len(changes) < MAX_CHANGES:
                if (draft.source.message_id not in {s.message_id for s in event.sources}
                        or change['subject_id'] not in event.participant_ids):
                    raise ValueError('source_updates_target_evidence')
                events[index] = replace(event, attributes={**event.attributes, 'state_changes': [*changes, change]})
                continue
        # Unknown target, overflow-deferred target, or a full six-change target:
        # retain a report; source overlap alone never guesses occurrence identity.
        key = (draft.source.message_id, change['subject_id'])
        report_groups.setdefault(key, []).append(draft)
    for group in report_groups.values():
        for start in range(0, len(group), MAX_CHANGES):
            part = group[start:start + MAX_CHANGES]
            first = part[0]
            changes = [d.change for d in part]
            subject = first.change['subject_id']
            subject_name = '人类伙伴' if subject == 'human' else 'AI'
            # Summary identifies the report only. Detailed claims and their own
            # occurrence clocks stay in source-bound state_changes, never cut.
            summary = f"{subject_name}报告了关于{first.change['object']}的更新。"
            events.append(EventDraft(ordinal=len(events), subject_id=subject, event_type='status_update',
                summary=summary, reported_at=first.change['reported_at'], active_date=plan.batch.active_date,
                calendar_date=first.change['reported_at'][:10], occurred_at=first.change['reported_at'],
                participant_ids=(subject,), sources=(first.source,),
                epistemic_status='explicit_report' if subject == 'human' else 'direct_observation',
                attributes={REPORT_FLAG: True, 'state_changes': changes, 'time_precision': 'reported_at',
                    'temporal_basis': 'contemporaneous_report', 'time_expression': '',
                    'memory_facets': ['state'], 'event_aspects': [],
                    'source_scope_ids': list(first.source_scope_ids)}))
    events = tuple(replace(e, ordinal=i, attributes={k: v for k, v in e.attributes.items() if k != _TARGET})
                   for i, e in enumerate(events))
    emitted = {s.message_id for e in events for s in e.sources}
    non_event = tuple(ref for ref in parsed.non_event_source_refs
                      if plan.batch_sources[int(ref[1:]) - 1].message_id not in emitted)
    return replace(parsed, events=events, source_updates=(), non_event_source_refs=non_event)


def preserve_after_repair(original, repaired, plan):
    """A grouping rewrite cannot erase already validated progress from this input."""
    from .source_notes import enabled as notes_enabled
    if notes_enabled(plan.batch.prompt_version):
        from .scene_communication import merge_summaries
        repaired = replace(repaired, communication_summaries=merge_summaries(
            (*original.communication_summaries, *repaired.communication_summaries)))
    drafts = list(repaired.source_updates)
    for event in original.events:
        sources = {s.message_id: s for s in event.sources}
        for change in event.attributes.get('state_changes', []):
            source = sources.get(change.get('source_message_id'))
            if source is not None:
                drafts.append(SourceUpdateDraft(change, source, source_scope_ids=tuple(event.attributes.get('source_scope_ids', []))))
    return finalize(replace(repaired, source_updates=tuple(drafts)), plan)
