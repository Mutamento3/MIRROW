"""Precompiled cognition briefs: asynchronous compilation, text-only fast reads."""
import asyncio
import json
import logging
import threading
from contextvars import ContextVar
from functools import wraps
from datetime import datetime
from . import books
from .brief_contract import CONTRACT, VERSION, fingerprint, material, validate, review_material, reviewed
from .brief_selection import REVIEW, review_material as selection_review_material, source_material
from .prompt_request import CognitionPrompt
from .brief_temporal import render as render_temporal
from .profile_scopes import LABELS, TASK_SCOPES, empty_capsules

logger = logging.getLogger(__name__)
_cached = {}
_dirty_roots = set()
_current_sources = {}
_guard = threading.RLock()
_compile_lock = asyncio.Lock()
_loop = None
_llm = None
_task = None
_pending = False
_pending_limit = 2
_failed_refreshes = {}
_batch_depth = ContextVar('cognition_brief_batch_depth', default=0)
_batch_attempted = ContextVar('cognition_brief_batch_attempted', default=False)
# These formats share identities/capsules; new contracts still require a new
# compilation. A verified, unchanged prior view can bridge that compilation.
_READABLE_VERSIONS = {'cognition-brief-v7', 'cognition-brief-v8', 'cognition-brief-v9', 'cognition-brief-v10', 'cognition-brief-v11', 'cognition-brief-v12', 'cognition-brief-v13', 'cognition-brief-v14', 'cognition-brief-v15', 'cognition-brief-v16', 'cognition-brief-v17', 'cognition-brief-v18', 'cognition-brief-v19', 'cognition-brief-v20', 'cognition-brief-v21', VERSION}


def source_refs(rows):
    return [{key: row.get(key) for key in
             ('n', 'domain', 'name', 'entry_id', 'revision', 'subject_id', 'basis')} for row in rows]


def path():
    return books.ROOT / 'data/cognition_brief/current.json'


def snapshot():
    return {domain: books.catalog(domain) for domain in ('self', 'other', 'world')}


def invalidate():
    """Commit hook: never performs model work or lets projection failure fail a book save."""
    with _guard:
        _dirty_roots.add(str(books.ROOT))
        _current_sources.pop(str(books.ROOT), None)
    # Book commits already run outside the reply path. Retain a source-only
    # snapshot here so unrelated approved knowledge stays available during
    # compilation, including when compilation fails.
    try:
        with books.LOCK:
            current = source_refs(material(snapshot()))
            with _guard:
                _current_sources[str(books.ROOT)] = current
    except Exception:
        logger.warning('Cognition brief source check unavailable')
    if not _batch_depth.get():
        from .recall_lifecycle import refresh as refresh_recall
        refresh_recall()
        schedule_refresh()


def defer_updates(func):
    """One notification per existing maintenance transaction, not per book/event."""
    @wraps(func)
    async def wrapped(*args, **kwargs):
        outer = not _batch_depth.get()
        attempt_token = _batch_attempted.set(False) if outer else None
        token = _batch_depth.set(_batch_depth.get() + 1)
        try:
            return await func(*args, **kwargs)
        finally:
            attempted = _batch_attempted.get()
            _batch_depth.reset(token)
            if outer:
                _batch_attempted.reset(attempt_token)
                if str(books.ROOT) in _dirty_roots:
                    from .recall_lifecycle import refresh as refresh_recall
                    refresh_recall()
                    schedule_refresh(recovery_only=attempted)
    return wrapped


def _read():
    target = path()
    if not target.exists():
        return None
    stamp = (target.stat().st_mtime_ns, target.stat().st_size)
    with _guard:
        prior = _cached.get(str(target))
        if prior and prior[0] == stamp:
            return prior[1]
    data = json.loads(target.read_text('utf-8'))
    if data.get('version') not in _READABLE_VERSIONS:
        return None
    with _guard:
        _cached[str(target)] = (stamp, data)
    return data


