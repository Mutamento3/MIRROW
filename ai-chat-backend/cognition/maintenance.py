"""Evidence-backed maintenance with durable plans and recoverable Markdown commits.

No reads invoke models or change state. Unknowns remain candidates; receipts make
the gap between the book commit and its audit record recoverable after a crash.
"""
import asyncio
import json
import secrets
from datetime import datetime
from pathlib import Path
from llm_usage_scope import usage_scope
from .prompt_request import CognitionPrompt
from .world_materials import REFERENCE_CONTRACT, numbered_references
from . import books
from .brief import defer_updates
from .maintenance_sources import EXTRACTION, REVIEW, model_materials, parse_response, resolve_ref, sources, source_metadata
_run_lock = asyncio.Lock()
_instance = secrets.token_hex(12)
INTEREST_COMPLETE_KEYWORD_REVISION_V1 = 'interest_complete_v1'

def digest(value):
    return books.revision(json.dumps(value, sort_keys=True, ensure_ascii=False))

def folder():
    return books.ROOT / 'data' / 'cognition_maintenance'

def record_path(rid):
    if not isinstance(rid, str) or len(rid) != 64 or any((c not in '0123456789abcdef' for c in rid)):
        raise books.BookError('维护记录编号无效')
    return folder() / 'items' / (rid + '.json')

def write(item):
    books.atomic_text(record_path(item['id']), json.dumps(item, ensure_ascii=False, indent=2))

def get(rid):
    path = record_path(rid)
    if not path.exists():
        raise books.BookError('维护记录不存在')
    return json.loads(path.read_text(encoding='utf-8'))

def records():
    return record_report()['records']

def record_report():
    with books.LOCK:
        items, damaged = ([], 0)
        for path in (folder() / 'items').glob('*.json'):
            try:
                item = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(item, dict) or any((k not in item for k in ('id', 'status', 'created_at', 'proposal', 'plan', 'review', 'reason', 'source_date'))):
                    raise ValueError('invalid record')
                items.append(item)
            except (ValueError, OSError):
                damaged += 1
        return {'records': sorted(items, key=lambda i: (i['created_at'], i['id']), reverse=True), 'damaged_records': damaged}

