"""Read-only daily analysis for AI's understanding of other entities.

This module deliberately stops before registration or book writes.  It turns a
day of attributed speech into a small, versioned patch proposal; ``autonomous``
is the only writer.  Keeping that seam explicit lets a failed or experimental
analysis be inspected without changing AI's existing understanding.
"""
from __future__ import annotations
import inspect
from copy import deepcopy
import json
import re
from collections import defaultdict
from typing import Any, Awaitable, Callable
from . import books, other_book
from .maintenance_sources import source_metadata
from .social_identity import EXTERNAL_PERSON_SOURCES
from .entity_names import mentions, names, normalize, resolve_name
from .maintenance_sources import model_materials, resolve_many
from .other_facets import FACETS, split, validate
from .prompt_request import CognitionPrompt
MAX_SEARCH_ROUNDS = 2
MAX_QUERIES_PER_ROUND = 6
from .other_extract_contract import PROMPT
from .other_review_contract import REVIEW
from .habit_frequency_evidence import select as frequency_select, model_rows as frequency_model_rows, GUIDE as FREQUENCY_GUIDE

def _content(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get('content', raw.get('text', ''))
    if not isinstance(raw, str):
        raise books.BookError('他者分析返回正文无效')
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    return raw

def _parse(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(_content(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise books.BookError('他者分析返回不是 JSON') from exc
    if not isinstance(value, dict):
        raise books.BookError('他者分析返回结构无效')
    for key in ('queries', 'entities', 'patches', 'observations'):
        if key not in value:
            value[key] = []
        if not isinstance(value[key], list):
            raise books.BookError('他者分析列表结构无效')
    return value

def _evidence(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Preserve complete original statements, accepting normal chat or search rows."""
    result, seen = ([], set())
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = str(row.get('id') or row.get('message_id') or '').strip()
        text = row.get('text', row.get('content', ''))
        if not mid or mid in seen or (not isinstance(text, str)) or (not text.strip()):
            continue
        speaker = str(row.get('speaker') or row.get('sender') or row.get('role') or '').strip()
        if speaker == 'user':
            speaker = 'human'
        elif speaker == 'assistant':
            speaker = 'k'
        result.append({'id': mid, 'text': text, 'speaker': speaker, 'timestamp': str(row.get('timestamp') or row.get('created_at') or ''), 'active_date': str(row.get('active_date') or ''), 'source_kind': str(row.get('source_kind') or 'private_chat'), **source_metadata(row), 'identity_basis': str(row.get('identity_basis') or '')})
        seen.add(mid)
    return result

def _observation_evidence(observations: list[Any]) -> list[dict[str, str]]:
    """Old observations are leads, never evidence by themselves.

    Only their stored original-source snapshots are eligible to be cited again.
    """
    rows = []
    for observation in observations:
        if isinstance(observation, dict) and isinstance(observation.get('evidence'), list):
            rows.extend(observation['evidence'])
    return _evidence(rows)

def _observation_leads(observations: list[Any]) -> list[dict[str, Any]]:
    """Pass the lead, not its duplicated source payload, to the model."""
    result = []
    for item in observations:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            continue
        lead = {key: item.get(key, '') for key in ('subject_id', 'entity_name', 'text', 'source_date', 'reference_ids')}
        if not lead['reference_ids'] and isinstance(item.get('evidence'), list):
            lead['reference_ids'] = [row['id'] for row in item['evidence'] if isinstance(row, dict) and isinstance(row.get('id'), str)]
        lead['frequency_evidence'] = frequency_select(item.get('frequency_evidence', []), _evidence(item.get('evidence', [])))
        result.append(lead)
    return result

def _model_pack(snapshot: dict[str, list[dict[str, Any]]], material: list[dict[str, str]], observations: list[Any], requested_ids=(), daily_evidence=None):
    """Return the small semantic package, keeping the complete snapshot local.

    The registry is always present so a model never invents an existing identity.
    Full other bodies are only supplied for people implicated by today's original
    speech, earlier leads, or an explicitly requested historical lookup.
    """
    known = other_book.entities()
    day_text = '\n'.join((row['text'] for row in (daily_evidence if daily_evidence is not None else material)))
    selected = {'human', *(sid for sid in requested_ids if sid in known)}
    selected.update((e.get('speaker') for e in material if e.get('source_kind') in EXTERNAL_PERSON_SOURCES and e.get('speaker') in known))
    for lead in observations:
        if isinstance(lead, dict) and lead.get('subject_id') in known:
            selected.add(lead['subject_id'])
    for sid, entity in known.items():
        if any((mentions(day_text, term) for term in names(entity))):
            selected.add(sid)
    registry = [{'id': sid, 'name': entity['name'], 'aliases': entity.get('aliases', []), 'type': entity['type']} for sid, entity in known.items()]
    selected_other, directory = ([], [])
    for entry in snapshot['other']:
        light = {'subject_id': entry.get('subject_id', ''), 'name': entry.get('name', '')}
        if entry.get('subject_id') in selected:
            selected_other.append({key: entry.get(key, '') for key in ('name', 'subject_id', 'knower_id', 'body', 'revision', 'updated_at', 'topics', 'basis', 'kind')})
        else:
            directory.append(light)
    world = []
    for entry in snapshot['world']:
        subject = entry.get('subject_id', '')
        terms = [entry.get('name', ''), *entry.get('keywords', [])]
        if subject in selected or any((isinstance(term, str) and term and mentions(day_text, term) for term in terms)):
            world.append({key: entry.get(key, '') for key in ('name', 'body', 'keywords', 'subject_id', 'topics', 'kind')})
    risk = []
    for lead in observations:
        if not isinstance(lead, dict) or not lead.get('source_date'):
            continue
        for entry in selected_other:
            if entry['subject_id'] == lead.get('subject_id') and entry.get('updated_at', '')[:10] > str(lead['source_date'])[:10]:
                risk.append({'subject_id': entry['subject_id'], 'history_source_date': lead['source_date'], 'entry_updated_at': entry['updated_at']})
    return {'registry': registry, 'other': selected_other, 'other_directory': directory, 'world': world, 'historical_backfill_risk': risk}

def _date(row: dict[str, str]) -> str:
    return row.get('active_date', '')[:10] or row.get('timestamp', '')[:10]

def _callable_result(call: Callable[[dict[str, str]], Any], query: dict[str, str]):
    value = call(query)
    return value if inspect.isawaitable(value) else _ready(value)

async def _ready(value: Any) -> Any:
    return value

async def _search(search: Callable[[dict[str, str]], Any], queries: list[dict[str, str]]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for query in queries:
        value = await _callable_result(search, query)
        if isinstance(value, dict):
            value = value.get('sources', value.get('results', []))
        if not isinstance(value, list):
            raise books.BookError('他者历史检索返回结构无效')
        found.extend(_evidence(value))
    return found

def _known_entity(subject_id: str, entity_name: str, known: dict[str, dict[str, Any]], suggested: dict[str, dict[str, Any]]):
    if subject_id in known:
        return (subject_id, known[subject_id])
    if subject_id in suggested:
        return (subject_id, suggested[subject_id])
    if not entity_name:
        raise books.BookError('他者认识对象不明确')
    resolved = resolve_name(entity_name, known)
    if resolved:
        return (resolved, known[resolved])
    raise books.BookError('他者认识对象不明确')

def _referenced(item: dict[str, Any], space: list[dict[str, str]]) -> list[dict[str, str]]:
    """解析模型给的短序号 n；归一化为真实 id 后返回条目。

    归一化写回 item，后续 suggestions/落库/autonomous 校验一律用真实 id。
    """
    resolved = resolve_many(item.get('reference_ids'), space)
    if resolved is None:
        raise books.BookError('他者认识缺少有效原话引用')
    item['reference_ids'] = [row['id'] for row in resolved]
    return resolved

def _entity_anchored(entity: dict[str, Any], refs: list[dict[str, str]], subject_id: str) -> bool:
    if subject_id == 'human' and any((r['speaker'].casefold() in {'human', '人类伙伴', 'user'} for r in refs)):
        return True
    if entity.get('id') == subject_id and any((r.get('source_kind') in EXTERNAL_PERSON_SOURCES and r['speaker'].casefold() == subject_id.casefold() for r in refs)):
        return True
    terms = [entity.get('name', ''), *entity.get('aliases', [])]
    return any((mentions(r['text'], term) for r in refs for term in terms if isinstance(term, str) and term))

def _speaker_is_entity(row: dict[str, str], subject_id: str, entity: dict[str, Any]) -> bool:
    values = {subject_id.casefold(), normalize(entity.get('name', '')), *(normalize(x) for x in entity.get('aliases', []))}
    return row['speaker'].casefold() in values

def _basis_supported(basis: str, refs: list[dict[str, str]], subject_id: str, entity: dict[str, Any]) -> bool:
    own = any((_speaker_is_entity(row, subject_id, entity) for row in refs))
    k = any((row['speaker'].casefold() in {'k', 'assistant'} for row in refs))
    if basis == 'self_report':
        return own
    if basis == 'observed':
        device = subject_id == 'human' and any((row.get('source_kind') == 'sentinel_day_fact' and row['speaker'] == 'device:human' for row in refs))
        return k or (subject_id == 'human' and own) or device
    if basis == 'mutual_agreement':
        return own and k
    return basis == 'inferred'

def _stable(item: dict[str, Any], refs: list[dict[str, str]], subject_id: str, entity: dict[str, Any], basis: str) -> bool:
    if item.get('stability', 'tentative') != 'stable':
        return True
    if item.get('period') == 'unspecified' and basis in {'observed', 'mutual_agreement'}:
        return _basis_supported(basis, refs, subject_id, entity)
    dates = {_date(row) for row in refs if _date(row)}
    if len(dates) >= 2:
        return True
    return basis == 'self_report' and any((_speaker_is_entity(row, subject_id, entity) for row in refs))

def _compact_text(value: str) -> str:
    return re.sub('[\\W_]+', '', value, flags=re.UNICODE).casefold()

def _substantial_repeat(left: str, right: str) -> bool:
    """Exact formatting-normalized duplicates only; semantic overlap is reviewed."""
    left, right = (_compact_text(left), _compact_text(right))
    if not left or not right:
        return False
    return left == right

def _render(overview: str, facets: list[dict[str, Any]], old_body: str='', overview_mode: str='preserve', refs=(), *, _qualified=False, remove_facets=()) -> str:
    old_overview, old_facets = split(old_body) if old_body else ('', [])
    if not isinstance(facets, list) or any((not isinstance(facet, dict) for facet in facets)):
        raise books.BookError('他者认识维度结构无效')
    if overview_mode not in {'preserve', 'replace'}:
        raise books.BookError('他者认识整体修订方式无效')
    if old_body and overview_mode == 'preserve':
        overview = old_overview
    elif old_body and overview_mode == 'replace' and (not isinstance(overview, str)):
        raise books.BookError('他者认识整体修订缺少正文')
    by_title = {title: text for title, text in old_facets}
    if not isinstance(remove_facets, (list, tuple)) or any((not isinstance(title, str) or title not in by_title for title in remove_facets)) or len(set(remove_facets)) != len(remove_facets) or (remove_facets and (not facets)):
        raise books.BookError('栏目重组需要真实旧标题和完整替代主题')
    for title in remove_facets:
        del by_title[title]
    seen = set()
    for facet in facets:
        title, text = (facet.get('title'), facet.get('text'))
        if not isinstance(title, str) or not isinstance(text, str) or (not title.strip()) or (not text.strip()):
            raise books.BookError('他者认识维度无效')
        from .other_temporal import qualify, retained_facets
        if not _qualified:
            ids = facet.get('reference_ids')
            selected_refs = [row for row in refs if row['id'] in ids] if ids is not None else refs
            title, text = qualify(facet, selected_refs, retained_texts=retained_facets(old_body, remove_facets))
        if title in seen or '\n' in title or '\r' in title:
            raise books.BookError('他者认识主题重复或标题无效')
        if _substantial_repeat(text, overview):
            raise books.BookError('他者认识栏目重复整体印象，未写入')
        seen.add(title)
        by_title[title] = text
    overview = overview.strip() if isinstance(overview, str) else ''
    if not overview:
        overview = old_overview
    ordered = [title for title in FACETS if title in by_title] + [title for title, _ in old_facets if title not in FACETS and title in by_title] + [title for title in by_title if title not in FACETS and title not in dict(old_facets)]
    body = overview + ''.join(('\n\n## %s\n%s' % (title, by_title[title]) for title in dict.fromkeys(ordered)))
    validate(body)
    return body
from .other_patch_merge import merge_patches as _merge_patches

def _before(snapshot: dict[str, list[dict[str, Any]]], entry: dict[str, Any]) -> str:
    return next((row.get('body', '') for row in snapshot['other'] if row.get('subject_id') == entry['subject_id'] and row.get('name') == entry.get('target_entry')), '')

def _old_entry(snapshot: dict[str, list[dict[str, Any]]], entry: dict[str, Any]):
    return next((row for row in snapshot['other'] if row.get('subject_id') == entry['subject_id'] and row.get('name') == entry.get('target_entry')), None)

def _review_units(entries, snapshot, raw_groups):
    """Make the review boundary match actual changed facets, not whole people."""
    units = []
    from .other_claims import for_review
    from .other_temporal import RECENT_SUFFIX
    for entry_index, entry in enumerate(entries):
        before = _before(snapshot, entry)
        before_overview, before_facets = split(before) if before else ('', [])
        after_overview, after_facets = split(entry['body'])
        before_map, after_map = (dict(before_facets), dict(after_facets))
        overview_claim = {'section': 'overview', 'basis': entry.get('basis', 'inferred'), 'stability': entry.get('stability', 'tentative'), 'reference_ids': entry['reference_ids'], 'change': '整体印象的实际修订', 'after': after_overview}
        overview_claim.update(entry.get('overview_provenance', {}))

        def claims_for(title=None):
            return [{**claim, 'after': after_map[claim['section']], 'before': before_map.get(claim['section'], '')} for claim in for_review(entry, title)]
        group = next((row for row in raw_groups if row['subject_id'] == entry['subject_id']), {})
        if entry.get('remove_facets'):
            units.append({'entry_index': entry_index, 'subject_id': entry['subject_id'], 'entity_name': entry['entity_name'], 'facet': '__restructure__', 'before': before, 'after': entry['body'], 'entry_before': before, 'entry_after': entry['body'], 'reference_ids': entry['reference_ids'], 'raw_group': group, 'topics': entry.get('topics', []), 'removed_sections': [{'section': title, 'text': before_map[title]} for title in entry['remove_facets']], 'retained_bindings': entry.get('retained_bindings', []), 'theme_claims': claims_for() + ([overview_claim] if after_overview != before_overview else [])})
            continue
        if not before or after_overview != before_overview:
            units.append({'entry_index': entry_index, 'subject_id': entry['subject_id'], 'entity_name': entry['entity_name'], 'facet': 'overview', 'before': before_overview, 'after': after_overview, 'entry_before': before, 'entry_after': entry['body'], 'reference_ids': entry['reference_ids'], 'raw_group': group, 'theme_claims': [overview_claim]})
        for title, text in after_facets:
            if before_map.get(title) != text:
                units.append({'entry_index': entry_index, 'subject_id': entry['subject_id'], 'entity_name': entry['entity_name'], 'facet': title, 'before': before_map.get(title, ''), 'after': text, 'entry_before': before, 'entry_after': entry['body'], 'reference_ids': entry['reference_ids'], 'raw_group': group, 'theme_claims': claims_for(title), 'baseline_context': {'section': title.removesuffix(RECENT_SUFFIX), 'text': before_map[title.removesuffix(RECENT_SUFFIX)]} if title.endswith(RECENT_SUFFIX) and title.removesuffix(RECENT_SUFFIX) in before_map else None})
    for unit in units:
        unit['coverage_required'] = bool(unit['theme_claims'])
    return units

async def _review(entries, entities, snapshot, material, raw_groups, llm, *, source_date='', run_id=''):
    units = _review_units(entries, snapshot, raw_groups)
    if not units:
        return ([], [], [])
    from .other_claims import source_focus
    source_focus(units, material)
    n_of = {row['id']: n for n, row in enumerate(material, 1)}

    def wire_refs(value):
        if isinstance(value, dict):
            return {key: [n_of.get(ref, ref) if isinstance(ref, str) else ref for ref in item] if key == 'reference_ids' and isinstance(item, list) else wire_refs(item) for key, item in value.items()}
        if isinstance(value, list):
            return [wire_refs(item) for item in value]
        return value
    indexed = [wire_refs({'index': index, **unit}) for index, unit in enumerate(units)]
    raw = await llm(CognitionPrompt(REVIEW + '\n候选 reference_ids 使用 evidence 材料的序号 n。', {'candidates': indexed, 'evidence': model_materials(material)}, stage='other_review', source_date=source_date, run_id=run_id, separator='\n数据：', shared_material_keys=('evidence',)))
    review = _parse_review(raw, len(units))
    from .other_claims import require_grounding
    review = require_grounding(review, json.loads(_content(raw)), units)
    approved = set(review['approved_indices'])
    approved_by_entry = defaultdict(list)
    rejected_by_entry = defaultdict(list)
    for index, unit in enumerate(units):
        if index in approved:
            approved_by_entry[unit['entry_index']].append(unit)
        else:
            rejected_by_entry[unit['entry_index']].append(unit)
    kept = []
    for entry_index, entry in enumerate(entries):
        chosen = approved_by_entry.get(entry_index, [])
        if not chosen:
            continue
        before = _before(snapshot, entry)
        if any((unit['facet'] == '__restructure__' for unit in chosen)):
            kept.append(entry)
            continue
        before_overview, _ = split(before) if before else ('', [])
        overview = next((unit['after'] for unit in chosen if unit['facet'] == 'overview'), before_overview)
        if not before and (not any((unit['facet'] == 'overview' for unit in chosen))):
            continue
        facets = [{'title': unit['facet'], 'text': unit['after']} for unit in chosen if unit['facet'] != 'overview']
        body = _render(overview, facets, before, 'replace' if any((unit['facet'] == 'overview' for unit in chosen)) else 'preserve', _qualified=True)
        if body != before:
            from .topics import merge_annotations
            old = _old_entry(snapshot, entry)
            selected_titles = {unit['facet'] for unit in chosen}
            accepted_topics = [topic for topic in entry.get('topics', []) if topic['section'] in selected_titles]
            from .topics import carry_annotations
            retained = carry_annotations(old.get('topics', []) if old else [], before, body)
            entry = {**entry, 'topics': merge_annotations(retained, accepted_topics, body), 'theme_claims': [claim for claim in entry.get('theme_claims', []) if claim['section'] in selected_titles]}
            if entry['theme_claims'] or 'overview' in selected_titles:
                from .other_claims import page_provenance
                selected_claims = [claim for unit in chosen for claim in unit.get('theme_claims', [])]
                selected_refs = {mid for claim in selected_claims for mid in claim['reference_ids']}
                entry = {**entry, **page_provenance(selected_claims), 'reference_ids': [mid for mid in entry['reference_ids'] if mid in selected_refs]}
            if rejected_by_entry.get(entry_index):
                old = _old_entry(snapshot, entry)
                if old is not None:
                    keywords = old.get('keywords', [])
                else:
                    entity = other_book.entities().get(entry['subject_id'], {})
                    keywords = list(dict.fromkeys([entity.get('name', entry['entity_name']), *entity.get('aliases', [])]))
                kept.append({**entry, 'body': body, 'keywords': keywords})
            else:
                kept.append({**entry, 'body': body})
    kept_subjects = {entry['subject_id'] for entry in kept}
    readable_rejections = [{'index': item['index'], 'entity_name': units[item['index']]['entity_name'], 'facet': units[item['index']]['facet'], 'reason': item['reason']} for item in review['rejections']]
    return (kept, [entity for entity in entities if entity['id'] in kept_subjects], readable_rejections)

def _parse_review(raw: Any, length: int) -> dict[str, Any]:
    try:
        value = json.loads(_content(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise books.BookError('他者认识复核返回不是 JSON') from exc
    if not isinstance(value, dict) or not isinstance(value.get('approved_indices'), list) or (not isinstance(value.get('rejections'), list)):
        raise books.BookError('他者认识复核结构无效')
    approved = value['approved_indices']
    rejected = value['rejections']
    rejected_indices = []
    for item in rejected:
        if not isinstance(item, dict) or not isinstance(item.get('index'), int) or (not isinstance(item.get('reason'), str)) or (not item['reason'].strip()):
            raise books.BookError('他者认识复核拒绝项无效')
        rejected_indices.append(item['index'])
    if any((not isinstance(index, int) for index in approved)) or len(set(approved)) != len(approved) or len(set(rejected_indices)) != len(rejected_indices) or set(approved) & set(rejected_indices) or (set(approved) | set(rejected_indices) != set(range(length))):
        raise books.BookError('他者认识复核未覆盖全部候选')
    return {'approved_indices': approved, 'rejections': [{'index': item['index'], 'reason': item['reason'].strip()} for item in rejected]}

def _validate_output(output: dict[str, Any], snapshot: dict[str, list[dict[str, Any]]], material: list[dict[str, str]]):
    known = other_book.entities()
    suggestions: dict[str, dict[str, Any]] = {}
    derivative_kinds = {'event_fact', 'ambient_observation', 'wander_activity'}
    validation_rejections: list[dict[str, Any]] = []
    derivative_only_subjects: set[str] = set()
    for entity in output['entities']:
        if not isinstance(entity, dict):
            raise books.BookError('新实体建议无效')
        sid, name = (entity.get('subject_id', ''), entity.get('entity_name', ''))
        if not isinstance(sid, str) or not sid.startswith('new:') or (not isinstance(name, str)) or (not name.strip()):
            raise books.BookError('新实体建议必须使用临时编号和名称')
        if resolve_name(name, known):
            validation_rejections.append({'candidate_type': 'entity', 'entity_name': name.strip(), 'reason': '已有实体被误建议为新实体，未重复登记', 'reference_ids': entity.get('reference_ids', [])})
            continue
        if sid in suggestions:
            raise books.BookError('新实体编号或名称重复')
        if entity.get('type') not in {'human', 'silicon', 'other'}:
            raise books.BookError('新实体类型无效')
        refs = _referenced(entity, material)
        if all((ref.get('source_kind') in derivative_kinds for ref in refs)):
            derivative_only_subjects.add(sid)
            validation_rejections.append({'entity_name': name.strip(), 'reason': '新实体不能只凭事件摘要或观察推定身份', 'reference_ids': entity['reference_ids']})
            continue
        aliases = entity.get('aliases', [])
        if not isinstance(aliases, list) or any((not isinstance(a, str) for a in aliases)):
            raise books.BookError('新实体别名无效')
        candidate = {'name': name.strip(), 'aliases': [a.strip() for a in aliases if a.strip()]}
        if not _entity_anchored(candidate, refs, sid):
            raise books.BookError('新实体未由原话精确锚定')
        suggestions[sid] = {**candidate, 'id': sid, 'subject_id': sid, 'entity_name': candidate['name'], 'type': entity['type'], 'reference_ids': entity['reference_ids']}
    existing = defaultdict(list)
    for entry in snapshot['other']:
        existing[entry.get('subject_id', '')].append(entry)
    entries, seen_subjects = ([], set())
    for patch in output['patches']:
        if not isinstance(patch, dict):
            validation_rejections.append({'entity_name': '', 'reason': '他者认识修订无效', 'reference_ids': []})
            continue
        candidate_name = str(patch.get('entity_name') or patch.get('subject_id') or '')
        try:
            sid, entity = _known_entity(str(patch.get('subject_id', '')), str(patch.get('entity_name', '')), known, suggestions)
            candidate_name = entity['name']
            if sid in seen_subjects:
                raise books.BookError('同一实体本次只能有一份合并修订')
            seen_subjects.add(sid)
            refs = _referenced(patch, material)
            if all((ref.get('source_kind') in derivative_kinds for ref in refs)):
                raise books.BookError('他者认识不能只凭派生事件或未知说话人观察修订')
            if not _entity_anchored(entity, refs, sid):
                raise books.BookError('引用原话未锚定到对应实体')
            basis = patch.get('basis', 'inferred')
            if basis not in {'self_report', 'observed', 'inferred', 'mutual_agreement'}:
                raise books.BookError('他者认识依据无效')
            old_entries = existing[sid]
            target_name = patch.get('target_entry', '')
            target = next((e for e in old_entries if e.get('name') == target_name), None) if target_name else old_entries[0] if len(old_entries) == 1 else None
            if target_name and target is None:
                raise books.BookError('他者认识目标条目不属于该实体')
            if not target_name and len(old_entries) > 1:
                raise books.BookError('该实体有多个既有认识，需明确目标条目')
            overview_changed = target is None or patch.get('overview_mode', 'preserve') != 'preserve'
            overview_provenance = {'reference_ids': list(patch['reference_ids']), 'basis': basis, 'stability': patch.get('stability', 'tentative')}
            if overview_changed:
                if not _basis_supported(basis, refs, sid, entity):
                    raise books.BookError('他者认识依据与原话说话者不符')
                if not _stable(patch, refs, sid, entity, basis):
                    raise books.BookError('稳定认识需要跨日期原话或对象明确自述')
            from .other_claims import select_facets
            patch, theme_claims = select_facets(patch, material, entity, rejected=validation_rejections, referenced=_referenced, basis_supported=_basis_supported, stable=_stable, old_body=target.get('body', '') if target else '')
            if not overview_changed:
                if not theme_claims:
                    continue
                from .other_claims import page_provenance
                provenance = page_provenance(theme_claims)
                basis = provenance['basis']
                ids = {mid for claim in theme_claims for mid in claim['reference_ids']}
                refs = [row for row in material if row['id'] in ids]
                patch = {**patch, **provenance, 'reference_ids': [row['id'] for row in refs]}
            else:
                ids = set(patch['reference_ids']) | {mid for claim in theme_claims for mid in claim['reference_ids']}
                patch = {**patch, 'reference_ids': [row['id'] for row in material if row['id'] in ids]}
                refs = [row for row in material if row['id'] in ids]
            body = _render(patch.get('overview', ''), patch.get('facets', []), target.get('body', '') if target else '', patch.get('overview_mode', 'preserve'), refs=refs, remove_facets=patch.get('remove_facets', []))
            from .topics import merge_annotations
            from .other_temporal import facet_title
            annotations = [{'section': facet_title(facet), **{key: facet[key] for key in ('types', 'areas', 'context') if key in facet}} for facet in patch.get('facets', []) if any((key in facet for key in ('types', 'areas', 'context')))]
            from .topics import carry_annotations
            old_topics = carry_annotations(target.get('topics', []) if target else [], target.get('body', '') if target else '', body)
            topics = merge_annotations(old_topics, annotations, body)
            entry_name = target['name'] if target else entity['name']
            entries.append({'domain': 'other', 'name': entry_name, 'target_entry': target['name'] if target else '', 'subject_id': sid, 'entity_name': entity['name'], 'body': body, 'topics': topics, 'remove_facets': patch.get('remove_facets', []), 'theme_claims': theme_claims, 'overview_provenance': overview_provenance, 'keywords': patch.get('keywords', []), 'basis': basis, 'stability': patch.get('stability', 'tentative'), 'reference_ids': patch['reference_ids']})
        except books.BookError as exc:
            validation_rejections.append({'entity_name': candidate_name, 'reason': str(exc), 'reference_ids': patch.get('reference_ids', [])})
    observations = []
    for observation in output['observations']:
        try:
            if not isinstance(observation, dict) or not isinstance(observation.get('text'), str) or (not observation['text'].strip()):
                raise books.BookError('他者观察线索无效')
            if not isinstance(observation.get('subject_id', ''), str):
                raise books.BookError('他者观察线索对象无效')
            if observation.get('subject_id') in derivative_only_subjects:
                continue
            sid, entity = _known_entity(str(observation.get('subject_id', '')), str(observation.get('entity_name', '')), known, suggestions)
            refs = _referenced(observation, material)
            if not _entity_anchored(entity, refs, sid):
                raise books.BookError('观察线索未锚定实体')
            observations.append({'subject_id': sid, 'entity_name': entity['name'], 'text': observation['text'].strip(), 'reference_ids': observation['reference_ids']})
        except books.BookError as exc:
            candidate = observation if isinstance(observation, dict) else {}
            validation_rejections.append({'candidate_type': 'observation', 'entity_name': str(candidate.get('entity_name') or candidate.get('subject_id') or ''), 'reason': str(exc), 'reference_ids': candidate.get('reference_ids', [])})
    return (entries, list(suggestions.values()), observations, validation_rejections)

async def analyse(evidence, llm, *, source_date, session_id, search=None, observations=None, stage_state=None, checkpoint=None, validate_retrieval=None):
    """Analyse one day's complete speech without writes or entity registration.

    ``search`` receives ``{'entity_id', 'query'}`` and may return a list or a
    ``{'sources': [...]}`` mapping.  Its results are treated as ordinary cited
    material and it is never called more than two query rounds.
    """
    original = _evidence(list(evidence or []))
    snapshot = {'other': books.catalog('other'), 'world': books.catalog('world')}
    if not original:
        return {'entries': [], 'entities': [], 'snapshot': snapshot, 'evidence': [], 'observations': [], 'diagnostics': {'llm_calls': 0, 'search_rounds': 0, 'queries': 0, 'search_exhausted': False, 'search_trace': [], 'daily_evidence': 0, 'total_evidence': 0}}
    previous_observations = list(observations or [])
    from .maintenance import digest
    stage_identity = digest({'contract': 'other-preparation-v10', 'date': source_date, 'session': session_id, 'evidence': original, 'snapshot': snapshot, 'observations': previous_observations, 'prompts': [PROMPT, REVIEW], 'search_limits': [MAX_SEARCH_ROUNDS, MAX_QUERIES_PER_ROUND]})
    if stage_state is not None and stage_state.get('identity') == stage_identity and ('prepared' in stage_state):
        prepared = deepcopy(stage_state['prepared'])
        if validate_retrieval is None or await _callable_result(validate_retrieval, prepared.get('retrieved_sources', [])):
            return await _finish_prepared(prepared, snapshot, original, llm, resumed=True, source_date=source_date, run_id=stage_identity)
    if stage_state is not None:
        stage_state.clear()
    material = list(original)
    retrieved_sources = []
    material_ids = {row['id'] for row in material}
    for row in _observation_evidence(previous_observations):
        if row['id'] not in material_ids:
            material.append(row)
            material_ids.add(row['id'])
    rounds = calls = query_count = 0
    search_exhausted = False
    search_trace = []
    requested_ids = set()
    output = None
    while True:
        n_of = {row['id']: i for i, row in enumerate(material, 1)}
        leads = _observation_leads(previous_observations)
        for lead in leads:
            lead['frequency_evidence'] = frequency_model_rows(lead['frequency_evidence'], n_of)
            lead['reference_ids'] = [n for ref in lead.get('reference_ids') or [] if isinstance(ref, str) and (n := n_of.get(ref)) is not None]
        payload = {'source_date': source_date, 'session_id': session_id, 'books': _model_pack(snapshot, material, previous_observations, requested_ids, original), 'evidence': model_materials(material), 'previous_observations': leads, 'search_round': rounds, 'remaining_search_rounds': MAX_SEARCH_ROUNDS - rounds}
        prompt = PROMPT.replace('{max_queries}', str(MAX_QUERIES_PER_ROUND))
        if any((lead['frequency_evidence'] for lead in leads)):
            prompt += '\n' + FREQUENCY_GUIDE
        if rounds >= MAX_SEARCH_ROUNDS:
            prompt += '\n这是最终轮：不得再输出 queries；若仍不足，只输出带原话引用的 observations，不输出 patches。'
        raw = await llm(CognitionPrompt(prompt, payload, stage='other_extract', source_date=source_date, run_id=stage_identity, separator='\n数据：', shared_material_keys=('evidence',)))
        calls += 1
        output = _parse(raw)
        raw_queries = output['queries']
        if not raw_queries or search is None:
            break
        if rounds >= MAX_SEARCH_ROUNDS:
            search_exhausted = True
            output['patches'] = []
            output['entities'] = []
            break
        if len(raw_queries) > MAX_QUERIES_PER_ROUND or any((not isinstance(q, dict) or not isinstance(q.get('entity_id'), str) or (not isinstance(q.get('query'), str)) or (not q['query'].strip()) for q in raw_queries)):
            raise books.BookError('他者历史补查请求无效')
        queries = [{'entity_id': q['entity_id'], 'query': q['query'].strip()} for q in raw_queries]
        found = await _search(search, queries)
        existing_ids = {row['id'] for row in material}
        added = 0
        for row in found:
            if row['id'] not in existing_ids:
                material.append(row)
                retrieved_sources.append(row)
                existing_ids.add(row['id'])
                added += 1
        search_trace.append({'round': rounds + 1, 'queries': queries, 'new_evidence': added})
        requested_ids.update((q['entity_id'] for q in queries))
        rounds += 1
        query_count += len(raw_queries)
    merged_patches, raw_groups, merge_rejections = _merge_patches(output['patches'])
    prepared_output = {**output, 'patches': merged_patches}
    entries, entities, pending, validation_rejections = _validate_output(prepared_output, snapshot, material)
    validation_rejections = merge_rejections + validation_rejections
    validated_entry_count = len(entries)
    entries = [entry for entry in entries if entry['body'] != _before(snapshot, entry)]
    changed_subjects = {entry['subject_id'] for entry in entries}
    entities = [entity for entity in entities if entity['id'] in changed_subjects]
    from .lead_diagnostics import input_counts
    lead_funnel = {'status': 'available', 'input_leads': input_counts(previous_observations, material_ids | {row['id'] for row in material}), 'raw_patches': len(output['patches']), 'raw_observations': len(output['observations']), 'merged_patches': len(merged_patches), 'validated_candidates': validated_entry_count, 'unchanged_candidates': validated_entry_count - len(entries), 'validation_rejections': len(validation_rejections), 'accepted_observations': len(pending)}
    prepared = {'entries': entries, 'entities': entities, 'pending': pending, 'retrieved_sources': retrieved_sources, 'material': material, 'raw_groups': raw_groups, 'validation_rejections': validation_rejections, 'calls': calls, 'rounds': rounds, 'query_count': query_count, 'search_exhausted': search_exhausted, 'search_trace': search_trace, 'lead_funnel': lead_funnel}
    if stage_state is not None:
        stage_state.update(identity=stage_identity, prepared=deepcopy(prepared))
        if checkpoint is not None:
            checkpoint()
    return await _finish_prepared(prepared, snapshot, original, llm, resumed=False, source_date=source_date, run_id=stage_identity)

async def _finish_prepared(prepared, snapshot, original, llm, *, resumed, source_date='', run_id=''):
    entries, entities = (prepared['entries'], prepared['entities'])
    material, raw_groups = (prepared['material'], prepared['raw_groups'])
    needs_review = bool(entries)
    entries, entities, rejections = await _review(entries, entities, snapshot, material, raw_groups, llm, source_date=source_date, run_id=run_id)
    calls = prepared['calls'] + int(needs_review)
    from .lead_diagnostics import reviewed_funnel
    return {'entries': entries, 'entities': entities, 'snapshot': snapshot, 'evidence': material, 'observations': prepared['pending'], 'diagnostics': {'llm_calls': calls, 'search_rounds': prepared['rounds'], 'queries': prepared['query_count'], 'search_exhausted': prepared['search_exhausted'], 'search_trace': prepared['search_trace'], 'daily_evidence': len(original), 'total_evidence': len(material), 'rejections': rejections, 'validation_rejections': prepared['validation_rejections'], 'preparation_resumed': resumed, 'current_attempt_llm_calls': int(needs_review) if resumed else calls, 'raw_patch_groups': raw_groups, 'lead_funnel': reviewed_funnel(prepared, len(entries))}}