def read_context(task='writing', *, subject_ids=None, covered_self_book=()):
    """No LLM, embeddings or catalog scans in the reply path."""
    if task not in TASK_SCOPES:
        raise ValueError('未知认知简版消费范围')
    subjects = set(('k', 'human') if subject_ids is None else subject_ids)
    if subjects - {'k', 'human'}:
        raise ValueError('未知画像对象')
    try:
        data = _read()
    except (OSError, ValueError, TypeError):
        return '认知简版：当前读取不可用。'
    if not data:
        return '认知简版：尚未建立。'
    with _guard:
        pending = str(books.ROOT) in _dirty_roots
        current = _current_sources.get(str(books.ROOT))
    if pending:
        if current is None:
            return '认知简版：来源正在核对，刷新尚未完成。'
        from .brief_serving import unchanged_view
        data, availability = unchanged_view(data, current)
        if not data['identities'] and not availability['available_point_count']:
            return '认知简版：来源已修订，刷新尚未完成。'
    if not data['identities'] and not any(c['points'] for c in data['capsules']):
        return '认知简版：当前没有可用认识条目。'
    rows = ['认知简版（来自现行认知书；事实、自我认识与对他者的理解保留各自性质）']
    if data.get('version') != VERSION:
        rows[0] = '上一版认知简版（新版整理尚未完成；以下仍保留原有认识及性质）'
    if pending:
        rows[0] = '认知简版（以下认识的来源版本仍有效；来源已修订的部分暂缺，刷新尚未完成）'
    for identity in data['identities']:
        if identity['subject_id'] not in subjects:
            continue
        aliases = '、'.join(identity.get('reading_aliases', []) if task == 'reading' else identity['aliases'])
        name = books.SUBJECTS[identity['subject_id']] if task == 'reading' else identity['name']
        rows.append(f"{books.SUBJECTS[identity['subject_id']]}的真实身份称呼：{name}" + (f'；别名：{aliases}' if aliases else ''))
    if task != 'reading':  # Reading consumes only names, not possibly plot-bearing prose.
        scopes = set(TASK_SCOPES[task])
        seen_text = set()
        for capsule in data['capsules']:
            if capsule['subject_id'] not in subjects or capsule['scope'] not in scopes or not capsule['points']:
                continue
            points = [point for point in capsule['points']
                      if (capsule['subject_id'], point['text'], point.get('source_voice'), json.dumps(point.get('temporal', {}), sort_keys=True)) not in seen_text]
            seen_text.update((capsule['subject_id'], point['text'], point.get('source_voice'), json.dumps(point.get('temporal', {}), sort_keys=True)) for point in points)
            if not points:
                continue
            who = books.SUBJECTS[capsule['subject_id']]
            scope = LABELS[capsule['scope']]
            point_renderer = None
            if covered_self_book:
                from .brief_coverage import render_with_coverage
                from functools import partial
                point_renderer = partial(render_with_coverage, subject_id=capsule['subject_id'],
                    sources={source['n']: source for source in data.get('sources', [])},
                    covered=covered_self_book)
            rows.append(f'【{who} · {scope}】\n' + render_temporal(points, point_renderer=point_renderer))
    return '\n\n'.join(rows)


async def refresh(llm, *, source_date='', force=False):
    async with _compile_lock:
        root = books.ROOT
        with books.LOCK:
            rows = material(snapshot())
        revision = fingerprint(rows)
        try:
            result = await _refresh_locked(llm, root, rows, revision, source_date=source_date, force=force)
        except BaseException:
            # A notification for the same failed source is not a new recovery
            # request. Explicit refresh remains available and resumes its candidate.
            _failed_refreshes[str(root)] = revision
            from . import brief_checkpoint
            try:
                brief_checkpoint.record_failure(root, brief_checkpoint.binding(
                    VERSION, revision, source_refs(rows), CONTRACT, REVIEW))
            except (OSError, books.BookError, ValueError):
                logger.warning('Cognition brief failure marker unavailable; candidate retained')
            raise
        _failed_refreshes.pop(str(root), None)
        from . import brief_checkpoint
        try:
            brief_checkpoint.record_failure(root, brief_checkpoint.binding(
                VERSION, revision, source_refs(rows), CONTRACT, REVIEW), clear=True)
        except (OSError, books.BookError, ValueError):
            logger.warning('Cognition brief failure marker cleanup unavailable')
        return result