def status():
    daily_paths = list((folder() / 'daily_runs').glob('*.json'))
    paths = sorted(daily_paths or list((folder() / 'runs').glob('*.json')), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    try:
        state = json.loads(paths[0].read_text(encoding='utf-8')) if paths else {'status': 'not_run'}
        if not isinstance(state, dict):
            raise ValueError('invalid run')
    except (ValueError, OSError):
        return {'status': 'error', 'message': '最近运行状态无法读取，请保留文件排查；未视作完成'}
    if state.get('status') == 'running' and state.get('instance') != _instance:
        state = {**state, 'status': 'interrupted', 'message': '上次进程在分析期间退出；可重试，已写入记录保留'}
    from .maintenance_status import current_lanes
    from .habit_diagnostics import public_fields
    visible = {k: state[k] for k in ('id', 'status', 'source_date', 'started_at', 'completed_at', 'message', 'items', 'lanes', 'memory_status', 'wander_processed', 'lane_errors', 'lane_rejections', 'failure_stage', 'rejected_proposal_count', 'discarded_review_count') if k in state}
    from .habit_natural_policy import status as habit_runtime_status
    return {**current_lanes({**visible, **public_fields(state)}), 'habit_runtime_policy': habit_runtime_status()}

def validate(proposal, space):
    """space 是引用空间（当天材料 + 历史观察证据）；模型给的是短序号 n。"""
    if not isinstance(proposal, dict):
        raise books.BookError('候选不是对象')
    p = dict(proposal)
    if p.get('domain') not in books.DOMAINS or p.get('subject_id') not in books.subject_catalog() or p.get('knower_id') not in books.subject_catalog():
        raise books.BookError('领域或主体无效')
    if p.get('kind') not in books.KINDS or p.get('operation') not in {'append', 'replace'}:
        raise books.BookError('材料性质或处理方式无效')
    for key in ('name', 'body', 'reason'):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise books.BookError('候选缺少名称、正文或依据')
    if not isinstance(p.get('target_entry', ''), str):
        raise books.BookError('目标词条无效')
    if not isinstance(p.get('keywords', []), list) or any((not isinstance(k, str) for k in p.get('keywords', []))):
        raise books.BookError('关键词无效')
    if p['domain'] == 'self' and (p['subject_id'] != 'k' or p['knower_id'] != 'k'):
        raise books.BookError('自我认识必须归属 AI')
    if p['domain'] == 'other':
        from .other_book import metadata
        p.update(metadata(p, {}, p['subject_id'], p['knower_id']))
    if p['domain'] == 'world' and p['kind'] in {'self_reflection', 'theory'}:
        raise books.BookError('自我或理论内容不能写入世界书')
    refs = p.get('evidence')
    if not isinstance(refs, list) or not refs:
        raise books.BookError('缺少原文证据')
    resolved = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise books.BookError('证据结构无效')
        msg = resolve_ref(ref, space)
        quote = ref.get('quote')
        if not msg or not isinstance(quote, str) or len(quote.strip()) < 4 or (quote not in msg['text']):
            raise books.BookError('引文或消息编号无法核实')
        resolved.append({'message_id': msg['id'], 'speaker': msg['speaker'], 'quote': quote, 'timestamp': msg['timestamp'], 'identity_basis': msg['identity_basis'], **source_metadata(msg), 'source_text': msg['text'], 'source_revision': books.revision(msg['text']), 'source_complete': msg.get('source_complete', True), 'complete_statement': msg.get('source_complete', True) and (quote.strip() == msg['text'].strip() or (quote.strip().endswith(tuple('。！？!?')) and (msg['text'].find(quote) == 0 or msg['text'][msg['text'].find(quote) - 1] in '。！？!?\n')))})
    p['evidence'] = resolved
    if 'topics' in p:
        from .topics import normalize
        p['topics'] = normalize(p['topics'], p['body'])
    return p

def make_plan(p, domain=None, name=None, operation=None):
    domain = domain or p['domain']
    name = name or p.get('target_entry') or p['name']
    if domain not in books.DOMAINS or not isinstance(name, str):
        raise books.BookError('目标无效')
    entries = books.catalog(domain)
    targets = [e for e in entries if e['name'] == name]
    if not targets:
        targets = [e for e in entries if name in e['aliases']]
    if not targets and domain == 'world':
        targets = [e for e in entries if books.title_key(e['name']) == books.title_key(name)]
    if len(targets) > 1:
        raise books.BookError('目标名称不唯一')
    target = targets[0] if targets else None
    if target:
        name = target['name']
    operation = operation or p['operation']
    if operation not in {'append', 'replace'}:
        raise books.BookError('处理方式无效')
    before = target['body'] if target else ''
    addition = books.strip_log(p['body'])
    after = addition if operation == 'replace' else before if addition in before else (before + '\n\n' + addition).strip()
    base = target or {'subject_id': p['subject_id'], 'knower_id': p['knower_id'], 'kind': p['kind'], 'core': False, 'aliases': [], 'keywords': [], 'category': ''}
    if domain == 'self':
        base = {**base, 'subject_id': 'k', 'knower_id': 'k', 'kind': 'self_reflection'}
    elif domain == 'other':
        base = {**base, 'knower_id': 'k', 'kind': p['kind'] if p['kind'] in {'preference', 'interpretation', 'agreement'} else 'interpretation'}
    elif base['kind'] in {'theory', 'self_reflection'}:
        base = {**base, 'kind': 'interpretation'}
    from .topics import carry_annotations
    complete_interest_keywords = p.get('keyword_revision_mode') == INTEREST_COMPLETE_KEYWORD_REVISION_V1 and 'keywords' in p and (domain == 'self') and (operation == 'replace') and ((base.get('category') if target else p.get('category')) in {'兴趣', '兴趣爱好'})
    return {'domain': domain, 'name': name, 'before': before, 'after': after, 'revision': target['revision'] if target else 'new', 'operation': operation, 'topics': p.get('topics', carry_annotations(base.get('topics', []), before, after)), **{k: base[k] for k in ('subject_id', 'knower_id', 'kind', 'core', 'aliases')}, **({'category': base.get('category') or p.get('category', '')} if domain == 'self' else {}), **({k: base.get(k, p.get(k, default)) for k, default in {'owner_id': 'k', 'scope': 'person', 'basis': 'inferred', 'state': 'active', 'expires_at': ''}.items()} if domain == 'other' else {}), 'keywords': list(dict.fromkeys(p.get('keywords', []))) if complete_interest_keywords else list(dict.fromkeys([*base['keywords'], *p.get('keywords', [])]))}

def auto_reason(p, review, plan, snapshot):
    if not all((review.get(k) is True for k in ('supported', 'attribution_clear', 'no_conflict', 'entity_match', 'novel'))):
        return '独立审核未全部确认支持、归属、实体、增量与无冲突'
    if p['operation'] != 'append' or plan['core']:
        return '更正、替换或核心自我变化需要确认'
    if not all((e['complete_statement'] for e in p['evidence'])):
        return '引文不是完整陈述，需保留上下文后确认'
    if plan['revision'] == 'new' and books.related_entries(p['name'], p.get('keywords', []), p['body'], p['domain']):
        return '新建内容涉及已有条目，需核对实体或概念合并目标'
    if p.get('target_entry') and plan['revision'] == 'new':
        return '指定目标不存在'
    if plan['domain'] == 'world':
        related = books.related_entries(p['name'], p.get('keywords', []), p['body'])
        identities = {plan['name'], *plan['aliases']}
        if plan['revision'] == 'new' and related:
            return '新建内容涉及已有实体，需核对合并目标'
        if p['kind'] not in {'fact', 'preference'} or p['knower_id'] != 'human':
            return '不是人类伙伴明确陈述的外部事实或偏好'
        if any((e['speaker'] != 'human' for e in p['evidence'])):
            return '包含非人类伙伴自述的证据'
        if p['subject_id'] != 'human' and (not any((len(n) >= 2 and n in e['quote'] for n in identities for e in p['evidence']))):
            return '证据没有明确命名外部主体'
    elif plan['domain'] == 'self':
        if review.get('stable') is not True or len({e['message_id'] for e in p['evidence'] if e['speaker'] == 'k'}) < 2:
            return '自我认识尚缺少多条一致的 AI 自述'
        if any((e['speaker'] != 'k' for e in p['evidence'])):
            return '不能把他人评价自动认作 AI 的自我认识'
    elif plan['domain'] == 'other':
        return '他者书处于存量归属确认阶段，新增长期认识先保留候选'
    elif plan['revision'] != 'new' or review.get('agreed') is not True or {e['speaker'] for e in p['evidence']} != {'k', 'human'}:
        return '定义修订或尚未得到双方明确一致'
    existing = next((e for e in snapshot[p['domain']] if e['name'] == plan['name']), None)
    if plan['revision'] != 'new' and (not existing):
        return '审核之后出现了新目标，需重新核对'
    if existing and any((existing[k] not in {'unknown', p[k]} for k in ('subject_id', 'knower_id'))):
        return '已有主体或观点归属与候选不同'
    if existing and existing['revision'] != plan['revision']:
        return '分析期间目标已变化'
    return ''

async def run_for_date(source_date, session_id, llm, progress_cb=None, status_cb=None):
    """Read the canonical active-day rows, never a rolling 24-hour transcript."""
    from event_chronicle import get_global_chronicle
    if not session_id:
        raise books.BookError('没有可确定的主会话')
    rows = await asyncio.to_thread(get_global_chronicle().get_messages_by_date, source_date)
    rows = [r for r in rows if r.get('session_id') == session_id]
    from neuron_registry import neuron_trace
    with neuron_trace('cognition_maintenance', model='Flash') as trace:
        trace.set_input(f'active_date={source_date}; canonical messages={len(rows)}')
        try:
            from .daily import run_daily
            result = await run_daily(rows, llm, source_date=source_date, session_id=session_id, progress_cb=progress_cb, status_cb=status_cb)
            trace.set_output(f"status={result['status']}; records={len(result.get('items', []))}")
            return result
        except BaseException:
            trace.set_error('认知维护未全部完成，查看维护记录')
            raise

def commit(item, plan, automatic):
    with books.LOCK:
        item.update(status='applying', plan=plan, automatic=automatic)
        write(item)
        try:
            entry = books.save(plan['domain'], plan['name'], {**plan, 'body': plan['after']}, receipt='maintenance:' + item['id'], editor=('AI 自主整理自动更新' if plan['domain'] in {'self', 'other'} else '世界书事实核验自动更新') if automatic else '人类伙伴确认认知维护候选', provenance={'maintenance_item_id': item['id'], 'source_date': item['source_date'], 'automatic': automatic, 'subject_id': item['proposal']['subject_id'], 'knower_id': item['proposal']['knower_id'], 'evidence_ids': list(dict.fromkeys((e['message_id'] for e in item['proposal']['evidence']))), 'evidence_sources': [{'message_id': e['message_id'], **source_metadata(e)} for e in item['proposal']['evidence']]})
        except books.BookError:
            item.update(status='observing' if item.get('evolution_version') == 2 else 'error' if automatic and plan['domain'] in {'self', 'other'} else 'pending', reason='目标或版本校验未通过，保留原文等待重新整理')
            write(item)
            raise
        item.update(status='auto_applied' if automatic else 'applied', result_revision=entry['revision'], completed_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        write(item)
        return item

def preview(rid, options):
    with books.LOCK:
        item = get(rid)
        if item['status'] not in {'pending', 'applying'}:
            raise books.BookError('该记录无需确认')
        if item['status'] == 'applying':
            plan = item['plan']
        else:
            plan = make_plan(item['proposal'], options.get('domain'), options.get('name'), options.get('operation'))
        return {**plan, 'item_revision': digest(item), 'id': rid, 'related': books.related_entries(item['proposal']['name'], item['proposal'].get('keywords', []), item['proposal']['body'], plan['domain'])}

def music_note_only_world(item, domain):
    refs = item.get('proposal', {}).get('evidence', [])
    return domain == 'world' and item.get('session_id') == 'music' and bool(refs) and all((str(ref.get('message_id', '')).startswith('music-note:') for ref in refs))

def decide(rid, action, plan=None, expected='', new_entity_confirmed=False):
    with books.LOCK:
        item = get(rid)
        if item['status'] not in {'pending', 'applying'}:
            raise books.RevisionConflict('记录已处理，请刷新')
        if digest(item) != expected:
            raise books.RevisionConflict('记录已变化，请重新预览')
        if action == 'reject':
            if item['status'] == 'applying':
                raise books.BookError('写入结果待核对，请先恢复收尾，不能直接忽略')
            item.update(status='rejected', completed_at=datetime.now().astimezone().isoformat(timespec='seconds'))
            write(item)
            return item
        if action != 'apply' or not plan:
            raise books.BookError('处理动作无效')
        if music_note_only_world(item, plan['domain']):
            raise books.BookError('这条音乐建议只有 AI 的感想，没有设备或歌单操作回执，不能确认为外部事实')
        committed = books.receipt_present(plan['domain'], plan['name'], 'maintenance:' + rid)
        if plan['revision'] == 'new' and (not committed) and any((e['name'] == plan['name'] for e in books.catalog(plan['domain']))):
            raise books.RevisionConflict('预览后目标已创建，请重新预览')
        if plan['revision'] == 'new' and (not committed) and (not new_entity_confirmed):
            p = item['proposal']
            if books.related_entries(p['name'], p.get('keywords', []), p['body'], plan['domain']):
                raise books.BookError('存在相关条目，请选择已有目标或明确确认这是不同实体／概念')
        item['human_reclassified'] = plan['domain'] != item['proposal']['domain'] or plan['name'] != (item['proposal'].get('target_entry') or item['proposal']['name'])
        return commit(item, plan, item.get('automatic', False) if item['status'] == 'applying' else False)

async def run(messages, llm, *, source_date, session_id, factual=False, memory=None, generation='', extra_evidence=(), habit_mode=None, habit_cycle_policy=None):
    """Called by topic-end orchestration; retries reuse evidence-derived run IDs."""
    from .maintenance_sources import merge_evidence
    evidence = merge_evidence(sources(messages), extra_evidence)
    prepared = {'requests': [], 'evidence': []}
    if factual:
        from .habit_review import prepare
        prepared = prepare(source_date, session_id, context_sources=habit_mode is not None)
        from .habit_review_cycle import MODES as cycle_modes
        if habit_mode in cycle_modes:
            from .habit_local_contract import SPAN_VERSION, MESSAGE_EVIDENCE
            prepared['requests'] = [{**r, 'output_contract': SPAN_VERSION, 'evidence_selection_contract': MESSAGE_EVIDENCE} for r in prepared['requests']]
    return await run_evidence(merge_evidence(evidence, prepared['evidence']), llm, source_date=source_date, session_id=session_id, factual=factual, memory=memory, generation=generation, review_requests=prepared['requests'], habit_mode=habit_mode, habit_cycle_policy=habit_cycle_policy)

async def _finish_habits(state, requests, space, checkpoint, *, max_request_bytes=None, habit_transport=None, habit_batch_limit=4):
    from . import habit_review
    from .habit_claim_review import review as review_habit_claims
    from . import habit_review_cycle as cycle
    frozen = state.get('extraction_material', {})
    if frozen.get('habit_mode') in cycle.MODES:
        if 'habit_cycle_material' not in state:
            state['habit_cycle_material'] = {'snapshot': {d: books.catalog(d) for d in books.DOMAINS}}
            checkpoint()
        cycle_limits = {'max_calls': habit_batch_limit}
        if frozen.get('habit_cycle_policy') is not None:
            from .habit_natural_policy import limits
            cycle_limits = limits(frozen['habit_cycle_policy'], len(requests))
        result = await cycle.run(state, requests, space, state['habit_cycle_material']['snapshot'], checkpoint, source_date=state['source_date'], run_id=state['id'], transport=habit_transport, **cycle_limits)
        state['habit_claim_review'] = {'status': 'reviewed' if result['status'] == 'completed' else 'pending', 'deferred_count': len(result['deferred']), 'contract': result['version'], 'model_calls_reserved': result['model_calls_reserved']}
        if result.get('cost_limit_cny') is not None:
            from .habit_cycle_cost import summary
            state['habit_claim_review']['budget'] = summary(result)
    else:
        state['habit_review_approved_ids'], state['habit_claim_review'] = await review_habit_claims(requests, state.get('habit_review_reports', []), space, state.get('habit_review_approved_ids', []), max_request_bytes=max_request_bytes)
    state['phase'] = 'habit_finish'
    checkpoint()
    state['habit_review'] = habit_review.finish(requests, state.get('habit_review_reports', []), state.get('habit_review_approved_ids', []), state.get('items', []), space, source_date=state['source_date'], session_id=state['session_id'], run_id=state['id'], decisions=[*state.get('habit_review_decisions', []), *state.get('habit_claim_review', {}).get('decisions', [])])

@defer_updates
async def run_evidence(evidence_list, llm, *, source_date, session_id, factual=False, memory=None, generation='', review_requests=(), resume_habits=False, habit_review_max_request_bytes=None, habit_mode=None, habit_transport=None, habit_batch_limit=4, resume_habit_batches=False, habit_cycle_policy=None):
    """Internal attributed observations; never map device facts into user speech."""
    if review_requests and (not factual):
        raise books.BookError('行为复核只属于事实日结通道')
    if habit_cycle_policy is not None:
        from .habit_natural_policy import validate as validate_habit_policy
        validate_habit_policy(habit_cycle_policy)
        if habit_mode != habit_cycle_policy['mode'] or not factual:
            raise books.BookError('自然习惯合同仅属于对应事实模式')
    if habit_mode is not None:
        from .habit_batches import MODE
        from .habit_review_cycle import MODES as cycle_modes
        if habit_mode not in {MODE, *cycle_modes} or not factual or resume_habits:
            raise books.BookError('新习惯分批模式只接受新事实任务，不改旧恢复身份')
    elif habit_transport is not None:
        raise books.BookError('专用习惯提取仅属于显式新模式')
    if resume_habit_batches and (habit_mode is None or not review_requests):
        raise books.BookError('分批续处理须显式指定新模式及其原请求')
    if resume_habits and (not factual or not review_requests) or (habit_review_max_request_bytes is not None and (not resume_habits)):
        raise books.BookError('习惯收尾恢复须显式指定原事实日结与原复核请求')
    if not evidence_list:
        return {'status': 'no_evidence', 'items': []}
    from . import world_evolution as evolution
    identity = {'date': source_date, 'session': session_id, 'messages': evidence_list, 'pipeline': evolution.PIPELINE if factual else 'legacy'}
    if generation:
        identity['generation'] = generation
    if habit_mode is not None:
        identity['habit_mode'] = habit_mode
    if habit_cycle_policy is not None:
        identity['habit_cycle_policy'] = habit_cycle_policy
    if review_requests:
        identity['habit_review_requests'] = review_requests
        from .habit_review import contract_version
        identity['habit_review_contract'] = contract_version(source_date)
    run_id = digest(identity)
    path = folder() / 'runs' / (run_id + '.json')
    async with _run_lock:
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        from .habit_diagnostics import public_fields

        def checkpoint():
            state.update(public_fields(state))
            books.atomic_text(path, json.dumps(state, ensure_ascii=False, indent=2))
        if resume_habit_batches:
            frozen = state.get('extraction_material', {})
            if state.get('status') not in {'completed', 'completed_with_rejections'} or frozen.get('habit_mode') != habit_mode or frozen.get('review_requests') != list(review_requests) or (frozen.get('habit_cycle_policy') != habit_cycle_policy):
                raise books.BookError('分批续处理只接受事实已完成的原新模式任务')
            from .habit_batch_resume import resume as resume_batches
            if habit_mode not in cycle_modes:
                await resume_batches(state, llm, checkpoint, transport=habit_transport, max_new_calls=habit_batch_limit)
            await _finish_habits(state, frozen['review_requests'], frozen['space'], checkpoint, habit_transport=habit_transport, habit_batch_limit=habit_batch_limit)
            state['habit_finish_updated_at'] = datetime.now().astimezone().isoformat(timespec='microseconds')
            checkpoint()
            return {**state, **public_fields(state)}
        if resume_habits:
            if state.get('status') not in {'completed', 'completed_with_rejections'}:
                raise books.BookError('习惯收尾恢复只接受已完成原事实阶段的同源任务')
            frozen = state.get('extraction_material', {})
            if not isinstance(frozen.get('space'), list):
                raise books.BookError('原复核来源未保存，保留原件等待核对')
            await _finish_habits(state, frozen.get('review_requests', review_requests), frozen['space'], checkpoint, max_request_bytes=habit_review_max_request_bytes)
            state['habit_finish_updated_at'] = datetime.now().astimezone().isoformat(timespec='microseconds')
            checkpoint()
            return {**state, **public_fields(state)}
        if state.get('status') in {'completed', 'completed_with_rejections'}:
            return {**state, **public_fields(state)}
        state = {**state, 'id': run_id, 'source_date': source_date, 'status': 'running', 'instance': _instance, 'started_at': datetime.now().astimezone().isoformat(timespec='seconds')}
        if review_requests:
            state.setdefault('habit_review', {'completed': 0, 'pending': len(review_requests)})
        if factual and 'historical_observations' not in state:
            selected, deferred = evolution.collect(records(), source_date, session_id)
            state.update(historical_observations=selected, deferred_material_ids=deferred, evolution_version=2, session_id=session_id, historical_suggestions_not_evidence=evolution.legacy_hints())
        checkpoint()
        try:
            state['phase'] = 'catalog'
            snapshot = {domain: [{k: e[k] for k in ('name', 'body', 'aliases', 'keywords', 'revision', 'subject_id', 'knower_id', 'kind', 'core', 'owner_id', 'scope', 'basis', 'state', 'expires_at', 'entry_id', 'topics')} for e in books.catalog(domain)] for domain in books.DOMAINS}
            space = list(evidence_list)
            _space_ids = {e['id'] for e in space}
            for observation in state.get('historical_observations', []):
                for ref in observation.get('evidence', []):
                    if ref.get('message_id') not in _space_ids:
                        _space_ids.add(ref['message_id'])
                        space.append({'id': ref['message_id'], 'text': ref.get('source_text', ref.get('quote', '')), **source_metadata(ref), 'speaker': ref.get('speaker', ''), 'timestamp': ref.get('timestamp', ''), 'identity_basis': ref.get('identity_basis', ''), 'source_complete': ref.get('source_complete', bool(ref.get('source_text'))), 'source_kind': ref.get('source_kind', '') if ref.get('source_text') else 'historical_excerpt'})
            _n_of = {e['id']: i for i, e in enumerate(space, 1)}
            source_texts = {e['id']: e['text'] for e in space}
            if len(_n_of) != len(space):
                raise ValueError('world_material_duplicate_id')
            from world_book.suggestions import build_flags_section
            flags = build_flags_section(source_date) if books.ROOT == Path(__file__).resolve().parents[1] else ''
            payload = {'source_date': source_date, 'books': snapshot, 'messages': model_materials(space), 'attention_hints_not_evidence': flags, 'historical_observations': numbered_references(state.get('historical_observations', []), _n_of, source_texts)}
            if factual:
                payload['memory'] = memory or []
                payload['historical_suggestions_not_evidence'] = state.get('historical_suggestions_not_evidence', [])
            if review_requests:
                from . import habit_review
            if review_requests and habit_mode is None:
                target_catalogue = []
                payload['habit_review_requests'] = habit_review.model_requests(review_requests, _n_of, local=True, snapshot=snapshot, target_catalogue=target_catalogue)
                if target_catalogue:
                    payload['habit_target_choices'] = target_catalogue
            if 'proposals' not in state:
                if 'extraction_material' not in state:
                    state['extraction_material'] = {'snapshot': snapshot, 'space': space, 'payload': payload, 'review_requests': list(review_requests), **({'habit_mode': habit_mode} if habit_mode is not None else {}), **({'habit_cycle_policy': habit_cycle_policy} if habit_cycle_policy is not None else {})}
                    checkpoint()
            if 'extraction_material' in state:
                frozen = state['extraction_material']
                if frozen.get('habit_mode') != habit_mode:
                    raise books.BookError('冻结习惯模式不一致，保留旧调用身份')
                if frozen.get('habit_cycle_policy') != habit_cycle_policy:
                    raise books.BookError('冻结自然习惯合同不一致，保留旧调用身份')
                snapshot, space, payload = (frozen['snapshot'], frozen['space'], frozen['payload'])
                review_requests = frozen.get('review_requests', [{k: v for k, v in r.items() if k != 'output_contract'} for r in review_requests])
                _n_of = {e['id']: i for i, e in enumerate(space, 1)}
                source_texts = {e['id']: e['text'] for e in space}
            shared_material_keys = tuple(payload)
            if 'proposals' not in state:
                state['phase'] = 'extraction'
                if factual:
                    from .factual import EXTRACTION as extraction_prompt
                else:
                    extraction_prompt = EXTRACTION
                if review_requests and habit_mode is None:
                    extraction_prompt += '\n' + habit_review.extraction_contract(review_requests)
                with usage_scope('cognition_world_extract' if factual else 'cognition_maintenance_extract'):
                    from .extraction_output import run as extract_output
                    extraction_raw = await extract_output(state, CognitionPrompt(extraction_prompt + REFERENCE_CONTRACT, payload, stage='world_extract' if factual else 'maintenance_extract', source_date=source_date, run_id=run_id, shared_material_keys=shared_material_keys), llm, checkpoint)
                state['phase'] = 'parse_extraction'
                proposals = parse_response(extraction_raw, 'proposals')
                if review_requests and habit_mode is None:
                    assessment = habit_review.report_assessment(extraction_raw, review_requests, space, snapshot)
                    state['habit_review_reports'] = assessment['reports']
                    state['habit_report_rejections'] = assessment['rejections']
                state['phase'] = 'validate_proposals'
                checked, source_indexes, rejections = ([], [], [])
                for index, proposal in enumerate(proposals):
                    state['proposal_index'] = index
                    try:
                        candidate = validate(proposal, space)
                        if factual and (candidate.get('domain') != 'world' or candidate.get('operation') != 'replace'):
                            raise books.BookError('世界书必须返回完整修订正文')
                        if factual and music_note_only_world({'session_id': session_id, 'proposal': candidate}, candidate['domain']):
                            raise books.BookError('AI 的音乐感想不能单独证明设备播放或歌单变更')
                        if factual:
                            if type(candidate.get('observation', False)) is not bool:
                                raise books.BookError('观察状态必须是布尔值')
                            resolved = candidate.get('resolves_observations', [])
                            allowed = {o['record_id'] for o in state['historical_observations']}
                            if not isinstance(resolved, list) or any((not isinstance(x, str) or x not in allowed for x in resolved)):
                                raise books.BookError('解决记录必须来自本轮待重整材料')
                    except books.BookError as exc:
                        if factual:
                            isolation_id = evolution.isolate(proposal, space, source_date=source_date, session_id=session_id, run_id=run_id, source_index=index, reason=str(exc))
                            state.setdefault('isolated_items', []).append(isolation_id)
                        rejections.append({'source_index': index, 'stage': 'validation', 'error_type': type(exc).__name__, 'reason': str(exc)})
                        continue
                    checked.append(candidate)
                    source_indexes.append(index)
                state['proposals'] = checked
                state['proposal_source_indexes'] = source_indexes
                state['proposal_rejections'] = rejections
                state.pop('proposal_index', None)
                checkpoint()
            proposals = state['proposals']
            source_indexes = state.get('proposal_source_indexes', list(range(len(proposals))))
            if 'reviews' not in state:
                if review_requests and habit_mode is not None and (habit_mode not in cycle_modes):
                    from .habit_batches import extract as extract_habit_batches
                    await extract_habit_batches(state, review_requests, space, snapshot, proposals, checkpoint, source_date=source_date, run_id=run_id, transport=habit_transport, max_new_calls=habit_batch_limit)
                elif review_requests and habit_mode is None:
                    from .habit_report_repair import repair as repair_habit_reports
                    await repair_habit_reports(state, review_requests, space, snapshot, {**payload, 'existing_proposals': numbered_references(proposals, _n_of, source_texts)}, llm, checkpoint, source_date=source_date, run_id=run_id, shared_material_keys=tuple((k for k in shared_material_keys if k != 'habit_review_requests')))
                state['phase'] = 'review'
                from .factual import REVIEW as factual_review
                from .output_repair import run as validated_output
                from .world_review_output import validator as review_validator
                from .habit_claim_support import model_report as habit_model_report
                from .habit_claim_review import eligible_report
                request_by_id = {r['id']: r for r in review_requests}
                claim_ids = {r['id'] for r in state.get('habit_review_reports', []) if r['id'] in request_by_id and eligible_report(request_by_id[r['id']], r)}
                general_requests = [r for r in review_requests if r['id'] not in claim_ids]
                general_reports = [r for r in state.get('habit_review_reports', []) if r['id'] not in claim_ids]
                if habit_mode is not None:
                    reported_ids = {r['id'] for r in general_reports}
                    general_requests = [r for r in general_requests if r['id'] in reported_ids]
                review_payload = {**payload}
                if review_requests:
                    review_payload['habit_review_requests'] = habit_review.model_requests(general_requests, _n_of)
                with usage_scope('cognition_world_review' if factual else 'cognition_maintenance_review'):
                    review_raw = await validated_output(CognitionPrompt((factual_review if factual else REVIEW) + REFERENCE_CONTRACT + ('\n' + habit_review.review_contract(general_requests) if general_requests else ''), {**review_payload, 'proposals': numbered_references(proposals, _n_of, source_texts), **({'habit_reviews': [habit_model_report(report, _n_of) for report in general_reports]} if general_requests else {})}, stage='world_review' if factual else 'maintenance_review', source_date=source_date, run_id=run_id, shared_material_keys=shared_material_keys), llm, review_validator(len(proposals))) if proposals or general_requests else None
                    raw = review_raw['reviews'] if review_raw is not None else []
                    if review_requests:
                        state['habit_review_approved_ids'] = habit_review.approvals(json.dumps(review_raw, ensure_ascii=False), review_requests)
                        state['habit_review_decisions'] = habit_review.approval_decisions(json.dumps(review_raw, ensure_ascii=False), review_requests, general_reports, space)
                review_map, duplicate_indexes, unbound_reviews = ({}, set(), 0)
                for review_item in raw:
                    if not isinstance(review_item, dict) or type(review_item.get('index')) is not int or review_item['index'] not in range(len(proposals)):
                        unbound_reviews += 1
                        continue
                    review_index = review_item['index']
                    if review_index in review_map:
                        duplicate_indexes.add(review_index)
                        continue
                    review_map[review_index] = review_item
                for review_index in duplicate_indexes:
                    review_map.pop(review_index, None)
                reviewed_proposals, reviewed_source_indexes, reviews = ([], [], [])
                for review_index, proposal in enumerate(proposals):
                    review_item = review_map.get(review_index)
                    if review_item is None:
                        state.setdefault('proposal_rejections', []).append({'source_index': source_indexes[review_index], 'stage': 'review', 'error_type': 'ReviewContractError', 'reason': '审核结果缺项或编号重复', **({'proposal': proposal} if factual else {})})
                        if not factual:
                            continue
                        review_item = {'index': review_index, 'reason': '核对结果缺项或编号重复，保留材料继续整理'}
                    reviews.append({**review_item, 'index': len(reviewed_proposals)})
                    reviewed_proposals.append(proposal)
                    reviewed_source_indexes.append(source_indexes[review_index])
                state['proposals'] = proposals = reviewed_proposals
                state['proposal_source_indexes'] = source_indexes = reviewed_source_indexes
                state['reviews'] = reviews
                state['discarded_review_count'] = unbound_reviews
                state['snapshot'] = snapshot
                checkpoint()
            reviewed_snapshot = state['snapshot']
            if habit_mode is not None:
                from .habit_batches import check_sources as check_habit_sources
                check_habit_sources(review_requests, space)
            state['phase'] = 'commit'
            result_ids = list(dict.fromkeys(state.get('isolated_items', [])))
            for index, p in enumerate(proposals):
                rid = digest({'session': session_id, 'proposal': p})
                result_ids.append(rid)
                with books.LOCK:
                    old = None
                    if record_path(rid).exists():
                        old = get(rid)
                        if old['status'] == 'applying':
                            try:
                                commit(old, old['plan'], old['automatic'])
                            except books.RevisionConflict:
                                old.update(status='observing' if factual else 'pending', reason='恢复时目标版本已变化，保留材料重新整理')
                                write(old)
                        if not factual or old['status'] not in evolution.UNRESOLVED:
                            if factual:
                                evolution.finish_materials(state, old)
                            continue
                    review = next((r for r in state['reviews'] if r['index'] == index))
                    try:
                        plan = make_plan(p)
                        from .factual import auto_reason as factual_reason
                        reason = (factual_reason if factual else auto_reason)(p, review, plan, reviewed_snapshot)
                    except books.BookError as exc:
                        if not factual:
                            raise
                        plan, reason = (evolution.fallback_plan(p), str(exc))
                    item = {**(old or {}), 'id': rid, 'status': 'observing' if factual and reason else 'pending', 'source_date': source_date, 'session_id': session_id, 'created_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'proposal': p, 'review': review, 'reason': reason or '证据与独立审核通过', 'plan': plan}
                    if factual:
                        item['evolution_version'] = 2
                        if old:
                            item['created_at'] = old['created_at']
                            item['previous_attempts'] = [*old.get('previous_attempts', []), {'status': old['status'], 'reason': old['reason'], 'review': old['review'], 'plan': old['plan'], 'run_id': old.get('run_id', '')}]
                        item['run_id'] = run_id
                    if factual and p.get('observation'):
                        item['status'] = 'observing'
                        write(item)
                        continue
                    write(item)
                    if factual and reason:
                        continue
                    if plan['after'] == plan['before'] or (p['operation'] == 'append' and all((e['quote'] in plan['before'] for e in p['evidence']))):
                        item.update(status='unchanged', reason='正文已包含该内容，无重复写入', result_revision=plan['revision'], completed_at=datetime.now().astimezone().isoformat(timespec='seconds'))
                        write(item)
                        if factual:
                            evolution.finish_materials(state, item)
                    elif not reason:
                        if not factual:
                            statements = []
                            for e in p['evidence']:
                                source = e['speaker']
                                if source in {'system', 'device', 'observation'}:
                                    label = {'system': 'MIRROW 系统事件记录', 'device': '设备回执', 'observation': 'AI 的环境观察记录'}[source]
                                elif source in books.SUBJECTS and source != 'unknown':
                                    label = books.SUBJECTS[source] + '的明确陈述'
                                else:
                                    raise books.BookError('证据说话者未确认，不能自动写入')
                                text = f'{label}（{source_date}，原文）：\n' + '\n'.join(('> ' + line for line in e['quote'].splitlines()))
                                if text not in statements:
                                    statements.append(text)
                            plan['after'] = (plan['before'] + '\n\n' + '\n\n'.join(statements)).strip()
                        try:
                            commit(item, plan, True)
                            if factual:
                                evolution.finish_materials(state, item)
                        except books.RevisionConflict:
                            item.update(status='observing' if factual else 'pending', reason='写入前目标版本已变化，保留材料重新整理')
                            write(item)
            rejected_count = len(state.get('proposal_rejections', []))
            discarded_review_count = int(state.get('discarded_review_count', 0))
            final_status = 'completed_with_rejections' if rejected_count or discarded_review_count else 'completed'
            state.update(status='running', items=result_ids, rejected_proposal_count=rejected_count, discarded_review_count=discarded_review_count)
            if final_status == 'completed_with_rejections':
                state['message'] = '可核实的候选已完成；无法逐条核实的候选已隔离，未写入认知书'
            if factual:
                evolution.mark_considered(state, considered_at=datetime.now().astimezone().isoformat(timespec='seconds'))
            if review_requests:
                await _finish_habits(state, review_requests, space, checkpoint, habit_transport=habit_transport, habit_batch_limit=habit_batch_limit)
            for key in ('error_type', 'error_detail', 'output_failure', 'proposal_index', 'message'):
                if key != 'message' or final_status == 'completed':
                    state.pop(key, None)
            state.update(status=final_status, completed_at=datetime.now().astimezone().isoformat(timespec='seconds'))
            checkpoint()
            return state
        except BaseException as exc:
            state.update(status='error', message='本轮未全部完成；已落地记录保留，下次相同来源重试可恢复')
            state['error_type'] = type(exc).__name__
            state['error_detail'] = str(exc) if isinstance(exc, books.BookError) else 'HTTP ' + str(exc.status_code) if hasattr(exc, 'status_code') else '详见本次调用诊断'
            from .output_repair import ContractError, diagnostic
            if isinstance(exc, ContractError):
                state['output_failure'] = diagnostic(exc)
            checkpoint()
            raise