async def _refresh_locked(llm, root, rows, revision, *, source_date, force):
    if _batch_depth.get():
        _batch_attempted.set(True)
    previous = _read()
    with _guard:
        _current_sources[str(root)] = source_refs(rows)
        if previous and previous.get('sources') == source_refs(rows):
            # Contract-only upgrades need not blank an unchanged source.
            # A changed book stays dirty until its new view is published.
            _dirty_roots.discard(str(root))
        elif previous:
            _dirty_roots.add(str(root))
    if not force and previous and previous.get('version') == VERSION and previous.get('source_revision') == revision:
        with _guard:
            _dirty_roots.discard(str(root))
        return {'status': 'cached', 'source_revision': revision}
    rejected_count = 0
    selected_counts = {}
    retained_count = 0
    candidate_resumed = False
    if not rows:
        result = {'identities': [], 'capsules': empty_capsules()}
    else:
        from . import brief_checkpoint
        identity = brief_checkpoint.binding(VERSION, revision, source_refs(rows), CONTRACT, REVIEW)
        candidate = None if force else brief_checkpoint.load(root, identity)
        if candidate is None:
            from uuid import uuid4
            from .output_repair import run as validated_output, ContractError
            from .brief_projection_output import validator
            prompt = CognitionPrompt(CONTRACT, {'books': source_material(rows, fragments=True)}, stage='brief_projection',
                                     source_date=source_date, preferred_thinking=False)
            result, rejected_count = await validated_output(prompt, llm, validator(rows),
                binding={**identity, **({'explicit_generation': uuid4().hex} if force else {})},
                checkpoint_date='')
            if rejected_count and not any(c['points'] for c in result['capsules']):
                # A valid envelope with rejected evidence is not a request to
                # invent replacement claims. Its saved output is reused as-is.
                error = ContractError('brief_projection_no_valid_points',
                                      '认知简版全部认识候选来源或时间无效，保留上一版')
                error.stage = 'brief_projection'
                raise error
            from .brief_serving import merge_unchanged_points
            result, retained_count = merge_unchanged_points(result, previous, source_refs(rows))
            result = validate(result, rows, require_selection=True, candidate_pool=True)
            generation_id = brief_checkpoint.save(root, identity, result, rejected_count=rejected_count,
                                  retained_count=retained_count)
        else:
            candidate_resumed = True
            result = candidate['result']
            rejected_count = candidate['rejected_count']
            retained_count = candidate['retained_count']
            generation_id = candidate['generation_id']
        # A derived checkpoint never bypasses current literal/temporal
        # validation or the independent review/publication guards below.
        result = validate(result, rows, require_selection=True, candidate_pool=True)
        selected_counts = {(c['subject_id'], c['scope']): len(c['points']) for c in result['capsules']}
        review_input = review_material(result, rows)
        if review_input['candidates']:
            from .output_repair import run as validated_output
            from .brief_review_output import validator
            result, semantic_rejections = await validated_output(CognitionPrompt(
                REVIEW, selection_review_material(review_input), stage='brief_review', source_date=source_date,
                preferred_thinking=False), llm, validator(result, review_input),
                binding={'candidate_generation': generation_id, 'version': VERSION}, checkpoint_date='')
            rejected_count += semantic_rejections
            if (any(candidate['kind'] == 'point' for candidate in review_input['candidates'])
                    and not any(c['points'] for c in result['capsules'])):
                return {'status': 'completed_with_rejections', 'published': False,
                        'reason': 'no_approved_points', 'source_revision': revision,
                        'rejected_point_count': rejected_count, 'candidate_resumed': candidate_resumed}
    # Semantic approval and size arithmetic have separate responsibilities.
    # Keep complete approved points in their existing order; omitted points
    # stay in their full authority books and candidate checkpoint.
    from .brief_budget import select_whole_points
    from .brief_duplicates import deduplicate
    result, duplicate_omitted_count = deduplicate(result)
    result, budget_omissions = select_whole_points(result)
    budget_omitted_count = sum(item['point_count'] for item in budget_omissions)
    if budget_omitted_count and not any(c['points'] for c in result['capsules']):
        raise books.BookError('认知简版完整认识均无法放入领域篇幅，保留上一版')
    result = validate(result, rows, require_selection=True)
    by_n = {row['n']: row for row in rows}
    for identity in result['identities']:
        allowed = set()
        for n in identity['references']:
            source = by_n[n]
            # Legacy untagged identity pages can still bind via their
            # exact canonical page title; body mentions do not bind.
            if (source.get('subject_id') == identity['subject_id'] or
                (source.get('subject_id') == 'unknown' and source['name'] == books.SUBJECTS[identity['subject_id']])):
                allowed.update([source['name'], *source.get('aliases', [])])
        # Reading never introduces a name merely mentioned in book prose.
        identity['reading_aliases'] = [name for name in [identity['name'], *identity['aliases']]
                                      if name in allowed and name != books.SUBJECTS[identity['subject_id']]]
    with books.LOCK:
        if books.ROOT != root or fingerprint(material(snapshot())) != revision:
            raise books.RevisionConflict('认知简版生成期间来源已变化')
        data = {'version': VERSION, 'source_revision': revision,
                'generated_at': datetime.now().astimezone().isoformat(),
                'source_date': source_date, 'rejected_point_count': rejected_count, **result,
                'budget_omitted_point_count': budget_omitted_count,
                'duplicate_omitted_point_count': duplicate_omitted_count,
                'budget_omissions': budget_omissions,
                'retained_candidate_count': retained_count,
                'scope_audit': [{'subject_id': c['subject_id'], 'scope': c['scope'],
                    'source_valid_candidates': selected_counts.get((c['subject_id'], c['scope']), 0),
                    'approved_points': len(c['points'])} for c in result['capsules']],
                'sources': source_refs(rows)}
        books.atomic_text(path(), json.dumps(data, ensure_ascii=False))
        with _guard:
            # Our atomic replacement may have the same size/timestamp as
            # the previous file on Windows. Invalidate our own read cache
            # rather than serving a successfully replaced old projection.
            _cached.pop(str(path()), None)
            _dirty_roots.discard(str(root))
    return {'status': 'completed', 'source_revision': revision,
            'chars': len(read_context('writing')), 'source_count': len(rows), 'rejected_point_count': rejected_count,
            'budget_omitted_point_count': budget_omitted_count,
            'duplicate_omitted_point_count': duplicate_omitted_count,
            'candidate_resumed': candidate_resumed}

async def _drain():
    global _pending, _task
    try:
        attempts = 0
        while _pending and attempts < _pending_limit:
            _pending = False
            with books.LOCK:
                rows = material(snapshot())
                revision = fingerprint(rows)
                # Failed model work must not block the independent read-only
                # source check, including after install cleared this snapshot.
                with _guard:
                    _current_sources[str(books.ROOT)] = source_refs(rows)
            if _failed_refreshes.get(str(books.ROOT)) == revision:
                break
            from . import brief_checkpoint
            identity = brief_checkpoint.binding(VERSION, revision, source_refs(rows), CONTRACT, REVIEW)
            try:
                if brief_checkpoint.failed(books.ROOT, identity):
                    previous = _read()
                    if not (previous and previous.get('version') == VERSION
                            and previous.get('source_revision') == revision):
                        break
            except (OSError, books.BookError, ValueError):
                logger.warning('Cognition brief checkpoint unavailable; background refresh deferred')
                break
            attempts += 1
            try:
                await refresh(_llm)
            except books.RevisionConflict:
                _pending = True
            except Exception:
                logger.warning('Cognition brief refresh failed; authoritative books unchanged')
                # Keep the failed phase/candidate for explicit recovery. A
                # same-source notification must not start another paid round.
    finally:
        _task = None


def request_refresh(recovery_only=False):
    global _pending, _task, _pending_limit
    limit = 1 if recovery_only else 2
    _pending_limit = max(_pending_limit, limit) if _pending else limit
    _pending = True
    if _loop is not None and _llm is not None and (_task is None or _task.done()):
        _task = _loop.create_task(_drain(), name='cognition_brief_refresh')


def schedule_refresh(*, recovery_only=False):
    """Sync/manual writers notify the application loop without running a model inline."""
    if _loop is not None and _llm is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(request_refresh, recovery_only)


def install(loop, llm):
    global _loop, _llm
    _loop, _llm = loop, llm
    # Startup compares authoritative revisions once in the background, never per reply.
    with _guard:
        _dirty_roots.add(str(books.ROOT))
        _current_sources.pop(str(books.ROOT), None)
    request_refresh()
